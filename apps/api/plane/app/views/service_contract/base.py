# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
from django.db import transaction

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions import ROLE, allow_permission
from plane.app.serializers import (
    ServiceAlertDismissalSerializer,
    ServiceContractedHoursSerializer,
    ServiceContractPeriodCloseSerializer,
    ServiceContractPeriodSerializer,
    ServiceContractRenewalSerializer,
    ServiceContractSerializer,
    ServiceContractSuccessorSerializer,
    ServiceHourLedgerEntrySerializer,
)
from plane.db.models import (
    Issue,
    ServiceClient,
    ServiceContract,
    ServiceContractPeriod,
    ServiceHourLedgerEntry,
    Workspace,
)
from plane.utils.service_catalog import (
    create_with_config_activity,
    delete_with_config_activity,
    save_with_config_activity,
)
from plane.utils.service_pool import (
    ServicePoolValidationError,
    close_period,
    contract_balance_statement,
    end_and_create_successor,
    issue_pool_snapshot,
    materialize_contract_periods,
    reconcile_period,
    renew_expiring_balance,
    renew_in_place,
    update_contracted_hours,
)
from plane.utils.service_pool_alerts import (
    contracts_without_default,
    dismiss_alert,
    visible_alerts_for_period,
    workspace_alert_panel,
    workspace_allowance_alerts,
)

from ..base import BaseAPIView, BaseViewSet


