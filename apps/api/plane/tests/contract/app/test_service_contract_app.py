# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""HTTP surface of contracts, periods and the alert panel.

Covers the acceptance criteria that are only true if they are true *through the API*:

* **13** -- a closed period rejects a new work log with a clear message, and the rows
  roll back with the refusal;
* **2, 3, 4, 12** through the real work log endpoints rather than the domain layer, so
  the debit is proven to be wired in and not merely to exist;
* **7, 18, 19** on the alert panel;
* **8, 9** through the close endpoint;
* authorisation: GUEST reaches none of it, and MEMBER cannot write contract terms.

The authorisation tests matter as much as the arithmetic. The contract payload carries
monthly hours, the accrual ceiling and the overage hour rate -- commercial terms, and from
the pricing phase on, prices.
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
    ServiceContract,
    ServiceContractPeriod,
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
from plane.utils.service_pool import close_period, resolve_period

pytestmark = pytest.mark.contract

CONTRACTS_URL = "/api/workspaces/{slug}/service-contracts/"
CONTRACT_URL = "/api/workspaces/{slug}/service-contracts/{pk}/"
CONTRACT_PERIODS_URL = "/api/workspaces/{slug}/service-contracts/{pk}/periods/"
CONTRACT_RENEW_URL = "/api/workspaces/{slug}/service-contracts/{pk}/renew/"
CONTRACT_SUCCESSOR_URL = "/api/workspaces/{slug}/service-contracts/{pk}/successor/"
PERIOD_URL = "/api/workspaces/{slug}/service-contract-periods/{pk}/"
PERIOD_CLOSE_URL = "/api/workspaces/{slug}/service-contract-periods/{pk}/close/"
PERIOD_HOURS_URL = "/api/workspaces/{slug}/service-contract-periods/{pk}/contracted-hours/"
PERIOD_DISMISS_URL = "/api/workspaces/{slug}/service-contract-periods/{pk}/dismiss-alert/"
ALERTS_URL = "/api/workspaces/{slug}/service-contract-alerts/"
POOL_URL = "/api/workspaces/{slug}/projects/{project_id}/issues/{issue_id}/service-pool/"
SERVICE_LOGS_URL = "/api/workspaces/{slug}/projects/{project_id}/issues/{issue_id}/service-logs/"
SERVICE_LOG_BATCH_URL = (
    "/api/workspaces/{slug}/projects/{project_id}/issues/{issue_id}/service-logs/batches/{batch_id}/"
)


@pytest.fixture(autouse=True)
def no_celery_dispatch(monkeypatch):
    monkeypatch.setattr("celery.app.task.Task.apply_async", lambda *args, **kwargs: None)


@pytest.fixture
def setup(db):
    """One workspace, an admin, a member, a guest, a client, a contract and a project.

    Every role is created here rather than per test so that an authorisation assertion and
    the arithmetic beside it run against the same fixture -- a 403 proven on a workspace
    where the resource does not exist proves nothing.
    """
    admin = UserFactory(email="admin-contract@plane.so")
    member = UserFactory(email="member-contract@plane.so")
    guest = UserFactory(email="guest-contract@plane.so")

    workspace = Workspace.objects.create(name="Contract WS", slug="contract-ws", owner=admin)

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
    double = ServiceHourTypeFactory(
        workspace=workspace, name="Domingos e feriados", multiplier=Decimal("2.00")
    )
    pool_route = ServiceBillingTypeFactory(
        workspace=workspace, name="Contrato", billing_route=ServiceBillingType.BillingRoute.DEBIT_POOL
    )
    warranty = ServiceBillingTypeFactory(
        workspace=workspace, name="Garantia", billing_route=ServiceBillingType.BillingRoute.NON_BILLABLE
    )

    assert hour_type.multiplier == Decimal("1.00")
    assert double.multiplier == Decimal("2.00")
    assert warranty.billing_route == ServiceBillingType.BillingRoute.NON_BILLABLE
    assert project.is_time_tracking_enabled is True

    return {
        "admin": admin,
        "member": member,
        "guest": guest,
        "workspace": workspace,
        "client": service_client,
        "contract": contract,
        "project": project,
        "hour_type": hour_type,
        "double": double,
        "pool_route": pool_route,
        "warranty": warranty,
    }


