# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""The one filter descriptor. D50, and the D48 invariant that makes it correct.

The descriptor exists so that a total and the list behind it cannot disagree, which means
the tests that matter here are **invariants** rather than examples:

* ``from_params(to_params(x)) == x`` for every shape a descriptor can take;
* narrowing to a period **drops the ``worked_on`` window**, because D31 puts some of a
  period's work logs outside its own month and keeping both filters would make the
  drill-down return fewer rows than the aggregate counted.

The second one is the whole reason ``competence_basis`` is a field instead of an argument
somewhere. It is verified against real rows, not by reading the code.
"""

# Python imports
from datetime import date
from decimal import Decimal

# Third party imports
import pytest

# Module imports
from plane.db.models import ServiceBillingType, ServiceLog
from plane.tests.factories import (
    IssueFactory,
    ProjectFactory,
    ServiceBillingTypeFactory,
    ServiceClientFactory,
    ServiceContractFactory,
    ServiceHourTypeFactory,
    ServiceLogFactory,
    UserFactory,
)
from plane.utils.service_pool import resolve_period
from plane.utils.service_reports_filters import (
    CompetenceBasis,
    ServiceLogFilterSet,
    ServiceReportFilterError,
    competence_index,
    format_competence,
    parse_competence,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def no_celery_dispatch(monkeypatch):
    monkeypatch.setattr("celery.app.task.Task.apply_async", lambda *args, **kwargs: None)


@pytest.fixture
def actor(db):
    return UserFactory()


@pytest.fixture
def scenario(db, actor):
    """A contract starting in March, with one work log dated February. D31's case.

    The February log debits the **March** period, per D31. That is the row every assertion
    about ``competence_basis`` turns on, so the fixture asserts the clamp actually happened
    rather than trusting it -- if D31 ever changed, these tests would otherwise start
    passing for the wrong reason.
    """
    service_client = ServiceClientFactory(name="Terlogs")
    contract = ServiceContractFactory(
        service_client=service_client,
        code="TER-1",
        monthly_hours=Decimal("40.0000"),
        starts_on=date(2026, 3, 1),
        ends_on=date(2026, 12, 31),
    )
    project = ProjectFactory(
        name="Suporte", workspace=service_client.workspace, service_client=service_client
    )
    hour_type = ServiceHourTypeFactory(
        workspace=service_client.workspace, name="Comercial", multiplier=Decimal("1.00")
    )
    pool_type = ServiceBillingTypeFactory(
        workspace=service_client.workspace,
        name="Contrato",
        billing_route=ServiceBillingType.BillingRoute.DEBIT_POOL,
    )

    march = resolve_period(contract, date(2026, 3, 10), actor=actor)

    # The retroactive entry: worked in February, debited to March by D31.
    retroactive = ServiceLogFactory(
        project=project,
        issue=IssueFactory(project=project),
        author=actor,
        worked_on=date(2026, 2, 20),
        hour_type=hour_type,
        billing_type=pool_type,
        logged_hours=Decimal("2.0000"),
        equivalent_hours=Decimal("2.0000"),
        debited_hours=Decimal("2.0000"),
        applied_billing_route=ServiceBillingType.BillingRoute.DEBIT_POOL,
        settled_billing_route=ServiceBillingType.BillingRoute.DEBIT_POOL,
        debited_period=march,
    )

    # An ordinary entry, worked and debited in March.
    ordinary = ServiceLogFactory(
        project=project,
        issue=IssueFactory(project=project),
        author=actor,
        worked_on=date(2026, 3, 5),
        hour_type=hour_type,
        billing_type=pool_type,
        logged_hours=Decimal("3.0000"),
        equivalent_hours=Decimal("3.0000"),
        debited_hours=Decimal("3.0000"),
        applied_billing_route=ServiceBillingType.BillingRoute.DEBIT_POOL,
        settled_billing_route=ServiceBillingType.BillingRoute.DEBIT_POOL,
        debited_period=march,
    )

    assert retroactive.debited_period_id == march.pk, (
        "D31 must clamp a pre-vigency log onto the first period, or this fixture proves nothing"
    )
    assert (march.competence_year, march.competence_month) == (2026, 3)

    return {
        "workspace_id": service_client.workspace_id,
        "contract": contract,
        "march": march,
        "project": project,
        "retroactive": retroactive,
        "ordinary": ordinary,
        "client": service_client,
        "hour_type": hour_type,
        "author": actor,
    }


class TestCompetenceParsing:
    def test_a_competence_round_trips(self):
        assert parse_competence("2026-03", field_name="x") == (2026, 3)
        assert format_competence((2026, 3)) == "2026-03"
        assert format_competence(parse_competence("2026-12", field_name="x")) == "2026-12"

    def test_an_already_parsed_tuple_is_accepted(self):
        """So a descriptor can be re-parsed without the caller special-casing."""
        assert parse_competence((2026, 7), field_name="x") == (2026, 7)

    @pytest.mark.parametrize(
        "raw", ["2026", "2026-13", "2026-00", "banana", "2026-ab", "26-3", "2026-03-01"]
    )
    def test_a_malformed_competence_is_a_named_error(self, raw):
        """A 400 with a code, never a 500. ``26-3`` is in the list on purpose: silently
        reading it as the year 26 would produce an empty report that looks like a month with
        no work, which is worse than an error."""
        with pytest.raises(ServiceReportFilterError) as raised:
            parse_competence(raw, field_name="competence_from")

        assert raised.value.code == "INVALID_COMPETENCE"

    def test_the_index_orders_across_a_year_boundary(self):
        """The reason competencies are compared as an integer rather than a pair: December
        to January must be two consecutive steps, not a decrease."""
        assert competence_index(2027, 1) - competence_index(2026, 12) == 1
        assert competence_index(2026, 1) < competence_index(2026, 12) < competence_index(2027, 1)


class TestDescriptorRoundTrip:
    """``from_params(to_params(x)) == x``. The property that lets a bucket descriptor
    travel to the browser and come back as the same query."""

    def test_an_empty_descriptor_serialises_to_almost_nothing(self):
        params = ServiceLogFilterSet().to_params()

        assert params == {"competence_basis": "worked_on"}, (
            "an unfiltered descriptor must not produce a wall of empty parameters"
        )

    def test_a_fully_populated_descriptor_round_trips(self, scenario):
        original = ServiceLogFilterSet(
            competence_basis=CompetenceBasis.WORKED_ON,
            competence_from=(2026, 1),
            competence_to=(2026, 12),
            service_client_ids=(scenario["client"].id,),
            project_ids=(scenario["project"].id,),
            issue_ids=(scenario["ordinary"].issue_id,),
            author_ids=(scenario["author"].id,),
            hour_type_ids=(scenario["hour_type"].id,),
            settled_routes=("bill_amount", "debit_pool"),
            route_deviation_reasons=("contract_ended",),
            pricing_failure_reasons=("no_price_sheet_for_client",),
            debited_period_ids=(scenario["march"].pk,),
            without_pool_origin=True,
        )

        assert ServiceLogFilterSet.from_params(original.to_params()) == original

    def test_the_order_a_caller_lists_ids_in_does_not_change_the_descriptor(self, scenario):
        """Two descriptors selecting the same rows must be equal, or the round-trip
        assertion above would be testing string formatting rather than selection."""
        first = ServiceLogFilterSet.from_params(
            {"project_ids": f"{scenario['project'].id},{scenario['ordinary'].issue.project_id}"}
        )
        second = ServiceLogFilterSet.from_params({"project_ids": str(scenario["project"].id)})

        assert first == second, "duplicates must collapse"

    def test_an_unknown_parameter_is_ignored_rather_than_rejected(self):
        """The frontend puts UI-only state in the URL. A report that 400s because of a tab
        name would be a filter descriptor dictating navigation."""
        assert ServiceLogFilterSet.from_params(
            {"competence_basis": "worked_on", "tab": "overview", "zoom": "3"}
        ) == ServiceLogFilterSet()

    def test_a_bad_uuid_is_a_named_error(self):
        with pytest.raises(ServiceReportFilterError) as raised:
            ServiceLogFilterSet.from_params({"project_ids": "not-a-uuid"})

        assert raised.value.code == "INVALID_UUID_FILTER"
        assert raised.value.detail["field"] == "project_ids"

    def test_an_unknown_route_is_a_named_error(self):
        """Read from the choice class, so a code the database can never hold is refused at
        the boundary instead of quietly matching nothing."""
        with pytest.raises(ServiceReportFilterError) as raised:
            ServiceLogFilterSet.from_params({"settled_routes": "invented_route"})

        assert raised.value.code == "INVALID_FILTER_VALUE"

    def test_an_inverted_competence_range_is_refused(self):
        with pytest.raises(ServiceReportFilterError) as raised:
            ServiceLogFilterSet(competence_from=(2026, 6), competence_to=(2026, 3))

        assert raised.value.code == "COMPETENCE_RANGE_IS_INVERTED"

    def test_an_unknown_basis_is_refused(self):
        with pytest.raises(ServiceReportFilterError) as raised:
            ServiceLogFilterSet(competence_basis="whenever")

        assert raised.value.code == "INVALID_COMPETENCE_BASIS"

    def test_a_descriptor_cannot_be_edited_after_the_fact(self):
        """Frozen on purpose: a descriptor is a record of what was already summed, and one
        that could be edited between producing the number and producing the list is the
        divergence D50 exists to prevent."""
        descriptor = ServiceLogFilterSet()

        with pytest.raises(Exception):
            descriptor.competence_basis = CompetenceBasis.DEBITED_PERIOD


class TestCompetenceBasisSelectsDifferentRows:
    """**D48 against real rows.** The two bases are not interchangeable, and D31 is why."""

    def test_the_worked_on_basis_puts_the_retroactive_log_in_february(self, scenario):
        february = ServiceLogFilterSet(
            competence_basis=CompetenceBasis.WORKED_ON,
            competence_from=(2026, 2),
            competence_to=(2026, 2),
        )

        rows = february.queryset(scenario["workspace_id"])

        assert [row.pk for row in rows] == [scenario["retroactive"].pk]

    def test_the_period_basis_puts_it_in_march_with_the_others(self, scenario):
        """The same row, a different month, and **this** is the one a contract dashboard
        must use: the period is the competency, D31's clamp included."""
        march = ServiceLogFilterSet(
            competence_basis=CompetenceBasis.DEBITED_PERIOD,
            competence_from=(2026, 3),
            competence_to=(2026, 3),
        )

        rows = march.queryset(scenario["workspace_id"])

        assert set(rows.values_list("pk", flat=True)) == {
            scenario["retroactive"].pk,
            scenario["ordinary"].pk,
        }

    def test_the_period_basis_finds_nothing_in_february(self, scenario):
        """The complement, and the assertion that would fail if someone "simplified"
        ``competence_basis`` away: February has no period at all, so a contract consumption
        chart must show nothing there -- not two retroactive hours."""
        february = ServiceLogFilterSet(
            competence_basis=CompetenceBasis.DEBITED_PERIOD,
            competence_from=(2026, 2),
            competence_to=(2026, 2),
        )

        assert list(february.queryset(scenario["workspace_id"])) == []

    def test_a_range_crossing_a_year_boundary_works_on_the_period_basis(self, scenario, actor):
        """Where comparing ``(year, month)`` pairs naively would break."""
        january = resolve_period(scenario["contract"], date(2026, 12, 1), actor=actor)
        assert (january.competence_year, january.competence_month) == (2026, 12)

        window = ServiceLogFilterSet(
            competence_basis=CompetenceBasis.DEBITED_PERIOD,
            competence_from=(2026, 11),
            competence_to=(2027, 2),
        )

        # No logs in that window, but the important part is that the *query* is valid and
        # excludes March rather than erroring or matching everything.
        assert list(window.queryset(scenario["workspace_id"])) == []


