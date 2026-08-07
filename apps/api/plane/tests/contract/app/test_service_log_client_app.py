# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""The work logs a client is allowed to see. R11, D58, D65. Criteria 11 and 12.

Absence is asserted against the serializer's own tuples wherever one exists, and against
literal field names for the three quantities that are absent *by construction* -- they are
not in ``Meta.fields`` at all, so there is no tuple to assert against and a literal is the
only way to pin them.

``logged_hours`` gets its own test rather than sharing one with the other two. It is not
merely another hidden column: the client sees ``equivalent_hours`` legitimately, so
``logged_hours`` completes a division that returns the multiplier. R11(c) exists to prevent
that derivation, and a test named after it will survive a refactor that a parametrized list
would not explain.

Every negative has a positive control in the same fixture: the rows exist, the endpoint
returns them, and the technician's own endpoint still carries everything.
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from plane.app.serializers import ServiceLogClientSerializer, ServiceLogSerializer
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

CLIENT_URL = "/api/workspaces/{slug}/projects/{project_id}/issues/{issue_id}/service-logs/client/"
MEMBER_URL = "/api/workspaces/{slug}/projects/{project_id}/issues/{issue_id}/service-logs/"

ROUTE = ServiceBillingType.BillingRoute


def _user(prefix, workspace, project, role):
    unique_id = uuid4().hex[:8]
    user = User.objects.create(
        email=f"{prefix}-{unique_id}@plane.so",
        username=f"{prefix}_{unique_id}",
        first_name=prefix.title(),
    )
    user.set_password("test-password")
    user.save()
    WorkspaceMember.objects.create(workspace=workspace, member=user, role=role)
    ProjectMember.objects.create(project=project, member=user, workspace=workspace, role=role)
    return user


@pytest.fixture
def service_client(db, workspace):
    return ServiceClient.objects.create(name="Marubeni do Brasil", workspace=workspace)


@pytest.fixture
def project(db, workspace, create_user, service_client):
    project = Project.objects.create(
        name="Marubeni Support",
        identifier="MARU",
        workspace=workspace,
        created_by=create_user,
        service_client=service_client,
        guest_view_all_features=True,
        is_time_tracking_enabled=True,
    )
    ProjectMember.objects.create(project=project, member=create_user, workspace=workspace, role=20)
    return project


@pytest.fixture
def technician(db, workspace, project):
    return _user("tech", workspace, project, 15)


@pytest.fixture
def client_user(db, workspace, project):
    return _user("marcel", workspace, project, 5)


@pytest.fixture
def hour_type(db, workspace):
    return ServiceHourType.objects.create(
        name="Fora do expediente", workspace=workspace, multiplier=Decimal("1.50")
    )


@pytest.fixture
def billing_types(db, workspace):
    return {
        route: ServiceBillingType.objects.create(
            name=f"tipo-{route}", workspace=workspace, billing_route=route
        )
        for route in (ROUTE.DEBIT_POOL, ROUTE.BILL_AMOUNT, ROUTE.NON_BILLABLE)
    }


@pytest.fixture
def issue(db, project, workspace, technician):
    issue = Issue(name="Server is down", project=project, workspace=workspace)
    issue.save(created_by_id=technician.id)
    return issue


def _log(
    issue,
    project,
    workspace,
    author,
    hour_type,
    billing_type,
    *,
    route,
    amount="0.00",
    rate=None,
    applied_route=None,
    deviation=None,
    pricing_failure=None,
):
    """One work log with every quantity set explicitly.

    ``logged_hours`` is deliberately different from ``equivalent_hours`` (1.0 against 1.5,
    the 1.5x multiplier) so that a leak of the wrong quantity is visible as a different
    number rather than hidden behind two equal values.
    """
    return ServiceLog.objects.create(
        issue=issue,
        project=project,
        workspace=workspace,
        author=author,
        hour_type=hour_type,
        billing_type=billing_type,
        worked_on=date(2026, 3, 10),
        description="Restarted the cluster",
        raw_duration_minutes=60,
        logged_hours=Decimal("1.0000"),
        equivalent_hours=Decimal("1.5000"),
        debited_hours=Decimal("0.0000") if route == ROUTE.NON_BILLABLE else Decimal("1.5000"),
        applied_multiplier=Decimal("1.50"),
        # `applied` is the route chosen, `settled` the one honoured (D44). They differ only
        # in the one case D33 permits, which is the deviation scenario below.
        applied_billing_route=applied_route or route,
        settled_billing_route=route,
        # Set at INSERT rather than by a later UPDATE: `service_log_route_deviation_is_coherent`
        # is a biconditional, so a row is never briefly allowed to have one half without
        # the other.
        route_deviation_reason=deviation,
        pricing_failure_reason=pricing_failure,
        applied_hour_rate=rate,
        # A rate, its basis and a positive amount travel together or none of them exist --
        # `service_log_amount_requires_a_rate`.
        applied_rate_basis=ServiceLog.RateBasis.BASE_MULTIPLIER if rate is not None else None,
        amount=Decimal(amount),
        batch_id=uuid4(),
    )


