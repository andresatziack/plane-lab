# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Domain logic for work item hour allowances. Phase 5.

Pure functions over models, with no dependency on views, serializers or DRF, in the same
shape as ``plane.utils.service_pool`` and ``plane.utils.service_log``.

**Separate from ``service_pool`` because the allowance is deliberately isolated from
contracts.** That module is about a client's agreement and its monthly pools; this one is
about hours sold against one work item, and section 3 of the phase brief forbids the two
touching: an allowance that overflows must never reach the client's support pool, "nem
silenciosamente nem automaticamente". Keeping them in separate modules is the smallest
expression of that boundary. The one place they meet is rule R6's hierarchy, in
``service_pool.apply_debit``, which asks this module first and the contract second.

Both invariants of decision D23 carry over unchanged, and both are asserted by
``reconcile_allowance``:

1. **The allowance carries the balance; the ledger carries the history.** The allowance
   row holds the totals and is the row ``select_for_update()`` locks; the shared
   ``ServiceHourLedgerEntry`` holds every movement. They are written **in the same
   transaction, always**.
2. **No balance moves without a row.** A closed allowance's entries sum to exactly zero,
   the same invariant a closed competency period satisfies.

The journal is **shared** with contract pools rather than duplicated, and the reason is
acceptance criterion 7 rather than tidiness: the partial unique index on
``(service_log, entry_type)`` means a work log cannot carry two ``DEBIT`` rows, so
"debit the allowance and the contract for the same work log" is refused by the
**database**. See ``ServiceHourLedgerEntry`` for the full argument.
"""

# Python imports
from decimal import Decimal

# Django imports
from django.db import IntegrityError, transaction
from django.db.models import F, Sum
from django.utils import timezone

# Module imports
from plane.db.models import (
    Issue,
    ServiceHourLedgerEntry,
    ServiceIssueAllowance,
    ServiceIssueAllowanceStatus,
    ServiceLedgerEntryType,
    ServiceLog,
)
from plane.utils.service_log_time import ZERO_HOURS, format_hours, format_hours_human, quantize_hours as _quantize
from plane.utils.service_money import format_money, overage_amount, quantize_money
from plane.utils.service_pool import (
    ServicePoolValidationError,
    _overage_amount_discrepancies,
    write_ledger_entry,
)

# ---------------------------------------------------------------------------
# Error codes
# ---------------------------------------------------------------------------
#
# UPPER_SNAKE, in the style the client entity and the catalogues established. The
# frontend maps them to translated strings; the API never returns Portuguese.

#: A work log, a credit or a reversal was aimed at an allowance that has been closed.
#:
#: **Blocking, and it deliberately does NOT fall back to the contract pool.** Section 3
#: forbids an allowance's overflow reaching the client's support pool "nem
#: silenciosamente nem automaticamente"; routing the next work log to the contract
#: because the allowance happens to be closed would be exactly that, arriving by
#: omission rather than by decision. So this behaves like ``PERIOD_IS_CLOSED``: an
#: administrative lock on an already-settled pool, which blocks.
#:
#: Coherent with D27, which draws the line at *ambiguity blocks, absence does not*. This
#: is neither -- it is a lock. Absence of an allowance is not a failure at all; it is
#: level 2 of rule R6's hierarchy, and the work log goes to the contract.
ALLOWANCE_IS_CLOSED = "ALLOWANCE_IS_CLOSED"

#: Closing an allowance that is already closed.
ALLOWANCE_ALREADY_CLOSED = "ALLOWANCE_ALREADY_CLOSED"

#: A credit of zero or fewer hours. Removing hours from an allowance is not a negative
#: credit -- it would leave ``credited_hours`` disagreeing with what was actually sold.
#: An over-credit is corrected by closing the allowance and settling it.
ALLOWANCE_CREDIT_MUST_BE_POSITIVE = "ALLOWANCE_CREDIT_MUST_BE_POSITIVE"

#: The work item has no allowance of its own and none inherited from an ancestor. Only
#: raised by operations that require one to already exist -- closing, reading a
#: statement. Never raised by the debit path, where it is not a failure at all.
NO_ALLOWANCE_FOR_ISSUE = "NO_ALLOWANCE_FOR_ISSUE"


#: How many ancestors above the work item the allowance search walks before giving up.
#:
#: The search is bounded so that the debit path cannot degenerate into an unbounded walk
#: on a pathological tree -- a cycle, or a hierarchy built by an import. **Exceeding the
#: limit is treated as "no allowance", never as an error**, because refusing a work log
#: on the eleventh level of nesting would cost a technician their work over a shape of
#: the data they did not choose, and D4 and D9 both forbid that.
#:
#: Ten is far past anything a real work item tree reaches -- Plane's own UI stops
#: offering sub-item creation long before -- so in practice the limit is unreachable and
#: exists only to bound the query count. The walk visits at most
#: ``1 + ALLOWANCE_ANCESTOR_DEPTH_LIMIT`` work items: the log's own, plus ten ancestors.
ALLOWANCE_ANCESTOR_DEPTH_LIMIT = 10


# ---------------------------------------------------------------------------
# Resolution -- rule R6, first level
# ---------------------------------------------------------------------------


def _issue_ancestor_chain(issue):
    """``[issue.pk, parent.pk, grandparent.pk, ...]``, nearest first, bounded.

    Walks ``Issue.parent`` through ``all_objects`` rather than the soft-delete-filtered
    manager, deliberately. A soft deleted *parent* must not break the chain: its own
    allowance is soft deleted with it and therefore invisible to the lookup anyway, but
    a live grandparent's allowance is still the right answer, and stopping at the
    deleted row would silently route the work log to the contract pool.

    Fetches only ``parent_id`` at each step, so the walk costs one narrow query per
    level and never loads a work item.

    A ``seen`` set guards against a parent cycle. The depth limit alone would already
    terminate one; the set makes the intent explicit and stops early.
    """
    chain = [issue.pk]
    seen = {issue.pk}
    parent_id = issue.parent_id
    depth = 0

    while parent_id is not None and depth < ALLOWANCE_ANCESTOR_DEPTH_LIMIT:
        if parent_id in seen:
            break

        chain.append(parent_id)
        seen.add(parent_id)

        parent_id = Issue.all_objects.filter(pk=parent_id).values_list("parent_id", flat=True).first()
        depth += 1

    return chain


def resolve_work_item_allowance(issue):
    """The allowance a work log on ``issue`` debits, or ``None``. Rule R6, first level.

    **The nearest ancestor with an allowance wins**, starting with the work item itself.

    The allowance is inherited down the tree, and that is a decision the brief does not
    make explicitly, so here is the reasoning. The use case in section 3 is a 40h project
    sold apart from the support contract -- and a 40h project is broken into sub-tasks by
    anybody who actually executes one. Without inheritance the parent would hold 40h that
    nobody ever logs against, while every sub-task quietly debited the client's 30h
    support pool. The isolation the feature exists to provide would be gone, and gone
    *silently*: no error, no alert, just the wrong pool paying. The consequences are
    asymmetric -- inheritance costs one narrow query per level of nesting, and its
    absence costs the wrong client the wrong hours -- and that is what decides it.

    **Ordering along the chain is total**, so this is never ambiguous and D27 is not
    engaged: the chain is a list, and the first hit wins. The one shape that *would* be
    ambiguous, several allowances on a single work item, is unrepresentable -- see the
    partial unique index on ``ServiceIssueAllowance``.

    **Returns the allowance regardless of its status.** Filtering to open ones here
    would be a bug, and a quiet one: a closed allowance would fall through to the
    contract pool, which is precisely the fallback section 3 forbids. The status check
    belongs to the debit, exactly as it does for a competency period, and the answer
    there is to refuse. For the same reason this **stops at the first allowance it
    finds** rather than continuing past a closed one to a more distant ancestor --
    "closed" means settled, not absent.
    """
    chain = _issue_ancestor_chain(issue)

    allowances = {
        allowance.issue_id: allowance
        for allowance in ServiceIssueAllowance.objects.filter(issue_id__in=chain)
    }

    for issue_id in chain:
        allowance = allowances.get(issue_id)

        if allowance is not None:
            return allowance

    return None


def annotate_allowance_balance(queryset):
    """Attach ``balance`` to a queryset of allowances. Decision D3.

    The queryset counterpart of the ``balance_hours`` property, and the reason it does
    not need to be a stored column: filtering the panel on ``balance__lt=0`` works from
    this without a ``GeneratedField``. Same argument as
    ``service_pool.annotate_period_balance``.
    """
    return queryset.annotate(balance=F("credited_hours") - F("consumed_hours"))


def open_allowances_for_workspace(workspace_id, *, project_id=None):
    """Open allowances of a workspace, for the alert panel."""
    queryset = ServiceIssueAllowance.objects.filter(
        workspace_id=workspace_id, status=ServiceIssueAllowanceStatus.OPEN
    ).select_related("issue", "project")

    if project_id:
        queryset = queryset.filter(project_id=project_id)

    return queryset


def _lock_allowance(allowance):
    """Re-read an allowance under a row lock.

    The allowance counterpart of ``service_pool._lock_period``, and it carries the same
    two-part guarantee for the same reasons: ``select_for_update()`` serialises
    everything that touches one allowance's totals, and every mover then writes with an
    ``F()`` expression rather than assigning a value it read earlier.

    Those two are **independently sufficient for the arithmetic** -- that was established
    by sabotage in Phase 4, not by reasoning, and the finding transfers because the
    mechanism is identical. What the lock is *not* redundant for is the read-then-decide
    sequence around ``status``: a debit checks that the allowance is open and then debits
    it, and without the lock a concurrent ``close_allowance`` fits between those two
    steps and the debit lands in an allowance that has just been settled.
    """
    return ServiceIssueAllowance.objects.select_for_update().get(pk=allowance.pk)


# ---------------------------------------------------------------------------
# Crediting -- acceptance criteria 1 and 5
# ---------------------------------------------------------------------------


def credit_allowance(
    issue,
    hours,
    *,
    actor=None,
    reference=None,
    notes=None,
    origin_period=None,
    credit_notes="",
):
    """Credit hours to a work item's allowance, creating it on the first credit.

    Acceptance criterion 1 (40h credited, balance visible on the work item) and
    criterion 5 (a further 10h takes 35h to 45h, **with the history of both credits**).

    The history is the ledger, which is why an additional credit is a new ``CREDIT`` row
    on the same allowance and never a second allowance. Each row carries its own
    ``actor`` and ``created_at``, and that is the "autoria e data do crédito" section 1
    of the brief asks for -- recorded per credit, where it belongs, rather than as one
    pair of columns on the allowance that the second credit would overwrite.

    ``reference`` and ``notes`` are applied when given, on creation or later, so the
    commercial reference can be corrected without a second entity.

    ``origin_period`` is set when the hours came from a contract's remaining balance
    (Phase 4's criterion 17). It makes the provenance queryable -- "these 12h came from
    the 2026-03 competency of contract X" -- without a column of its own.

    **Refused on a closed allowance.** Crediting one would reopen it through the side
    door, and the brief's two options at closing time -- credit more hours, or bill the
    overage -- are both decisions taken *before* the close is committed. There is no
    reopen in this phase; that is a named limitation, not an oversight.
    """
    credited = _quantize(hours)

    if credited <= ZERO_HOURS:
        raise ServicePoolValidationError(
            ALLOWANCE_CREDIT_MUST_BE_POSITIVE, {"hours": str(credited)}
        )

    with transaction.atomic():
        allowance = _get_or_create_allowance(issue, reference=reference, notes=notes)
        locked = _lock_allowance(allowance)

        # A first credit (credited_hours still zero) means the allowance was just created.
        # After the credit, existing logs on this issue that debit the contract pool should
        # be moved to the new allowance -- the reconciliation the user expects.
        is_first_credit = locked.credited_hours == ZERO_HOURS

        if locked.status == ServiceIssueAllowanceStatus.CLOSED:
            raise ServicePoolValidationError(
                ALLOWANCE_IS_CLOSED,
                {"allowance_id": str(locked.pk), "issue_id": str(locked.issue_id)},
            )

        updates = {}

        if reference is not None and reference != locked.reference:
            updates["reference"] = reference

        if notes is not None and notes != locked.notes:
            updates["notes"] = notes

        write_ledger_entry(
            allowance=locked,
            entry_type=ServiceLedgerEntryType.CREDIT,
            hours=credited,
            origin_period=origin_period,
            actor=actor,
            notes=credit_notes or f"Credito de {credited}h na bolsa do chamado",
        )

        ServiceIssueAllowance.objects.filter(pk=locked.pk).update(
            credited_hours=F("credited_hours") + credited, **updates
        )

    locked.refresh_from_db()

    # Reconciliation: when a NEW allowance is created, move existing work logs that
    # currently debit the contract pool to the new allowance. This is the behaviour the
    # user expects -- adding a bolsa de horas to an issue should immediately capture the
    # existing apontamentos instead of leaving them on the contract pool until a new log
    # is created.
    if is_first_credit:
        _reconcile_existing_logs_to_allowance(issue, locked, actor=actor)
        locked.refresh_from_db()

    return locked


def _get_or_create_allowance(issue, *, reference=None, notes=None):
    """The work item's **own** allowance, created if it has none.

    Deliberately **not** ``resolve_work_item_allowance``: crediting names a work item,
    and an inherited allowance belongs to an ancestor. Crediting through inheritance
    would put hours somewhere other than where the Admin pointed, which for a commercial
    act is unacceptable even when it is usually what was meant.

    Two requests racing to create the first allowance for one work item both reach
    ``create``; the partial unique index lets one through and the loser reads the
    winner's row. Same shape, and same reason, as ``service_pool.resolve_period``:
    preventing the race would need a lock on a row that does not exist yet.
    """
    existing = ServiceIssueAllowance.objects.filter(issue_id=issue.pk).first()

    if existing is not None:
        return existing

    try:
        with transaction.atomic():
            return ServiceIssueAllowance.objects.create(
                project_id=issue.project_id,
                issue=issue,
                reference=reference or "",
                notes=notes or "",
            )
    except IntegrityError:
        # Lost the race. The winner's row is the right one to use.
        return ServiceIssueAllowance.objects.filter(issue_id=issue.pk).first()




# ---------------------------------------------------------------------------
# Reconciliation -- move existing pool debits to a new allowance
# ---------------------------------------------------------------------------


def _reconcile_existing_logs_to_allowance(issue, allowance, *, actor=None):
    """Move existing work logs from the contract pool to a newly created allowance.

    When a new allowance is created on an issue, any existing service logs on that issue
    that currently debit the contract pool (have ``debited_period_id`` set and
    ``debited_allowance_id`` null, with ``debited_hours > 0``) should be moved to the
    new allowance. This is the behaviour users expect: adding a bolsa de horas captures
    existing apontamentos immediately, instead of leaving them on the contract pool until
    a new log triggers the reconciliation.

    For each affected log:
    1. Reverse the period debit (giving hours back to the contract pool).
    2. Clear ``debited_period_id`` and the REVERSAL entry so re-debiting is possible.
    3. Re-apply the debit, which will now route through rule R6 to the new allowance.
    """
    from plane.utils.service_pool import apply_debit, reverse_debit

    affected_logs = list(
        ServiceLog.objects.filter(
            issue_id=issue.pk,
            debited_period_id__isnull=False,
            debited_allowance_id__isnull=True,
            debited_hours__gt=ZERO_HOURS,
        ).select_related("issue", "issue__project")
    )

    if not affected_logs:
        return

    for log in affected_logs:
        # Reverse the existing period debit.
        reverse_debit(log, actor=actor)

        # Clear the period pointer and the REVERSAL entry so apply_debit can run again.
        ServiceLog.objects.filter(pk=log.pk).update(debited_period_id=None)
        ServiceHourLedgerEntry.objects.filter(
            service_log_id=log.pk,
            entry_type=ServiceLedgerEntryType.REVERSAL,
        ).delete()

        # Refresh and re-apply. Rule R6 will now find the allowance and debit it.
        log.refresh_from_db()
        apply_debit(log, actor=actor)


# ---------------------------------------------------------------------------
# Update -- metadata only
# ---------------------------------------------------------------------------


def update_allowance(allowance, *, actor=None, reference=None, notes=None):
    """Update the reference and notes of an allowance. Admin only.

    Only metadata fields are mutable. Hour figures move through the ledger exclusively
    (decision D23), so they are never touched here. An admin correcting the commercial
    reference of a project that was already credited should not need a zero-hour credit
    to accomplish it.

    **Refused on a closed allowance.** Once settled the record is historical and its
    reference should not be edited -- it may already have appeared on an invoice.
    """
    with transaction.atomic():
        locked = _lock_allowance(allowance)

        if locked.status == ServiceIssueAllowanceStatus.CLOSED:
            raise ServicePoolValidationError(
                ALLOWANCE_IS_CLOSED,
                {"allowance_id": str(locked.pk), "issue_id": str(locked.issue_id)},
            )

        updates = {}

        if reference is not None:
            updates["reference"] = reference

        if notes is not None:
            updates["notes"] = notes

        if updates:
            ServiceIssueAllowance.objects.filter(pk=locked.pk).update(**updates)

    locked.refresh_from_db()

    return locked


# ---------------------------------------------------------------------------
# Delete -- reversal, soft-delete, and re-application to the contract pool
# ---------------------------------------------------------------------------

#: Deleting a closed allowance. A closed allowance has been settled and its ledger sums
#: to zero; removing it would erase a billing record that may have been invoiced.
ALLOWANCE_CANNOT_DELETE_CLOSED = "ALLOWANCE_CANNOT_DELETE_CLOSED"


def delete_allowance(allowance, *, actor=None):
    """Remove an allowance from a work item. Admin only.

    The inverse of crediting: every work log that was consuming this allowance is moved
    back to the contract pool. The operation is atomic:

    1. Find all ServiceLog entries with ``debited_allowance_id`` pointing here.
    2. For each, reverse the allowance debit.
    3. Soft-delete the allowance.
    4. For each affected log, clear ``debited_allowance_id`` and re-apply the debit to
       the contract pool using ``apply_debit``.

    **Refused on a closed allowance.** A closed allowance has been settled (deficit
    billed or surplus written off) and its ledger sums to zero. Deleting it would erase
    a billing record that may already have been invoiced.
    """
    from plane.utils.service_pool import apply_debit, reverse_debit

    with transaction.atomic():
        locked = _lock_allowance(allowance)

        if locked.status == ServiceIssueAllowanceStatus.CLOSED:
            raise ServicePoolValidationError(
                ALLOWANCE_CANNOT_DELETE_CLOSED,
                {"allowance_id": str(locked.pk), "issue_id": str(locked.issue_id)},
            )

        # Find all work logs currently debiting this allowance.
        affected_logs = list(
            ServiceLog.objects.filter(debited_allowance_id=locked.pk)
            .select_related("issue", "issue__project")
        )

        # Reverse each debit from the allowance.
        for log in affected_logs:
            debit = ServiceHourLedgerEntry.objects.filter(
                service_log_id=log.pk,
                entry_type=ServiceLedgerEntryType.DEBIT,
                allowance_id=locked.pk,
            ).first()

            if debit is not None:
                reverse_allowance_debit(log, debit, actor=actor)

        # Clear the allowance pointer on each affected log.
        ServiceLog.objects.filter(debited_allowance_id=locked.pk).update(
            debited_allowance_id=None
        )

        # Also clear any REVERSAL entries to allow re-debiting.
        ServiceHourLedgerEntry.objects.filter(
            service_log_id__in=[log.pk for log in affected_logs],
            entry_type=ServiceLedgerEntryType.REVERSAL,
        ).delete()

        # Soft-delete the allowance.
        locked.delete()

    # Outside the transaction that held the lock: re-apply debits to the contract pool.
    # Each `apply_debit` opens its own atomic block, so a failure on one does not roll
    # back the others -- the allowance is already gone, and the remaining logs will
    # simply sit with `debited_period` null until retried.
    for log in affected_logs:
        # Refresh from DB to pick up the cleared debited_allowance_id.
        log.refresh_from_db()
        apply_debit(log, actor=actor)


# ---------------------------------------------------------------------------
# Debit and reversal -- acceptance criteria 2, 3, 6, 7, 8 and 9
# ---------------------------------------------------------------------------


def debit_allowance(service_log, allowance, *, actor=None):
    """Debit one work log's hours from a work item allowance. Rule R6, first level.

    Returns the ``DEBIT`` ledger entry.

    Called only from ``service_pool.apply_debit``, which has already established that
    the route debits a pool, that the debited hours are non-zero, and that this
    allowance is the one rule R6 selects. Keeping those checks there rather than
    repeating them here is what keeps the hierarchy readable in one place.

    **Debits ``debited_hours``, never ``logged_hours`` or ``equivalent_hours``**, and
    that single choice satisfies two acceptance criteria without a branch:

    * criterion 8, an entry with multiplier 2.0 consumes double -- because
      ``debited_hours`` is already ``logged x multiplier``, persisted at creation by R4;
    * criterion 9, a ``NON_BILLABLE`` entry consumes nothing -- because the check
      constraint ``service_log_debited_hours_follows_billing_route`` guarantees the
      column is zero there. ``apply_debit`` returns before reaching this function in that
      case, so criterion 9 holds twice over, and
      ``service_log_non_billable_debits_no_origin`` holds it a third time in DDL.

    **Idempotent by the database, not by a check**, exactly as the pool debit is: the
    partial unique index on ``(service_log, entry_type)`` makes a second call a no-op,
    which is why the ledger row is inserted *before* the total moves. If the insert
    loses, the total is untouched.
    """
    with transaction.atomic():
        locked = _lock_allowance(allowance)

        if locked.status == ServiceIssueAllowanceStatus.CLOSED:
            raise ServicePoolValidationError(
                ALLOWANCE_IS_CLOSED,
                {"allowance_id": str(locked.pk), "issue_id": str(locked.issue_id)},
            )

        try:
            with transaction.atomic():
                entry = write_ledger_entry(
                    allowance=locked,
                    entry_type=ServiceLedgerEntryType.DEBIT,
                    hours=-service_log.debited_hours,
                    service_log=service_log,
                    actor=actor,
                    notes=f"Apontamento {service_log.pk}",
                )
        except IntegrityError:
            # Already debited. Return the existing row and leave the total alone.
            return ServiceHourLedgerEntry.objects.filter(
                service_log_id=service_log.pk, entry_type=ServiceLedgerEntryType.DEBIT
            ).first()

        ServiceIssueAllowance.objects.filter(pk=locked.pk).update(
            consumed_hours=F("consumed_hours") + _quantize(service_log.debited_hours)
        )

        # R4's snapshot of which origin paid, and acceptance criterion 3. Written with
        # `update()` so that saving the work log cannot re-run its own side effects from
        # inside a debit -- same reason as `debited_period`.
        ServiceLog.all_objects.filter(pk=service_log.pk).update(debited_allowance_id=locked.pk)
        service_log.debited_allowance_id = locked.pk

    return entry


def reverse_allowance_debit(service_log, debit, *, actor=None):
    """Give back exactly the hours a work log took from an allowance. Criterion 6.

    Called only from ``service_pool.reverse_debit``, which has already found the
    ``DEBIT`` row and established from **that row** that an allowance paid -- never by
    re-resolving rule R6. That is the whole point of criterion 6: the hours go back where
    they came from, and where they came from is a fact recorded on the debit, not a
    question to be asked again. Re-resolving would send them to the contract pool the
    moment an allowance was closed, or to the wrong ancestor's allowance the moment the
    work item was re-parented.

    Exact for the same reason: the amount is read from the ``DEBIT`` row rather than
    recomputed from the work log, which would give a different answer when the work log's
    own hours are what changed -- an edit from 2h to 3h.

    **Refuses on a closed allowance, and the work log then stays intact.** A closed
    allowance has been settled and its ledger sums to zero; handing hours back into it
    would change a total that has already been billed or written off. This is the exact
    equivalent of what Phase 4 decided for a closed period, and the caller behaves the
    same way -- ``ServiceLog.delete()`` lets the error propagate so the API can explain
    it, and the deletion cascade skips that work log rather than deleting a row whose
    money it cannot unwind.
    """
    with transaction.atomic():
        locked = _lock_allowance(debit.allowance)

        if locked.status == ServiceIssueAllowanceStatus.CLOSED:
            raise ServicePoolValidationError(
                ALLOWANCE_IS_CLOSED,
                {"allowance_id": str(locked.pk), "issue_id": str(locked.issue_id)},
            )

        returned = -debit.hours

        try:
            with transaction.atomic():
                entry = write_ledger_entry(
                    allowance=locked,
                    entry_type=ServiceLedgerEntryType.REVERSAL,
                    hours=returned,
                    service_log=service_log,
                    actor=actor,
                    notes=f"Estorno do apontamento {service_log.pk}",
                )
        except IntegrityError:
            return ServiceHourLedgerEntry.objects.filter(
                service_log_id=service_log.pk, entry_type=ServiceLedgerEntryType.REVERSAL
            ).first()

        ServiceIssueAllowance.objects.filter(pk=locked.pk).update(
            consumed_hours=F("consumed_hours") - _quantize(returned)
        )

    return entry


# ---------------------------------------------------------------------------
# Closing -- section 3
# ---------------------------------------------------------------------------


def close_allowance(allowance, *, actor=None):
    """Settle an allowance and stop it accepting movement. Section 3.

    Two cases, and unlike a competency period there is no third:

    * a **deficit** is billed as overage -- ``OVERAGE_BILLED`` for what is owed, recorded
      in hours on ``overage_hours`` and **in reais on the same ledger row**, through the
      identical mechanism a period's overage uses;
    * a **surplus** is written off -- ``EXPIRED_BY_ALLOWANCE_CLOSE``, recorded on
      ``expired_hours``. It **cannot** be carried anywhere: an allowance has no successor,
      and moving it to the client's contract pool is what section 3 forbids outright.

    **There is no ``settlement`` argument**, and that is a simplification over
    ``close_period`` rather than an omission. The brief gives the Admin two options for a
    deficit -- credit more hours (aditivo de escopo) or bill the overage -- and the first
    is a ``CREDIT`` *before* closing, not a way of closing. So the only settlement a
    close can perform is the billing one, and offering a choice with one option would be
    a parameter that reads as if it did something.

    When this returns, the allowance's ledger sums to exactly **zero**, which is the same
    invariant a closed period satisfies and what makes Phase 4's criterion 20 --
    "saldo nunca desaparece sem registro de auditoria" -- hold here by construction.
    """
    with transaction.atomic():
        locked = _lock_allowance(allowance)

        if locked.status == ServiceIssueAllowanceStatus.CLOSED:
            raise ServicePoolValidationError(
                ALLOWANCE_ALREADY_CLOSED,
                {"allowance_id": str(locked.pk), "issue_id": str(locked.issue_id)},
            )

        balance = locked.balance_hours

        if balance < 0:
            owed = _quantize(-balance)
            rate = _overage_rate_for(locked)
            amount = overage_amount(overage_hours=owed, overage_hour_rate=rate)

            write_ledger_entry(
                allowance=locked,
                entry_type=ServiceLedgerEntryType.OVERAGE_BILLED,
                hours=owed,
                actor=actor,
                notes=(
                    f"Excedente de {owed}h da bolsa faturado a {format_money(rate)}/h "
                    f"= {format_money(amount)}"
                ),
                amount=amount,
                applied_hour_rate=rate,
            )
            locked.overage_hours = owed
        elif balance > 0:
            write_ledger_entry(
                allowance=locked,
                entry_type=ServiceLedgerEntryType.EXPIRED_BY_ALLOWANCE_CLOSE,
                hours=-balance,
                actor=actor,
                notes=(
                    f"{_quantize(balance)}h remanescentes da bolsa baixadas no encerramento. "
                    "Nao migram para o pool do contrato."
                ),
            )
            locked.expired_hours = _quantize(balance)

        locked.status = ServiceIssueAllowanceStatus.CLOSED
        locked.closed_at = timezone.now()
        locked.closed_by = actor
        locked.save(
            update_fields=[
                "status",
                "closed_at",
                "closed_by",
                "overage_hours",
                "expired_hours",
                "updated_at",
            ]
        )

    return locked


def _overage_rate_for(allowance):
    """The rate an hour past this allowance is billed at.

    ``allowance.overage_hour_rate`` first, then the client's base rate for the vigency in
    force. **The allowance's own rate comes first because an allowance is a separately
    negotiated sale, and the support price is the wrong price for it**: a project sold at
    R$ 180/h does not have overage at R$ 200/h merely because that is what the client's
    support costs. Reaching for the client base rate while the allowance carries its own is
    the mistake this ordering exists to prevent, and there is a sabotage test for it.

    Resolved on the **closing date**, unlike a contract period's overage which uses the
    competency's start. An allowance has no competency -- it is not a month, it is a sale
    that ends when someone closes it -- so the closing date is the only date it has.

    Raises ``ServicePricingValidationError(OVERAGE_RATE_NOT_CONFIGURED)`` when neither
    resolves, for the reason given on ``plane.utils.service_pricing.resolve_overage_rate``:
    closing is a deliberate act with an alternative, so refusing is better than zeroing a
    real debt in silence.
    """
    from plane.utils.service_pricing import resolve_overage_rate

    return resolve_overage_rate(
        service_client_id=allowance.project.service_client_id,
        on_date=timezone.now().date(),
        allowance=allowance,
    )


def allowance_overage_preview(allowance):
    """What closing this allowance would bill, computed without closing it.

    The allowance counterpart of ``plane.utils.service_pool.overage_billing_preview``, and
    it exists for the same reason: **billing overage is irreversible**, so the confirmation
    has to show the value and the rate first and say so. See D42 for what a reversal would
    require.

    Returns ``None`` when there is nothing to bill -- a surplus or an exactly zero balance
    is not a charge, and offering an amount for it would invent one.
    """
    balance = allowance.balance_hours

    if balance >= 0:
        return None

    owed = _quantize(-balance)
    rate = _overage_rate_for(allowance)

    return {
        "overage_hours": str(owed),
        "overage_hour_rate": str(rate),
        "amount": str(overage_amount(overage_hours=owed, overage_hour_rate=rate)),
        "rate_source": ("allowance" if allowance.overage_hour_rate is not None else "client_base_rate"),
        "is_reversible": False,
    }


def set_allowance_overage_rate(allowance, overage_hour_rate, *, actor):
    """Register or clear the rate an hour past this allowance is billed at.

    Audited through ``save_with_config_activity``, which is why
    ``ServiceIssueAllowance`` carries ``ChangeTrackerMixin`` for this one field: the rate
    is **configuration that decides money**, unlike the hour accumulators, whose audit is
    the ledger itself.

    ``None`` clears it and restores the fallback to the client's base rate. That transition
    is auditable in both directions because the field is nullable *and* tracked, so it
    serialises through the ``CONFIG_VALUE_UNSET`` sentinel (D4) rather than violating
    ``svc_cfg_activity_shape_matches_verb``.

    Refuses on a closed allowance: its ledger already sums to zero and its overage is
    already billed at a rate that was snapshotted onto the row, so changing the rate
    afterwards would describe a decision that was never applied.
    """
    from plane.utils.service_catalog import save_with_config_activity

    with transaction.atomic():
        locked = _lock_allowance(allowance)

        if locked.status == ServiceIssueAllowanceStatus.CLOSED:
            raise ServicePoolValidationError(
                ALLOWANCE_ALREADY_CLOSED,
                {"allowance_id": str(locked.pk), "issue_id": str(locked.issue_id)},
            )

        locked.overage_hour_rate = None if overage_hour_rate is None else quantize_money(overage_hour_rate)
        save_with_config_activity(locked, actor=actor)

    return locked


# ---------------------------------------------------------------------------
# Reconciliation -- decision D23
# ---------------------------------------------------------------------------


def _ledger_sums(allowance_id):
    return {
        row["entry_type"]: row["total"]
        for row in ServiceHourLedgerEntry.objects.filter(allowance_id=allowance_id)
        .values("entry_type")
        .annotate(total=Sum("hours"))
    }


def reconcile_allowance(allowance):
    """Assert that the ledger and the allowance's totals still agree. Decision D23.

    Returns ``{"allowance_id", "issue_id", "is_consistent", "discrepancies"}``, where
    each discrepancy names the column, what the ledger says, and what the row says.

    **Per column, not one grand total**, for the reason D23 records: a single aggregate
    is satisfied by two errors that cancel, and it reports "something is wrong" when what
    an operator needs is which number to trust.

    =====================  ==============================================
    ``credited_hours``     sum of ``CREDIT``
    ``consumed_hours``     negated sum of ``DEBIT`` and ``REVERSAL``
    ``overage_hours``      sum of ``OVERAGE_BILLED``
    ``expired_hours``      negated sum of ``EXPIRED_BY_ALLOWANCE_CLOSE``
    =====================  ==============================================

    Plus the whole-ledger invariant, which is the one that catches a movement written
    with the wrong sign: an **open** allowance's entries sum to its balance, and a
    **closed** one's sum to exactly zero.

    Read without a lock, deliberately: it is a diagnostic, it has to be cheap enough to
    run across a workspace, and a debit landing mid-scan shows up as a discrepancy that
    disappears on the next run -- whereas locking every allowance to read it would block
    the debits it is measuring.
    """
    sums = _ledger_sums(allowance.pk)

    def total(*entry_types):
        return sum((sums.get(entry_type, ZERO_HOURS) for entry_type in entry_types), ZERO_HOURS)

    expectations = [
        ("credited_hours", total(ServiceLedgerEntryType.CREDIT), allowance.credited_hours),
        (
            "consumed_hours",
            -total(ServiceLedgerEntryType.DEBIT, ServiceLedgerEntryType.REVERSAL),
            allowance.consumed_hours,
        ),
        ("overage_hours", total(ServiceLedgerEntryType.OVERAGE_BILLED), allowance.overage_hours),
        (
            "expired_hours",
            -total(ServiceLedgerEntryType.EXPIRED_BY_ALLOWANCE_CLOSE),
            allowance.expired_hours,
        ),
    ]

    discrepancies = [
        {"field": field, "ledger": str(_quantize(ledger)), "allowance": str(_quantize(stored))}
        for field, ledger, stored in expectations
        if _quantize(ledger) != _quantize(stored)
    ]

    ledger_total = sum(sums.values(), ZERO_HOURS)
    expected_total = (
        ZERO_HOURS
        if allowance.status == ServiceIssueAllowanceStatus.CLOSED
        else allowance.balance_hours
    )

    if _quantize(ledger_total) != _quantize(expected_total):
        discrepancies.append(
            {
                "field": "ledger_total",
                "ledger": str(_quantize(ledger_total)),
                "allowance": str(_quantize(expected_total)),
            }
        )

    # The monetary half. Reuses the pool's checker so the arithmetic is asserted the same
    # way for both targets; the key is renamed to this function's own shape, because a
    # caller reading `discrepancies` here expects `allowance` and not `period`.
    for item in _overage_amount_discrepancies(allowance_id=allowance.pk):
        discrepancies.append(
            {"field": item["field"], "ledger": item["ledger"], "allowance": item["period"]}
        )

    return {
        "allowance_id": str(allowance.pk),
        "issue_id": str(allowance.issue_id),
        "is_consistent": not discrepancies,
        "discrepancies": discrepancies,
    }


def repair_allowance(allowance):
    """Rewrite an allowance's totals from its ledger. The repair half of reconciliation.

    **The ledger wins, always.** It is append-only and every row records its own cause,
    so it is the only one of the two that can be audited; the totals are a cache of it.
    That is also why this direction is the only one offered -- "fix the ledger from the
    totals" would mean inventing movements.
    """
    with transaction.atomic():
        locked = _lock_allowance(allowance)
        sums = _ledger_sums(locked.pk)

        def total(*entry_types):
            return sum((sums.get(entry_type, ZERO_HOURS) for entry_type in entry_types), ZERO_HOURS)

        locked.credited_hours = _quantize(total(ServiceLedgerEntryType.CREDIT))
        locked.consumed_hours = _quantize(
            -total(ServiceLedgerEntryType.DEBIT, ServiceLedgerEntryType.REVERSAL)
        )
        locked.overage_hours = _quantize(total(ServiceLedgerEntryType.OVERAGE_BILLED))
        locked.expired_hours = _quantize(
            -total(ServiceLedgerEntryType.EXPIRED_BY_ALLOWANCE_CLOSE)
        )

        locked.save(
            update_fields=[
                "credited_hours",
                "consumed_hours",
                "overage_hours",
                "expired_hours",
                "updated_at",
            ]
        )

    return locked


# ---------------------------------------------------------------------------
# Reading -- section 4's work item indicator
# ---------------------------------------------------------------------------


def allowance_credits(allowance):
    """Every credit ever made to an allowance, oldest first. Acceptance criterion 5.

    This *is* the credit history the brief asks for: each row carries the hours, who made
    it, when, and -- when the hours came from a contract's remaining balance -- which
    competency they came from.
    """
    return [
        {
            "entry_id": str(entry.pk),
            "hours": str(_quantize(entry.hours)),
            "hours_display": format_hours(entry.hours),
            "created_at": entry.created_at.isoformat(),
            "actor_id": str(entry.actor_id) if entry.actor_id else None,
            "actor_display_name": entry.actor.display_name if entry.actor_id else None,
            "origin_competence": (
                entry.origin_period.competence_label if entry.origin_period_id else None
            ),
            "notes": entry.notes,
        }
        for entry in ServiceHourLedgerEntry.objects.filter(
            allowance_id=allowance.pk, entry_type=ServiceLedgerEntryType.CREDIT
        )
        .select_related("actor", "origin_period")
        .order_by("created_at")
    ]


def allowance_summary(allowance, *, inherited_from_issue_id=None):
    """The four numbers section 4 wants on the work item, plus the lifecycle.

    "Creditado, consumido, restante, e percentual de consumo". The percentage is ``None``
    rather than zero before any credit exists, because zero of zero is not zero -- and a
    panel rendering "0% consumido" for an allowance with no hours in it would be stating
    something false.

    ``inherited_from_issue_id`` is set when the allowance belongs to an **ancestor** of
    the work item being looked at. The panel has to say so: a technician seeing 40h on a
    sub-task needs to know those hours are the parent project's, not this task's, or the
    first thing they will do is ask for a second allowance.
    """
    consumed_pct = allowance.consumed_pct

    return {
        "allowance_id": str(allowance.pk),
        "issue_id": str(allowance.issue_id),
        "reference": allowance.reference,
        "notes": allowance.notes,
        "status": allowance.status,
        # Raw for arithmetic, ``_display`` for reading. R3: decimals persist, humans see hours.
        # Without the twin the panel rendered "50.0000" where the allowance is fifty hours.
        "credited_hours": str(_quantize(allowance.credited_hours)),
        "credited_hours_display": format_hours_human(allowance.credited_hours),
        "consumed_hours": str(_quantize(allowance.consumed_hours)),
        "consumed_hours_display": format_hours_human(allowance.consumed_hours),
        "balance_hours": str(_quantize(allowance.balance_hours)),
        "balance_hours_display": format_hours_human(allowance.balance_hours),
        "consumed_pct": (str(consumed_pct.quantize(Decimal("0.01"))) if consumed_pct is not None else None),
        "overage_hours": str(_quantize(allowance.overage_hours)),
        "overage_hours_display": format_hours_human(allowance.overage_hours),
        "expired_hours": str(_quantize(allowance.expired_hours)),
        "expired_hours_display": format_hours_human(allowance.expired_hours),
        "closed_at": allowance.closed_at.isoformat() if allowance.closed_at else None,
        "is_inherited": inherited_from_issue_id is not None,
        "inherited_from_issue_id": (str(inherited_from_issue_id) if inherited_from_issue_id else None),
    }


def issue_allowance_snapshot(issue):
    """The allowance position a work item's panel shows, or ``None`` when there is none.

    Resolves through inheritance, so a sub-task reports the allowance that will actually
    pay for its work logs -- flagged as inherited, with the ancestor it came from.
    """
    allowance = resolve_work_item_allowance(issue)

    if allowance is None:
        return None

    inherited_from = allowance.issue_id if str(allowance.issue_id) != str(issue.pk) else None

    return allowance_summary(allowance, inherited_from_issue_id=inherited_from)


def workspace_allowance_hours_by_period(period_ids):
    """Hours paid by allowances on work items whose project belongs to these periods' clients.

    Used only to give the ``NO_SERVICE_LOGS_IN_MONTH`` alert its context, so that
    "cliente sem atendimento" is not said about a client whose work is all going to
    project allowances. Keyed by period id.

    Counts through ``debited_allowance`` and the work log's own ``worked_on``, matching
    the competency the period covers -- the same rule R7 the pool debit follows.
    """
    if not period_ids:
        return {}

    from plane.db.models import ServiceContractPeriod

    totals = {}

    periods = ServiceContractPeriod.objects.filter(pk__in=period_ids).select_related("contract")

    for period in periods:
        total = (
            ServiceLog.objects.filter(
                debited_allowance__isnull=False,
                worked_on__gte=period.starts_on,
                worked_on__lte=period.ends_on,
                project__service_client_id=period.contract.service_client_id,
            ).aggregate(total=Sum("debited_hours"))["total"]
            or ZERO_HOURS
        )

        totals[period.pk] = _quantize(total)

    return totals
