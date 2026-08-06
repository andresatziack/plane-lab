# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""The DDL that makes the allowance's wrong states unrepresentable rather than validated.

Every constraint here guards a rule that decides what a client is charged, and a rule held
only in Python is a rule the next queryset ``.update()`` bypasses. These tests exist because
that is not a hypothetical in this codebase: Phase 1's criterion 11 came to have a second,
unguarded write path exactly that way.

**Each refusal has a positive control beside it in the same class** -- the neighbouring
shape that must stay writable. Without that, a constraint written slightly too wide and a
constraint written slightly too narrow look identical from a passing ``pytest.raises``, and
too narrow is the one that breaks production.

``pytest.ini`` runs with ``--nomigrations``, so the schema under test comes from the models.
The migration itself is validated by hand against a throwaway database; see
``tools/agent-sandbox/README.md``.
"""

# Python imports
from decimal import Decimal

# Third party imports
import pytest

# Django imports
from django.db import IntegrityError, transaction
from django.utils import timezone

# Module imports
from plane.db.models import (
    ServiceBillingType,
    ServiceHourLedgerEntry,
    ServiceIssueAllowance,
    ServiceIssueAllowanceStatus,
    ServiceLedgerEntryType,
)
from plane.tests.factories import (
    IssueFactory,
    ServiceContractFactory,
    ServiceIssueAllowanceFactory,
    ServiceLogFactory,
    UserFactory,
)
from plane.utils.service_pool import resolve_period

pytestmark = pytest.mark.unit


@pytest.fixture
def allowance(db):
    return ServiceIssueAllowanceFactory()


@pytest.fixture
def period(db):
    """A competency period, for the entries that legitimately target one."""
    contract = ServiceContractFactory()
    return resolve_period(contract, contract.starts_on)


class TestLedgerTargetExclusivity:
    """``service_ledger_entry_has_exactly_one_target``.

    This is the constraint that pays for ``contract`` and ``period`` becoming nullable so
    that the allowance could reuse the contract's ledger. It leaves the table **stricter**
    than it was: an entry with no target at all, and an entry claiming both, are now both
    impossible, and so is an allowance entry carrying a contract.
    """

    def test_an_entry_targeting_a_period_is_written(self, period):
        """Positive control: the ordinary contract movement still works."""
        entry = ServiceHourLedgerEntry.objects.create(
            workspace_id=period.workspace_id,
            contract_id=period.contract_id,
            period=period,
            hours=Decimal("30.0000"),
            entry_type=ServiceLedgerEntryType.GRANT,
        )

        assert entry.allowance_id is None

    def test_an_entry_targeting_an_allowance_is_written(self, allowance):
        """Positive control: and so does the allowance movement, with no contract."""
        entry = ServiceHourLedgerEntry.objects.create(
            workspace_id=allowance.workspace_id,
            allowance=allowance,
            hours=Decimal("40.0000"),
            entry_type=ServiceLedgerEntryType.CREDIT,
        )

        assert entry.period_id is None
        assert entry.contract_id is None

    def test_an_entry_with_no_target_is_refused(self, allowance):
        """Hours that belong to nothing would reconcile against nothing."""
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                ServiceHourLedgerEntry.objects.create(
                    workspace_id=allowance.workspace_id,
                    hours=Decimal("40.0000"),
                    entry_type=ServiceLedgerEntryType.CREDIT,
                )

    def test_an_entry_targeting_both_is_refused(self, period, allowance):
        """One movement, one balance. Both would be counted twice by two reconciliations
        that each thought they owned it."""
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                ServiceHourLedgerEntry.objects.create(
                    workspace_id=period.workspace_id,
                    contract_id=period.contract_id,
                    period=period,
                    allowance=allowance,
                    hours=Decimal("-5.0000"),
                    entry_type=ServiceLedgerEntryType.DEBIT,
                )

    def test_an_allowance_entry_carrying_a_contract_is_refused(self, period, allowance):
        """R6's isolation, as DDL. An allowance movement has no contract -- if it could
        carry one, a contract-level report would sum project hours into support hours."""
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                ServiceHourLedgerEntry.objects.create(
                    workspace_id=allowance.workspace_id,
                    contract_id=period.contract_id,
                    allowance=allowance,
                    hours=Decimal("40.0000"),
                    entry_type=ServiceLedgerEntryType.CREDIT,
                )


class TestLedgerSignsForTheNewEntryTypes:
    """``service_ledger_hours_sign_matches_entry_type``, extended for the two new types.

    A positive write-off reconciles perfectly and is a credit wearing another name.
    """

    def test_a_credit_must_add_hours(self, allowance):
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                ServiceHourLedgerEntry.objects.create(
                    workspace_id=allowance.workspace_id,
                    allowance=allowance,
                    hours=Decimal("-40.0000"),
                    entry_type=ServiceLedgerEntryType.CREDIT,
                )

    def test_a_close_write_off_must_remove_hours(self, allowance):
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                ServiceHourLedgerEntry.objects.create(
                    workspace_id=allowance.workspace_id,
                    allowance=allowance,
                    hours=Decimal("10.0000"),
                    entry_type=ServiceLedgerEntryType.EXPIRED_BY_ALLOWANCE_CLOSE,
                )

    def test_the_correct_signs_are_written(self, allowance):
        """Positive control for both."""
        credit = ServiceHourLedgerEntry.objects.create(
            workspace_id=allowance.workspace_id,
            allowance=allowance,
            hours=Decimal("40.0000"),
            entry_type=ServiceLedgerEntryType.CREDIT,
        )
        write_off = ServiceHourLedgerEntry.objects.create(
            workspace_id=allowance.workspace_id,
            allowance=allowance,
            hours=Decimal("-10.0000"),
            entry_type=ServiceLedgerEntryType.EXPIRED_BY_ALLOWANCE_CLOSE,
        )

        assert credit.hours > 0
        assert write_off.hours < 0


class TestWorkLogOriginExclusivity:
    """``service_log_debits_at_most_one_origin`` -- rule R6's "nunca dois", as DDL."""

    def test_a_log_debiting_only_an_allowance_is_written(self, allowance):
        log = ServiceLogFactory(issue=allowance.issue, debited_allowance=allowance)

        assert log.debited_period_id is None

    def test_a_log_debiting_only_a_period_is_written(self, period):
        log = ServiceLogFactory(debited_period=period)

        assert log.debited_allowance_id is None

    def test_a_log_debiting_neither_is_written(self, allowance):
        """**This shape must stay legal**, and it is the reason the constraint is not a
        biconditional. It is what a work log looks like when no contract is configured --
        D27's non-blocking resolution -- and refusing it would block the first work log of
        every installation before sales registers a contract."""
        log = ServiceLogFactory(issue=allowance.issue)

        assert log.debited_period_id is None
        assert log.debited_allowance_id is None
        assert log.applied_billing_route == ServiceBillingType.BillingRoute.DEBIT_POOL

    def test_a_log_debiting_both_is_refused(self, period, allowance):
        """Criterion 7's "sem débito parcial nos dois", made unrepresentable."""
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                ServiceLogFactory(
                    issue=allowance.issue, debited_period=period, debited_allowance=allowance
                )


