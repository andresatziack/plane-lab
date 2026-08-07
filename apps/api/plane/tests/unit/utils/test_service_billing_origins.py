# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""The revenue classification, proved over its **whole** input space. Decision D49.

Phase 9 needs the origin rule in SQL, to aggregate a time series in the database instead
of iterating work logs in Python. That means two implementations of one rule that decides
money, which is normally indefensible -- it is the "quatro relatórios, quatro regras"
failure ``revenue_origin_of`` was written to prevent.

It is defensible here for a reason that has to be **checked, not assumed**: the input space
is finite and small enough to enumerate completely.

* ``settled_billing_route`` -- 3 values
* ``route_deviation_reason`` -- ``None`` plus 4
* ``pricing_failure_reason`` -- ``None`` plus 3
* the project has a client -- 2
* that client holds a contract covering the competency -- 2

The check constraints on ``ServiceLog`` then cut the product down sharply, because most
combinations are **unrepresentable**: D33's biconditional allows a deviation only on
``applied=debit_pool, settled=bill_amount``, and a pricing failure only on a billed row
with no rate. This module enumerates the survivors, builds each one, and asserts the two
implementations agree.

**So this is a proof over the input space rather than a sample.** And the count is asserted,
so adding a value to any of those enumerations makes the test fail by arithmetic -- which is
the point: a new deviation reason must not be able to slip past the SQL expression just
because nobody thought to write an example for it.
"""

# Python imports
from datetime import date
from decimal import Decimal
from itertools import product

# Third party imports
import pytest

# Module imports
from plane.db.models import (
    INTERNAL_WORK_FAILURES,
    PRICING_PENDENCY_FAILURES,
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
from plane.utils.service_billing import (
    ReportBucket,
    RevenueOrigin,
    report_bucket_expression,
    report_bucket_of,
)

pytestmark = pytest.mark.unit

_POOL = ServiceBillingType.BillingRoute.DEBIT_POOL
_BILL = ServiceBillingType.BillingRoute.BILL_AMOUNT
_FREE = ServiceBillingType.BillingRoute.NON_BILLABLE

ROUTES = (_POOL, _BILL, _FREE)
DEVIATIONS = (None,) + tuple(ServiceRouteDeviation.values)
FAILURES = (None,) + tuple(ServicePricingFailure.values)

#: ``(has_client, client_has_contract)``. Three, not four: a project with no client cannot
#: have a client that holds a contract, so that fourth pair is not a combination the
#: database can represent either.
CLIENT_FACTS = ((True, True), (True, False), (False, False))


def is_representable(route, deviation, failure):
    """Whether ``ServiceLog``'s check constraints permit this combination at all.

    Restated here **on purpose**, as an independent reading of the DDL rather than an import
    of it. The enumeration below asserts this prediction against what the database actually
    accepts, so a disagreement between this function and the constraints is itself a
    finding -- either the constraint drifted or my reading of it is wrong, and both are
    worth a red test.
    """
    if deviation is not None:
        # `service_log_route_deviation_is_coherent`, D33 as a biconditional: the only legal
        # deviation is "chose the pool, was billed in reais".
        if route != _BILL:
            return False

    if failure is not None:
        # `service_log_pricing_failure_means_no_amount`: a failure only exists where there
        # was money to make, i.e. a billed row with no rate and no amount.
        if route != _BILL:
            return False

    return True


@pytest.fixture(autouse=True)
def no_celery_dispatch(monkeypatch):
    monkeypatch.setattr("celery.app.task.Task.apply_async", lambda *args, **kwargs: None)


@pytest.fixture
def world(db):
    """One workspace with two clients -- one under contract, one not -- and a client-less
    project for the internal-work case.

    The contract's vigency is asserted to cover the competency every row below uses. A
    fixture whose contract silently missed the month would make every
    ``OUT_OF_SCOPE_LOG`` expectation collapse into ``STANDALONE_LOG`` and the differential
    would still pass, having compared two implementations on half the space.
    """
    actor = UserFactory()

    contracted = ServiceClientFactory(name="Com contrato")
    bare = ServiceClientFactory(name="Sem contrato", workspace=contracted.workspace)
    workspace = contracted.workspace

    contract = ServiceContractFactory(
        service_client=contracted,
        code="DIF-1",
        monthly_hours=Decimal("40.0000"),
        starts_on=date(2026, 3, 1),
        ends_on=date(2026, 12, 31),
    )
    assert contract.covers(date(2026, 3, 10)), "the contract must cover the competency used below"

    return {
        "workspace": workspace,
        "actor": actor,
        "contracted_client": contracted,
        "bare_client": bare,
        "contracted_project": ProjectFactory(
            name="Com contrato", workspace=workspace, service_client=contracted
        ),
        "bare_project": ProjectFactory(
            name="Sem contrato", workspace=workspace, service_client=bare
        ),
        "internal_project": ProjectFactory(
            name="Interno", workspace=workspace, service_client=None
        ),
        "hour_type": ServiceHourTypeFactory(
            workspace=workspace, name="Comercial", multiplier=Decimal("1.00")
        ),
        "billing_type": ServiceBillingTypeFactory(
            workspace=workspace, name="Avulso", billing_route=_BILL
        ),
        "contracted_ids": {contracted.pk},
    }


def build_log(world, *, route, deviation, failure, has_client, client_has_contract):
    """One work log with exactly the classification inputs asked for, or ``None`` if the
    database refuses it.

    The monetary columns are set to whatever keeps ``service_log_amount_requires_a_rate``
    and ``service_log_pricing_failure_means_no_amount`` satisfied, because this module is
    about *classification* -- the amount is scaffolding here, and the money arithmetic has
    its own tests in Phase 6.
    """
    if has_client:
        project = (
            world["contracted_project"] if client_has_contract else world["bare_project"]
        )
    else:
        project = world["internal_project"]

    # D33's biconditional: a deviation means the row chose the pool and was billed anyway.
    applied = _POOL if deviation is not None else route

    priced = route == _BILL and failure is None

    from django.db import IntegrityError, transaction as db_transaction

    try:
        with db_transaction.atomic():
            return ServiceLogFactory(
                project=project,
                issue=IssueFactory(project=project),
                author=world["actor"],
                worked_on=date(2026, 3, 10),
                hour_type=world["hour_type"],
                billing_type=world["billing_type"],
                logged_hours=Decimal("1.0000"),
                equivalent_hours=Decimal("1.0000"),
                debited_hours=Decimal("0.0000") if route == _FREE else Decimal("1.0000"),
                applied_billing_route=applied,
                settled_billing_route=route,
                route_deviation_reason=deviation,
                pricing_failure_reason=failure,
                applied_hour_rate=Decimal("200.00") if priced else None,
                applied_rate_basis=ServiceRateBasis.BASE_MULTIPLIER if priced else None,
                amount=Decimal("200.00") if priced else Decimal("0.00"),
            )
    except IntegrityError:
        return None


def sql_bucket(log, contracted_ids):
    """What the database says this row's bucket is, through the annotation."""
    return (
        ServiceLog.objects.filter(pk=log.pk)
        .annotate(bucket=report_bucket_expression(contracted_ids))
        .values_list("bucket", flat=True)
        .first()
    )


