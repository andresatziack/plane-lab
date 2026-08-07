# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Consumption dashboards and billing reports, over HTTP. Phase 9.

Five endpoints, and the split between them is by **audience and question**, not by chart:

============================ ================ =========================================
route                        roles            question
============================ ================ =========================================
``service-reports/consumption/``  ADMIN, MEMBER  what has this client consumed
``service-reports/operational/``  ADMIN, MEMBER  who worked on what
``service-reports/attention/``    ADMIN, MEMBER  what needs somebody to act
``service-reports/billing/``      ADMIN          what do we invoice this month
``service-reports/logs/``         ADMIN, MEMBER  **the one drill-down**
============================ ================ =========================================

**The role does not decide which endpoint you reach; it decides the shape you get.** A
Member calling ``consumption`` gets every hour figure and no money, because the money is
never computed for them -- see D50 and ``ReportViewer``. Only ``billing`` is Admin-only, and
that is because every number in it is money: there is no useful hours-only projection of "the
month's revenue by client".

**One drill-down endpoint, not one per chart.** Every bucket of every payload carries the
descriptor that produced it, and ``logs`` replays that descriptor. That is what makes
acceptance criterion 8 ("clicar em qualquer total leva à lista") and criterion 1 ("o número
bate com a soma") the same guarantee instead of two features that might agree -- see D49.
"""

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions import ROLE, allow_permission
from plane.app.serializers import ServiceIssueAllowanceSerializer, ServiceLogSerializer
from plane.db.models import (
    ServiceContract,
    ServiceIssueAllowance,
    Workspace,
    WorkspaceMember,
)
from plane.utils.service_allowance import allowance_credits
from plane.utils.service_billing import consolidated_billing
from plane.utils.service_pool import contract_balance_statement
from plane.utils.service_pool_alerts import (
    contracts_without_default,
    pending_closure_q,
    workspace_alert_panel,
    workspace_allowance_alerts,
)
from plane.utils.service_reports import (
    ReportViewer,
    distribution,
    headline_totals,
    hours_series,
    non_billable_breakdown,
    revenue_series,
)
from plane.utils.service_reports_filters import (
    CompetenceBasis,
    ServiceLogFilterSet,
    ServiceReportFilterError,
)

from ..base import BaseAPIView, BasePaginator


def _not_found():
    return Response(
        {"error": "The required object does not exist."}, status=status.HTTP_404_NOT_FOUND
    )


def _bad_filter(error):
    return Response(
        {"error": error.code, "detail": error.detail}, status=status.HTTP_400_BAD_REQUEST
    )


class ServiceReportBaseView(BaseAPIView):
    """Shared resolution of the two things every report needs: the workspace and the viewer.

    Centralised for the same reason ``ServiceLogViewSet._serializer_context`` is: forgetting
    it must fail *safe*. ``_viewer`` resolves to a Member unless workspace-Admin membership is
    proven, so an endpoint that forgets to pass the viewer through computes no money at all
    rather than computing it for everyone.
    """

    def _workspace(self, slug):
        return Workspace.objects.filter(slug=slug).first()

    def _viewer(self, request, slug):
        """Which projection this caller gets. R11, D50.

        Workspace ADMIN only for money, resolved from ``WorkspaceMember`` rather than from the
        ``allow_permission`` decorator -- the decorator has already let both roles through,
        because reading a consumption dashboard is legitimately open to Members and only the
        monetary subset of the payload is not. Same helper, same reasoning, as the work log
        view's ``_can_see_amounts``.

        A project Admin is not a workspace Admin in this fork.

        **GUEST never reaches here.** No route below admits it, and the client's own
        projection -- ``ReportViewer.guest()`` -- is built and tested at the domain layer for
        Phase 8 to mount (D55). Returning a Member projection to a Guest would be worse than
        a 403, because it carries ``logged_hours``.
        """
        is_admin = WorkspaceMember.objects.filter(
            workspace__slug=slug,
            member=request.user,
            role=ROLE.ADMIN.value,
            is_active=True,
        ).exists()

        return ReportViewer.admin() if is_admin else ReportViewer.member()

    def _filterset(self, request):
        return ServiceLogFilterSet.from_params(request.GET)


class ServiceConsumptionReportEndpoint(ServiceReportBaseView):
    """What a client has consumed. Sections 1, 1b and 1c.

    **One endpoint for the contract dashboard and the ad-hoc dashboard**, because which one
    applies is a property of the data and not of the caller: a client either holds a contract
    covering the window or does not, and asking the frontend to pick would be asking it to
    re-derive something the server already knows. The payload names which shape it returned in
    ``shape``.

    The contract half groups by ``debited_period`` and the ad-hoc half by ``worked_on``,
    which is D47 and is not a symmetry that was broken carelessly: D31 makes a work log dated
    before a contract's vigency debit the **first** period, so its ``worked_on`` month has no
    period at all. Grouping contract consumption by the service date would file those hours in
    a month the contract never had, and acceptance criterion 7 exists to catch exactly that.

    ``?service_client_id=`` selects the client. ``?include_group=true`` adds the economic
    group -- a parent client's children -- **listed separately and never summed into one
    pool**, because each contract has its own hours and merging them would hide a client that
    is overrunning behind a sibling that is not (section 1c).
    """

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def get(self, request, slug):
        workspace = self._workspace(slug)

        if workspace is None:
            return _not_found()

        viewer = self._viewer(request, slug)

        try:
            filterset = self._filterset(request)
        except ServiceReportFilterError as error:
            return _bad_filter(error)

        client_ids = list(filterset.service_client_ids)

        if request.GET.get("include_group") in ("true", "1", "yes") and client_ids:
            client_ids = self._with_children(workspace.id, client_ids)
            filterset = filterset.narrow(service_client_ids=tuple(client_ids))

        contracts = list(
            ServiceContract.objects.filter(
                workspace_id=workspace.id, **({"service_client_id__in": client_ids} if client_ids else {})
            ).select_related("service_client")
        )

        payload = {
            "shape": "contract" if contracts else "standalone",
            "totals": headline_totals(workspace.id, filterset, viewer),
            "distributions": {
                dimension: distribution(workspace.id, filterset, viewer, dimension=dimension)
                for dimension in ("hour_type", "billing_type")
            },
            # R5: never one number. Its own key so a frontend cannot render a combined
            # "não faturável" total by accident.
            "non_billable": non_billable_breakdown(workspace.id, filterset, viewer),
            "allowances": self._allowances(workspace.id, client_ids, viewer),
        }

        if contracts:
            # D47: the contract series is keyed by the period's competency.
            period_basis = filterset.narrow(competence_basis=CompetenceBasis.DEBITED_PERIOD)
            payload["consumption_series"] = hours_series(workspace.id, period_basis, viewer)
            payload["contracts"] = [
                {
                    "contract_id": str(contract.pk),
                    "code": contract.code,
                    "name": contract.name,
                    "service_client_id": str(contract.service_client_id),
                    "service_client_name": contract.service_client.name,
                    "status": contract.status,
                    # Listed per contract, never merged -- section 1c.
                    "statement": contract_balance_statement(contract),
                }
                for contract in contracts
            ]
        else:
            payload["consumption_series"] = hours_series(workspace.id, filterset, viewer)

        if viewer.can_see_money:
            payload["revenue_series"] = revenue_series(workspace.id, filterset, viewer)

        return Response(payload, status=status.HTTP_200_OK)

    def _with_children(self, workspace_id, client_ids):
        """A parent client plus its children. Section 1c.

        One level, matching ``ServiceClient.parent``: the model has no recursive hierarchy and
        inventing one here would be a report defining a data shape.
        """
        from plane.db.models import ServiceClient

        children = ServiceClient.objects.filter(
            workspace_id=workspace_id, parent_id__in=client_ids
        ).values_list("id", flat=True)

        return list(dict.fromkeys(list(client_ids) + list(children)))

    def _allowances(self, workspace_id, client_ids, viewer):
        """Active allowances, and the ones **awaiting a decision**, as separate lists.

        The split is the revised D35 (see ``ALLOWANCE_PENDING_CLOSURE``). An allowance whose
        work item closed more than the grace period ago is not an active pool -- it is a
        decision waiting on somebody -- and leaving it in "bolsas ativas" is what would make
        this panel fill with allowances from tickets closed months ago until it stopped being
        read.

        ``credits`` carries the history section 3b had no screen for: each credit with its
        author, its date and its origin competency. The API already returned it and nothing
        consumed it; the expandable panel is the consumer.
        """
        allowances = ServiceIssueAllowance.objects.filter(
            workspace_id=workspace_id, status=ServiceIssueAllowance.Status.OPEN
        ).select_related("issue", "project")

        if client_ids:
            allowances = allowances.filter(project__service_client_id__in=client_ids)

        pending = pending_closure_q()

        return {
            "active": [
                self._allowance_entry(allowance, viewer)
                for allowance in allowances.exclude(pending)
            ],
            "pending_closure": [
                self._allowance_entry(allowance, viewer)
                for allowance in allowances.filter(pending)
            ],
        }

    def _allowance_entry(self, allowance, viewer):
        """One allowance as the panel shows it, with its credit history.

        ``ServiceIssueAllowanceSerializer`` rather than a dict built here, so the money gating
        is the one Phase 5 already established and tested: ``overage_hour_rate`` is popped for
        a non-Admin by the serializer's own ``MONEY_FIELDS``. Re-deriving the projection would
        be a second place for R11 to be got wrong.
        """
        entry = ServiceIssueAllowanceSerializer(
            allowance, context={"can_see_amounts": viewer.can_see_money}
        ).data

        # Section 3b's missing screen. The API already returned this and nothing consumed it.
        entry["credits"] = allowance_credits(allowance)

        return entry


class ServiceOperationalReportEndpoint(ServiceReportBaseView):
    """Who worked on what. Section 3.

    Hours by technician, by project and by work item, for the team rather than for the
    invoice. Open to Members without reservation: section 3 is the view a coordinator uses to
    balance load, and a technician who cannot see the distribution cannot self-organise.

    Money appears for an Admin because the same screen answers "and what was that worth",
    but nothing here is money-shaped: remove the amounts and every chart still says something.
    That is the test of whether an endpoint is legitimately open to Members.
    """

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def get(self, request, slug):
        workspace = self._workspace(slug)

        if workspace is None:
            return _not_found()

        viewer = self._viewer(request, slug)

        try:
            filterset = self._filterset(request)
        except ServiceReportFilterError as error:
            return _bad_filter(error)

        return Response(
            {
                "totals": headline_totals(workspace.id, filterset, viewer),
                "series": hours_series(workspace.id, filterset, viewer),
                "distributions": {
                    dimension: distribution(workspace.id, filterset, viewer, dimension=dimension)
                    for dimension in ("author", "project", "service_client", "hour_type")
                },
                "non_billable": non_billable_breakdown(workspace.id, filterset, viewer),
            },
            status=status.HTTP_200_OK,
        )


class ServiceAttentionReportEndpoint(ServiceReportBaseView):
    """Everything that needs somebody to act. Section 3b.

    **One endpoint over three panels that Phases 4, 5 and 9 built separately**, because
    "what needs attention" is one question and answering it from three routes means a
    frontend deciding which of them to believe. The lists stay separate inside the payload,
    keyed by what they are about -- a contract period, an allowance, a configuration fault --
    since merging them would have meant giving every entry a nullable contract, which is the
    shape that makes a caller guess.

    Open to Admins and Members. Section 9 is explicit that the panel is "visível aos técnicos
    e ao Admin": a technician about to take on more work needs to know the pool is already
    overrun, and low consumption is a conversation somebody has to have before the renewal.

    Nothing here is money, so the payload is the same for both roles.
    """

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def get(self, request, slug):
        workspace = self._workspace(slug)

        if workspace is None:
            return _not_found()

        contract_id = request.GET.get("contract_id") or None
        project_id = request.GET.get("project_id") or None

        periods = workspace_alert_panel(workspace.id, contract_id=contract_id)
        allowances = workspace_allowance_alerts(workspace.id, project_id=project_id)
        misconfigured = contracts_without_default(workspace.id)

        return Response(
            {
                "periods": periods,
                "allowances": allowances,
                "clients_without_a_default_contract": misconfigured,
                # A count so a badge does not have to add three lists up and get it wrong.
                "count": len(periods) + len(allowances) + len(misconfigured),
            },
            status=status.HTTP_200_OK,
        )


class ServiceBillingReportEndpoint(ServiceReportBaseView):
    """What to invoice, for one competency and for a range. Section 2.

    **ADMIN only, and the only endpoint here that is.** Every number is money: the four
    revenue origins of D36, the two kinds of pendency, and the total. There is no useful
    hours-only projection of "the month's revenue by client" -- stripping the amounts leaves a
    list of clients -- so unlike the other reports this one does not have a Member shape.

    ``consolidated_billing`` for the single competency, unchanged from Phase 6: it is bounded
    to one month, it is the document somebody checks **before** issuing an invoice, and the
    screen needs its row list anyway, so its Python loop is the product rather than waste.
    The **series** is the unbounded one and that is aggregated in SQL.
    """

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def get(self, request, slug):
        workspace = self._workspace(slug)

        if workspace is None:
            return _not_found()

        try:
            filterset = self._filterset(request)
        except ServiceReportFilterError as error:
            return _bad_filter(error)

        if filterset.competence_to is None:
            return Response(
                {"error": "COMPETENCE_IS_REQUIRED"}, status=status.HTTP_400_BAD_REQUEST
            )

        viewer = ReportViewer.admin()
        year, month = filterset.competence_to
        client_ids = list(filterset.service_client_ids)

        return Response(
            {
                "consolidation": consolidated_billing(
                    workspace.id,
                    year,
                    month,
                    service_client_id=client_ids[0] if len(client_ids) == 1 else None,
                ),
                "revenue_series": revenue_series(workspace.id, filterset, viewer),
                "totals": headline_totals(workspace.id, filterset, viewer),
            },
            status=status.HTTP_200_OK,
        )


class ServiceReportLogsEndpoint(ServiceReportBaseView, BasePaginator):
    """**The one drill-down.** Section 8, acceptance criterion 8.

    Takes the same descriptor a bucket carried and returns the work logs behind it, paginated.
    One endpoint rather than one per chart, because the guarantee being made is that the list
    and the number come from the *same query* -- and two implementations of "the rows behind
    this bucket" is exactly how a list stops matching the total above it.

    ``totals`` is returned alongside the rows so the screen can show "12 apontamentos,
    37,5h" without a second request, and so a caller can verify the sum without paging
    through everything.

    Money is dropped from the rows by ``ServiceLogSerializer`` for a non-Admin, through
    ``can_see_amounts`` -- the same mechanism, and the same fail-safe default, that Phase 6
    established. **Commercial state stays** (D57): the settled route and the deviation reveal
    no value, and a technician who cannot see that a client's contract expired cannot warn
    anybody before spending another ten hours on it.
    """

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def get(self, request, slug):
        workspace = self._workspace(slug)

        if workspace is None:
            return _not_found()

        viewer = self._viewer(request, slug)

        try:
            filterset = self._filterset(request)
        except ServiceReportFilterError as error:
            return _bad_filter(error)

        rows = (
            filterset.queryset(workspace.id)
            .select_related("project", "project__service_client", "issue", "author")
            .order_by("-worked_on", "-created_at")
        )

        return self.paginate(
            request=request,
            queryset=rows,
            on_results=lambda results: ServiceLogSerializer(
                results,
                many=True,
                context={"request": request, "can_see_amounts": viewer.can_see_money},
            ).data,
            extra_stats={"totals": headline_totals(workspace.id, filterset, viewer)},
        )
