# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""HTTP surface of price sheets, the value in reais, and the consolidation.

The acceptance criteria that are only true if they are true *through the API*: 6, 10, 11 and
the whole of R11.

**The R11 tests are the point of this file.** R11(b) says the restriction is a serializer's
job and not the interface's -- "esconder na interface e mandar no payload é vazamento" -- so
these assert the **absence of the keys** from a Member's payload, never that a screen does
not render them. A test that checked the value was zero, or that the UI hid it, would pass on
exactly the implementation R11 forbids.
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
    ExporterHistory,
    ProjectMember,
    ServiceBillingType,
    ServiceClientPrice,
    Workspace,
    WorkspaceMember,
)
from plane.app.serializers import ServiceIssueAllowanceSerializer, ServiceLogSerializer
from plane.tests.factories import (
    IssueFactory,
    ProjectFactory,
    ServiceBillingTypeFactory,
    ServiceClientFactory,
    ServiceContractFactory,
    ServiceHourTypeFactory,
    UserFactory,
)

pytestmark = pytest.mark.contract

PRICES_URL = "/api/workspaces/{slug}/service-clients/{client_id}/prices/"
PRICE_URL = "/api/workspaces/{slug}/service-clients/{client_id}/prices/{pk}/"
OVERRIDES_URL = "/api/workspaces/{slug}/service-client-prices/{price_id}/hour-type-rates/"
OVERRIDE_URL = "/api/workspaces/{slug}/service-client-prices/{price_id}/hour-type-rates/{pk}/"
EFFECTIVE_URL = "/api/workspaces/{slug}/service-clients/{client_id}/effective-rates/"
CONSOLIDATION_URL = "/api/workspaces/{slug}/service-billing-consolidation/"
EXPORT_URL = "/api/workspaces/{slug}/service-log-exports/"
SERVICE_LOGS_URL = "/api/workspaces/{slug}/projects/{project_id}/issues/{issue_id}/service-logs/"
ALLOWANCE_URL = "/api/workspaces/{slug}/projects/{project_id}/issues/{issue_id}/service-allowance/"
OVERAGE_RATE_URL = "/api/workspaces/{slug}/service-issue-allowances/{pk}/overage-rate/"
PERIOD_PREVIEW_URL = "/api/workspaces/{slug}/service-contract-periods/{pk}/overage-preview/"


@pytest.fixture(autouse=True)
def no_celery_dispatch(monkeypatch):
    monkeypatch.setattr("celery.app.task.Task.apply_async", lambda *args, **kwargs: None)


@pytest.fixture
def setup(db):
    """One workspace with all three roles, a client priced at R$ 200,00/h, and a project."""
    admin = UserFactory(email="admin-pricing@plane.so")
    member = UserFactory(email="member-pricing@plane.so")
    guest = UserFactory(email="guest-pricing@plane.so")

    workspace = Workspace.objects.create(name="Pricing WS", slug="pricing-ws", owner=admin)

    for user, role in ((admin, 20), (member, 15), (guest, 5)):
        WorkspaceMember.objects.create(workspace=workspace, member=user, role=role)

    service_client = ServiceClientFactory(name="Marubeni", workspace=workspace)
    project = ProjectFactory(name="Marubeni", workspace=workspace, service_client=service_client)
    project.is_time_tracking_enabled = True
    project.save()

    for user, role in ((admin, 20), (member, 15), (guest, 5)):
        ProjectMember.objects.create(
            workspace=workspace, project=project, member=user, role=role
        )

    sheet = ServiceClientPrice.objects.create(
        workspace=workspace,
        service_client=service_client,
        starts_on=date(2026, 1, 1),
        base_hour_rate=Decimal("200.00"),
    )

    return {
        "admin": admin,
        "member": member,
        "guest": guest,
        "workspace": workspace,
        "client": service_client,
        "project": project,
        "sheet": sheet,
        "hour_type": ServiceHourTypeFactory(
            workspace=workspace, name="Comercial", multiplier=Decimal("1.00")
        ),
        "sunday": ServiceHourTypeFactory(
            workspace=workspace, name="Domingos", multiplier=Decimal("2.00")
        ),
        "avulso": ServiceBillingTypeFactory(
            workspace=workspace,
            name="Avulso",
            billing_route=ServiceBillingType.BillingRoute.BILL_AMOUNT,
        ),
    }