def client_for(user):
    api_client = APIClient()
    api_client.force_authenticate(user=user)
    return api_client


def post_log(api_client, setup, *, minutes, worked_on, hour_type=None, billing_type=None):
    issue = IssueFactory(project=setup["project"])
    response = api_client.post(
        SERVICE_LOGS_URL.format(
            slug=setup["workspace"].slug, project_id=setup["project"].id, issue_id=issue.id
        ),
        data=json.dumps(
            {
                "worked_on": str(worked_on),
                # Free text, per R1 -- the write serializer parses it. Expressed in
                # whole minutes so the R2 rounding is a no-op and the arithmetic these
                # tests assert on is the pool's, not the parser's.
                "duration": f"{minutes}min",
                "entry_mode": "duration",
                "description": "Atendimento",
                "hour_type_id": str((hour_type or setup["hour_type"]).id),
                "billing_type_id": str((billing_type or setup["pool_route"]).id),
            }
        ),
        content_type="application/json",
    )
    return issue, response


class TestContractCrud:
    def test_an_admin_creates_a_contract_and_its_periods(self, setup):
        """Criterion 1, through the API: the periods exist the day the contract is
        signed, not once somebody logs time."""
        api_client = client_for(setup["admin"])

        response = api_client.post(
            CONTRACTS_URL.format(slug=setup["workspace"].slug),
            data=json.dumps(
                {
                    "service_client": str(setup["client"].id),
                    "code": "INFRA-001",
                    "name": "Infraestrutura",
                    "monthly_hours": "20.0000",
                    "starts_on": "2026-01-01",
                    "ends_on": "2026-06-30",
                }
            ),
            content_type="application/json",
        )

        assert response.status_code == 201
        created = ServiceContract.objects.get(code="INFRA-001")
        assert ServiceContractPeriod.objects.filter(contract=created).count() == 6

    def test_an_incoherent_accrual_cap_is_refused_with_a_usable_code(self, setup):
        api_client = client_for(setup["admin"])

        response = api_client.post(
            CONTRACTS_URL.format(slug=setup["workspace"].slug),
            data=json.dumps(
                {
                    "service_client": str(setup["client"].id),
                    "code": "BAD-001",
                    "name": "Ruim",
                    "monthly_hours": "20.0000",
                    "starts_on": "2026-01-01",
                    "ends_on": "2026-06-30",
                    "accrual_cap_mode": "multiple",
                }
            ),
            content_type="application/json",
        )

        assert response.status_code == 400
        assert "ACCRUAL_CAP_MODE_REQUIRES_A_POSITIVE_VALUE" in json.dumps(response.json())

    def test_a_contract_with_ledger_entries_cannot_be_deleted(self, setup):
        """It can be ended or suspended. The ledger is what an invoice was built from."""
        api_client = client_for(setup["admin"])
        resolve_period(setup["contract"], date(2026, 1, 1), actor=setup["admin"])

        response = api_client.delete(
            CONTRACT_URL.format(slug=setup["workspace"].slug, pk=setup["contract"].id)
        )

        assert response.status_code == 400
        assert response.json()["error"] == "CONTRACT_HAS_LEDGER_ENTRIES"
        assert ServiceContract.objects.filter(pk=setup["contract"].id).exists()

    def test_a_contract_with_no_movement_can_be_deleted(self, setup):
        """The positive control: without it, a guard that refused every delete would pass
        the test above."""
        api_client = client_for(setup["admin"])
        spare = ServiceContractFactory(service_client=setup["client"], code="SPARE-1")

        response = api_client.delete(
            CONTRACT_URL.format(slug=setup["workspace"].slug, pk=spare.id)
        )

        assert response.status_code == 204


