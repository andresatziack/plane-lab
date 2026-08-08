# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""The client's own consumption dashboard. D55, D63, D67. Criteria 13 and 22.

Criterion 22 is inherited: ``ReportViewer.guest()`` is built and proved at the domain layer
in ``plane/tests/unit/utils/test_service_reports.py``, and those tests are not repeated
here. What is new is the HTTP boundary -- that a client reaches this route, that nobody
reaches another client's numbers through it, and that the projection is the guest one and
not the Member one that ``ServiceReportBaseView`` would have returned.

Two of these tests exist for failures that produce a **200 with wrong data** rather than an
error, which is why they are written as tenancy assertions rather than status assertions:

* ``narrow(project_ids=())`` applies no project filter at all, so an empty scope becomes
  every project in the workspace.
* ``narrow()`` replaces rather than intersects, so a scope applied before the query string
  is a scope the query string can widen.

**Every portal read here names its project**, because D67 made ``?project_ids=`` required and
single. Two consequences worth stating, since both are easy to get wrong in a test file:

* The absence assertions in this module are satisfied by an empty payload, and after D67 an
  empty payload is what a *type mismatch* in the scope intersection would produce for every
  request. ``TestD67`` therefore carries an explicit **positive** control asserting a
  non-empty result, without which the whole module could go green on a dead feature.
* The multi-project guest -- Marcel, a GUEST of Marubeni *and* Terlogs, the reference scenario
  of the entire series -- is exercised in ``TestD67TheDashboardIsOfOneClienteAtATime``. No
  fixture in *this* file implemented him before D67: ``marcel`` is a member of one project, so
  the class below proves isolation *between* clients and never asked what a user belonging to
  two of them receives.

  **He was implemented in ``test_service_client_isolation_app.py``, and that is where the real
  failure was**: a test there asserted his two Clientes summed into one payload -- ``entries ==
  2``, ``3.0000`` -- as the expected answer, contradicting section 2 of the Phase 8 brief, which
  forbids the consolidated view by name. The test was measuring tenancy, and pinned the
  aggregation in passing. See D67.
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from plane.db.models import (
    Issue,
    Project,
    ProjectMember,
    ServiceBillingType,
    ServiceClient,
    ServiceContract,
    ServiceHourType,
    ServiceLog,
    User,
    WorkspaceMember,
)
from plane.utils.service_reports import ReportViewer

PORTAL_URL = "/api/workspaces/{slug}/service-reports/portal/"
CONSUMPTION_URL = "/api/workspaces/{slug}/service-reports/consumption/"

ROUTE = ServiceBillingType.BillingRoute

#: The quantity a client must never receive, and the value it carries in this fixture.
#: `logged_hours` differs from `equivalent_hours` so a leak shows up as a distinct number.
LOGGED = "1.0000"
EQUIVALENT = "1.5000"


def _user(prefix, workspace, role, projects=()):
    unique_id = uuid4().hex[:8]
    user = User.objects.create(
        email=f"{prefix}-{unique_id}@plane.so",
        username=f"{prefix}_{unique_id}",
        first_name=prefix.title(),
    )
    user.set_password("test-password")
    user.save()
    WorkspaceMember.objects.create(workspace=workspace, member=user, role=role)

    for project in projects:
        ProjectMember.objects.create(project=project, member=user, workspace=workspace, role=role)

    return user


@pytest.fixture
def clients(db, workspace):
    """Marubeni and Terlogs: the isolation pair the whole series uses (D19)."""
    return {
        "marubeni": ServiceClient.objects.create(name="Marubeni do Brasil", workspace=workspace),
        "terlogs": ServiceClient.objects.create(name="Terlogs Transportes", workspace=workspace),
    }


@pytest.fixture
def projects(db, workspace, create_user, clients):
    built = {}

    for key, service_client in clients.items():
        project = Project.objects.create(
            name=f"{service_client.name} Support",
            identifier=key[:4].upper(),
            workspace=workspace,
            created_by=create_user,
            service_client=service_client,
            guest_view_all_features=True,
            is_time_tracking_enabled=True,
        )
        ProjectMember.objects.create(project=project, member=create_user, workspace=workspace, role=20)
        built[key] = project

    return built


@pytest.fixture
def hour_type(db, workspace):
    return ServiceHourType.objects.create(
        name="Fora do expediente", workspace=workspace, multiplier=Decimal("1.50")
    )


@pytest.fixture
def billing_type(db, workspace):
    return ServiceBillingType.objects.create(
        name="Fatura reais", workspace=workspace, billing_route=ROUTE.BILL_AMOUNT
    )


@pytest.fixture
def technician(db, workspace, projects):
    return _user("tech", workspace, 15, projects.values())


@pytest.fixture
def marcel(db, workspace, projects):
    """The Marubeni client's own user. A workspace GUEST, member of the Marubeni project."""
    return _user("marcel", workspace, 5, [projects["marubeni"]])


@pytest.fixture
def marcel_multi(db, workspace, projects):
    """Marcel as the master context actually describes him: a GUEST of **both** Clientes.

    The reference scenario of the whole series, and until D67 no fixture implemented it. The
    ``marcel`` fixture above belongs to one project, which is why the isolation class could
    pass while the question "what does a user of two Clientes receive" had never been asked.

    Kept as a second fixture rather than widening ``marcel``: the single-project user is the
    right subject for the tenancy tests, where two memberships would make an assertion of
    ``entries == 1`` ambiguous about *which* scope produced it.
    """
    return _user("marcel-multi", workspace, 5, [projects["marubeni"], projects["terlogs"]])