class TestTheEnumerationItself:
    """Before comparing the implementations, establish that the space really is small and
    that the prediction of what is representable matches the database."""

    def test_the_input_space_is_the_size_the_decision_claims(self):
        """D49 rests on this number. If the space grows, the justification for having two
        implementations has to be re-argued rather than silently inherited."""
        combinations = [
            (route, deviation, failure, has_client, has_contract)
            for route, deviation, failure in product(ROUTES, DEVIATIONS, FAILURES)
            for has_client, has_contract in CLIENT_FACTS
            if is_representable(route, deviation, failure)
        ]

        assert len(DEVIATIONS) == 5, "None plus four deviation reasons"
        assert len(FAILURES) == 4, "None plus three pricing failure reasons"
        assert len(combinations) == 66, (
            f"the representable space changed to {len(combinations)}; D49's argument that "
            "the duplication is safe depends on this being exhaustively enumerable"
        )

    def test_every_combination_predicted_representable_really_is(self, world):
        """And every one predicted impossible really is refused. This is what makes
        ``is_representable`` a reading of the constraints rather than a guess -- and it means
        a constraint someone loosens shows up here instead of silently widening the space
        the differential covers.
        """
        mispredicted = []

        for route, deviation, failure in product(ROUTES, DEVIATIONS, FAILURES):
            for has_client, has_contract in CLIENT_FACTS:
                predicted = is_representable(route, deviation, failure)
                built = build_log(
                    world,
                    route=route,
                    deviation=deviation,
                    failure=failure,
                    has_client=has_client,
                    client_has_contract=has_contract,
                )
                actual = built is not None

                if predicted != actual:
                    mispredicted.append(
                        (route, deviation, failure, has_client, predicted, actual)
                    )

        assert mispredicted == []


