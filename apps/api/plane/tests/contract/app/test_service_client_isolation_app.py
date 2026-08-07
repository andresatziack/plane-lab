# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""No service endpoint leaks a Cliente outside the caller's scope. Criterion 16, D19.

Section 6 of the phase brief: "um endpoint novo que esqueça o escopo de project é o
vazamento mais provável desta arquitetura -- os work items estão protegidos pelo Plane, mas
os apontamentos e contratos são código seu."

**The surface sweep enumerates Django's URL resolver rather than a hand-written list.**
D66. A list would be correct on the day it was written and stale on the day a tenth phase
adds a route. This walks every registered ``service-*`` pattern, probes each with every verb,
and demands a refusal -- so a new route is refused by default and admitting a client to one
requires a deliberate edit to :data:`CLIENT_REACHABLE`, in this file, with a reason.

That inversion is the whole point. The failure mode being guarded against is not a wrong
decision; it is an *absent* one.

It earned itself on the first run, by finding a cross-tenant read in Phase 2b's
``ServiceClassificationWindowViewSet``: ``get: retrieve`` was routed with no ``retrieve``
defined, so the inherited ``ModelViewSet`` one ran under ``IsAuthenticated`` alone. That fix
ships on its own, and the reasoning is in D66.

The reference scenario is the series' own (D19): Marcel is a client user in Marubeni and
Terlogs, Adriano only in Terlogs, and neither may ever see Vale -- a third Cliente belonging
to neither. Probed by direct id, by filter, by search and by listing, because those are four
different code paths and only the first is obvious.
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from django.urls import URLPattern, URLResolver, get_resolver
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

#: The only service routes a client's own user may reach, and why each one is safe.
#:
#: Every other ``service-*`` route must refuse a GUEST. Adding an entry here is a visibility
#: decision: it says "a client may call this, and I have checked what it returns".
CLIENT_REACHABLE = {
    # Which Clientes this user belongs to, derived from project membership. Identity only --
    # no billing configuration, no contract, no rate. Phase 1.
    "service-clients/me/": "the caller's own Clientes, identity only",
    # The portal dashboard. `_viewer` is fixed to ReportViewer.guest(), so no money and no
    # logged hours are computed, and the scope is the caller's own projects. D55, D63.
    "service-reports/portal/": "the client's own consumption, guest projection, own projects",
    # The client's work logs for one work item, through ServiceLogClientSerializer: no raw
    # duration, no logged hours, no multiplier, no rate, and money only on rows whose
    # settled route billed them. D58, D65.
    "service-logs/client/": "the client's own work logs, R11 allowlist",
    # The work item itself. Core Plane's own scoping plus this phase's field allowlist:
    # a client may change the priority and the state and nothing else. D59, D60.
    "issues/<uuid:issue_id>/": "the work item, narrowed to priority and state",
}

#: Refusals that mean "no data crossed". A 405 is safe -- DRF rejects the verb before any
#: handler runs -- and 401/403 are the permission refusals. Anything else (200, 400, 404,
#: 500) means the permission layer let the request through to the view body, which for a
#: client on one of these routes is the leak this file exists to catch.
REFUSALS = {
    status.HTTP_401_UNAUTHORIZED,
    status.HTTP_403_FORBIDDEN,
    status.HTTP_405_METHOD_NOT_ALLOWED,
}

VERBS = ("get", "post", "patch", "put", "delete")


def _service_routes():
    """Every registered URL pattern whose route mentions a service resource.

    Walks the resolver so the sweep cannot fall behind the routing table.
    """
    found = []

    def walk(resolver, prefix=""):
        for entry in resolver.url_patterns:
            if isinstance(entry, URLResolver):
                walk(entry, prefix + str(entry.pattern))
            elif isinstance(entry, URLPattern):
                route = prefix + str(entry.pattern)
                if "service-" in route:
                    found.append(route)

    walk(get_resolver())
    return sorted(set(found))


def _fill(route, *, slug, project_id, issue_id, some_uuid):
    """Substitute concrete values for the pattern's converters.

    The ids do not have to exist. ``allow_permission`` runs before the view body, so a
    refusal arrives before any lookup -- and a route that answers 404 instead of 403 has
    told us the permission layer passed, which is exactly what this file wants to know.
    """
    filled = route
    filled = filled.replace("<str:slug>", slug)
    filled = filled.replace("<uuid:project_id>", str(project_id))
    filled = filled.replace("<uuid:issue_id>", str(issue_id))

    for name in (
        "pk",
        "member_id",
        "batch_id",
        "price_id",
        "service_client_id",
        "contract_id",
        "allowance_id",
    ):
        filled = filled.replace(f"<uuid:{name}>", str(some_uuid))

    return "/" + filled.lstrip("^").rstrip("$")


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
    """Three Clientes. Vale belongs to neither client user and is the leak canary."""
    return {
        name: ServiceClient.objects.create(name=name.title(), workspace=workspace)
        for name in ("marubeni", "terlogs", "vale")
    }