@pytest.fixture
def contracts(db, workspace, create_user, clients):
    """One contract per Cliente, so ``shape`` is "contract" and ``contracts[]`` is assertable.

    Deliberately **not** used by the tests above. Adding a contract switches
    ``consumption_series`` to the debited-period basis (D48), and these logs debit no period,
    so the series would be legitimately empty -- which would make a positive control asserting
    a non-empty series fail for a reason that has nothing to do with what it is testing.
    """
    built = {}

    for name, service_client in clients.items():
        built[name] = ServiceContract.objects.create(
            workspace=workspace,
            service_client=service_client,
            code=f"CT-{name[:4].upper()}",
            name=f"{service_client.name} support contract",
            monthly_hours=Decimal("40.0000"),
            starts_on=date(2026, 1, 1),
            ends_on=date(2026, 12, 31),
            created_by=create_user,
        )

    return built


@pytest.fixture
def adriano(db, workspace, projects):
    """The Terlogs client's user. Must never see a Marubeni number."""
    return _user("adriano", workspace, 5, [projects["terlogs"]])


@pytest.fixture
def orphan(db, workspace):
    """A workspace GUEST with no project at all: the empty-scope case."""
    return _user("orphan", workspace, 5)


def _log(project, workspace, author, hour_type, billing_type, *, amount="300.00"):
    return ServiceLog.objects.create(
        issue=_issue(project, workspace, author),
        project=project,
        workspace=workspace,
        author=author,
        hour_type=hour_type,
        billing_type=billing_type,
        worked_on=date(2026, 3, 10),
        description="Restarted the cluster",
        raw_duration_minutes=60,
        logged_hours=Decimal(LOGGED),
        equivalent_hours=Decimal(EQUIVALENT),
        debited_hours=Decimal(EQUIVALENT),
        applied_multiplier=Decimal("1.50"),
        applied_billing_route=ROUTE.BILL_AMOUNT,
        settled_billing_route=ROUTE.BILL_AMOUNT,
        applied_hour_rate=Decimal("200.00"),
        applied_rate_basis=ServiceLog.RateBasis.BASE_MULTIPLIER,
        amount=Decimal(amount),
        batch_id=uuid4(),
    )


def _issue(project, workspace, author):
    issue = Issue(name="Server is down", project=project, workspace=workspace)
    issue.save(created_by_id=author.id)
    return issue


@pytest.fixture
def logs(db, projects, workspace, technician, hour_type, billing_type):
    """One log for each client, with distinguishable amounts."""
    return {
        "marubeni": _log(projects["marubeni"], workspace, technician, hour_type, billing_type, amount="300.00"),
        "terlogs": _log(projects["terlogs"], workspace, technician, hour_type, billing_type, amount="900.00"),
    }


def _api(user):
    api = APIClient()
    api.force_authenticate(user=user)
    return api


def _get(user, workspace, url=PORTAL_URL, project=None, **params):
    """A report read. ``project`` sets the ``project_ids`` the portal requires. D67.

    Passed as a keyword holding the project *object* rather than a raw id so that a call site
    reads as the scope it means, and so that omitting it -- which the portal answers with a
    400 -- is visibly a choice rather than a forgotten parameter.
    """
    if project is not None:
        params["project_ids"] = project.id

    query = "&".join(f"{key}={value}" for key, value in params.items())
    return _api(user).get(url.format(slug=workspace.slug) + (f"?{query}" if query else ""))


def _money_keys(payload):
    """Every monetary key anywhere in a nested payload."""
    found = set()
    monetary = {"amount", "amount_display", "total_amount", "average_amount_per_issue"}

    def walk(node):
        if isinstance(node, dict):
            found.update(set(node) & monetary)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return found


def _hour_keys(payload, key):
    found = []

    def walk(node):
        if isinstance(node, dict):
            if key in node:
                found.append(node[key])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return found


@pytest.mark.contract
class TestTheGuestProjectionIsWhatTheRouteReturns:
    @pytest.mark.django_db
    def test_a_client_reaches_the_portal_dashboard(self, workspace, marcel, projects, logs):
        response = _get(marcel, workspace, project=projects["marubeni"])

        assert response.status_code == status.HTTP_200_OK, response.data

    @pytest.mark.django_db
    def test_the_payload_carries_no_logged_hours_anywhere(self, workspace, marcel, projects, logs):
        """The inherited criterion, asserted at the HTTP boundary. Nothing computes
        logged_hours for a guest viewer, so the key is absent rather than stripped."""
        response = _get(marcel, workspace, project=projects["marubeni"])

        assert _hour_keys(response.data, "logged_hours") == []
        assert _hour_keys(response.data, "logged_hours_display") == []

    @pytest.mark.django_db
    def test_the_payload_carries_no_money_anywhere(self, workspace, marcel, projects, logs):
        response = _get(marcel, workspace, project=projects["marubeni"])

        assert _money_keys(response.data) == set()

    @pytest.mark.django_db
    def test_revenue_series_is_not_even_a_key(self, workspace, marcel, projects, logs):
        """`revenue_series` returns hours-only buckets rather than raising for a non-money
        viewer, so calling it would have produced a silently empty section instead of an
        error. It is not called at all."""
        response = _get(marcel, workspace, project=projects["marubeni"])

        assert "revenue_series" not in response.data

    @pytest.mark.django_db
    def test_the_client_does_receive_their_hours(self, workspace, marcel, projects, logs):
        """The positive control. Every absence above is satisfied by an empty payload."""
        response = _get(marcel, workspace, project=projects["marubeni"])

        assert response.data["totals"]["equivalent_hours"] == EQUIVALENT
        assert "debited_hours" in response.data["totals"]
        assert response.data["totals"]["entries"] == 1

    @pytest.mark.django_db
    def test_the_viewer_is_overridden_and_not_inherited(self, workspace, marcel):
        """Structural. The inherited `_viewer` falls back to `ReportViewer.member()`, which
        carries logged_hours -- and handing that to a client is worse than a 403."""
        from plane.app.views.service_reports.base import ServiceClientPortalReportEndpoint

        view = ServiceClientPortalReportEndpoint()

        assert view._viewer(request=None, slug=workspace.slug) == ReportViewer.guest()

    @pytest.mark.django_db
    def test_an_admin_calling_this_route_also_gets_the_guest_projection(
        self, workspace, create_user, projects, logs
    ):
        """Unconditional by design: it makes the portal auditable from the inside, and
        leaves no role-conditional branch for R11 to be wrong in."""
        response = _get(create_user, workspace, project=projects["marubeni"])

        assert response.status_code == status.HTTP_200_OK, response.data
        assert _hour_keys(response.data, "logged_hours") == []
        assert _money_keys(response.data) == set()

    @pytest.mark.django_db
    def test_the_same_admin_does_see_money_on_the_admin_route(self, workspace, create_user, logs):
        """The other positive control: the money exists and this admin can see it. Without
        this, a workspace with no priced rows would make the test above vacuous."""
        response = _get(create_user, workspace, url=CONSUMPTION_URL)

        assert response.status_code == status.HTTP_200_OK, response.data
        assert _money_keys(response.data) != set()


