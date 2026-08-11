# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""The aggregation layer. Criteria 1, 2, 3, 6, 7, 8, 10 and 13.

**The centre of this module is a property test, not a list of examples.** Every bucket of
every payload carries the descriptor that produced it (D50), so the test walks the payload,
replays each descriptor, and asserts the direct sum equals the bucket. A bucket added later
is covered by construction; a bucket whose descriptor lies fails immediately. That is
criterion 1 ("o gráfico bate com a soma") and criterion 8 ("clicar no total leva à lista")
proved as one property rather than as two features that might agree.

The scenario is built once and deliberately contains every awkward case at the same time:
a retroactive log clamped by D31, non-billable work of two different billing types, an
ad-hoc log on a client under contract, a log on a client with no contract, a pricing
pendency, and internal work on a client-less project. A fixture with only the easy rows
would let a broken aggregation pass.
"""

# Python imports
from datetime import date
from decimal import Decimal

# Third party imports
import pytest

# Module imports
from plane.db.models import (
    ServiceBillingType,
    ServiceLog,
    ServicePricingFailure,
    ServiceRateBasis,
    ServiceRouteDeviation,
)
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
from plane.tests.hour_display import hour_fields_missing_a_rendered_twin
from plane.utils.service_billing import RevenueOrigin
from plane.utils.service_pool import resolve_period
from plane.utils.service_reports import (
    ReportViewer,
    distribution,
    headline_totals,
    hours_series,
    non_billable_breakdown,
    revenue_series,
)
from plane.utils.service_reports_filters import (
    CompetenceBasis,
    ServiceLogFilterSet,
    ServiceReportFilterError,
)

pytestmark = pytest.mark.unit

_POOL = ServiceBillingType.BillingRoute.DEBIT_POOL
_BILL = ServiceBillingType.BillingRoute.BILL_AMOUNT
_FREE = ServiceBillingType.BillingRoute.NON_BILLABLE


@pytest.fixture(autouse=True)
def no_celery_dispatch(monkeypatch):
    monkeypatch.setattr("celery.app.task.Task.apply_async", lambda *args, **kwargs: None)


@pytest.fixture
def world(db):
    """One workspace with every awkward row shape present at once. See the module docstring."""
    actor = UserFactory()

    contracted = ServiceClientFactory(name="Terlogs")
    workspace = contracted.workspace
    bare = ServiceClientFactory(name="Marubeni", workspace=workspace)

    contract = ServiceContractFactory(
        service_client=contracted,
        code="TER-1",
        monthly_hours=Decimal("40.0000"),
        starts_on=date(2026, 3, 1),
        ends_on=date(2026, 12, 31),
    )

    contracted_project = ProjectFactory(
        name="Suporte Terlogs", workspace=workspace, service_client=contracted
    )
    bare_project = ProjectFactory(name="Marubeni", workspace=workspace, service_client=bare)
    internal_project = ProjectFactory(name="Interno", workspace=workspace, service_client=None)

    commercial = ServiceHourTypeFactory(
        workspace=workspace, name="Comercial", multiplier=Decimal("1.00")
    )
    sunday = ServiceHourTypeFactory(
        workspace=workspace, name="Domingos", multiplier=Decimal("2.00")
    )

    pool_type = ServiceBillingTypeFactory(
        workspace=workspace, name="Contrato", billing_route=_POOL
    )
    adhoc_type = ServiceBillingTypeFactory(workspace=workspace, name="Avulso", billing_route=_BILL)
    warranty = ServiceBillingTypeFactory(workspace=workspace, name="Garantia", billing_route=_FREE)
    courtesy = ServiceBillingTypeFactory(workspace=workspace, name="Cortesia", billing_route=_FREE)

    march = resolve_period(contract, date(2026, 3, 10), actor=actor)
    april = resolve_period(contract, date(2026, 4, 10), actor=actor)

    def log(**kwargs):
        project = kwargs.pop("project", contracted_project)
        equivalent = kwargs.pop("equivalent_hours", Decimal("1.0000"))
        route = kwargs.pop("settled_billing_route")
        priced = kwargs.pop("priced", False)

        return ServiceLogFactory(
            project=project,
            issue=kwargs.pop("issue", None) or IssueFactory(project=project),
            author=kwargs.pop("author", actor),
            hour_type=kwargs.pop("hour_type", commercial),
            billing_type=kwargs.pop("billing_type", pool_type),
            logged_hours=kwargs.pop("logged_hours", equivalent),
            equivalent_hours=equivalent,
            debited_hours=Decimal("0.0000") if route == _FREE else equivalent,
            applied_billing_route=kwargs.pop("applied_billing_route", route),
            settled_billing_route=route,
            applied_hour_rate=Decimal("200.00") if priced else None,
            applied_rate_basis=ServiceRateBasis.BASE_MULTIPLIER if priced else None,
            amount=kwargs.pop("amount", Decimal("200.00") if priced else Decimal("0.00")),
            **kwargs,
        )

    rows = {
        # D31: worked in February, debited to the MARCH period.
        "retroactive_pool": log(
            worked_on=date(2026, 2, 20),
            equivalent_hours=Decimal("2.0000"),
            settled_billing_route=_POOL,
            debited_period=march,
        ),
        "march_pool": log(
            worked_on=date(2026, 3, 5),
            equivalent_hours=Decimal("3.0000"),
            settled_billing_route=_POOL,
            debited_period=march,
        ),
        "april_pool": log(
            worked_on=date(2026, 4, 7),
            equivalent_hours=Decimal("5.0000"),
            settled_billing_route=_POOL,
            debited_period=april,
        ),
        # Out of scope: a client under contract, billed ad-hoc with no deviation.
        "out_of_scope": log(
            worked_on=date(2026, 3, 11),
            billing_type=adhoc_type,
            settled_billing_route=_BILL,
            priced=True,
        ),
        # Standalone: a client with no contract at all.
        "standalone_bare": log(
            project=bare_project,
            worked_on=date(2026, 3, 12),
            billing_type=adhoc_type,
            settled_billing_route=_BILL,
            priced=True,
        ),
        # Standalone by D33: chose the pool, billed anyway because the contract could not
        # absorb it.
        "deviated": log(
            worked_on=date(2026, 3, 13),
            billing_type=pool_type,
            applied_billing_route=_POOL,
            settled_billing_route=_BILL,
            route_deviation_reason=ServiceRouteDeviation.CONTRACT_ENDED,
            priced=True,
        ),
        # A pendency: billed, no price sheet, so no amount. Must never reach a revenue total.
        "pendency": log(
            worked_on=date(2026, 3, 14),
            billing_type=adhoc_type,
            settled_billing_route=_BILL,
            pricing_failure_reason=ServicePricingFailure.NO_PRICE_SHEET_IN_FORCE,
        ),
        # Internal work on a client-less project. Out of revenue entirely.
        "internal": log(
            project=internal_project,
            worked_on=date(2026, 3, 15),
            billing_type=adhoc_type,
            settled_billing_route=_BILL,
            pricing_failure_reason=ServicePricingFailure.INTERNAL_PROJECT_NO_CLIENT,
        ),
        # R5: two non-billable rows of DIFFERENT billing types, so a combined number is
        # visibly wrong.
        "warranty": log(
            worked_on=date(2026, 3, 16),
            billing_type=warranty,
            equivalent_hours=Decimal("4.0000"),
            settled_billing_route=_FREE,
        ),
        "courtesy": log(
            worked_on=date(2026, 3, 17),
            billing_type=courtesy,
            equivalent_hours=Decimal("1.5000"),
            settled_billing_route=_FREE,
        ),
        # A Sunday, so the multiplier makes logged and equivalent differ -- which is what
        # makes the GUEST projection meaningful.
        "sunday": log(
            worked_on=date(2026, 3, 18),
            hour_type=sunday,
            logged_hours=Decimal("1.0000"),
            equivalent_hours=Decimal("2.0000"),
            settled_billing_route=_POOL,
            debited_period=march,
        ),
    }

    assert rows["retroactive_pool"].debited_period_id == march.pk, "D31 must clamp"

    return {
        "workspace_id": workspace.id,
        "actor": actor,
        "contract": contract,
        "march": march,
        "april": april,
        "contracted": contracted,
        "bare": bare,
        "contracted_project": contracted_project,
        "commercial": commercial,
        "sunday_type": sunday,
        "warranty": warranty,
        "courtesy": courtesy,
        "rows": rows,
    }


def direct_sum(world, params, field):
    """The unaggregated truth: replay a descriptor and add the column up in Python.

    Deliberately **not** using the aggregation layer, because that is what is under test. The
    only shared code is the descriptor itself, which is the point.
    """
    filterset = ServiceLogFilterSet.from_params(params)
    rows = filterset.queryset(world["workspace_id"])

    return sum((getattr(row, field) for row in rows), Decimal("0.0000"))


def walk_buckets(payload):
    """Every dict in a payload that carries a ``filters`` key, however deeply nested.

    Written generically so a bucket added to a payload later is picked up by the property
    test without anybody remembering to extend the walker.
    """
    if isinstance(payload, dict):
        if "filters" in payload:
            yield payload
        for value in payload.values():
            yield from walk_buckets(value)
    elif isinstance(payload, list):
        for item in payload:
            yield from walk_buckets(item)


class TestEveryBucketAgreesWithItsOwnDescriptor:
    """**The property test.** Criteria 1 and 8, proved as one thing. D50."""

    @pytest.mark.parametrize("role", ["admin", "member", "guest"])
    def test_every_bucket_of_every_payload_reproduces_its_number(self, world, role):
        viewer = ReportViewer(role)
        window = ServiceLogFilterSet(competence_from=(2026, 2), competence_to=(2026, 4))
        period_window = ServiceLogFilterSet(
            competence_basis=CompetenceBasis.DEBITED_PERIOD,
            competence_from=(2026, 2),
            competence_to=(2026, 4),
        )

        payloads = [
            hours_series(world["workspace_id"], window, viewer),
            hours_series(world["workspace_id"], period_window, viewer),
            distribution(world["workspace_id"], window, viewer, dimension="hour_type"),
            distribution(world["workspace_id"], window, viewer, dimension="billing_type"),
            distribution(world["workspace_id"], window, viewer, dimension="author"),
            distribution(world["workspace_id"], window, viewer, dimension="service_client"),
            non_billable_breakdown(world["workspace_id"], window, viewer),
            headline_totals(world["workspace_id"], window, viewer),
            revenue_series(world["workspace_id"], window, viewer),
        ]

        checked = 0

        for payload in payloads:
            for entry in walk_buckets(payload):
                for field in viewer.hour_fields:
                    if field not in entry:
                        continue

                    expected = direct_sum(world, entry["filters"], field)

                    assert Decimal(entry[field]) == expected, (
                        f"bucket {entry.get('competence') or entry.get('filters')} claims "
                        f"{entry[field]} of {field} but its own descriptor sums to {expected}"
                    )
                    checked += 1

                if "amount" in entry:
                    expected = direct_sum(world, entry["filters"], "amount")
                    assert Decimal(entry["amount"]) == expected, (
                        "a money bucket disagrees with its own descriptor"
                    )
                    checked += 1

        assert checked > 30, (
            f"only {checked} bucket/field pairs were verified; the walker is probably not "
            "finding the buckets and the property test is passing vacuously"
        )

    def test_the_walker_really_finds_buckets(self, world):
        """The control the property test needs. A walker that yielded nothing would make
        every assertion above unreachable and the test would still be green."""
        series = hours_series(
            world["workspace_id"],
            ServiceLogFilterSet(competence_from=(2026, 2), competence_to=(2026, 4)),
            ReportViewer.admin(),
        )

        found = list(walk_buckets(series))

        assert len(found) == 3, "February, March and April all have work logs"
        assert all("filters" in entry for entry in found)


class TestD47TheTwoBasesDisagreeAndBothAreRight:
    """Criterion 7: a retroactive log appears in the right competency in **every** chart."""

    def test_contract_consumption_puts_the_retroactive_hours_in_march(self, world):
        series = hours_series(
            world["workspace_id"],
            ServiceLogFilterSet(
                competence_basis=CompetenceBasis.DEBITED_PERIOD,
                competence_from=(2026, 2),
                competence_to=(2026, 4),
            ),
            ReportViewer.admin(),
        )

        by_competence = {entry["competence"]: entry for entry in series}

        assert "2026-02" not in by_competence, (
            "February has no contract period at all, so a consumption chart must show "
            "nothing there -- not the two retroactive hours"
        )
        # 2h retroactive + 3h March + 2h Sunday equivalent = 7h in the March period.
        assert Decimal(by_competence["2026-03"]["equivalent_hours"]) == Decimal("7.0000")
        assert Decimal(by_competence["2026-04"]["equivalent_hours"]) == Decimal("5.0000")

    def test_the_worked_on_basis_puts_them_in_february(self, world):
        """The same hours, a different month, and this basis is the right one for ad-hoc
        revenue because R7 makes the service date the competency there."""
        series = hours_series(
            world["workspace_id"],
            ServiceLogFilterSet(
                competence_basis=CompetenceBasis.WORKED_ON,
                competence_from=(2026, 2),
                competence_to=(2026, 2),
            ),
            ReportViewer.admin(),
        )

        assert [entry["competence"] for entry in series] == ["2026-02"]
        assert Decimal(series[0]["equivalent_hours"]) == Decimal("2.0000")


class TestCriterion3AllowanceAndContractStaySeparate:
    def test_contract_consumption_counts_only_period_debited_work(self, world):
        """The structural guarantee: consumption is aggregated over ``debited_period``, so
        non-billable and ad-hoc rows cannot leak in even though they belong to the same
        client in the same month."""
        march_only = ServiceLogFilterSet(
            competence_basis=CompetenceBasis.DEBITED_PERIOD,
            competence_from=(2026, 3),
            competence_to=(2026, 3),
        )

        total = Decimal(
            hours_series(world["workspace_id"], march_only, ReportViewer.admin())[0][
                "equivalent_hours"
            ]
        )

        assert total == Decimal("7.0000")

        # The client's March work logs total far more than that -- the difference is exactly
        # the ad-hoc, non-billable and pendency rows that must NOT be contract consumption.
        everything = Decimal(
            headline_totals(
                world["workspace_id"],
                ServiceLogFilterSet(competence_from=(2026, 3), competence_to=(2026, 3)),
                ReportViewer.admin(),
            )["equivalent_hours"]
        )

        assert everything > total, "positive control: there is other work in March"


class TestCriterion2NonBillableIsNeverOneNumber:
    def test_the_breakdown_separates_warranty_from_courtesy(self, world):
        """R5. "Não faturável: 5,5h" would hide which conversation to have -- an obligation
        honoured and goodwill spent are different facts."""
        rows = non_billable_breakdown(
            world["workspace_id"],
            ServiceLogFilterSet(competence_from=(2026, 3), competence_to=(2026, 3)),
            ReportViewer.admin(),
        )

        by_name = {row["billing_type_name"]: row for row in rows}

        assert set(by_name) == {"Garantia", "Cortesia"}
        assert Decimal(by_name["Garantia"]["equivalent_hours"]) == Decimal("4.0000")
        assert Decimal(by_name["Cortesia"]["equivalent_hours"]) == Decimal("1.5000")

    def test_the_breakdown_excludes_billable_work(self, world):
        rows = non_billable_breakdown(
            world["workspace_id"],
            ServiceLogFilterSet(competence_from=(2026, 3), competence_to=(2026, 3)),
            ReportViewer.admin(),
        )

        assert "Avulso" not in {row["billing_type_name"] for row in rows}
        assert "Contrato" not in {row["billing_type_name"] for row in rows}


class TestRevenueSeries:
    def test_the_four_origins_are_always_present(self, world):
        """All four keys, even at zero. A missing key makes a frontend branch on absence and
        a zero origin become invisible rather than reported as nothing."""
        series = revenue_series(
            world["workspace_id"],
            ServiceLogFilterSet(competence_from=(2026, 3), competence_to=(2026, 3)),
            ReportViewer.admin(),
        )

        assert set(series[0]["origins"]) == {
            RevenueOrigin.STANDALONE_LOG,
            RevenueOrigin.OUT_OF_SCOPE_LOG,
            RevenueOrigin.CONTRACT_OVERAGE,
            RevenueOrigin.ALLOWANCE_OVERAGE,
        }

    def test_out_of_scope_and_standalone_are_split_by_the_contract(self, world):
        series = revenue_series(
            world["workspace_id"],
            ServiceLogFilterSet(competence_from=(2026, 3), competence_to=(2026, 3)),
            ReportViewer.admin(),
        )
        origins = series[0]["origins"]

        # Out of scope: the one ad-hoc log on the contracted client with no deviation.
        assert origins[RevenueOrigin.OUT_OF_SCOPE_LOG]["entries"] == 1
        assert origins[RevenueOrigin.OUT_OF_SCOPE_LOG]["amount"] == "200.00"

        # Standalone: the bare client's log plus the deviated one (D33).
        assert origins[RevenueOrigin.STANDALONE_LOG]["entries"] == 2
        assert origins[RevenueOrigin.STANDALONE_LOG]["amount"] == "400.00"

    def test_the_pendency_reaches_no_revenue_total(self, world):
        """Criterion 13's other half: a row that cannot be invoiced must not add a zero to a
        revenue column, because that makes a missing price sheet look like completed work
        worth nothing."""
        series = revenue_series(
            world["workspace_id"],
            ServiceLogFilterSet(competence_from=(2026, 3), competence_to=(2026, 3)),
            ReportViewer.admin(),
        )

        assert series[0]["total_amount"] == "600.00", "only the three priced logs"

        counted = sum(entry["entries"] for entry in series[0]["origins"].values())
        assert counted == 3, "the pendency and the internal row are both excluded"

    def test_internal_work_is_not_a_fifth_origin(self, world):
        series = revenue_series(
            world["workspace_id"],
            ServiceLogFilterSet(competence_from=(2026, 3), competence_to=(2026, 3)),
            ReportViewer.admin(),
        )

        assert "internal_work" not in series[0]["origins"]

    def test_the_two_overage_origins_carry_no_work_log_descriptor_at_all(self, world):
        """**D56, made structural.** A billed overage is the settlement of a deficit, so no
        list of work logs sums to it.

        The first version of this emitted a descriptor plus a flag saying "not really
        drillable", and the property test caught a bucket reporting zero hours while its
        descriptor selected every row in the month. A flag is something a frontend can forget
        to read; an absent key is not.
        """
        series = revenue_series(
            world["workspace_id"],
            ServiceLogFilterSet(competence_from=(2026, 3), competence_to=(2026, 3)),
            ReportViewer.admin(),
        )
        origins = series[0]["origins"]

        for origin in (RevenueOrigin.STANDALONE_LOG, RevenueOrigin.OUT_OF_SCOPE_LOG):
            assert "filters" in origins[origin]
            assert "drill_down" not in origins[origin]

        for origin, kind in (
            (RevenueOrigin.CONTRACT_OVERAGE, "contract_period_statement"),
            (RevenueOrigin.ALLOWANCE_OVERAGE, "allowance_statement"),
        ):
            assert "filters" not in origins[origin], "an overage bucket must offer no log list"
            assert origins[origin]["drill_down"] == {"kind": kind, "competence": "2026-03"}

    def test_a_bucket_cannot_have_both_or_neither_target(self, world):
        """The invariant enforced in ``bucket`` itself, so a future payload cannot reintroduce
        the shape the property test rejected."""
        from plane.utils.service_reports import bucket as make_bucket

        with pytest.raises(ValueError):
            make_bucket(viewer=ReportViewer.admin(), filters=None, drill_down=None)

        with pytest.raises(ValueError):
            make_bucket(
                viewer=ReportViewer.admin(),
                filters=ServiceLogFilterSet(),
                drill_down={"kind": "anything"},
            )

    def test_the_origin_descriptors_select_disjoint_sets_that_cover_the_revenue(self, world):
        """The two drillable descriptors must partition the billed rows, or a click would
        double-count or lose one."""
        series = revenue_series(
            world["workspace_id"],
            ServiceLogFilterSet(competence_from=(2026, 3), competence_to=(2026, 3)),
            ReportViewer.admin(),
        )
        origins = series[0]["origins"]

        def ids(origin):
            params = origins[origin]["filters"]
            return set(
                ServiceLogFilterSet.from_params(params)
                .queryset(world["workspace_id"])
                .values_list("pk", flat=True)
            )

        standalone = ids(RevenueOrigin.STANDALONE_LOG)
        out_of_scope = ids(RevenueOrigin.OUT_OF_SCOPE_LOG)

        assert standalone & out_of_scope == set(), "the two origins must be disjoint"
        assert standalone == {world["rows"]["standalone_bare"].pk, world["rows"]["deviated"].pk}
        assert out_of_scope == {world["rows"]["out_of_scope"].pk}

    def test_a_multi_month_origin_filter_is_refused_rather_than_approximated(self, world):
        """Whether a client held a contract is per-competency, so one client set cannot serve
        a range. Refusing beats returning a report that looks right."""
        with pytest.raises(ServiceReportFilterError) as raised:
            ServiceLogFilterSet(
                competence_from=(2026, 3),
                competence_to=(2026, 4),
                revenue_origins=(RevenueOrigin.STANDALONE_LOG,),
            )

        assert raised.value.code == "REVENUE_ORIGIN_NEEDS_ONE_COMPETENCE"


class TestD50TheRoleProjectionIsAppliedAtTheAggregationBoundary:
    """R11 and D57. The money is **never computed** for a viewer who may not see it, and the
    hour columns differ for a client. Absence, never zero."""

    def test_a_member_gets_hours_and_no_money_anywhere(self, world):
        window = ServiceLogFilterSet(competence_from=(2026, 3), competence_to=(2026, 3))
        viewer = ReportViewer.member()

        payloads = [
            hours_series(world["workspace_id"], window, viewer),
            distribution(world["workspace_id"], window, viewer, dimension="hour_type"),
            headline_totals(world["workspace_id"], window, viewer),
            revenue_series(world["workspace_id"], window, viewer),
        ]

        for payload in payloads:
            for entry in walk_buckets(payload):
                assert "amount" not in entry, "money leaked to a MEMBER"
                assert "amount_display" not in entry
                assert "average_amount_per_issue" not in entry

        assert "total_amount" not in revenue_series(world["workspace_id"], window, viewer)[0]

    def test_an_admin_does_get_it(self, world):
        """The positive control. Without it the test above would pass on an aggregation that
        returned nothing at all."""
        window = ServiceLogFilterSet(competence_from=(2026, 3), competence_to=(2026, 3))
        totals = headline_totals(world["workspace_id"], window, ReportViewer.admin())

        assert totals["amount"] == "600.00"
        assert totals["amount_display"] == "R$ 600,00"
        assert "average_amount_per_issue" in totals

    def test_a_member_keeps_all_three_hour_quantities(self, world):
        """R11 takes the price away, not the work. And D57: the technician still sees the
        commercial state, which is what lets them notice an expired contract."""
        window = ServiceLogFilterSet(competence_from=(2026, 3), competence_to=(2026, 3))
        totals = headline_totals(world["workspace_id"], window, ReportViewer.member())

        for field in ("logged_hours", "equivalent_hours", "debited_hours"):
            assert field in totals

    def test_a_guest_never_receives_logged_hours(self, world):
        """**The multiplier is the gap between logged and equivalent.** R11(c): showing that
        gap to a client under the wrong label turns a contractual rule into an accusation of
        inflated hours, so ``logged_hours`` is absent from every bucket."""
        window = ServiceLogFilterSet(competence_from=(2026, 3), competence_to=(2026, 3))
        viewer = ReportViewer.guest()

        payloads = [
            hours_series(world["workspace_id"], window, viewer),
            distribution(world["workspace_id"], window, viewer, dimension="hour_type"),
            headline_totals(world["workspace_id"], window, viewer),
        ]

        for payload in payloads:
            for entry in walk_buckets(payload):
                assert "logged_hours" not in entry, "the multiplier leaked to a GUEST"
                assert "logged_hours_display" not in entry
                assert "amount" not in entry

    def test_a_guest_does_get_equivalent_and_debited(self, world):
        """The positive control for the guest projection, and the reason the fixture has a
        Sunday log: logged 1h and equivalent 2h differ, so a projection that silently
        returned the wrong column would be visible."""
        window = ServiceLogFilterSet(competence_from=(2026, 3), competence_to=(2026, 3))
        totals = headline_totals(world["workspace_id"], window, ReportViewer.guest())

        assert "equivalent_hours" in totals
        assert "debited_hours" in totals
        assert Decimal(totals["equivalent_hours"]) > Decimal(totals["debited_hours"]), (
            "the non-billable rows make equivalent exceed debited, which is the point"
        )

    def test_an_unknown_role_is_refused(self):
        with pytest.raises(ValueError):
            ReportViewer("superuser")


class TestD52AveragesArePresentationOnly:
    def test_the_average_is_quantized_to_two_places_and_never_fed_back(self, world):
        """A money total divided by a count is not representable in two decimals. It is
        quantized once, here, and no total is derived from it."""
        totals = headline_totals(
            world["workspace_id"],
            ServiceLogFilterSet(competence_from=(2026, 3), competence_to=(2026, 3)),
            ReportViewer.admin(),
        )

        assert totals["average_amount_per_issue"].split(".")[1] == "00" or len(
            totals["average_amount_per_issue"].split(".")[1]
        ) == 2

        # The total is the SUM of persisted amounts, not the average times the count -- which
        # for 600.00 over 9 issues would not round-trip.
        assert totals["amount"] == "600.00"

    def test_the_hours_average_ships_rendered_and_is_the_least_round_hour_in_the_feature(
        self, world
    ):
        """D68 on the one quantity in this feature that is not block-aligned.

        Every other hour is a multiple of 0.25h, or a sum of such multiples times a
        two-decimal multiplier. A quotient by a count of work items is not: it lands wherever
        the division puts it, which is why ``format_hours_human`` needed a seconds term at all.
        ``operational-tab.tsx`` renders this card and used to print ``"0.4286"``.
        """
        totals = headline_totals(
            world["workspace_id"],
            ServiceLogFilterSet(competence_from=(2026, 3), competence_to=(2026, 3)),
            ReportViewer.member(),
        )

        raw = totals["average_equivalent_hours_per_issue"]
        assert raw == "1.7222", "equivalent hours over the work items that carried them"
        # 1.7222h is 6199.92s, and the renderer rounds the whole thing to the nearest second
        # (ROUND_HALF_UP) rather than giving up and printing "1,7222h".
        assert totals["average_equivalent_hours_per_issue_display"] == "1h 43min 20s"
        assert hour_fields_missing_a_rendered_twin(totals) == []

    def test_an_empty_selection_reports_no_average_rather_than_zero(self, world):
        """Dividing by no issues has no answer, and ``0,00`` would be one."""
        totals = headline_totals(
            world["workspace_id"],
            ServiceLogFilterSet(competence_from=(2020, 1), competence_to=(2020, 1)),
            ReportViewer.admin(),
        )

        assert totals["entries"] == 0
        assert "average_amount_per_issue" not in totals
        assert "average_equivalent_hours_per_issue" not in totals
        assert "average_equivalent_hours_per_issue_display" not in totals, (
            "no issues means no average, and a rendered '0min' would be an answer where there "
            "is none (D53)"
        )
        assert totals["amount"] == "0.00", "a total of nothing IS zero, unlike an average"


class TestDistributionDimensions:
    def test_an_unknown_dimension_is_refused_rather_than_passed_to_the_orm(self, world):
        """An arbitrary lookup would let a caller reach a money column for a Member, or any
        unrelated field. Same allowlist defence the export schema uses."""
        with pytest.raises(ValueError):
            distribution(
                world["workspace_id"],
                ServiceLogFilterSet(),
                ReportViewer.member(),
                dimension="project__service_client__notes",
            )

    def test_each_slice_descriptor_selects_only_its_own_slice(self, world):
        rows = distribution(
            world["workspace_id"],
            ServiceLogFilterSet(competence_from=(2026, 3), competence_to=(2026, 3)),
            ReportViewer.admin(),
            dimension="hour_type",
        )

        by_name = {row["hour_type_name"]: row for row in rows}
        sunday = by_name["Domingos"]

        selected = ServiceLogFilterSet.from_params(sunday["filters"]).queryset(
            world["workspace_id"]
        )

        assert {row.pk for row in selected} == {world["rows"]["sunday"].pk}
        assert Decimal(sunday["equivalent_hours"]) == Decimal("2.0000")
        assert Decimal(sunday["logged_hours"]) == Decimal("1.0000"), "the multiplier is visible"



class TestThePerformanceClaimIsMeasuredNotAsserted:
    """**Query counts, because "aggregated in the database" is a testable claim.**

    The phase requires totals to be aggregated by Postgres rather than by Python iterating
    work logs. A docstring saying so is worth nothing the moment somebody adds a
    ``select_related`` loop, so the guarantee is written as a bound that does not grow with
    the range.
    """

    def test_a_series_costs_the_same_number_of_queries_for_one_month_as_for_twelve(
        self, world, django_assert_num_queries
    ):
        viewer = ReportViewer.admin()

        one_month = ServiceLogFilterSet(competence_from=(2026, 3), competence_to=(2026, 3))
        twelve = ServiceLogFilterSet(competence_from=(2026, 1), competence_to=(2026, 12))

        with django_assert_num_queries(1):
            hours_series(world["workspace_id"], one_month, viewer)

        with django_assert_num_queries(1):
            hours_series(world["workspace_id"], twelve, viewer)

    def test_a_distribution_is_one_query(self, world, django_assert_num_queries):
        with django_assert_num_queries(1):
            distribution(
                world["workspace_id"],
                ServiceLogFilterSet(competence_from=(2026, 1), competence_to=(2026, 12)),
                ReportViewer.admin(),
                dimension="hour_type",
            )

    def test_headline_totals_are_one_query(self, world, django_assert_num_queries):
        with django_assert_num_queries(1):
            headline_totals(
                world["workspace_id"],
                ServiceLogFilterSet(competence_from=(2026, 1), competence_to=(2026, 12)),
                ReportViewer.admin(),
            )

    def test_the_revenue_series_is_a_fixed_four_queries_whatever_the_range(
        self, world, django_assert_num_queries
    ):
        """One scan of the work logs, one contract read for the per-competency coverage sets,
        and one per overage origin. **Four regardless of how many months are asked for** --
        which is the whole point of grouping by the classification inputs instead of running
        the classification per month.
        """
        for window in (
            ServiceLogFilterSet(competence_from=(2026, 3), competence_to=(2026, 3)),
            ServiceLogFilterSet(competence_from=(2024, 1), competence_to=(2026, 12)),
        ):
            with django_assert_num_queries(4):
                revenue_series(world["workspace_id"], window, ReportViewer.admin())

    def test_the_contract_statement_no_longer_costs_one_query_per_period(
        self, world, django_assert_num_queries, actor
    ):
        """The N+1 Phase 9 inherited. ``contract_balance_statement`` walked the periods calling
        ``period_parcels`` on each, so a 36 month contract cost 37 round trips -- invisible on
        a single contract's detail page and the spine of this phase's dashboard.

        Asserted as **two queries for a contract with many periods**, which is the shape that
        cannot regress quietly: a reintroduced loop changes the number.
        """
        from plane.utils.service_pool import contract_balance_statement, resolve_period

        contract = world["contract"]

        # From March, because that is where the vigency starts. Asking for January would not
        # add a period -- D31 clamps it onto the first one, which is March and already exists.
        for month in range(3, 13):
            resolve_period(contract, date(2026, month, 15), actor=world["actor"])

        from plane.db.models import ServiceContractPeriod

        periods = ServiceContractPeriod.objects.filter(contract_id=contract.pk).count()
        assert periods == 10, f"March to December inclusive, got {periods}"

        with django_assert_num_queries(2):
            statement = contract_balance_statement(contract)

        assert len(statement) == 10, "positive control: it really did read every period"


@pytest.fixture
def actor(db):
    return UserFactory()