class TestNarrowing:
    def test_narrowing_to_a_period_switches_the_basis_and_clears_the_window(self, scenario):
        """**The invariant that keeps criterion 8 honest.**

        A dashboard filtered to March narrows into a period bucket. If the ``worked_on``
        window survived, the drill-down would exclude the February entry that the aggregate
        counted, and the list would come up two hours short of the number above it.
        """
        dashboard = ServiceLogFilterSet(
            competence_basis=CompetenceBasis.WORKED_ON,
            competence_from=(2026, 3),
            competence_to=(2026, 3),
        )

        bucket = dashboard.narrow(debited_period_ids=(scenario["march"].pk,))

        assert bucket.competence_basis == CompetenceBasis.DEBITED_PERIOD
        assert bucket.competence_from is None and bucket.competence_to is None

        assert set(bucket.queryset(scenario["workspace_id"]).values_list("pk", flat=True)) == {
            scenario["retroactive"].pk,
            scenario["ordinary"].pk,
        }, "the retroactive entry must survive the narrowing"

    def test_the_unnarrowed_window_would_have_missed_it(self, scenario):
        """The control that gives the test above its meaning: with the ``worked_on`` window
        left in place, the same period bucket returns one row instead of two."""
        naive = ServiceLogFilterSet(
            competence_basis=CompetenceBasis.WORKED_ON,
            competence_from=(2026, 3),
            competence_to=(2026, 3),
            debited_period_ids=(scenario["march"].pk,),
        )

        assert [row.pk for row in naive.queryset(scenario["workspace_id"])] == [
            scenario["ordinary"].pk
        ]

    def test_narrowing_preserves_the_other_filters(self, scenario):
        """Narrowing adds a constraint; it must not reset the dashboard's own selection."""
        dashboard = ServiceLogFilterSet(
            service_client_ids=(scenario["client"].id,),
            settled_routes=("debit_pool",),
        )

        bucket = dashboard.narrow(hour_type_ids=(scenario["hour_type"].id,))

        assert bucket.service_client_ids == (scenario["client"].id,)
        assert bucket.settled_routes == ("debit_pool",)
        assert bucket.hour_type_ids == (scenario["hour_type"].id,)

    def test_narrowing_returns_a_new_descriptor(self, scenario):
        dashboard = ServiceLogFilterSet(competence_from=(2026, 3), competence_to=(2026, 3))

        bucket = dashboard.narrow(debited_period_ids=(scenario["march"].pk,))

        assert dashboard.competence_from == (2026, 3), "the original must be untouched"
        assert bucket is not dashboard

    def test_an_explicit_override_beats_the_period_default(self, scenario):
        """``setdefault``, not assignment: a caller that genuinely wants both can say so,
        which matters for an export of "what this period consumed *during* March"."""
        bucket = ServiceLogFilterSet().narrow(
            debited_period_ids=(scenario["march"].pk,),
            competence_basis=CompetenceBasis.WORKED_ON,
            competence_from=(2026, 3),
            competence_to=(2026, 3),
        )

        assert bucket.competence_basis == CompetenceBasis.WORKED_ON
        assert bucket.competence_from == (2026, 3)