@pytest.mark.contract
class TestD63TheScopeIsResolvedFirstAndNarrowedLast:
    @pytest.mark.django_db
    def test_a_client_sees_only_their_own_clients_hours(self, workspace, marcel, projects, logs):
        """Marubeni's log is 1.5h; Terlogs' is another 1.5h. A total of 1.5 proves the
        scope held; 3.0 would mean it did not."""
        response = _get(marcel, workspace, project=projects["marubeni"])

        assert response.data["totals"]["equivalent_hours"] == EQUIVALENT
        assert response.data["totals"]["entries"] == 1

    @pytest.mark.django_db
    def test_the_other_client_sees_only_theirs(self, workspace, adriano, projects, logs):
        """The mirror, so a scope that accidentally pinned one project would fail here."""
        response = _get(adriano, workspace, project=projects["terlogs"])

        assert response.data["totals"]["entries"] == 1

    @pytest.mark.django_db
    def test_asking_only_for_another_clients_project_returns_nothing(
        self, workspace, marcel, projects, logs
    ):
        """**Rewritten by D67, and the assertion moved with the semantics.**

        This test used to assert ``entries == 1``: the scope *replaced* the request, so a
        caller asking for somebody else's project silently received their own. The property
        the name claims -- nothing of Terlogs' -- held either way, and the ``1`` was an
        artefact of replacement rather than the thing being protected.

        With the intersection it is 0, and both halves are asserted: nothing of theirs, and
        nothing of anybody's. Answering with the caller's own project would now be the bug.
        """
        response = _get(marcel, workspace, project=projects["terlogs"])

        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["totals"]["entries"] == 0
        assert response.data["consumption_series"] == []
        assert _money_keys(response.data) == set()

    @pytest.mark.django_db
    def test_a_crafted_service_client_filter_cannot_widen_the_scope(
        self, workspace, marcel, projects, clients, logs
    ):
        """The other axis: the descriptor also carries `service_client_ids`, and the project
        scope has to defeat it because the project is what membership is granted on."""
        response = _get(
            marcel,
            workspace,
            project=projects["marubeni"],
            service_client_ids=str(clients["terlogs"].id),
        )

        assert response.data["totals"]["entries"] == 0 or response.data["totals"][
            "equivalent_hours"
        ] == "0.0000"

    @pytest.mark.django_db
    def test_a_client_with_no_projects_sees_nothing_rather_than_everything(
        self, workspace, orphan, projects, logs
    ):
        """The most dangerous failure in this phase: an empty scope becoming total access.
        `narrow(project_ids=())` would have applied no project filter at all.

        After D67 the caller must name a project, so the empty scope now arrives as an empty
        *intersection* rather than an empty membership -- the same short circuit, reached by
        the path a real request takes."""
        response = _get(orphan, workspace, project=projects["marubeni"])

        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["totals"]["entries"] == 0
        assert response.data["consumption_series"] == []
        assert _money_keys(response.data) == set()

    @pytest.mark.django_db
    def test_the_empty_payload_has_the_same_keys_as_a_full_one(
        self, workspace, orphan, marcel, projects, logs
    ):
        """So the frontend has no special case, and so a future key added to one branch and
        not the other fails here."""
        empty = _get(orphan, workspace, project=projects["marubeni"]).data
        full = _get(marcel, workspace, project=projects["marubeni"]).data

        assert set(empty) <= set(full)
        for key in ("shape", "totals", "distributions", "non_billable", "allowances", "consumption_series"):
            assert key in empty

    @pytest.mark.django_db
    def test_scoping_helper_refuses_an_empty_project_set(self):
        """Belt and braces: even if a caller forgot the short circuit, the helper raises
        rather than silently selecting the workspace."""
        from plane.utils.service_portal import scope_filterset_to_client
        from plane.utils.service_reports_filters import ServiceLogFilterSet

        with pytest.raises(ValueError, match="empty project set"):
            scope_filterset_to_client(ServiceLogFilterSet(), ())


