# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Domain logic for work logs: segmentation, persistence and aggregation.

The half of the work log domain layer that needs models. The other half --
parsing, rounding and the hour arithmetic of R1, R2, R3 and R9 -- is
``plane.utils.service_log_time``, which imports nothing from Django so that the
financial arithmetic can be tested without a database.

Split that way on purpose: everything in this module is about *which rows exist*,
and everything in that one is about *what the numbers are*. Views and serializers
call into here; nothing here knows that HTTP exists, as section 6 of the master
context requires.
"""

# Python imports
import uuid
import zoneinfo
from dataclasses import replace

# Django imports
from django.db import transaction
from django.db.models import DecimalField, OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone as django_timezone

# Module imports
from plane.db.models import ServiceBillingType, ServiceLog, ServiceLogEntryMode, ServiceLogSource

# `Segment` lives with the engine that produces it, and is re-exported here so the
# callers and tests written against `service_log.Segment` keep working. The dependency
# runs one way: this module imports the engine, never the reverse.
from plane.utils.service_calendar import Segment, classify
from plane.utils.service_log_time import (
    BLOCK_MINUTES,
    ZERO_HOURS,
    debited_hours,
    equivalent_hours,
    round_to_block,
    to_decimal_hours,
)

# Error codes, in the UPPER_SNAKE style the client entity and the catalogues
# established. The frontend maps them to translated strings; the API never returns
# Portuguese.
HOUR_TYPE_REQUIRED = "HOUR_TYPE_REQUIRED"
WORKED_ON_CANNOT_BE_IN_THE_FUTURE = "WORKED_ON_CANNOT_BE_IN_THE_FUTURE"
TIME_TRACKING_DISABLED_FOR_PROJECT = "TIME_TRACKING_DISABLED_FOR_PROJECT"
ONLY_THE_AUTHOR_CAN_CHANGE_A_SERVICE_LOG = "ONLY_THE_AUTHOR_CAN_CHANGE_A_SERVICE_LOG"
SERVICE_CLIENT_CANNOT_BE_UNSET_WITH_SERVICE_LOGS = "SERVICE_CLIENT_CANNOT_BE_UNSET_WITH_SERVICE_LOGS"
SERVICE_CLIENT_CANNOT_BE_CHANGED_WITH_SERVICE_LOGS = "SERVICE_CLIENT_CANNOT_BE_CHANGED_WITH_SERVICE_LOGS"

#: Field names of the three totals, so the API, the annotations and the frontend
#: cannot drift apart on spelling. Section 6 of the phase brief warns that
#: confusing these three is the most likely error in the feature, which is also why
#: they are never collapsed into one "total".
TOTAL_FIELDS = ("logged_hours", "equivalent_hours", "debited_hours")


class ServiceLogValidationError(ValueError):
    """A work log could not be built from the given input.

    Carries an UPPER_SNAKE ``code``; the translation belongs to the frontend. Mirrors
    ``InvalidDurationError`` in the pure module so callers have one shape to handle.
    """

    def __init__(self, code):
        self.code = code
        super().__init__(code)


# ---------------------------------------------------------------------------
# Billing route
# ---------------------------------------------------------------------------


def is_billable_route(billing_route):
    """Whether a billing route charges the client at all. Rules R5 and R11(a).

    The single place that knows what ``NON_BILLABLE`` means. ``debited_hours`` in the
    pure module takes a boolean rather than the route precisely so that this
    comparison exists once: "do not charge" has exactly one mechanism (D20), and two
    copies of that comparison is how a second mechanism starts.

    Accepts a route value or a ``ServiceBillingType``.
    """
    if isinstance(billing_route, ServiceBillingType):
        billing_route = billing_route.billing_route

    return str(billing_route) != ServiceBillingType.BillingRoute.NON_BILLABLE


def compute_hour_quantities(*, raw_duration_minutes, multiplier, billing_route):
    """The three persisted hour quantities for one segment. Section 4, quantities 2-4.

    The only place they are derived, so that the API, an import, a recalculation and
    a future phase cannot each apply the rules slightly differently.

    Returns ``{"logged_hours", "equivalent_hours", "debited_hours"}``.
    """
    logged = to_decimal_hours(round_to_block(raw_duration_minutes))
    equivalent = equivalent_hours(logged, multiplier)

    return {
        "logged_hours": logged,
        "equivalent_hours": equivalent,
        "debited_hours": debited_hours(equivalent, is_billable=is_billable_route(billing_route)),
    }


# ---------------------------------------------------------------------------
# Segmentation -- the seam the calendar and windows phase fills in
# ---------------------------------------------------------------------------


def build_segments(
    *,
    worked_on,
    raw_duration_minutes,
    entry_mode,
    start_time=None,
    end_time=None,
    workspace_id=None,
    service_client=None,
):
    """Split an entry into classified segments. Rule R10.

    A thin adapter over ``plane.utils.service_calendar.classify``, the engine the
    calendar and windows phase delivered. This function survives rather than being
    inlined because it owns one decision the engine should not: in duration mode the
    times are **discarded** instead of passed on. R9 is explicit that a duration entry
    does not establish when the work happened, and letting the times through would let
    the engine split an entry that D13 forbids splitting.

    Everything else belongs to the engine: resolving windows by priority, giving each
    segment its own date (R7), emitting a segment per change of classification rather
    than per midnight, and filling in the reason.

    Rounding stays here, in ``build_batch_rows``, applied per segment together with the
    sub-15-minute guardrail. The engine returns raw minutes and does no arithmetic --
    that split is deliberate and should not drift.

    ``workspace_id`` is required in practice: without it there is no configuration to
    resolve against. It remains keyword-optional so that adopting the engine did not
    change the signature any caller was written against.
    """
    is_interval = entry_mode == ServiceLogEntryMode.INTERVAL

    if workspace_id is None:
        # No workspace, no windows to resolve against. Returns one unclassified segment
        # rather than failing, for the same reason the engine itself is total: a missing
        # configuration must never stop work that was already performed from being
        # recorded.
        return [
            Segment(
                worked_on=worked_on,
                start_time=start_time if is_interval else None,
                end_time=end_time if is_interval else None,
                raw_duration_minutes=raw_duration_minutes,
                suggested_hour_type=None,
                reason="",
            )
        ]

    return classify(
        workspace_id=workspace_id,
        worked_on=worked_on,
        raw_duration_minutes=raw_duration_minutes,
        start_time=start_time if is_interval else None,
        end_time=end_time if is_interval else None,
        service_client=service_client,
    )



def apply_minimum_block_guardrail(segments):
    """Collapse a split whose total is under one block into a single segment.

    The mandatory guardrail from section 6 of the calendar and windows brief. R2
    rounds each segment independently, so 17:57 to 18:03 -- six minutes of work
    across the 18:00 boundary -- would otherwise become 3 + 3 minutes, each floored
    up to 15, billing half an hour for six minutes. Five times the real time, and
    indefensible in front of a client.

    Collapses onto the *longest* segment, which is the "faixa predominante" that
    brief asks for, and keeps the full span's start and end so the row still records
    when the work actually happened. Ties go to the earlier segment, which is
    arbitrary but deterministic -- and at these durations the alternative is a
    coin flip on 15 minutes.

    A single segment, or a split totalling a block or more, passes through untouched.
    """
    if len(segments) <= 1:
        return segments

    total_raw = sum(segment.raw_duration_minutes for segment in segments)

    if total_raw >= BLOCK_MINUTES:
        return segments

    predominant = max(segments, key=lambda segment: segment.raw_duration_minutes)

    return [
        replace(
            predominant,
            worked_on=segments[0].worked_on,
            start_time=segments[0].start_time,
            end_time=segments[-1].end_time,
            raw_duration_minutes=total_raw,
        )
    ]


# ---------------------------------------------------------------------------
# Building and persisting a batch
# ---------------------------------------------------------------------------


def resolve_segment_hour_type(segment, *, override=None):
    """The hour type a segment is recorded against, plus whether that was an override.

    Precedence: the technician's explicit choice wins over the engine's suggestion.
    R10 is explicit that "classificação é conveniência, não trava" and that an
    override is "sempre permitida, e registrada na auditoria quando divergir da
    sugestão" -- so this returns the flag as well as the value, and only sets it when
    the two genuinely differ. Overriding a suggestion with the same value is not an
    override.

    Raises when neither exists. Before the calendar and windows phase that was every
    entry where the technician did not choose, because no engine ran and there was never
    a suggestion to fall back on. Now it means the instant is genuinely unclassified --
    an uncovered range, which the coverage ratchet permits -- and the technician has to
    pick. The engine is total and returns ``None`` with a reason instead of raising, so
    this is the only place the missing choice becomes an error, and it is a 400 rather
    than a crash.
    """
    suggested = segment.suggested_hour_type
    final = override or suggested

    if final is None:
        raise ServiceLogValidationError(HOUR_TYPE_REQUIRED)

    is_overridden = bool(override is not None and suggested is not None and override.pk != suggested.pk)

    return final, is_overridden


def build_batch_rows(
    *,
    issue,
    author,
    description,
    billing_type,
    segments,
    hour_type=None,
    entry_mode=ServiceLogEntryMode.DURATION,
    source=ServiceLogSource.MANUAL,
    batch_id=None,
):
    """Unsaved ``ServiceLog`` rows for a set of segments. No database writes.

    Separate from ``create_service_log_batch`` so the preview endpoint can show
    exactly what would be created -- section 3 of the phase brief requires the
    preview to be shown *before* saving, and a preview computed by different code
    than the save is a preview that can lie.

    R2 is applied here, per segment, because each segment is an independently
    billable unit with its own multiplier (D17 and section 6 of the calendar brief).
    The consequence is documented and accepted: the same 20 minutes are 0.25h entered
    as a duration and 0.5h entered as an interval crossing 18:00, because in the
    second case the system knows the work spanned two price bands.

    The multiplier and the billing route are snapshotted onto every row (R4, and D20
    for the route). Nothing downstream may re-read them through the foreign keys.
    """
    batch_id = batch_id or uuid.uuid4()
    rows = []

    for index, segment in enumerate(apply_minimum_block_guardrail(segments)):
        segment_hour_type, is_overridden = resolve_segment_hour_type(segment, override=hour_type)

        quantities = compute_hour_quantities(
            raw_duration_minutes=segment.raw_duration_minutes,
            multiplier=segment_hour_type.multiplier,
            billing_route=billing_type.billing_route,
        )

        rows.append(
            ServiceLog(
                issue=issue,
                project=issue.project,
                author=author,
                worked_on=segment.worked_on,
                description=description,
                entry_mode=entry_mode,
                start_time=segment.start_time,
                end_time=segment.end_time,
                source=source,
                raw_duration_minutes=segment.raw_duration_minutes,
                hour_type=segment_hour_type,
                billing_type=billing_type,
                suggested_hour_type=segment.suggested_hour_type,
                is_hour_type_overridden=is_overridden,
                classification_reason=segment.reason,
                applied_multiplier=segment_hour_type.multiplier,
                applied_billing_route=billing_type.billing_route,
                batch_id=batch_id,
                segment_index=index,
                **quantities,
            )
        )

    return rows


@transaction.atomic
def create_service_log_batch(rows):
    """Persist a batch, all of it or none of it.

    Atomic because a half-written split entry is worse than a rejected one: the work
    item would show 1h of a 3h interval and the client would be under-billed with
    nothing to indicate why.

    Saved one row at a time rather than with ``bulk_create``, deliberately.
    ``ProjectBaseModel.save()`` denormalises ``workspace`` from the project and
    ``BaseModel.save()`` sets ``created_by`` from crum -- neither runs under
    ``bulk_create``, which would leave ``workspace_id`` null and violate the column.
    A batch is one row in the common case and rarely more than three, so there is
    nothing to win here.
    """
    for row in rows:
        row.save()

    return rows


@transaction.atomic
def replace_service_log_batch(*, batch_id, rows):
    """Swap a batch's segments for a new set, atomically.

    An edit can change the number of segments -- widening an interval across 18:00
    turns one row into two -- so this deletes and recreates rather than updating in
    place. Soft delete keeps the old rows in ``all_objects`` for anything that
    already counted them.

    Reuses ``batch_id`` so the entry keeps its identity across the edit, which is
    what makes the audit trail and any future pool reversal able to follow it. The
    delete runs first: ``segment_index`` is unique per live batch, so recreating
    before removing would collide on the partial unique index.
    """
    delete_service_log_batch(batch_id)

    for row in rows:
        row.batch_id = batch_id
        row.save()

    return rows


@transaction.atomic
def delete_service_log_batch(batch_id):
    """Soft delete every segment of a batch. Acceptance criterion 11.

    "Excluir um lançamento dividido remove todos os seus segmentos", transactionally,
    per section 3 of the phase brief.

    Each row is deleted individually rather than through the queryset. The queryset's
    ``delete()`` is a bulk ``update(deleted_at=...)`` that skips
    ``SoftDeleteModel.delete()``, so the cascade task never runs -- harmless today,
    but the moment Phase 4 hangs a pool debit off a work log, a batch deleted in bulk
    would leave the debit behind. Batches are tiny; correctness is worth the queries.
    """
    rows = list(ServiceLog.objects.filter(batch_id=batch_id))

    for row in rows:
        row.delete()

    return len(rows)


# ---------------------------------------------------------------------------
# Validation that needs the database
# ---------------------------------------------------------------------------


def workspace_today(workspace):
    """Today's date in the workspace's timezone.

    The anchor for "is this service date in the future" (acceptance criterion 14).

    The *workspace* timezone, and explicitly not the requesting user's. Both
    ``Workspace`` and ``Project`` carry a ``timezone`` column and the master context
    says to use one of them rather than inventing another; the workspace is the
    coherent choice because the classification windows are a commercial parameter of
    the workspace (D16), and using the project's would let the same worked hour fall
    in different bands per project. Reading the active timezone instead would be
    worse still -- ``TimezoneMixin`` activates the user's per request, so whether a
    log validated would depend on who had the tab open.

    This is a provisional anchor. Section 5b of the calendar and windows phase owns
    the full timezone strategy and will formalise it; what is fixed here is only the
    choice of the workspace as the source, so that phase inherits a decision rather
    than a contradiction.
    """
    try:
        zone = zoneinfo.ZoneInfo(workspace.timezone or "UTC")
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        # A workspace with an unparseable timezone must not make logging impossible.
        zone = zoneinfo.ZoneInfo("UTC")

    return django_timezone.now().astimezone(zone).date()


def validate_worked_on(worked_on, workspace):
    """Refuse a service date in the future. Acceptance criterion 14.

    Today is allowed; tomorrow is not. Backdating is explicitly legitimate -- R7
    exists so a retroactive entry debits the retroactive month -- so only the future
    is refused.
    """
    if worked_on > workspace_today(workspace):
        raise ServiceLogValidationError(WORKED_ON_CANNOT_BE_IN_THE_FUTURE)


def validate_time_tracking_enabled(project):
    """Refuse writes to a project that has not switched the feature on.

    ``Project.is_time_tracking_enabled`` already existed and had no consumer -- it is
    one of the dormant hooks of Plane's commercial Time Tracking feature. Section 4
    of the phase brief reuses it as the per-project toggle, which makes this the
    first code in the codebase that reads it.

    Writes only. Reads stay open on purpose: switching the flag off must not
    retroactively hide work that was already logged and possibly already invoiced.
    """
    if not project.is_time_tracking_enabled:
        raise ServiceLogValidationError(TIME_TRACKING_DISABLED_FOR_PROJECT)


def validate_author_can_change(service_log, user):
    """Only the author may edit or delete, for now.

    Section 6 of the phase brief: "permissões vêm na Fase 7; por ora, apenas o
    autor". The author, not ``created_by`` -- once Phase 7 adds delegation those
    differ, and it is the person whose work is described who owns the record.

    Checked here rather than with ``allow_permission(creator=True)``: that flag
    hands the whole view to the row's creator and ignores their role, which the fork
    audit flagged as an existing security problem (ACHADOS-DO-CODIGO.md, and Phase 8
    section 4b). Reusing it would spread the bug.
    """
    if str(service_log.author_id) != str(user.id):
        raise ServiceLogValidationError(ONLY_THE_AUTHOR_CAN_CHANGE_A_SERVICE_LOG)


# ---------------------------------------------------------------------------
# Aggregation -- section 7
# ---------------------------------------------------------------------------


def issue_service_log_totals(issue_id):
    """The three totals for one work item. Section 6 of the phase brief.

    Three separate numbers, never one. Section 6 warns that confusing them is the
    most likely error in this feature, so they are returned under their own names and
    the UI labels them distinctly:

    * ``logged_hours`` -- chronological time, including non-billable entries
    * ``equivalent_hours`` -- after the multiplier; the only one a client sees (R11)
    * ``debited_hours`` -- excludes ``NON_BILLABLE`` routes, so it is the billable one

    Summed from the persisted columns rather than recomputed from the multiplier.
    Section 4b requires it: "Todo total é soma dos valores persistidos, nunca
    recalculado", because recomputing would silently reprice history whenever a
    catalogue multiplier changed -- exactly what R4 forbids.
    """
    aggregates = ServiceLog.objects.filter(issue_id=issue_id).aggregate(
        **{field: Coalesce(Sum(field), Value(ZERO_HOURS)) for field in TOTAL_FIELDS}
    )

    return {field: aggregates[field] or ZERO_HOURS for field in TOTAL_FIELDS}


def annotate_service_log_totals(queryset):
    """Attach the three totals to a queryset of work items. Section 7.

    Lets list views and future reports sum by project, client and period without
    walking work logs one by one -- which is possible at all because
    ``debited_hours`` is a persisted column rather than something derived at read
    time.

    Correlated subqueries rather than a join plus ``Sum``. A join would multiply every
    other row in the query by the number of work logs on the issue, and Plane's issue
    list already carries several annotations -- so a join here would quietly corrupt
    their counts. This is also why there is no denormalised total column on ``Issue``:
    that would mean touching a core model, and section 6 of the master context
    prefers extension to modification. If list performance ever demands it, adding
    one is a separate, deliberate decision.
    """

    def total_for(field):
        return Coalesce(
            Subquery(
                ServiceLog.objects.filter(issue_id=OuterRef("pk"))
                .values("issue_id")
                .annotate(total=Sum(field))
                .values("total")[:1]
            ),
            Value(ZERO_HOURS),
            output_field=DecimalField(max_digits=10, decimal_places=4),
        )

    return queryset.annotate(**{f"service_log_{field}": total_for(field) for field in TOTAL_FIELDS})


# ---------------------------------------------------------------------------
# Guards inherited from the client entity phase
# ---------------------------------------------------------------------------


def project_has_service_logs(project_id):
    """Whether a project has any work log at all.

    ``all_objects``, so a soft deleted log still counts. It can be restored, its
    hours may already be on an invoice, and either way unlinking the client would
    orphan it from the billing anchor it was recorded under.
    """
    return ServiceLog.all_objects.filter(project_id=project_id).exists()


def validate_service_client_change(project, new_service_client):
    """Refuse changing or clearing a project's client once it has work logs.

    Phase 1's acceptance criterion 11 -- "Remover o Cliente de um project com
    apontamentos é rejeitado" -- which could not be written in that phase because no
    work log model existed. Extended to cover a *change* of client as well as a
    clear, because both break the same thing: every existing log was recorded against
    the old client and its contract, and reassigning the project silently reattributes
    already-billed hours.

    Callers must apply this on **both** write paths. The phase brief says the project
    PATCH is "o único lugar por onde o vínculo é gravado", and that is no longer true
    -- Phase 2 added a bulk assign action that writes ``service_client_id`` through a
    queryset ``update()``, bypassing serializers entirely. A guard on only one path
    is not a guard.

    ``None`` when the change is allowed, otherwise an error code.
    """
    current_id = project.service_client_id
    new_id = getattr(new_service_client, "pk", new_service_client)

    if str(current_id or "") == str(new_id or ""):
        return None

    if current_id is None:
        # Attaching a client to a project that had none takes nothing away.
        return None

    if not project_has_service_logs(project.pk):
        return None

    if new_id is None:
        return SERVICE_CLIENT_CANNOT_BE_UNSET_WITH_SERVICE_LOGS

    return SERVICE_CLIENT_CANNOT_BE_CHANGED_WITH_SERVICE_LOGS


def service_client_change_alert(issue, destination_project):
    """Whether moving a work item would change which client its logs belong to.

    Phase 1's acceptance criterion 10 -- "Mover um chamado com apontamentos para um
    project de outro Cliente alerta o usuário". A warning, not a refusal: the move
    may well be correct, and Phases 4 to 6 will add the reversal on the origin
    contract and the debit on the destination.

    **There is currently no way to reach this.** Nothing in the backend changes an
    existing ``Issue.project_id``: ``project`` is in ``read_only_fields`` on both the
    app and the v1 issue serializers, every issue route is nested under its project,
    and the only "move" in the product is for *draft* issues, which are not work
    items yet. The board even tells the user the feature is absent -- "To change the
    client of a work item, move it to a project of that client". So criterion 10
    cannot be closed until a move path exists.

    The detection lives here anyway, tested, because it is the part that belongs to
    this phase: whoever builds the move endpoint should find the rule written down
    rather than reinvent it. Returns ``None`` when there is nothing to warn about.
    """
    origin_client_id = issue.project.service_client_id
    destination_client_id = destination_project.service_client_id

    if str(origin_client_id or "") == str(destination_client_id or ""):
        return None

    if not ServiceLog.all_objects.filter(issue_id=issue.pk).exists():
        return None

    return {
        "code": "SERVICE_CLIENT_WILL_CHANGE",
        "origin_service_client_id": str(origin_client_id) if origin_client_id else None,
        "destination_service_client_id": str(destination_client_id) if destination_client_id else None,
        "service_log_count": ServiceLog.all_objects.filter(issue_id=issue.pk).count(),
    }