@pytest.fixture
def projects(db, workspace, create_user, clients):
    built = {}

    for name, service_client in clients.items():
        project = Project.objects.create(
            name=f"{name.title()} Support",
            identifier=name[:4].upper(),
            workspace=workspace,
            created_by=create_user,
            service_client=service_client,
            guest_view_all_features=True,
            is_time_tracking_enabled=True,
        )
        ProjectMember.objects.create(project=project, member=create_user, workspace=workspace, role=20)
        built[name] = project

    return built


@pytest.fixture
def technician(db, workspace, projects):
    return _user("tech", workspace, 15, projects.values())


@pytest.fixture
def marcel(db, workspace, projects):
    """Client user of two Clientes."""
    return _user("marcel", workspace, 5, [projects["marubeni"], projects["terlogs"]])


@pytest.fixture
def adriano(db, workspace, projects):
    """Client user of one."""
    return _user("adriano", workspace, 5, [projects["terlogs"]])


@pytest.fixture
def hour_type(db, workspace):
    return ServiceHourType.objects.create(
        name="Fora do expediente", workspace=workspace, multiplier=Decimal("1.50")
    )


@pytest.fixture
def billing_type(db, workspace):
    return ServiceBillingType.objects.create(
        name="Fatura reais", workspace=workspace, billing_route=ServiceBillingType.BillingRoute.BILL_AMOUNT
    )


@pytest.fixture
def issues(db, projects, workspace, technician):
    built = {}

    for name, project in projects.items():
        issue = Issue(name=f"{name} incident", project=project, workspace=workspace)
        issue.save(created_by_id=technician.id)
        built[name] = issue

    return built


@pytest.fixture
def logs(db, projects, issues, workspace, technician, hour_type, billing_type):
    """One priced work log per Cliente, with a distinct amount so a leak is identifiable."""
    amounts = {"marubeni": "300.00", "terlogs": "600.00", "vale": "900.00"}
    built = {}

    for name, project in projects.items():
        built[name] = ServiceLog.objects.create(
            issue=issues[name],
            project=project,
            workspace=workspace,
            author=technician,
            hour_type=hour_type,
            billing_type=billing_type,
            worked_on=date(2026, 3, 10),
            description=f"work for {name}",
            raw_duration_minutes=60,
            logged_hours=Decimal("1.0000"),
            equivalent_hours=Decimal("1.5000"),
            debited_hours=Decimal("1.5000"),
            applied_multiplier=Decimal("1.50"),
            applied_billing_route=ServiceBillingType.BillingRoute.BILL_AMOUNT,
            settled_billing_route=ServiceBillingType.BillingRoute.BILL_AMOUNT,
            applied_hour_rate=Decimal("200.00"),
            applied_rate_basis=ServiceLog.RateBasis.BASE_MULTIPLIER,
            amount=Decimal(amounts[name]),
            batch_id=uuid4(),
        )

    return built


@pytest.fixture
def contracts(db, workspace, clients, create_user):
    built = {}

    for name, service_client in clients.items():
        built[name] = ServiceContract.objects.create(
            workspace=workspace,
            service_client=service_client,
            code=f"CT-{name[:4].upper()}",
            name=f"{name.title()} support contract",
            monthly_hours=Decimal("40.0000"),
            starts_on=date(2026, 1, 1),
            ends_on=date(2026, 12, 31),
            created_by=create_user,
        )

    return built


def _api(user):
    api = APIClient()
    api.force_authenticate(user=user)
    return api


def _body(response):
    """The response as a searchable string, whatever renderer produced it."""
    try:
        return str(response.data)
    except AttributeError:
        return response.content.decode("utf-8", errors="replace")