class TestScalarFilters:
    def test_the_client_filter_reaches_through_the_project(self, scenario):
        """D19: a work log has no client column, the client hangs off the project."""
        mine = ServiceLogFilterSet(service_client_ids=(scenario["client"].id,))
        assert mine.queryset(scenario["workspace_id"]).count() == 2

        other = ServiceClientFactory(name="Outro", workspace=scenario["client"].workspace)
        theirs = ServiceLogFilterSet(service_client_ids=(other.id,))
        assert theirs.queryset(scenario["workspace_id"]).count() == 0

    def test_without_pool_origin_separates_ad_hoc_from_debited_work(self, scenario):
        """A route filter alone cannot do this: a non-billable log also debits nothing, so
        "billed ad-hoc" and "debited nowhere" are different populations."""
        assert (
            ServiceLogFilterSet(without_pool_origin=True)
            .queryset(scenario["workspace_id"])
            .count()
            == 0
        ), "both fixture logs debit a period"

        assert (
            ServiceLogFilterSet().queryset(scenario["workspace_id"]).count() == 2
        ), "positive control"

    def test_the_workspace_is_an_argument_and_not_a_field(self, scenario):
        """Tenancy is decided by the view from the URL, never by a descriptor that travelled
        through the browser. This is a design assertion, and it is cheap to keep."""
        assert "workspace_id" not in ServiceLogFilterSet().to_params()

        with pytest.raises(TypeError):
            ServiceLogFilterSet().queryset()

    def test_a_descriptor_can_be_applied_to_a_caller_supplied_queryset(self, scenario):
        """So an aggregation can pre-narrow with ``select_related`` or a role projection and
        still go through the one filter implementation."""
        base = ServiceLog.objects.filter(
            workspace_id=scenario["workspace_id"], worked_on=date(2026, 3, 5)
        )

        rows = ServiceLogFilterSet().queryset(scenario["workspace_id"], queryset=base)

        assert [row.pk for row in rows] == [scenario["ordinary"].pk]