def client_for(user):
    api = APIClient()
    api.force_authenticate(user=user)
    return api


def post_log(api, setup, *, minutes=60, worked_on=date(2026, 3, 10)):
    issue = IssueFactory(project=setup["project"])
    response = api.post(
        SERVICE_LOGS_URL.format(
            slug=setup["workspace"].slug, project_id=setup["project"].id, issue_id=issue.id
        ),
        data=json.dumps(
            {
                "worked_on": str(worked_on),
                # Free text, per R1 -- the write serializer parses it. Whole minutes so the R2
                # rounding is a no-op and the arithmetic asserted here is the pricing, not the
                # parser's.
                "duration": f"{minutes}min",
                "entry_mode": "duration",
                "description": "Atendimento",
                "hour_type_id": str(setup["hour_type"].id),
                "billing_type_id": str(setup["avulso"].id),
            }
        ),
        content_type="application/json",
    )
    return issue, response


class TestR11TheMoneyIsAbsentFromAMembersPayload:
    """**The leak test, and it asserts absence rather than a value.**

    Revised by D57. Phase 6 had one field set called ``AMOUNT_FIELDS`` holding both the
    money and the commercial state, and restricted all of it. The commercial half is now
    ``COMMERCIAL_STATE_FIELDS`` and reaches a Member, so this class asserts **both
    directions**: money absent, commercial state present. Asserting only the absence
    would let a future phase re-widen the restriction without a test noticing.
    """

    def test_a_member_receives_no_monetary_keys_at_all(self, setup):
        issue, _created = post_log(client_for(setup["admin"]), setup)

        response = client_for(setup["member"]).get(
            SERVICE_LOGS_URL.format(
                slug=setup["workspace"].slug,
                project_id=setup["project"].id,
                issue_id=issue.id,
            )
        )

        assert response.status_code == 200
        assert response.json()["service_logs"], "the fixture must produce a row to inspect"

        # Asserted against the serializer's own list, so this test cannot drift from the code
        # by restating the field names.
        for row in response.json()["service_logs"]:
            for field in ServiceLogSerializer.MONEY_FIELDS:
                assert field not in row, f"{field} leaked to a MEMBER"

        assert "amount" not in response.json()["totals"]

    def test_a_member_does_receive_the_commercial_state(self, setup):
        """**D57.** The route, the deviation and the pricing pendency are not money.

        None of the three reveals a value, a rate or a multiplier. And a technician who
        cannot see that the client's contract has expired cannot warn anybody before
        spending another ten hours that will land as ad-hoc billing -- which makes hiding
        them operationally worse than showing them.

        Phase 4 section 9 already put contract status in front of technicians in the
        attention panel; had the route been money-class, that panel would have been
        violating R11 since Phase 4. This test fixes the narrower classification so the
        wide one cannot come back by accident.
        """
        issue, _created = post_log(client_for(setup["admin"]), setup)

        row = (
            client_for(setup["member"])
            .get(
                SERVICE_LOGS_URL.format(
                    slug=setup["workspace"].slug,
                    project_id=setup["project"].id,
                    issue_id=issue.id,
                )
            )
            .json()["service_logs"][0]
        )

        for field in ServiceLogSerializer.COMMERCIAL_STATE_FIELDS:
            assert field in row, f"{field} is commercial state, not money, and a MEMBER needs it"

        # The specific value, not just the key: this log is ad-hoc, so the settled route
        # says so. Asserting the value is what proves the field is populated rather than
        # present-and-null.
        assert row["settled_billing_route"] == "bill_amount"

        # And the two sets really are disjoint -- otherwise this test and the one above
        # would be contradicting each other rather than partitioning the payload.
        assert not set(ServiceLogSerializer.MONEY_FIELDS) & set(
            ServiceLogSerializer.COMMERCIAL_STATE_FIELDS
        )

    def test_an_admin_receives_them(self, setup):
        """The positive control. Without it the test above would pass on a serializer that
        returned nothing at all."""
        _issue, created = post_log(client_for(setup["admin"]), setup)

        assert created.status_code == 201
        row = created.json()["service_logs"][0]

        for field in ServiceLogSerializer.MONEY_FIELDS:
            assert field in row, f"{field} is missing for an ADMIN"

        assert row["amount"] == "200.00"
        assert row["amount_display"] == "R$ 200,00"
        assert created.json()["totals"]["amount"] == "200.00"

    def test_the_hours_still_reach_a_member(self, setup):
        """R11 takes the price away, not the work. A technician needs their own hours."""
        issue, _created = post_log(client_for(setup["admin"]), setup)

        response = client_for(setup["member"]).get(
            SERVICE_LOGS_URL.format(
                slug=setup["workspace"].slug,
                project_id=setup["project"].id,
                issue_id=issue.id,
            )
        )
        row = response.json()["service_logs"][0]

        assert row["logged_hours"] == "1.0000"
        assert row["equivalent_hours"] == "1.0000"
        assert response.json()["totals"]["logged_hours"] == "1.0000"

    def test_the_activity_trail_carries_no_money(self, setup):
        """**The leak that gating the response alone would not have closed.**

        ``_record_activity`` stores its payload in ``IssueActivity``, whose feed every Member
        of the project can read. Reusing an Admin's response payload there -- the obvious
        thing to do, since it is already serialised -- would put the value in front of exactly
        the audience R11 keeps it from, one hop away from the endpoint that withheld it.
        """
        from plane.app.views.service_log.base import ServiceLogViewSet

        recorded = {}

        original = ServiceLogViewSet._record_activity

        def capture(self, activity_type, request, issue, project_id, requested_data, current_instance):
            recorded["requested_data"] = requested_data
            return original(
                self, activity_type, request, issue, project_id, requested_data, current_instance
            )

        ServiceLogViewSet._record_activity = capture
        try:
            post_log(client_for(setup["admin"]), setup)
        finally:
            ServiceLogViewSet._record_activity = original

        payload = json.loads(recorded["requested_data"])

        for field in ServiceLogSerializer.MONEY_FIELDS:
            assert field not in payload[0], f"{field} reached the activity feed"

        # Positive control: the trail is not empty, it just has no prices in it.
        assert payload[0]["logged_hours"] == "1.0000"

    def test_an_allowance_rate_is_hidden_from_a_member_too(self, setup):
        """The allowance endpoint is legitimately open to Members, so the same rule applies."""
        from plane.utils.service_allowance import credit_allowance

        issue = IssueFactory(project=setup["project"])
        allowance = credit_allowance(issue, Decimal("10.0000"), actor=setup["admin"])
        allowance.overage_hour_rate = Decimal("180.00")
        allowance.save(update_fields=["overage_hour_rate"])

        url = ALLOWANCE_URL.format(
            slug=setup["workspace"].slug, project_id=setup["project"].id, issue_id=issue.id
        )

        as_member = client_for(setup["member"]).get(url).json()["allowance"]
        as_admin = client_for(setup["admin"]).get(url).json()["allowance"]

        for field in ServiceIssueAllowanceSerializer.MONEY_FIELDS:
            assert field not in as_member
            assert field in as_admin

        assert as_member["credited_hours"] == "10.0000", "the hours still reach them"


