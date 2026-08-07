# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""The five report endpoints, over HTTP. Criteria 8, 9, 10 and 14.

The unit tests prove the aggregation. These prove the **boundary**: who reaches which route,
what shape each role receives, and that the drill-down really does replay a bucket's own
descriptor across the wire -- which is where criterion 8 is either true or a claim.

Every "the money is absent" assertion has an Admin control in the same fixture, per the
conventions: a payload that returned nothing at all would satisfy the negative half.
"""

# Python imports
import json
from datetime import date
from decimal import Decimal

# Third party imports
import pytest
from rest_framework.test import APIClient

# Module imports
from plane.app.serializers import ServiceLogSerializer
from plane.db.models import ServiceBillingType, ServiceRateBasis
from plane.tests.factories import (
    IssueFactory,
    ProjectFactory,
    ProjectMemberFactory,
    ServiceBillingTypeFactory,
    ServiceClientFactory,
    ServiceContractFactory,
    ServiceHourTypeFactory,
    ServiceLogFactory,
    UserFactory,
    WorkspaceMemberFactory,
)
from plane.utils.service_pool import resolve_period

pytestmark = pytest.mark.contract

_POOL = ServiceBillingType.BillingRoute.DEBIT_POOL
_BILL = ServiceBillingType.BillingRoute.BILL_AMOUNT
_FREE = ServiceBillingType.BillingRoute.NON_BILLABLE

CONSUMPTION = "/api/workspaces/{slug}/service-reports/consumption/"
OPERATIONAL = "/api/workspaces/{slug}/service-reports/operational/"
ATTENTION = "/api/workspaces/{slug}/service-reports/attention/"
BILLING = "/api/workspaces/{slug}/service-reports/billing/"
LOGS = "/api/workspaces/{slug}/service-reports/logs/"


@pytest.fixture(autouse=True)
def no_celery_dispatch(monkeypatch):
    monkeypatch.setattr("celery.app.task.Task.apply_async", lambda *args, **kwargs: None)


@pytest.fixture
def setup(db):
    admin = UserFactory()
    member = UserFactory()

    service_client = ServiceClientFactory(name="Terlogs")
    workspace = service_client.workspace

    WorkspaceMemberFactory(workspace=workspace, member=admin, role=20)
    WorkspaceMemberFactory(workspace=workspace, member=member, role=15)

    contract = ServiceContractFactory(
        service_client=service_client,
        code="TER-1",
        monthly_hours=Decimal("40.0000"),
        starts_on=date(2026, 3, 1),
        ends_on=date(2026, 12, 31),
    )

    project = ProjectFactory(
        name="Suporte", workspace=workspace, service_client=service_client
    )
    ProjectMemberFactory(project=project, member=admin, role=20)
    ProjectMemberFactory(project=project, member=member, role=15)

    commercial = ServiceHourTypeFactory(
        workspace=workspace, name="Comercial", multiplier=Decimal("1.00")
    )
    pool_type = ServiceBillingTypeFactory(workspace=workspace, name="Contrato", billing_route=_POOL)
    adhoc = ServiceBillingTypeFactory(workspace=workspace, name="Avulso", billing_route=_BILL)
    warranty = ServiceBillingTypeFactory(workspace=workspace, name="Garantia", billing_route=_FREE)

    march = resolve_period(contract, date(2026, 3, 10), actor=admin)

    pool_log = ServiceLogFactory(
        project=project,
        issue=IssueFactory(project=project),
        author=member,
        worked_on=date(2026, 3, 5),
        hour_type=commercial,
        billing_type=pool_type,
        logged_hours=Decimal("3.0000"),
        equivalent_hours=Decimal("3.0000"),
        debited_hours=Decimal("3.0000"),
        applied_billing_route=_POOL,
        settled_billing_route=_POOL,
        debited_period=march,
    )

    billed_log = ServiceLogFactory(
        project=project,
        issue=IssueFactory(project=project),
        author=member,
        worked_on=date(2026, 3, 11),
        hour_type=commercial,
        billing_type=adhoc,
        logged_hours=Decimal("1.0000"),
        equivalent_hours=Decimal("1.0000"),
        debited_hours=Decimal("1.0000"),
        applied_billing_route=_BILL,
        settled_billing_route=_BILL,
        applied_hour_rate=Decimal("200.00"),
        applied_rate_basis=ServiceRateBasis.BASE_MULTIPLIER,
        amount=Decimal("200.00"),
    )

    free_log = ServiceLogFactory(
        project=project,
        issue=IssueFactory(project=project),
        author=member,
        worked_on=date(2026, 3, 12),
        hour_type=commercial,
        billing_type=warranty,
        logged_hours=Decimal("2.0000"),
        equivalent_hours=Decimal("2.0000"),
        debited_hours=Decimal("0.0000"),
        applied_billing_route=_FREE,
        settled_billing_route=_FREE,
    )

    return {
        "workspace": workspace,
        "slug": workspace.slug,
        "admin": admin,
        "member": member,
        "client": service_client,
        "contract": contract,
        "march": march,
        "project": project,
        "logs": {"pool": pool_log, "billed": billed_log, "free": free_log},
    }


def client_for(user):
    api = APIClient()
    api.force_authenticate(user=user)
    return api


MARCH = {"competence_from": "2026-03", "competence_to": "2026-03"}


class TestRoleGating:
    """Criterion 10, at the boundary. The role decides the **shape**, not the route -- except
    for billing, where every number is money."""

    def test_a_member_reaches_consumption_operational_and_attention(self, setup):
        api = client_for(setup["member"])

        for url in (CONSUMPTION, OPERATIONAL, ATTENTION, LOGS):
            response = api.get(url.format(slug=setup["slug"]), MARCH)
            assert response.status_code == 200, f"{url} refused a MEMBER"

    def test_a_member_is_refused_the_billing_report(self, setup):
        """The one ADMIN-only route. Stripping the amounts from "the month's revenue by
        client" leaves a list of clients, so there is no Member shape to serve."""
        response = client_for(setup["member"]).get(BILLING.format(slug=setup["slug"]), MARCH)

        assert response.status_code == 403

    def test_an_admin_reaches_it(self, setup):
        response = client_for(setup["admin"]).get(BILLING.format(slug=setup["slug"]), MARCH)

        assert response.status_code == 200
        assert response.json()["consolidation"]["competence"] == "2026-03"

    def test_a_non_member_of_the_workspace_reaches_nothing(self, setup):
        outsider = UserFactory()
        api = client_for(outsider)

        for url in (CONSUMPTION, OPERATIONAL, ATTENTION, BILLING, LOGS):
            response = api.get(url.format(slug=setup["slug"]), MARCH)
            assert response.status_code in (401, 403), f"{url} leaked to an outsider"


