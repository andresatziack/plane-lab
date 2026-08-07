# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""What a client's own user may change, and which work items they may reach.

The client portal is a GUEST of the project that belongs to their Cliente (master
context, section 2b). Phase 8 is the first phase in which that GUEST is a real actor
rather than a role that every endpoint refuses, and this module holds the two decisions
that inclusion depends on.

**Why the allowlist lives here and not in the view.** ``IssueViewSet.partial_update``
is core Plane code, rebased from upstream. An allowlist written inline there is an
allowlist that a rebase can silently widen, and one that can only be tested through
HTTP. Here it is a module constant with unit tests, and the core touch is a call. Same
argument D55 makes for ``ReportViewer.guest()``: a rule that decides what a client may
see or do is a domain concern, not a template detail.

**Why the allowlist has to exist before GUEST is allowed through the decorator.**
``allow_permission(..., creator=True, model=Issue)`` releases the whole view to whoever
created the row, *before* the role list is consulted (``plane.app.permissions.base``).
So a GUEST who opened a ticket can already PATCH every field on it: responsible party,
labels, dates, estimate, parent. That hole is reachable today, and no new endpoint can
close it -- extension can add a safe door, it cannot close an open one. Closing it is
what justifies this phase's single deliberate divergence from core, and the allowlist is
what does the closing. Adding GUEST to the role list first would turn a latent
over-permission into a granted one.