class TestPriceSheetEndpoints:
    def test_an_admin_registers_a_vigency_and_reads_the_effective_table(self, setup):
        api = client_for(setup["admin"])

        response = api.get(
            EFFECTIVE_URL.format(slug=setup["workspace"].slug, client_id=setup["client"].id),
            {"on_date": "2026-03-01"},
        )

        assert response.status_code == 200
        by_name = {row["hour_type_name"]: row for row in response.json()["effective_table"]}
        assert by_name["Comercial"]["rate"] == "200.00"
        assert by_name["Domingos"]["rate"] == "400.00"

    def test_a_readjustment_is_a_new_vigency_not_an_edit(self, setup):
        """Criterion 6 through the API: the old sheet is untouched and still resolves for
        dates before the new one."""
        api = client_for(setup["admin"])

        created = api.post(
            PRICES_URL.format(slug=setup["workspace"].slug, client_id=setup["client"].id),
            data=json.dumps({"starts_on": "2027-01-01", "base_hour_rate": "220.00"}),
            content_type="application/json",
        )

        assert created.status_code == 201
        assert ServiceClientPrice.objects.filter(service_client=setup["client"]).count() == 2

        listed = api.get(
            PRICES_URL.format(slug=setup["workspace"].slug, client_id=setup["client"].id),
            {"on_date": "2026-06-01"},
        ).json()

        assert listed["in_force_price_id"] == str(setup["sheet"].id)

    def test_a_vigency_starting_mid_month_is_refused_with_a_code(self, setup):
        """The DDL refuses it; this asserts the API says so in a code a client can render
        rather than surfacing a Postgres constraint name."""
        response = client_for(setup["admin"]).post(
            PRICES_URL.format(slug=setup["workspace"].slug, client_id=setup["client"].id),
            data=json.dumps({"starts_on": "2027-03-15", "base_hour_rate": "220.00"}),
            content_type="application/json",
        )

        assert response.status_code == 400
        assert "PRICE_VIGENCY_MUST_START_ON_THE_FIRST_OF_A_MONTH" in str(response.json())

    def test_a_duplicate_vigency_is_refused(self, setup):
        response = client_for(setup["admin"]).post(
            PRICES_URL.format(slug=setup["workspace"].slug, client_id=setup["client"].id),
            data=json.dumps({"starts_on": "2026-01-01", "base_hour_rate": "999.00"}),
            content_type="application/json",
        )

        assert response.status_code == 400
        assert response.json()["error"] == "PRICE_VIGENCY_ALREADY_EXISTS"

    def test_an_override_prevails_in_the_effective_table(self, setup):
        api = client_for(setup["admin"])

        created = api.post(
            OVERRIDES_URL.format(slug=setup["workspace"].slug, price_id=setup["sheet"].id),
            data=json.dumps(
                {"hour_type": str(setup["sunday"].id), "absolute_rate": "350.00"}
            ),
            content_type="application/json",
        )

        assert created.status_code == 201

        table = api.get(
            EFFECTIVE_URL.format(slug=setup["workspace"].slug, client_id=setup["client"].id),
            {"on_date": "2026-03-01"},
        ).json()["effective_table"]
        by_name = {row["hour_type_name"]: row for row in table}

        assert by_name["Domingos"]["rate"] == "350.00"
        assert by_name["Domingos"]["is_overridden"] is True
        assert by_name["Comercial"]["rate"] == "200.00", "positive control: only one changed"

    def test_a_vigency_that_priced_work_cannot_be_deleted(self, setup):
        """Auditability, not referential integrity: those rows carry a rate snapshot, and
        deleting the sheet would leave nobody able to look up where the number came from."""
        post_log(client_for(setup["admin"]), setup)

        response = client_for(setup["admin"]).delete(
            PRICE_URL.format(
                slug=setup["workspace"].slug,
                client_id=setup["client"].id,
                pk=setup["sheet"].id,
            )
        )

        assert response.status_code == 400
        assert response.json()["error"] == "PRICE_VIGENCY_ALREADY_PRICED_WORK_LOGS"

    def test_a_vigency_that_priced_nothing_can_be_deleted(self, setup):
        """Positive control for the refusal above."""
        future = ServiceClientPrice.objects.create(
            workspace=setup["workspace"],
            service_client=setup["client"],
            starts_on=date(2028, 1, 1),
            base_hour_rate=Decimal("300.00"),
        )

        response = client_for(setup["admin"]).delete(
            PRICE_URL.format(
                slug=setup["workspace"].slug, client_id=setup["client"].id, pk=future.id
            )
        )

        assert response.status_code == 204

    def test_the_audit_trail_records_the_registration(self, setup):
        """D22 asked the pricing phase for all three verbs by name: "when was this hour rate
        registered, and by whom" has to be answerable months later."""
        from plane.db.models import ServiceConfigActivity, ServiceConfigEntity

        client_for(setup["admin"]).post(
            PRICES_URL.format(slug=setup["workspace"].slug, client_id=setup["client"].id),
            data=json.dumps({"starts_on": "2027-01-01", "base_hour_rate": "220.00"}),
            content_type="application/json",
        )

        trail = ServiceConfigActivity.objects.filter(
            entity_name=ServiceConfigEntity.CLIENT_PRICE
        )

        assert trail.count() == 1
        assert trail.first().verb == "created"
        assert trail.first().actor_id == setup["admin"].id
        assert "220.00" in trail.first().new_value