@pytest.fixture
def pool_log(issue, project, workspace, technician, hour_type, billing_types):
    """A contract client's ordinary row: absorbed by the pool, no invoice line."""
    return _log(issue, project, workspace, technician, hour_type, billing_types[ROUTE.DEBIT_POOL], route=ROUTE.DEBIT_POOL)


@pytest.fixture
def billed_log(issue, project, workspace, technician, hour_type, billing_types):
    """Out of scope, billed separately: the row the client will see on an invoice."""
    return _log(
        issue,
        project,
        workspace,
        technician,
        hour_type,
        billing_types[ROUTE.BILL_AMOUNT],
        route=ROUTE.BILL_AMOUNT,
        amount="300.00",
        rate=Decimal("200.00"),
    )


def _api(user):
    api = APIClient()
    api.force_authenticate(user=user)
    return api


def _get(user, project, issue, url=CLIENT_URL):
    return _api(user).get(url.format(slug=project.workspace.slug, project_id=project.id, issue_id=issue.id))


@pytest.mark.contract
class TestR11TheClientNeverReceivesTheHiddenQuantities:
    @pytest.mark.django_db
    def test_the_client_never_receives_logged_hours(self, project, issue, client_user, pool_log, billed_log):
        """The one that matters most: with equivalent_hours, which the client does see,
        logged_hours yields the multiplier by division. R11(c)."""
        response = _get(client_user, project, issue)

        assert response.status_code == status.HTTP_200_OK, response.data

        for row in response.data["service_logs"]:
            assert "logged_hours" not in row

        assert "logged_hours" not in response.data["totals"]
        # And the value itself, in case a future key name carries it.
        assert "1.0000" not in str(response.data)

    @pytest.mark.django_db
    @pytest.mark.parametrize("field", ["raw_duration_minutes", "applied_multiplier", "pricing_failure_reason"])
    def test_the_client_never_receives_the_other_hidden_fields(
        self, project, issue, client_user, pool_log, billed_log, field
    ):
        """Absent by construction: not in `Meta.fields`, so no context can restore them."""
        response = _get(client_user, project, issue)

        for row in response.data["service_logs"]:
            assert field not in row

    @pytest.mark.django_db
    @pytest.mark.parametrize("field", ["applied_hour_rate", "applied_rate_basis"])
    def test_the_client_never_receives_the_rate(self, project, issue, client_user, billed_log, field):
        """The rate is money even on a row whose amount the client may see: the amount is
        an invoice line, the rate is the commercial term behind it."""
        response = _get(client_user, project, issue)

        for row in response.data["service_logs"]:
            assert field not in row

    @pytest.mark.django_db
    def test_the_hidden_fields_are_not_merely_stripped_but_undeclared(self):
        """Structural, not behavioural. A field absent from `Meta.fields` cannot be
        restored by a caller passing a context flag."""
        declared = set(ServiceLogClientSerializer.Meta.fields)

        assert declared.isdisjoint({"raw_duration_minutes", "logged_hours", "applied_multiplier"})
        assert declared.isdisjoint({"applied_hour_rate", "applied_rate_basis", "pricing_failure_reason"})

    @pytest.mark.django_db
    def test_the_client_does_receive_the_hours_they_are_entitled_to(
        self, project, issue, client_user, pool_log, billed_log
    ):
        """The positive control. An endpoint returning nothing passes every test above."""
        response = _get(client_user, project, issue)

        assert len(response.data["service_logs"]) == 2

        for row in response.data["service_logs"]:
            assert row["equivalent_hours"] == "1.5000"
            assert row["equivalent_hours_display"]
            assert "debited_hours" in row
            assert row["hour_type_name"] == "Fora do expediente"
            assert row["author_detail"]["id"]

        assert response.data["totals"]["equivalent_hours"] == "3.0000"

    @pytest.mark.django_db
    def test_a_member_still_receives_everything(self, project, issue, technician, pool_log, billed_log):
        """The other positive control: the columns exist and carry the numbers. Without
        this, a model that stopped persisting logged_hours would look like a fix."""
        response = _get(technician, project, issue, url=MEMBER_URL)

        assert response.status_code == status.HTTP_200_OK, response.data

        for row in response.data["service_logs"]:
            assert row["logged_hours"] == "1.0000"
            assert row["raw_duration_minutes"] == 60
            assert row["applied_multiplier"] == "1.50"