class TestTheTwoImplementationsAgreeEverywhere:
    """**The differential, over all 66 representable combinations.** D49."""

    def test_python_and_sql_classify_every_representable_row_identically(self, world):
        checked = 0
        disagreements = []

        for route, deviation, failure in product(ROUTES, DEVIATIONS, FAILURES):
            for has_client, has_contract in CLIENT_FACTS:
                log = build_log(
                    world,
                    route=route,
                    deviation=deviation,
                    failure=failure,
                    has_client=has_client,
                    client_has_contract=has_contract,
                )

                if log is None:
                    continue

                expected = report_bucket_of(
                    log,
                    client_has_contract=has_contract,
                    has_client=has_client,
                )
                actual = sql_bucket(log, world["contracted_ids"])

                checked += 1

                if expected != actual:
                    disagreements.append(
                        {
                            "route": route,
                            "deviation": deviation,
                            "failure": failure,
                            "has_client": has_client,
                            "has_contract": has_contract,
                            "python": expected,
                            "sql": actual,
                        }
                    )

        assert disagreements == []
        assert checked == 66, (
            f"only {checked} combinations were actually compared; a differential that "
            "silently covers less than the whole space is a sample wearing a proof's clothes"
        )

    def test_every_bucket_is_reached_by_at_least_one_combination(self, world):
        """The positive control the differential needs. Two implementations that both
        returned ``None`` for everything would agree perfectly and prove nothing.

        Every one of the six outcomes has to be produced by some row, and asserting the
        *set* means an outcome that becomes unreachable shows up here rather than as a
        column that is quietly always empty.
        """
        reached = set()

        for route, deviation, failure in product(ROUTES, DEVIATIONS, FAILURES):
            for has_client, has_contract in CLIENT_FACTS:
                log = build_log(
                    world,
                    route=route,
                    deviation=deviation,
                    failure=failure,
                    has_client=has_client,
                    client_has_contract=has_contract,
                )
                if log is None:
                    continue

                reached.add(sql_bucket(log, world["contracted_ids"]))

        assert reached == {
            None,  # ReportBucket.NOT_REVENUE
            ReportBucket.INTERNAL_WORK,
            ReportBucket.REGISTRATION_PENDENCY,
            RevenueOrigin.STANDALONE_LOG,
            RevenueOrigin.OUT_OF_SCOPE_LOG,
        }, (
            "the two overage origins are ledger rows, not work logs, so they are "
            "unreachable here by construction -- see D56"
        )


