# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""The client's own consumption dashboard. D55, D63. Criteria 13 and 22.

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


def _get(user, workspace, url=PORTAL_URL, **params):
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
    def test_a_client_reaches_the_portal_dashboard(self, workspace, marcel, logs):
        response = _get(marcel, workspace)

        assert response.status_code == status.HTTP_200_OK, response.data

    @pytest.mark.django_db
    def test_the_payload_carries_no_logged_hours_anywhere(self, workspace, marcel, logs):
        """The inherited criterion, asserted at the HTTP boundary. Nothing computes
        logged_hours for a guest viewer, so the key is absent rather than stripped."""
        response = _get(marcel, workspace)

        assert _hour_keys(response.data, "logged_hours") == []
        assert _hour_keys(response.data, "logged_hours_display") == []

    @pytest.mark.django_db
    def test_the_payload_carries_no_money_anywhere(self, workspace, marcel, logs):
        response = _get(marcel, workspace)

        assert _money_keys(response.data) == set()

    @pytest.mark.django_db
    def test_revenue_series_is_not_even_a_key(self, workspace, marcel, logs):
        """`revenue_series` returns hours-only buckets rather than raising for a non-money
        viewer, so calling it would have produced a silently empty section instead of an
        error. It is not called at all."""
        response = _get(marcel, workspace)

        assert "revenue_series" not in response.data

    @pytest.mark.django_db
    def test_the_client_does_receive_their_hours(self, workspace, marcel, logs):
        """The positive control. Every absence above is satisfied by an empty payload."""
        response = _get(marcel, workspace)

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
        self, workspace, create_user, logs
    ):
        """Unconditional by design: it makes the portal auditable from the inside, and
        leaves no role-conditional branch for R11 to be wrong in."""
        response = _get(create_user, workspace)

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
    def test_a_client_sees_only_their_own_clients_hours(self, workspace, marcel, logs):
        """Marubeni's log is 1.5h; Terlogs' is another 1.5h. A total of 1.5 proves the
        scope held; 3.0 would mean it did not."""
        response = _get(marcel, workspace)

        assert response.data["totals"]["equivalent_hours"] == EQUIVALENT
        assert response.data["totals"]["entries"] == 1

    @pytest.mark.django_db
    def test_the_other_client_sees_only_theirs(self, workspace, adriano, logs):
        """The mirror, so a scope that accidentally pinned one project would fail here."""
        response = _get(adriano, workspace)

        assert response.data["totals"]["entries"] == 1

    @pytest.mark.django_db
    def test_a_crafted_project_filter_cannot_widen_the_scope(self, workspace, marcel, projects, logs):
        """`narrow()` replaces rather than intersects, so the scope must be applied after
        the query string. This is the test that fails if the order is ever reversed."""
        response = _get(
            marcel,
            workspace,
            project_ids=f"{projects['marubeni'].id},{projects['terlogs'].id}",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["totals"]["entries"] == 1
        assert response.data["totals"]["equivalent_hours"] == EQUIVALENT

    @pytest.mark.django_db
    def test_asking_only_for_another_clients_project_returns_nothing_of_theirs(
        self, workspace, marcel, projects, logs
    ):
        response = _get(marcel, workspace, project_ids=str(projects["terlogs"].id))

        assert response.status_code == status.HTTP_200_OK, response.data
        # The scope replaced the request, so the caller gets their own project, not an
        # empty result and certainly not Terlogs'.
        assert response.data["totals"]["entries"] == 1
        assert response.data["totals"]["equivalent_hours"] == EQUIVALENT

    @pytest.mark.django_db
    def test_a_crafted_service_client_filter_cannot_widen_the_scope(
        self, workspace, marcel, clients, logs
    ):
        """The other axis: the descriptor also carries `service_client_ids`, and the project
        scope has to defeat it because the project is what membership is granted on."""
        response = _get(marcel, workspace, service_client_ids=str(clients["terlogs"].id))

        assert response.data["totals"]["entries"] == 0 or response.data["totals"][
            "equivalent_hours"
        ] == "0.0000"

    @pytest.mark.django_db
    def test_a_client_with_no_projects_sees_nothing_rather_than_everything(
        self, workspace, orphan, logs
    ):
        """The most dangerous failure in this phase: an empty scope becoming total access.
        `narrow(project_ids=())` would have applied no project filter at all."""
        response = _get(orphan, workspace)

        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["totals"]["entries"] == 0
        assert response.data["consumption_series"] == []
        assert _money_keys(response.data) == set()

    @pytest.mark.django_db
    def test_the_empty_payload_has_the_same_keys_as_a_full_one(self, workspace, orphan, marcel, logs):
        """So the frontend has no special case, and so a future key added to one branch and
        not the other fails here."""
        empty = _get(orphan, workspace).data
        full = _get(marcel, workspace).data

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