@pytest.mark.contract
class TestCriterion16TheServiceSurfaceRefusesAClientByDefault:
    def test_the_sweep_actually_found_the_surface(self):
        """A resolver walk that silently matched nothing would make every test below pass."""
        routes = _service_routes()

        assert len(routes) >= 50, routes

    @pytest.mark.django_db
    @pytest.mark.parametrize("route", _service_routes())
    def test_no_service_route_admits_a_client_unless_it_is_named(
        self, route, workspace, projects, issues, marcel, logs, contracts
    ):
        """Every registered service route, every verb, refused -- unless it is in
        ``CLIENT_REACHABLE`` with a stated reason.

        This is the test that makes a *forgotten* decision fail. A tenth phase adding
        ``service-invoices/`` gets a red test until somebody says what a client may see."""
        exempt = any(marker in route for marker in CLIENT_REACHABLE)

        url = _fill(
            route,
            slug=workspace.slug,
            project_id=projects["marubeni"].id,
            issue_id=issues["marubeni"].id,
            some_uuid=uuid4(),
        )

        api = _api(marcel)

        for verb in VERBS:
            response = getattr(api, verb)(url, {}, format="json")

            if exempt:
                continue

            assert response.status_code in REFUSALS, (
                f"{verb.upper()} {url} answered {response.status_code}; a client reached past "
                f"the permission layer. Either scope it or name it in CLIENT_REACHABLE."
            )

    @pytest.mark.django_db
    def test_the_named_exceptions_are_the_four_expected_ones(self):
        """Pinned so that widening the allowlist is a visible diff rather than a quiet one."""
        assert set(CLIENT_REACHABLE) == {
            "service-clients/me/",
            "service-reports/portal/",
            "service-logs/client/",
            "issues/<uuid:issue_id>/",
        }

    @pytest.mark.django_db
    @pytest.mark.parametrize(
        "route",
        [
            "service-hour-types/",
            "service-billing-types/",
            "service-config-activities/",
            "service-holidays/",
            "service-holidays/calendar/",
            "service-classification-windows/",
            "service-classification-windows/coverage/",
            "service-contracts/",
            "service-contract-alerts/",
            "service-issue-allowance-alerts/",
            "service-clients/",
            "service-log-exports/",
            "service-billing-consolidation/",
            "service-member-permissions/",
            "service-member-permissions/me/",
        ],
        ids=lambda route: route.strip("/").replace("/", "-"),
    )
    def test_the_routes_the_brief_names_are_refused_individually(self, route, workspace, marcel, logs):
        """The resolver sweep above covers these, but the brief names them one by one and a
        named failure is easier to act on than one parametrized over a generated list.

        ``service-hour-types/`` matters most and the reason is not organisational: the
        payload carries ``multiplier``, and R11 keeps the multiplier away from the client.
        The portal reads the hour type *label* through the work log serializer instead.
        ``service-config-activities/`` is the history of multipliers and prices --
        commercial intelligence, ADMIN only, not even MEMBER."""
        response = _api(marcel).get(f"/api/workspaces/{workspace.slug}/{route}")

        assert response.status_code == status.HTTP_403_FORBIDDEN, _body(response)

    @pytest.mark.django_db
    def test_the_bulk_import_is_refused_too(self, workspace, marcel):
        """Named separately in the brief because a write route that only refuses reads is a
        route somebody forgot to finish."""
        response = _api(marcel).post(
            f"/api/workspaces/{workspace.slug}/service-holidays/bulk-import/", {}, format="json"
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN, _body(response)

    @pytest.mark.django_db
    def test_a_technician_still_reaches_them(self, workspace, technician, logs):
        """The positive control for the whole sweep. Without it, a routing table that
        refused everybody would look like airtight isolation."""
        for route in ("service-hour-types/", "service-billing-types/", "service-contracts/"):
            response = _api(technician).get(f"/api/workspaces/{workspace.slug}/{route}")

            assert response.status_code == status.HTTP_200_OK, f"{route}: {_body(response)}"


@pytest.mark.contract
class TestD19OneClientUserNeverSeesAnotherCliente:
    @pytest.mark.django_db
    def test_marcel_sees_both_of_his_clientes(self, workspace, marcel, projects, logs):
        """The positive control first: he is a client user of two, and must see two."""
        response = _api(marcel).get(f"/api/workspaces/{workspace.slug}/service-clients/me/")

        assert response.status_code == status.HTTP_200_OK, _body(response)
        names = {row["name"] for row in response.data}
        assert names == {"Marubeni", "Terlogs"}

    @pytest.mark.django_db
    def test_marcel_never_sees_the_third_cliente(self, workspace, marcel, logs):
        response = _api(marcel).get(f"/api/workspaces/{workspace.slug}/service-clients/me/")

        assert "Vale" not in _body(response)

    @pytest.mark.django_db
    def test_adriano_sees_only_his_one(self, workspace, adriano, logs):
        response = _api(adriano).get(f"/api/workspaces/{workspace.slug}/service-clients/me/")

        names = {row["name"] for row in response.data}
        assert names == {"Terlogs"}
        assert "Marubeni" not in _body(response)

    @pytest.mark.django_db
    def test_adriano_is_denied_a_marubeni_work_item_by_direct_id(
        self, workspace, adriano, projects, issues
    ):
        """By direct id -- the first of the four paths the brief asks for."""
        response = _api(adriano).get(
            f"/api/workspaces/{workspace.slug}/projects/{projects['marubeni'].id}"
            f"/issues/{issues['marubeni'].id}/"
        )

        assert response.status_code in REFUSALS, _body(response)

    @pytest.mark.django_db
    def test_adriano_is_denied_marubeni_work_logs_by_direct_id(
        self, workspace, adriano, projects, issues, logs
    ):
        """The same probe against *this phase's* new endpoint, which is the one the brief
        warns about: the work items are protected by Plane, the work logs are our code."""
        response = _api(adriano).get(
            f"/api/workspaces/{workspace.slug}/projects/{projects['marubeni'].id}"
            f"/issues/{issues['marubeni'].id}/service-logs/client/"
        )

        assert response.status_code in REFUSALS, _body(response)
        assert "300.00" not in _body(response)

    @pytest.mark.django_db
    def test_adrianos_portal_dashboard_carries_no_marubeni_hours(
        self, workspace, adriano, logs
    ):
        """By listing. Terlogs' log is 1.5h, and so is Marubeni's -- so the total is the
        assertion: 1.5 means the scope held, 3.0 or 4.5 means it did not."""
        response = _api(adriano).get(f"/api/workspaces/{workspace.slug}/service-reports/portal/")

        assert response.status_code == status.HTTP_200_OK, _body(response)
        assert response.data["totals"]["entries"] == 1
        assert response.data["totals"]["equivalent_hours"] == "1.5000"

    @pytest.mark.django_db
    def test_marcels_portal_dashboard_carries_both_of_his_and_not_the_third(
        self, workspace, marcel, logs
    ):
        """Two Clientes, two logs, 3.0h -- and never the third client's 1.5h on top."""
        response = _api(marcel).get(f"/api/workspaces/{workspace.slug}/service-reports/portal/")

        assert response.data["totals"]["entries"] == 2
        assert response.data["totals"]["equivalent_hours"] == "3.0000"

    @pytest.mark.django_db
    def test_a_crafted_filter_does_not_reach_another_cliente(
        self, workspace, adriano, clients, projects, logs
    ):
        """By filter. The descriptor travels to the browser and back, so it is a record and
        not a permission -- D63."""
        for query in (
            f"?service_client_ids={clients['marubeni'].id}",
            f"?project_ids={projects['marubeni'].id}",
            f"?service_client_ids={clients['vale'].id},{clients['marubeni'].id}",
        ):
            response = _api(adriano).get(
                f"/api/workspaces/{workspace.slug}/service-reports/portal/{query}"
            )

            assert response.status_code == status.HTTP_200_OK, _body(response)
            body = _body(response)
            assert "300.00" not in body, query
            assert "900.00" not in body, query

    @pytest.mark.django_db
    def test_a_search_does_not_reach_another_cliente(self, workspace, adriano, logs):
        """By search. The work item search is core Plane's, so this is a characterisation
        test -- it exists because "protected by Plane" is a claim worth checking once."""
        response = _api(adriano).get(
            f"/api/workspaces/{workspace.slug}/search/?search=incident&workspace_search=true"
        )

        if response.status_code == status.HTTP_200_OK:
            assert "marubeni incident" not in _body(response).lower()

    @pytest.mark.django_db
    def test_no_client_user_sees_any_money_on_any_route_they_can_reach(
        self, workspace, marcel, adriano, projects, issues, logs
    ):
        """The money canary across the whole reachable surface at once.

        Each Cliente's log has a distinct amount, so this asserts that none of the three
        values appears anywhere a client can read -- including their *own*, on the portal
        dashboard, where D58's per-row exception does not apply because the dashboard deals
        in hours only."""
        for user in (marcel, adriano):
            dashboard = _api(user).get(f"/api/workspaces/{workspace.slug}/service-reports/portal/")

            for amount in ("300.00", "600.00", "900.00"):
                assert amount not in _body(dashboard), f"{amount} leaked to the portal dashboard"

            assert "1.0000" not in _body(dashboard), "logged hours leaked to the portal dashboard"

    @pytest.mark.django_db
    def test_the_client_does_see_the_amount_on_their_own_billed_work_log(
        self, workspace, adriano, projects, issues, logs
    ):
        """The positive control for the canary above, and D58's boundary: money is absent
        from the dashboard but present on the client's own billed row, because that row is
        an invoice line they will receive."""
        response = _api(adriano).get(
            f"/api/workspaces/{workspace.slug}/projects/{projects['terlogs'].id}"
            f"/issues/{issues['terlogs'].id}/service-logs/client/"
        )

        assert response.status_code == status.HTTP_200_OK, _body(response)
        assert response.data["service_logs"][0]["amount"] == "600.00"