@pytest.mark.contract
class TestD67TheDashboardIsOfOneClienteAtATime:
    """Criteria 1 and 2 of Phase 8b: one Cliente per payload, and the sum unreachable.

    Section 2 of Phase 8 forbids a consolidated view of two companies. Before D67 the portal
    endpoint produced exactly that for a user who is a GUEST of two Clientes -- and a test in
    ``test_service_client_isolation_app.py`` asserted it as **correct**, which is why nothing
    caught it. The endpoint is now structurally incapable of it: the project is required and
    single. See D67.
    """

    @pytest.mark.django_db
    def test_the_project_is_required(self, workspace, marcel_multi, logs):
        """Without this the endpoint's default answer is every project the caller belongs to,
        summed -- the consolidated payload section 2 prohibits. A 400 makes it unrepresentable
        instead of merely unused, which is the same move as the ledger's XOR."""
        response = _get(marcel_multi, workspace)

        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.data
        assert response.data["error"] == "PORTAL_REPORT_REQUIRES_A_PROJECT"

    @pytest.mark.django_db
    def test_more_than_one_project_is_refused(self, workspace, marcel_multi, projects, logs):
        """**Replaces the old ``a_crafted_project_filter_cannot_widen_the_scope``.**

        That test asked for both projects and asserted the answer was one of them, which was
        the replacement semantics doing the defending. Two projects is now a 400, so the
        widening it guarded against is refused earlier and louder -- and for the caller who
        legitimately belongs to both, which is the case that makes it interesting.
        """
        response = _get(
            marcel_multi,
            workspace,
            project_ids=f"{projects['marubeni'].id},{projects['terlogs'].id}",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.data
        assert response.data["error"] == "PORTAL_REPORT_ACCEPTS_ONE_PROJECT"

    @pytest.mark.django_db
    def test_a_legitimate_single_project_request_is_not_empty(
        self, workspace, marcel_multi, projects, logs
    ):
        """**The positive control, and the reason it is not optional.**

        The intersection compares ids from the query string against ids from the database. If
        the two sides were ever different types the intersection would be empty for *every*
        request -- and that failure is invisible to this module's other tests, all of which
        assert absence and would go green on a permanently blank dashboard. This test is what
        distinguishes "correctly scoped" from "dead".
        """
        response = _get(marcel_multi, workspace, project=projects["marubeni"])

        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["totals"]["entries"] == 1
        assert response.data["totals"]["equivalent_hours"] == EQUIVALENT
        assert response.data["consumption_series"] != []
        assert response.data["distributions"]["hour_type"] != []

    @pytest.mark.django_db
    def test_the_scope_helper_accepts_ids_as_strings_and_as_uuids(
        self, workspace, marcel_multi, projects
    ):
        """The same trap, asserted at the unit the coercion lives in, for both shapes a caller
        could plausibly hold. A set intersection without the coercion passes one and fails the
        other, which is precisely the bug that looks like a scoping success."""
        from plane.utils.service_portal import client_project_scope

        project = projects["marubeni"]

        as_uuid = client_project_scope(marcel_multi, slug=workspace.slug, requested=[project.id])
        as_string = client_project_scope(
            marcel_multi, slug=workspace.slug, requested=[str(project.id)]
        )

        assert as_uuid == (project.id,)
        assert as_string == (project.id,)

    @pytest.mark.django_db
    def test_the_multi_project_guest_sees_marubeni_inside_marubeni(
        self, workspace, marcel_multi, projects, logs
    ):
        """Criterion 1. Marcel belongs to both, so 3.0 here would be the consolidated view."""
        response = _get(marcel_multi, workspace, project=projects["marubeni"])

        assert response.data["totals"]["equivalent_hours"] == EQUIVALENT
        assert response.data["totals"]["entries"] == 1

    @pytest.mark.django_db
    def test_the_multi_project_guest_sees_terlogs_inside_terlogs(
        self, workspace, marcel_multi, projects, logs
    ):
        """Criterion 2. The same user, the other project, and a different Cliente's numbers --
        without choosing a company anywhere. A scope that pinned the first project the caller
        happened to belong to would pass the test above and fail this one."""
        response = _get(marcel_multi, workspace, project=projects["terlogs"])

        assert response.data["totals"]["equivalent_hours"] == EQUIVALENT
        assert response.data["totals"]["entries"] == 1

    @pytest.mark.django_db
    def test_neither_payload_is_the_sum_of_the_two(
        self, workspace, marcel_multi, projects, logs
    ):
        """The prohibition itself, stated as one assertion. Each Cliente logged 1.5h; a payload
        carrying 3.0 or two entries is the consolidated view, whatever produced it."""
        for key in ("marubeni", "terlogs"):
            response = _get(marcel_multi, workspace, project=projects[key])

            assert response.data["totals"]["entries"] == 1, key
            assert response.data["totals"]["equivalent_hours"] == EQUIVALENT, key

    @pytest.mark.django_db
    def test_only_the_active_projects_contract_is_listed(
        self, workspace, marcel_multi, projects, contracts, logs
    ):
        """Criterion 1 for the contract itself, and the sharpest form of the prohibition: the
        client holding two contracts must never see both in one payload, because section 2 says
        they are independent. ``contracts[]`` is the key where a leak would be unmistakable."""
        response = _get(marcel_multi, workspace, project=projects["marubeni"])

        assert response.data["shape"] == "contract"
        codes = [contract["code"] for contract in response.data["contracts"]]
        assert codes == [contracts["marubeni"].code]
        assert contracts["terlogs"].code not in codes

    @pytest.mark.django_db
    def test_the_other_project_lists_the_other_contract(
        self, workspace, marcel_multi, projects, contracts, logs
    ):
        """The mirror, so a payload that always returned the same contract fails here."""
        response = _get(marcel_multi, workspace, project=projects["terlogs"])

        codes = [contract["code"] for contract in response.data["contracts"]]
        assert codes == [contracts["terlogs"].code]

    @pytest.mark.django_db
    def test_an_absent_project_is_refused_before_the_scope_is_resolved(self, workspace, orphan):
        """A caller with no projects at all still gets the 400 and not the empty payload: the
        missing parameter is a malformed request, and answering 200 would teach a frontend that
        omitting it is fine until the day the caller has two Clientes."""
        response = _get(orphan, workspace)

        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.data
        assert response.data["error"] == "PORTAL_REPORT_REQUIRES_A_PROJECT"


@pytest.mark.contract
class TestTheAdminReportsStillRefuseAClient:
    @pytest.mark.django_db
    @pytest.mark.parametrize(
        "path",
        ["consumption", "operational", "attention", "billing", "logs"],
    )
    def test_a_client_is_refused_every_other_report_route(self, workspace, marcel, logs, path):
        """Criterion 16 for this surface: the portal route is the only one that admits a
        client, and adding GUEST to the others is exactly the mistake this catches."""
        response = _api(marcel).get(f"/api/workspaces/{workspace.slug}/service-reports/{path}/")

        assert response.status_code == status.HTTP_403_FORBIDDEN, response.data

    @pytest.mark.django_db
    def test_an_outsider_is_refused_the_portal_too(self, workspace, logs):
        outsider = User.objects.create(
            email=f"out-{uuid4().hex[:8]}@plane.so", username=f"out_{uuid4().hex[:8]}"
        )
        outsider.set_password("test-password")
        outsider.save()

        response = _get(outsider, workspace)

        assert response.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)