class TestOnlyAdminsReachAnyOfThis:
    @pytest.mark.parametrize(
        "url_template,method",
        [
            (PRICES_URL, "get"),
            (PRICES_URL, "post"),
            (EFFECTIVE_URL, "get"),
        ],
    )
    def test_a_member_is_refused(self, setup, url_template, method):
        """R11: a price sheet is the value in reais before it has been applied."""
        url = url_template.format(
            slug=setup["workspace"].slug, client_id=setup["client"].id
        )
        response = getattr(client_for(setup["member"]), method)(url)

        assert response.status_code == 403

    def test_a_member_cannot_read_the_consolidation(self, setup):
        response = client_for(setup["member"]).get(
            CONSOLIDATION_URL.format(slug=setup["workspace"].slug)
        )

        assert response.status_code == 403

    def test_a_guest_reaches_nothing(self, setup):
        for url in (
            PRICES_URL.format(slug=setup["workspace"].slug, client_id=setup["client"].id),
            CONSOLIDATION_URL.format(slug=setup["workspace"].slug),
            EXPORT_URL.format(slug=setup["workspace"].slug),
        ):
            assert client_for(setup["guest"]).get(url).status_code == 403


class TestTheConsolidationEndpoint:
    def test_it_reports_the_month_by_client_and_origin(self, setup):
        post_log(client_for(setup["admin"]), setup, worked_on=date(2026, 3, 10))

        response = client_for(setup["admin"]).get(
            CONSOLIDATION_URL.format(slug=setup["workspace"].slug),
            {"year": 2026, "month": 3},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["competence"] == "2026-03"
        assert body["total_amount"] == "200.00"
        assert body["total_amount_display"] == "R$ 200,00"

    def test_an_impossible_competence_is_refused(self, setup):
        response = client_for(setup["admin"]).get(
            CONSOLIDATION_URL.format(slug=setup["workspace"].slug), {"year": 2026, "month": 13}
        )

        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_COMPETENCE"


class TestTheExportUsesTheExistingPipeline:
    def test_it_queues_a_row_of_the_reserved_type(self, setup):
        """Fills the ``issue_worklogs`` choice that has sat on ``ExporterHistory`` with no
        handler behind it. Nothing new is built -- the queue, the history, the status
        transitions and the expiry all come from the existing pipeline."""
        response = client_for(setup["admin"]).post(
            EXPORT_URL.format(slug=setup["workspace"].slug),
            data=json.dumps({"provider": "csv", "year": 2026, "month": 3}),
            content_type="application/json",
        )

        assert response.status_code == 200
        assert "token" in response.json()

        row = ExporterHistory.objects.get(token=response.json()["token"])
        assert row.type == "issue_worklogs"
        assert row.provider == "csv"
        assert row.status == "queued"
        # `filters` has likewise been on the model unused. Recording the competency is what
        # makes the history legible rather than a list of identical rows.
        assert row.filters == {"year": "2026", "month": "3"}

    def test_a_month_without_a_year_is_refused(self, setup):
        response = client_for(setup["admin"]).post(
            EXPORT_URL.format(slug=setup["workspace"].slug),
            data=json.dumps({"provider": "csv", "month": 3}),
            content_type="application/json",
        )

        assert response.status_code == 400
        assert "COMPETENCE_REQUIRES_BOTH_YEAR_AND_MONTH" in str(response.json())

    def test_the_history_is_visible_and_carries_the_failure_reason(self, setup):
        """``ExporterHistorySerializer`` omits ``reason``, which is harmless for issues and not
        for a billing export: an operator waiting on a month's numbers needs the reason, not a
        status of ``failed`` and silence."""
        ExporterHistory.objects.create(
            workspace=setup["workspace"],
            project=[],
            initiated_by=setup["admin"],
            provider="csv",
            type="issue_worklogs",
            status="failed",
            reason="Unsupported format: pdf",
        )

        body = client_for(setup["admin"]).get(
            EXPORT_URL.format(slug=setup["workspace"].slug)
        ).json()

        assert body[0]["reason"] == "Unsupported format: pdf"
        assert body[0]["type"] == "issue_worklogs"


class TestTheOveragePreviewEndpoint:
    def test_it_shows_the_value_and_says_the_action_is_final(self, setup):
        """Requirement F1: an irreversible mistake must be deliberate rather than careless."""
        from plane.utils.service_pool import resolve_period

        contract = ServiceContractFactory(
            service_client=setup["client"],
            code="SUP-001",
            monthly_hours=Decimal("1.0000"),
            starts_on=date(2026, 1, 1),
            ends_on=date(2026, 12, 31),
            overage_hour_rate=Decimal("250.00"),
        )
        period = resolve_period(contract, date(2026, 1, 1), actor=setup["admin"])
        type(period).objects.filter(pk=period.pk).update(consumed_hours=Decimal("4.0000"))
        period.refresh_from_db()

        response = client_for(setup["admin"]).get(
            PERIOD_PREVIEW_URL.format(slug=setup["workspace"].slug, pk=period.id)
        )

        assert response.status_code == 200
        body = response.json()
        assert body["overage_hours"] == "3.0000"
        assert body["overage_hour_rate"] == "250.00"
        assert body["amount"] == "750.00"
        assert body["is_reversible"] is False


class TestTheAllowanceOverageRateEndpoint:
    def test_an_admin_sets_and_clears_the_rate(self, setup):
        """Decision B: the allowance is a separately negotiated sale, so it carries its own
        price. ``null`` clears it back to the client's base rate."""
        from plane.utils.service_allowance import credit_allowance

        issue = IssueFactory(project=setup["project"])
        allowance = credit_allowance(issue, Decimal("40.0000"), actor=setup["admin"])
        url = OVERAGE_RATE_URL.format(slug=setup["workspace"].slug, pk=allowance.id)
        api = client_for(setup["admin"])

        set_response = api.post(
            url, data=json.dumps({"overage_hour_rate": "180.00"}), content_type="application/json"
        )
        assert set_response.status_code == 200
        assert set_response.json()["allowance"]["overage_hour_rate"] == "180.00"

        cleared = api.post(
            url, data=json.dumps({"overage_hour_rate": None}), content_type="application/json"
        )
        assert cleared.status_code == 200
        assert cleared.json()["allowance"]["overage_hour_rate"] is None

    def test_a_member_cannot_set_it(self, setup):
        from plane.utils.service_allowance import credit_allowance

        issue = IssueFactory(project=setup["project"])
        allowance = credit_allowance(issue, Decimal("40.0000"), actor=setup["admin"])

        response = client_for(setup["member"]).post(
            OVERAGE_RATE_URL.format(slug=setup["workspace"].slug, pk=allowance.id),
            data=json.dumps({"overage_hour_rate": "180.00"}),
            content_type="application/json",
        )

        assert response.status_code == 403

    def test_the_rate_change_is_audited(self, setup):
        """Decision B's other half: the rate is configuration that decides money, so unlike the
        hour accumulators it is audited here rather than in the ledger."""
        from plane.db.models import ServiceConfigActivity, ServiceConfigEntity
        from plane.utils.service_allowance import credit_allowance

        issue = IssueFactory(project=setup["project"])
        allowance = credit_allowance(issue, Decimal("40.0000"), actor=setup["admin"])

        client_for(setup["admin"]).post(
            OVERAGE_RATE_URL.format(slug=setup["workspace"].slug, pk=allowance.id),
            data=json.dumps({"overage_hour_rate": "180.00"}),
            content_type="application/json",
        )

        trail = ServiceConfigActivity.objects.filter(
            entity_name=ServiceConfigEntity.ISSUE_ALLOWANCE, field_name="overage_hour_rate"
        )

        assert trail.count() == 1
        # The nullable tracked field serialises its unset side through the D4 sentinel rather
        # than as SQL NULL, which is what keeps `svc_cfg_activity_shape_matches_verb` satisfied.
        assert trail.first().old_value == "UNSET"
        assert trail.first().new_value == "180.00"