@pytest.mark.contract
class TestD58MoneyFollowsTheSettledRouteOfEachRow:
    @pytest.mark.django_db
    def test_a_pool_debited_row_carries_no_amount(self, project, issue, client_user, pool_log):
        """A contract client's ordinary hours produce no invoice line, so no value."""
        response = _get(client_user, project, issue)

        row = response.data["service_logs"][0]

        for field in ServiceLogClientSerializer.CLIENT_MONEY_FIELDS:
            assert field not in row

    @pytest.mark.django_db
    def test_a_billed_row_does_carry_its_amount(self, project, issue, client_user, billed_log):
        """The client sees money exactly where they will receive an invoice line."""
        response = _get(client_user, project, issue)

        row = response.data["service_logs"][0]

        assert row["amount"] == "300.00"
        assert row["amount_display"] == "R$ 300,00"

    @pytest.mark.django_db
    def test_one_response_mixes_both_correctly(self, project, issue, client_user, pool_log, billed_log):
        """The case a per-serializer flag would get wrong: the answer is per row, so a
        field popped at construction would apply one row's answer to all of them."""
        response = _get(client_user, project, issue)

        by_route = {row["settled_billing_route"]: row for row in response.data["service_logs"]}

        assert "amount" not in by_route[ROUTE.DEBIT_POOL]
        assert by_route[ROUTE.BILL_AMOUNT]["amount"] == "300.00"

    @pytest.mark.django_db
    def test_a_non_billable_row_carries_no_amount(
        self, project, issue, client_user, technician, hour_type, billing_types
    ):
        _log(
            issue,
            project,
            workspace=project.workspace,
            author=technician,
            hour_type=hour_type,
            billing_type=billing_types[ROUTE.NON_BILLABLE],
            route=ROUTE.NON_BILLABLE,
        )

        response = _get(client_user, project, issue)
        row = response.data["service_logs"][0]

        assert "amount" not in row
        assert row["debited_hours"] == "0.0000"

    @pytest.mark.django_db
    def test_the_total_counts_only_billed_rows(self, project, issue, client_user, pool_log, billed_log):
        response = _get(client_user, project, issue)

        assert response.data["totals"]["amount"] == "300.00"
        assert response.data["totals"]["amount_display"] == "R$ 300,00"

    @pytest.mark.django_db
    def test_the_total_omits_money_entirely_when_nothing_was_billed(
        self, project, issue, client_user, pool_log
    ):
        """Absent, not zero: a zero would be a number the client may not have, and a false
        one. Same argument the aggregation layer's `bucket()` makes."""
        response = _get(client_user, project, issue)

        assert "amount" not in response.data["totals"]
        assert "amount_display" not in response.data["totals"]

    @pytest.mark.django_db
    def test_the_client_money_fields_are_disjoint_from_the_members_rate_fields(self):
        """Pins that the client's money set is the invoice line only, never the terms."""
        assert set(ServiceLogClientSerializer.CLIENT_MONEY_FIELDS) == {"amount", "amount_display"}
        assert "applied_hour_rate" in ServiceLogSerializer.MONEY_FIELDS
        assert "applied_hour_rate" not in ServiceLogClientSerializer.CLIENT_MONEY_FIELDS