class TestContractAuthorisation:
    def test_a_guest_cannot_list_contracts(self, setup):
        response = client_for(setup["guest"]).get(
            CONTRACTS_URL.format(slug=setup["workspace"].slug)
        )

        assert response.status_code == 403

    def test_a_member_can_read_but_not_write(self, setup):
        """A technician needs to see how much of the pool is left (section 7). Changing
        the monthly hours is a commercial decision."""
        api_client = client_for(setup["member"])
        slug = setup["workspace"].slug

        assert api_client.get(CONTRACTS_URL.format(slug=slug)).status_code == 200

        write = api_client.patch(
            CONTRACT_URL.format(slug=slug, pk=setup["contract"].id),
            data=json.dumps({"monthly_hours": "999.0000"}),
            content_type="application/json",
        )

        assert write.status_code == 403
        setup["contract"].refresh_from_db()
        assert setup["contract"].monthly_hours == Decimal("30.0000")

    def test_a_guest_cannot_read_the_alert_panel(self, setup):
        response = client_for(setup["guest"]).get(ALERTS_URL.format(slug=setup["workspace"].slug))

        assert response.status_code == 403

    def test_a_guest_cannot_read_a_work_items_pool(self, setup):
        issue = IssueFactory(project=setup["project"])

        response = client_for(setup["guest"]).get(
            POOL_URL.format(
                slug=setup["workspace"].slug, project_id=setup["project"].id, issue_id=issue.id
            )
        )

        assert response.status_code == 403


