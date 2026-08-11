# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Work item hour allowances: crediting, precedence over the contract, and isolation.

Covers all nine acceptance criteria of the allowance phase, plus the inheritance rule and
its guardrails.

**Every assertion that the contract pool "was not touched" is made against a period that
exists and holds zero**, never against the absence of a period. Those are different facts:
a debit engine that was never wired up, a work item whose allowance paid, and a client with
no contract at all all produce "no hours left the pool", and they are three different bugs.
So each isolation test materialises the competency first and then asserts zero on it, and
the positive control -- the same pipeline debiting the pool when there is no allowance --
sits in ``TestPrecedence.test_a_work_item_without_any_allowance_debits_the_contract``.

The other rule this file follows: the multiplier, the route and the hour arithmetic all come
from the real pipeline through ``build_batch_rows``, never from hand-written numbers on a
factory-built row. A factory could carry a ``debited_hours`` the multiplier never produced,
and criterion 8 is precisely about that number being right.
"""

# Python imports
from datetime import date
from decimal import Decimal

# Third party imports
import pytest

# Django imports
from django.db import IntegrityError, transaction
from django.db.models import Sum

# Module imports
from plane.db.models import (
    ServiceBillingType,
    ServiceContract,
    ServiceHourLedgerEntry,
    ServiceIssueAllowance,
    ServiceIssueAllowanceStatus,
    ServiceLedgerEntryType,
    ServiceLog,
    ServicePeriodStatus,
)
from plane.tests.factories import (
    IssueFactory,
    ProjectFactory,
    ServiceBillingTypeFactory,
    ServiceClientFactory,
    ServiceContractFactory,
    ServiceHourTypeFactory,
    ServiceIssueAllowanceFactory,
    UserFactory,
)
from plane.tests.hour_display import hour_fields_missing_a_rendered_twin
from plane.utils.service_allowance import (
    ALLOWANCE_ALREADY_CLOSED,
    ALLOWANCE_ANCESTOR_DEPTH_LIMIT,
    ALLOWANCE_CREDIT_MUST_BE_POSITIVE,
    ALLOWANCE_IS_CLOSED,
    allowance_credits,
    allowance_overage_preview,
    close_allowance,
    credit_allowance,
    issue_allowance_snapshot,
    reconcile_allowance,
    repair_allowance,
    resolve_work_item_allowance,
)
from plane.utils.service_log import build_batch_rows, create_service_log_batch
from plane.utils.service_pool import (
    ORIGIN_CONTRACT_PERIOD,
    ORIGIN_ISSUE_ALLOWANCE,
    ServicePoolValidationError,
    apply_debit,
    issue_pool_snapshot,
    resolve_period,
    reverse_debit,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def no_celery_dispatch(monkeypatch):
    """Soft delete fires ``.delay``, and there is no broker in tests.

    Patched at ``Task.apply_async`` rather than per task, so a new ``.delay`` added
    anywhere does not silently start needing a broker.
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
    """One client, one 30h/month contract for 2026, one project.

    The support contract of the use case in section 3: the client pays for 30h a month,
    and the whole point of an allowance is that a separately sold project does not eat it.
    Asserts the specific configuration rather than a count, because "one contract" can be
    one for the wrong reasons.
    """
    service_client = ServiceClientFactory(name="Marubeni")
    contract = ServiceContractFactory(
        service_client=service_client,
        code="SUP-001",
        name="Suporte",
        monthly_hours=Decimal("30.0000"),
        starts_on=date(2026, 1, 1),
        ends_on=date(2026, 12, 31),
    )
    project = ProjectFactory(
        name="Marubeni", workspace=service_client.workspace, service_client=service_client
    )
    project.is_time_tracking_enabled = True
    project.save()

    assert contract.monthly_hours == Decimal("30.0000")
    assert (contract.starts_on, contract.ends_on) == (date(2026, 1, 1), date(2026, 12, 31))
    assert contract.carryover_months is None, "these tests assume the balance never expires"
    assert contract.accrual_cap_mode == ServiceContract.AccrualCapMode.NONE

    return {"client": service_client, "contract": contract, "project": project}