class TestTheMemberShapeCarriesNoMoney:
    """D50 across the wire: the money is never computed for a Member, so no key exists."""

    def _money_keys(self, payload, found=None):
        found = [] if found is None else found

        if isinstance(payload, dict):
            for key, value in payload.items():
                if key in ("amount", "amount_display", "total_amount", "average_amount_per_issue"):
                    found.append(key)
                self._money_keys(value, found)
        elif isinstance(payload, list):
            for item in payload:
                self._money_keys(item, found)

        return found

    def test_no_monetary_key_appears_anywhere_for_a_member(self, setup):
        api = client_for(setup["member"])

        for url in (CONSUMPTION, OPERATIONAL):
            payload = api.get(url.format(slug=setup["slug"]), MARCH).json()

            assert self._money_keys(payload) == [], f"money leaked to a MEMBER on {url}"

    def test_the_admin_control_in_the_same_fixture(self, setup):
        """Without this the test above would pass on an endpoint that returned ``{}``."""
        payload = client_for(setup["admin"]).get(
            CONSUMPTION.format(slug=setup["slug"]), MARCH
        ).json()

        assert self._money_keys(payload) != []
        assert payload["totals"]["amount"] == "200.00"

    def test_a_member_still_gets_every_hour_figure(self, setup):
        totals = client_for(setup["member"]).get(
            OPERATIONAL.format(slug=setup["slug"]), MARCH
        ).json()["totals"]

        assert totals["logged_hours"] == "6.0000"
        assert totals["equivalent_hours"] == "6.0000"
        assert totals["debited_hours"] == "4.0000", "the warranty row debits nothing"

    def test_a_member_gets_the_revenue_series_key_at_all(self, setup):
        """It is absent, not empty. An empty series would be indistinguishable from a month
        with no revenue, and a Member would have no way to know they were not being shown it."""
        payload = client_for(setup["member"]).get(
            CONSUMPTION.format(slug=setup["slug"]), MARCH
        ).json()

        assert "revenue_series" not in payload

        admin_payload = client_for(setup["admin"]).get(
            CONSUMPTION.format(slug=setup["slug"]), MARCH
        ).json()
        assert "revenue_series" in admin_payload, "positive control"