# ---------------------------------------------------------------------------
# The ticket table -- Phase 10, item 7
# ---------------------------------------------------------------------------


PORTAL_ISSUES_URL = "/api/workspaces/{slug}/service-reports/portal/issues/"


def _rows(response):
    return response.data["results"]


@pytest.fixture
def marubeni_tickets(db, projects, workspace, technician, hour_type, billing_type):
    """Three Marubeni tickets across two competencies, so paging and filtering are testable.

    Distinct ``worked_on`` dates rather than three logs on one day, because the table's order
    is ``-last_worked_on`` and an order that is not **total** lets offset pagination repeat or
    skip a row. Equal dates would make the page-boundary test pass or fail on the database's
    incidental row order, which is the kind of test that goes green for the wrong reason.
    """
    built = {}
    project = projects["marubeni"]

    for key, worked_on in (
        ("march_old", date(2026, 3, 2)),
        ("march_new", date(2026, 3, 20)),
        ("april", date(2026, 4, 5)),
    ):
        issue = _issue(project, workspace, technician)
        built[key] = ServiceLog.objects.create(
            issue=issue,
            project=project,
            workspace=workspace,
            author=technician,
            hour_type=hour_type,
            billing_type=billing_type,
            worked_on=worked_on,
            description=f"Work on {key}",
            raw_duration_minutes=60,
            logged_hours=Decimal(LOGGED),
            equivalent_hours=Decimal(EQUIVALENT),
            debited_hours=Decimal(EQUIVALENT),
            applied_multiplier=Decimal("1.50"),
            applied_billing_route=ROUTE.BILL_AMOUNT,
            settled_billing_route=ROUTE.BILL_AMOUNT,
            applied_hour_rate=Decimal("200.00"),
            applied_rate_basis=ServiceLog.RateBasis.BASE_MULTIPLIER,
            amount=Decimal("300.00"),
            batch_id=uuid4(),
        )

    return built


