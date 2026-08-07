# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Price sheets, the effective rate table, and the monthly billing consolidation.

**Every endpoint here is workspace ADMIN.** R11 puts the value in reais out of a
technician's reach, and a price sheet is the value in reais before it has been applied. A
project Admin is not a workspace Admin in this fork -- the same distinction the allowance
endpoints already draw for crediting hours, and money is at least as sensitive as an hour
credit.

There is no Member-readable variant of any of this, deliberately. R11(b) makes the
restriction a serializer's job rather than an interface's, and the simplest form of that
here is that a Member never reaches the view at all.
"""

# Python imports
from datetime import date, datetime

# Django imports
from django.db import transaction
from django.utils import timezone

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions import ROLE, allow_permission
from plane.app.serializers import (
    ServiceClientHourTypeRateSerializer,
    ServiceClientPriceSerializer,
    ServiceLogExportHistorySerializer,
    ServiceLogExportRequestSerializer,
)
from plane.bgtasks.export_task import service_log_export_task
from plane.db.models import (
    ExporterHistory,
    Project,
    ServiceClient,
    ServiceClientHourTypeRate,
    ServiceClientPrice,
    ServiceHourType,
    Workspace,
)
from plane.utils.service_billing import consolidated_billing
from plane.utils.service_catalog import (
    create_with_config_activity,
    delete_with_config_activity,
    save_with_config_activity,
)
from plane.utils.service_pricing import (
    effective_rate_table,
    price_sheet_in_use,
    price_sheet_summary,
    resolve_price_sheet,
)

from ..base import BaseAPIView, BaseViewSet


def _not_found():
    return Response(
        {"error": "The required object does not exist."}, status=status.HTTP_404_NOT_FOUND
    )


def _parse_date(value):
    """A ``YYYY-MM-DD`` query parameter, or ``None`` when absent or malformed.

    ``None`` rather than a 400 on malformed input, following ``_parse_date`` in the contract
    views: these are optional viewing parameters, and a typo in a URL an operator pasted
    should show today's table rather than an error page.
    """
    if not value:
        return None

    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


class ServiceClientPriceViewSet(BaseViewSet):
    """A client's dated price sheets. Section 1 and section 2.

    Audited with all three ``ServiceConfigVerb`` values, which is what D22 asked the pricing
    phase for by name: registering a vigency is ``CREATED``, changing its base rate is
    ``UPDATED``, and removing one is ``DELETED``. "When was this hour rate registered, and by
    whom" has to be answerable months later.
    """

    serializer_class = ServiceClientPriceSerializer
    model = ServiceClientPrice

    def get_queryset(self):
        # `service_client` is not select_related: the foreign key is DO_NOTHING and may point
        # at a soft deleted client, which the forward descriptor would raise on.
        return ServiceClientPrice.objects.filter(
            workspace__slug=self.kwargs.get("slug"),
            service_client_id=self.kwargs.get("service_client_id"),
        ).select_related("workspace")

    def _client(self, slug, service_client_id):
        return ServiceClient.objects.filter(
            workspace__slug=slug, pk=service_client_id
        ).first()

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def list(self, request, slug, service_client_id):
        """Every vigency of this client, newest first, each with its overrides."""
        service_client = self._client(slug, service_client_id)

        if service_client is None:
            return _not_found()

        sheets = self.get_queryset().order_by("-starts_on")
        on_date = _parse_date(request.GET.get("on_date")) or timezone.now().date()
        in_force = resolve_price_sheet(service_client_id, on_date)

        return Response(
            {
                "prices": [price_sheet_summary(sheet) for sheet in sheets],
                # Which one actually applies on the date being viewed, resolved by the same
                # function that prices a work log -- so the screen cannot disagree with the
                # invoice about which sheet is in force.
                "in_force_price_id": str(in_force.pk) if in_force is not None else None,
                "on_date": str(on_date),
                "effective_table": effective_rate_table(
                    service_client_id, on_date, workspace_id=service_client.workspace_id
                ),
            },
            status=status.HTTP_200_OK,
        )

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def create(self, request, slug, service_client_id):
        """Register a new vigency. This is what an annual readjustment is.

        A readjustment does **not** edit the current sheet -- it adds the next one, which is
        what keeps history intact (section 2) and what makes a retroactive work log price at
        the rate that applied on its service date.
        """
        service_client = self._client(slug, service_client_id)

        if service_client is None:
            return _not_found()

        serializer = ServiceClientPriceSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        if self.get_queryset().filter(starts_on=serializer.validated_data["starts_on"]).exists():
            # Also a partial unique index; this is the readable message. Two sheets starting
            # on the same day would make the rate for that day ambiguous, which is the state
            # the whole "no ends_on" design exists to prevent.
            return Response(
                {"error": "PRICE_VIGENCY_ALREADY_EXISTS"}, status=status.HTTP_400_BAD_REQUEST
            )

        price = ServiceClientPrice(
            workspace_id=service_client.workspace_id,
            service_client_id=service_client.pk,
            **serializer.validated_data,
        )

        with transaction.atomic():
            create_with_config_activity(price, actor=request.user)

        return Response(price_sheet_summary(price), status=status.HTTP_201_CREATED)

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def partial_update(self, request, slug, service_client_id, pk):
        """Correct a sheet that was registered wrong.

        Editing is for **corrections**, not readjustments -- a readjustment is a new vigency.
        Nothing stops an edit to a sheet that already priced work logs, and it does not
        reprice them: those rows carry their own R4 snapshot, which is exactly what
        acceptance criterion 6 requires.
        """
        price = self.get_queryset().filter(pk=pk).first()

        if price is None:
            return _not_found()

        serializer = ServiceClientPriceSerializer(price, data=request.data, partial=True)

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            for field, value in serializer.validated_data.items():
                setattr(price, field, value)

            save_with_config_activity(price, actor=request.user)

        return Response(price_sheet_summary(price), status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def destroy(self, request, slug, service_client_id, pk):
        """Remove a vigency that should never have existed.

        **Refused once the vigency has priced work.** ``validate_catalog_delete``'s docstring
        named this as the pricing phase's extension point, and the reason is auditability
        rather than referential integrity: those work logs carry a rate snapshot, and
        deleting the sheet would leave nobody able to look up where that number came from
        when a client disputes an invoice.

        A sheet that priced nothing is safe to remove, and removing it correctly restores the
        previous sheet -- resolution is a fresh ordered read, not a stored pointer.
        """
        price = self.get_queryset().filter(pk=pk).first()

        if price is None:
            return _not_found()

        if price_sheet_in_use(price):
            return Response(
                {"error": "PRICE_VIGENCY_ALREADY_PRICED_WORK_LOGS"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            # The overrides are children of the sheet, so they go with it. Each is audited in
            # its own right: removing an override is a commercial decision, not a cascade
            # detail.
            for override in ServiceClientHourTypeRate.objects.filter(price_id=price.pk):
                delete_with_config_activity(override, actor=request.user)

            delete_with_config_activity(price, actor=request.user)

        return Response(status=status.HTTP_204_NO_CONTENT)


class ServiceClientHourTypeRateViewSet(BaseViewSet):
    """The absolute overrides of one price sheet. Section 1, acceptance criterion 5."""

    serializer_class = ServiceClientHourTypeRateSerializer
    model = ServiceClientHourTypeRate

    def get_queryset(self):
        return ServiceClientHourTypeRate.objects.filter(
            workspace__slug=self.kwargs.get("slug"),
            price_id=self.kwargs.get("price_id"),
        ).select_related("workspace")

    def _price(self, slug, price_id):
        return ServiceClientPrice.objects.filter(workspace__slug=slug, pk=price_id).first()

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def create(self, request, slug, price_id):
        price = self._price(slug, price_id)

        if price is None:
            return _not_found()

        serializer = ServiceClientHourTypeRateSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        hour_type = serializer.validated_data["hour_type"]

        if str(hour_type.workspace_id) != str(price.workspace_id):
            # Configuration pointing outside its workspace would be unreachable but still
            # audited, which is worse than refusing it.
            return Response(
                {"error": "HOUR_TYPE_NOT_FOUND"}, status=status.HTTP_400_BAD_REQUEST
            )

        if self.get_queryset().filter(hour_type_id=hour_type.pk).exists():
            return Response(
                {"error": "HOUR_TYPE_OVERRIDE_ALREADY_EXISTS"}, status=status.HTTP_400_BAD_REQUEST
            )

        override = ServiceClientHourTypeRate(
            workspace_id=price.workspace_id,
            price_id=price.pk,
            hour_type=hour_type,
            absolute_rate=serializer.validated_data["absolute_rate"],
        )

        with transaction.atomic():
            create_with_config_activity(override, actor=request.user)

        return Response(
            ServiceClientHourTypeRateSerializer(override).data, status=status.HTTP_201_CREATED
        )

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def partial_update(self, request, slug, price_id, pk):
        override = self.get_queryset().filter(pk=pk).first()

        if override is None:
            return _not_found()

        serializer = ServiceClientHourTypeRateSerializer(override, data=request.data, partial=True)

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            # `hour_type` is not reassignable: moving an override between hour types would be
            # indistinguishable in the audit trail from removing one and adding another, and
            # those are two decisions.
            override.absolute_rate = serializer.validated_data.get(
                "absolute_rate", override.absolute_rate
            )
            save_with_config_activity(override, actor=request.user)

        return Response(ServiceClientHourTypeRateSerializer(override).data, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def destroy(self, request, slug, price_id, pk):
        """Remove an override, restoring ``base * multiplier`` for that hour type.

        Not refused for having priced work, unlike a whole sheet: the rows it priced carry
        their own snapshot, and the sheet they point at still exists to explain the base.
        """
        override = self.get_queryset().filter(pk=pk).first()

        if override is None:
            return _not_found()

        with transaction.atomic():
            delete_with_config_activity(override, actor=request.user)

        return Response(status=status.HTTP_204_NO_CONTENT)


class ServiceEffectiveRateTableEndpoint(BaseAPIView):
    """The rate that actually applies to each hour type, for a client on a date.

    Section 1: "a tela deve exibir a tabela efetiva". Derived on every read rather than
    stored -- a stored copy would be the second price table that section 2b of the master
    context and D16 exist to prevent, and it would drift from the multiplier the first time
    either changed.
    """

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def get(self, request, slug, service_client_id):
        service_client = ServiceClient.objects.filter(
            workspace__slug=slug, pk=service_client_id
        ).first()

        if service_client is None:
            return _not_found()

        on_date = _parse_date(request.GET.get("on_date")) or timezone.now().date()
        table = effective_rate_table(
            service_client_id, on_date, workspace_id=service_client.workspace_id
        )
        in_force = resolve_price_sheet(service_client_id, on_date)

        return Response(
            {
                "on_date": str(on_date),
                "effective_table": table,
                "base_hour_rate": str(in_force.base_hour_rate) if in_force is not None else None,
                "in_force_since": str(in_force.starts_on) if in_force is not None else None,
                # Empty is a legitimate answer and the reason travels with it, so the screen
                # says "no price registered" rather than showing an empty table with no
                # explanation. Which of the two registration mistakes it was matters.
                "hour_types": ServiceHourType.objects.filter(
                    workspace_id=service_client.workspace_id, is_active=True
                ).count(),
            },
            status=status.HTTP_200_OK,
        )


class ServiceBillingConsolidationEndpoint(BaseAPIView):
    """Everything billable in one competency, by client and by origin. Section 5.

    The four origins of D36, plus the two kinds of pendency kept apart, plus internal work
    reported outside revenue. See ``plane.utils.service_billing.consolidated_billing``.

    ``ADMIN`` and workspace level, unlike the issue export endpoint which also admits
    Members: this is the whole month's revenue.
    """

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def get(self, request, slug):
        workspace = Workspace.objects.filter(slug=slug).first()

        if workspace is None:
            return _not_found()

        today = timezone.now().date()

        try:
            year = int(request.GET.get("year", today.year))
            month = int(request.GET.get("month", today.month))
        except (TypeError, ValueError):
            return Response({"error": "INVALID_COMPETENCE"}, status=status.HTTP_400_BAD_REQUEST)

        if not 1 <= month <= 12:
            return Response({"error": "INVALID_COMPETENCE"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            date(year, month, 1)
        except ValueError:
            return Response({"error": "INVALID_COMPETENCE"}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            consolidated_billing(
                workspace.id,
                year,
                month,
                service_client_id=request.GET.get("service_client_id") or None,
            ),
            status=status.HTTP_200_OK,
        )



class ServiceLogExportEndpoint(BaseAPIView):
    """Queue a work log export, and read the history of them. Acceptance criterion 11.

    **Its own route and its own history rather than a parameter on the issue export.** The
    two share the pipeline but not the audience: exporting issues is open to Members, and
    this carries the value in reais, so it is Admin only (R11). Bolting a ``type`` switch
    onto the existing endpoint would have meant one route with two permission levels
    depending on a body field, which is the shape permission bugs live in.

    It also means the issue export's ``GET``, which filters ``type="issue_exports"``, stays
    correct instead of needing to learn about a type it does not serve -- and a worklog
    export is not invisible, because it is listed here.
    """

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def post(self, request, slug):
        """Queue it. Fire and forget, following ``ExportIssuesEndpoint``.

        The response carries the ``token``, which that endpoint does not return -- so a
        client can poll this one export instead of diffing the whole history.
        """
        workspace = Workspace.objects.filter(slug=slug).first()

        if workspace is None:
            return _not_found()

        serializer = ServiceLogExportRequestSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        validated = serializer.validated_data
        project_ids = [str(pk) for pk in validated.get("project", [])]

        if not project_ids:
            # Default to every project the caller can actually see, matching
            # `ExportIssuesEndpoint`. The task re-checks membership anyway, so this is the
            # convenient default rather than the security boundary.
            project_ids = [
                str(pk)
                for pk in Project.objects.filter(
                    workspace=workspace,
                    project_projectmember__member=request.user,
                    project_projectmember__is_active=True,
                    archived_at__isnull=True,
                ).values_list("id", flat=True)
            ]

        filters = {
            key: str(validated[key])
            for key in ("year", "month", "service_client_id")
            if validated.get(key) is not None
        }

        exporter = ExporterHistory.objects.create(
            workspace=workspace,
            project=project_ids,
            initiated_by=request.user,
            provider=validated["provider"],
            # The choice that has been on the model with no handler behind it. This is the
            # handler.
            type="issue_worklogs",
            # `filters` has likewise been on the model unused. A monthly consolidation is
            # always of something, and recording what makes the history legible.
            filters=filters,
        )

        service_log_export_task.delay(
            provider=validated["provider"],
            workspace_id=str(workspace.id),
            project_ids=project_ids,
            token_id=exporter.token,
            slug=slug,
            filters=filters,
        )

        return Response(
            {
                "message": "Once the export is ready you will be able to download it",
                "token": exporter.token,
            },
            status=status.HTTP_200_OK,
        )

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def get(self, request, slug):
        """The history of work log exports, newest first.

        Unlike ``ExportIssuesEndpoint.get`` this does not demand ``per_page`` and ``cursor``:
        that endpoint returns a 400 without them, which is a footgun for a caller that just
        wants the latest few. Paginated when asked, capped when not.
        """
        exports = ExporterHistory.objects.filter(
            workspace__slug=slug, type="issue_worklogs"
        ).select_related("workspace", "initiated_by")

        return Response(
            ServiceLogExportHistorySerializer(exports[:20], many=True).data,
            status=status.HTTP_200_OK,
        )
