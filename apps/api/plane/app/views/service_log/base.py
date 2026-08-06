# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
import json

# Django imports
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions import ROLE, allow_permission
from plane.app.serializers import ServiceLogSerializer, ServiceLogWriteSerializer
from plane.bgtasks.issue_activities_task import issue_activity
from plane.db.models import Issue, ServiceBillingType, ServiceHourType, ServiceLog
from plane.utils.host import base_host
from plane.utils.service_log import (
    ServiceLogValidationError,
    build_batch_rows,
    build_segments,
    create_service_log_batch,
    delete_service_log_batch,
    issue_service_log_totals,
    replace_service_log_batch,
    validate_author_can_change,
    validate_time_tracking_enabled,
)
from plane.utils.service_log_time import LONG_ENTRY_WARNING_MINUTES, format_hours

from ..base import BaseViewSet


class ServiceLogViewSet(BaseViewSet):
    """Work logs of one work item.

    **GUEST is on no endpoint here**, including reads. Section 6 of the phase brief is
    explicit -- "não expor apontamento a papel GUEST em nenhum endpoint" -- and the
    payload carries the raw duration and the multiplier that R11 keeps from clients.
    The client portal gets its own endpoint and its own serializer in Phase 8;
    ``ServiceLogClientSerializer`` is already written to that allowlist.

    Reads are open to project admins and members. Writes additionally require the
    project to have switched the feature on, and editing or deleting is restricted to
    the log's author until Phase 7 brings real permissions.
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

    def _totals(self, issue_id):
        """The three totals, plus their pt-BR renderings. Section 6.

        Returned on every write so the work item panel never has to make a second
        request to refresh them, and cannot show a stale total next to a new row.
        """
        totals = issue_service_log_totals(issue_id)

        return {
            **{field: str(value) for field, value in totals.items()},
            **{f"{field}_display": format_hours(value) for field, value in totals.items()},
        }

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

        return Response(
            {
                "service_logs": ServiceLogSerializer(service_logs, many=True).data,
                "totals": self._totals(issue_id),
            },
            status=status.HTTP_200_OK,
        )

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def totals(self, request, slug, project_id, issue_id):
        """Just the three totals, for the work item header."""
        return Response(self._totals(issue_id), status=status.HTTP_200_OK)

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

        return Response(
            {
                # Unsaved rows, so the same serializer renders the same shape the list
                # will show once saved.
                "segments": ServiceLogSerializer(rows, many=True).data,
                "raw_duration_minutes": raw_total,
                # Acceptance criterion 2: the UI has to say that rounding happened.
                "was_rounded": logged_total * 60 != raw_total,
                "totals": {
                    "logged_hours": str(logged_total),
                    "equivalent_hours": str(sum(row.equivalent_hours for row in rows)),
                    "debited_hours": str(sum(row.debited_hours for row in rows)),
                },
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

        # Delegation is Phase 7. Until the permission that authorises it exists, an
        # author other than the caller would let anyone attribute work to a colleague.
        if serializer.validated_data.get("author_id") and str(
            serializer.validated_data["author_id"]
        ) != str(request.user.id):
            return Response(
                {"error": "SERVICE_LOG_DELEGATION_NOT_AVAILABLE"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            rows, error_code = self._build_rows(issue, serializer.validated_data, request.user)
        except ServiceLogValidationError as error:
            return Response({"error": error.code}, status=status.HTTP_400_BAD_REQUEST)

        if error_code:
            return Response({"error": error_code}, status=status.HTTP_400_BAD_REQUEST)

        create_service_log_batch(rows)

        payload = ServiceLogSerializer(rows, many=True).data

        self._record_activity(
            "service_log.activity.created",
            request,
            issue,
            project_id,
            requested_data=json.dumps(payload, cls=DjangoJSONEncoder),
            current_instance=None,
        )
        self._record_override_activity(request, issue, project_id, payload)

        return Response(
            {
                "service_logs": payload,
                "batch_id": str(rows[0].batch_id),
                "totals": self._totals(issue_id),
                "warning": self._long_entry_warning(rows),
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

        try:
            validate_time_tracking_enabled(issue.project)
            validate_author_can_change(existing[0], request.user)
        except ServiceLogValidationError as error:
            code = error.code
            return Response(
                {"error": code},
                status=(
                    status.HTTP_403_FORBIDDEN
                    if code == "ONLY_THE_AUTHOR_CAN_CHANGE_A_SERVICE_LOG"
                    else status.HTTP_400_BAD_REQUEST
                ),
            )

        serializer = ServiceLogWriteSerializer(
            data=request.data, context={"workspace": issue.project.workspace}
        )

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        # Captured before the swap so the activity generator can diff against it.
        current_instance = json.dumps(
            ServiceLogSerializer(existing, many=True).data, cls=DjangoJSONEncoder
        )

        try:
            rows, error_code = self._build_rows(
                issue, serializer.validated_data, existing[0].author, batch_id=batch_id
            )
        except ServiceLogValidationError as error:
            return Response({"error": error.code}, status=status.HTTP_400_BAD_REQUEST)

        if error_code:
            return Response({"error": error_code}, status=status.HTTP_400_BAD_REQUEST)

        replace_service_log_batch(batch_id=batch_id, rows=rows)

        payload = ServiceLogSerializer(rows, many=True).data

        self._record_activity(
            "service_log.activity.updated",
            request,
            issue,
            project_id,
            requested_data=json.dumps(payload, cls=DjangoJSONEncoder),
            current_instance=current_instance,
        )
        self._record_override_activity(request, issue, project_id, payload)

        return Response(
            {
                "service_logs": payload,
                "batch_id": str(batch_id),
                "totals": self._totals(issue_id),
                "warning": self._long_entry_warning(rows),
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

        try:
            validate_author_can_change(existing[0], request.user)
        except ServiceLogValidationError as error:
            return Response({"error": error.code}, status=status.HTTP_403_FORBIDDEN)

        current_instance = json.dumps(
            ServiceLogSerializer(existing, many=True).data, cls=DjangoJSONEncoder
        )

        delete_service_log_batch(batch_id)

        self._record_activity(
            "service_log.activity.deleted",
            request,
            issue,
            project_id,
            requested_data=json.dumps({"batch_id": str(batch_id)}),
            current_instance=current_instance,
        )

        return Response({"totals": self._totals(issue_id)}, status=status.HTTP_200_OK)
