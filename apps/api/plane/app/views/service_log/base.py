# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
import json

# Django imports
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils import timezone

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions import ROLE, allow_permission
from plane.app.serializers import (
    ServiceLogClientSerializer,
    ServiceLogSerializer,
    ServiceLogWriteSerializer,
)
from plane.bgtasks.issue_activities_task import issue_activity
from plane.db.models import (
    Issue,
    ServiceBillingType,
    ServiceHourType,
    ServiceLog,
    WorkspaceMember,
)
from plane.utils.host import base_host
from plane.utils.service_billing import settle_service_log_batch, settlement_fields
from plane.utils.service_log import (
    ServiceLogValidationError,
    build_batch_rows,
    build_segments,
    create_service_log_batch,
    delete_service_log_batch,
    issue_service_log_amount,
    issue_service_log_totals,
    replace_service_log_batch,
    validate_time_tracking_enabled,
)
from plane.utils.service_permission import (
    CLOSED_PERIOD_IS_ADMIN_ONLY,
    ONLY_THE_AUTHOR_CAN_CHANGE_A_SERVICE_LOG,
    SERVICE_LOG_AUTHOR_IS_REASSIGNED_SEPARATELY,
    SERVICE_LOG_AUTHOR_IS_REQUIRED,
    # `SERVICE_LOG_AUTHOR_MUST_BE_A_TECHNICIAN` is deliberately NOT imported: since D47 it is
    # a 400, so it is no longer named in `PERMISSION_DENIED_CODES` and the view never
    # references it directly -- the domain raises it and the default branch answers 400.
    SERVICE_LOG_DELEGATION_NOT_PERMITTED,
    SERVICE_LOG_REASSIGNMENT_NOT_PERMITTED,
    resolve_capabilities,
    validate_can_change,
    validate_can_delegate,
    validate_can_reassign,
    validate_closed_period_access,
    validate_delegated_author,
)
from plane.utils.service_log_time import LONG_ENTRY_WARNING_MINUTES, format_hours
from plane.utils.service_money import ZERO_MONEY, format_money
from plane.utils.service_pool import ServicePoolValidationError, issue_pool_snapshot
from plane.utils.service_portal import (
    client_may_reach_issue,
    is_client_portal_member,
    issue_client_totals,
)
from plane.utils.service_pricing import ServicePricingValidationError

from ..base import BaseAPIView, BaseViewSet

# THE STATUS CODE RULE FOR THIS VIEWSET, in one line:
#
#   403 when the ACTOR lacks the capability; 400 when the PAYLOAD is wrong -- whether it is
#   malformed or names an ineligible target.
#
# The distinction is a contract with the interface, not bookkeeping. A 403 means the UI
# should hide or disable the action; a 400 means it should show an error **on the field**.
#
# `SERVICE_LOG_AUTHOR_MUST_BE_A_TECHNICIAN` is therefore a 400 and deliberately not in this
# set, even though it guards a privilege boundary. The caller in that case *holds*
# `can_delegate` -- they are authorised to delegate, and what is wrong is the value of
# `author_id`. Answering 403 would make the interface say "you do not have permission" to
# somebody who does, and they would go and ask for a permission they already have.
#
# Nothing leaks by saying so: anybody holding `can_delegate` is a workspace MEMBER and can
# already list the members and their roles.
PERMISSION_DENIED_CODES = {
    ONLY_THE_AUTHOR_CAN_CHANGE_A_SERVICE_LOG,
    SERVICE_LOG_DELEGATION_NOT_PERMITTED,
    SERVICE_LOG_REASSIGNMENT_NOT_PERMITTED,
    CLOSED_PERIOD_IS_ADMIN_ONLY,
}

#: What to do about a closed competency, returned alongside ``PERIOD_IS_CLOSED``.
#:
#: A workspace ADMIN passes the permission gate for a closed period and then hits the pool
#: layer, which refuses to move hours in a settled competency for everybody. Without this,
#: the Admin gets a refusal and no route forward -- and the obvious guess, "ask an engineer
#: to reopen the month", is very possibly the wrong thing to build. See D42, which is now
#: recorded as an open question rather than a task: accounting does not edit a closed period,
#: it posts an adjustment in the open one.
#:
#: A code rather than a sentence, like every other error in this domain: the frontend owns the
#: translation and the API never returns Portuguese.
RECORD_A_NEW_ENTRY_IN_THE_OPEN_COMPETENCE = "RECORD_A_NEW_ENTRY_IN_THE_OPEN_COMPETENCE"