class TestTheDrillDownReplaysTheBucket:
    """**Criterion 8 across the wire.** Take a bucket's descriptor out of one response, send it
    to the drill-down, and the rows must sum to the bucket."""

    def test_a_series_bucket_descriptor_returns_exactly_its_own_rows(self, setup):
        api = client_for(setup["admin"])

        series = api.get(
            OPERATIONAL.format(slug=setup["slug"]),
            {"competence_from": "2026-01", "competence_to": "2026-12"},
        ).json()["series"]

        march = next(entry for entry in series if entry["competence"] == "2026-03")

        drilled = api.get(LOGS.format(slug=setup["slug"]), march["filters"]).json()

        assert drilled["total_results"] == march["entries"] == 3
        assert drilled["extra_stats"]["totals"]["equivalent_hours"] == march["equivalent_hours"]

    def test_a_distribution_slice_descriptor_returns_only_that_slice(self, setup):
        api = client_for(setup["admin"])

        distributions = api.get(OPERATIONAL.format(slug=setup["slug"]), MARCH).json()[
            "distributions"
        ]
        warranty = next(
            row
            for row in distributions["hour_type"]
            if row["hour_type_name"] == "Comercial"
        )

        drilled = api.get(LOGS.format(slug=setup["slug"]), warranty["filters"]).json()

        assert drilled["total_results"] == warranty["entries"]

    def test_the_non_billable_breakdown_drills_to_the_warranty_row_only(self, setup):
        """R5 and criterion 2 together: the slice is per billing type, and clicking it reaches
        exactly the rows of that type."""
        api = client_for(setup["admin"])

        rows = api.get(CONSUMPTION.format(slug=setup["slug"]), MARCH).json()["non_billable"]

        assert [row["billing_type_name"] for row in rows] == ["Garantia"]

        drilled = api.get(LOGS.format(slug=setup["slug"]), rows[0]["filters"]).json()

        assert drilled["total_results"] == 1
        assert drilled["results"][0]["id"] == str(setup["logs"]["free"].id)

    def test_a_revenue_origin_bucket_drills_to_the_billed_row(self, setup):
        api = client_for(setup["admin"])

        series = api.get(BILLING.format(slug=setup["slug"]), MARCH).json()["revenue_series"]
        origins = series[0]["origins"]

        # The client holds a contract covering March, so an ad-hoc log with no deviation is
        # out of scope rather than standalone.
        out_of_scope = origins["out_of_scope_log"]
        assert out_of_scope["amount"] == "200.00"

        drilled = api.get(LOGS.format(slug=setup["slug"]), out_of_scope["filters"]).json()

        assert drilled["total_results"] == 1
        assert drilled["results"][0]["id"] == str(setup["logs"]["billed"].id)
        assert drilled["extra_stats"]["totals"]["amount"] == "200.00"

    def test_the_overage_origins_offer_no_descriptor_over_the_wire_either(self, setup):
        """D56 survives serialisation. An absent key cannot be followed."""
        series = client_for(setup["admin"]).get(
            BILLING.format(slug=setup["slug"]), MARCH
        ).json()["revenue_series"]
        origins = series[0]["origins"]

        for origin in ("contract_overage", "allowance_overage"):
            assert "filters" not in origins[origin]
            assert origins[origin]["drill_down"]["competence"] == "2026-03"