@pytest.fixture
def catalog(db, client_with_contract):
    """A 1.0 pool route, a 2.0 pool route, and a non-billable route.

    Three routes, not one, because the positive control for every "did not consume the
    allowance" test is a *different route on the same fixture*. Without the billable pair
    in the same place, a suite where nothing is consumed is indistinguishable from one
    that works.
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


def log_on(issue, *, actor, catalog, minutes, worked_on, hour_type=None, billing_type=None):
    """Create a work log **on a given work item** through the real pipeline, and debit it.

    Takes the work item rather than creating one, unlike the helper of the same shape in
    ``test_service_pool.py``: half the tests here are about a log on a *sub-task* of the
    work item that holds the allowance, and that is precisely the thing the caller has to
    choose.
    """
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


@pytest.fixture
def project_issue(db, client_with_contract):
    """The work item a 40h project is opened as."""
    return IssueFactory(project=client_with_contract["project"], name="Projeto de migracao")


@pytest.fixture
def january(db, client_with_contract, actor):
    """The client's competency month, materialised so isolation can be asserted against it.

    **Materialised on purpose.** Every "the contract was not touched" assertion in this
    file is made against this row holding zero, not against a period that does not exist.
    A missing period and an untouched period look the same from the balance and are
    opposite facts.
    """
    period = resolve_period(client_with_contract["contract"], date(2026, 1, 15), actor=actor)

    assert period.contracted_hours == Decimal("30.0000")
    assert period.consumed_hours == Decimal("0.0000")
    assert period.status == ServicePeriodStatus.OPEN

    return period


# ---------------------------------------------------------------------------
# Criteria 1 and 5 -- crediting, and the history
# ---------------------------------------------------------------------------


class TestCrediting:
    def test_crediting_forty_hours_shows_the_balance_on_the_work_item(
        self, project_issue, actor
    ):
        """Criterion 1. The four numbers section 4 puts on the work item."""
        allowance = credit_allowance(
            project_issue, Decimal("40.0000"), actor=actor, reference="Proposta 2026-014"
        )

        assert allowance.credited_hours == Decimal("40.0000")
        assert allowance.consumed_hours == Decimal("0.0000")
        assert allowance.balance_hours == Decimal("40.0000")
        assert allowance.reference == "Proposta 2026-014"
        assert allowance.status == ServiceIssueAllowanceStatus.OPEN

        summary = issue_allowance_snapshot(project_issue)

        assert summary["credited_hours"] == "40.0000"
        assert summary["balance_hours"] == "40.0000"
        assert summary["consumed_pct"] == "0.00"
        assert summary["is_inherited"] is False

        assert reconcile_allowance(allowance)["is_consistent"]

    def test_an_additional_credit_adds_up_and_keeps_both_in_the_history(
        self, project_issue, actor
    ):
        """Criterion 5: 35h plus 10h is 45h, **with the history of both credits**.

        The history is the point of the criterion, not the sum. A second allowance, or a
        single mutated ``credited_hours``, would produce the same 45h and lose the record
        of the aditivo de escopo -- which is the document a client disputes.
        """
        second_author = UserFactory(email="comercial@plane.so")

        credit_allowance(project_issue, Decimal("40.0000"), actor=actor)
        credit_allowance(project_issue, Decimal("10.0000"), actor=second_author)

        allowance = ServiceIssueAllowance.objects.get(issue_id=project_issue.pk)

        assert allowance.credited_hours == Decimal("50.0000")
        assert allowance.balance_hours == Decimal("50.0000")

        assert ServiceIssueAllowance.objects.filter(issue_id=project_issue.pk).count() == 1, (
            "an additional credit is a row on the same allowance, never a second allowance"
        )

        credits = allowance_credits(allowance)

        assert [entry["hours"] for entry in credits] == ["40.0000", "10.0000"]
        assert [entry["actor_id"] for entry in credits] == [str(actor.pk), str(second_author.pk)]
        assert all(entry["created_at"] for entry in credits), "each credit carries its date"

        # The ledger is rendered line by line on the work item, so D68 applies to every row and
        # not only to the summary above it: the exact strings, and the walker so a second hour
        # field added to a credit row cannot arrive without one.
        assert [entry["hours_display"] for entry in credits] == ["40h", "10h"]
        assert hour_fields_missing_a_rendered_twin(credits) == []

    def test_a_credit_of_zero_is_refused_and_names_why(self, project_issue, actor):
        """A ledger row that moves no balance is noise in the one table that has to be
        readable line by line. Named, so it cannot be mistaken for a silent no-op."""
        with pytest.raises(ServicePoolValidationError) as caught:
            credit_allowance(project_issue, Decimal("0.0000"), actor=actor)

        assert caught.value.code == ALLOWANCE_CREDIT_MUST_BE_POSITIVE
        assert not ServiceIssueAllowance.objects.filter(issue_id=project_issue.pk).exists(), (
            "and it did not leave an empty allowance behind"
        )

    def test_a_work_item_can_only_hold_one_allowance(self, project_issue, actor):
        """The index that makes allowance resolution total instead of ambiguous.

        With two allowances on one work item there would be an ambiguous resolution to
        design a rule for, and D27 would have to be read again for this phase. There is
        not, because the database refuses.
        """
        ServiceIssueAllowanceFactory(issue=project_issue)

        with pytest.raises(IntegrityError):
            with transaction.atomic():
                ServiceIssueAllowanceFactory(issue=project_issue)


# ---------------------------------------------------------------------------
# Criteria 2, 3, 4, 7, 8 and 9 -- precedence and isolation
# ---------------------------------------------------------------------------


class TestPrecedence:
    def test_logging_five_hours_consumes_the_allowance_and_leaves_the_contract_intact(
        self, client_with_contract, catalog, project_issue, january, actor
    ):
        """Criteria 2 and 3, which are the heart of the phase.

        40h credited, 5h logged: the allowance falls to 35h and the client's 30h support
        pool is **untouched** -- asserted against a period that exists and holds zero, not
        against a missing period.
        """
        credit_allowance(project_issue, Decimal("40.0000"), actor=actor)

        rows = log_on(
            project_issue, actor=actor, catalog=catalog, minutes=5 * 60, worked_on=date(2026, 1, 15)
        )

        allowance = ServiceIssueAllowance.objects.get(issue_id=project_issue.pk)

        assert allowance.consumed_hours == Decimal("5.0000")
        assert allowance.balance_hours == Decimal("35.0000")

        january.refresh_from_db()
        assert january.consumed_hours == Decimal("0.0000"), "the support pool never moved"
        assert january.balance_hours == Decimal("30.0000")

        # Criterion 3: the work log says which origin paid, and says it exactly once.
        log = ServiceLog.objects.get(pk=rows[0].pk)

        assert log.debited_allowance_id == allowance.pk
        assert log.debited_period_id is None, "the two origins are mutually exclusive"
        assert log.debited_hours == Decimal("5.0000")

        debit = ServiceHourLedgerEntry.objects.get(
            service_log_id=log.pk, entry_type=ServiceLedgerEntryType.DEBIT
        )

        assert debit.allowance_id == allowance.pk
        assert debit.period_id is None
        assert debit.contract_id is None, "an allowance entry has no contract: R6's isolation"
        assert debit.hours == Decimal("-5.0000")

        assert reconcile_allowance(allowance)["is_consistent"]

    def test_a_work_item_without_any_allowance_debits_the_contract(
        self, client_with_contract, catalog, january, actor
    ):
        """Criterion 4, **and the positive control for every isolation test above**.

        Its meaning changed with inheritance: "a work item with no allowance" now means no
        allowance of its own **and none inherited from an ancestor**. This one has neither.

        Without this test, "the support pool did not move" would be satisfied by a debit
        engine that was never wired up at all.
        """
        plain_issue = IssueFactory(project=client_with_contract["project"])

        assert resolve_work_item_allowance(plain_issue) is None

        rows = log_on(
            plain_issue, actor=actor, catalog=catalog, minutes=5 * 60, worked_on=date(2026, 1, 15)
        )

        january.refresh_from_db()
        assert january.consumed_hours == Decimal("5.0000"), "the contract pool did move"

        log = ServiceLog.objects.get(pk=rows[0].pk)

        assert log.debited_period_id == january.pk
        assert log.debited_allowance_id is None

    def test_a_multiplier_of_two_consumes_double_the_allowance(
        self, catalog, project_issue, january, actor
    ):
        """Criterion 8. Holds because the debit reads ``debited_hours``, which R4 already
        persisted as logged x multiplier -- not because of a branch."""
        credit_allowance(project_issue, Decimal("40.0000"), actor=actor)

        rows = log_on(
            project_issue,
            actor=actor,
            catalog=catalog,
            minutes=3 * 60,
            worked_on=date(2026, 1, 15),
            hour_type=catalog["double"],
        )

        assert rows[0].logged_hours == Decimal("3.0000")
        assert rows[0].applied_multiplier == Decimal("2.00")
        assert rows[0].debited_hours == Decimal("6.0000"), "3h at 2.0 costs the client 6h"

        allowance = ServiceIssueAllowance.objects.get(issue_id=project_issue.pk)

        assert allowance.consumed_hours == Decimal("6.0000"), "double, not the logged 3h"
        assert allowance.balance_hours == Decimal("34.0000")

        january.refresh_from_db()
        assert january.consumed_hours == Decimal("0.0000")

    def test_a_non_billable_log_consumes_no_allowance(self, catalog, project_issue, january, actor):
        """Criterion 9, with the reason for the absence asserted rather than just the
        absence.

        Garantia and Cortesia leave the allowance alone because ``debited_hours`` is zero
        by check constraint -- and the work log still shows the effort delivered, which is
        R11(a): the concession is evidenced, not hidden.
        """
        credit_allowance(project_issue, Decimal("40.0000"), actor=actor)

        rows = log_on(
            project_issue,
            actor=actor,
            catalog=catalog,
            minutes=2 * 60,
            worked_on=date(2026, 1, 15),
            billing_type=catalog["warranty"],
        )

        log = ServiceLog.objects.get(pk=rows[0].pk)

        # The reason, not just the outcome.
        assert log.applied_billing_route == ServiceBillingType.BillingRoute.NON_BILLABLE
        assert log.debited_hours == Decimal("0.0000")
        assert log.equivalent_hours == Decimal("2.0000"), "the effort is still recorded (R11a)"
        assert log.debited_allowance_id is None, "and it claims no origin at all"
        assert log.debited_period_id is None

        allowance = ServiceIssueAllowance.objects.get(issue_id=project_issue.pk)

        assert allowance.consumed_hours == Decimal("0.0000")
        assert allowance.balance_hours == Decimal("40.0000")

        assert not ServiceHourLedgerEntry.objects.filter(service_log_id=log.pk).exists(), (
            "no movement, so no row: the two are the same thing"
        )

        january.refresh_from_db()
        assert january.consumed_hours == Decimal("0.0000")

    def test_overflowing_the_allowance_never_reaches_the_contract(
        self, catalog, project_issue, january, actor
    ):
        """Criterion 7, the one the whole feature exists for.

        10h credited and 25h logged: the allowance goes to **-15h**, the work log is
        recorded, and the client's support pool is untouched. Section 3 forbids the
        overflow migrating to the contract "nem silenciosamente nem automaticamente", and
        this is that sentence as a test.
        """
        credit_allowance(project_issue, Decimal("10.0000"), actor=actor)

        rows = log_on(
            project_issue, actor=actor, catalog=catalog, minutes=25 * 60, worked_on=date(2026, 1, 15)
        )

        allowance = ServiceIssueAllowance.objects.get(issue_id=project_issue.pk)

        assert allowance.consumed_hours == Decimal("25.0000")
        assert allowance.balance_hours == Decimal("-15.0000"), "negative is a legitimate state"

        log = ServiceLog.objects.get(pk=rows[0].pk)
        assert log.deleted_at is None, "work already performed is never discarded (D4)"
        assert log.debited_allowance_id == allowance.pk

        january.refresh_from_db()
        assert january.consumed_hours == Decimal("0.0000"), "not one hour of the overflow"
        assert january.balance_hours == Decimal("30.0000")

        assert reconcile_allowance(allowance)["is_consistent"]

    def test_one_work_log_can_never_carry_two_debits(self, catalog, project_issue, january, actor):
        """Criterion 7 held by the **database** rather than by the order of an ``if``.

        The partial unique index on ``(service_log, entry_type)`` is why "debit the
        allowance and the contract for one work log" is not a state the schema can hold.
        This is the test that says so, and it is the reason the allowance reuses the
        contract's ledger instead of opening a second one.
        """
        credit_allowance(project_issue, Decimal("40.0000"), actor=actor)

        rows = log_on(
            project_issue, actor=actor, catalog=catalog, minutes=5 * 60, worked_on=date(2026, 1, 15)
        )

        existing = ServiceHourLedgerEntry.objects.get(
            service_log_id=rows[0].pk, entry_type=ServiceLedgerEntryType.DEBIT
        )
        assert existing.allowance_id is not None

        with pytest.raises(IntegrityError):
            with transaction.atomic():
                ServiceHourLedgerEntry.objects.create(
                    workspace_id=january.workspace_id,
                    contract_id=january.contract_id,
                    period=january,
                    hours=Decimal("-5.0000"),
                    entry_type=ServiceLedgerEntryType.DEBIT,
                    service_log_id=rows[0].pk,
                )

    def test_applying_the_debit_twice_is_a_no_op(self, catalog, project_issue, actor):
        """Idempotent by the index, not by a check -- the same guarantee the pool debit
        has, and it matters because Celery retries the deletion cascade."""
        credit_allowance(project_issue, Decimal("40.0000"), actor=actor)

        rows = log_on(
            project_issue, actor=actor, catalog=catalog, minutes=5 * 60, worked_on=date(2026, 1, 15)
        )
        apply_debit(rows[0], actor=actor)

        allowance = ServiceIssueAllowance.objects.get(issue_id=project_issue.pk)

        assert allowance.consumed_hours == Decimal("5.0000"), "still five, not ten"
        assert (
            ServiceHourLedgerEntry.objects.filter(
                service_log_id=rows[0].pk, entry_type=ServiceLedgerEntryType.DEBIT
            ).count()
            == 1
        )


# ---------------------------------------------------------------------------
# Inheritance -- the sub-task case
# ---------------------------------------------------------------------------


class TestInheritance:
    """A 40h project is broken into sub-tasks by anybody who executes one.

    Without inheritance the parent would hold 40h nobody logs against while every
    sub-task quietly debited the client's support pool -- the isolation gone, and gone
    silently. These tests are the pair criterion 2 needs in order to be verified at all.
    """

    def test_a_log_on_a_sub_task_consumes_the_parents_allowance(
        self, client_with_contract, catalog, project_issue, january, actor
    ):
        """**Criterion 2's pair.** Without this test the inheritance is unverified."""
        credit_allowance(project_issue, Decimal("40.0000"), actor=actor)

        sub_task = IssueFactory(
            project=client_with_contract["project"], parent=project_issue, name="Levantamento"
        )

        assert resolve_work_item_allowance(sub_task).issue_id == project_issue.pk

        rows = log_on(
            sub_task, actor=actor, catalog=catalog, minutes=5 * 60, worked_on=date(2026, 1, 15)
        )

        allowance = ServiceIssueAllowance.objects.get(issue_id=project_issue.pk)

        assert allowance.consumed_hours == Decimal("5.0000"), "the parent project paid"
        assert allowance.balance_hours == Decimal("35.0000")

        january.refresh_from_db()
        assert january.consumed_hours == Decimal("0.0000"), "the support pool stayed intact"

        log = ServiceLog.objects.get(pk=rows[0].pk)
        assert log.debited_allowance_id == allowance.pk
        assert log.debited_period_id is None

    def test_the_panel_says_the_allowance_is_inherited_and_from_where(
        self, client_with_contract, project_issue, actor
    ):
        """A technician seeing 40h on a sub-task has to know the hours are the parent's.

        Otherwise the first thing they do is ask for a second allowance on the sub-task,
        which is the one shape the schema refuses.
        """
        credit_allowance(project_issue, Decimal("40.0000"), actor=actor)
        sub_task = IssueFactory(project=client_with_contract["project"], parent=project_issue)

        summary = issue_allowance_snapshot(sub_task)

        assert summary["is_inherited"] is True
        assert summary["inherited_from_issue_id"] == str(project_issue.pk)
        assert summary["balance_hours"] == "40.0000"

    def test_a_grandchild_reaches_the_grandparents_allowance(
        self, client_with_contract, catalog, project_issue, january, actor
    ):
        """Nesting is not one level deep in practice."""
        credit_allowance(project_issue, Decimal("40.0000"), actor=actor)

        child = IssueFactory(project=client_with_contract["project"], parent=project_issue)
        grandchild = IssueFactory(project=client_with_contract["project"], parent=child)

        assert resolve_work_item_allowance(grandchild).issue_id == project_issue.pk

        log_on(grandchild, actor=actor, catalog=catalog, minutes=60, worked_on=date(2026, 1, 15))

        allowance = ServiceIssueAllowance.objects.get(issue_id=project_issue.pk)
        assert allowance.consumed_hours == Decimal("1.0000")

        january.refresh_from_db()
        assert january.consumed_hours == Decimal("0.0000")

    def test_the_nearest_allowance_wins_when_parent_and_child_both_have_one(
        self, client_with_contract, catalog, project_issue, january, actor
    ):
        """Characterisation test: **the child wins.**

        A sub-task with an allowance of its own was funded separately, and the nearest
        ancestor is the most specific answer. Fixed here so that changing it has to be
        deliberate.
        """
        credit_allowance(project_issue, Decimal("40.0000"), actor=actor)

        sub_task = IssueFactory(project=client_with_contract["project"], parent=project_issue)
        credit_allowance(sub_task, Decimal("8.0000"), actor=actor)

        assert resolve_work_item_allowance(sub_task).issue_id == sub_task.pk

        log_on(sub_task, actor=actor, catalog=catalog, minutes=2 * 60, worked_on=date(2026, 1, 15))

        child_allowance = ServiceIssueAllowance.objects.get(issue_id=sub_task.pk)
        parent_allowance = ServiceIssueAllowance.objects.get(issue_id=project_issue.pk)

        assert child_allowance.consumed_hours == Decimal("2.0000"), "the child's own pool paid"
        assert parent_allowance.consumed_hours == Decimal("0.0000"), "the parent's did not"

        january.refresh_from_db()
        assert january.consumed_hours == Decimal("0.0000")

    def test_a_closed_ancestor_allowance_blocks_and_does_not_fall_back(
        self, client_with_contract, catalog, project_issue, january, actor
    ):
        """A closed allowance up the chain blocks, exactly as a closed own one does.

        **It falls back to neither the contract nor a more distant ancestor.** Falling to
        the contract is the migration section 3 forbids; skipping to a grandparent would
        spend hours the closed project's settlement already accounted for. "Closed" means
        settled, not absent -- so resolution stops at the first allowance it finds, and
        the debit refuses.
        """
        grandparent = IssueFactory(project=client_with_contract["project"], name="Programa")
        credit_allowance(grandparent, Decimal("100.0000"), actor=actor)

        project_issue.parent = grandparent
        project_issue.save()

        credit_allowance(project_issue, Decimal("40.0000"), actor=actor)
        closed = close_allowance(
            ServiceIssueAllowance.objects.get(issue_id=project_issue.pk), actor=actor
        )
        assert closed.status == ServiceIssueAllowanceStatus.CLOSED

        sub_task = IssueFactory(project=client_with_contract["project"], parent=project_issue)

        with pytest.raises(ServicePoolValidationError) as caught:
            log_on(
                sub_task, actor=actor, catalog=catalog, minutes=60, worked_on=date(2026, 1, 15)
            )

        assert caught.value.code == ALLOWANCE_IS_CLOSED

        january.refresh_from_db()
        assert january.consumed_hours == Decimal("0.0000"), "not the contract"

        grandparent_allowance = ServiceIssueAllowance.objects.get(issue_id=grandparent.pk)
        assert grandparent_allowance.consumed_hours == Decimal("0.0000"), (
            "and not a more distant ancestor either"
        )

    def test_an_ancestor_beyond_the_depth_limit_counts_as_no_allowance(
        self, client_with_contract, catalog, january, actor
    ):
        """Past the limit the work log goes to the contract, and is **never refused**.

        Refusing a work log because of how deeply somebody nested their work items would
        cost a technician their work over a shape of the data they did not choose, and D4
        and D9 both forbid that. So the bound degrades to "no allowance", which is a
        legitimate answer that rule R6 already knows how to handle.
        """
        project = client_with_contract["project"]

        chain = [IssueFactory(project=project, name="Ancestral 0")]

        for index in range(1, ALLOWANCE_ANCESTOR_DEPTH_LIMIT + 2):
            chain.append(
                IssueFactory(project=project, parent=chain[-1], name=f"Ancestral {index}")
            )

        root, leaf = chain[0], chain[-1]
        credit_allowance(root, Decimal("40.0000"), actor=actor)

        # The leaf is ALLOWANCE_ANCESTOR_DEPTH_LIMIT + 1 levels below the root, so the
        # walk stops one short of it.
        assert resolve_work_item_allowance(leaf) is None
        assert resolve_work_item_allowance(chain[1]).issue_id == root.pk, (
            "positive control: within reach, the same root is found"
        )

        log_on(leaf, actor=actor, catalog=catalog, minutes=60, worked_on=date(2026, 1, 15))

        january.refresh_from_db()
        assert january.consumed_hours == Decimal("1.0000"), "it went to the contract, not nowhere"

        root_allowance = ServiceIssueAllowance.objects.get(issue_id=root.pk)
        assert root_allowance.consumed_hours == Decimal("0.0000")

    def test_a_parent_cycle_does_not_hang_the_debit(self, client_with_contract, actor):
        """Nothing in Plane forbids a parent cycle, and a debit must not spin on one."""
        project = client_with_contract["project"]
        first = IssueFactory(project=project)
        second = IssueFactory(project=project, parent=first)

        Issue = type(first)
        Issue.objects.filter(pk=first.pk).update(parent_id=second.pk)
        first.refresh_from_db()

        assert resolve_work_item_allowance(first) is None