class TestTheDispatchOrderIsTheOneTheRuleRequires:
    """The three orderings in ``report_bucket_of`` that are decisions rather than style.
    Each has a row that would be filed differently if the order changed."""

    def test_a_client_less_project_with_a_missing_sheet_is_internal_not_a_pendency(self, world):
        """Internal work is checked **before** pendency. A client-less project has no price
        sheet by definition, and filing it as a pendency asks somebody to register a sheet
        for a client that does not exist."""
        log = build_log(
            world,
            route=_BILL,
            deviation=None,
            failure=ServicePricingFailure.NO_PRICE_SHEET_FOR_CLIENT,
            has_client=False,
            client_has_contract=False,
        )

        assert log is not None
        assert report_bucket_of(log, client_has_contract=False, has_client=False) == (
            ReportBucket.INTERNAL_WORK
        )
        assert sql_bucket(log, world["contracted_ids"]) == ReportBucket.INTERNAL_WORK

    def test_a_pendency_is_not_filed_under_an_origin(self, world):
        """Pendency is checked **before** origin. A row with no resolvable price has no
        amount, so an origin bucket would gain a zero and a missing price sheet would look
        like completed work worth nothing."""
        log = build_log(
            world,
            route=_BILL,
            deviation=None,
            failure=ServicePricingFailure.NO_PRICE_SHEET_IN_FORCE,
            has_client=True,
            client_has_contract=True,
        )

        assert log is not None
        assert sql_bucket(log, world["contracted_ids"]) == ReportBucket.REGISTRATION_PENDENCY
        assert sql_bucket(log, world["contracted_ids"]) != RevenueOrigin.OUT_OF_SCOPE_LOG

    def test_a_deviation_beats_the_contract_and_stays_standalone(self, world):
        """D33, and the ordering that carries it: the client **does** hold a contract, but it
        could not absorb this work. That is ad-hoc-with-a-reason, never out of scope --
        out of scope means something deliberately sold outside the contract, which is a
        decision, and this is a lapse."""
        log = build_log(
            world,
            route=_BILL,
            deviation=ServiceRouteDeviation.CONTRACT_ENDED,
            failure=None,
            has_client=True,
            client_has_contract=True,
        )

        assert log is not None
        assert sql_bucket(log, world["contracted_ids"]) == RevenueOrigin.STANDALONE_LOG

    def test_the_same_row_without_the_deviation_is_out_of_scope(self, world):
        """The control for the test above: it is the deviation doing the work, not the
        client."""
        log = build_log(
            world,
            route=_BILL,
            deviation=None,
            failure=None,
            has_client=True,
            client_has_contract=True,
        )

        assert sql_bucket(log, world["contracted_ids"]) == RevenueOrigin.OUT_OF_SCOPE_LOG

    def test_a_pool_debit_is_not_revenue_whatever_else_is_true(self, world):
        """Route is checked first. A pool debit's money already moved as hours."""
        log = build_log(
            world,
            route=_POOL,
            deviation=None,
            failure=None,
            has_client=True,
            client_has_contract=True,
        )

        assert sql_bucket(log, world["contracted_ids"]) is None

    def test_a_non_billable_row_is_not_internal_work(self, world):
        """R5's distinction, and a place the two could plausibly have been conflated.
        Garantia on a client's project is **not** internal work -- it is real work for a real
        client that nobody is charged for, and folding it into internal hours would hide it
        from the client's own consumption report."""
        log = build_log(
            world,
            route=_FREE,
            deviation=None,
            failure=None,
            has_client=True,
            client_has_contract=True,
        )

        assert sql_bucket(log, world["contracted_ids"]) is None
        assert sql_bucket(log, world["contracted_ids"]) != ReportBucket.INTERNAL_WORK


class TestTheOracleStillDrivesTheConsolidation:
    """``consolidated_billing`` was refactored to call ``report_bucket_of``. This asserts the
    extraction really is the same dispatch, at the level that matters: the Phase 6 report."""

    def test_the_consolidation_and_the_expression_agree_on_every_row(self, world):
        """Every representable row, classified by the report and by SQL, must land in the
        same place. This is the join between D49's proof and the report a client is invoiced
        from."""
        from plane.utils.service_billing import consolidated_billing

        for route, deviation, failure in product(ROUTES, DEVIATIONS, FAILURES):
            build_log(
                world,
                route=route,
                deviation=deviation,
                failure=failure,
                has_client=True,
                client_has_contract=True,
            )

        report = consolidated_billing(world["workspace"].id, 2026, 3)

        by_sql = {}
        for log in ServiceLog.objects.filter(workspace_id=world["workspace"].id):
            bucket = sql_bucket(log, world["contracted_ids"])
            by_sql[bucket] = by_sql.get(bucket, 0) + 1

        client_entry = next(
            entry
            for entry in report["clients"]
            if entry["service_client_id"] == str(world["contracted_client"].pk)
        )

        for origin in (RevenueOrigin.STANDALONE_LOG, RevenueOrigin.OUT_OF_SCOPE_LOG):
            assert client_entry["origins"][origin]["entries"] == by_sql.get(origin, 0), (
                f"the consolidation and the SQL expression disagree on {origin}"
            )

        pendency_entries = sum(
            item["entries"] for item in client_entry["registration_pendencies"]
        )
        assert pendency_entries == by_sql.get(ReportBucket.REGISTRATION_PENDENCY, 0)