See DECISOES.md, D59 and D60.
"""

from plane.db.models.state import State, StateGroup
from plane.utils.service_log import ServiceLogValidationError
from plane.utils.service_permission import ROLE_GUEST

#: Payload keys a client's own user may change on a work item.
#:
#: ``priority`` is a plain CharField. State is written as ``state_id`` --
#: ``IssueCreateSerializer`` declares it as ``PrimaryKeyRelatedField(source="state")``,
#: so ``state`` is not a writable input key and does not belong here.
#:
#: A frozenset held as a module constant so the tests assert against the same object the
#: code uses instead of restating it and drifting apart. That is the D57 pattern, and the
#: reason is sharper here than there: a test that restates this list would keep passing
#: after somebody widened it.
CLIENT_WRITABLE_ISSUE_FIELDS = frozenset({"priority", "state_id"})

#: State groups a client's own user may move a work item into.
#:
#: Two closing groups and two reopening ones, which is the whole of what section 6 of the
#: phase brief gives the client: "não quero mais isto" and "voltei a precisar".
#:
#: ``TRIAGE`` is excluded because a client must not put work into the triage queue. Note
#: that ``IssueViewSet.partial_update`` already refuses triage today, but only as a side
#: effect of not passing ``allow_triage_state`` into the serializer context -- a
#: guarantee that rests on an absent dict key is a guarantee that the next phase to add
#: that key removes without noticing. Named here so it is refused on purpose.
#:
#: ``BACKLOG`` is excluded because the client already has both of the controls backlog
#: would blur: closing says "not wanted", priority says "how urgent". Backlog is internal
#: planning. A work item a client moved there is neither closed nor queued, drops out of
#: the active views, and comes back six weeks later as "you ignored my request". Denying
#: it removes an ambiguity whose only product is a dispute.
CLIENT_ALLOWED_STATE_GROUPS = frozenset(
    {
        StateGroup.COMPLETED.value,
        StateGroup.CANCELLED.value,
        StateGroup.UNSTARTED.value,
        StateGroup.STARTED.value,
    }
)

#: The client asked for a state they are not allowed to reach.
#:
#: 400 and not 403, per D47: the actor genuinely holds the capability to change state --
#: that is the whole point of criterion 6 -- and it is the *target* named by the payload
#: that is ineligible. 403 would say "you may not change states", which is false and
#: would send a reader looking for a permission problem that does not exist.
STATE_GROUP_NOT_PERMITTED_FOR_CLIENT = "STATE_GROUP_NOT_PERMITTED_FOR_CLIENT"


def filter_issue_payload_for_client(data, issue):
    """Narrow a work item PATCH payload to what a client's own user may change.

    Returns a new dict containing exactly the keys in
    :data:`CLIENT_WRITABLE_ISSUE_FIELDS`, each defaulted to the value already on
    ``issue`` when the caller did not send it.

    **Unknown keys are dropped, not rejected.** Criterion 19 accepts either, and the
    precedent in this codebase is to drop: ``IntakeIssueViewSet.partial_update`` narrows
    a guest's payload the same way, rebuilding the dict from the instance. A client's
    interface never offered ``assignees``, so there is no field on which to render an
    error for it, and a 400 would turn a payload a well-behaved client never sends into a
    failure mode a well-behaved client has to handle.

    Defaulting from the instance rather than omitting absent keys keeps the payload a
    full statement of the two fields, which is what makes the activity trail readable:
    ``requested_data`` is dumped from the *filtered* dict by the caller, so the trail
    records what was applied instead of what was attempted.
    """
    return {
        "priority": data.get("priority", issue.priority),
        "state_id": data.get("state_id", str(issue.state_id) if issue.state_id else None),
    }


def validate_client_state_transition(state_id, *, project_id):
    """Refuse a state whose group is closed to a client's own user.

    Raises :class:`ServiceLogValidationError` with
    :data:`STATE_GROUP_NOT_PERMITTED_FOR_CLIENT` when the target state's group is not in
    :data:`CLIENT_ALLOWED_STATE_GROUPS`.

    Validates the *group*, never the state row itself. A workspace renames and adds
    states freely, so a list of permitted state ids or names would be a list that goes
    stale the first time an admin adds "Aguardando cliente". The group is the part of a
    state that carries meaning across workspaces.

    Silent on a ``None`` state id, which is how "the client did not touch the state"
    arrives after :func:`filter_issue_payload_for_client` has defaulted it, and on a
    state that does not resolve -- that is the serializer's error to raise, scoped to the
    project, and duplicating it here would mean two different messages for one mistake.
    """
    if not state_id:
        return

    state = State.all_state_objects.filter(pk=state_id, project_id=project_id).first()

    if state is None:
        return

    if state.group not in CLIENT_ALLOWED_STATE_GROUPS:
        raise ServiceLogValidationError(STATE_GROUP_NOT_PERMITTED_FOR_CLIENT)



def client_project_ids(user, *, slug):
    """The projects a client's own user may see, as a tuple of ids.

    One place, because every portal read has to be scoped by it and a second
    implementation is a second thing to get wrong. Reads ``ProjectMember`` directly, the
    idiom the rest of the codebase uses for this question -- there is no shared helper for
    "projects this user belongs to" and inventing one would touch core call sites.

    Deliberately does *not* filter on ``role``. A portal caller is a GUEST by
    construction, and the interesting failure is not "a Member reached the portal" -- a
    Member reaching the guest projection sees strictly less than they are entitled to --
    but "a GUEST reached a project that is not theirs", which is what this scoping
    prevents regardless of role.

    Returns a tuple so it can be handed straight to ``ServiceLogFilterSet.narrow`` and
    cannot be mutated by a caller. **An empty result means the caller sees nothing, and
    callers must treat it that way explicitly** -- see the warning in
    :func:`scope_filterset_to_client`.
    """
    from plane.db.models import ProjectMember

    return tuple(
        ProjectMember.objects.filter(
            member=user,
            workspace__slug=slug,
            is_active=True,
            project__archived_at__isnull=True,
        ).values_list("project_id", flat=True)
    )


def scope_filterset_to_client(filterset, project_ids):
    """Confine a report descriptor to the client's own projects. D63.

    Two traps this exists to make unmissable, both of which fail silently:

    **1. ``narrow()`` replaces, it does not intersect.** It is ``dataclasses.replace``, so
    the scope has to be applied *last*, after anything that came from the query string.
    Narrowing first and letting caller parameters through afterwards widens the tenancy
    boundary with no error anywhere.

    **2. Narrowing with an empty tuple selects everything.**
    ``ServiceLogFilterSet.queryset`` applies each lookup only ``if values:``, so
    ``narrow(project_ids=())`` produces a query with no project filter at all -- an empty
    scope becoming total access, which is the failure that looks impossible in review
    precisely because the code reads like it is scoping. Callers must short-circuit on an
    empty result instead of calling this, and this function refuses to be the place that
    quietly does the wrong thing.
    """
    if not project_ids:
        raise ValueError(
            "refusing to scope a report to an empty project set: narrowing with an "
            "empty tuple selects every project in the workspace. Short-circuit to an "
            "empty response instead. See D63."
        )

    return filterset.narrow(project_ids=tuple(project_ids))



def is_client_portal_member(user, *, slug, project_id):
    """Whether this caller is acting as a client's own user on this project.

    A GUEST of a project is a client's user by construction (master context, section 2b):
    the role exists in this fork for exactly that, and Phase 7 already made it incapable
    of holding any elevated work log capability.

    Compares with ``<=`` against GUEST rather than ``== 5``, matching
    ``IntakeIssueViewSet.partial_update`` and ``IssueViewSet.retrieve``. The comparison
    direction matters: a role *below* guest, if one is ever added, must be narrowed too,
    and ``==`` would silently give it the unrestricted path.

    Note this deliberately ignores workspace-admin status. A workspace ADMIN who is also
    a project GUEST gets narrowed here, which is the same asymmetry ``IntakeIssueViewSet``
    has at its own field allowlist. Erring toward narrowing is the safe direction, and an
    admin who needs the full form has their own membership to fix.
    """
    from plane.db.models import ProjectMember

    membership = (
        ProjectMember.objects.filter(
            workspace__slug=slug,
            project_id=project_id,
            member=user,
            is_active=True,
        )
        .values_list("role", flat=True)
        .first()
    )

    return membership is not None and membership <= ROLE_GUEST


def client_may_reach_issue(user, issue):
    """Whether a client's own user may act on this work item at all.

    The same condition ``IssueViewSet.retrieve`` applies before showing a work item to a
    GUEST, applied to the write path, which had no equivalent: ``partial_update`` would
    otherwise let a client change the priority of a work item the read endpoint refuses
    to show them. The narrowed allowlist makes that a small leak rather than a large one,
    which is not a reason to leave it open.
    """
    return bool(issue.project.guest_view_all_features) or issue.created_by_id == user.id



#: Activity feed ``field`` values a client's own user must not receive. D61.
#:
#: **This phase creates the exposure, so this phase owns the fix.** Before the portal, no
#: GUEST was a member of a Cliente's project, so ``IssueActivityEndpoint`` -- which has
#: admitted GUEST all along -- had no client reading it. Shipping the portal without this
#: would be shipping a known leak in the same change that makes it reachable.
#:
#: The leak is concrete. ``_service_log_batch_summary`` persists
#: ``f"{sum(logged_hours)} h ({hour type labels})"`` into ``IssueActivity.new_value``, and
#: ``logged_hours`` is the one hour quantity R11 keeps from the client. A client who sees
#: ``equivalent_hours`` in the portal and ``logged_hours`` in the feed divides one by the
#: other and has the multiplier -- exactly the derivation R11(c) exists to prevent, and
#: exactly what D55 refuses to compute for ``ReportViewer.guest()``.
#:
#: Why no test caught it: ``test_the_activity_trail_carries_no_money`` asserts money
#: absence and uses ``logged_hours == "1.0000"`` as its *positive control*. Correct for a
#: feed read by a Member. It is the leak for a feed read by a Guest. The test was never
#: wrong; it was never asked the Guest question.
#:
#: **Excluded rather than re-projected**, for the reason D51 gives about aggregation
#: boundaries: a second rendering of the same work log is a second place R11 can be wrong,
#: and the first one already has tests. Here the argument is stronger still, because the
#: entire informational content of the ``service_log`` row *is* the logged hours -- there
#: is no client-safe remainder left to show once the number is gone.
#:
#: All four values, not only ``service_log``. Each of the other three answers a question
#: about how the work was recorded and priced internally -- R8's audit trail, R10's
#: override record -- and none answers a question the client asked:
#:
#: * ``service_log_author`` -- an internal correction of who is credited
#: * ``service_log_delegation`` -- "João, registrado por Maria", internal bookkeeping
#: * ``service_log_hour_type_override`` -- suggested against applied hour type, which
#:   hands the client the multiplier structure and an accusation to make with it
#:
#: The client's own questions -- what was done, when, by whom, how many hours count
#: against the contract -- are all answered by the portal work log endpoint, projected
#: once, through ``ServiceLogClientSerializer``.
CLIENT_HIDDEN_ACTIVITY_FIELDS = frozenset(
    {
        "service_log",
        "service_log_author",
        "service_log_delegation",
        "service_log_hour_type_override",
    }
)



def is_client_portal_workspace_member(user, *, slug):
    """Whether this caller is a client's own user at the workspace level.

    The workspace-level twin of :func:`is_client_portal_member`, for the activity readers
    that are not scoped to a project.

    Needed because ``WorkspaceEntityPermission`` and ``ProjectEntityPermission`` both
    admit *any* active member on a safe method, with no role filter at all -- so a client's
    user reaches several activity readers that were written when no client had a login.
    ``GET /api/workspaces/<slug>/user-activity/<user_id>/`` is the sharpest of them: it
    takes the actor's id straight from the URL, so a client can ask for a named
    technician's activity directly.
    """
    from plane.db.models import WorkspaceMember

    membership = (
        WorkspaceMember.objects.filter(workspace__slug=slug, member=user, is_active=True)
        .values_list("role", flat=True)
        .first()
    )

    return membership is not None and membership <= ROLE_GUEST


def activity_fields_hidden_from(user, *, slug, project_id=None):
    """The ``field`` values to exclude from an activity feed for this caller. D61.

    One function for every activity reader, because the leak this closes had **three**
    doors and patching the obvious one would have left two open:

    * ``IssueActivityEndpoint`` -- the work item feed, ``allow_permission`` already listed
      GUEST explicitly
    * ``WorkspaceUserActivityEndpoint`` -- another user's activity, actor id from the URL,
      admitted by ``WorkspaceEntityPermission`` on any safe method
    * ``IssueActivityListAPIEndpoint`` -- the external token API, admitted by
      ``ProjectEntityPermission`` on any safe method

    Returns the base exclusions unchanged for everybody else, so a caller can use it
    unconditionally and there is no second list of the four core values to keep in step.
    """
    base = ["comment", "vote", "reaction", "draft"]

    if project_id is not None:
        is_client = is_client_portal_member(user, slug=slug, project_id=project_id)
    else:
        is_client = is_client_portal_workspace_member(user, slug=slug)

    if is_client:
        return base + sorted(CLIENT_HIDDEN_ACTIVITY_FIELDS)

    return base



def issue_client_totals(issue_id):
    """The work item totals a client may see. R11, D58.

    Two hour quantities and not three. ``logged_hours`` is absent for the reason R11(c)
    gives: the client sees ``equivalent_hours``, and given both, one division returns the
    multiplier. This is the same projection ``ReportViewer.guest()`` applies at the
    aggregation boundary (D55), applied to a single work item -- deliberately the same two
    field names, so a reader comparing the portal's work item panel against the portal's
    dashboard is comparing like with like.

    The monetary total is summed **only over rows whose settled route bills the client**
    (D58), and is **absent** rather than zero when there are none.

    The explicit route filter is the point. ``issue_service_log_amount`` needs no such
    filter because pool debits and non-billable rows carry ``0.00`` by construction, and
    for an Admin total that reasoning is sound. Relying on it here would make the client's
    monetary boundary an emergent property of a check constraint somewhere else, so that
    the day a pool debit legitimately carries a value -- invoiced overage is already
    priced, D40 -- a contract client would silently start seeing money for hours their
    pool absorbed. Naming the route keeps D58 true by construction instead of by luck.

    Summed from the persisted columns, never recomputed: section 4b, and criterion 13.
    """
    from django.db.models import Sum, Value
    from django.db.models.functions import Coalesce

    from plane.db.models import ServiceBillingType, ServiceLog
    from plane.utils.service_log_time import ZERO_HOURS, format_hours
    from plane.utils.service_money import ZERO_MONEY, format_money

    client_hour_fields = ("equivalent_hours", "debited_hours")

    logs = ServiceLog.objects.filter(issue_id=issue_id)

    aggregates = logs.aggregate(
        **{field: Coalesce(Sum(field), Value(ZERO_HOURS)) for field in client_hour_fields}
    )

    payload = {}

    for field in client_hour_fields:
        value = aggregates[field] or ZERO_HOURS
        payload[field] = str(value)
        payload[f"{field}_display"] = format_hours(value)

    billed = logs.filter(settled_billing_route=ServiceBillingType.BillingRoute.BILL_AMOUNT)

    if billed.exists():
        amount = billed.aggregate(amount=Coalesce(Sum("amount"), Value(ZERO_MONEY)))["amount"] or ZERO_MONEY
        payload["amount"] = str(amount)
        payload["amount_display"] = format_money(amount)

    return payload