# ---------------------------------------------------------------------------
# Criterion 6 -- the reversal goes back where it came from
# ---------------------------------------------------------------------------


class TestReversal:
    def test_deleting_a_log_returns_the_hours_to_the_allowance_not_the_contract(
        self, catalog, project_issue, january, actor
    ):
        """Criterion 6, and it asserts **both** halves.

        "The hours came back" is satisfied by a reversal against the wrong pool, and a
        reversal against the contract would silently credit the client's support pool with
        hours it never granted. So this checks the allowance went back up *and* that the
        contract did not move.
        """
        credit_allowance(project_issue, Decimal("40.0000"), actor=actor)
        rows = log_on(
            project_issue, actor=actor, catalog=catalog, minutes=5 * 60, worked_on=date(2026, 1, 15)
        )

        allowance = ServiceIssueAllowance.objects.get(issue_id=project_issue.pk)
        assert allowance.balance_hours == Decimal("35.0000")

        ServiceLog.objects.get(pk=rows[0].pk).delete()

        allowance.refresh_from_db()
        assert allowance.consumed_hours == Decimal("0.0000")
        assert allowance.balance_hours == Decimal("40.0000"), "back to the allowance"

        january.refresh_from_db()
        assert january.consumed_hours == Decimal("0.0000"), "and the contract was never credited"

        reversal = ServiceHourLedgerEntry.objects.get(
            service_log_id=rows[0].pk, entry_type=ServiceLedgerEntryType.REVERSAL
        )

        assert reversal.allowance_id == allowance.pk
        assert reversal.period_id is None
        assert reversal.hours == Decimal("5.0000")

        assert reconcile_allowance(allowance)["is_consistent"]

    def test_the_reversal_reads_the_debited_amount_from_the_debit_row(
        self, catalog, project_issue, actor
    ):
        """Exactness under an edit, which is what makes reading the row matter.

        The debit was 6h because of a 2.0 multiplier. If the reversal recomputed from the
        work log after its hours changed, it would give back the wrong number.
        """
        credit_allowance(project_issue, Decimal("40.0000"), actor=actor)
        rows = log_on(
            project_issue,
            actor=actor,
            catalog=catalog,
            minutes=3 * 60,
            worked_on=date(2026, 1, 15),
            hour_type=catalog["double"],
        )

        allowance = ServiceIssueAllowance.objects.get(issue_id=project_issue.pk)
        assert allowance.consumed_hours == Decimal("6.0000")

        # Something changed the work log's own hours behind the debit's back.
        ServiceLog.all_objects.filter(pk=rows[0].pk).update(
            logged_hours=Decimal("1.0000"),
            equivalent_hours=Decimal("2.0000"),
            debited_hours=Decimal("2.0000"),
        )

        reverse_debit(ServiceLog.objects.get(pk=rows[0].pk), actor=actor)

        allowance.refresh_from_db()
        assert allowance.consumed_hours == Decimal("0.0000"), (
            "exactly the 6h that left, not the 2h the row now claims"
        )

    def test_reversing_twice_gives_the_hours_back_once(self, catalog, project_issue, actor):
        """Celery retries the deletion cascade, so this is not theoretical."""
        credit_allowance(project_issue, Decimal("40.0000"), actor=actor)
        rows = log_on(
            project_issue, actor=actor, catalog=catalog, minutes=5 * 60, worked_on=date(2026, 1, 15)
        )

        log = ServiceLog.objects.get(pk=rows[0].pk)
        reverse_debit(log, actor=actor)
        reverse_debit(log, actor=actor)

        allowance = ServiceIssueAllowance.objects.get(issue_id=project_issue.pk)

        assert allowance.consumed_hours == Decimal("0.0000")
        assert allowance.balance_hours == Decimal("40.0000"), "not 45h"
        assert (
            ServiceHourLedgerEntry.objects.filter(
                service_log_id=log.pk, entry_type=ServiceLedgerEntryType.REVERSAL
            ).count()
            == 1
        )

    def test_a_closed_allowance_refuses_the_reversal_and_the_log_stays(
        self, catalog, project_issue, actor
    ):
        """The equivalent of what Phase 4 decided for a closed period.

        A closed allowance has been settled and its ledger sums to zero; handing hours
        back would change a total already billed or written off. So the reversal is
        refused, and the work log is left **intact** rather than deleted -- a row whose
        money cannot be unwound must not disappear.
        """
        credit_allowance(project_issue, Decimal("40.0000"), actor=actor)
        rows = log_on(
            project_issue, actor=actor, catalog=catalog, minutes=5 * 60, worked_on=date(2026, 1, 15)
        )

        close_allowance(ServiceIssueAllowance.objects.get(issue_id=project_issue.pk), actor=actor)

        with pytest.raises(ServicePoolValidationError) as caught:
            ServiceLog.objects.get(pk=rows[0].pk).delete()

        assert caught.value.code == ALLOWANCE_IS_CLOSED

        log = ServiceLog.all_objects.get(pk=rows[0].pk)
        assert log.deleted_at is None, "the work log survived the refused reversal"


