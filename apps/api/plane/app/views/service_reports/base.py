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
never computed for them -- see D51 and ``ReportViewer``. Only ``billing`` is Admin-only, and
that is because every number in it is money: there is no useful hours-only projection of "the
month's revenue by client".

**One drill-down endpoint, not one per chart.** Every bucket of every payload carries the
descriptor that produced it, and ``logs`` replays that descriptor. That is what makes
acceptance criterion 8 ("clicar em qualquer total leva à lista") and criterion 1 ("o número
bate com a soma") the same guarantee instead of two features that might agree -- see D50.
"""

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions import ROLE, allow_permission
from plane.app.serializers import ServiceIssueAllowanceSerializer, ServiceLogSerializer
from plane.db.models import (
    Project,
    ServiceContract,
    ServiceIssueAllowance,
    ServiceLog,
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
    issue_row,
    issue_rows,
    non_billable_breakdown,
    revenue_series,
)
from plane.utils.service_log import ServiceLogValidationError
from plane.utils.service_portal import (
    client_project_scope,
    client_visible_issues_q,
    is_client_portal_member,
    scope_filterset_to_client,
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
        """Which projection this caller gets. R11, D51.

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

        On the base view rather than on the consumption endpoint because Phase 8's portal
        shows the same panel to the client. The money projection travels with ``viewer``, so
        the client gets the identical structure with ``overage_hour_rate`` popped -- moving it
        here rather than copying it is what keeps that from being two decisions.
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


class ServiceConsumptionReportEndpoint(ServiceReportBaseView):
    """What a client has consumed. Sections 1, 1b and 1c.

    **One endpoint for the contract dashboard and the ad-hoc dashboard**, because which one
    applies is a property of the data and not of the caller: a client either holds a contract
    covering the window or does not, and asking the frontend to pick would be asking it to
    re-derive something the server already knows. The payload names which shape it returned in
    ``shape``.

    The contract half groups by ``debited_period`` and the ad-hoc half by ``worked_on``,
    which is D48 and is not a symmetry that was broken carelessly: D31 makes a work log dated
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
            # D48: the contract series is keyed by the period's competency.
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



class ServicePortalBaseView(ServiceReportBaseView):
    """The client's projection and the client's tenancy, for **every** portal route. D63, D67.

    Extracted when the portal gained its second route (the ticket table, Phase 10 item 7),
    and the extraction is the point rather than a tidy-up. Phase 10's own finding is that
    every defect it found lived *between* two places that should have agreed -- a component
    mounted in the detail and not in the peek, an ACL key computed one way and indexed
    another. A second portal endpoint that restated ``_viewer`` and re-ran the scope sequence
    by hand would be that shape exactly: two copies of R11's projection and two copies of a
    tenancy boundary, correct on the day they were written.

    So both live here, once, and a new portal route gets them by inheriting.
    """

    def _viewer(self, request, slug):
        """Always the client's projection. **Overridden, never inherited.** D63.

        The inherited implementation resolves workspace-Admin membership and falls back to
        ``ReportViewer.member()``, which carries ``logged_hours`` -- and a Member projection
        handed to a client is worse than a 403, because the client sees ``equivalent_hours``
        legitimately and the two together give up the multiplier (R11(c)).

        Unconditional, so a Member or an Admin calling a portal route sees exactly what the
        client sees. That is the point: it makes the portal auditable from the inside without
        a role-conditional branch for R11 to be wrong in, the same reasoning as
        ``ServiceLogClientEndpoint``.
        """
        return ReportViewer.guest()

    def _portal_scope(self, request, slug, filterset):
        """The client's one project, and the descriptor already confined to it. D63, D67.

        Returns ``(scoped_filterset, project_ids, refusal)``:

        * a **refusal** ``Response`` when the request named no project or named more than one;
        * ``scoped_filterset is None`` when the caller named a project that is not theirs,
          which the routes answer with an empty 200 rather than a 403 -- see
          ``_empty_payload``;
        * otherwise the filterset **already narrowed**, plus the tuple of exactly one id.

        **Returning the narrowed filterset rather than just the ids is the whole reason this
        is a method.** Three things have to happen in one order and all three fail silently
        when they do not:

        1. ``narrow()`` is ``dataclasses.replace``, so it REPLACES rather than intersects --
           the scope has to be applied after anything that arrived in the query string, or a
           crafted ``?project_ids=`` widens the tenancy boundary with no error anywhere.
        2. Narrowing with an empty tuple selects EVERY project in the workspace, because
           ``ServiceLogFilterSet.queryset`` applies each lookup only ``if values:``. A caller
           whose requested project is not theirs would receive the whole workspace.
           ``scope_filterset_to_client`` raises rather than accept an empty scope, and the
           short circuit above is what keeps it from ever being called with one.
        3. The project is REQUIRED, so "every project this caller belongs to, summed" is not
           a payload any portal route can produce -- see ``client_project_scope`` and D67.
           That is what keeps section 2 of Phase 8 from depending on frontend discipline.

        Handing back ids and leaving the narrowing to the caller would make step 1 a thing
        each new route has to remember. Handing back the narrowed descriptor makes forgetting
        it impossible.
        """
        try:
            project_ids = client_project_scope(
                request.user, slug=slug, requested=filterset.project_ids
            )
        except ServiceLogValidationError as error:
            return None, (), Response(
                {"error": error.code}, status=status.HTTP_400_BAD_REQUEST
            )

        if not project_ids:
            return None, (), None

        return scope_filterset_to_client(filterset, project_ids), project_ids, None


class ServiceClientPortalReportEndpoint(ServicePortalBaseView):
    """What the client sees about their own consumption. D55, D63, D67. Criteria 2 and 22.

    **``?project_ids=`` is required and takes exactly one project.** The client's dashboard is
    the dashboard *of one Cliente*, because section 2 of Phase 8 says the contracts are
    independent and the dashboards dedicated. Marcel, a GUEST of both Marubeni and Terlogs,
    gets Marubeni's numbers inside Marubeni and Terlogs' inside Terlogs -- never one payload
    containing both. Requiring the parameter is what makes the consolidated payload
    unrepresentable rather than merely unused; see :func:`client_project_scope` and D67.

    **Criterion 22 is inherited, not decided here.** ``ReportViewer.guest()`` was built and
    tested at the domain layer by Phase 9 precisely so this route would not get to rediscover
    which columns leak (D55). This endpoint mounts that projection; it does not choose fields.

    Everything in the payload is hours. There is no monetary key anywhere, and
    ``revenue_series`` is deliberately not called even though it would not raise: it returns
    hours-only buckets for a non-money viewer rather than a 403, so calling it would produce a
    silently empty section instead of an error. The client's money lives on the work log rows
    that were billed to them (D58), one endpoint over, where each amount corresponds to an
    invoice line.

    ``contract_balance_statement`` is handed over unprojected because it is entirely hours --
    contracted, carried, consumed, granted, balance, discarded, overage, plus each carried
    parcel's origin competency. It answers the portal's first question, "how much of my
    contract have I used", and it contains nothing R11 withholds.
    """

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def get(self, request, slug):
        workspace = self._workspace(slug)

        if workspace is None:
            return _not_found()

        viewer = self._viewer(request, slug)

        try:
            filterset = self._filterset(request)
        except ServiceReportFilterError as error:
            return _bad_filter(error)

        # Resolved and narrowed in one call, in the one order that is safe -- see
        # `ServicePortalBaseView._portal_scope`, which documents the three silent failures.
        scoped, project_ids, refusal = self._portal_scope(request, slug, filterset)

        if refusal is not None:
            return refusal

        if scoped is None:
            return Response(self._empty_payload(filterset), status=status.HTTP_200_OK)

        filterset = scoped

        client_ids = list(
            Project.objects.filter(id__in=project_ids, service_client_id__isnull=False)
            .values_list("service_client_id", flat=True)
            .distinct()
        )

        contracts = list(
            ServiceContract.objects.filter(
                workspace_id=workspace.id, service_client_id__in=client_ids
            ).select_related("service_client")
            if client_ids
            else []
        )

        payload = {
            "shape": "contract" if contracts else "standalone",
            "totals": headline_totals(workspace.id, filterset, viewer),
            "distributions": {
                dimension: distribution(workspace.id, filterset, viewer, dimension=dimension)
                for dimension in ("hour_type", "billing_type")
            },
            # R5: never one number, for the client least of all -- "não faturável" collapsed
            # into a single figure is what turns a courtesy into an argument.
            "non_billable": non_billable_breakdown(workspace.id, filterset, viewer),
            "allowances": self._allowances(workspace.id, client_ids, viewer),
        }

        if contracts:
            # D48, the same asymmetry the Admin dashboard has and for the same reason: a work
            # log dated before the contract's vigency debits the first period (D31), so its
            # `worked_on` month has no period and grouping by it would file those hours in a
            # month the contract never had.
            period_basis = filterset.narrow(competence_basis=CompetenceBasis.DEBITED_PERIOD)
            payload["consumption_series"] = hours_series(workspace.id, period_basis, viewer)
            payload["contracts"] = [
                {
                    "contract_id": str(contract.pk),
                    "code": contract.code,
                    "name": contract.name,
                    "status": contract.status,
                    # Listed per contract, never merged. Section 1c's rule holds here too:
                    # a client holding two contracts must not see one balance.
                    "statement": contract_balance_statement(contract),
                }
                for contract in contracts
            ]
        else:
            payload["consumption_series"] = hours_series(workspace.id, filterset, viewer)

        return Response(payload, status=status.HTTP_200_OK)

    def _empty_payload(self, filterset):
        """What a caller outside the requested project gets: the shape, with nothing in it.

        200 and not 403, for two populations. A legitimate workspace member who has not been
        given a project yet is a state of the data and not a permission failure -- and a portal
        that answers 403 on its own dashboard sends its user to support rather than to their
        administrator. And a caller naming a project that is not theirs learns nothing from an
        empty dashboard that they could not learn from the project route itself, which already
        refuses them; answering 404 here would only add a second, differently worded refusal.

        Returns the full key set so the frontend has no special case.
        """
        return {
            "shape": "standalone",
            "totals": {"entries": 0, "issues": 0, "filters": filterset.to_params()},
            "distributions": {"hour_type": [], "billing_type": []},
            "non_billable": [],
            "allowances": {"active": [], "pending_closure": []},
            "consumption_series": [],
        }


class ServiceClientPortalIssuesEndpoint(ServicePortalBaseView, BasePaginator):
    """The **chamados** behind the client's numbers, paginated. Phase 10, item 7.

    The portal dashboard showed totals, a series, a statement, a breakdown by hour type and
    the active allowances -- and no list of the work items that produced any of it. So a
    client could see that 37,5h had been consumed and had no way to ask *which tickets*. This
    is that list.

    **A paginated table rather than a modal.** Decided with the user: the ticket list is the
    section a client actually wants, and hiding it behind a click on a chart would put the
    primary answer behind a gesture nobody discovers. The chart click still exists, but it
    *filters* the table rather than revealing it -- so the information is reachable without
    knowing the gesture, and the gesture is a refinement instead of a prerequisite.

    **Paginated, and it is the only portal section that is.** Every other section is bounded
    by something -- competencies in the window, hour types in the catalogue, open allowances.
    A ticket list is bounded by nothing, so this is the one place the portal has to page.

    Everything ``ServiceClientPortalReportEndpoint`` guarantees is inherited rather than
    restated: the guest projection and the one-Cliente tenancy both come from
    ``ServicePortalBaseView``. What this route adds is per-**issue** visibility, which the
    dashboard never needed because a total does not name the tickets it summed -- see
    ``_visible_logs``.

    No money, for anybody, including an Admin auditing the route. See :func:`issue_row`: a
    per-issue amount is not a figure D58 licenses, because a ticket half absorbed by an
    allowance and half billed has no single amount corresponding to an invoice line.
    """

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def get(self, request, slug):
        workspace = self._workspace(slug)

        if workspace is None:
            return _not_found()

        viewer = self._viewer(request, slug)

        try:
            filterset = self._filterset(request)
        except ServiceReportFilterError as error:
            return _bad_filter(error)

        scoped, project_ids, refusal = self._portal_scope(request, slug, filterset)

        if refusal is not None:
            return refusal

        if scoped is None:
            return self._empty_page(request, filterset, viewer)

        rows = issue_rows(
            workspace.id,
            scoped,
            viewer,
            queryset=self._visible_logs(request, slug, project_ids[0]),
        )

        return self.paginate(
            request=request,
            queryset=rows,
            on_results=lambda results: [issue_row(row, scoped, viewer) for row in results],
            # The same descriptor's totals, so the table's own header can state "N chamados,
            # X h" without a second request -- and so the count is checkable against the
            # dashboard above it. `totals["issues"]` is `Count("issue_id", distinct=True)` over
            # this selection, which is by construction the row count of this table.
            extra_stats={"totals": headline_totals(workspace.id, scoped, viewer)},
        )

    def _visible_logs(self, request, slug, project_id):
        """The work logs this caller may see, before the descriptor narrows them further.

        **Why the dashboard did not need this and the table does.** A total does not name the
        work items it summed, so a client seeing "37,5h" learns nothing about a ticket they
        were not shown. A *list* names them. So the per-issue gate that
        ``ServiceLogClientEndpoint`` applies when reached by issue id has to be applied here
        too, in its queryset form.

        The gate is the project flag first and the narrowing second, which is the shape core
        Plane's own guest-scope sites have and the shape ``client_may_reach_issue`` documents:
        a project with ``guest_view_all_features`` shows its clients everything in it, and
        that flag is *required* for a Cliente's project by the master context (section 2b) --
        without it Marcel would not see the tickets his colleagues opened. So in the
        configuration this product prescribes, this narrowing is inert. It exists for the
        project that was configured wrongly, where the alternative is a client reading the
        titles of tickets belonging to other people at their own company.

        Applied only to a caller who is actually a client's user on this project. A Member or
        an Admin calling this route gets the client's *projection* by design (auditability),
        but not the client's *visibility*: narrowing an Admin to the tickets they personally
        created would make the audit view lie about what the client sees.
        """
        logs = ServiceLog.objects.filter(workspace__slug=slug, project_id=project_id)

        if not is_client_portal_member(request.user, slug=slug, project_id=project_id):
            return logs

        if Project.objects.filter(id=project_id, guest_view_all_features=True).exists():
            return logs

        return logs.filter(client_visible_issues_q(request.user, prefix="issue__"))

    def _empty_page(self, request, filterset, viewer):
        """What a caller outside the requested project gets: a real page, with no rows.

        200 and not 403, for the two populations ``_empty_payload`` names, and built by
        paginating an empty queryset rather than by hand. That matters: the envelope
        ``BasePaginator`` produces has ten keys around ``results``, and a literal dict written
        here would be a second definition of the page shape -- correct until the paginator's
        changed and this had not. An empty queryset gives the identical envelope by
        construction.

        ``totals`` mirrors ``_empty_payload``'s: the counts and the descriptor, and no hour
        keys, because there is no selection to have summed. Computing ``headline_totals`` on
        the **unnarrowed** filterset here would be the leak this whole sequence exists to
        prevent -- an empty scope means the descriptor still selects every project in the
        workspace.
        """
        return self.paginate(
            request=request,
            queryset=ServiceLog.objects.none().values("issue_id"),
            on_results=lambda results: [],
            extra_stats={
                "totals": {"entries": 0, "issues": 0, "filters": filterset.to_params()}
            },
        )