@pytest.mark.contract
class TestD65TwoOfTheThreeCommercialStateFields:
    @pytest.mark.django_db
    def test_the_client_sees_the_settled_route(self, project, issue, client_user, pool_log):
        """Explains which arrangement absorbed the hours, which the client can already
        infer from debited_hours."""
        response = _get(client_user, project, issue)

        assert response.data["service_logs"][0]["settled_billing_route"] == ROUTE.DEBIT_POOL

    @pytest.mark.django_db
    def test_the_client_sees_the_route_deviation_reason(
        self, project, issue, client_user, technician, hour_type, billing_types
    ):
        """When the amount is visible the client will ask why they are charged despite
        holding a contract. "Contract expired" is the answer, and showing the charge while
        hiding its reason is the error shape D58 rejects.

        The row is built the only way D33 permits one to exist: the technician chose the
        pool, and it settled as billed because the contract had expired. That is the
        production scenario this decision is about, not a synthetic combination.
        """
        _log(
            issue,
            project,
            workspace=project.workspace,
            author=technician,
            hour_type=hour_type,
            billing_type=billing_types[ROUTE.BILL_AMOUNT],
            route=ROUTE.BILL_AMOUNT,
            applied_route=ROUTE.DEBIT_POOL,
            deviation=ServiceLog.RouteDeviation.CONTRACT_EXPIRED,
            amount="300.00",
            rate=Decimal("200.00"),
        )

        response = _get(client_user, project, issue)
        row = response.data["service_logs"][0]

        assert row["route_deviation_reason"] == ServiceLog.RouteDeviation.CONTRACT_EXPIRED
        # Charge and reason travel together.
        assert row["amount"] == "300.00"

    @pytest.mark.django_db
    def test_the_client_does_not_see_the_pricing_failure_reason(
        self, project, issue, client_user, technician, hour_type, billing_types
    ):
        """An internal configuration gap on a row that is not billable yet. Nothing for
        the client to act on."""
        _log(
            issue,
            project,
            workspace=project.workspace,
            author=technician,
            hour_type=hour_type,
            billing_type=billing_types[ROUTE.BILL_AMOUNT],
            route=ROUTE.BILL_AMOUNT,
            pricing_failure=ServiceLog.PricingFailure.NO_PRICE_SHEET_FOR_CLIENT,
        )

        response = _get(client_user, project, issue)

        assert "pricing_failure_reason" not in response.data["service_logs"][0]
        assert "no_price_sheet" not in str(response.data)

    @pytest.mark.django_db
    def test_a_member_does_see_the_pricing_failure_reason(
        self, project, issue, technician, hour_type, billing_types
    ):
        """The positive control, and D57's boundary: it is commercial state, visible to a
        Member. GUEST was a separate question, taken in D65."""
        _log(
            issue,
            project,
            workspace=project.workspace,
            author=technician,
            hour_type=hour_type,
            billing_type=billing_types[ROUTE.BILL_AMOUNT],
            route=ROUTE.BILL_AMOUNT,
            pricing_failure=ServiceLog.PricingFailure.NO_PRICE_SHEET_FOR_CLIENT,
        )

        response = _get(technician, project, issue, url=MEMBER_URL)

        assert response.data["service_logs"][0]["pricing_failure_reason"] == (
            ServiceLog.PricingFailure.NO_PRICE_SHEET_FOR_CLIENT
        )

    @pytest.mark.django_db
    def test_the_client_commercial_set_is_two_of_the_three(self):
        assert set(ServiceLogClientSerializer.CLIENT_COMMERCIAL_STATE_FIELDS) == {
            "settled_billing_route",
            "route_deviation_reason",
        }
        assert "pricing_failure_reason" in ServiceLogSerializer.COMMERCIAL_STATE_FIELDS
        assert "pricing_failure_reason" not in ServiceLogClientSerializer.CLIENT_COMMERCIAL_STATE_FIELDS


@pytest.mark.contract
class TestTheProjectionDoesNotDependOnWhoAsks:
    @pytest.mark.django_db
    def test_an_admin_asking_this_endpoint_sees_exactly_what_the_client_sees(
        self, project, issue, client_user, create_user, pool_log, billed_log
    ):
        """No role-conditional branch inside, so there is no place for R11 to be wrong --
        and an operator can check what the portal shows before a client asks."""
        as_client = _get(client_user, project, issue).data
        as_admin = _get(create_user, project, issue).data

        assert as_admin == as_client

    @pytest.mark.django_db
    def test_a_client_cannot_read_a_work_item_the_read_endpoint_hides(
        self, project, issue, client_user, pool_log
    ):
        project.guest_view_all_features = False
        project.save(update_fields=["guest_view_all_features"])

        response = _get(client_user, project, issue)

        assert response.status_code == status.HTTP_403_FORBIDDEN, response.data

    @pytest.mark.django_db
    def test_a_member_is_unaffected_by_that_flag(self, project, issue, technician, pool_log):
        """The positive control: the flag narrows the client, not the endpoint."""
        project.guest_view_all_features = False
        project.save(update_fields=["guest_view_all_features"])

        response = _get(technician, project, issue)

        assert response.status_code == status.HTTP_200_OK, response.data

    @pytest.mark.django_db
    def test_an_outsider_is_refused(self, project, issue, workspace, pool_log):
        outsider = User.objects.create(email=f"out-{uuid4().hex[:8]}@plane.so", username=f"out_{uuid4().hex[:8]}")
        outsider.set_password("test-password")
        outsider.save()

        response = _get(outsider, project, issue)

        assert response.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)