class TestDebitThroughTheApi:
    def test_a_two_hour_log_leaves_twenty_eight(self, setup):
        """Criterion 2, wired end to end. The domain layer having the arithmetic is not
        the same as the endpoint calling it."""
        api_client = client_for(setup["member"])

        _issue, response = post_log(api_client, setup, minutes=120, worked_on=date(2026, 1, 15))

        assert response.status_code == 201
        period = resolve_period(setup["contract"], date(2026, 1, 15))
        assert period.consumed_hours == Decimal("2.0000")
        assert response.json()["pool"]["period"]["balance_hours"] == "28.0000"

    def test_a_double_multiplier_takes_four_hours(self, setup):
        """Criterion 3."""
        api_client = client_for(setup["member"])

        _issue, response = post_log(
            api_client, setup, minutes=120, worked_on=date(2026, 1, 15), hour_type=setup["double"]
        )

        assert response.status_code == 201
        assert resolve_period(setup["contract"], date(2026, 1, 15)).consumed_hours == Decimal("4.0000")

    def test_a_non_billable_log_does_not_move_the_pool(self, setup):
        """Criterion 4, with its reason and a positive control in the same test."""
        api_client = client_for(setup["member"])

        _issue, response = post_log(
            api_client,
            setup,
            minutes=120,
            worked_on=date(2026, 1, 15),
            billing_type=setup["warranty"],
        )

        assert response.status_code == 201
        assert response.json()["service_logs"][0]["debited_hours"] == "0.0000"
        assert response.json()["service_logs"][0]["applied_billing_route"] == "non_billable"

        period = resolve_period(setup["contract"], date(2026, 1, 15))
        assert period.consumed_hours == Decimal("0.0000")

        _issue2, billable = post_log(api_client, setup, minutes=120, worked_on=date(2026, 1, 16))
        assert billable.status_code == 201
        period.refresh_from_db()
        assert period.consumed_hours == Decimal("2.0000"), "positive control"

    def test_editing_a_log_from_two_to_three_hours_leaves_no_double_debit(self, setup):
        """Criterion 12, through the batch edit endpoint -- which is where a double debit
        would actually happen, because an edit is a delete plus a create."""
        api_client = client_for(setup["member"])
        issue, response = post_log(api_client, setup, minutes=120, worked_on=date(2026, 1, 15))
        batch_id = response.json()["batch_id"]

        period = resolve_period(setup["contract"], date(2026, 1, 15))
        assert period.consumed_hours == Decimal("2.0000")

        edit = api_client.patch(
            SERVICE_LOG_BATCH_URL.format(
                slug=setup["workspace"].slug,
                project_id=setup["project"].id,
                issue_id=issue.id,
                batch_id=batch_id,
            ),
            data=json.dumps(
                {
                    "worked_on": "2026-01-15",
                    "duration": "180min",
                    "entry_mode": "duration",
                    "description": "Atendimento maior",
                    "hour_type_id": str(setup["hour_type"].id),
                    "billing_type_id": str(setup["pool_route"].id),
                }
            ),
            content_type="application/json",
        )

        assert edit.status_code == 200
        period.refresh_from_db()
        assert period.consumed_hours == Decimal("3.0000"), "3h, not 5h and not 2h"

    def test_deleting_a_log_returns_its_hours(self, setup):
        """Criterion 11, through the batch delete endpoint."""
        api_client = client_for(setup["member"])
        issue, response = post_log(api_client, setup, minutes=180, worked_on=date(2026, 1, 15))
        batch_id = response.json()["batch_id"]

        deleted = api_client.delete(
            SERVICE_LOG_BATCH_URL.format(
                slug=setup["workspace"].slug,
                project_id=setup["project"].id,
                issue_id=issue.id,
                batch_id=batch_id,
            )
        )

        assert deleted.status_code == 200
        assert resolve_period(setup["contract"], date(2026, 1, 15)).consumed_hours == Decimal("0.0000")

    def test_a_closed_period_rejects_a_new_log_and_rolls_the_rows_back(self, setup):
        """CRITERION 13, and the rollback is half the criterion.

        A refusal that left the work log rows behind would be worse than no refusal at
        all: the work item would show hours that no pool accounted for, and the totals on
        the panel would disagree with the contract forever.
        """
        period = resolve_period(setup["contract"], date(2026, 1, 1), actor=setup["admin"])
        close_period(period, actor=setup["admin"])

        api_client = client_for(setup["member"])
        issue, response = post_log(api_client, setup, minutes=120, worked_on=date(2026, 1, 20))

        assert response.status_code == 400
        assert response.json()["error"] == "PERIOD_IS_CLOSED"
        assert response.json()["detail"]["competence"] == "2026-01"
        assert not ServiceLog.all_objects.filter(issue_id=issue.id).exists(), "the rows rolled back"

    def test_a_log_in_an_open_month_still_works_after_another_is_closed(self, setup):
        """The positive control for criterion 13: closing January must not block February."""
        close_period(
            resolve_period(setup["contract"], date(2026, 1, 1), actor=setup["admin"]),
            actor=setup["admin"],
        )

        api_client = client_for(setup["member"])
        _issue, response = post_log(api_client, setup, minutes=120, worked_on=date(2026, 2, 10))

        assert response.status_code == 201
        assert resolve_period(setup["contract"], date(2026, 2, 1)).consumed_hours == Decimal("2.0000")