class TestTheDrillDownRowsRespectR11:
    def test_a_member_gets_rows_without_money_but_with_commercial_state(self, setup):
        """D57 at the boundary. The technician needs to see that a row was billed ad-hoc; they
        do not need to see what it was worth."""
        rows = client_for(setup["member"]).get(
            LOGS.format(slug=setup["slug"]), MARCH
        ).json()["results"]

        for row in rows:
            for field in ServiceLogSerializer.MONEY_FIELDS:
                assert field not in row, f"{field} leaked to a MEMBER in the drill-down"
            for field in ServiceLogSerializer.COMMERCIAL_STATE_FIELDS:
                assert field in row, f"{field} is commercial state and a MEMBER needs it"

    def test_an_admin_gets_the_money_in_the_rows(self, setup):
        rows = client_for(setup["admin"]).get(
            LOGS.format(slug=setup["slug"]), MARCH
        ).json()["results"]

        billed = next(row for row in rows if row["id"] == str(setup["logs"]["billed"].id))

        assert billed["amount"] == "200.00"
        assert billed["amount_display"] == "R$ 200,00"


class TestD47OverHttp:
    def test_the_consumption_series_uses_the_period_competency(self, setup):
        """Criterion 7 at the boundary: the contract shape switches the basis itself, so a
        frontend cannot get it wrong by sending the wrong parameter."""
        payload = client_for(setup["admin"]).get(
            CONSUMPTION.format(slug=setup["slug"]),
            {
                "service_client_ids": str(setup["client"].id),
                "competence_from": "2026-01",
                "competence_to": "2026-12",
            },
        ).json()

        assert payload["shape"] == "contract"

        for entry in payload["consumption_series"]:
            assert entry["filters"]["competence_basis"] == "debited_period", (
                "a contract consumption bucket must drill down on the period basis, or a "
                "retroactive log would be missing from the list"
            )

    def test_a_client_without_a_contract_gets_the_standalone_shape(self, setup):
        bare = ServiceClientFactory(name="Marubeni", workspace=setup["workspace"])

        payload = client_for(setup["admin"]).get(
            CONSUMPTION.format(slug=setup["slug"]),
            {"service_client_ids": str(bare.id), **MARCH},
        ).json()

        assert payload["shape"] == "standalone"
        assert "contracts" not in payload


class TestFilterErrorsAreNamed:
    @pytest.mark.parametrize(
        "params,code",
        [
            ({"competence_from": "banana"}, "INVALID_COMPETENCE"),
            ({"competence_from": "2026-13"}, "INVALID_COMPETENCE"),
            ({"project_ids": "not-a-uuid"}, "INVALID_UUID_FILTER"),
            ({"settled_routes": "invented"}, "INVALID_FILTER_VALUE"),
            (
                {"competence_from": "2026-06", "competence_to": "2026-03"},
                "COMPETENCE_RANGE_IS_INVERTED",
            ),
        ],
    )
    def test_a_bad_filter_is_a_400_with_a_code(self, setup, params, code):
        """A 400 the frontend can translate, never a 500. Same shape as every other error in
        this feature."""
        response = client_for(setup["admin"]).get(OPERATIONAL.format(slug=setup["slug"]), params)

        assert response.status_code == 400
        assert response.json()["error"] == code

    def test_the_billing_report_requires_a_competency(self, setup):
        """"Everything ever billed" is not a document anybody issues, and defaulting to the
        current month would silently answer a different question than the one asked."""
        response = client_for(setup["admin"]).get(BILLING.format(slug=setup["slug"]))

        assert response.status_code == 400
        assert response.json()["error"] == "COMPETENCE_IS_REQUIRED"