class TestNonBillableDebitsNoOrigin:
    """``service_log_non_billable_debits_no_origin`` -- acceptance criterion 9, as DDL.

    The existing ``service_log_debited_hours_follows_billing_route`` already forces
    ``debited_hours`` to zero on a non-billable row, so a debit engine reading that column
    moves nothing. This closes the remaining gap: a code path that stamped the origin
    without moving hours would leave the row *claiming* an allowance paid for it, and that
    claim is what a Phase 9 report would bill against.
    """

    def test_a_non_billable_log_with_no_origin_is_written(self, allowance):
        """Positive control, and the only shape a non-billable row may take."""
        log = ServiceLogFactory(
            issue=allowance.issue,
            applied_billing_route=ServiceBillingType.BillingRoute.NON_BILLABLE,
            debited_hours=Decimal("0.0000"),
        )

        assert log.debited_allowance_id is None
        assert log.debited_period_id is None

    def test_a_non_billable_log_claiming_an_allowance_is_refused(self, allowance):
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                ServiceLogFactory(
                    issue=allowance.issue,
                    applied_billing_route=ServiceBillingType.BillingRoute.NON_BILLABLE,
                    debited_hours=Decimal("0.0000"),
                    debited_allowance=allowance,
                )

    def test_a_non_billable_log_claiming_a_period_is_refused(self, period, allowance):
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                ServiceLogFactory(
                    issue=allowance.issue,
                    applied_billing_route=ServiceBillingType.BillingRoute.NON_BILLABLE,
                    debited_hours=Decimal("0.0000"),
                    debited_period=period,
                )

    def test_a_billable_log_may_still_claim_an_origin(self, allowance):
        """One direction only. Narrowing this to a biconditional would forbid the ordinary
        billable debit, which is the entire feature."""
        log = ServiceLogFactory(issue=allowance.issue, debited_allowance=allowance)

        assert log.debited_allowance_id == allowance.pk