class TestPeriodClose:
    def test_closing_with_a_carried_deficit(self, setup):
        """Criterion 8, through the endpoint."""
        api_client = client_for(setup["admin"])
        post_log(client_for(setup["member"]), setup, minutes=33 * 60, worked_on=date(2026, 1, 15))
        period = resolve_period(setup["contract"], date(2026, 1, 1))

        response = api_client.post(
            PERIOD_CLOSE_URL.format(slug=setup["workspace"].slug, pk=period.id),
            data=json.dumps({"overage_settlement": "carried"}),
            content_type="application/json",
        )

        assert response.status_code == 200
        assert response.json()["reconciliation"]["is_consistent"] is True
        february = resolve_period(setup["contract"], date(2026, 2, 1))
        assert february.granted_hours == Decimal("27.0000")

    def test_closing_by_billing_the_overage(self, setup):
        """Criterion 9, through the endpoint."""
        api_client = client_for(setup["admin"])
        post_log(client_for(setup["member"]), setup, minutes=33 * 60, worked_on=date(2026, 1, 15))
        period = resolve_period(setup["contract"], date(2026, 1, 1))

        response = api_client.post(
            PERIOD_CLOSE_URL.format(slug=setup["workspace"].slug, pk=period.id),
            data=json.dumps({"overage_settlement": "billed"}),
            content_type="application/json",
        )

        assert response.status_code == 200
        assert response.json()["period"]["overage_hours"] == "3.0000"
        february = resolve_period(setup["contract"], date(2026, 2, 1))
        assert february.granted_hours == Decimal("30.0000"), "the next month is whole"

    def test_a_member_cannot_close_a_period(self, setup):
        """Closing locks an invoiced month. That is an admin decision."""
        period = resolve_period(setup["contract"], date(2026, 1, 1), actor=setup["admin"])

        response = client_for(setup["member"]).post(
            PERIOD_CLOSE_URL.format(slug=setup["workspace"].slug, pk=period.id),
            data=json.dumps({}),
            content_type="application/json",
        )

        assert response.status_code == 403
        assert ServiceContractPeriod.objects.get(pk=period.id).status == "open"

    def test_an_admin_can_correct_the_contracted_hours_of_an_open_month(self, setup):
        """Decision B1, through the endpoint."""
        api_client = client_for(setup["admin"])
        period = resolve_period(setup["contract"], date(2026, 1, 1), actor=setup["admin"])

        response = api_client.post(
            PERIOD_HOURS_URL.format(slug=setup["workspace"].slug, pk=period.id),
            data=json.dumps({"contracted_hours": "15.0000"}),
            content_type="application/json",
        )

        assert response.status_code == 200
        assert response.json()["contracted_hours"] == "15.0000"
        assert response.json()["balance_hours"] == "15.0000"

    def test_correcting_a_closed_month_is_refused(self, setup):
        api_client = client_for(setup["admin"])
        period = resolve_period(setup["contract"], date(2026, 1, 1), actor=setup["admin"])
        close_period(period, actor=setup["admin"])

        response = api_client.post(
            PERIOD_HOURS_URL.format(slug=setup["workspace"].slug, pk=period.id),
            data=json.dumps({"contracted_hours": "15.0000"}),
            content_type="application/json",
        )

        assert response.status_code == 400
        assert response.json()["error"] == "PERIOD_IS_CLOSED_FOR_CONTRACTED_HOURS"

    def test_the_period_statement_shows_the_ledger_and_reconciles(self, setup):
        api_client = client_for(setup["member"])
        post_log(api_client, setup, minutes=120, worked_on=date(2026, 1, 15))
        period = resolve_period(setup["contract"], date(2026, 1, 15))

        response = api_client.get(
            PERIOD_URL.format(slug=setup["workspace"].slug, pk=period.id)
        )

        assert response.status_code == 200
        body = response.json()
        entry_types = {entry["entry_type"] for entry in body["ledger"]}
        assert {"grant", "debit"} <= entry_types
        assert body["reconciliation"]["is_consistent"] is True


class TestAlertPanel:
    def test_high_consumption_before_midmonth_appears(self, setup):
        """Criterion 18, through the panel endpoint.

        ``reference_date`` is passed explicitly rather than relying on today's date. The
        alert is defined as "above the threshold *before mid-month*", so a test that used
        the real clock would pass or fail depending on the day it ran -- and would go red
        every month after the 15th for no reason at all.
        """
        api_client = client_for(setup["member"])
        post_log(api_client, setup, minutes=27 * 60, worked_on=date(2026, 1, 5))

        response = api_client.get(
            ALERTS_URL.format(slug=setup["workspace"].slug)
            + f"?contract_id={setup['contract'].id}&reference_date=2026-01-10"
        )

        assert response.status_code == 200
        entries = response.json()["entries"]
        assert entries, "the contract shows up on the panel"
        codes = {alert["code"] for entry in entries for alert in entry["alerts"]}
        assert "HIGH_CONSUMPTION_BEFORE_MIDMONTH" in codes
        assert response.json()["high_consumption_count"] == 1

    def test_the_same_consumption_is_not_flagged_early_as_high_after_midmonth(self, setup):
        """Absence with its reason, and a positive control in the same call.

        90% consumption on the 25th is not the criterion-18 alert -- that alert exists
        because it is *early*, while there is still time to act. Something else fires, so
        the panel is demonstrably running.
        """
        api_client = client_for(setup["member"])
        post_log(api_client, setup, minutes=27 * 60, worked_on=date(2026, 1, 5))

        response = api_client.get(
            ALERTS_URL.format(slug=setup["workspace"].slug)
            + f"?contract_id={setup['contract'].id}&reference_date=2026-01-25"
        )

        codes = {
            alert["code"] for entry in response.json()["entries"] for alert in entry["alerts"]
        }

        assert "HIGH_CONSUMPTION_BEFORE_MIDMONTH" not in codes
        assert codes, "positive control: the panel produced other alerts"

    def test_a_contract_with_no_logs_appears_in_low_consumption(self, setup):
        """Criterion 19."""
        resolve_period(setup["contract"], date(2026, 1, 1), actor=setup["admin"])

        response = client_for(setup["member"]).get(
            ALERTS_URL.format(slug=setup["workspace"].slug)
        )

        assert response.status_code == 200
        body = response.json()
        codes = {
            alert["code"] for entry in body["entries"] for alert in entry["alerts"]
        }
        assert "NO_SERVICE_LOGS_IN_MONTH" in codes
        assert body["low_consumption_count"] >= 1

    def test_dismissing_an_alert_removes_it_from_the_panel(self, setup):
        api_client = client_for(setup["member"])
        post_log(api_client, setup, minutes=33 * 60, worked_on=date(2026, 1, 15))
        period = resolve_period(setup["contract"], date(2026, 1, 15))

        before = api_client.get(PERIOD_URL.format(slug=setup["workspace"].slug, pk=period.id))
        assert "NEGATIVE_BALANCE" in {a["code"] for a in before.json()["alerts"]}

        response = api_client.post(
            PERIOD_DISMISS_URL.format(slug=setup["workspace"].slug, pk=period.id),
            data=json.dumps({"alert_code": "NEGATIVE_BALANCE"}),
            content_type="application/json",
        )

        assert response.status_code == 200
        assert "NEGATIVE_BALANCE" not in {a["code"] for a in response.json()["alerts"]}
        assert response.json()["balance_at_dismissal"] == "-3.0000"


class TestPoolPanel:
    def test_the_work_item_shows_the_pool_it_debits(self, setup):
        """Section 7."""
        api_client = client_for(setup["member"])
        issue, _response = post_log(api_client, setup, minutes=120, worked_on=date(2026, 1, 15))

        response = api_client.get(
            POOL_URL.format(
                slug=setup["workspace"].slug, project_id=setup["project"].id, issue_id=issue.id
            )
            + "?worked_on=2026-01-15"
        )

        assert response.status_code == 200
        body = response.json()
        assert body["contract_code"] == "SUP-001"
        assert body["period"]["consumed_hours"] == "2.0000"
        assert body["period"]["balance_hours"] == "28.0000"

    def test_an_unresolvable_contract_is_reported_as_a_code_not_an_empty_panel(self, setup):
        """A panel that simply showed nothing would be indistinguishable from a client
        with no consumption -- and those need opposite actions."""
        internal = ProjectFactory(name="Interno", workspace=setup["workspace"], service_client=None)
        ProjectMember.objects.create(
            project=internal, member=setup["member"], role=15, workspace=setup["workspace"]
        )
        issue = IssueFactory(project=internal)

        response = client_for(setup["member"]).get(
            POOL_URL.format(
                slug=setup["workspace"].slug, project_id=internal.id, issue_id=issue.id
            )
        )

        assert response.status_code == 200
        assert response.json()["error"] == "NO_SERVICE_CLIENT_FOR_PROJECT"