class TestTheAllowancePanels:
    def test_an_active_allowance_carries_its_credit_history(self, setup):
        """The screen section 3b had no consumer for. The API already returned ``credits``."""
        from plane.utils.service_allowance import credit_allowance

        issue = IssueFactory(project=setup["project"])
        credit_allowance(issue, Decimal("10.0000"), actor=setup["admin"])

        payload = client_for(setup["admin"]).get(
            CONSUMPTION.format(slug=setup["slug"]),
            {"service_client_ids": str(setup["client"].id), **MARCH},
        ).json()

        active = payload["allowances"]["active"]

        assert len(active) == 1
        assert active[0]["credited_hours"] == "10.0000"
        assert len(active[0]["credits"]) == 1

        credit = active[0]["credits"][0]
        # The three facts the brief asks the history to carry: how many hours, who, and when.
        assert credit["hours"] == "10.0000"
        assert credit["actor_display_name"] == setup["admin"].display_name
        assert credit["created_at"] is not None
        # `origin_competence` is null here because these hours were credited directly rather
        # than converted from a contract's remaining balance. Asserted so the key's presence
        # is part of the contract even when it has nothing in it -- Phase 4's renewal path
        # fills it, and a panel that branched on the key's absence would break there.
        assert credit["origin_competence"] is None

    def test_an_allowance_past_its_grace_period_moves_out_of_the_active_list(self, setup):
        """The revised D35. It is a decision waiting, not an active pool -- and leaving it in
        "bolsas ativas" is what would make the panel fill with dead allowances."""
        from datetime import timedelta

        from django.utils import timezone

        from plane.db.models import Issue
        from plane.utils.service_allowance import credit_allowance

        issue = IssueFactory(project=setup["project"])
        credit_allowance(issue, Decimal("10.0000"), actor=setup["admin"])
        Issue.objects.filter(pk=issue.pk).update(completed_at=timezone.now() - timedelta(days=40))

        payload = client_for(setup["admin"]).get(
            CONSUMPTION.format(slug=setup["slug"]),
            {"service_client_ids": str(setup["client"].id), **MARCH},
        ).json()

        assert payload["allowances"]["active"] == []
        assert len(payload["allowances"]["pending_closure"]) == 1

    def test_a_member_sees_the_allowance_without_its_rate(self, setup):
        from plane.utils.service_allowance import credit_allowance

        issue = IssueFactory(project=setup["project"])
        allowance = credit_allowance(issue, Decimal("10.0000"), actor=setup["admin"])
        allowance.overage_hour_rate = Decimal("180.00")
        allowance.save(update_fields=["overage_hour_rate"])

        as_member = client_for(setup["member"]).get(
            CONSUMPTION.format(slug=setup["slug"]),
            {"service_client_ids": str(setup["client"].id), **MARCH},
        ).json()["allowances"]["active"][0]

        as_admin = client_for(setup["admin"]).get(
            CONSUMPTION.format(slug=setup["slug"]),
            {"service_client_ids": str(setup["client"].id), **MARCH},
        ).json()["allowances"]["active"][0]

        assert "overage_hour_rate" not in as_member
        assert as_admin["overage_hour_rate"] == "180.00"
        assert as_member["credited_hours"] == "10.0000", "the hours still reach them"


class TestTheAttentionPanelIsOneQuestion:
    def test_it_returns_the_three_lists_and_a_count(self, setup):
        payload = client_for(setup["member"]).get(ATTENTION.format(slug=setup["slug"])).json()

        assert set(payload) == {
            "periods",
            "allowances",
            "clients_without_a_default_contract",
            "count",
        }
        assert payload["count"] == (
            len(payload["periods"])
            + len(payload["allowances"])
            + len(payload["clients_without_a_default_contract"])
        )

    def test_a_pending_closure_allowance_appears_with_its_own_severity(self, setup):
        """The revised D35 reaching the panel, with ``pending_action`` rather than a
        consumption severity: it is an action item, not a reading."""
        from datetime import timedelta

        from django.utils import timezone

        from plane.db.models import Issue
        from plane.utils.service_allowance import credit_allowance

        issue = IssueFactory(project=setup["project"])
        credit_allowance(issue, Decimal("10.0000"), actor=setup["admin"])
        Issue.objects.filter(pk=issue.pk).update(completed_at=timezone.now() - timedelta(days=40))

        payload = client_for(setup["member"]).get(ATTENTION.format(slug=setup["slug"])).json()

        codes = {
            alert["code"]
            for entry in payload["allowances"]
            for alert in entry["alerts"]
        }
        severities = {
            alert["severity"]
            for entry in payload["allowances"]
            for alert in entry["alerts"]
        }

        assert "ALLOWANCE_PENDING_CLOSURE" in codes
        assert "pending_action" in severities