class TestAllowanceConstraints:
    def test_credited_hours_may_not_be_negative(self, db):
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                ServiceIssueAllowanceFactory(credited_hours=Decimal("-1.0000"))

    def test_consumed_hours_may_be_negative(self, db):
        """Deliberately unguarded, and it is the one hour column here that may move both
        ways: a reversal subtracts from it, and a transient negative during a repair is not
        a corruption. Same treatment ``carried_hours`` gets on a period."""
        allowance = ServiceIssueAllowanceFactory(consumed_hours=Decimal("-2.0000"))

        assert allowance.consumed_hours == Decimal("-2.0000")

    def test_a_closed_allowance_must_carry_its_closing_timestamp(self, db):
        """A close with no date would be a decision nobody can place in time."""
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                ServiceIssueAllowanceFactory(
                    status=ServiceIssueAllowanceStatus.CLOSED, closed_at=None
                )

    def test_an_open_allowance_may_not_carry_one(self, db):
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                ServiceIssueAllowanceFactory(
                    status=ServiceIssueAllowanceStatus.OPEN, closed_at=timezone.now()
                )

    def test_a_closed_allowance_with_a_timestamp_is_written(self, db):
        """Positive control for the pair above."""
        allowance = ServiceIssueAllowanceFactory(
            status=ServiceIssueAllowanceStatus.CLOSED,
            closed_at=timezone.now(),
            closed_by=UserFactory(),
        )

        assert allowance.closed_at is not None

    def test_two_allowances_on_one_work_item_are_refused(self, db):
        """The index that makes allowance resolution total rather than ambiguous, which is
        why D27 needs no second reading for this phase."""
        issue = IssueFactory()
        ServiceIssueAllowanceFactory(issue=issue)

        with pytest.raises(IntegrityError):
            with transaction.atomic():
                ServiceIssueAllowanceFactory(issue=issue)

    def test_two_allowances_on_different_work_items_are_written(self, db):
        """Positive control: the index is per work item, not per project."""
        project = IssueFactory().project
        first = ServiceIssueAllowanceFactory(issue=IssueFactory(project=project))
        second = ServiceIssueAllowanceFactory(issue=IssueFactory(project=project))

        assert first.pk != second.pk
        assert first.project_id == second.project_id

    def test_the_workspace_is_denormalised_from_the_project(self, db):
        """``ProjectBaseModel.save()`` does this, and the allowance relies on it -- the
        alert panel scans by workspace."""
        issue = IssueFactory()
        allowance = ServiceIssueAllowance.objects.create(project_id=issue.project_id, issue=issue)

        assert allowance.workspace_id == issue.project.workspace_id

    def test_the_balance_and_the_percentage_are_derived_not_stored(self, db):
        """Decision D3. And the percentage is ``None`` before any credit, because zero of
        zero is not zero -- a panel rendering "0% consumido" for an empty allowance would
        be stating something false."""
        empty = ServiceIssueAllowanceFactory()

        assert empty.balance_hours == Decimal("0.0000")
        assert empty.consumed_pct is None

        funded = ServiceIssueAllowanceFactory(
            issue=IssueFactory(), credited_hours=Decimal("40.0000"), consumed_hours=Decimal("10.0000")
        )

        assert funded.balance_hours == Decimal("30.0000")
        assert funded.consumed_pct == Decimal("25")