class TestRenewalThroughTheApi:
    def test_renewing_in_place_extends_the_vigency(self, setup):
        """Criterion 16."""
        api_client = client_for(setup["admin"])

        response = api_client.post(
            CONTRACT_RENEW_URL.format(slug=setup["workspace"].slug, pk=setup["contract"].id),
            data=json.dumps(
                {"mode": "in_place", "ends_on": "2027-12-31", "monthly_hours": "20.0000"}
            ),
            content_type="application/json",
        )

        assert response.status_code == 200
        setup["contract"].refresh_from_db()
        assert setup["contract"].ends_on == date(2027, 12, 31)
        assert setup["contract"].monthly_hours == Decimal("20.0000")

    def test_creating_a_successor_transfers_the_balance(self, setup):
        """Criterion 17, the transfer path."""
        api_client = client_for(setup["admin"])
        post_log(client_for(setup["member"]), setup, minutes=10 * 60, worked_on=date(2026, 1, 15))

        response = api_client.post(
            CONTRACT_SUCCESSOR_URL.format(slug=setup["workspace"].slug, pk=setup["contract"].id),
            data=json.dumps(
                {
                    "code": "SUP-002",
                    "name": "Suporte 2027",
                    "monthly_hours": "20.0000",
                    "starts_on": "2027-01-01",
                    "ends_on": "2027-12-31",
                    "balance_destination": "transfer",
                }
            ),
            content_type="application/json",
        )

        assert response.status_code == 201
        successor = ServiceContract.objects.get(code="SUP-002")
        assert successor.previous_contract_id == setup["contract"].id
        first = resolve_period(successor, date(2027, 1, 1))
        assert first.carried_hours == Decimal("20.0000")

    def test_converting_to_a_work_item_allowance_returns_not_implemented(self, setup):
        """Criterion 17's third destination is Phase 5's.

        501 rather than 400: the request is well formed and will be valid once the
        allowance entity exists. Tracked as an inherited criterion.
        """
        api_client = client_for(setup["admin"])

        response = api_client.post(
            CONTRACT_SUCCESSOR_URL.format(slug=setup["workspace"].slug, pk=setup["contract"].id),
            data=json.dumps(
                {
                    "code": "SUP-003",
                    "name": "Suporte",
                    "monthly_hours": "20.0000",
                    "starts_on": "2027-01-01",
                    "ends_on": "2027-12-31",
                    "balance_destination": "issue_allowance",
                }
            ),
            content_type="application/json",
        )

        assert response.status_code == 501
        assert response.json()["error"] == "ISSUE_ALLOWANCE_NOT_AVAILABLE"


class TestWorkspaceIsolation:
    def test_a_contract_of_another_workspace_is_not_visible(self, setup):
        """The isolation test D19 says must be repeated for every new entity, because
        these are our own code and do not inherit Plane's protection automatically."""
        other_owner = UserFactory(email="other-owner@plane.so")
        other_workspace = Workspace.objects.create(
            name="Other", slug="other-contract-ws", owner=other_owner
        )
        other_client = ServiceClientFactory(name="Outra", workspace=other_workspace)
        foreign = ServiceContractFactory(service_client=other_client, code="FOREIGN-1")

        response = client_for(setup["admin"]).get(
            CONTRACTS_URL.format(slug=setup["workspace"].slug)
        )

        assert response.status_code == 200
        codes = {row["code"] for row in response.json()}
        assert "SUP-001" in codes, "positive control: our own contract is listed"
        assert "FOREIGN-1" not in codes

        direct = client_for(setup["admin"]).get(
            CONTRACT_URL.format(slug=setup["workspace"].slug, pk=foreign.id)
        )
        assert direct.status_code == 404