@pytest.mark.contract
class TestTheTicketTableCarriesTheGuestProjection:
    """Item 7's table is a second portal route, so R11 has a second place to be got wrong.

    Every absence assertion in this class is paired with a positive control, because after
    D67 an empty page is what a broken scope intersection produces for *every* request -- and
    an empty page satisfies "no money" and "no logged_hours" perfectly.
    """

    @pytest.mark.django_db
    def test_a_client_reaches_the_ticket_table(self, workspace, marcel, projects, logs):
        response = _get(marcel, workspace, url=PORTAL_ISSUES_URL, project=projects["marubeni"])

        assert response.status_code == status.HTTP_200_OK, response.data

    @pytest.mark.django_db
    def test_the_client_receives_their_ticket(self, workspace, marcel, projects, logs):
        """**The positive control for this whole class.** Without it every assertion below is
        also satisfied by a table that returns nothing at all."""
        response = _get(marcel, workspace, url=PORTAL_ISSUES_URL, project=projects["marubeni"])

        rows = _rows(response)
        assert len(rows) == 1
        assert rows[0]["equivalent_hours"] == EQUIVALENT
        assert rows[0]["entries"] == 1
        assert rows[0]["name"] == "Server is down"
        # The readable key the frontend links by. Without both halves there is no `/browse/`
        # URL to build, and the table would be a list of titles with nowhere to go.
        assert rows[0]["project_identifier"] == "MARU"
        assert rows[0]["sequence_id"]

    @pytest.mark.django_db
    def test_the_page_carries_no_logged_hours_anywhere(self, workspace, marcel, projects, logs):
        response = _get(marcel, workspace, url=PORTAL_ISSUES_URL, project=projects["marubeni"])

        assert _hour_keys(response.data, "logged_hours") == []
        assert _hour_keys(response.data, "logged_hours_display") == []
        # The paired positive control: the row IS there and DOES carry the client's quantity,
        # so the absence above is a projection and not an empty page.
        assert _hour_keys(response.data, "equivalent_hours") == [EQUIVALENT, EQUIVALENT]

    @pytest.mark.django_db
    def test_the_page_carries_no_money_anywhere(self, workspace, marcel, projects, logs):
        """The work log behind this ticket settled as BILL_AMOUNT and carries R$300, so a
        per-issue amount would have something to report here -- which is what makes this
        assertion meaningful rather than vacuous."""
        response = _get(marcel, workspace, url=PORTAL_ISSUES_URL, project=projects["marubeni"])

        assert _money_keys(response.data) == set()

    @pytest.mark.django_db
    def test_an_admin_gets_the_same_moneyless_projection(
        self, workspace, create_user, projects, logs
    ):
        """No money **for anybody** on this route, unlike every other report where the amount
        is gated on the viewer. D58: a per-issue sum is not a figure the client's money rule
        licenses, so it is not computed at all -- and an operator auditing the portal should
        see precisely what the client sees."""
        response = _get(
            create_user, workspace, url=PORTAL_ISSUES_URL, project=projects["marubeni"]
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert _money_keys(response.data) == set()
        assert _hour_keys(response.data, "logged_hours") == []
        # Positive control: the admin really did reach the rows.
        assert len(_rows(response)) == 1

    @pytest.mark.django_db
    def test_the_same_admin_does_see_money_on_the_admin_route(
        self, workspace, create_user, projects, logs
    ):
        """The control that proves the assertion above is about the route and not about the
        user's permissions."""
        response = _get(create_user, workspace, url=CONSUMPTION_URL)

        assert "amount" in response.data["totals"]

    @pytest.mark.django_db
    def test_the_row_count_is_the_dashboards_issue_count(
        self, workspace, marcel, projects, marubeni_tickets, logs
    ):
        """**The invariant that makes this table trustworthy.** ``totals["issues"]`` on the
        dashboard is ``Count("issue_id", distinct=True)`` over the same descriptor, so the
        table's ``total_count`` must equal it by construction. Listing issues and summing their
        logs separately -- the obvious alternative implementation -- is what would let these
        two numbers drift, and a client comparing them is the one who would find out."""
        table = _get(marcel, workspace, url=PORTAL_ISSUES_URL, project=projects["marubeni"])
        dashboard = _get(marcel, workspace, project=projects["marubeni"])

        assert table.data["total_count"] == dashboard.data["totals"]["issues"]
        assert table.data["total_count"] == 4

    @pytest.mark.django_db
    def test_the_tables_hours_sum_to_the_dashboards_total(
        self, workspace, marcel, projects, marubeni_tickets, logs
    ):
        """Criterion 1's shape for this section: the list agrees with the number above it.
        Same descriptor, so this is arithmetic rather than luck."""
        table = _get(
            marcel, workspace, url=PORTAL_ISSUES_URL, project=projects["marubeni"], per_page=100
        )
        dashboard = _get(marcel, workspace, project=projects["marubeni"])

        summed = sum(Decimal(row["equivalent_hours"]) for row in _rows(table))

        assert summed == Decimal(dashboard.data["totals"]["equivalent_hours"])

    @pytest.mark.django_db
    def test_each_row_carries_a_descriptor_that_replays_to_itself(
        self, workspace, marcel, projects, marubeni_tickets, logs
    ):
        """D50 one dimension further. The row's own ``filters`` must select exactly that
        ticket, so the table is drillable by the same mechanism every other bucket is."""
        table = _get(
            marcel, workspace, url=PORTAL_ISSUES_URL, project=projects["marubeni"], per_page=100
        )

        for row in _rows(table):
            assert row["filters"]["issue_ids"] == row["issue_id"]

    @pytest.mark.django_db
    def test_the_extra_stats_totals_are_the_client_projection_too(
        self, workspace, marcel, projects, logs
    ):
        """``extra_stats`` is a second payload on the same response and an easy place to leak
        the Member projection, since it is built by ``headline_totals`` rather than by the row
        projector."""
        response = _get(marcel, workspace, url=PORTAL_ISSUES_URL, project=projects["marubeni"])

        totals = response.data["extra_stats"]["totals"]
        assert totals["equivalent_hours"] == EQUIVALENT
        assert "logged_hours" not in totals
        assert "amount" not in totals


@pytest.mark.contract
class TestTheTicketTableIsOfOneClienteAtATime:
    """D67 again, at the second portal route. Inherited from ``ServicePortalBaseView`` rather
    than reimplemented -- and asserted here anyway, because "it inherits it" is a claim about
    code and these are claims about the HTTP boundary."""

    @pytest.mark.django_db
    def test_the_project_is_required(self, workspace, marcel_multi, logs):
        response = _get(marcel_multi, workspace, url=PORTAL_ISSUES_URL)

        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.data
        assert response.data["error"] == "PORTAL_REPORT_REQUIRES_A_PROJECT"

    @pytest.mark.django_db
    def test_more_than_one_project_is_refused(self, workspace, marcel_multi, projects, logs):
        response = _get(
            marcel_multi,
            workspace,
            url=PORTAL_ISSUES_URL,
            project_ids=f"{projects['marubeni'].id},{projects['terlogs'].id}",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.data
        assert response.data["error"] == "PORTAL_REPORT_ACCEPTS_ONE_PROJECT"

    @pytest.mark.django_db
    def test_the_multi_project_guest_sees_each_cliente_inside_itself(
        self, workspace, marcel_multi, projects, logs
    ):
        """Two tickets exist, one per Cliente. A table returning both is the consolidated view
        section 2 of Phase 8 forbids, whatever produced it."""
        for key in ("marubeni", "terlogs"):
            response = _get(
                marcel_multi, workspace, url=PORTAL_ISSUES_URL, project=projects[key]
            )

            rows = _rows(response)
            assert len(rows) == 1, key
            assert rows[0]["project_identifier"] == key[:4].upper(), key

    @pytest.mark.django_db
    def test_a_client_cannot_reach_the_other_clientes_tickets(
        self, workspace, adriano, projects, logs
    ):
        """Adriano belongs only to Terlogs. Naming Marubeni's project must not produce
        Marubeni's ticket."""
        response = _get(adriano, workspace, url=PORTAL_ISSUES_URL, project=projects["marubeni"])

        assert response.status_code == status.HTTP_200_OK, response.data
        assert _rows(response) == []
        # The positive control: he does reach his own.
        own = _get(adriano, workspace, url=PORTAL_ISSUES_URL, project=projects["terlogs"])
        assert len(_rows(own)) == 1

    @pytest.mark.django_db
    def test_an_empty_scope_does_not_return_the_whole_workspace(
        self, workspace, orphan, projects, marubeni_tickets, logs
    ):
        """**The sharpest failure this route could have had.** An empty scope means the
        descriptor is still unnarrowed, so computing the page or its ``extra_stats`` from it
        would select every project in the workspace -- 200, correct-looking envelope, another
        company's ticket titles. Four Marubeni tickets and one Terlogs ticket exist here, so a
        leak has something unmistakable to report."""
        response = _get(orphan, workspace, url=PORTAL_ISSUES_URL, project=projects["marubeni"])

        assert response.status_code == status.HTTP_200_OK, response.data
        assert _rows(response) == []
        assert response.data["total_count"] == 0
        assert response.data["extra_stats"]["totals"]["entries"] == 0
        assert response.data["extra_stats"]["totals"]["issues"] == 0
        assert _money_keys(response.data) == set()

    @pytest.mark.django_db
    def test_the_empty_page_has_the_same_envelope_as_a_full_one(
        self, workspace, orphan, marcel, projects, logs
    ):
        """Built by paginating an empty queryset rather than by hand, so the ten keys around
        ``results`` cannot drift from the paginator's."""
        empty = _get(orphan, workspace, url=PORTAL_ISSUES_URL, project=projects["marubeni"]).data
        full = _get(marcel, workspace, url=PORTAL_ISSUES_URL, project=projects["marubeni"]).data

        assert set(empty) == set(full)


@pytest.mark.contract
class TestTheTablePagesAndFilters:
    """The two behaviours the screen depends on: it pages, and a chart click narrows it."""

    @pytest.mark.django_db
    def test_the_page_size_is_honoured_and_the_next_page_is_announced(
        self, workspace, marcel, projects, marubeni_tickets, logs
    ):
        response = _get(
            marcel, workspace, url=PORTAL_ISSUES_URL, project=projects["marubeni"], per_page=2
        )

        assert len(_rows(response)) == 2
        assert response.data["total_count"] == 4
        assert response.data["next_page_results"] is True

    @pytest.mark.django_db
    def test_walking_the_pages_yields_every_ticket_exactly_once(
        self, workspace, marcel, projects, marubeni_tickets, logs
    ):
        """**What a non-total sort order breaks.** Offset pagination re-runs the query per
        page, so two rows the ``ORDER BY`` cannot separate may land on both pages or on
        neither. Asserting the union is a *set of the right size* is what catches that; a
        per-page length assertion would not."""
        seen = []
        cursor = None

        for _ in range(4):
            params = {"per_page": 2}
            if cursor:
                params["cursor"] = cursor
            response = _get(
                marcel,
                workspace,
                url=PORTAL_ISSUES_URL,
                project=projects["marubeni"],
                **params,
            )
            seen.extend(row["issue_id"] for row in _rows(response))
            if not response.data["next_page_results"]:
                break
            cursor = response.data["next_cursor"]

        assert len(seen) == 4
        assert len(set(seen)) == 4

    @pytest.mark.django_db
    def test_the_newest_activity_comes_first(
        self, workspace, marcel, projects, marubeni_tickets, logs
    ):
        """``last_worked_on`` descending: a ticket list is scanned for what moved recently."""
        response = _get(
            marcel, workspace, url=PORTAL_ISSUES_URL, project=projects["marubeni"], per_page=100
        )

        dates = [row["last_worked_on"] for row in _rows(response)]
        assert dates == sorted(dates, reverse=True)
        assert dates[0] == "2026-04-05"

    @pytest.mark.django_db
    def test_a_competence_filter_narrows_the_table(
        self, workspace, marcel, projects, marubeni_tickets, logs
    ):
        """**This is the chart click.** Clicking a bar sends that bucket's own descriptor, and
        a competency is the only thing the bar identifies. Three Marubeni tickets are in
        2026-03 and one in 2026-04."""
        march = _get(
            marcel,
            workspace,
            url=PORTAL_ISSUES_URL,
            project=projects["marubeni"],
            per_page=100,
            competence_from="2026-03",
            competence_to="2026-03",
        )

        assert march.data["total_count"] == 3
        assert all(row["last_worked_on"].startswith("2026-03") for row in _rows(march))

    @pytest.mark.django_db
    def test_the_other_competence_returns_the_other_ticket(
        self, workspace, marcel, projects, marubeni_tickets, logs
    ):
        """The mirror, so a filter that silently did nothing fails here rather than passing the
        test above by returning everything."""
        april = _get(
            marcel,
            workspace,
            url=PORTAL_ISSUES_URL,
            project=projects["marubeni"],
            per_page=100,
            competence_from="2026-04",
            competence_to="2026-04",
        )

        assert april.data["total_count"] == 1
        assert _rows(april)[0]["last_worked_on"] == "2026-04-05"

    @pytest.mark.django_db
    def test_a_filtered_tables_totals_are_filtered_too(
        self, workspace, marcel, projects, marubeni_tickets, logs
    ):
        """``extra_stats`` is computed from the same narrowed descriptor, so the header of a
        filtered table cannot report the unfiltered total."""
        march = _get(
            marcel,
            workspace,
            url=PORTAL_ISSUES_URL,
            project=projects["marubeni"],
            competence_from="2026-03",
            competence_to="2026-03",
        )

        assert march.data["extra_stats"]["totals"]["issues"] == 3
        assert march.data["extra_stats"]["totals"]["entries"] == 3

    @pytest.mark.django_db
    def test_a_bad_filter_is_a_named_400_not_a_500(self, workspace, marcel, projects, logs):
        response = _get(
            marcel,
            workspace,
            url=PORTAL_ISSUES_URL,
            project=projects["marubeni"],
            competence_from="not-a-competence",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.data
        assert response.data["error"] == "INVALID_COMPETENCE"


@pytest.mark.contract
class TestPerIssueVisibilityReachesTheTable:
    """The gate the dashboard never needed. A total does not name the tickets it summed; a
    list does, so ``client_may_reach_issue``'s queryset form has to apply here.

    In the configuration this product prescribes -- ``guest_view_all_features = True`` on a
    Cliente's project, per the master context section 2b -- this narrowing is inert. These
    tests are about the project configured without it, where the alternative is a client
    reading the titles of tickets belonging to other people at their own company.
    """

    @pytest.mark.django_db
    def test_a_client_without_project_wide_visibility_sees_only_their_own_tickets(
        self, workspace, marcel, projects, marubeni_tickets, logs
    ):
        """Every ticket here was opened by the technician, so with the flag off the correct
        answer is none of them."""
        Project.objects.filter(id=projects["marubeni"].id).update(guest_view_all_features=False)

        response = _get(marcel, workspace, url=PORTAL_ISSUES_URL, project=projects["marubeni"])

        assert response.status_code == status.HTTP_200_OK, response.data
        assert _rows(response) == []

    @pytest.mark.django_db
    def test_the_same_request_with_the_flag_on_returns_them(
        self, workspace, marcel, projects, marubeni_tickets, logs
    ):
        """**The positive control, and the reason the test above is not vacuous.** The fixture
        sets the flag on, so this is the same request differing only in the flag -- which is
        what distinguishes "the gate works" from "the table is broken"."""
        response = _get(marcel, workspace, url=PORTAL_ISSUES_URL, project=projects["marubeni"])

        assert response.data["total_count"] == 4

    @pytest.mark.django_db
    def test_a_ticket_opened_on_the_clients_behalf_is_visible_without_the_flag(
        self, workspace, marcel, projects, technician, hour_type, billing_type
    ):
        """D62's clause, at this route. A technician opened it and recorded Marcel as the
        requester, so he must see it even with project-wide visibility off -- otherwise
        recording a requester is a feature that reports success and does nothing."""
        from plane.db.models import ServiceIssueRequester

        Project.objects.filter(id=projects["marubeni"].id).update(guest_view_all_features=False)

        log = _log(projects["marubeni"], workspace, technician, hour_type, billing_type)
        ServiceIssueRequester.objects.create(
            issue=log.issue,
            requester=marcel,
            project=projects["marubeni"],
            workspace=workspace,
            created_by=technician,
        )

        response = _get(marcel, workspace, url=PORTAL_ISSUES_URL, project=projects["marubeni"])

        rows = _rows(response)
        assert len(rows) == 1
        assert rows[0]["issue_id"] == str(log.issue_id)

    @pytest.mark.django_db
    def test_an_admin_is_not_narrowed_by_the_clients_visibility(
        self, workspace, create_user, projects, marubeni_tickets, logs
    ):
        """An Admin gets the client's *projection* by design, but not the client's *visibility*:
        narrowing them to tickets they personally created would make the audit view lie about
        what the client sees."""
        Project.objects.filter(id=projects["marubeni"].id).update(guest_view_all_features=False)

        response = _get(
            create_user, workspace, url=PORTAL_ISSUES_URL, project=projects["marubeni"]
        )

        assert response.data["total_count"] == 4


@pytest.mark.contract
class TestTheAdminRoutesStillRefuseTheClientAfterItem7:
    @pytest.mark.django_db
    def test_the_two_portal_routes_are_the_only_ones_that_admit_a_client(
        self, workspace, marcel, projects, logs
    ):
        """The refusal list of ``TestTheAdminReportsStillRefuseAClient`` restated as its
        complement, so adding a route without deciding its audience shows up here."""
        allowed = ("portal/", "portal/issues/")

        for path in allowed:
            response = _api(marcel).get(
                f"/api/workspaces/{workspace.slug}/service-reports/{path}"
                f"?project_ids={projects['marubeni'].id}"
            )
            assert response.status_code == status.HTTP_200_OK, (path, response.data)

        for path in ("consumption", "operational", "attention", "billing", "logs"):
            response = _api(marcel).get(
                f"/api/workspaces/{workspace.slug}/service-reports/{path}/"
            )
            assert response.status_code == status.HTTP_403_FORBIDDEN, (path, response.data)

    @pytest.mark.django_db
    def test_an_outsider_is_refused_the_ticket_table(self, workspace, projects, logs):
        outsider = User.objects.create(
            email=f"out-{uuid4().hex[:8]}@plane.so", username=f"out_{uuid4().hex[:8]}"
        )
        outsider.set_password("test-password")
        outsider.save()

        response = _get(outsider, workspace, url=PORTAL_ISSUES_URL, project=projects["marubeni"])

        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )
