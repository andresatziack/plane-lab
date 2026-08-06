# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""The work item allowance over HTTP: the credit action, the indicator, and the roles.

Section 4 of the phase brief, and the acceptance criteria as an admin and a technician
actually reach them.

**The authorisation assertions run against a fixture where the resource exists and the
happy path is proven in the same class.** A 403 on a workspace where the allowance does not
exist proves nothing -- it would pass against an endpoint that was never routed.
"""

# Python imports
import json
from datetime import date
from decimal import Decimal

# Third party imports
import pytest
from rest_framework.test import APIClient

# Module imports
from plane.db.models import (
    ProjectMember,
    ServiceBillingType,
    ServiceIssueAllowance,
    ServiceIssueAllowanceStatus,
    ServiceLog,
    Workspace,
    WorkspaceMember,
)
from plane.tests.factories import (
    IssueFactory,
    ProjectFactory,
    ServiceBillingTypeFactory,
    ServiceClientFactory,
    ServiceContractFactory,
    ServiceHourTypeFactory,
    UserFactory,
)
from plane.utils.service_allowance import close_allowance, credit_allowance
from plane.utils.service_pool import resolve_period

pytestmark = pytest.mark.contract

ALLOWANCE_URL = (
    "/api/workspaces/{slug}/projects/{project_id}/issues/{issue_id}/service-allowance/"
)
ALLOWANCE_CLOSE_URL = "/api/workspaces/{slug}/service-issue-allowances/{pk}/close/"
ALLOWANCE_ALERTS_URL = "/api/workspaces/{slug}/service-issue-allowance-alerts/"
POOL_URL = "/api/workspaces/{slug}/projects/{project_id}/issues/{issue_id}/service-pool/"
SERVICE_LOGS_URL = "/api/workspaces/{slug}/projects/{project_id}/issues/{issue_id}/service-logs/"


@pytest.fixture(autouse=True)
def no_celery_dispatch(monkeypatch):
    monkeypatch.setattr("celery.app.task.Task.apply_async", lambda *args, **kwargs: None)


@pytest.fixture
def setup(db):
    """One workspace with all three roles, a 30h/month support contract, and a project.

    Every role is created here rather than per test so that an authorisation assertion and
    the arithmetic beside it run against the same fixture.
    """
    admin = UserFactory(email="admin-allowance@plane.so")
    member = UserFactory(email="member-allowance@plane.so")
    guest = UserFactory(email="guest-allowance@plane.so")

    workspace = Workspace.objects.create(name="Allowance WS", slug="allowance-ws", owner=admin)

    for user, role in ((admin, 20), (member, 15), (guest, 5)):
        WorkspaceMember.objects.create(workspace=workspace, member=user, role=role)

    service_client = ServiceClientFactory(name="Marubeni", workspace=workspace)
    contract = ServiceContractFactory(
        service_client=service_client,
        code="SUP-001",
        name="Suporte",
        monthly_hours=Decimal("30.0000"),
        starts_on=date(2026, 1, 1),
        ends_on=date(2026, 12, 31),
    )
    project = ProjectFactory(name="Marubeni", workspace=workspace, service_client=service_client)
    project.is_time_tracking_enabled = True
    project.save()

    for user, role in ((admin, 20), (member, 15), (guest, 5)):
        ProjectMember.objects.create(project=project, member=user, role=role, workspace=workspace)

    hour_type = ServiceHourTypeFactory(
        workspace=workspace, name="Horario comercial", multiplier=Decimal("1.00")
    )
    pool_route = ServiceBillingTypeFactory(
        workspace=workspace,
        name="Contrato",
        billing_route=ServiceBillingType.BillingRoute.DEBIT_POOL,
    )

    # Materialised so that "the contract pool was not touched" is asserted against a row
    # that exists and holds zero, never against a missing period.
    january = resolve_period(contract, date(2026, 1, 15))

    assert contract.monthly_hours == Decimal("30.0000")
    assert january.consumed_hours == Decimal("0.0000")
    assert pool_route.billing_route == ServiceBillingType.BillingRoute.DEBIT_POOL

    return {
        "admin": admin,
        "member": member,
        "guest": guest,
        "workspace": workspace,
        "client": service_client,
        "contract": contract,
        "project": project,
        "january": january,
        "hour_type": hour_type,
        "pool_route": pool_route,
    }


def client_for(user):
    api_client = APIClient()
    api_client.force_authenticate(user=user)
    return api_client


def allowance_url(setup, issue):
    return ALLOWANCE_URL.format(
        slug=setup["workspace"].slug, project_id=setup["project"].id, issue_id=issue.id
    )


def post_credit(api_client, setup, issue, **body):
    return api_client.post(
        allowance_url(setup, issue), data=json.dumps(body), content_type="application/json"
    )


def post_log(api_client, setup, issue, *, minutes, worked_on, billing_type=None):
    return api_client.post(
        SERVICE_LOGS_URL.format(
            slug=setup["workspace"].slug, project_id=setup["project"].id, issue_id=issue.id
        ),
        data=json.dumps(
            {
                "worked_on": str(worked_on),
                "duration": f"{minutes}min",
                "entry_mode": "duration",
                "description": "Atendimento",
                "hour_type_id": str(setup["hour_type"].id),
                "billing_type_id": str((billing_type or setup["pool_route"]).id),
            }
        ),
        content_type="application/json",
    )


class TestCreditingThroughTheApi:
    def test_an_admin_credits_forty_hours_and_the_balance_appears(self, setup):
        """Criterion 1, through the API an admin actually uses."""
        issue = IssueFactory(project=setup["project"], name="Projeto de migracao")

        response = post_credit(
            client_for(setup["admin"]),
            setup,
            issue,
            hours="40.0000",
            reference="Proposta 2026-014",
        )

        assert response.status_code == 200

        payload = response.json()
        assert payload["allowance"]["credited_hours"] == "40.0000"
        assert payload["allowance"]["balance_hours"] == "40.0000"
        assert payload["allowance"]["reference"] == "Proposta 2026-014"
        assert payload["summary"]["consumed_pct"] == "0.00"
        assert len(payload["credits"]) == 1

    def test_a_second_credit_adds_up_and_returns_both(self, setup):
        """Criterion 5, with the history the criterion actually asks for."""
        issue = IssueFactory(project=setup["project"])
        api_client = client_for(setup["admin"])

        post_credit(api_client, setup, issue, hours="40.0000")
        response = post_credit(api_client, setup, issue, hours="10.0000")

        assert response.status_code == 200

        payload = response.json()
        assert payload["allowance"]["credited_hours"] == "50.0000"
        assert [entry["hours"] for entry in payload["credits"]] == ["40.0000", "10.0000"]
        assert ServiceIssueAllowance.objects.filter(issue_id=issue.id).count() == 1

    def test_a_credit_of_zero_is_a_bad_request(self, setup):
        issue = IssueFactory(project=setup["project"])

        response = post_credit(client_for(setup["admin"]), setup, issue, hours="0.0000")

        assert response.status_code == 400
        assert not ServiceIssueAllowance.objects.filter(issue_id=issue.id).exists()

    def test_reading_an_issue_without_an_allowance_returns_null(self, setup):
        """A legitimate answer, and it has to be distinguishable from an error: it means
        rule R6 falls through to the contract pool."""
        issue = IssueFactory(project=setup["project"])

        response = client_for(setup["member"]).get(allowance_url(setup, issue))

        assert response.status_code == 200
        assert response.json()["allowance"] is None

    def test_crediting_a_sub_task_credits_the_sub_task_and_not_the_parent(self, setup):
        """The Admin named a work item, so the hours go there.

        Crediting through inheritance would put a commercial decision somewhere other than
        where it was aimed, even though it is usually what was meant. To top up the parent
        project, credit the parent.
        """
        parent = IssueFactory(project=setup["project"])
        sub_task = IssueFactory(project=setup["project"], parent=parent)
        api_client = client_for(setup["admin"])

        post_credit(api_client, setup, parent, hours="40.0000")
        post_credit(api_client, setup, sub_task, hours="8.0000")

        assert ServiceIssueAllowance.objects.get(issue_id=parent.id).credited_hours == Decimal(
            "40.0000"
        )
        assert ServiceIssueAllowance.objects.get(issue_id=sub_task.id).credited_hours == Decimal(
            "8.0000"
        )


class TestRoles:
    """Section 4 restricts the credit action to Admin. Reading is wider, because the
    indicator is for the technician who is about to log against it."""

    def test_a_member_cannot_credit_hours(self, setup):
        """Crediting is a commercial act: it decides a project is worth 40 hours."""
        issue = IssueFactory(project=setup["project"])

        response = post_credit(client_for(setup["member"]), setup, issue, hours="40.0000")

        assert response.status_code == 403
        assert not ServiceIssueAllowance.objects.filter(issue_id=issue.id).exists()

    def test_a_member_can_read_the_balance(self, setup):
        """Positive control for the 403 above, and section 4's indicator: the technician
        about to log 5h needs to know 35h are left."""
        issue = IssueFactory(project=setup["project"])
        post_credit(client_for(setup["admin"]), setup, issue, hours="40.0000")

        response = client_for(setup["member"]).get(allowance_url(setup, issue))

        assert response.status_code == 200
        assert response.json()["allowance"]["balance_hours"] == "40.0000"

    def test_a_guest_cannot_read_the_balance(self, setup):
        """The payload carries debited hours and pool totals. The client's own view is
        Phase 8's portal with its own serializer -- sending it here and hiding it in the
        interface is the leak R11(b) names."""
        issue = IssueFactory(project=setup["project"])
        post_credit(client_for(setup["admin"]), setup, issue, hours="40.0000")

        response = client_for(setup["guest"]).get(allowance_url(setup, issue))

        assert response.status_code == 403

    def test_a_guest_cannot_credit_hours(self, setup):
        issue = IssueFactory(project=setup["project"])

        response = post_credit(client_for(setup["guest"]), setup, issue, hours="40.0000")

        assert response.status_code == 403

    def test_a_member_cannot_close_an_allowance(self, setup):
        issue = IssueFactory(project=setup["project"])
        post_credit(client_for(setup["admin"]), setup, issue, hours="40.0000")
        allowance = ServiceIssueAllowance.objects.get(issue_id=issue.id)

        response = client_for(setup["member"]).post(
            ALLOWANCE_CLOSE_URL.format(slug=setup["workspace"].slug, pk=allowance.id)
        )

        assert response.status_code == 403
        allowance.refresh_from_db()
        assert allowance.status == ServiceIssueAllowanceStatus.OPEN


class TestDebitThroughTheApi:
    def test_logging_time_consumes_the_allowance_and_leaves_the_contract_intact(self, setup):
        """Criteria 2 and 3, end to end over HTTP -- the path the technician takes."""
        issue = IssueFactory(project=setup["project"])
        post_credit(client_for(setup["admin"]), setup, issue, hours="40.0000")

        response = post_log(
            client_for(setup["member"]), setup, issue, minutes=5 * 60, worked_on=date(2026, 1, 15)
        )

        assert response.status_code == 201

        allowance = ServiceIssueAllowance.objects.get(issue_id=issue.id)
        assert allowance.balance_hours == Decimal("35.0000")

        setup["january"].refresh_from_db()
        assert setup["january"].consumed_hours == Decimal("0.0000")

        log = ServiceLog.objects.get(issue_id=issue.id)
        assert log.debited_allowance_id == allowance.pk
        assert log.debited_period_id is None

    def test_the_work_item_pool_panel_names_the_allowance_as_the_origin(self, setup):
        """Section 4's indicator, on the endpoint the work item panel already reads."""
        issue = IssueFactory(project=setup["project"])
        post_credit(client_for(setup["admin"]), setup, issue, hours="40.0000")

        response = client_for(setup["member"]).get(
            POOL_URL.format(
                slug=setup["workspace"].slug, project_id=setup["project"].id, issue_id=issue.id
            )
            + "?worked_on=2026-01-15"
        )

        assert response.status_code == 200

        payload = response.json()
        assert payload["origin"] == "issue_allowance"
        assert payload["allowance"]["balance_hours"] == "40.0000"
        assert payload["period"] is None

    def test_a_sub_task_log_consumes_the_parents_allowance(self, setup):
        """Criterion 2's inheritance pair, over HTTP."""
        parent = IssueFactory(project=setup["project"])
        sub_task = IssueFactory(project=setup["project"], parent=parent)

        post_credit(client_for(setup["admin"]), setup, parent, hours="40.0000")

        response = post_log(
            client_for(setup["member"]),
            setup,
            sub_task,
            minutes=5 * 60,
            worked_on=date(2026, 1, 15),
        )

        assert response.status_code == 201

        assert ServiceIssueAllowance.objects.get(issue_id=parent.id).balance_hours == Decimal(
            "35.0000"
        )

        setup["january"].refresh_from_db()
        assert setup["january"].consumed_hours == Decimal("0.0000")

    def test_a_log_on_an_issue_without_an_allowance_still_debits_the_contract(self, setup):
        """Criterion 4, and the positive control for every isolation assertion above."""
        issue = IssueFactory(project=setup["project"])

        response = post_log(
            client_for(setup["member"]), setup, issue, minutes=5 * 60, worked_on=date(2026, 1, 15)
        )

        assert response.status_code == 201

        setup["january"].refresh_from_db()
        assert setup["january"].consumed_hours == Decimal("5.0000")

    def test_a_log_on_a_closed_allowance_is_refused_and_names_why(self, setup):
        """It blocks, and it does **not** fall back to the contract -- which is the
        migration section 3 forbids."""
        issue = IssueFactory(project=setup["project"])
        post_credit(client_for(setup["admin"]), setup, issue, hours="40.0000")
        close_allowance(ServiceIssueAllowance.objects.get(issue_id=issue.id), actor=setup["admin"])

        response = post_log(
            client_for(setup["member"]), setup, issue, minutes=60, worked_on=date(2026, 1, 15)
        )

        assert response.status_code == 400
        assert response.json()["error"] == "ALLOWANCE_IS_CLOSED"

        setup["january"].refresh_from_db()
        assert setup["january"].consumed_hours == Decimal("0.0000")


class TestClosingThroughTheApi:
    def test_closing_reports_the_reconciliation(self, setup):
        """Closing is the one moment the ledger must sum to exactly zero, so it is the
        cheapest place to prove it did."""
        issue = IssueFactory(project=setup["project"])
        post_credit(client_for(setup["admin"]), setup, issue, hours="40.0000")
        post_log(
            client_for(setup["member"]), setup, issue, minutes=30 * 60, worked_on=date(2026, 1, 15)
        )

        allowance = ServiceIssueAllowance.objects.get(issue_id=issue.id)

        response = client_for(setup["admin"]).post(
            ALLOWANCE_CLOSE_URL.format(slug=setup["workspace"].slug, pk=allowance.id)
        )

        assert response.status_code == 200

        payload = response.json()
        assert payload["allowance"]["status"] == "closed"
        assert payload["allowance"]["expired_hours"] == "10.0000"
        assert payload["reconciliation"]["is_consistent"] is True

    def test_closing_twice_is_a_bad_request(self, setup):
        issue = IssueFactory(project=setup["project"])
        post_credit(client_for(setup["admin"]), setup, issue, hours="40.0000")
        allowance = ServiceIssueAllowance.objects.get(issue_id=issue.id)

        api_client = client_for(setup["admin"])
        url = ALLOWANCE_CLOSE_URL.format(slug=setup["workspace"].slug, pk=allowance.id)

        assert api_client.post(url).status_code == 200

        response = api_client.post(url)

        assert response.status_code == 400
        assert response.json()["error"] == "ALLOWANCE_ALREADY_CLOSED"


class TestAlertsThroughTheApi:
    def test_an_overrun_allowance_appears_on_the_panel(self, setup):
        """Section 3: overflowing does not block, "a bolsa fica com saldo negativo e o
        alerta aparece". This is the second half of that sentence."""
        issue = IssueFactory(project=setup["project"], name="Projeto estourado")
        post_credit(client_for(setup["admin"]), setup, issue, hours="10.0000")
        post_log(
            client_for(setup["member"]), setup, issue, minutes=25 * 60, worked_on=date(2026, 1, 15)
        )

        response = client_for(setup["member"]).get(
            ALLOWANCE_ALERTS_URL.format(slug=setup["workspace"].slug)
        )

        assert response.status_code == 200

        payload = response.json()
        assert payload["count"] == 1

        entry = payload["entries"][0]
        assert entry["issue_id"] == str(issue.id)
        assert entry["balance_hours"] == "-15.0000"
        assert [alert["code"] for alert in entry["alerts"]] == ["ALLOWANCE_NEGATIVE_BALANCE"]

    def test_a_healthy_allowance_does_not_appear(self, setup):
        """The positive control the panel needs: a panel that lists everything is a panel
        nobody reads."""
        issue = IssueFactory(project=setup["project"])
        post_credit(client_for(setup["admin"]), setup, issue, hours="40.0000")
        post_log(
            client_for(setup["member"]), setup, issue, minutes=60, worked_on=date(2026, 1, 15)
        )

        response = client_for(setup["member"]).get(
            ALLOWANCE_ALERTS_URL.format(slug=setup["workspace"].slug)
        )

        assert response.json()["count"] == 0


class TestWorkspaceIsolation:
    def test_an_allowance_of_another_workspace_is_not_reachable(self, setup):
        """D19 requires this for every new entity: our own code does not inherit Plane's
        project isolation automatically."""
        other_owner = UserFactory(email="other-allowance@plane.so")
        other_workspace = Workspace.objects.create(
            name="Other", slug="other-allowance-ws", owner=other_owner
        )
        WorkspaceMember.objects.create(workspace=other_workspace, member=other_owner, role=20)
        other_project = ProjectFactory(name="Outro", workspace=other_workspace)
        other_issue = IssueFactory(project=other_project)
        credit_allowance(other_issue, Decimal("40.0000"), actor=other_owner)

        other_allowance = ServiceIssueAllowance.objects.get(issue_id=other_issue.id)

        # Asking for it through *this* workspace's slug must not find it.
        response = client_for(setup["admin"]).post(
            ALLOWANCE_CLOSE_URL.format(slug=setup["workspace"].slug, pk=other_allowance.id)
        )

        assert response.status_code == 404

        other_allowance.refresh_from_db()
        assert other_allowance.status == ServiceIssueAllowanceStatus.OPEN