# ---------------------------------------------------------------------------
# Section 3 -- closing, and the two settlements
# ---------------------------------------------------------------------------


class TestClosing:
    def test_closing_with_a_surplus_writes_it_off_and_never_moves_it_to_the_contract(
        self, catalog, project_issue, january, actor
    ):
        """The project came in under budget. The hours are lost, by design.

        They cannot go to the client's contract pool -- that is section 3's prohibition --
        and there is no next allowance to carry them to. So they are written off with an
        entry type of their own, and the ledger closes at exactly zero.
        """
        credit_allowance(project_issue, Decimal("40.0000"), actor=actor)
        log_on(
            project_issue, actor=actor, catalog=catalog, minutes=30 * 60, worked_on=date(2026, 1, 15)
        )

        closed = close_allowance(
            ServiceIssueAllowance.objects.get(issue_id=project_issue.pk), actor=actor
        )

        assert closed.status == ServiceIssueAllowanceStatus.CLOSED
        assert closed.closed_by_id == actor.pk
        assert closed.closed_at is not None
        assert closed.expired_hours == Decimal("10.0000")
        assert closed.overage_hours == Decimal("0.0000")

        write_off = ServiceHourLedgerEntry.objects.get(
            allowance_id=closed.pk, entry_type=ServiceLedgerEntryType.EXPIRED_BY_ALLOWANCE_CLOSE
        )
        assert write_off.hours == Decimal("-10.0000")
        assert write_off.notes, "the record says how many hours and why"

        assert ServiceHourLedgerEntry.objects.filter(allowance_id=closed.pk).aggregate(
            total=Sum("hours")
        )["total"] == Decimal("0.0000"), "a closed allowance's ledger sums to exactly zero"

        january.refresh_from_db()
        assert january.carried_hours == Decimal("0.0000"), "the surplus did not reach the contract"
        assert january.balance_hours == Decimal("30.0000")

        assert reconcile_allowance(closed)["is_consistent"]

    def test_closing_with_a_deficit_bills_the_overage_in_hours_and_in_reais(
        self, catalog, project_issue, january, actor
    ):
        """The other settlement, through the same mechanism a period's overage uses.

        The value is priced at the **allowance's own** rate, not the client's support rate:
        an allowance is a separately negotiated sale, so a project sold at R$ 180/h does not
        have overage at whatever support costs. ``TestOverageRate`` covers the precedence
        and the sabotage directly.
        """
        credit_allowance(project_issue, Decimal("10.0000"), actor=actor)
        ServiceIssueAllowance.objects.filter(issue_id=project_issue.pk).update(
            overage_hour_rate=Decimal("180.00")
        )
        log_on(
            project_issue, actor=actor, catalog=catalog, minutes=25 * 60, worked_on=date(2026, 1, 15)
        )

        closed = close_allowance(
            ServiceIssueAllowance.objects.get(issue_id=project_issue.pk), actor=actor
        )

        assert closed.overage_hours == Decimal("15.0000")
        assert closed.expired_hours == Decimal("0.0000")

        billed = ServiceHourLedgerEntry.objects.get(
            allowance_id=closed.pk, entry_type=ServiceLedgerEntryType.OVERAGE_BILLED
        )
        assert billed.hours == Decimal("15.0000")
        assert billed.amount == Decimal("2700.00"), "15h at R$ 180,00/h"
        assert billed.applied_hour_rate == Decimal("180.00")

        assert ServiceHourLedgerEntry.objects.filter(allowance_id=closed.pk).aggregate(
            total=Sum("hours")
        )["total"] == Decimal("0.0000")

        january.refresh_from_db()
        assert january.consumed_hours == Decimal("0.0000"), "the deficit was never the contract's"

        assert reconcile_allowance(closed)["is_consistent"]

    def test_the_overage_preview_renders_the_hours_it_is_about_to_bill(
        self, catalog, project_issue, actor
    ):
        """D68 on the confirmation modal, which is the last screen before an irreversible act.

        ``overage-billing-confirmation.tsx`` prints these hours, and it used to print the raw
        ``"15.2500"``. The value is deliberately not whole-minute-round here: a deficit is a
        difference between two sums, so it lands wherever the arithmetic puts it, and this is
        the payload where a decimal leaking is most expensive.
        """
        credit_allowance(project_issue, Decimal("10.0000"), actor=actor)
        ServiceIssueAllowance.objects.filter(issue_id=project_issue.pk).update(
            overage_hour_rate=Decimal("180.00")
        )
        log_on(
            project_issue,
            actor=actor,
            catalog=catalog,
            minutes=25 * 60 + 15,
            worked_on=date(2026, 1, 15),
        )

        allowance = ServiceIssueAllowance.objects.get(issue_id=project_issue.pk)
        preview = allowance_overage_preview(allowance)

        assert preview["overage_hours"] == "15.2500", "the raw string still travels beside it"
        assert preview["overage_hours_display"] == "15h 15min"
        assert hour_fields_missing_a_rendered_twin(preview) == []

    def test_a_surplus_has_no_overage_preview_at_all(self, catalog, project_issue, actor):
        """The positive control for the test above: ``None`` rather than a rendered zero.

        A surplus is not a charge, and offering "0h" for it would invent one -- the same
        distinction D51 draws between an absent figure and a zero.
        """
        credit_allowance(project_issue, Decimal("10.0000"), actor=actor)

        allowance = ServiceIssueAllowance.objects.get(issue_id=project_issue.pk)

        assert allowance_overage_preview(allowance) is None

    def test_crediting_more_hours_before_closing_settles_the_deficit(
        self, catalog, project_issue, actor
    ):
        """Section 3's *other* option for a deficit: the aditivo de escopo.

        It is a credit **before** the close, not a way of closing -- which is why
        ``close_allowance`` takes no settlement argument. This is that path.
        """
        credit_allowance(project_issue, Decimal("10.0000"), actor=actor)
        log_on(
            project_issue, actor=actor, catalog=catalog, minutes=25 * 60, worked_on=date(2026, 1, 15)
        )

        credit_allowance(project_issue, Decimal("15.0000"), actor=actor)

        closed = close_allowance(
            ServiceIssueAllowance.objects.get(issue_id=project_issue.pk), actor=actor
        )

        assert closed.credited_hours == Decimal("25.0000")
        assert closed.overage_hours == Decimal("0.0000"), "nothing to bill: the scope was extended"
        assert closed.expired_hours == Decimal("0.0000")
        assert reconcile_allowance(closed)["is_consistent"]

    def test_a_closed_allowance_refuses_a_new_work_log_and_does_not_fall_back(
        self, catalog, project_issue, january, actor
    ):
        """Blocking, and deliberately so: a closed allowance is a settled pool.

        Falling through to the contract here is the exact migration section 3 forbids, and
        it would arrive by omission rather than by anybody deciding it.
        """
        credit_allowance(project_issue, Decimal("40.0000"), actor=actor)
        close_allowance(ServiceIssueAllowance.objects.get(issue_id=project_issue.pk), actor=actor)

        with pytest.raises(ServicePoolValidationError) as caught:
            log_on(
                project_issue, actor=actor, catalog=catalog, minutes=60, worked_on=date(2026, 1, 15)
            )

        assert caught.value.code == ALLOWANCE_IS_CLOSED

        january.refresh_from_db()
        assert january.consumed_hours == Decimal("0.0000")

    def test_crediting_a_closed_allowance_is_refused(self, project_issue, actor):
        """Reopening through the side door. There is no reopen in this phase, and a credit
        must not become one."""
        credit_allowance(project_issue, Decimal("40.0000"), actor=actor)
        close_allowance(ServiceIssueAllowance.objects.get(issue_id=project_issue.pk), actor=actor)

        with pytest.raises(ServicePoolValidationError) as caught:
            credit_allowance(project_issue, Decimal("5.0000"), actor=actor)

        assert caught.value.code == ALLOWANCE_IS_CLOSED

    def test_closing_twice_is_refused_and_names_why(self, project_issue, actor):
        credit_allowance(project_issue, Decimal("40.0000"), actor=actor)
        allowance = ServiceIssueAllowance.objects.get(issue_id=project_issue.pk)
        close_allowance(allowance, actor=actor)

        with pytest.raises(ServicePoolValidationError) as caught:
            close_allowance(ServiceIssueAllowance.objects.get(pk=allowance.pk), actor=actor)

        assert caught.value.code == ALLOWANCE_ALREADY_CLOSED