class ServiceContractViewSet(BaseViewSet):
    """Contracts of one workspace.

    **Writes are workspace ADMIN only, and GUEST is on no endpoint here at all.** The
    payload carries the monthly hours, the accrual ceiling and the overage hour rate --
    commercial terms, and from the pricing phase on, prices. That is the same reasoning
    that keeps the configuration audit trail admin-only.

    Reads are open to admins and members: a technician looking at a work item needs to
    know how much of the pool is left, which is section 7. What a *client* sees is Phase
    8's portal, with its own serializer.
    """

    serializer_class = ServiceContractSerializer
    model = ServiceContract
    search_fields = ["code", "name"]

    def get_queryset(self):
        return (
            ServiceContract.objects.filter(workspace__slug=self.kwargs.get("slug"))
            # `service_client` is DO_NOTHING and can point at a soft deleted row, which
            # `select_related` through the default manager would turn into a null join.
            # Resolved through `all_objects` where the name is actually needed.
            .select_related("workspace")
        )

    def _workspace(self, slug):
        return Workspace.objects.filter(slug=slug).first()

    def _not_found(self):
        return Response(
            {"error": "The required object does not exist."}, status=status.HTTP_404_NOT_FOUND
        )

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def list(self, request, slug):
        contracts = self.get_queryset()

        if request.GET.get("service_client_id"):
            contracts = contracts.filter(service_client_id=request.GET["service_client_id"])

        if request.GET.get("status"):
            contracts = contracts.filter(status=request.GET["status"])

        return Response(
            ServiceContractSerializer(contracts, many=True).data, status=status.HTTP_200_OK
        )

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def retrieve(self, request, slug, pk):
        contract = self.get_queryset().filter(pk=pk).first()

        if contract is None:
            return self._not_found()

        return Response(ServiceContractSerializer(contract).data, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def create(self, request, slug):
        """Register a contract, and materialise its competency periods.

        Acceptance criterion 1: the periods are generated deterministically from the
        vigency, here, rather than lazily on the first work log. A contract whose months
        only appear once somebody logs time would show an empty dashboard on the day it
        was signed, and the admin could not set a partial first month before any work
        arrived -- which decision B1 requires them to be able to do.
        """
        workspace = self._workspace(slug)

        if workspace is None:
            return self._not_found()

        serializer = ServiceContractSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        client = ServiceClient.objects.filter(
            pk=serializer.validated_data["service_client"].pk, workspace_id=workspace.id
        ).first()

        if client is None:
            return Response({"error": "SERVICE_CLIENT_NOT_FOUND"}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            contract = ServiceContract(workspace_id=workspace.id, **serializer.validated_data)
            create_with_config_activity(contract, actor=request.user)
            materialize_contract_periods(contract, actor=request.user)

        return Response(ServiceContractSerializer(contract).data, status=status.HTTP_201_CREATED)

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def partial_update(self, request, slug, pk):
        """Edit a contract's terms, with the change recorded.

        Changing ``monthly_hours`` deliberately does **not** rewrite existing periods:
        each one snapshotted the value it was created with (R4), so raising the monthly
        hours in June cannot reprice January. Periods materialised after the change pick
        up the new value.
        """
        contract = self.get_queryset().filter(pk=pk).first()

        if contract is None:
            return self._not_found()

        serializer = ServiceContractSerializer(contract, data=request.data, partial=True)

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            for field, value in serializer.validated_data.items():
                setattr(contract, field, value)

            save_with_config_activity(contract, actor=request.user)

        return Response(ServiceContractSerializer(contract).data, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def destroy(self, request, slug, pk):
        """Delete a contract, but only while it has moved no hours.

        A contract with ledger entries can be ended or suspended, never deleted. The
        ledger is what an invoice was built from, and its rows point here; deleting the
        contract would leave them unable to name what they belonged to, and a hard
        delete would fail against the DO_NOTHING constraint anyway. Same shape as the
        client delete guard.
        """
        contract = self.get_queryset().filter(pk=pk).first()

        if contract is None:
            return self._not_found()

        entry_count = ServiceHourLedgerEntry.all_objects.filter(contract_id=pk).count()

        if entry_count:
            return Response(
                {"error": "CONTRACT_HAS_LEDGER_ENTRIES", "ledger_entry_count": entry_count},
                status=status.HTTP_400_BAD_REQUEST,
            )

        delete_with_config_activity(contract, actor=request.user)

        return Response(status=status.HTTP_204_NO_CONTENT)

    # ------------------------------------------------------------------ periods

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def periods(self, request, slug, pk):
        """A contract's pool, month by month. Section 7.

        Includes each carried parcel with its competency of origin, which is what
        section 7 asks for and what a scalar total cannot answer.
        """
        contract = self.get_queryset().filter(pk=pk).first()

        if contract is None:
            return self._not_found()

        return Response(
            {
                "contract": ServiceContractSerializer(contract).data,
                "periods": contract_balance_statement(contract),
            },
            status=status.HTTP_200_OK,
        )

    # ----------------------------------------------------------------- renewal

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def renew(self, request, slug, pk):
        """Section 8, paths (a) and (b)."""
        contract = self.get_queryset().filter(pk=pk).first()

        if contract is None:
            return self._not_found()

        serializer = ServiceContractRenewalSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        mode = serializer.validated_data["mode"]

        if mode == ServiceContractRenewalSerializer.MODE_IN_PLACE:
            renew_in_place(
                contract,
                ends_on=serializer.validated_data["ends_on"],
                monthly_hours=serializer.validated_data.get("monthly_hours"),
                actor=request.user,
            )
            materialize_contract_periods(contract, actor=request.user)
        else:
            renew_expiring_balance(contract, actor=request.user)

        return Response(
            {
                "contract": ServiceContractSerializer(contract).data,
                "periods": contract_balance_statement(contract),
            },
            status=status.HTTP_200_OK,
        )

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def create_successor(self, request, slug, pk):
        """Section 8c, and acceptance criterion 17.

        All three balance destinations work. ``issue_allowance`` used to answer
        ``ISSUE_ALLOWANCE_NOT_AVAILABLE`` with a 501 because the allowance entity was
        Phase 5's; Phase 5 shipped it, so the destination now credits the target work
        item's allowance and the 501 branch is gone. A missing ``target_issue`` is a 400,
        because that one genuinely is the caller's mistake.
        """
        contract = self.get_queryset().filter(pk=pk).first()

        if contract is None:
            return self._not_found()

        serializer = ServiceContractSuccessorSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        validated = serializer.validated_data
        target_issue = None

        if validated.get("target_issue"):
            target_issue = Issue.objects.filter(pk=validated["target_issue"]).first()

        try:
            successor, moved = end_and_create_successor(
                contract,
                code=validated["code"],
                name=validated["name"],
                monthly_hours=validated["monthly_hours"],
                starts_on=validated["starts_on"],
                ends_on=validated["ends_on"],
                balance_destination=validated["balance_destination"],
                actor=request.user,
                target_issue=target_issue,
            )
        except ServicePoolValidationError as error:
            return Response(
                {"error": error.code, "detail": error.detail}, status=status.HTTP_400_BAD_REQUEST
            )

        materialize_contract_periods(successor, actor=request.user)

        return Response(
            {
                "contract": ServiceContractSerializer(successor).data,
                "previous_contract": ServiceContractSerializer(contract).data,
                "moved_entries": ServiceHourLedgerEntrySerializer(moved, many=True).data,
            },
            status=status.HTTP_201_CREATED,
        )


class ServiceContractPeriodViewSet(BaseViewSet):
    """One competency month: read it, close it, correct its quota, quiet its alerts."""

    serializer_class = ServiceContractPeriodSerializer
    model = ServiceContractPeriod

    def get_queryset(self):
        return ServiceContractPeriod.objects.filter(
            workspace__slug=self.kwargs.get("slug")
        ).select_related("contract", "contract__service_client", "closed_by")

    def _not_found(self):
        return Response(
            {"error": "The required object does not exist."}, status=status.HTTP_404_NOT_FOUND
        )

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def retrieve(self, request, slug, pk):
        """A period with its full statement: the movements and the live alerts."""
        period = self.get_queryset().filter(pk=pk).first()

        if period is None:
            return self._not_found()

        entries = ServiceHourLedgerEntry.objects.filter(period_id=period.pk).select_related(
            "origin_period", "actor"
        )

        return Response(
            {
                "period": ServiceContractPeriodSerializer(period).data,
                "ledger": ServiceHourLedgerEntrySerializer(entries, many=True).data,
                "alerts": visible_alerts_for_period(period),
                "reconciliation": reconcile_period(period),
            },
            status=status.HTTP_200_OK,
        )

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def close(self, request, slug, pk):
        """Close the month. Section 6, and criteria 8 and 9."""
        period = self.get_queryset().filter(pk=pk).first()

        if period is None:
            return self._not_found()

        serializer = ServiceContractPeriodCloseSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            closed = close_period(
                period,
                settlement=serializer.validated_data.get("overage_settlement"),
                actor=request.user,
            )
        except ServicePoolValidationError as error:
            return Response(
                {"error": error.code, "detail": error.detail}, status=status.HTTP_400_BAD_REQUEST
            )

        return Response(
            {
                "period": ServiceContractPeriodSerializer(closed).data,
                "reconciliation": reconcile_period(closed),
            },
            status=status.HTTP_200_OK,
        )

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def set_contracted_hours(self, request, slug, pk):
        """Correct the month's quota while it is open. Decision B1."""
        period = self.get_queryset().filter(pk=pk).first()

        if period is None:
            return self._not_found()

        serializer = ServiceContractedHoursSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            updated = update_contracted_hours(
                period, serializer.validated_data["contracted_hours"], actor=request.user
            )
        except ServicePoolValidationError as error:
            return Response(
                {"error": error.code, "detail": error.detail}, status=status.HTTP_400_BAD_REQUEST
            )

        return Response(ServiceContractPeriodSerializer(updated).data, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def dismiss_alert(self, request, slug, pk):
        """Acknowledge an alert for this period. Section 9, decision B2.

        Open to members as well as admins: section 9 puts the panel in front of
        technicians, and an alert nobody present can quiet is an alert everybody learns
        to ignore. The balance at this moment is recorded, so the alert comes back if
        things get materially worse.
        """
        period = self.get_queryset().filter(pk=pk).first()

        if period is None:
            return self._not_found()

        serializer = ServiceAlertDismissalSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        dismissal, created = dismiss_alert(
            period, serializer.validated_data["alert_code"], actor=request.user
        )

        return Response(
            {
                "alert_code": dismissal.alert_code,
                "balance_at_dismissal": str(dismissal.balance_at_dismissal),
                "created": created,
                "alerts": visible_alerts_for_period(period),
            },
            status=status.HTTP_200_OK,
        )


class ServiceContractAlertPanelEndpoint(BaseAPIView):
    """Clients that need attention, at both extremes of consumption. Section 9.

    Open to admins and members. Section 9 is explicit that the panel is "visível aos
    técnicos e ao Admin" -- a technician about to take on more work needs to know the
    pool is already overrun, and low consumption is a conversation somebody has to have
    before the renewal.
    """

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def get(self, request, slug):
        workspace = Workspace.objects.filter(slug=slug).first()

        if workspace is None:
            return Response(
                {"error": "The required object does not exist."}, status=status.HTTP_404_NOT_FOUND
            )

        # `reference_date` defaults to today, which is what the live panel wants. It is
        # accepted explicitly because several of these alerts are defined *relative to a
        # point in the month* -- "above the threshold before mid-month", "below it at
        # month end", "projected to overrun" -- so a report that asks "what did this look
        # like on the 10th" needs to say which 10th. Without it the answer would silently
        # depend on when the request happened to be made.
        entries = workspace_alert_panel(
            workspace.id,
            reference_date=_parse_date(request.GET.get("reference_date")),
            contract_id=request.GET.get("contract_id") or None,
        )

        return Response(
            {
                "entries": entries,
                "high_consumption_count": sum(1 for e in entries if e["has_high_consumption"]),
                "low_consumption_count": sum(1 for e in entries if e["has_low_consumption"]),
                # A configuration fault rather than a consumption alert, surfaced here
                # so it is fixed before it blocks a technician's first work log with
                # AMBIGUOUS_CONTRACT_RESOLUTION.
                "clients_without_default_contract": contracts_without_default(workspace.id),
                # Work item allowances, in a list of their own rather than folded into
                # `entries`: that structure is keyed by contract and competency and an
                # allowance has neither. Included here anyway so an admin opening one panel
                # sees both kinds of overrun -- a separate endpoint nobody visits is a
                # place alerts go to be ignored.
                "allowance_entries": workspace_allowance_alerts(workspace.id),
            },
            status=status.HTTP_200_OK,
        )


class IssueServicePoolEndpoint(BaseAPIView):
    """The pool position shown on a work item. Section 7.

    GUEST is excluded, like every other work log endpoint in this phase: the payload
    carries pool totals in debited hours, and the client's view of its own contract is
    Phase 8's portal with its own serializer.
    """

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def get(self, request, slug, project_id, issue_id):
        issue = (
            Issue.objects.filter(workspace__slug=slug, project_id=project_id, pk=issue_id)
            .select_related("project")
            .first()
        )

        if issue is None:
            return Response(
                {"error": "The required object does not exist."}, status=status.HTTP_404_NOT_FOUND
            )

        worked_on = request.GET.get("worked_on")

        return Response(
            issue_pool_snapshot(issue, worked_on=_parse_date(worked_on)),
            status=status.HTTP_200_OK,
        )


def _parse_date(value):
    """A ``YYYY-MM-DD`` query parameter, or ``None``.

    Returns ``None`` rather than raising on a malformed value: this endpoint is a panel
    read, and falling back to today is more useful than a 400 for a date the caller can
    correct by looking at the answer.
    """
    if not value:
        return None

    from django.utils.dateparse import parse_date

    return parse_date(value)