class ServiceLogViewSet(BaseViewSet):
    """Work logs of one work item.

    **GUEST is on no endpoint here**, including reads. Section 6 of the phase brief is
    explicit -- "não expor apontamento a papel GUEST em nenhum endpoint" -- and the
    payload carries the raw duration and the multiplier that R11 keeps from clients.
    The client portal gets its own endpoint and its own serializer in Phase 8;
    ``ServiceLogClientSerializer`` is already written to that allowlist.

    Reads are open to project admins and members. Writes additionally require the
    project to have switched the feature on.

    **Authority over an existing log is Phase 7's** and resolves through
    ``plane.utils.service_permission``: the author may edit and delete their own, a holder
    of ``can_manage_others`` may edit anybody's, and a workspace ADMIN holds all three
    capabilities implicitly. A CLOSED competency period narrows that to ADMIN alone, and no
    grant opens it. Delegation and reassignment are separate capabilities again -- see
    ``create`` and ``reassign_author``.
    """

    serializer_class = ServiceLogSerializer
    model = ServiceLog

    def get_queryset(self):
        return (
            ServiceLog.objects.filter(
                workspace__slug=self.kwargs.get("slug"),
                project_id=self.kwargs.get("project_id"),
                issue_id=self.kwargs.get("issue_id"),
            )
            .select_related("author", "issue")
            # Not select_related on hour_type / billing_type: those foreign keys are
            # DO_NOTHING and may point at a soft deleted option, which the default
            # manager would filter out and turn into a None join. The serializer reads
            # them through all_objects instead.
        )

    def _issue(self, slug, project_id, issue_id):
        return (
            Issue.objects.filter(workspace__slug=slug, project_id=project_id, pk=issue_id)
            .select_related("project", "project__workspace")
            .first()
        )

    def _not_found(self):
        return Response({"error": "The required object does not exist."}, status=status.HTTP_404_NOT_FOUND)

    # ------------------------------------------------------------------ helpers

    def _resolve_catalog(self, workspace_id, validated):
        """The chosen hour type and billing type, both scoped to the workspace.

        Only *active* options are accepted for a new entry. An inactive option is one
        an admin has retired, and Phase 2's criterion 3 keeps it readable on old rows
        without making it selectable for new ones.

        Scoped by workspace so a crafted payload cannot borrow another workspace's
        catalogue -- the same isolation concern as ``validate_service_client``.
        """
        hour_type = None

        if validated.get("hour_type_id"):
            hour_type = ServiceHourType.objects.filter(
                pk=validated["hour_type_id"], workspace_id=workspace_id, is_active=True
            ).first()

            if hour_type is None:
                return None, None, "HOUR_TYPE_NOT_FOUND"

        billing_type = ServiceBillingType.objects.filter(
            pk=validated["billing_type_id"], workspace_id=workspace_id, is_active=True
        ).first()

        if billing_type is None:
            return None, None, "BILLING_TYPE_NOT_FOUND"

        return hour_type, billing_type, None

    def _build_rows(self, issue, validated, author, batch_id=None):
        """Run the domain pipeline: classify, then build the rows. No writes."""
        segments = build_segments(
            worked_on=validated["worked_on"],
            raw_duration_minutes=validated["raw_duration_minutes"],
            entry_mode=validated["entry_mode"],
            start_time=validated.get("start_time"),
            end_time=validated.get("end_time"),
            workspace_id=issue.project.workspace_id,
            service_client=issue.project.service_client_id,
        )

        hour_type, billing_type, error_code = self._resolve_catalog(
            issue.project.workspace_id, validated
        )

        if error_code:
            return None, error_code

        return (
            build_batch_rows(
                issue=issue,
                author=author,
                description=validated["description"],
                billing_type=billing_type,
                hour_type=hour_type,
                segments=segments,
                entry_mode=validated["entry_mode"],
                batch_id=batch_id,
            ),
            None,
        )

    def _long_entry_warning(self, rows):
        """The over-24h advisory. Section 5: warn, never block.

        A 30 hour entry is a legitimate use case (a project worked end to end), so this
        is returned alongside a successful response rather than as an error. The form
        asks for confirmation before submitting; this is the server saying the same
        thing so an API caller is not left guessing.
        """
        total_raw = sum(row.raw_duration_minutes for row in rows)

        if total_raw <= LONG_ENTRY_WARNING_MINUTES:
            return None

        return {"code": "SERVICE_LOG_EXCEEDS_24_HOURS", "raw_duration_minutes": total_raw}

    def _capabilities(self, request, slug):
        """What this caller may do with work logs here. Phase 7.

        Resolved once per request and passed down, rather than re-queried by each guard:
        the three capabilities plus the role are one question, and asking it twice in one
        view invites the two answers to be used inconsistently.
        """
        return resolve_capabilities(request.user, slug=slug)

    def _permission_error(self, code):
        """403 when the actor lacks the capability, 400 when the payload is wrong. Criterion 8.

        See ``PERMISSION_DENIED_CODES`` for the rule and for why naming an ineligible author
        is the 400 side of it rather than the 403 side.
        """
        return Response(
            {"error": code},
            status=(
                status.HTTP_403_FORBIDDEN if code in PERMISSION_DENIED_CODES else status.HTTP_400_BAD_REQUEST
            ),
        )

    def _pool_error(self, error):
        """A domain refusal from the pool or pricing layer, with a way forward where one exists.

        ``PERIOD_IS_CLOSED`` reaching an ADMIN is the case worth handling: they passed the
        permission gate, so as far as authorisation goes they may edit this log, and the
        refusal comes from the ledger declining to move hours in a settled competency. A bare
        code there tells them what they cannot do and nothing about what they can.
        """
        body = {"error": error.code, "detail": error.detail}

        if error.code == "PERIOD_IS_CLOSED":
            body["remediation"] = RECORD_A_NEW_ENTRY_IN_THE_OPEN_COMPETENCE

        return Response(body, status=status.HTTP_400_BAD_REQUEST)

    def _can_see_amounts(self, request, slug):
        """Whether this caller may see the value in reais. Rule R11.

        **Workspace Admin only.** A project Admin is not a workspace Admin in this fork,
        which is the same distinction the allowance endpoints already draw for crediting
        hours -- and money is at least as sensitive as an hour credit. A technician sees
        hours; what those hours are worth is commercial information.

        Resolved from ``WorkspaceMember`` rather than from the ``allow_permission``
        decorator, because the decorator has already let both roles through by this point:
        reading a work item's logs is legitimately open to Members, and only the monetary
        subset of the payload is not.
        """
        return WorkspaceMember.objects.filter(
            workspace__slug=slug,
            member=request.user,
            role=ROLE.ADMIN.value,
            is_active=True,
        ).exists()

    def _serializer_context(self, request):
        """Context every ``ServiceLogSerializer`` in this view is built with.

        Centralised so that adding an endpoint cannot accidentally omit it. Omitting it
        fails *safe* -- the serializer drops the monetary fields when the flag is absent --
        but a payload that silently loses a column an Admin expected is still a bug, and
        one method is easier to keep right than six call sites.
        """
        return {
            "request": request,
            "can_see_amounts": self._can_see_amounts(request, self.kwargs.get("slug")),
        }

    def _activity_payload(self, rows):
        """Work log rows as the **activity trail** may record them: never with money.

        Deliberately built with no context, so ``ServiceLogSerializer`` strips the
        monetary fields.

        **This is a leak that gating the response alone would not have closed.**
        ``_record_activity`` stores its argument in ``IssueActivity``, and a work item's
        activity feed is readable by every Member of the project. Reusing an Admin's
        response payload here -- which is the obvious thing to do, since it is already
        serialised -- would put the value in reais in front of exactly the audience R11
        keeps it from, one hop away from the endpoint that carefully withheld it. R11(b)
        is about the payload, and this is a payload.

        The hour quantities stay: R8 requires the trail, and hours are not the secret.
        """
        return ServiceLogSerializer(rows, many=True).data

    def _totals(self, issue_id, *, can_see_amounts=False):
        """The three hour totals, plus their pt-BR renderings, and the value for an Admin.

        Returned on every write so the work item panel never has to make a second
        request to refresh them, and cannot show a stale total next to a new row.

        The monetary total is **absent** rather than zero for a non-Admin, matching the
        serializer: R11(b) forbids sending a number the caller may not see, and a zero
        would be a number.

        Section 4 of the phase brief asks the work item to show the total value "para
        clientes avulsos". It is summed from the persisted ``amount`` column in the
        database, never recomputed from hours -- acceptance criterion 13.
        """
        totals = issue_service_log_totals(issue_id)

        payload = {
            **{field: str(value) for field, value in totals.items()},
            **{f"{field}_display": format_hours(value) for field, value in totals.items()},
        }

        if can_see_amounts:
            billed = issue_service_log_amount(issue_id)
            payload["amount"] = str(billed)
            payload["amount_display"] = format_money(billed)

        return payload

    def _record_activity(self, activity_type, request, issue, project_id, requested_data, current_instance):
        """Rule R8's audit trail, on the work item.

        ``IssueActivity`` via ``issue_activity.delay(...)``, following the link and
        attachment precedent -- a sub-entity records its activity against its parent
        work item. Section 6 of the master context keeps this trail separate from
        ``ServiceConfigActivity``, which is for configuration that affects money.

        ``notification=False``: the notification task branches on the activity type and
        has no case for these, and a work log is not something to email subscribers
        about.
        """
        issue_activity.delay(
            type=activity_type,
            requested_data=requested_data,
            actor_id=str(request.user.id),
            issue_id=str(issue.id),
            project_id=str(project_id),
            current_instance=current_instance,
            epoch=int(timezone.now().timestamp()),
            notification=False,
            origin=base_host(request=request, is_app=True),
        )

    def _display_name(self, user):
        """How a user is named in the activity feed.

        ``display_name`` is what Plane shows everywhere else; email is the fallback
        because a member invited but never onboarded can have it blank, and an audit entry
        reading "reassigned from  to " is an audit entry that failed.
        """
        return getattr(user, "display_name", None) or getattr(user, "email", "") or str(user.id)

    def _record_delegation_activity(self, request, issue, project_id, author):
        """R8's second fact, when it differs from the first.

        Emits nothing when somebody logs their own work, which is the overwhelming
        majority of writes -- a feed entry saying "Maria logged work on behalf of Maria"
        is noise, and noise in a trail is what teaches people to stop reading it.
        """
        if str(author.id) == str(request.user.id):
            return

        self._record_activity(
            "service_log.activity.delegated",
            request,
            issue,
            project_id,
            requested_data=json.dumps(
                {
                    "author_id": str(author.id),
                    "author_name": self._display_name(author),
                    "created_by_id": str(request.user.id),
                    "created_by_name": self._display_name(request.user),
                },
                cls=DjangoJSONEncoder,
            ),
            current_instance=None,
        )

    def _record_override_activity(self, request, issue, project_id, payload):
        """R10's override trail, when the technician disagreed with the engine.

        A separate activity type from the write itself because it answers a different
        question: not "how much time" but "why is this priced this way". Dispatched only
        when something actually diverged, which is never until the classification engine
        exists -- with no suggestion there can be no divergence.
        """
        if not any(row.get("is_hour_type_overridden") for row in payload):
            return

        self._record_activity(
            "service_log.activity.overridden",
            request,
            issue,
            project_id,
            requested_data=json.dumps(payload, cls=DjangoJSONEncoder),
            current_instance=None,
        )

    # --------------------------------------------------------------------- read

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def list(self, request, slug, project_id, issue_id):
        """Every work log of the work item, newest service date first.

        Not gated on ``is_time_tracking_enabled``: turning the toggle off must not
        retroactively hide work that was already logged and possibly already invoiced.
        """
        service_logs = self.get_queryset()
        context = self._serializer_context(request)

        return Response(
            {
                "service_logs": ServiceLogSerializer(service_logs, many=True, context=context).data,
                "totals": self._totals(issue_id, can_see_amounts=context["can_see_amounts"]),
            },
            status=status.HTTP_200_OK,
        )

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def totals(self, request, slug, project_id, issue_id):
        """Just the totals, for the work item header. Money only for an Admin."""
        return Response(
            self._totals(issue_id, can_see_amounts=self._can_see_amounts(request, slug)),
            status=status.HTTP_200_OK,
        )

    # ------------------------------------------------------------------ preview

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def preview(self, request, slug, project_id, issue_id):
        """What would be created, without creating it. Section 3.

        The preview has to be shown before saving, and it is computed by exactly the
        same code as the save -- ``build_batch_rows`` -- so it cannot promise one thing
        and persist another. That is the whole reason building rows is separate from
        persisting them.

        Also serves R1's real-time conversion display: submitting "1h 08min" comes back
        with 1.25 logged hours and ``was_rounded`` true, which is acceptance criterion 2.
        """
        issue = self._issue(slug, project_id, issue_id)

        if issue is None:
            return self._not_found()

        serializer = ServiceLogWriteSerializer(
            data=request.data, context={"workspace": issue.project.workspace}
        )

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            rows, error_code = self._build_rows(issue, serializer.validated_data, request.user)
        except ServiceLogValidationError as error:
            return Response({"error": error.code}, status=status.HTTP_400_BAD_REQUEST)

        if error_code:
            return Response({"error": error_code}, status=status.HTTP_400_BAD_REQUEST)

        raw_total = serializer.validated_data["raw_duration_minutes"]
        logged_total = sum(row.logged_hours for row in rows)

        context = self._serializer_context(request)

        # Section 4: the form shows the value before saving. Computed by the same function
        # the save uses, on the unsaved rows -- a preview computed by different code than
        # the save is a preview that can lie, which is why `settlement_fields` exists
        # separately from `settle_service_log`. Nothing is written.
        if context["can_see_amounts"]:
            for row in rows:
                for name, value in settlement_fields(row).items():
                    setattr(row, name, value)

        totals = {
            "logged_hours": str(logged_total),
            "equivalent_hours": str(sum(row.equivalent_hours for row in rows)),
            "debited_hours": str(sum(row.debited_hours for row in rows)),
        }

        if context["can_see_amounts"]:
            previewed = sum((row.amount for row in rows), ZERO_MONEY)
            totals["amount"] = str(previewed)
            totals["amount_display"] = format_money(previewed)

        return Response(
            {
                # Unsaved rows, so the same serializer renders the same shape the list
                # will show once saved.
                "segments": ServiceLogSerializer(rows, many=True, context=context).data,
                "raw_duration_minutes": raw_total,
                # Acceptance criterion 2: the UI has to say that rounding happened.
                "was_rounded": logged_total * 60 != raw_total,
                "totals": totals,
                "warning": self._long_entry_warning(rows),
            },
            status=status.HTTP_200_OK,
        )

    # -------------------------------------------------------------------- write

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def create(self, request, slug, project_id, issue_id):
        issue = self._issue(slug, project_id, issue_id)

        if issue is None:
            return self._not_found()

        try:
            validate_time_tracking_enabled(issue.project)
        except ServiceLogValidationError as error:
            return Response({"error": error.code}, status=status.HTTP_400_BAD_REQUEST)

        serializer = ServiceLogWriteSerializer(
            data=request.data, context={"workspace": issue.project.workspace}
        )

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        # Delegation. Section 3 of the Phase 7 brief: whoever holds the grant may name
        # another member as the author, and R8's two facts -- who did the work and who
        # typed it in -- then genuinely differ. `author` is the declared technician and
        # `created_by`, set by BaseModel.save() from crum, is the caller.
        #
        # Two checks, deliberately separate. The first asks whether the CALLER may
        # delegate at all; the second asks whether the DECLARED AUTHOR is somebody who
        # can perform work here. Collapsing them would let a grant holder attribute work
        # to a GUEST -- a client's own user -- which would put the client's name on the
        # labour side of an invoice.
        capabilities = self._capabilities(request, slug)
        author = request.user
        requested_author_id = serializer.validated_data.get("author_id")

        try:
            validate_can_delegate(requested_author_id, capabilities)

            if requested_author_id and str(requested_author_id) != str(request.user.id):
                author = validate_delegated_author(
                    requested_author_id, workspace_id=issue.project.workspace_id
                ).member
        except ServiceLogValidationError as error:
            return self._permission_error(error.code)

        try:
            rows, error_code = self._build_rows(issue, serializer.validated_data, author)
        except ServiceLogValidationError as error:
            return Response({"error": error.code}, status=status.HTTP_400_BAD_REQUEST)

        if error_code:
            return Response({"error": error_code}, status=status.HTTP_400_BAD_REQUEST)

        # Persist and settle in ONE transaction. A batch that exists without its pool
        # debit is the divergence the whole contract phase is built to prevent, and a
        # closed period has to refuse the entry (acceptance criterion 13) -- which only
        # works as a refusal if the rows roll back with it. Since Phase 6 "settle" also
        # covers pricing, so the same argument extends to the value in reais: a row that
        # exists without its amount would be an invoice line nobody can find.
        try:
            with transaction.atomic():
                create_service_log_batch(rows)
                settle_service_log_batch(rows, actor=request.user)
        except (ServicePoolValidationError, ServicePricingValidationError) as error:
            return self._pool_error(error)

        context = self._serializer_context(request)
        payload = ServiceLogSerializer(rows, many=True, context=context).data

        self._record_activity(
            "service_log.activity.created",
            request,
            issue,
            project_id,
            # NOT `payload`: the activity feed is readable by Members. See
            # `_activity_payload`.
            requested_data=json.dumps(self._activity_payload(rows), cls=DjangoJSONEncoder),
            current_instance=None,
        )
        self._record_override_activity(request, issue, project_id, payload)
        self._record_delegation_activity(request, issue, project_id, author)

        return Response(
            {
                "service_logs": payload,
                "batch_id": str(rows[0].batch_id),
                # Section 3: the response states the delegation explicitly rather than
                # leaving the client to compare two ids. Criterion 5 wants the list to
                # show both names, and a flag it can trust is cheaper than that comparison
                # repeated in every consumer.
                "was_delegated": str(author.id) != str(request.user.id),
                "totals": self._totals(issue_id, can_see_amounts=context["can_see_amounts"]),
                "warning": self._long_entry_warning(rows),
                # Section 7: the work item shows the pool it just debited, so the panel
                # never has to make a second request to refresh the balance and cannot
                # show a stale one beside a new row. Carries the out-of-vigency and
                # suspended warnings (D9, B4), which are advisory and never blocked the
                # entry that just succeeded.
                "pool": issue_pool_snapshot(issue, worked_on=serializer.validated_data["worked_on"]),
            },
            status=status.HTTP_201_CREATED,
        )

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def update_batch(self, request, slug, project_id, issue_id, batch_id):
        """Edit a whole entry. Acceptance criterion 16.

        The batch, not a single segment: widening an interval across a window boundary
        turns one row into two, so the entry is replaced rather than patched. Section 3
        requires the segments of one entry to be editable together.
        """
        issue = self._issue(slug, project_id, issue_id)

        if issue is None:
            return self._not_found()

        existing = list(self.get_queryset().filter(batch_id=batch_id))

        if not existing:
            return self._not_found()

        capabilities = self._capabilities(request, slug)

        try:
            validate_time_tracking_enabled(issue.project)
            # Phase 7: the author may edit their own, and a holder of
            # `can_manage_others` may edit anybody's. Replaces Phase 3's author-only rule.
            validate_can_change(existing[0], capabilities)
            # A CLOSED competency period is ADMIN only, and no grant unlocks it. See
            # `validate_closed_period_access` -- what the lock protects is the
            # reproducibility of a consolidation that has already been sent to the client,
            # which is broader than "no balance may move".
            validate_closed_period_access(existing, capabilities)
        except ServiceLogValidationError as error:
            return self._permission_error(error.code)

        serializer = ServiceLogWriteSerializer(
            data=request.data, context={"workspace": issue.project.workspace}
        )

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        # An edit does NOT move the author, and refusing is better than ignoring.
        #
        # Reassignment has its own route because it must not touch the ledger: this path
        # rebuilds the batch, which soft deletes the old rows and reverses each debit
        # before reapplying it. That leaves the balance where it started only by
        # cancellation, and it cannot run at all in a closed period, where the pool layer
        # refuses the reversal. Setting `author` in place -- what `reassign_author` does --
        # makes "changing the author moves no balance" true by construction instead.
        if serializer.validated_data.get("author_id") and str(
            serializer.validated_data["author_id"]
        ) != str(existing[0].author_id):
            return Response(
                {"error": SERVICE_LOG_AUTHOR_IS_REASSIGNED_SEPARATELY},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Captured before the swap so the activity generator can diff against it.
        current_instance = json.dumps(
            self._activity_payload(existing), cls=DjangoJSONEncoder
        )

        try:
            rows, error_code = self._build_rows(
                issue, serializer.validated_data, existing[0].author, batch_id=batch_id
            )
        except ServiceLogValidationError as error:
            return Response({"error": error.code}, status=status.HTTP_400_BAD_REQUEST)

        if error_code:
            return Response({"error": error_code}, status=status.HTTP_400_BAD_REQUEST)

        # Acceptance criterion 12: editing 2h to 3h must leave the balance consistent
        # with no double debit. `replace_service_log_batch` soft deletes the old rows,
        # and `ServiceLog.delete()` reverses each one's debit on the way out, so the
        # reversal and the new debit are one transaction. The reversal reads its amount
        # from the DEBIT row rather than recomputing it, which is what makes the old 2h
        # come back and not the new 3h.
        #
        # The monetary half needs no reversal of its own, and that is a real consequence of
        # where the value lives rather than an omission: the amount sits on the work log
        # row, so soft deleting the row removes it from every total by removing it from
        # `objects`. The recreated rows are then priced from scratch -- and price at the
        # same rate, because `resolve_log_price` reads the sheet in force on `worked_on`,
        # which an edit does not move unless the technician changed the service date.
        try:
            with transaction.atomic():
                replace_service_log_batch(batch_id=batch_id, rows=rows)
                settle_service_log_batch(rows, actor=request.user)
        except (ServicePoolValidationError, ServicePricingValidationError) as error:
            return self._pool_error(error)

        context = self._serializer_context(request)
        payload = ServiceLogSerializer(rows, many=True, context=context).data

        self._record_activity(
            "service_log.activity.updated",
            request,
            issue,
            project_id,
            # NOT `payload`: the activity feed is readable by Members. See
            # `_activity_payload`.
            requested_data=json.dumps(self._activity_payload(rows), cls=DjangoJSONEncoder),
            current_instance=current_instance,
        )
        self._record_override_activity(request, issue, project_id, payload)

        return Response(
            {
                "service_logs": payload,
                "batch_id": str(batch_id),
                "totals": self._totals(issue_id, can_see_amounts=context["can_see_amounts"]),
                "warning": self._long_entry_warning(rows),
                "pool": issue_pool_snapshot(issue, worked_on=serializer.validated_data["worked_on"]),
            },
            status=status.HTTP_200_OK,
        )

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def reassign_author(self, request, slug, project_id, issue_id, batch_id):
        """Change who a work log is credited to. Section 4, acceptance criterion 6.

        **Its own route, and it does not go through the batch rebuild.** ``author`` is set
        in place on every segment, so no ledger row is written and no debit is reversed --
        which is what makes "reassigning the author does not change the pool balance" true
        *structurally* rather than as the arithmetic coincidence of a reversal followed by
        an identical debit. It is also what lets a workspace ADMIN reassign inside a closed
        period: nothing moves, so the pool layer is never asked.

        Three guards, in order of what they protect:

        1. ``validate_can_reassign`` -- the **scoped** grant of D45. ``can_reassign_author``
           composes with edit rights instead of granting them, so holding it alone
           reassigns only your own logs;
        2. ``validate_closed_period_access`` -- ADMIN only once the competency is closed,
           because the technician's name is on the consolidation the client already
           received;
        3. ``validate_delegated_author`` -- the incoming author must be a technician of
           this workspace, never a GUEST.
        """
        issue = self._issue(slug, project_id, issue_id)

        if issue is None:
            return self._not_found()

        existing = list(self.get_queryset().filter(batch_id=batch_id))

        if not existing:
            return self._not_found()

        new_author_id = request.data.get("author_id")

        if not new_author_id:
            return Response(
                {"error": SERVICE_LOG_AUTHOR_IS_REQUIRED}, status=status.HTTP_400_BAD_REQUEST
            )

        capabilities = self._capabilities(request, slug)

        try:
            validate_can_reassign(existing[0], capabilities)
            validate_closed_period_access(existing, capabilities)
            membership = validate_delegated_author(
                new_author_id, workspace_id=issue.project.workspace_id
            )
        except ServiceLogValidationError as error:
            return self._permission_error(error.code)

        previous_author = existing[0].author

        # A reassignment to the incumbent is not an event. Returning 200 without writing
        # keeps the endpoint idempotent and keeps the trail free of entries recording that
        # nothing happened.
        if str(previous_author.id) == str(membership.member_id):
            context = self._serializer_context(request)
            return Response(
                {
                    "service_logs": ServiceLogSerializer(existing, many=True, context=context).data,
                    "batch_id": str(batch_id),
                    "was_reassigned": False,
                },
                status=status.HTTP_200_OK,
            )

        # Saved row by row rather than through `queryset.update()`, for the reason
        # `create_service_log_batch` gives: `update()` skips `BaseModel.save()`, which is
        # what stamps `updated_by` from crum -- and "quem alterou" is one of the four facts
        # section 4 requires the trail to carry. A batch is one to three rows.
        with transaction.atomic():
            for row in existing:
                row.author = membership.member
                row.save()

        self._record_activity(
            "service_log.activity.reassigned",
            request,
            issue,
            project_id,
            requested_data=json.dumps(
                {
                    "batch_id": str(batch_id),
                    "previous_author_id": str(previous_author.id),
                    "previous_author_name": self._display_name(previous_author),
                    "new_author_id": str(membership.member_id),
                    "new_author_name": self._display_name(membership.member),
                },
                cls=DjangoJSONEncoder,
            ),
            current_instance=None,
        )

        context = self._serializer_context(request)
        refreshed = list(self.get_queryset().filter(batch_id=batch_id))

        return Response(
            {
                "service_logs": ServiceLogSerializer(refreshed, many=True, context=context).data,
                "batch_id": str(batch_id),
                "was_reassigned": True,
                # The totals are returned unchanged on purpose, and a caller comparing them
                # across the call is asserting criterion 6: the pool balance did not move.
                "totals": self._totals(issue_id, can_see_amounts=context["can_see_amounts"]),
                "pool": issue_pool_snapshot(issue),
            },
            status=status.HTTP_200_OK,
        )

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def destroy_batch(self, request, slug, project_id, issue_id, batch_id):
        """Delete a whole entry, every segment of it. Acceptance criterion 11."""
        issue = self._issue(slug, project_id, issue_id)

        if issue is None:
            return self._not_found()

        existing = list(self.get_queryset().filter(batch_id=batch_id))

        if not existing:
            return self._not_found()

        capabilities = self._capabilities(request, slug)

        try:
            validate_can_change(existing[0], capabilities)
            validate_closed_period_access(existing, capabilities)
        except ServiceLogValidationError as error:
            return self._permission_error(error.code)

        current_instance = json.dumps(
            self._activity_payload(existing), cls=DjangoJSONEncoder
        )

        # Acceptance criterion 11: exactly the hours taken go back to the right period.
        # The reversal rides on `ServiceLog.delete()`, so it happens per row inside the
        # batch delete's own transaction. A work log whose period is already CLOSED
        # refuses here rather than silently changing an invoiced total.
        try:
            delete_service_log_batch(batch_id)
        except ServicePoolValidationError as error:
            return self._pool_error(error)

        self._record_activity(
            "service_log.activity.deleted",
            request,
            issue,
            project_id,
            requested_data=json.dumps({"batch_id": str(batch_id)}),
            current_instance=current_instance,
        )

        return Response(
            {
                "totals": self._totals(issue_id, can_see_amounts=self._can_see_amounts(request, slug)),
                "pool": issue_pool_snapshot(issue),
            },
            status=status.HTTP_200_OK,
        )



class ServiceLogClientEndpoint(BaseAPIView):
    """The work logs of one work item, as the client is allowed to see them. R11, D58.

    Separate from ``ServiceLogViewSet`` rather than a seventh action on it, because that
    viewset's guarantee is "GUEST is on no endpoint here, including reads" and that
    sentence should stay true. A GUEST action on it would make the guarantee conditional on
    a decorator, which is a worse thing to have to verify.

    **The projection is identical for every role that may call this.** An Admin asking this
    endpoint sees exactly what the client sees, which is the point: it gives an operator a
    way to check what the portal is showing before a client asks about it, and it means
    there is no role-conditional branch in here for R11 to be wrong inside. The unrestricted
    view of the same rows is ``ServiceLogViewSet.list``, one path over.

    What is absent by construction rather than by stripping: ``raw_duration_minutes``,
    ``logged_hours``, ``applied_multiplier``, the hour rate, the rate basis, and
    ``pricing_failure_reason``. None of them is in ``ServiceLogClientSerializer.Meta.fields``,
    so no context flag can restore them.

    Money follows the settled route of each individual row (D58), so one response can
    legitimately carry a pool-debited row with no amount beside a billed row with one.
    """

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST])
    def get(self, request, slug, project_id, issue_id):
        issue = (
            Issue.objects.filter(workspace__slug=slug, project_id=project_id, pk=issue_id)
            .select_related("project")
            .first()
        )

        if issue is None:
            return Response(
                {"error": "The required object does not exist."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # A client must not read the work logs of a work item the read endpoint refuses to
        # show them. Same condition `IssueViewSet.retrieve` applies, and the reason it has
        # to be repeated here is that this endpoint is reached by issue id directly rather
        # than through the work item.
        if is_client_portal_member(request.user, slug=slug, project_id=project_id) and not client_may_reach_issue(
            request.user, issue
        ):
            return Response(
                {"error": "You are not allowed to view this issue"},
                status=status.HTTP_403_FORBIDDEN,
            )

        service_logs = (
            ServiceLog.objects.filter(workspace__slug=slug, project_id=project_id, issue_id=issue_id)
            .select_related("author")
            # Not the catalogue foreign keys: they are DO_NOTHING and may point at a soft
            # deleted option, which the default manager would turn into a None join. The
            # serializer reads them through all_objects.
        )

        return Response(
            {
                "service_logs": ServiceLogClientSerializer(service_logs, many=True).data,
                "totals": issue_client_totals(issue_id),
            },
            status=status.HTTP_200_OK,
        )