# ---------------------------------------------------------------------------
# Reconciliation -- decision D23
# ---------------------------------------------------------------------------


class TestReconciliation:
    def test_a_divergence_is_detected_and_names_the_column(self, catalog, project_issue, actor):
        """Per column, not one grand total: an aggregate is satisfied by two errors that
        cancel, and reports "something is wrong" when an operator needs to know which
        number to trust."""
        credit_allowance(project_issue, Decimal("40.0000"), actor=actor)
        log_on(
            project_issue, actor=actor, catalog=catalog, minutes=5 * 60, worked_on=date(2026, 1, 15)
        )

        allowance = ServiceIssueAllowance.objects.get(issue_id=project_issue.pk)
        assert reconcile_allowance(allowance)["is_consistent"]

        # The class of bug a direct `update()` on an accumulator reopens.
        ServiceIssueAllowance.objects.filter(pk=allowance.pk).update(
            consumed_hours=Decimal("2.0000")
        )
        allowance.refresh_from_db()

        result = reconcile_allowance(allowance)

        assert result["is_consistent"] is False
        fields = {entry["field"] for entry in result["discrepancies"]}
        assert "consumed_hours" in fields, "and it says which column"

    def test_repair_rewrites_the_totals_from_the_ledger(self, catalog, project_issue, actor):
        """The ledger wins, always: it is append-only and every row records its cause, so
        it is the only one of the two that can be audited."""
        credit_allowance(project_issue, Decimal("40.0000"), actor=actor)
        log_on(
            project_issue, actor=actor, catalog=catalog, minutes=5 * 60, worked_on=date(2026, 1, 15)
        )

        allowance = ServiceIssueAllowance.objects.get(issue_id=project_issue.pk)
        ServiceIssueAllowance.objects.filter(pk=allowance.pk).update(
            consumed_hours=Decimal("99.0000")
        )

        repaired = repair_allowance(ServiceIssueAllowance.objects.get(pk=allowance.pk))

        assert repaired.consumed_hours == Decimal("5.0000")
        assert repaired.credited_hours == Decimal("40.0000")
        assert reconcile_allowance(repaired)["is_consistent"]


