# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""The work item hour allowance, over HTTP. Phase 5, section 4.

**Reading and writing are split by role, and the split is not cosmetic.** Crediting hours
is a commercial act -- it is the moment somebody decides a project is worth 40 hours -- so
it needs a **workspace ADMIN**, the same level every money-writing endpoint of the
contract phase requires. A project ADMIN is not a workspace ADMIN in this fork.

Reading is open to MEMBER as well, because section 4 puts the balance indicator on the
work item and the technician about to log against it is exactly who needs to see it.

GUEST is excluded from all of it, like every other work log endpoint in this feature: the
payload carries debited hours and pool totals, and the client's own view is Phase 8's
portal with its own serializer. Hiding it in the interface and sending it in the payload
is the leak R11(b) names.
"""

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions import ROLE, allow_permission
from plane.app.serializers import (
    ServiceAlertDismissalSerializer,
    ServiceAllowanceOverageRateSerializer,
    ServiceHourLedgerEntrySerializer,
    ServiceIssueAllowanceCreditSerializer,
    ServiceIssueAllowanceSerializer,
)
from plane.db.models import Issue, ServiceIssueAllowance, Workspace, WorkspaceMember
from plane.utils.service_allowance import (
    allowance_credits,
    allowance_overage_preview,
    close_allowance,
    credit_allowance,
    issue_allowance_snapshot,
    reconcile_allowance,
    resolve_work_item_allowance,
    set_allowance_overage_rate,
)
from plane.utils.service_pool import ServicePoolValidationError
from plane.utils.service_pricing import ServicePricingValidationError
from plane.utils.service_pool_alerts import (
    dismiss_alert,
    visible_alerts_for_allowance,
    workspace_allowance_alerts,
)

from ..base import BaseAPIView


def _not_found():
    return Response(
        {"error": "The required object does not exist."}, status=status.HTTP_404_NOT_FOUND
    )


def _can_see_amounts(request, slug):
    """Whether this caller may see the value in reais. Rule R11.

    Workspace ADMIN only, resolved from `WorkspaceMember` rather than from the
    `allow_permission` decorator -- the decorator has already let Members through, because
    reading the balance indicator is legitimately open to them, and only the monetary subset
    of the payload is not. Same helper, same reasoning, as the work log view's.
    """
    return WorkspaceMember.objects.filter(
        workspace__slug=slug,
        member=request.user,
        role=ROLE.ADMIN.value,
        is_active=True,
    ).exists()


class IssueServiceAllowanceEndpoint(BaseAPIView):
    """Read the allowance on a work item, or credit hours into it. Section 4."""

    def _issue(self, slug, project_id, issue_id):
        return (
            Issue.objects.filter(workspace__slug=slug, project_id=project_id, pk=issue_id)
            .select_related("project")
            .first()
        )

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def get(self, request, slug, project_id, issue_id):
        """The allowance that pays for work on this work item, inherited or its own.

        Resolves through the work item tree, so a sub-task reports the allowance that will
        actually be debited -- flagged as inherited, with the ancestor it came from,
        because a technician seeing 40h on a sub-task needs to know those hours belong to
        the parent project.

        ``null`` is a legitimate answer and means rule R6 falls through to the contract
        pool. The work item's contract panel is the separate ``service-pool`` endpoint.
        """
        issue = self._issue(slug, project_id, issue_id)

        if issue is None:
            return _not_found()

        allowance = resolve_work_item_allowance(issue)

        if allowance is None:
            return Response({"allowance": None}, status=status.HTTP_200_OK)

        return Response(
            {
                "allowance": ServiceIssueAllowanceSerializer(
                    allowance, context={"can_see_amounts": _can_see_amounts(request, slug)}
                ).data,
                "summary": issue_allowance_snapshot(issue),
                "alerts": visible_alerts_for_allowance(allowance),
                "credits": allowance_credits(allowance),
            },
            status=status.HTTP_200_OK,
        )

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def post(self, request, slug, project_id, issue_id):
        """Credit hours, creating the allowance on the first one. Criteria 1 and 5.

        Credits **this** work item, never an inherited ancestor's allowance: the Admin
        named a work item and the hours go where they pointed. Crediting through
        inheritance would put hours somewhere other than where they were aimed, which for
        a commercial act is unacceptable even when it is usually what was meant. To top up
        a parent project, credit the parent.
        """
        issue = self._issue(slug, project_id, issue_id)

        if issue is None:
            return _not_found()

        serializer = ServiceIssueAllowanceCreditSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        validated = serializer.validated_data

        try:
            allowance = credit_allowance(
                issue,
                validated["hours"],
                actor=request.user,
                reference=validated.get("reference"),
                notes=validated.get("notes"),
            )

            # The rate usually arrives with the credit, because the moment somebody decides a
            # project is worth 40 hours is the moment they know what an hour past them costs.
            # Applied through the audited setter rather than inline, so the change is recorded
            # as configuration either way. Absent means "leave it alone", which is why the
            # credit serializer marks it `required=False` while the dedicated endpoint's
            # serializer does not.
            if "overage_hour_rate" in validated:
                allowance = set_allowance_overage_rate(
                    allowance, validated["overage_hour_rate"], actor=request.user
                )
        except (ServicePoolValidationError, ServicePricingValidationError) as error:
            return Response(
                {"error": error.code, "detail": error.detail}, status=status.HTTP_400_BAD_REQUEST
            )

        return Response(
            {
                "allowance": ServiceIssueAllowanceSerializer(
                    allowance, context={"can_see_amounts": _can_see_amounts(request, slug)}
                ).data,
                "summary": issue_allowance_snapshot(issue),
                "alerts": visible_alerts_for_allowance(allowance),
                "credits": allowance_credits(allowance),
            },
            status=status.HTTP_200_OK,
        )


class ServiceIssueAllowanceCloseEndpoint(BaseAPIView):
    """Settle an allowance and stop it accepting movement. Section 3."""

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def post(self, request, slug, pk):
        """Close it: bill the deficit, or write off the surplus.

        Neither outcome is offered as a choice, and that is deliberate -- see
        ``close_allowance``. The brief's other option for a deficit, crediting more hours,
        is a credit **before** this call rather than a way of making it.

        The reconciliation result comes back in the response. Closing is the one moment
        the ledger has to sum to exactly zero, so this is the cheapest place to prove it
        did, and an operator who closed a month sees immediately whether the numbers agree
        instead of finding out when a client disputes an invoice.
        """
        allowance = (
            ServiceIssueAllowance.objects.filter(workspace__slug=slug, pk=pk)
            .select_related("issue")
            .first()
        )

        if allowance is None:
            return _not_found()

        try:
            closed = close_allowance(allowance, actor=request.user)
        except (ServicePoolValidationError, ServicePricingValidationError) as error:
            return Response(
                {"error": error.code, "detail": error.detail}, status=status.HTTP_400_BAD_REQUEST
            )

        return Response(
            {
                "allowance": ServiceIssueAllowanceSerializer(
                    closed, context={"can_see_amounts": True}
                ).data,
                "reconciliation": reconcile_allowance(closed),
                "entries": ServiceHourLedgerEntrySerializer(
                    closed.ledger_entries.order_by("created_at"), many=True
                ).data,
            },
            status=status.HTTP_200_OK,
        )

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def get(self, request, slug, pk):
        """What closing would bill, before closing. Phase 6.

        **Exists because billing an overage cannot be undone.** There is no reversal in this
        system -- D42 records what one would require -- so the Admin has to see the hours, the
        rate, the resulting value and the fact that it is final *before* confirming.

        ``overage_preview`` is ``None`` when there is nothing to bill, which is the honest
        answer for a surplus: offering an amount for it would invent one.
        """
        allowance = (
            ServiceIssueAllowance.objects.filter(workspace__slug=slug, pk=pk)
            .select_related("issue", "project")
            .first()
        )

        if allowance is None:
            return _not_found()

        try:
            preview = allowance_overage_preview(allowance)
        except (ServicePoolValidationError, ServicePricingValidationError) as error:
            return Response(
                {"error": error.code, "detail": error.detail}, status=status.HTTP_400_BAD_REQUEST
            )

        return Response({"overage_preview": preview}, status=status.HTTP_200_OK)


class ServiceIssueAllowanceOverageRateEndpoint(BaseAPIView):
    """The rate an hour past one allowance is billed at. Phase 6.

    Its own endpoint rather than a field on the credit body, because the two are different
    decisions taken at different moments: crediting hours is "this project is worth 40
    hours", and this is "an hour past them costs R$ 180,00". They usually arrive together,
    which is why the credit endpoint also accepts the rate -- but a renegotiation changes
    only this one, and forcing a zero-hour credit to express that would be absurd.

    Audited, because the rate is configuration that decides money. ``null`` clears it and
    restores the fallback to the client's base rate.
    """

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def post(self, request, slug, pk):
        allowance = (
            ServiceIssueAllowance.objects.filter(workspace__slug=slug, pk=pk)
            .select_related("issue", "project")
            .first()
        )

        if allowance is None:
            return _not_found()

        serializer = ServiceAllowanceOverageRateSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            updated = set_allowance_overage_rate(
                allowance,
                serializer.validated_data["overage_hour_rate"],
                actor=request.user,
            )
        except (ServicePoolValidationError, ServicePricingValidationError) as error:
            return Response(
                {"error": error.code, "detail": error.detail}, status=status.HTTP_400_BAD_REQUEST
            )

        return Response(
            {"allowance": ServiceIssueAllowanceSerializer(updated, context={"can_see_amounts": True}).data},
            status=status.HTTP_200_OK,
        )


class ServiceIssueAllowanceAlertPanelEndpoint(BaseAPIView):
    """Work item allowances that need attention. Section 3.

    A list of its own rather than an addition to
    ``ServiceContractAlertPanelEndpoint``'s entries, because that structure is keyed by
    contract and competency and an allowance has neither.

    Open to members as well as admins, for the reason section 9 gives about the contract
    panel: a technician about to take on more work on a project needs to know its
    allowance is already spent.
    """

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def get(self, request, slug):
        workspace = Workspace.objects.filter(slug=slug).first()

        if workspace is None:
            return _not_found()

        entries = workspace_allowance_alerts(
            workspace.id, project_id=request.GET.get("project_id") or None
        )

        return Response(
            {"entries": entries, "count": len(entries)},
            status=status.HTTP_200_OK,
        )


class ServiceIssueAllowanceDismissAlertEndpoint(BaseAPIView):
    """Acknowledge an alert for one work item allowance. D54.

    **The endpoint Phase 5 could not offer.** ``ServiceAlertDismissal.period`` was a
    mandatory foreign key, so there was nowhere to record the acknowledgement of an
    allowance alert; migration 0132 made the target a nullable pair with the exclusivity in
    DDL, following D29, and the dismissal now travels the same code path as a period's --
    including decision B2's re-arming, which needed no new code because an allowance has a
    ``balance_hours`` too.

    Open to members as well as admins, matching the period endpoint and for the same
    reason: section 9 puts the panel in front of technicians, and an alert nobody present
    can quiet is an alert everybody learns to ignore.

    **Dismissing ``ALLOWANCE_PENDING_CLOSURE`` is legitimate and does not forfeit
    anything.** It silences the reminder while the negotiation D35's grace period exists
    for is still happening. B2's band brings it back if the balance moves materially, and
    the hours are only ever written off by the close endpoint, which is a separate,
    deliberate, ADMIN-only act.
    """

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def post(self, request, slug, pk):
        allowance = (
            ServiceIssueAllowance.objects.filter(workspace__slug=slug, pk=pk)
            .select_related("issue", "project")
            .first()
        )

        if allowance is None:
            return _not_found()

        serializer = ServiceAlertDismissalSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        dismissal, created = dismiss_alert(
            allowance, serializer.validated_data["alert_code"], actor=request.user
        )

        return Response(
            {
                "alert_code": dismissal.alert_code,
                "balance_at_dismissal": str(dismissal.balance_at_dismissal),
                "created": created,
                "alerts": visible_alerts_for_allowance(allowance),
            },
            status=status.HTTP_200_OK,
        )
