# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contracts, monthly pools, carryover, overage and renewal.

Covers acceptance criteria 1 to 12 and 15 to 24 of the contract phase. Criterion 13 and
14 have homes of their own: 13 is exercised end to end through HTTP in
``contract/app/test_service_contract_app.py``, and 14 needs real threads and real
transactions, so it lives in ``test_service_pool_concurrency.py``.

**Every assertion of absence names the reason for the absence, and has a positive
control beside it in the same fixture.** That rule is not decoration here: "did not
debit" is produced by a NON_BILLABLE route, by a missing contract, and by a closed
period, and those are three opposite bugs with one symptom. A test that only checked the
balance had not moved would pass for all three, and for a debit engine that was never
wired up at all.
"""

# Python imports
from datetime import date
from decimal import Decimal

# Third party imports
import pytest

# Django imports
from django.db import IntegrityError
from django.db.models import Sum

# Module imports
from plane.db.models import (
    ServiceBillingType,
    ServiceContract,
    ServiceContractPeriod,
    ServiceHourLedgerEntry,
    ServiceLedgerEntryType,
    ServiceLog,
    ServiceOverageSettlement,
    ServicePeriodStatus,
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
from plane.tests.hour_display import hour_fields_missing_a_rendered_twin
from plane.utils.service_log import build_batch_rows, create_service_log_batch
from plane.utils.service_pool import (
    AMBIGUOUS_CONTRACT_RESOLUTION,
    CONTRACT_OUT_OF_VIGENCY,
    CONTRACT_PINNED_ON_PROJECT_BELONGS_TO_ANOTHER_CLIENT,
    CONTRACT_SUSPENDED,
    ISSUE_ALLOWANCE_REQUIRES_TARGET_ISSUE,
    NO_CONTRACT_FOR_CLIENT,
    NO_NEXT_PERIOD_FOR_DEFICIT,
    NO_SERVICE_CLIENT_FOR_PROJECT,
    PERIOD_ALREADY_CLOSED,
    PERIOD_IS_CLOSED,
    PERIOD_IS_CLOSED_FOR_CONTRACTED_HOURS,
    BalanceDestination,
    ServicePoolValidationError,
    annotate_period_balance,
    apply_debit,
    close_period,
    contract_balance_statement,
    end_and_create_successor,
    materialize_contract_periods,
    reconcile_period,
    remaining_parcels,
    renew_in_place,
    resolve_contract,
    resolve_period,
    reverse_debit,
    update_contracted_hours,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def no_celery_dispatch(monkeypatch):
    """Soft delete fires ``.delay``, and there is no broker in tests.

    Patched at ``Task.apply_async`` rather than per task, the same way
    ``test_service_log.py`` does it, so a new ``.delay`` added anywhere does not silently
    start needing a broker.
    """
    monkeypatch.setattr("celery.app.task.Task.apply_async", lambda *args, **kwargs: None)


# ---------------------------------------------------------------------------
# Fixtures -- seeded explicitly, and asserting the configuration they seed
# ---------------------------------------------------------------------------


@pytest.fixture
def actor(db):
    return UserFactory()


@pytest.fixture
def client_with_contract(db):
    """One client, one 30h/month contract for the 2026 calendar year, one project.

    Asserts the *specific* configuration the tests depend on rather than a count. "One
    contract" could be one for the wrong reasons; "30h/month, 2026-01-01 to 2026-12-31,
    no cap, no carryover expiry" is precisely what makes the arithmetic below land where
    the criteria say it should.
    """
    service_client = ServiceClientFactory(name="Marubeni")
    contract = ServiceContractFactory(
        service_client=service_client,
        code="SUP-001",
        name="Suporte",
        monthly_hours=Decimal("30.0000"),
        starts_on=date(2026, 1, 1),
        ends_on=date(2026, 12, 31),
        # Phase 6: billing an overage now writes a value, and refuses without a rate that
        # resolves. R$ 250,00/h is the figure the pricing brief's acceptance criterion 7
        # uses, so a 3h deficit here bills exactly the R$ 750,00 that criterion names.
        overage_hour_rate=Decimal("250.00"),
    )
    project = ProjectFactory(
        name="Marubeni", workspace=service_client.workspace, service_client=service_client
    )
    project.is_time_tracking_enabled = True
    project.save()

    assert contract.monthly_hours == Decimal("30.0000")
    assert (contract.starts_on, contract.ends_on) == (date(2026, 1, 1), date(2026, 12, 31))
    assert contract.overage_hour_rate == Decimal("250.00"), "billing an overage needs a rate"
    assert contract.carryover_months is None, "these tests assume the balance never expires"
    assert contract.accrual_cap_mode == ServiceContract.AccrualCapMode.NONE
    assert contract.is_default is False, "a single contract must resolve without needing the flag"

    return {"client": service_client, "contract": contract, "project": project}


@pytest.fixture
def catalog(db, client_with_contract):
    """A 1.0 pool route, a 2.0 pool route, and a non-billable route.

    Three routes, not one, because the positive control for every "did not debit" test
    below is a *different route on the same fixture*. Without the billable pair in the
    same place, a suite where nothing debits is indistinguishable from one that works.
    """
    workspace = client_with_contract["client"].workspace

    normal = ServiceHourTypeFactory(
        workspace=workspace, name="Horario comercial", multiplier=Decimal("1.00")
    )
    double = ServiceHourTypeFactory(
        workspace=workspace, name="Domingos e feriados", multiplier=Decimal("2.00")
    )
    pool_route = ServiceBillingTypeFactory(
        workspace=workspace,
        name="Contrato",
        billing_route=ServiceBillingType.BillingRoute.DEBIT_POOL,
    )
    warranty = ServiceBillingTypeFactory(
        workspace=workspace,
        name="Garantia",
        billing_route=ServiceBillingType.BillingRoute.NON_BILLABLE,
    )

    assert normal.multiplier == Decimal("1.00")
    assert double.multiplier == Decimal("2.00")
    assert pool_route.billing_route == ServiceBillingType.BillingRoute.DEBIT_POOL
    assert warranty.billing_route == ServiceBillingType.BillingRoute.NON_BILLABLE

    return {"normal": normal, "double": double, "pool": pool_route, "warranty": warranty}


def log_hours(*, project, actor, catalog, minutes, worked_on, hour_type=None, billing_type=None):
    """Create a work log through the real pipeline and debit it.

    Goes through ``build_batch_rows`` rather than ``ServiceLogFactory`` so the hour
    arithmetic and the R4 snapshots are the ones production writes -- a factory-built row
    could carry hand-written numbers that the multiplier never actually produced.
    """
    issue = IssueFactory(project=project)
    rows = build_batch_rows(
        issue=issue,
        author=actor,
        description="Atendimento",
        billing_type=billing_type or catalog["pool"],
        hour_type=hour_type or catalog["normal"],
        segments=[
            type(
                "Seg",
                (),
                {
                    "worked_on": worked_on,
                    "start_time": None,
                    "end_time": None,
                    "raw_duration_minutes": minutes,
                    "suggested_hour_type": None,
                    "reason": "",
                },
            )()
        ],
    )
    create_service_log_batch(rows)

    for row in rows:
        apply_debit(row, actor=actor)

    return rows


# ---------------------------------------------------------------------------
# Criterion 1 -- period generation
# ---------------------------------------------------------------------------


class TestPeriodGeneration:
    def test_a_twelve_month_contract_generates_twelve_periods(self, client_with_contract, actor):
        """Criterion 1, and it asserts the *bounds* as well as the count.

        Twelve periods could be twelve for the wrong reasons -- off by a month at either
        end and the count is unchanged. January's and December's real bounds are what
        prove the vigency drove the generation.
        """
        contract = client_with_contract["contract"]

        periods = materialize_contract_periods(contract, actor=actor)

        assert len(periods) == 12
        assert [(p.competence_year, p.competence_month) for p in periods] == [
            (2026, month) for month in range(1, 13)
        ]
        assert (periods[0].starts_on, periods[0].ends_on) == (date(2026, 1, 1), date(2026, 1, 31))
        assert (periods[-1].starts_on, periods[-1].ends_on) == (date(2026, 12, 1), date(2026, 12, 31))
        assert all(p.contracted_hours == Decimal("30.0000") for p in periods)

    def test_generation_is_idempotent(self, client_with_contract, actor):
        contract = client_with_contract["contract"]

        materialize_contract_periods(contract, actor=actor)
        materialize_contract_periods(contract, actor=actor)

        assert ServiceContractPeriod.objects.filter(contract=contract).count() == 12

    def test_a_partial_first_month_is_clipped_to_the_vigency(self, db, actor):
        """A contract starting on the 10th gets a period that starts on the 10th.

        Which is the fact decision B1 then lets an admin price, instead of the system
        inventing a pro-rata rule nobody wrote down.
        """
        contract = ServiceContractFactory(
            code="PART-1", starts_on=date(2026, 3, 10), ends_on=date(2026, 4, 20)
        )

        periods = materialize_contract_periods(contract, actor=actor)

        assert (periods[0].starts_on, periods[0].ends_on) == (date(2026, 3, 10), date(2026, 3, 31))
        assert (periods[1].starts_on, periods[1].ends_on) == (date(2026, 4, 1), date(2026, 4, 20))

    def test_materialising_a_period_grants_its_quota_in_the_ledger(self, client_with_contract, actor):
        """The GRANT row is what makes the pool exist. Without it the period would show
        30h of balance that no movement accounts for, and reconciliation would say so."""
        period = resolve_period(client_with_contract["contract"], date(2026, 1, 15), actor=actor)

        grants = ServiceHourLedgerEntry.objects.filter(
            period=period, entry_type=ServiceLedgerEntryType.GRANT
        )

        assert grants.count() == 1
        assert grants.first().hours == Decimal("30.0000")
        assert grants.first().origin_period_id == period.pk
        assert reconcile_period(period)["is_consistent"]


# ---------------------------------------------------------------------------
# Criteria 2, 3, 4 -- the debit engine
# ---------------------------------------------------------------------------


class TestDebit:
    def test_two_hours_at_one_times_leaves_twenty_eight(self, client_with_contract, catalog, actor):
        """Criterion 2."""
        log_hours(
            project=client_with_contract["project"],
            actor=actor,
            catalog=catalog,
            minutes=120,
            worked_on=date(2026, 1, 15),
        )

        period = resolve_period(client_with_contract["contract"], date(2026, 1, 15))

        assert period.consumed_hours == Decimal("2.0000")
        assert period.balance_hours == Decimal("28.0000")
        assert reconcile_period(period)["is_consistent"]

    def test_two_hours_at_two_times_takes_four_not_two(self, client_with_contract, catalog, actor):
        """Criterion 3. The pool is debited in equivalent hours, so the multiplier is
        what leaves the pool -- not the chronological time."""
        log_hours(
            project=client_with_contract["project"],
            actor=actor,
            catalog=catalog,
            minutes=120,
            worked_on=date(2026, 1, 15),
            hour_type=catalog["double"],
        )

        period = resolve_period(client_with_contract["contract"], date(2026, 1, 15))

        assert period.consumed_hours == Decimal("4.0000")
        assert period.balance_hours == Decimal("26.0000")

    def test_a_non_billable_route_does_not_move_the_balance_and_says_why(
        self, client_with_contract, catalog, actor
    ):
        """Criterion 4, WITH its reason and WITH a positive control.

        The absence is asserted three ways, because "the balance did not move" alone
        would also be true if the debit engine were never called: the route is recorded
        as NON_BILLABLE, ``debited_hours`` is zero by check constraint, and **no DEBIT
        row exists**. The positive control in the same fixture is the billable log
        immediately below, which does move it.
        """
        rows = log_hours(
            project=client_with_contract["project"],
            actor=actor,
            catalog=catalog,
            minutes=120,
            worked_on=date(2026, 1, 15),
            billing_type=catalog["warranty"],
        )

        period = resolve_period(client_with_contract["contract"], date(2026, 1, 15))

        assert rows[0].applied_billing_route == ServiceBillingType.BillingRoute.NON_BILLABLE
        assert rows[0].debited_hours == Decimal("0.0000")
        assert not ServiceHourLedgerEntry.objects.filter(
            service_log=rows[0], entry_type=ServiceLedgerEntryType.DEBIT
        ).exists()
        assert period.consumed_hours == Decimal("0.0000")
        assert rows[0].logged_hours == Decimal("2.0000"), "the work still counts as time worked"

        # POSITIVE CONTROL, same fixture: a billable log does move it.
        log_hours(
            project=client_with_contract["project"],
            actor=actor,
            catalog=catalog,
            minutes=120,
            worked_on=date(2026, 1, 15),
        )

        period.refresh_from_db()
        assert period.consumed_hours == Decimal("2.0000")

    def test_a_backdated_log_debits_the_backdated_month(self, client_with_contract, catalog, actor):
        """Criterion 10, and rule R7. Asserts BOTH months, because "March moved" is only
        half the claim -- the other half is that the current month did not."""
        log_hours(
            project=client_with_contract["project"],
            actor=actor,
            catalog=catalog,
            minutes=120,
            worked_on=date(2026, 3, 20),
        )

        march = resolve_period(client_with_contract["contract"], date(2026, 3, 1))
        april = resolve_period(client_with_contract["contract"], date(2026, 4, 1))

        assert march.consumed_hours == Decimal("2.0000")
        assert april.consumed_hours == Decimal("0.0000")

    def test_the_debited_period_is_snapshotted_on_the_work_log(
        self, client_with_contract, catalog, actor
    ):
        """R4. "Which pool paid for this hour" cannot be re-derived later, because the
        resolution depends on configuration that may since have changed."""
        rows = log_hours(
            project=client_with_contract["project"],
            actor=actor,
            catalog=catalog,
            minutes=60,
            worked_on=date(2026, 2, 10),
        )

        period = resolve_period(client_with_contract["contract"], date(2026, 2, 1))
        stored = ServiceLog.objects.get(pk=rows[0].pk)

        assert stored.debited_period_id == period.pk

    def test_a_second_debit_of_the_same_log_is_a_no_op(self, client_with_contract, catalog, actor):
        """Idempotency, guaranteed by the partial unique index rather than by a check.

        This is the sabotage target for "remover a unique parcial do DEBIT": with the
        index gone, the second call inserts a second row and consumes 2h twice.
        """
        rows = log_hours(
            project=client_with_contract["project"],
            actor=actor,
            catalog=catalog,
            minutes=120,
            worked_on=date(2026, 1, 15),
        )

        apply_debit(rows[0], actor=actor)
        apply_debit(rows[0], actor=actor)

        period = resolve_period(client_with_contract["contract"], date(2026, 1, 15))

        assert period.consumed_hours == Decimal("2.0000")
        assert (
            ServiceHourLedgerEntry.objects.filter(
                service_log=rows[0], entry_type=ServiceLedgerEntryType.DEBIT
            ).count()
            == 1
        )

    def test_the_database_refuses_a_second_debit_row_directly(
        self, client_with_contract, catalog, actor
    ):
        """The index, tested without going through ``apply_debit`` at all.

        ``apply_debit`` catches the IntegrityError, so a test that only called it would
        still pass if the index were dropped and the catch never fired. This asserts the
        guarantee at the level it actually lives.
        """
        rows = log_hours(
            project=client_with_contract["project"],
            actor=actor,
            catalog=catalog,
            minutes=120,
            worked_on=date(2026, 1, 15),
        )
        period = resolve_period(client_with_contract["contract"], date(2026, 1, 15))

        with pytest.raises(IntegrityError):
            ServiceHourLedgerEntry.objects.create(
                workspace_id=period.workspace_id,
                contract_id=period.contract_id,
                period=period,
                hours=Decimal("-2.0000"),
                entry_type=ServiceLedgerEntryType.DEBIT,
                service_log=rows[0],
            )

    def test_a_closed_period_refuses_a_new_debit(self, client_with_contract, catalog, actor):
        """Criterion 13, at the domain layer. The HTTP half is in the contract tests."""
        period = resolve_period(client_with_contract["contract"], date(2026, 1, 15), actor=actor)
        close_period(period, actor=actor)

        issue = IssueFactory(project=client_with_contract["project"])
        rows = build_batch_rows(
            issue=issue,
            author=actor,
            description="Atrasado",
            billing_type=catalog["pool"],
            hour_type=catalog["normal"],
            segments=[
                type(
                    "Seg",
                    (),
                    {
                        "worked_on": date(2026, 1, 20),
                        "start_time": None,
                        "end_time": None,
                        "raw_duration_minutes": 60,
                        "suggested_hour_type": None,
                        "reason": "",
                    },
                )()
            ],
        )
        create_service_log_batch(rows)

        with pytest.raises(ServicePoolValidationError) as caught:
            apply_debit(rows[0], actor=actor)

        assert caught.value.code == PERIOD_IS_CLOSED


# ---------------------------------------------------------------------------
# Criteria 11 and 12 -- reversal
# ---------------------------------------------------------------------------


class TestReversal:
    def test_deleting_a_log_returns_exactly_its_hours(self, client_with_contract, catalog, actor):
        """Criterion 11."""
        rows = log_hours(
            project=client_with_contract["project"],
            actor=actor,
            catalog=catalog,
            minutes=180,
            worked_on=date(2026, 1, 15),
        )
        period = resolve_period(client_with_contract["contract"], date(2026, 1, 15))
        assert period.consumed_hours == Decimal("3.0000")

        rows[0].delete()

        period.refresh_from_db()
        assert period.consumed_hours == Decimal("0.0000")
        assert period.balance_hours == Decimal("30.0000")
        assert reconcile_period(period)["is_consistent"]

    def test_the_reversal_returns_the_debited_amount_not_a_recomputed_one(
        self, client_with_contract, catalog, actor
    ):
        """Criterion 12, and the reason the reversal reads the DEBIT row.

        The work log's own hours are edited to 3h *before* the reversal runs. A reversal
        that recomputed from the log would give back 3h for a 2h debit and invent an
        hour. Reading the ledger gives back exactly what was taken.
        """
        rows = log_hours(
            project=client_with_contract["project"],
            actor=actor,
            catalog=catalog,
            minutes=120,
            worked_on=date(2026, 1, 15),
        )
        period = resolve_period(client_with_contract["contract"], date(2026, 1, 15))
        assert period.consumed_hours == Decimal("2.0000")

        ServiceLog.objects.filter(pk=rows[0].pk).update(
            equivalent_hours=Decimal("3.0000"), debited_hours=Decimal("3.0000")
        )
        edited = ServiceLog.objects.get(pk=rows[0].pk)

        reverse_debit(edited, actor=actor)

        period.refresh_from_db()
        assert period.consumed_hours == Decimal("0.0000"), "gave back 2h, the amount taken"

    def test_a_second_reversal_is_a_no_op(self, client_with_contract, catalog, actor):
        """Celery retries tasks, so this is not theoretical: without the unique index
        covering REVERSAL a retry credits hours that never existed."""
        rows = log_hours(
            project=client_with_contract["project"],
            actor=actor,
            catalog=catalog,
            minutes=120,
            worked_on=date(2026, 1, 15),
        )

        reverse_debit(rows[0], actor=actor)
        reverse_debit(rows[0], actor=actor)

        period = resolve_period(client_with_contract["contract"], date(2026, 1, 15))

        assert period.consumed_hours == Decimal("0.0000")
        assert (
            ServiceHourLedgerEntry.objects.filter(
                service_log=rows[0], entry_type=ServiceLedgerEntryType.REVERSAL
            ).count()
            == 1
        )

    def test_reversing_a_log_that_never_debited_returns_none_not_a_credit(
        self, client_with_contract, catalog, actor
    ):
        """Absence with its reason: there is no DEBIT row to reverse, so nothing happens.

        The positive control is the billable log below. Without it, a `reverse_debit`
        that always returned None would pass this test.
        """
        rows = log_hours(
            project=client_with_contract["project"],
            actor=actor,
            catalog=catalog,
            minutes=120,
            worked_on=date(2026, 1, 15),
            billing_type=catalog["warranty"],
        )

        assert reverse_debit(rows[0], actor=actor) is None

        billable = log_hours(
            project=client_with_contract["project"],
            actor=actor,
            catalog=catalog,
            minutes=120,
            worked_on=date(2026, 1, 15),
        )
        assert reverse_debit(billable[0], actor=actor) is not None

    def test_a_closed_period_refuses_a_reversal(self, client_with_contract, catalog, actor):
        """An invoiced month is never rewritten by a deletion."""
        rows = log_hours(
            project=client_with_contract["project"],
            actor=actor,
            catalog=catalog,
            minutes=120,
            worked_on=date(2026, 1, 15),
        )
        period = resolve_period(client_with_contract["contract"], date(2026, 1, 15))
        close_period(period, actor=actor)

        with pytest.raises(ServicePoolValidationError) as caught:
            rows[0].delete()

        assert caught.value.code == PERIOD_IS_CLOSED
        assert ServiceLog.objects.filter(pk=rows[0].pk).exists(), "the log survives the refusal"


# ---------------------------------------------------------------------------
# Criteria 5, 6, 8, 9, 15 -- closing, carryover, cap and overage
# ---------------------------------------------------------------------------


class TestCarryover:
    def test_twenty_two_of_thirty_leaves_february_with_thirty_eight(
        self, client_with_contract, catalog, actor
    ):
        """Criterion 5."""
        log_hours(
            project=client_with_contract["project"],
            actor=actor,
            catalog=catalog,
            minutes=22 * 60,
            worked_on=date(2026, 1, 15),
        )

        january = resolve_period(client_with_contract["contract"], date(2026, 1, 1))
        close_period(january, actor=actor)

        february = resolve_period(client_with_contract["contract"], date(2026, 2, 1))

        assert february.carried_hours == Decimal("8.0000")
        assert february.contracted_hours == Decimal("30.0000")
        assert february.granted_hours == Decimal("38.0000")

    def test_a_closed_period_ledger_sums_to_exactly_zero(self, client_with_contract, catalog, actor):
        """The invariant that makes criterion 20 structural: after a close, everything
        either left, was billed, or was written off -- and every one of those is a row."""
        log_hours(
            project=client_with_contract["project"],
            actor=actor,
            catalog=catalog,
            minutes=22 * 60,
            worked_on=date(2026, 1, 15),
        )
        january = resolve_period(client_with_contract["contract"], date(2026, 1, 1))
        close_period(january, actor=actor)

        total = sum(
            entry.hours for entry in ServiceHourLedgerEntry.objects.filter(period=january)
        )

        assert total == Decimal("0.0000")
        assert reconcile_period(ServiceContractPeriod.objects.get(pk=january.pk))["is_consistent"]

    def test_the_carried_parcel_keeps_its_competency_of_origin(
        self, client_with_contract, catalog, actor
    ):
        """Criterion 6 depends on this: without the origin there is no way to know which
        parcel is old enough to expire."""
        log_hours(
            project=client_with_contract["project"],
            actor=actor,
            catalog=catalog,
            minutes=10 * 60,
            worked_on=date(2026, 1, 15),
        )
        january = resolve_period(client_with_contract["contract"], date(2026, 1, 1))
        close_period(january, actor=actor)

        february = resolve_period(client_with_contract["contract"], date(2026, 2, 1))
        carry_in = ServiceHourLedgerEntry.objects.get(
            period=february, entry_type=ServiceLedgerEntryType.CARRY_IN
        )

        assert carry_in.origin_period_id == january.pk
        assert carry_in.hours == Decimal("20.0000")

    def test_the_statement_renders_every_hour_it_carries_including_the_parcels(
        self, client_with_contract, catalog, actor
    ):
        """D68 on the read model three screens share -- contract detail, internal report, portal.

        ``contract_balance_statement`` is where "10.0000" reached a client where the contract
        said ten hours, so both halves are asserted: the exact rendered strings, and the walker,
        which reports any hour key in the payload with no ``_display`` sibling. The walker is the
        half that survives a new field being added to the period dict -- eight of them are hour
        quantities today, and the parcels are nested, which is where every previous omission hid.

        Deliberately not a whole number of hours: 10h15 consumed of 30h carries 19h45, so a
        renderer that silently fell back to the decimal notation reads "19,75h" here and fails.
        """
        log_hours(
            project=client_with_contract["project"],
            actor=actor,
            catalog=catalog,
            minutes=10 * 60 + 15,
            worked_on=date(2026, 1, 15),
        )
        january = resolve_period(client_with_contract["contract"], date(2026, 1, 1))
        close_period(january, actor=actor)
        resolve_period(client_with_contract["contract"], date(2026, 2, 1))

        statement = contract_balance_statement(client_with_contract["contract"])

        assert [period["competence"] for period in statement] == ["2026-01", "2026-02"]

        closed, opened = statement

        assert (closed["consumed_hours"], closed["consumed_hours_display"]) == (
            "10.2500",
            "10h 15min",
        )
        assert (opened["carried_hours"], opened["carried_hours_display"]) == ("19.7500", "19h 45min")
        assert (opened["granted_hours"], opened["granted_hours_display"]) == ("49.7500", "49h 45min")

        # The parcel keeps its origin AND its rendering: section 7 asks for the competency of
        # origin of each parcel, and it is a client-facing line like any other.
        assert opened["parcels"][0] == {
            "origin_competence": "2026-01",
            "hours": "19.7500",
            "hours_display": "19h 45min",
        }

        assert hour_fields_missing_a_rendered_twin(statement) == []

    def test_consumption_eats_the_oldest_parcel_first(self, client_with_contract, catalog, actor):
        """Decision D6, FIFO, and it is the guard on criterion 6.

        January carries 20h into February. February then consumes 25h -- more than the
        carried parcel. FIFO means the 20h from January goes first and only 5h of
        February's own quota is touched, so what remains is 25h all originating in
        February. The reverse order would leave January's hours sitting there to expire
        while the client had a full pool, and they would be right to complain.

        This is the sabotage target for "inverter o FIFO".
        """
        log_hours(
            project=client_with_contract["project"],
            actor=actor,
            catalog=catalog,
            minutes=10 * 60,
            worked_on=date(2026, 1, 15),
        )
        january = resolve_period(client_with_contract["contract"], date(2026, 1, 1))
        close_period(january, actor=actor)

        log_hours(
            project=client_with_contract["project"],
            actor=actor,
            catalog=catalog,
            minutes=25 * 60,
            worked_on=date(2026, 2, 10),
        )
        february = ServiceContractPeriod.objects.get(
            contract=client_with_contract["contract"], competence_month=2
        )

        parcels = remaining_parcels(february)

        assert [(p[0].competence_label, p[1]) for p in parcels] == [("2026-02", Decimal("25.0000"))]

    def test_a_parcel_expires_after_its_carryover_validity(self, db, catalog, actor):
        """Criterion 6, with the boundary written out.

        ``carryover_months = 2`` means a January parcel is usable in February and March
        and expires at the close of March. The test walks all three closes and asserts
        the parcel survives the first two -- a test that only checked March would pass
        against an implementation that expired it immediately.
        """
        service_client = ServiceClientFactory(name="Validade")
        contract = ServiceContractFactory(
            service_client=service_client,
            code="VAL-1",
            monthly_hours=Decimal("10.0000"),
            starts_on=date(2026, 1, 1),
            ends_on=date(2026, 12, 31),
            carryover_months=2,
        )
        project = ProjectFactory(
            name="Validade", workspace=service_client.workspace, service_client=service_client
        )
        project.is_time_tracking_enabled = True
        project.save()

        hour_type = ServiceHourTypeFactory(
            workspace=service_client.workspace, name="Comercial", multiplier=Decimal("1.00")
        )
        pool_route = ServiceBillingTypeFactory(
            workspace=service_client.workspace,
            name="Contrato",
            billing_route=ServiceBillingType.BillingRoute.DEBIT_POOL,
        )
        local_catalog = {"normal": hour_type, "pool": pool_route}

        assert contract.carryover_months == 2

        january = resolve_period(contract, date(2026, 1, 1), actor=actor)
        close_period(january, actor=actor)

        february = resolve_period(contract, date(2026, 2, 1), actor=actor)
        assert february.carried_hours == Decimal("10.0000"), "January survives its first carry"

        # 4h consumed in February, so FIFO and validity actually interact here rather than
        # the test walking an untouched pool -- which is what makes this criterion 6 and
        # not merely a date-arithmetic check. FIFO eats January first, so 6h of January
        # remains and February's own 10h is untouched.
        log_hours(
            project=project,
            actor=actor,
            catalog=local_catalog,
            minutes=4 * 60,
            worked_on=date(2026, 2, 10),
        )
        february = ServiceContractPeriod.objects.get(pk=february.pk)
        assert {p[0].competence_label: p[1] for p in remaining_parcels(february)} == {
            "2026-01": Decimal("6.0000"),
            "2026-02": Decimal("10.0000"),
        }, "FIFO spent January's hours before February's own quota"

        close_period(february, actor=actor)

        march = resolve_period(contract, date(2026, 3, 1), actor=actor)
        origins = {p[0].competence_label for p in remaining_parcels(march)}
        assert "2026-01" in origins, "what is left of January is still usable in March"
        close_period(march, actor=actor)

        april = resolve_period(contract, date(2026, 4, 1), actor=actor)
        expired = ServiceHourLedgerEntry.objects.filter(
            period=march, entry_type=ServiceLedgerEntryType.EXPIRED_BY_VALIDITY
        )

        assert expired.count() == 1
        assert expired.first().hours == Decimal("-6.0000"), "only the unspent remainder expired"
        assert expired.first().origin_period_id == january.pk
        assert april.carried_hours == Decimal("20.0000"), (
            "February and March carried their 10h each; January's remainder did not, "
            "and did not inflate the following months"
        )
        assert "2026-01" not in {p[0].competence_label for p in remaining_parcels(april)}

    def test_the_accrual_cap_limits_the_carry_and_records_the_discard(self, db, catalog, actor):
        """Criterion 15: a 2x cap on a 30h/month contract never carries more than 60h,
        and the discarded excess is recorded rather than dropped."""
        service_client = ServiceClientFactory(name="Teto")
        contract = ServiceContractFactory(
            service_client=service_client,
            code="CAP-1",
            monthly_hours=Decimal("30.0000"),
            starts_on=date(2026, 1, 1),
            ends_on=date(2026, 12, 31),
            accrual_cap_mode=ServiceContract.AccrualCapMode.MULTIPLE,
            accrual_cap_value=Decimal("2.0000"),
        )
        assert contract.accrual_cap_hours() == Decimal("60.0000")

        # Three untouched months: 30 + 30 + 30 = 90h wants to carry, 60h may.
        for month in (1, 2, 3):
            close_period(resolve_period(contract, date(2026, month, 1), actor=actor), actor=actor)

        march = ServiceContractPeriod.objects.get(contract=contract, competence_month=3)
        april = ServiceContractPeriod.objects.get(contract=contract, competence_month=4)

        assert april.carried_hours == Decimal("60.0000")
        assert march.discarded_by_cap_hours == Decimal("30.0000")
        assert (
            ServiceHourLedgerEntry.objects.filter(
                period=march, entry_type=ServiceLedgerEntryType.EXPIRED_BY_CAP
            ).count()
            >= 1
        )
        assert reconcile_period(march)["is_consistent"]

    def test_the_cap_discards_the_newest_parcels_first(self, db, catalog, actor):
        """CHARACTERISATION TEST for a decision the brief does not settle.

        The excess is taken from the newest parcels, so the oldest survive. Two reasons,
        and they agree: under FIFO the oldest are what consumption reaches first, so
        keeping them means the surviving balance is the one that will be used; and it
        matches what a client expects to be told -- "you lost the hours you did not use
        this month", not "you lost hours from last quarter".

        This test exists so that changing the order has to be deliberate.
        """
        service_client = ServiceClientFactory(name="TetoOrdem")
        contract = ServiceContractFactory(
            service_client=service_client,
            code="CAP-2",
            monthly_hours=Decimal("30.0000"),
            starts_on=date(2026, 1, 1),
            ends_on=date(2026, 12, 31),
            accrual_cap_mode=ServiceContract.AccrualCapMode.ABSOLUTE,
            accrual_cap_value=Decimal("45.0000"),
        )

        for month in (1, 2):
            close_period(resolve_period(contract, date(2026, month, 1), actor=actor), actor=actor)

        february = ServiceContractPeriod.objects.get(contract=contract, competence_month=2)
        march = ServiceContractPeriod.objects.get(contract=contract, competence_month=3)

        discarded = ServiceHourLedgerEntry.objects.get(
            period=february, entry_type=ServiceLedgerEntryType.EXPIRED_BY_CAP
        )
        surviving = {p[0].competence_label: p[1] for p in remaining_parcels(march)}

        assert discarded.origin_period.competence_label == "2026-02", "the newest parcel was cut"
        assert surviving["2026-01"] == Decimal("30.0000"), "January survives whole"
        assert surviving["2026-02"] == Decimal("15.0000"), "February was trimmed to fit"


class TestOverage:
    def test_carrying_a_deficit_opens_the_next_month_short(
        self, client_with_contract, catalog, actor
    ):
        """Criterion 8: 33h consumed of 30h, carried, so February opens with 27h."""
        log_hours(
            project=client_with_contract["project"],
            actor=actor,
            catalog=catalog,
            minutes=33 * 60,
            worked_on=date(2026, 1, 15),
        )
        january = resolve_period(client_with_contract["contract"], date(2026, 1, 1))
        assert january.balance_hours == Decimal("-3.0000")

        close_period(january, settlement=ServiceOverageSettlement.CARRIED, actor=actor)

        february = resolve_period(client_with_contract["contract"], date(2026, 2, 1))
        january.refresh_from_db()

        assert february.carried_hours == Decimal("-3.0000")
        assert february.granted_hours == Decimal("27.0000")
        assert january.overage_settlement == ServiceOverageSettlement.CARRIED
        assert january.overage_hours == Decimal("0.0000")

    def test_billing_the_overage_keeps_the_next_month_whole(
        self, client_with_contract, catalog, actor
    ):
        """Criterion 9, and decision B3. The deficit leaves the pool entirely."""
        log_hours(
            project=client_with_contract["project"],
            actor=actor,
            catalog=catalog,
            minutes=33 * 60,
            worked_on=date(2026, 1, 15),
        )
        january = resolve_period(client_with_contract["contract"], date(2026, 1, 1))

        close_period(january, settlement=ServiceOverageSettlement.BILLED, actor=actor)

        february = resolve_period(client_with_contract["contract"], date(2026, 2, 1))
        january.refresh_from_db()

        assert february.carried_hours == Decimal("0.0000")
        assert february.granted_hours == Decimal("30.0000"), "the next month is whole"
        assert january.overage_hours == Decimal("3.0000")
        assert january.overage_settlement == ServiceOverageSettlement.BILLED

        # Phase 6: the same ledger row now carries the value. Acceptance criterion 7 of the
        # pricing brief, exactly as written: 3h at R$ 250,00/h is R$ 750,00.
        billed = ServiceHourLedgerEntry.objects.get(
            period=january, entry_type=ServiceLedgerEntryType.OVERAGE_BILLED
        )
        assert billed.amount == Decimal("750.00")
        assert billed.applied_hour_rate == Decimal("250.00")

        assert reconcile_period(january)["is_consistent"]

    def test_a_deficit_uses_the_contracts_preference_when_none_is_given(
        self, db, catalog, actor
    ):
        """The contract holds a preference; the period holds the binding decision."""
        service_client = ServiceClientFactory(name="Prefere")
        contract = ServiceContractFactory(
            service_client=service_client,
            code="PREF-1",
            monthly_hours=Decimal("10.0000"),
            starts_on=date(2026, 1, 1),
            ends_on=date(2026, 12, 31),
            overage_policy=ServiceContract.OveragePolicy.BILL_AMOUNT,
            # Phase 6: billing needs a rate that resolves, or the close is refused.
            overage_hour_rate=Decimal("100.00"),
        )
        period = resolve_period(contract, date(2026, 1, 1), actor=actor)
        ServiceContractPeriod.objects.filter(pk=period.pk).update(consumed_hours=Decimal("12.0000"))
        ServiceHourLedgerEntry.objects.create(
            workspace_id=period.workspace_id,
            contract_id=contract.pk,
            period=period,
            hours=Decimal("-12.0000"),
            entry_type=ServiceLedgerEntryType.DEBIT,
        )

        closed = close_period(ServiceContractPeriod.objects.get(pk=period.pk), actor=actor)

        assert closed.overage_settlement == ServiceOverageSettlement.BILLED

    def test_a_deficit_with_nowhere_to_carry_is_refused_not_forgiven(self, db, actor):
        """Writing a deficit off at contract end would be a gift nobody authorised, so
        the admin is forced to choose billing instead."""
        contract = ServiceContractFactory(
            code="END-1",
            monthly_hours=Decimal("10.0000"),
            starts_on=date(2026, 1, 1),
            ends_on=date(2026, 1, 31),
        )
        period = resolve_period(contract, date(2026, 1, 1), actor=actor)
        ServiceContractPeriod.objects.filter(pk=period.pk).update(consumed_hours=Decimal("12.0000"))

        with pytest.raises(ServicePoolValidationError) as caught:
            close_period(
                ServiceContractPeriod.objects.get(pk=period.pk),
                settlement=ServiceOverageSettlement.CARRIED,
                actor=actor,
            )

        assert caught.value.code == NO_NEXT_PERIOD_FOR_DEFICIT

    def test_closing_twice_is_refused(self, client_with_contract, actor):
        period = resolve_period(client_with_contract["contract"], date(2026, 1, 1), actor=actor)
        close_period(period, actor=actor)

        with pytest.raises(ServicePoolValidationError) as caught:
            close_period(ServiceContractPeriod.objects.get(pk=period.pk), actor=actor)

        assert caught.value.code == PERIOD_ALREADY_CLOSED

    def test_a_negative_balance_is_allowed_and_never_blocks(
        self, client_with_contract, catalog, actor
    ):
        """Criterion 7. 2h of balance receiving a 5h log: the log is SAVED and the
        balance goes to -3h. D4 and section 4 both forbid blocking work already done."""
        log_hours(
            project=client_with_contract["project"],
            actor=actor,
            catalog=catalog,
            minutes=28 * 60,
            worked_on=date(2026, 1, 5),
        )
        period = resolve_period(client_with_contract["contract"], date(2026, 1, 1))
        assert period.balance_hours == Decimal("2.0000")

        rows = log_hours(
            project=client_with_contract["project"],
            actor=actor,
            catalog=catalog,
            minutes=5 * 60,
            worked_on=date(2026, 1, 20),
        )

        period.refresh_from_db()
        assert ServiceLog.objects.filter(pk=rows[0].pk).exists(), "the work log was saved"
        assert period.balance_hours == Decimal("-3.0000")


# ---------------------------------------------------------------------------
# Decision B1 -- an editable partial month
# ---------------------------------------------------------------------------


class TestContractedHours:
    def test_editing_an_open_period_moves_the_balance_through_a_ledger_row(
        self, client_with_contract, actor
    ):
        """Decision B1. A correction in an append-only journal is a new row, never an
        edited one -- so the GRANT total still equals the contracted hours."""
        period = resolve_period(client_with_contract["contract"], date(2026, 1, 1), actor=actor)

        updated = update_contracted_hours(period, Decimal("15.0000"), actor=actor)

        assert updated.contracted_hours == Decimal("15.0000")
        assert updated.balance_hours == Decimal("15.0000")
        grants = ServiceHourLedgerEntry.objects.filter(
            period=period, entry_type=ServiceLedgerEntryType.GRANT
        )
        assert grants.count() == 2, "the correction is a second row"
        assert sum(g.hours for g in grants) == Decimal("15.0000")
        assert reconcile_period(
            ServiceContractPeriod.objects.get(pk=period.pk)
        )["is_consistent"]

    def test_editing_a_closed_period_is_refused(self, client_with_contract, actor):
        """The snapshot is what stops an invoiced month being rewritten. This is the
        sabotage target for "quebrar o snapshot de contracted_hours"."""
        period = resolve_period(client_with_contract["contract"], date(2026, 1, 1), actor=actor)
        close_period(period, actor=actor)

        with pytest.raises(ServicePoolValidationError) as caught:
            update_contracted_hours(
                ServiceContractPeriod.objects.get(pk=period.pk), Decimal("5.0000"), actor=actor
            )

        assert caught.value.code == PERIOD_IS_CLOSED_FOR_CONTRACTED_HOURS

    def test_raising_the_contracts_monthly_hours_does_not_reprice_existing_periods(
        self, client_with_contract, actor
    ):
        """R4, and the reason ``contracted_hours`` is a snapshot at all."""
        contract = client_with_contract["contract"]
        january = resolve_period(contract, date(2026, 1, 1), actor=actor)

        contract.monthly_hours = Decimal("50.0000")
        contract.save()

        january.refresh_from_db()

        assert january.contracted_hours == Decimal("30.0000")
        # The DERIVED value too, and not only the column. An earlier version of this test
        # asserted the column alone, and a deliberate sabotage that made `granted_hours`
        # read `contract.monthly_hours` instead of the snapshot slipped straight past it:
        # the column was still 30 while everything a user actually sees said 50.
        assert january.granted_hours == Decimal("30.0000")
        assert january.balance_hours == Decimal("30.0000")

        june = resolve_period(contract, date(2026, 6, 1), actor=actor)
        assert june.contracted_hours == Decimal("50.0000"), "new periods pick up the new value"


# ---------------------------------------------------------------------------
# Criteria 21, 22, 23, 24 -- contract resolution
# ---------------------------------------------------------------------------


class TestContractResolution:
    def test_two_clients_have_independent_pools(self, db, catalog, actor):
        """Criterion 21, the reference scenario: Marubeni 10h/month and Terlogs
        30h/month, and the corporate relationship between them mixes nothing."""
        # One workspace shared by both clients, created up front rather than by passing
        # `workspace=None` into the first factory call -- that would override the
        # SubFactory with a literal null and violate the not-null column.
        workspace = ServiceClientFactory(name="Anchor").workspace
        setup = {}

        for name, hours in (("Marubeni", Decimal("10.0000")), ("Terlogs", Decimal("30.0000"))):
            service_client = ServiceClientFactory(name=name, workspace=workspace)
            contract = ServiceContractFactory(
                service_client=service_client,
                code=f"{name[:3].upper()}-1",
                monthly_hours=hours,
                starts_on=date(2026, 1, 1),
                ends_on=date(2026, 12, 31),
            )
            project = ProjectFactory(
                name=name, workspace=workspace, service_client=service_client
            )
            project.is_time_tracking_enabled = True
            project.save()
            setup[name] = {"contract": contract, "project": project}

        # Terlogs is a subsidiary of Marubeni; the field carries no behaviour (D18).
        marubeni_client = setup["Marubeni"]["contract"].service_client
        terlogs_client = setup["Terlogs"]["contract"].service_client
        terlogs_client.parent = marubeni_client
        terlogs_client.save()

        pool_route = ServiceBillingTypeFactory(
            workspace=workspace, name="Contrato", billing_route=ServiceBillingType.BillingRoute.DEBIT_POOL
        )
        hour_type = ServiceHourTypeFactory(
            workspace=workspace, name="Comercial", multiplier=Decimal("1.00")
        )
        local_catalog = {"pool": pool_route, "normal": hour_type}

        log_hours(
            project=setup["Terlogs"]["project"],
            actor=actor,
            catalog=local_catalog,
            minutes=120,
            worked_on=date(2026, 1, 15),
        )

        marubeni_period = resolve_period(setup["Marubeni"]["contract"], date(2026, 1, 1), actor=actor)
        terlogs_period = resolve_period(setup["Terlogs"]["contract"], date(2026, 1, 1))

        assert terlogs_period.consumed_hours == Decimal("2.0000")
        assert terlogs_period.granted_hours == Decimal("30.0000")
        assert marubeni_period.consumed_hours == Decimal("0.0000"), "the parent pool is untouched"
        assert marubeni_period.granted_hours == Decimal("10.0000")

    def test_a_project_pinned_contract_wins(self, client_with_contract, actor):
        """Criterion 22: two contracts, two projects, each debiting the one pinned on it."""
        service_client = client_with_contract["client"]
        infra = ServiceContractFactory(
            service_client=service_client,
            code="INFRA-1",
            name="Infraestrutura",
            monthly_hours=Decimal("20.0000"),
            starts_on=date(2026, 1, 1),
            ends_on=date(2026, 12, 31),
        )
        infra_project = ProjectFactory(
            name="Marubeni Infra",
            workspace=service_client.workspace,
            service_client=service_client,
        )
        infra_project.service_contract = infra
        infra_project.save()

        issue = IssueFactory(project=infra_project)
        resolved, warning = resolve_contract(issue, date(2026, 1, 15))

        assert resolved.pk == infra.pk
        assert warning is None

    def test_the_default_contract_is_used_when_the_project_pins_none(
        self, client_with_contract, actor
    ):
        """Criterion 23."""
        service_client = client_with_contract["client"]
        support = client_with_contract["contract"]
        support.is_default = True
        support.save()

        ServiceContractFactory(
            service_client=service_client,
            code="INFRA-2",
            monthly_hours=Decimal("20.0000"),
            starts_on=date(2026, 1, 1),
            ends_on=date(2026, 12, 31),
        )

        issue = IssueFactory(project=client_with_contract["project"])
        resolved, _warning = resolve_contract(issue, date(2026, 1, 15))

        assert resolved.pk == support.pk

    def test_an_ambiguous_resolution_fails_explicitly(self, client_with_contract, actor):
        """Criterion 24. Two candidates, no default, no pin: refuse rather than pick.

        Asserts the CODE, and that the candidates are named in the payload. "It raised"
        alone would also be satisfied by a missing-client error, which is a different
        bug.
        """
        service_client = client_with_contract["client"]
        ServiceContractFactory(
            service_client=service_client,
            code="INFRA-3",
            monthly_hours=Decimal("20.0000"),
            starts_on=date(2026, 1, 1),
            ends_on=date(2026, 12, 31),
        )

        issue = IssueFactory(project=client_with_contract["project"])

        with pytest.raises(ServicePoolValidationError) as caught:
            resolve_contract(issue, date(2026, 1, 15))

        assert caught.value.code == AMBIGUOUS_CONTRACT_RESOLUTION
        assert len(caught.value.detail["candidate_contract_ids"]) == 2

    def test_an_absent_contract_does_not_cost_the_technician_their_work_log(self, db, actor):
        """The deviation from the literal wording of criterion 24, and why.

        Criterion 24 says an "ambígua **ou vazia**" resolution fails explicitly. Its own
        justification is "debitar o pool errado é pior que bloquear o apontamento" -- which
        only bites when a wrong pool could be chosen. With no contract at all there is no
        wrong pool, and section 4, D4 and D9 each say separately that work already
        performed is never discarded for a commercial pendency.

        So the resolution still fails loudly -- ``resolve_contract`` raises, and the work
        item panel reports the code -- but the work log survives with no debit. Asserted
        three ways, because "the balance did not move" alone would also be true if the
        engine were never wired up: the log EXISTS, ``debited_period`` is null, and the
        snapshot NAMES the reason.
        """
        from plane.utils.service_pool import issue_pool_snapshot

        service_client = ServiceClientFactory(name="SemContratoAinda")
        project = ProjectFactory(
            name="SemContratoAinda",
            workspace=service_client.workspace,
            service_client=service_client,
        )
        project.is_time_tracking_enabled = True
        project.save()

        hour_type = ServiceHourTypeFactory(
            workspace=service_client.workspace, name="Comercial", multiplier=Decimal("1.00")
        )
        pool_route = ServiceBillingTypeFactory(
            workspace=service_client.workspace,
            name="Contrato",
            billing_route=ServiceBillingType.BillingRoute.DEBIT_POOL,
        )

        rows = log_hours(
            project=project,
            actor=actor,
            catalog={"normal": hour_type, "pool": pool_route},
            minutes=120,
            worked_on=date(2026, 1, 15),
        )

        stored = ServiceLog.objects.get(pk=rows[0].pk)
        snapshot = issue_pool_snapshot(stored.issue, worked_on=date(2026, 1, 15))

        assert stored.debited_hours == Decimal("2.0000"), "the work is recorded in full"
        assert stored.debited_period_id is None, "but nothing was debited"
        assert snapshot["error"] == NO_CONTRACT_FOR_CLIENT, "and the reason is reported"
        assert not ServiceHourLedgerEntry.objects.filter(service_log=stored).exists()

    def test_an_ambiguous_resolution_still_blocks_the_work_log(
        self, client_with_contract, catalog, actor
    ):
        """The other side of the split: ambiguity DOES block.

        This is the case criterion 24 was actually written about -- two candidate pools,
        and choosing wrongly would bill the wrong company. The positive control is the test
        above, where an absent contract does not block.
        """
        ServiceContractFactory(
            service_client=client_with_contract["client"],
            code="AMB-1",
            monthly_hours=Decimal("20.0000"),
            starts_on=date(2026, 1, 1),
            ends_on=date(2026, 12, 31),
        )

        issue = IssueFactory(project=client_with_contract["project"])
        rows = build_batch_rows(
            issue=issue,
            author=actor,
            description="Ambiguo",
            billing_type=catalog["pool"],
            hour_type=catalog["normal"],
            segments=[
                type(
                    "Seg",
                    (),
                    {
                        "worked_on": date(2026, 1, 15),
                        "start_time": None,
                        "end_time": None,
                        "raw_duration_minutes": 60,
                        "suggested_hour_type": None,
                        "reason": "",
                    },
                )()
            ],
        )
        create_service_log_batch(rows)

        with pytest.raises(ServicePoolValidationError) as caught:
            apply_debit(rows[0], actor=actor)

        assert caught.value.code == AMBIGUOUS_CONTRACT_RESOLUTION
        assert caught.value.blocks_the_work_log is True

    def test_a_project_without_a_client_fails_with_its_own_code(self, db, actor):
        """Absence with the reason. NO_SERVICE_CLIENT_FOR_PROJECT and
        NO_CONTRACT_FOR_CLIENT are different faults with different fixes, and the
        positive control for both is the other one."""
        project = ProjectFactory(name="Interno", service_client=None)
        issue = IssueFactory(project=project)

        with pytest.raises(ServicePoolValidationError) as caught:
            resolve_contract(issue, date(2026, 1, 15))

        assert caught.value.code == NO_SERVICE_CLIENT_FOR_PROJECT

    def test_a_client_without_a_contract_fails_with_its_own_code(self, db, actor):
        service_client = ServiceClientFactory(name="SemContrato")
        project = ProjectFactory(
            name="SemContrato", workspace=service_client.workspace, service_client=service_client
        )
        issue = IssueFactory(project=project)

        with pytest.raises(ServicePoolValidationError) as caught:
            resolve_contract(issue, date(2026, 1, 15))

        assert caught.value.code == NO_CONTRACT_FOR_CLIENT

    def test_a_pin_to_another_clients_contract_is_refused(self, client_with_contract, db):
        """The one case where following the pin would bill the wrong company."""
        other = ServiceClientFactory(name="Outra")
        foreign = ServiceContractFactory(
            service_client=other,
            code="OUT-1",
            starts_on=date(2026, 1, 1),
            ends_on=date(2026, 12, 31),
        )
        project = client_with_contract["project"]
        project.service_contract = foreign
        project.save()

        issue = IssueFactory(project=project)

        with pytest.raises(ServicePoolValidationError) as caught:
            resolve_contract(issue, date(2026, 1, 15))

        assert caught.value.code == CONTRACT_PINNED_ON_PROJECT_BELONGS_TO_ANOTHER_CLIENT

    def test_a_vigency_that_has_not_started_warns_and_debits_the_first_period(
        self, client_with_contract, catalog, actor
    ):
        """D9 and D31 together. Permitted, flagged, never discarded -- and the hours land
        in the contract's FIRST period, not in a period of their own.

        This test used to assert ``CONTRACT_OUT_OF_VIGENCY`` for a date *after* the
        vigency, when both directions shared one warning. Phase 6 split them because they
        acquired opposite destinations: before the start there is a future pool to borrow
        from, after the end there is nothing left to debit and D33 bills it instead. The
        after-the-end half is now
        ``test_a_vigency_that_has_ended_does_not_warn_because_d33_bills_it``.
        """
        issue = IssueFactory(project=client_with_contract["project"])

        resolved, warning = resolve_contract(issue, date(2025, 11, 20))

        assert resolved.pk == client_with_contract["contract"].pk
        assert warning == CONTRACT_OUT_OF_VIGENCY

        log_hours(
            project=client_with_contract["project"],
            actor=actor,
            catalog=catalog,
            minutes=60,
            worked_on=date(2025, 11, 20),
        )

        # D31: the hour landed in January 2026, the first competency of the contract.
        first = resolve_period(client_with_contract["contract"], date(2026, 1, 15))
        assert first.competence_label == "2026-01"
        assert first.consumed_hours == Decimal("1.0000")

        # And NO period was materialised for November 2025. This is the assertion that
        # catches the 390h-against-a-360h-contract bug D31 exists to prevent.
        assert not ServiceContractPeriod.objects.filter(
            contract=client_with_contract["contract"], competence_year=2025
        ).exists()

        # The contract still sells exactly 360h, which is the invariant underneath D31.
        granted = ServiceContractPeriod.objects.filter(
            contract=client_with_contract["contract"]
        ).aggregate(total=Sum("contracted_hours"))["total"]
        assert granted == Decimal("30.0000"), "only the periods touched so far exist"

    def test_a_vigency_that_has_ended_does_not_warn_because_d33_bills_it(
        self, client_with_contract
    ):
        """The other half of the old single warning. D33 turned this into a deviation.

        ``contract_warnings`` deliberately stays silent here: the work is not going to
        touch this pool at all, so warning that the pool is out of vigency would describe
        a debit that never happens. The fact is recorded on the work log instead, as
        ``route_deviation_reason = CONTRACT_EXPIRED`` -- asserted in the billing tests.
        """
        issue = IssueFactory(project=client_with_contract["project"])

        resolved, warning = resolve_contract(issue, date(2027, 3, 1))

        assert resolved.pk == client_with_contract["contract"].pk
        assert warning is None, "an expired contract is D33's business, not a pool warning"

    def test_a_suspended_contract_debits_and_warns_with_a_distinct_code(
        self, client_with_contract, catalog, actor
    ):
        """Decision B4: SUSPENDED behaves exactly like ACTIVE except for the alert code.

        Registered explicitly as a characterisation: "suspended blocks new work but
        accepts retroactive entries" would be a new business rule, written nowhere.
        """
        contract = client_with_contract["contract"]
        contract.status = ServiceContract.Status.SUSPENDED
        contract.save()

        issue = IssueFactory(project=client_with_contract["project"])
        resolved, warning = resolve_contract(issue, date(2026, 1, 15))

        assert resolved.pk == contract.pk
        assert warning == CONTRACT_SUSPENDED
        assert warning != CONTRACT_OUT_OF_VIGENCY, "the two must be distinguishable in reports"

        log_hours(
            project=client_with_contract["project"],
            actor=actor,
            catalog=catalog,
            minutes=120,
            worked_on=date(2026, 1, 15),
        )
        period = resolve_period(contract, date(2026, 1, 1))
        assert period.consumed_hours == Decimal("2.0000"), "suspension changes no behaviour"


# ---------------------------------------------------------------------------
# Criteria 16, 17, 20 -- renewal, and where the balance goes
# ---------------------------------------------------------------------------


class TestRenewal:
    def test_renewing_in_place_carries_the_accumulated_balance(
        self, client_with_contract, catalog, actor
    ):
        """Criterion 16. The balance carries because nothing interrupts it -- the point
        of this path is the *absence* of a transfer."""
        contract = client_with_contract["contract"]
        log_hours(
            project=client_with_contract["project"],
            actor=actor,
            catalog=catalog,
            minutes=10 * 60,
            worked_on=date(2026, 12, 5),
        )
        december = resolve_period(contract, date(2026, 12, 1), actor=actor)
        assert december.balance_hours == Decimal("20.0000")

        renew_in_place(contract, ends_on=date(2027, 12, 31), monthly_hours=Decimal("20.0000"), actor=actor)
        close_period(ServiceContractPeriod.objects.get(pk=december.pk), actor=actor)

        january = resolve_period(contract, date(2027, 1, 1), actor=actor)

        assert contract.ends_on == date(2027, 12, 31)
        assert january.carried_hours == Decimal("20.0000")
        assert january.contracted_hours == Decimal("20.0000"), "the new, lower monthly hours"
        assert january.granted_hours == Decimal("40.0000")

    def test_a_successor_can_receive_the_transferred_balance(
        self, client_with_contract, catalog, actor
    ):
        """Criterion 17, the transfer path, with the audit row that criterion 20
        demands."""
        contract = client_with_contract["contract"]
        log_hours(
            project=client_with_contract["project"],
            actor=actor,
            catalog=catalog,
            minutes=10 * 60,
            worked_on=date(2026, 1, 15),
        )
        january = resolve_period(contract, date(2026, 1, 1))
        assert january.balance_hours == Decimal("20.0000")

        successor, moved = end_and_create_successor(
            contract,
            code="SUP-002",
            name="Suporte 2027",
            monthly_hours=Decimal("20.0000"),
            starts_on=date(2027, 1, 1),
            ends_on=date(2027, 12, 31),
            balance_destination=BalanceDestination.TRANSFER,
            actor=actor,
        )

        contract.refresh_from_db()
        target = resolve_period(successor, date(2027, 1, 1))

        assert contract.status == ServiceContract.Status.ENDED
        assert successor.previous_contract_id == contract.pk
        assert target.carried_hours == Decimal("20.0000")
        assert any(e.entry_type == ServiceLedgerEntryType.TRANSFERRED_TO_CONTRACT for e in moved)
        assert all(e.actor_id == actor.pk for e in moved), "criterion 20: who decided is recorded"

    def test_a_successor_can_expire_the_balance_with_an_audit_row(
        self, client_with_contract, catalog, actor
    ):
        """Criterion 17, the expiry path. Criterion 20 applied to the path where hours
        are most likely to quietly vanish."""
        contract = client_with_contract["contract"]
        log_hours(
            project=client_with_contract["project"],
            actor=actor,
            catalog=catalog,
            minutes=10 * 60,
            worked_on=date(2026, 1, 15),
        )
        assert resolve_period(contract, date(2026, 1, 1)).balance_hours == Decimal("20.0000")

        _successor, moved = end_and_create_successor(
            contract,
            code="SUP-003",
            name="Suporte novo",
            monthly_hours=Decimal("20.0000"),
            starts_on=date(2027, 1, 1),
            ends_on=date(2027, 12, 31),
            balance_destination=BalanceDestination.EXPIRE,
            actor=actor,
        )

        expiries = [
            e for e in moved if e.entry_type == ServiceLedgerEntryType.EXPIRED_BY_CONTRACT_END
        ]

        assert expiries
        assert sum(-e.hours for e in expiries) == Decimal("20.0000")
        assert all(e.actor_id == actor.pk for e in expiries)
        assert all(e.notes for e in expiries), "the record says how many hours and why"

    def test_converting_the_remaining_balance_into_a_work_item_allowance(
        self, client_with_contract, catalog, actor
    ):
        """**Closes criterion 17 of the contract phase**, its third destination.

        Phase 4 delivered transfer and expiry and left this one raising
        ``ISSUE_ALLOWANCE_NOT_AVAILABLE`` with a 501, because the allowance entity was
        Phase 5's. This is the test that criterion asked for: the same path a user takes,
        with the origin period going to zero and the work item's allowance receiving
        **exactly** the same hours.

        Also asserts the provenance, which is the part a scalar total cannot answer: the
        ``CREDIT`` row on the allowance still names the competency the hours came from,
        so "where did these 20h come from" is answered from the ledger rather than from a
        note somebody wrote.
        """
        from plane.db.models import ServiceIssueAllowance
        from plane.utils.service_allowance import reconcile_allowance

        contract = client_with_contract["contract"]
        log_hours(
            project=client_with_contract["project"],
            actor=actor,
            catalog=catalog,
            minutes=10 * 60,
            worked_on=date(2026, 1, 15),
        )
        january = resolve_period(contract, date(2026, 1, 1))
        assert january.balance_hours == Decimal("20.0000"), "30h contracted, 10h logged"

        # Created after the work log on purpose: an allowance that existed first would
        # have paid for that log, and then there would be no pool balance to convert.
        issue = IssueFactory(project=client_with_contract["project"])

        successor, moved = end_and_create_successor(
            contract,
            code="SUP-004",
            name="Suporte",
            monthly_hours=Decimal("20.0000"),
            starts_on=date(2027, 1, 1),
            ends_on=date(2027, 12, 31),
            balance_destination=BalanceDestination.ISSUE_ALLOWANCE,
            actor=actor,
            target_issue=issue,
        )

        conversions = [
            entry
            for entry in moved
            if entry.entry_type == ServiceLedgerEntryType.CONVERTED_TO_ISSUE_ALLOWANCE
        ]

        assert conversions, "the balance left the period as an audited movement"
        assert sum(-entry.hours for entry in conversions) == Decimal("20.0000")
        assert all(entry.actor_id == actor.pk for entry in conversions)
        assert all(entry.notes for entry in conversions), "the record says how many and why"

        january.refresh_from_db()

        # "The origin period's balance goes to zero" means its **ledger** sums to zero,
        # which is the invariant Phase 4 chose for a closed period: +30 granted, -10
        # debited, -20 converted. The period's own `contracted_hours` and `consumed_hours`
        # stay as the historical record of the month -- rewriting them would destroy the
        # audit rather than settle it, and is why `reconcile_period` expects zero from the
        # ledger and not from the columns.
        assert january.status == ServicePeriodStatus.CLOSED
        assert (
            ServiceHourLedgerEntry.objects.filter(period_id=january.pk).aggregate(
                total=Sum("hours")
            )["total"]
            == Decimal("0.0000")
        ), "everything that was granted either was consumed or left, and each is a row"

        allowance = ServiceIssueAllowance.objects.get(issue_id=issue.pk)

        assert allowance.credited_hours == Decimal("20.0000"), "exactly the hours that left"
        assert allowance.consumed_hours == Decimal("0.0000")
        assert allowance.balance_hours == Decimal("20.0000")

        credits = ServiceHourLedgerEntry.objects.filter(
            allowance_id=allowance.pk, entry_type=ServiceLedgerEntryType.CREDIT
        )

        assert credits.count() == len(conversions), "credited parcel by parcel"
        assert all(entry.origin_period_id is not None for entry in credits), (
            "each credit still names the competency the hours came from"
        )
        assert all(entry.period_id is None and entry.contract_id is None for entry in credits), (
            "an allowance entry has no contract: that isolation is R6, as a null column"
        )

        # Both sides balance, which is the invariant that makes criterion 20 hold on the
        # path where hours most easily vanish.
        for period in ServiceContractPeriod.objects.filter(
            contract_id=client_with_contract["contract"].pk
        ):
            assert reconcile_period(period)["is_consistent"]

        assert reconcile_allowance(allowance)["is_consistent"]

    def test_converting_without_naming_a_work_item_is_refused(self, client_with_contract, actor):
        """The one failure left on that destination, and it names itself.

        A positive control for the test above: without this, "the conversion did nothing"
        and "the conversion was refused for lack of a target" look identical from the
        balance alone, and they are opposite bugs.
        """
        with pytest.raises(ServicePoolValidationError) as caught:
            end_and_create_successor(
                client_with_contract["contract"],
                code="SUP-005",
                name="Suporte",
                monthly_hours=Decimal("20.0000"),
                starts_on=date(2027, 1, 1),
                ends_on=date(2027, 12, 31),
                balance_destination=BalanceDestination.ISSUE_ALLOWANCE,
                actor=actor,
                target_issue=None,
            )

        assert caught.value.code == ISSUE_ALLOWANCE_REQUIRES_TARGET_ISSUE


# ---------------------------------------------------------------------------
# Reconciliation -- decision D2
# ---------------------------------------------------------------------------


class TestReconciliation:
    def test_a_divergence_is_detected_and_names_the_column(
        self, client_with_contract, catalog, actor
    ):
        """Decision D2's whole justification. Two numbers that should be equal drift
        silently otherwise, and nobody finds out until a client disputes an invoice.

        The divergence is created with a raw ``update()``, which is exactly how it would
        happen for real -- a queryset write that bypassed the domain layer.
        """
        log_hours(
            project=client_with_contract["project"],
            actor=actor,
            catalog=catalog,
            minutes=120,
            worked_on=date(2026, 1, 15),
        )
        period = resolve_period(client_with_contract["contract"], date(2026, 1, 15))
        assert reconcile_period(period)["is_consistent"], "positive control before sabotage"

        ServiceContractPeriod.objects.filter(pk=period.pk).update(consumed_hours=Decimal("99.0000"))

        result = reconcile_period(ServiceContractPeriod.objects.get(pk=period.pk))

        assert result["is_consistent"] is False
        fields = {item["field"] for item in result["discrepancies"]}
        assert "consumed_hours" in fields, "the report names WHICH number diverged"

    def test_repair_rebuilds_the_totals_from_the_ledger(self, client_with_contract, catalog, actor):
        from plane.utils.service_pool import repair_period

        log_hours(
            project=client_with_contract["project"],
            actor=actor,
            catalog=catalog,
            minutes=120,
            worked_on=date(2026, 1, 15),
        )
        period = resolve_period(client_with_contract["contract"], date(2026, 1, 15))
        ServiceContractPeriod.objects.filter(pk=period.pk).update(consumed_hours=Decimal("99.0000"))

        repair_period(ServiceContractPeriod.objects.get(pk=period.pk))

        repaired = ServiceContractPeriod.objects.get(pk=period.pk)
        assert repaired.consumed_hours == Decimal("2.0000")
        assert reconcile_period(repaired)["is_consistent"]


class TestBalanceAnnotation:
    def test_the_annotation_matches_the_property(self, client_with_contract, catalog, actor):
        """Decision D3: ``annotate()`` gives the alert panel its filter without storing a
        derived column. If the two ever disagree, one of them is wrong."""
        log_hours(
            project=client_with_contract["project"],
            actor=actor,
            catalog=catalog,
            minutes=33 * 60,
            worked_on=date(2026, 1, 15),
        )
        period = resolve_period(client_with_contract["contract"], date(2026, 1, 15))

        annotated = annotate_period_balance(
            ServiceContractPeriod.objects.filter(pk=period.pk)
        ).first()

        assert annotated.balance == period.balance_hours == Decimal("-3.0000")
        assert annotated.granted == period.granted_hours == Decimal("30.0000")

    def test_negative_balances_can_be_filtered_in_sql(self, client_with_contract, catalog, actor):
        log_hours(
            project=client_with_contract["project"],
            actor=actor,
            catalog=catalog,
            minutes=33 * 60,
            worked_on=date(2026, 1, 15),
        )
        contract = client_with_contract["contract"]
        resolve_period(contract, date(2026, 2, 1), actor=actor)

        negative = annotate_period_balance(
            ServiceContractPeriod.objects.filter(contract=contract)
        ).filter(balance__lt=0)

        assert [p.competence_month for p in negative] == [1]