# ---------------------------------------------------------------------------
# The work item panel -- rule R6's hierarchy, named
# ---------------------------------------------------------------------------


class TestSnapshot:
    def test_the_panel_reports_the_allowance_as_the_origin(self, project_issue, actor):
        """``origin`` is explicit so a panel never infers R6's answer from which key came
        back null. "No period" and "an allowance pays instead" were the same payload
        before, and they are different facts."""
        credit_allowance(project_issue, Decimal("40.0000"), actor=actor)

        snapshot = issue_pool_snapshot(project_issue, worked_on=date(2026, 1, 15))

        assert snapshot["origin"] == ORIGIN_ISSUE_ALLOWANCE
        assert snapshot["allowance"]["balance_hours"] == "40.0000"
        assert snapshot["period"] is None, "the contract is not consulted at all"

    def test_the_panel_reports_the_contract_when_there_is_no_allowance(
        self, client_with_contract, january, actor
    ):
        """The positive control for the test above."""
        plain_issue = IssueFactory(project=client_with_contract["project"])

        snapshot = issue_pool_snapshot(plain_issue, worked_on=date(2026, 1, 15))

        assert snapshot["origin"] == ORIGIN_CONTRACT_PERIOD
        assert snapshot["allowance"] is None
        assert snapshot["period"]["balance_hours"] == "30.0000"

    def test_an_allowance_resolves_even_when_the_project_has_no_client(self, db, actor):
        """An allowance-funded work item in a project with no client is a working state.

        R6 asks the allowance first, so this must not report
        ``NO_SERVICE_CLIENT_FOR_PROJECT`` -- an internal project funded by an allowance is
        exactly the case, and reporting a contract error for it would send somebody to fix
        configuration that is not wrong.
        """
        project = ProjectFactory(name="Interno")
        issue = IssueFactory(project=project)
        credit_allowance(issue, Decimal("12.0000"), actor=actor)

        snapshot = issue_pool_snapshot(issue, worked_on=date(2026, 1, 15))

        assert snapshot["origin"] == ORIGIN_ISSUE_ALLOWANCE
        assert "error" not in snapshot
        assert snapshot["allowance"]["balance_hours"] == "12.0000"


# ---------------------------------------------------------------------------
# D19's isolation test, applied to the new entity
# ---------------------------------------------------------------------------


class TestWorkspaceIsolation:
    def test_an_allowance_of_another_workspace_is_not_reachable_from_this_one(
        self, client_with_contract, project_issue, actor
    ):
        """D19 requires this test for every new entity: they are our own code and do not
        inherit Plane's project isolation automatically."""
        credit_allowance(project_issue, Decimal("40.0000"), actor=actor)

        other_project = ProjectFactory(name="Outro cliente")
        other_issue = IssueFactory(project=other_project)

        assert resolve_work_item_allowance(other_issue) is None
        assert issue_allowance_snapshot(other_issue) is None

        assert (
            ServiceIssueAllowance.objects.filter(
                workspace_id=other_project.workspace_id
            ).count()
            == 0
        )
