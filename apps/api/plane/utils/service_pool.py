# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Domain logic for contracts and monthly hour pools.

Pure functions over models, with no dependency on views, serializers or DRF, in the
same shape as ``plane.utils.service_log``. Everything that moves a balance lives here
so it can be unit tested without an HTTP layer -- and so that there is exactly one
implementation of each rule, because a second copy of a pool debit is a second answer
to "what does this client owe".

Two invariants hold everything else up, and both are asserted by ``reconcile_period``:

1. **The period carries the balance; the ledger carries the history** (decision D2).
   The period row holds the totals and is the row that ``select_for_update()`` locks;
   the ledger holds every movement. They are written **in the same transaction,
   always**.
2. **No balance moves without a row.** Every reduction *is* a ledger insert, which
   makes acceptance criterion 20 true by construction rather than by diligence.

Concurrency, for acceptance criterion 14: ``transaction.atomic()`` plus
``select_for_update()`` on the period row, and ``consumed_hours`` moved with an ``F()``
expression -- never read, add, write. Concurrent materialisation of the same competency
is resolved by the partial unique index, with a retry that reads the winner's row.
"""

# Python imports
import calendar
from datetime import date

# Django imports
from django.db import IntegrityError, transaction
from django.db.models import F, Q, Sum
from django.utils import timezone

# Module imports
from plane.db.models import (
    ServiceBillingType,
    ServiceContract,
    ServiceContractPeriod,
    ServiceHourLedgerEntry,
    ServiceLedgerEntryType,
    ServiceLog,
    ServiceOveragePolicy,
    ServiceOverageSettlement,
    ServicePeriodStatus,
)
from plane.utils.service_log_time import ZERO_HOURS

# Imported under the module-private name the ~40 call sites below already use. The
# implementation moved to `service_log_time` when Phase 5's allowance domain needed the
# same quantiser: two copies of the money rounding would be two answers to what a client
# owes. Aliased rather than renamed here so the move is one line of diff in a file where
# every line moves a balance.
from plane.utils.service_log_time import quantize_hours as _quantize
from plane.utils.service_money import format_money, overage_amount, quantize_money

# ---------------------------------------------------------------------------
# Error and warning codes
# ---------------------------------------------------------------------------
#
# UPPER_SNAKE, in the style the client entity and the catalogues established. The
# frontend maps them to translated strings; the API never returns Portuguese.

#: The project has no client, so there is no contract to look for. Section 5, step 1.
NO_SERVICE_CLIENT_FOR_PROJECT = "NO_SERVICE_CLIENT_FOR_PROJECT"

#: The client holds no contract at all.
NO_CONTRACT_FOR_CLIENT = "NO_CONTRACT_FOR_CLIENT"

#: The client holds several contracts, none pinned on the project and none marked as
#: the default. Acceptance criterion 24: refuse rather than pick one, because debiting
#: the wrong pool is worse than refusing the entry.
AMBIGUOUS_CONTRACT_RESOLUTION = "AMBIGUOUS_CONTRACT_RESOLUTION"

#: The project pins a contract belonging to a different client than the project's own.
#: A misconfiguration, and the one case where following the pin would silently bill the
#: wrong company.
CONTRACT_PINNED_ON_PROJECT_BELONGS_TO_ANOTHER_CLIENT = "CONTRACT_PINNED_ON_PROJECT_BELONGS_TO_ANOTHER_CLIENT"

#: The competency period is closed. Acceptance criterion 13.
PERIOD_IS_CLOSED = "PERIOD_IS_CLOSED"

#: Closing a period that is already closed.
PERIOD_ALREADY_CLOSED = "PERIOD_ALREADY_CLOSED"

#: A deficit was to be carried, but the contract has no period after this one to carry
#: it into. Forcing the choice is deliberate: writing a deficit off at contract end
#: would be a gift nobody authorised.
NO_NEXT_PERIOD_FOR_DEFICIT = "NO_NEXT_PERIOD_FOR_DEFICIT"

#: Editing the contracted hours of a closed period. Decision B1.
PERIOD_IS_CLOSED_FOR_CONTRACTED_HOURS = "PERIOD_IS_CLOSED_FOR_CONTRACTED_HOURS"

#: Converting a remaining balance into a work item allowance without saying into which
#: work item. Phase 4 held this whole destination open with
#: ``ISSUE_ALLOWANCE_NOT_AVAILABLE`` and an HTTP 501 because the allowance entity did not
#: exist; Phase 5 supplied it, so the only remaining failure is a missing target.
ISSUE_ALLOWANCE_REQUIRES_TARGET_ISSUE = "ISSUE_ALLOWANCE_REQUIRES_TARGET_ISSUE"

#: Warning, never a blocker. D9 and section 1: an entry outside contractual vigency is
#: recorded, flagged in the form and on the work item, and marked in reports. "Nunca
#: descartar o registro por causa de uma pendência comercial."
#:
#: **Since D33 this covers only a vigency that has not started yet.** The two halves of
#: "outside the vigency" acquired opposite destinations in the pricing phase, so they can
#: no longer share one code:
#:
#: * a service date **before** ``starts_on`` still debits the pool -- the contract's first
#:   period, per D31 -- and carries this warning;
#: * a service date **after** ``ends_on`` has no pool left to debit, so D33 settles it as
#:   avulso and it carries ``ServiceRouteDeviation.CONTRACT_EXPIRED`` on the work log
#:   instead of a warning here.
CONTRACT_OUT_OF_VIGENCY = "CONTRACT_OUT_OF_VIGENCY"

#: Warning, never a blocker, and **a distinct code from the one above** -- decision B4,
#: so the operations report can tell a suspended contract from a lapsed one.
CONTRACT_SUSPENDED = "CONTRACT_SUSPENDED"


#: Resolution failures that mean "the configuration is simply ABSENT", and which
#: therefore must **not** cost the technician their work log.
#:
#: This is the one place where the letter of acceptance criterion 24 -- "resolução de
#: contrato ambígua **ou vazia** falha com mensagem explícita" -- is read against section
#: 4, and the two genuinely pull in opposite directions. The reconciliation:
#:
#: * criterion 24's own justification is "debitar o pool errado é pior que bloquear o
#:   apontamento". That reasoning only bites when a **wrong pool could be chosen**, which
#:   is the ambiguous case. With no client and no contract there is no wrong pool -- there
#:   is no pool;
#: * section 4, D4 and D9 all say the same thing three times over: "bloquear gera
#:   apontamento perdido, que é pior que saldo negativo", and "nunca descartar o registro
#:   por causa de uma pendência comercial". A contract that sales has not registered yet
#:   is precisely a commercial pendency;
#: * D21 settled the identical question for the classification engine and made it total,
#:   on the grounds that a bad configuration must never stop **all** logging in a
#:   workspace. The same argument applies unchanged here.
#:
#: So these two fail **loudly but without blocking**: no debit happens, ``debited_period``
#: stays null, and the reason is reported by ``issue_pool_snapshot`` on the work item and
#: in the write response. Ambiguity, a pin to another client's contract, and a closed
#: period all still raise.
#:
#: **This is a deviation from the literal wording of criterion 24 and is flagged as such
#: for confirmation** -- it is a business rule, not a coding choice, and the phase brief
#: says to ask rather than assume.
NON_BLOCKING_RESOLUTION_FAILURES = frozenset(
    {NO_SERVICE_CLIENT_FOR_PROJECT, NO_CONTRACT_FOR_CLIENT}
)


class ServicePoolValidationError(ValueError):
    """A pool operation could not be completed.

    Carries an UPPER_SNAKE ``code`` and, when there is one, a ``detail`` dict for the
    payload. Mirrors ``ServiceLogValidationError`` so callers have one shape to handle.
    """

    def __init__(self, code, detail=None):
        self.code = code
        self.detail = detail or {}
        super().__init__(code)

    @property
    def blocks_the_work_log(self):
        """Whether this failure should stop the work log from being recorded.

        See ``NON_BLOCKING_RESOLUTION_FAILURES``. Asked as a property rather than
        compared at each call site so there is one answer, in one place.
        """
        return self.code not in NON_BLOCKING_RESOLUTION_FAILURES


# ---------------------------------------------------------------------------
# Contract resolution -- section 5, steps 1 and 2
# ---------------------------------------------------------------------------


def contract_warnings(contract, worked_on):
    """Every non-blocking flag that applies to debiting this contract on this date.

    A list, because more than one can be true at once: a contract can be both suspended
    and outside its vigency, and a report that only ever saw one of the two would
    mis-describe it.

    None of these blocks anything. D4 and D9 both say the same thing from different
    directions -- work already performed is recorded regardless of a commercial
    pendency.
    """
    warnings = []

    if contract.status == ServiceContract.Status.SUSPENDED:
        warnings.append(CONTRACT_SUSPENDED)

    # Only a vigency that has not started yet. A service date past `ends_on` is not a
    # warning any more -- D33 settles it as avulso, and `route_deviation_reason` on the
    # work log is where that is recorded. See the comment on CONTRACT_OUT_OF_VIGENCY.
    if worked_on < contract.starts_on:
        warnings.append(CONTRACT_OUT_OF_VIGENCY)

    return warnings


def resolve_contract(issue, worked_on):
    """Which contract a work log on ``issue`` at ``worked_on`` debits.

    Returns ``(contract, warning_code_or_None)``. Raises
    ``ServicePoolValidationError`` when resolution is empty or ambiguous.

    Precedence, from section 1b of the phase brief:

    1. the contract **pinned on the project**, which is how two projects of one client
       debit separate pools (acceptance criterion 22);
    2. otherwise, among the client's contracts: the single one that exists, or the one
       flagged ``is_default`` (criterion 23);
    3. otherwise, an explicit failure (criterion 24). Never an arbitrary choice.

    Within step 2, contracts **in force on the service date are preferred** before the
    default flag is consulted. That ordering matters at a renewal boundary: when an old
    contract and its successor both exist and only one covers the date, the date decides
    -- consulting ``is_default`` first would debit a contract that had not started.
    Nothing is *excluded* for being out of vigency, though; if no contract covers the
    date the whole set is reconsidered, because D9 permits the entry and only warns.

    The returned warning is the highest-precedence element of ``contract_warnings``,
    suspension first: a suspension is an active commercial decision, while a lapsed
    vigency is usually an administrative delay. Callers that need the full set -- the
    alert panel does -- call ``contract_warnings`` directly.
    """
    project = issue.project

    if project.service_contract_id:
        # Read through the manager rather than the descriptor. The foreign key is
        # DO_NOTHING, so it can point at a soft deleted contract, and the forward
        # descriptor resolves through the soft-delete-filtered base manager -- which
        # raises DoesNotExist instead of returning None.
        pinned = ServiceContract.objects.filter(pk=project.service_contract_id).first()

        if pinned is not None:
            if project.service_client_id and str(pinned.service_client_id) != str(project.service_client_id):
                raise ServicePoolValidationError(
                    CONTRACT_PINNED_ON_PROJECT_BELONGS_TO_ANOTHER_CLIENT,
                    {
                        "project_service_client_id": str(project.service_client_id),
                        "contract_service_client_id": str(pinned.service_client_id),
                    },
                )

            return pinned, _primary_warning(pinned, worked_on)

    if not project.service_client_id:
        raise ServicePoolValidationError(NO_SERVICE_CLIENT_FOR_PROJECT, {"project_id": str(project.pk)})

    contracts = list(ServiceContract.objects.filter(service_client_id=project.service_client_id))

    if not contracts:
        raise ServicePoolValidationError(
            NO_CONTRACT_FOR_CLIENT, {"service_client_id": str(project.service_client_id)}
        )

    in_vigency = [contract for contract in contracts if contract.covers(worked_on)]
    candidates = in_vigency or contracts

    if len(candidates) == 1:
        resolved = candidates[0]
        return resolved, _primary_warning(resolved, worked_on)

    defaults = [contract for contract in candidates if contract.is_default]

    if len(defaults) == 1:
        return defaults[0], _primary_warning(defaults[0], worked_on)

    raise ServicePoolValidationError(
        AMBIGUOUS_CONTRACT_RESOLUTION,
        {
            "service_client_id": str(project.service_client_id),
            "candidate_contract_ids": [str(contract.pk) for contract in candidates],
        },
    )


def _primary_warning(contract, worked_on):
    warnings = contract_warnings(contract, worked_on)
    return warnings[0] if warnings else None


# ---------------------------------------------------------------------------
# Period materialisation -- section 2, and R7
# ---------------------------------------------------------------------------


def month_bounds(year, month):
    """First and last calendar day of a competency month."""
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def _period_bounds(contract, year, month):
    """The real boundaries of one competency month of a contract.

    Clipped to the contract's vigency, so a contract starting on the 10th gets a first
    period that runs from the 10th -- which is the fact decision B1 then lets an admin
    price, by editing ``contracted_hours``, instead of the system inventing a pro-rata
    rule.

    **Every caller passes a competency the vigency touches**, so the clip can never
    invert the range. That is guaranteed upstream: ``materialize_contract_periods`` walks
    only inside the vigency, and ``resolve_period`` clamps the service date to it (D31).

    This function used to carry a branch returning the *natural* month bounds for a
    competency the vigency does not touch, added so that a backdated or late entry could
    not violate the ``ends_on >= starts_on`` check constraint. **D31 made that branch
    both unreachable and wrong**: materialising a period outside the vigency invents a
    quota that was never sold, so the entry is clamped or billed instead of getting a
    month of its own. The branch is gone rather than left in place describing a rule the
    system no longer has.
    """
    first, last = month_bounds(year, month)

    return max(first, contract.starts_on), min(last, contract.ends_on)


def competence_date_for(contract, worked_on):
    """The date whose competency month a work log debits. Decision D31.

    Normally ``worked_on`` itself, per R7. The exception is a service date **before the
    contract starts**, which debits the contract's **first** period.

    D31's reasoning, worth keeping next to the arithmetic: a 30h/month contract running
    twelve months sells 360h, and no work log may change that total. Materialising a
    period for February on a contract that starts in March would grant a thirteenth
    monthly quota -- 390h against a 360h contract -- so the hours would be a gift the
    system invented. Anticipating work costs the client hours from the month they
    contracted for; it does not create a month.

    A service date **after** ``ends_on`` never reaches here: D33 settles it as avulso,
    because a contract that has ended has no pool left to debit. That asymmetry is the
    point -- before the start there is a future pool to borrow from, after the end there
    is nothing.
    """
    if worked_on < contract.starts_on:
        return contract.starts_on

    return worked_on


def resolve_period(contract, worked_on, *, materialize=True, actor=None):
    """The competency period of ``worked_on`` for ``contract``. Rule R7.

    The month of the **service date**, never of the moment the row was typed: a
    backdated entry debits the backdated month, which is acceptance criterion 10.

    Materialising a period is what grants its monthly quota, so this writes the
    ``GRANT`` ledger entry in the same transaction as the row. ``contracted_hours``
    starts as a snapshot of ``monthly_hours`` (R4) and may then be edited while the
    period is open, through ``update_contracted_hours``.

    Two requests racing to materialise the same competency both reach ``create``; the
    partial unique index lets one through and the loser reads the winner's row. That is
    why the ``IntegrityError`` is caught rather than prevented -- preventing it would
    need a lock on something that does not exist yet.

    ``materialize=False`` returns ``None`` instead of creating, for callers that only
    want to read -- a dashboard must not create rows as a side effect of being looked
    at.

    **The service date is clamped to the contract's vigency first** (D31, see
    ``competence_date_for``), and the clamp is here rather than in the callers so that
    reading and writing cannot disagree: ``issue_pool_snapshot`` calls this with
    ``materialize=False`` and now previews the very period a debit would use.
    """
    competence = competence_date_for(contract, worked_on)

    existing = ServiceContractPeriod.objects.filter(
        contract_id=contract.pk,
        competence_year=competence.year,
        competence_month=competence.month,
    ).first()

    if existing is not None or not materialize:
        return existing

    starts_on, ends_on = _period_bounds(contract, competence.year, competence.month)

    try:
        with transaction.atomic():
            period = ServiceContractPeriod.objects.create(
                workspace_id=contract.workspace_id,
                contract=contract,
                competence_year=competence.year,
                competence_month=competence.month,
                starts_on=starts_on,
                ends_on=ends_on,
                contracted_hours=_quantize(contract.monthly_hours),
            )
            write_ledger_entry(
                period=period,
                entry_type=ServiceLedgerEntryType.GRANT,
                hours=period.contracted_hours,
                origin_period=period,
                actor=actor,
                notes=f"Cota mensal de {period.competence_label}",
            )
    except IntegrityError:
        # Lost the race. The winner's row is the right one to use.
        return ServiceContractPeriod.objects.filter(
            contract_id=contract.pk,
            competence_year=competence.year,
            competence_month=competence.month,
        ).first()

    return period


def materialize_contract_periods(contract, *, actor=None):
    """Every competency period of a contract's vigency. Acceptance criterion 1.

    Deterministic, as section 2 requires: a 30h/month contract running 12 months
    produces 12 periods, each with its own real bounds, and running it twice produces
    nothing new because ``resolve_period`` is idempotent.
    """
    periods = []
    year, month = contract.starts_on.year, contract.starts_on.month

    while (year, month) <= (contract.ends_on.year, contract.ends_on.month):
        periods.append(resolve_period(contract, date(year, month, 1), actor=actor))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)

    return periods


def next_period(period, *, materialize=True, actor=None):
    """The competency period following ``period``, or ``None`` past the vigency.

    ``None`` is what tells ``close_period`` that a remaining balance has nowhere to go
    and must be written off as ``EXPIRED_BY_CONTRACT_END`` -- section 8, and the reason
    criterion 20 needs an entry type of its own for contract end.
    """
    year, month = period.competence_year, period.competence_month
    year, month = (year + 1, 1) if month == 12 else (year, month + 1)

    contract = period.contract

    if (year, month) > (contract.ends_on.year, contract.ends_on.month):
        return None

    return resolve_period(contract, date(year, month, 1), materialize=materialize, actor=actor)


def annotate_period_balance(queryset):
    """Attach ``granted`` and ``balance`` to a queryset of periods. Decision D3.

    The queryset counterpart of the ``granted_hours`` and ``balance_hours`` properties,
    and the reason neither needs to be a stored column: filtering the alert panel on
    ``balance__lt=0`` works from this without a ``GeneratedField``.

    A ``GeneratedField`` was considered and refused. It would have been the first in the
    repository, introduced in the phase that handles money, and it interacts with three
    things this phase relies on -- ``F()`` plus ``refresh_from_db``, ``update()`` having
    to exclude the generated column, and the behaviour under ``--nomigrations``. What it
    would buy is an index on a table holding one row per contract per month. If a slow
    query ever appears, that is the moment to revisit, with evidence.
    """
    return queryset.annotate(
        granted=F("contracted_hours") + F("carried_hours"),
        balance=F("contracted_hours") + F("carried_hours") - F("consumed_hours"),
    )


# ---------------------------------------------------------------------------
# The ledger
# ---------------------------------------------------------------------------


def write_ledger_entry(
    *,
    period=None,
    allowance=None,
    entry_type,
    hours,
    origin_period=None,
    service_log=None,
    actor=None,
    notes="",
    amount=None,
    applied_hour_rate=None,
):
    """Insert one ledger row. **The only place rows are created.**

    Exactly one of ``period`` and ``allowance`` is given, matching the
    ``service_ledger_entry_has_exactly_one_target`` constraint in DDL. A period entry
    derives its ``contract`` from the period; an allowance entry has no contract, which
    is the isolation rule R6 describes expressed as a null column.

    Named without a leading underscore only so that
    ``plane.utils.service_allowance`` can use it -- **not** because it is part of any
    API. A caller outside the domain layer that could write an arbitrary entry could
    move a balance without moving the owning row's totals, and those two are supposed to
    be impossible to separate. It stays the single writer precisely so that "no balance
    moves without a row" (D23) keeps holding for the allowance as well as for the pool.

    ``amount`` and ``applied_hour_rate`` are Phase 6's monetary pair, and they travel
    together or not at all -- ``service_ledger_amount_only_on_overage_billed`` enforces
    both that and the fact that only ``OVERAGE_BILLED`` may carry them. Extending this
    signature was preferred to a second writer for the reason the whole function exists:
    one place that creates rows is what keeps "no balance moves without a row" checkable.
    Quantised here, at the boundary, exactly as ``hours`` is.
    """
    if (period is None) == (allowance is None):
        # Defence against a caller, not against data: the DDL constraint is what
        # actually guarantees this. Raised here so the traceback points at the mistake
        # rather than at a Postgres constraint name.
        raise ValueError("A ledger entry targets exactly one of a period or an allowance")

    if (amount is None) != (applied_hour_rate is None):
        # Same kind of defence. A value with no rate cannot be audited back to a decision,
        # and a rate with no value is a rate that was never applied.
        raise ValueError("A ledger entry carries an amount and its rate together, or neither")

    owner = period if period is not None else allowance

    return ServiceHourLedgerEntry.objects.create(
        workspace_id=owner.workspace_id,
        contract_id=period.contract_id if period is not None else None,
        period=period,
        allowance=allowance,
        origin_period=origin_period,
        hours=_quantize(hours),
        entry_type=entry_type,
        service_log=service_log,
        actor=actor,
        notes=notes,
        amount=None if amount is None else quantize_money(amount),
        applied_hour_rate=None if applied_hour_rate is None else quantize_money(applied_hour_rate),
    )


def _lock_period(period):
    """Re-read a period under a row lock. Acceptance criterion 14.

    ``select_for_update()`` serialises everything that touches one period's totals. Every
    function here that moves a total goes through this first, and every one of them then
    writes with an ``F()`` expression rather than assigning a value it read earlier.

    **The two are independently sufficient for the arithmetic, and that was established
    by sabotage rather than by reasoning.** Removing the lock leaves ``F()`` doing the
    increment in SQL; removing ``F()`` leaves the lock serialising the read-modify-write.
    The concurrency test only goes red when **both** are removed -- and then
    catastrophically, losing seven debits out of eight. Recorded here because the obvious
    thing to write in this docstring, and what an earlier draft did write, is that a test
    covers each half separately. It does not, and it cannot: with either half present the
    behaviour is correct, so there is nothing for a test to detect.

    What the lock is *not* redundant for is the read-then-decide sequence around
    ``status``. ``apply_debit`` checks that the period is open and then debits it; without
    the lock a concurrent ``close_period`` fits between those two steps and the debit
    lands in a month that has just been invoiced. That is the part ``F()`` cannot cover,
    and it has its own test.
    """
    return ServiceContractPeriod.objects.select_for_update().get(pk=period.pk)


def _work_item_allowance(issue):
    """The hour allowance that takes precedence over the pool. Rule R6, first level.

    Kept as a named function here, rather than inlining the call, because R6 is a
    *hierarchy* and the order is the part that is easy to get wrong later: allowance
    first, then the contract pool, then a monetary charge, and **never two of them**.
    This is the seam that keeps the order visible in ``apply_debit``.

    Delegates to ``plane.utils.service_allowance.resolve_work_item_allowance``, which
    resolves through the work item tree -- nearest ancestor with an allowance wins -- and
    which returns an allowance **regardless of its status**. Filtering to open ones here
    would let a closed allowance fall through to the contract pool, and that fallback is
    what section 3 of the Phase 5 brief forbids outright.

    The import is function level to keep the two domain modules from importing each other
    at load time. Same pattern as ``update_contracted_hours`` and ``ServiceLog.delete``.
    """
    from plane.utils.service_allowance import resolve_work_item_allowance

    return resolve_work_item_allowance(issue)


def apply_debit(service_log, actor=None):
    """Debit one work log's hours from its competency pool. Section 5.

    Returns the ``DEBIT`` ledger entry, or ``None`` when nothing was debited.

    **Idempotent, and by the database rather than by a check.** The partial unique index
    on ``(service_log, entry_type)`` is what makes a second call a no-op, which is why
    the ledger row is inserted *before* the total moves: if the insert loses, the total
    is untouched. A pre-flight ``exists()`` would have a window between the check and
    the insert that two concurrent requests fit through.

    Debits ``debited_hours``, never ``equivalent_hours``. R5 and the check constraint
    ``service_log_debited_hours_follows_billing_route`` together guarantee a
    ``NON_BILLABLE`` row carries zero there -- so acceptance criterion 4, "Garantia e
    Cortesia não alteram o saldo", holds because of the column that is read, not because
    of a branch that could be forgotten.

    Returns ``None`` for four different reasons, and they are **not**
    interchangeable -- the route is not a pool route, the debited hours are zero, the work
    item has its own allowance (Phase 5), or the contract configuration is absent. The
    caller distinguishes them through ``issue_pool_snapshot``, which reports the
    resolution code. A caller that only checked the balance had not moved would be unable
    to tell a non-billable log from a missing contract, and those are opposite faults.

    **Since Phase 6 this is the pool half of a larger decision**, and it is no longer the
    entry point the API calls. ``plane.utils.service_billing.settle_service_log`` decides
    between the pool, a monetary charge and no charge at all -- because D33 turned "there
    is no usable contract" from "debit nothing" into "bill it as avulso", and that choice
    needs the contract resolution this function used to keep to itself. The resolution now
    happens once, in ``resolve_settlement``, and the mechanical debit lives in
    ``debit_resolved_pool`` below.

    This function is kept, unchanged in behaviour, because it is the honest expression of
    "debit this log from its pool" and Phase 4's and Phase 5's tests are written against
    it. It resolves and then delegates.
    """
    if service_log.applied_billing_route != ServiceBillingType.BillingRoute.DEBIT_POOL:
        # Not a pool route. R5 for NON_BILLABLE; a monetary route is priced by
        # `plane.utils.service_billing`, never debited here.
        return None

    if service_log.debited_hours == ZERO_HOURS:
        return None

    allowance = _work_item_allowance(service_log.issue)

    if allowance is not None:
        # Rule R6, first level. The work item -- or an ancestor of it -- has an allowance,
        # so the allowance pays and the contract pool is **not** consulted at all. This
        # `return` is what makes acceptance criterion 7 true in the code; the unique index
        # on `(service_log, entry_type)` is what makes it true even if this line were
        # wrong.
        from plane.utils.service_allowance import debit_allowance

        return debit_allowance(service_log, allowance, actor=actor)

    try:
        contract, _warning = resolve_contract(service_log.issue, service_log.worked_on)
    except ServicePoolValidationError as error:
        if error.blocks_the_work_log:
            raise

        # Configuration merely absent: no pool to debit, and no wrong pool to debit
        # either. The work log stands with `debited_period` null, and the reason is
        # reported by `issue_pool_snapshot` rather than being silently dropped -- "não
        # faturável" and "não existe contrato" produce the same balance and are opposite
        # faults, so the absence has to say which one it is.
        #
        # Note that a caller coming through `settle_service_log` never reaches this: D33
        # settles an absent contract as avulso, so the log is priced instead.
        return None

    return debit_resolved_pool(service_log, contract=contract, actor=actor)


def debit_resolved_pool(service_log, *, contract, actor=None):
    """Move the hours, given a contract that is already resolved. The mechanical half.

    Split out of ``apply_debit`` by Phase 6 so that the **decision** of which contract
    applies happens exactly once. ``plane.utils.service_billing.resolve_settlement`` has
    to resolve the contract in order to apply D33 -- an expired or suspended contract is
    billed rather than debited -- and if this function did not exist it would resolve it,
    hand back "debit the pool", and then ``apply_debit`` would resolve it a second time.
    Two resolutions is two chances to disagree about which pool pays, which is precisely
    the class of bug the R4 snapshot exists to make impossible after the fact.

    Assumes the caller has already established that the route debits a pool, that the
    hours are non-zero and that no allowance applies. Raises ``PERIOD_IS_CLOSED``.
    """
    with transaction.atomic():
        period = resolve_period(contract, service_log.worked_on, actor=actor)
        locked = _lock_period(period)

        if locked.status == ServicePeriodStatus.CLOSED:
            raise ServicePoolValidationError(
                PERIOD_IS_CLOSED,
                {"period_id": str(locked.pk), "competence": locked.competence_label},
            )

        try:
            with transaction.atomic():
                entry = write_ledger_entry(
                    period=locked,
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

        ServiceContractPeriod.objects.filter(pk=locked.pk).update(
            consumed_hours=F("consumed_hours") + _quantize(service_log.debited_hours)
        )

        # R4's snapshot of which pool paid. Written with `update()` so that saving the
        # work log cannot re-run its own side effects from inside a debit.
        ServiceLog.all_objects.filter(pk=service_log.pk).update(debited_period_id=locked.pk)
        service_log.debited_period_id = locked.pk

    return entry


def apply_batch_debit(rows, actor=None):
    """Debit every segment of a batch, all of it or none of it.

    A batch can straddle two competencies -- 31/01 23:00 to 01/02 01:00 -- and each
    segment debits **its own** period, because each segment carries its own
    ``worked_on`` (R7). If any one of them is refused, the whole batch is refused: half
    a batch debited is worse than a batch rejected, which is the same argument
    ``create_service_log_batch`` makes for its own atomicity.
    """
    with transaction.atomic():
        return [entry for entry in (apply_debit(row, actor=actor) for row in rows) if entry is not None]


def reverse_debit(service_log, actor=None):
    """Give back exactly the hours a work log took. Acceptance criteria 11 and 12.

    Returns the ``REVERSAL`` entry, ``None`` when there was nothing to reverse.

    Exact by construction: the reversal reads the amount from the ``DEBIT`` row rather
    than recomputing it from the work log. Recomputing would give a different answer the
    moment the work log's own hours were what changed -- which is precisely criterion
    12, editing 2h to 3h.

    **Idempotent for the same reason a debit is**, and here it is not theoretical: the
    deletion cascade runs in a Celery task and Celery retries tasks, so without the
    unique index covering ``REVERSAL`` a retry would credit hours that never existed.

    **Refuses on a closed period, deliberately.** A closed month has been invoiced and
    its ledger has been settled to zero; handing hours back into it would change a total
    a client has already been billed for. The caller decides what to do about that --
    ``ServiceLog.delete()`` lets the error propagate so the API can explain it, and the
    deletion cascade skips that work log and leaves it intact rather than deleting a row
    whose money it cannot unwind. A **closed allowance** refuses for the same reason and
    behaves the same way.

    **Where the hours go is read off the ``DEBIT`` row, not resolved again.** A debit
    names either a period or an allowance -- exactly one, by check constraint -- so this
    dispatches on a column instead of re-asking rule R6. That is Phase 5's acceptance
    criterion 6: deleting a work log gives the hours back to the allowance, never to the
    contract.
    """
    debit = ServiceHourLedgerEntry.objects.filter(
        service_log_id=service_log.pk, entry_type=ServiceLedgerEntryType.DEBIT
    ).first()

    if debit is None:
        return None

    existing = ServiceHourLedgerEntry.objects.filter(
        service_log_id=service_log.pk, entry_type=ServiceLedgerEntryType.REVERSAL
    ).first()

    if existing is not None:
        return existing

    if debit.allowance_id is not None:
        # **The origin comes from the persisted debit, never from re-resolving R6**, and
        # that is Phase 5's acceptance criterion 6 in one line. Re-resolving would send
        # the hours to the contract pool the moment the allowance was closed, and to a
        # different allowance the moment the work item was re-parented -- both of them
        # silent, and both of them the wrong client's hours. One query, one row, and the
        # answer is a column on it.
        from plane.utils.service_allowance import reverse_allowance_debit

        return reverse_allowance_debit(service_log, debit, actor=actor)

    with transaction.atomic():
        locked = _lock_period(debit.period)

        if locked.status == ServicePeriodStatus.CLOSED:
            raise ServicePoolValidationError(
                PERIOD_IS_CLOSED,
                {"period_id": str(locked.pk), "competence": locked.competence_label},
            )

        returned = -debit.hours

        try:
            with transaction.atomic():
                entry = write_ledger_entry(
                    period=locked,
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

        ServiceContractPeriod.objects.filter(pk=locked.pk).update(
            consumed_hours=F("consumed_hours") - _quantize(returned)
        )

    return entry


# ---------------------------------------------------------------------------
# FIFO parcels -- decision D6, and acceptance criterion 6
# ---------------------------------------------------------------------------


def period_parcels(period):
    """The parcels of balance available in a period, oldest competency first.

    A parcel is a ``GRANT`` or a ``CARRY_IN``, and its age is its ``origin_period``.
    The month's own quota is a parcel originating in that month, which makes it the
    newest -- so under FIFO it is consumed last.

    Returned as ``[(origin_period, hours)]``, hours signed. Ordering by competency and
    not by ``created_at``: a backdated carry-in must sort by where the hours came from,
    not by when the row happened to be written.
    """
    return period_parcels_bulk([period])[period.pk]


def period_parcels_bulk(periods):
    """``period_parcels`` for several periods in **one** query. Phase 9.

    ``{period_id: [(origin_period, hours)]}``, every requested period present even when it
    has no parcels, so a caller never has to distinguish "no entry" from "not asked".

    Added because ``contract_balance_statement`` walked a contract's periods calling
    ``period_parcels`` on each: a 36-month contract cost 37 queries, and the statement is
    the spine of Phase 9's contract dashboard. ``period_parcels`` now delegates here with a
    list of one, so the grouping rule -- several rows may share an origin, per decision B1's
    grant correction -- exists once rather than in a fast path and a slow path that can
    drift.
    """
    by_id = {period.pk: period for period in periods}

    if not by_id:
        return {}

    entries = (
        ServiceHourLedgerEntry.objects.filter(
            period_id__in=list(by_id),
            entry_type__in=[ServiceLedgerEntryType.GRANT, ServiceLedgerEntryType.CARRY_IN],
        )
        .select_related("origin_period")
        .order_by("origin_period__competence_year", "origin_period__competence_month", "created_at")
    )

    parcels = {period_id: {} for period_id in by_id}

    for entry in entries:
        period = by_id[entry.period_id]
        origin = entry.origin_period or period
        # Several rows can share an origin: a `GRANT` correction under decision B1 adds
        # a second grant row for the same month.
        bucket = parcels[entry.period_id]
        bucket[origin.pk] = (origin, bucket.get(origin.pk, (origin, ZERO_HOURS))[1] + entry.hours)

    return {
        period_id: sorted(found.values(), key=lambda parcel: parcel[0].competence_index)
        for period_id, found in parcels.items()
    }


def remaining_parcels(period, parcels=None):
    """What is left of each parcel after consumption, oldest first. Decision D6.

    **FIFO: consumption eats the oldest parcel first.** The brief never states this, and
    without it acceptance criterion 6 has no determinate answer -- "the January balance
    expires at the right moment" depends on whether January's hours were the ones spent.
    The consequence is the reason for the choice: carried hours are spent before the
    current month's quota, so **no hour ever expires that could have been used**. The
    opposite order would make a client lose balance while their pool was full, and they
    would be right to complain.

    Allocation happens **here, at read time**, not at debit time. That is what lets a
    debit be a single row against the period under one lock, instead of a row per parcel
    touched -- decision D2, and the reason criterion 14 is achievable at all.

    A negative parcel is a carried deficit. It is not something consumption can eat, so
    it is folded into the amount to be absorbed by the positive parcels: an obligation
    reduces the pool exactly as consumption does.

    ``parcels`` may be supplied by a caller that already read them in bulk. The FIFO
    allocation itself stays here either way -- ``contract_balance_statement`` passes them in
    to avoid one query per period, and passing the *logic* around instead would be how a
    dashboard and a close come to disagree about which hours were spent.
    """
    if parcels is None:
        parcels = period_parcels(period)

    outstanding = _quantize(period.consumed_hours) + sum(
        (-hours for _origin, hours in parcels if hours < 0), ZERO_HOURS
    )

    remaining = []

    for origin, hours in parcels:
        if hours <= 0:
            continue

        if outstanding <= 0:
            remaining.append((origin, hours))
            continue

        if outstanding >= hours:
            outstanding -= hours
            continue

        remaining.append((origin, hours - outstanding))
        outstanding = ZERO_HOURS

    return remaining


def _parcel_has_expired(contract, parcel_origin, closing_period):
    """Whether a parcel is too old to be carried past ``closing_period``.

    ``carryover_months`` counts **carries**, and the boundary is written out here
    because an off-by-one deletes a client's hours. With 2, a parcel originating in
    January is usable in February and March and expires at the close of March:
    ``months_between(January, March) == 2 >= 2``.

    ``None`` means the balance never expires, which section 3 makes the explicit
    default.
    """
    if contract.carryover_months is None:
        return False

    return (closing_period.competence_index - parcel_origin.competence_index) >= contract.carryover_months


# ---------------------------------------------------------------------------
# Closing a period -- section 6
# ---------------------------------------------------------------------------


def close_period(period, settlement=None, actor=None):
    """Settle a competency month and open the next one correctly. Section 6.

    Closing does four things, in this order, and the order is load bearing:

    1. a **deficit** is either carried into the next period or billed as overage
       (acceptance criteria 8 and 9);
    2. a **positive balance** is broken into its FIFO parcels, and any parcel past its
       carryover validity expires (criterion 6);
    3. what survives is trimmed to the accrual ceiling, and the trimmed hours are
       recorded rather than dropped (criterion 15);
    4. the rest is carried out, parcel by parcel with its origin preserved, so the next
       close can apply validity to it again.

    Every one of those four writes a ledger row, which is what makes criterion 20 --
    "saldo nunca desaparece sem registro de auditoria" -- true here without a separate
    audit mechanism. When this returns, the period's ledger sums to exactly zero.

    ``settlement`` is only consulted when there is a deficit, and it defaults to the
    contract's ``overage_policy``. Section 4 requires the choice to be confirmable
    period by period, which is why the binding value is stored on the period and the
    contract only holds a preference.
    """
    with transaction.atomic():
        locked = _lock_period(period)

        if locked.status == ServicePeriodStatus.CLOSED:
            raise ServicePoolValidationError(
                PERIOD_ALREADY_CLOSED,
                {"period_id": str(locked.pk), "competence": locked.competence_label},
            )

        contract = locked.contract
        balance = locked.balance_hours

        if balance < 0:
            _settle_deficit(locked, contract, balance, settlement, actor)
        elif balance > 0:
            _settle_surplus(locked, contract, actor)

        locked.status = ServicePeriodStatus.CLOSED
        locked.closed_at = timezone.now()
        locked.closed_by = actor
        locked.save(
            update_fields=[
                "status",
                "closed_at",
                "closed_by",
                "overage_hours",
                "overage_settlement",
                "discarded_by_cap_hours",
                "updated_at",
            ]
        )

    return locked


def _settle_deficit(period, contract, balance, settlement, actor):
    """A negative balance, settled one of the two ways section 4 allows.

    Decision B3, confirmed: billing the overage moves the deficit **out** of the pool,
    so the next month opens with its contracted hours whole -- which is what criterion 9
    asks for and what makes "pay for this month's excess rather than mortgage the rest
    of the contract" a real option instead of a relabelling.

    **Billing now carries a value in reais, and can therefore be refused.** See
    ``_bill_period_overage``.
    """
    owed = -balance

    if settlement is None:
        settlement = (
            ServiceOverageSettlement.BILLED
            if contract.overage_policy == ServiceOveragePolicy.BILL_AMOUNT
            else ServiceOverageSettlement.CARRIED
        )

    if settlement == ServiceOverageSettlement.BILLED:
        _bill_period_overage(period, contract, owed, actor)
        return

    following = next_period(period, actor=actor)

    if following is None:
        raise ServicePoolValidationError(
            NO_NEXT_PERIOD_FOR_DEFICIT,
            {"period_id": str(period.pk), "deficit_hours": str(owed)},
        )

    write_ledger_entry(
        period=period,
        entry_type=ServiceLedgerEntryType.CARRY_OUT,
        hours=owed,
        origin_period=period,
        actor=actor,
        notes=f"Deficit de {owed}h transportado para {following.competence_label}",
    )
    write_ledger_entry(
        period=following,
        entry_type=ServiceLedgerEntryType.CARRY_IN,
        hours=-owed,
        origin_period=period,
        actor=actor,
        notes=f"Deficit de {owed}h recebido de {period.competence_label}",
    )
    ServiceContractPeriod.objects.filter(pk=following.pk).update(
        carried_hours=F("carried_hours") - _quantize(owed)
    )
    period.overage_settlement = ServiceOverageSettlement.CARRIED


def overage_billing_preview(period):
    """What billing this period's overage would cost, computed without billing it.

    Returns ``{"overage_hours", "overage_hour_rate", "amount", "rate_source",
    "is_reversible"}``, all monetary values as strings, or raises
    ``ServicePricingValidationError(OVERAGE_RATE_NOT_CONFIGURED)`` when no rate resolves.

    **This exists because billing overage is irreversible**, and an irreversible mistake
    has to be deliberate rather than careless. There is no ``reverse_overage_billing`` in
    this system -- see ``docs/worklog/DECISOES.md`` D42 for what one would require -- so the
    confirmation an Admin sees must show the value and the rate *before* the action, and
    say plainly that it cannot be undone. ``is_reversible`` is a constant ``False`` rather
    than an omission, so the API states the fact instead of leaving the client to know it.

    Computed by the same functions the close uses, for the reason the work log preview
    gives: a preview computed by different code than the action is a preview that can lie.
    """
    from plane.utils.service_pricing import resolve_overage_rate

    contract = period.contract
    balance = period.balance_hours
    owed = _quantize(-balance) if balance < 0 else ZERO_HOURS

    rate = resolve_overage_rate(
        service_client_id=contract.service_client_id,
        # Decision C: the sheet in force when the COMPETENCY BEGAN. Closing in April must
        # not put April's readjusted price on March's invoice, and `starts_on` rather than
        # `ends_on` also means a readjustment never reaches backwards over a month that is
        # already partly worked. Price sheets are pinned to the first of a month in DDL, so
        # the two readings coincide anyway -- this is belt and braces on purpose.
        on_date=period.starts_on,
        contract=contract,
    )

    return {
        "overage_hours": str(owed),
        "overage_hour_rate": str(quantize_money(rate)),
        "amount": str(overage_amount(overage_hours=owed, overage_hour_rate=rate)),
        "rate_source": ("contract" if contract.overage_hour_rate is not None else "client_base_rate"),
        "is_reversible": False,
    }


def _bill_period_overage(period, contract, owed, actor):
    """Bill a period's deficit in reais. Acceptance criteria 7, 8 and 9.

    **The one place in this phase that refuses rather than recording an absence.** When no
    rate resolves, ``resolve_overage_rate`` raises and the close is refused with
    ``OVERAGE_RATE_NOT_CONFIGURED``.

    That is not a contradiction of D27, D9 and D21, and the distinction is the whole
    reason it is safe: those protect **work already executed by a technician**, where
    refusing would destroy a record of real labour over configuration the technician does
    not control. Closing a period is a **deliberate administrative act, at a moment of
    choice, with a legitimate alternative already available** -- settle as ``CARRIED``
    instead. Billing at R$ 0,00 would silently zero a real deficit, destroying the debt
    with no trace, and that is the worst of the three outcomes.

    Acceptance criterion 8 is answered by ``overage_amount``'s signature rather than by a
    branch here: ``owed`` is accumulated from ``debited_hours``, which is already
    equivalent, and the function has no multiplier parameter to pass one through.
    """
    from plane.utils.service_pricing import resolve_overage_rate

    rate = resolve_overage_rate(
        service_client_id=contract.service_client_id,
        on_date=period.starts_on,
        contract=contract,
    )
    amount = overage_amount(overage_hours=owed, overage_hour_rate=rate)

    write_ledger_entry(
        period=period,
        entry_type=ServiceLedgerEntryType.OVERAGE_BILLED,
        hours=owed,
        actor=actor,
        notes=(
            f"Excedente de {_quantize(owed)}h faturado a {format_money(rate)}/h "
            f"= {format_money(amount)}, competencia {period.competence_label}"
        ),
        amount=amount,
        applied_hour_rate=rate,
    )

    period.overage_hours = _quantize(owed)
    period.overage_settlement = ServiceOverageSettlement.BILLED


def _settle_surplus(period, contract, actor):
    """A positive balance: expire what is stale, trim to the ceiling, carry the rest."""
    surviving = []

    for origin, hours in remaining_parcels(period):
        if _parcel_has_expired(contract, origin, period):
            write_ledger_entry(
                period=period,
                entry_type=ServiceLedgerEntryType.EXPIRED_BY_VALIDITY,
                hours=-hours,
                origin_period=origin,
                actor=actor,
                notes=(
                    f"{hours}h da competencia {origin.competence_label} expiraram: "
                    f"validade de {contract.carryover_months} meses"
                ),
            )
            continue

        surviving.append((origin, hours))

    following = next_period(period, actor=actor)

    if following is None:
        # Section 8: the contract's vigency is over, so there is nowhere to carry to.
        # Written off explicitly, with its own entry type, because "the balance ran out
        # of contract" is a different fact from "the balance ran out of validity" and a
        # renewal negotiation turns on which one it was.
        for origin, hours in surviving:
            write_ledger_entry(
                period=period,
                entry_type=ServiceLedgerEntryType.EXPIRED_BY_CONTRACT_END,
                hours=-hours,
                origin_period=origin,
                actor=actor,
                notes=f"{hours}h da competencia {origin.competence_label} expiraram no fim da vigencia",
            )
        return

    surviving = _apply_accrual_cap(period, contract, surviving, actor)

    for origin, hours in surviving:
        if hours <= 0:
            continue

        write_ledger_entry(
            period=period,
            entry_type=ServiceLedgerEntryType.CARRY_OUT,
            hours=-hours,
            origin_period=origin,
            actor=actor,
            notes=f"{hours}h da competencia {origin.competence_label} transportadas",
        )
        write_ledger_entry(
            period=following,
            entry_type=ServiceLedgerEntryType.CARRY_IN,
            hours=hours,
            origin_period=origin,
            actor=actor,
            notes=f"{hours}h da competencia {origin.competence_label} recebidas",
        )

    carried_total = sum((hours for _origin, hours in surviving if hours > 0), ZERO_HOURS)

    if carried_total:
        ServiceContractPeriod.objects.filter(pk=following.pk).update(
            carried_hours=F("carried_hours") + _quantize(carried_total)
        )


def _apply_accrual_cap(period, contract, parcels, actor):
    """Trim carried balance to the contract's ceiling. Acceptance criterion 15.

    **The excess is discarded from the NEWEST parcels first**, and the brief does not
    settle this, so it is a decision rather than a detail. Two reasons, and they agree:
    under FIFO the oldest parcels are the ones consumption reaches first, so keeping
    them means the surviving balance is the balance that will actually get used; and it
    matches what a client expects to be told, which is "you lost the hours you did not
    use this month", not "you lost hours from last quarter". A characterisation test
    fixes the order so that changing it has to be deliberate.

    The discarded total lands on ``discarded_by_cap_hours`` as well as in the ledger,
    because section 3 wants it *visible*: hours thrown away by the ceiling are the
    clearest evidence that a client is paying for a pool larger than they use, which
    section 9 treats as a churn signal.
    """
    cap = contract.accrual_cap_hours()

    if cap is None:
        return parcels

    total = sum((hours for _origin, hours in parcels), ZERO_HOURS)
    excess = _quantize(total) - _quantize(cap)

    if excess <= 0:
        return parcels

    discarded = ZERO_HOURS
    trimmed = list(parcels)

    for index in range(len(trimmed) - 1, -1, -1):
        if excess <= 0:
            break

        origin, hours = trimmed[index]
        take = min(hours, excess)

        write_ledger_entry(
            period=period,
            entry_type=ServiceLedgerEntryType.EXPIRED_BY_CAP,
            hours=-take,
            origin_period=origin,
            actor=actor,
            notes=(
                f"{take}h da competencia {origin.competence_label} descartadas pelo teto de acumulo "
                f"de {_quantize(cap)}h"
            ),
        )

        trimmed[index] = (origin, hours - take)
        discarded += take
        excess -= take

    period.discarded_by_cap_hours = _quantize(period.discarded_by_cap_hours + discarded)

    return [(origin, hours) for origin, hours in trimmed if hours > 0]


def update_contracted_hours(period, contracted_hours, actor):
    """Change a period's contracted hours while it is open. Decision B1.

    This is how a **partial month** is priced. Neither pro-rata nor "always the full
    month": ``contracted_hours`` is already a per-period snapshot, so making it editable
    turns pro-rata into a datum the admin supplies when the contract says so, instead of
    a rule the system invents from a policy column that does not exist.

    Refused on a closed period, and every change lands in the configuration audit trail
    -- both mandatory, because an editable billing quantity with no trail is a way to
    rewrite an invoiced month quietly.

    The balance moves through a ``GRANT`` row for the difference. In an append-only
    journal a correction is a new row, never an edited one.
    """
    from plane.utils.service_catalog import save_with_config_activity

    with transaction.atomic():
        locked = _lock_period(period)

        if locked.status == ServicePeriodStatus.CLOSED:
            raise ServicePoolValidationError(
                PERIOD_IS_CLOSED_FOR_CONTRACTED_HOURS,
                {"period_id": str(locked.pk), "competence": locked.competence_label},
            )

        previous = _quantize(locked.contracted_hours)
        target = _quantize(contracted_hours)
        delta = target - previous

        if delta == 0:
            return locked

        locked.contracted_hours = target
        save_with_config_activity(locked, actor=actor)

        write_ledger_entry(
            period=locked,
            entry_type=ServiceLedgerEntryType.GRANT,
            hours=delta,
            origin_period=locked,
            actor=actor,
            notes=f"Ajuste da cota de {previous}h para {target}h",
        )

    return locked


# ---------------------------------------------------------------------------
# Reconciliation -- decision D2
# ---------------------------------------------------------------------------


def reconcile_period(period):
    """Assert that the ledger and the period's totals still agree. Decision D2.

    Returns ``{"is_consistent", "competence", "discrepancies"}``, where each discrepancy
    names the column, what the ledger says, and what the row says.

    **Per column, not one grand total.** A single aggregate would be satisfied by two
    errors that cancel, and it would report "something is wrong" when what an operator
    needs is which number to trust. The columns and their ledger counterparts:

    ==========================  ===============================================
    ``contracted_hours``        sum of ``GRANT``
    ``carried_hours``           sum of ``CARRY_IN``
    ``consumed_hours``          negated sum of ``DEBIT`` and ``REVERSAL``
    ``overage_hours``           sum of ``OVERAGE_BILLED``
    ``discarded_by_cap_hours``  negated sum of ``EXPIRED_BY_CAP``
    ==========================  ===============================================

    Plus the whole-ledger invariant, which is the one that catches a movement written
    with the wrong sign: an **open** period's entries sum to its balance, and a
    **closed** period's sum to exactly zero.

    Reading this without a lock is deliberate. It is a diagnostic, it must be cheap
    enough to run across a workspace from a management command, and a debit landing
    mid-scan shows up as a discrepancy that disappears on the next run -- whereas
    locking every period to read it would block the debits it is measuring.
    """
    sums = {
        row["entry_type"]: row["total"]
        for row in ServiceHourLedgerEntry.objects.filter(period_id=period.pk)
        .values("entry_type")
        .annotate(total=Sum("hours"))
    }

    def total(*entry_types):
        return sum((sums.get(entry_type, ZERO_HOURS) for entry_type in entry_types), ZERO_HOURS)

    expectations = [
        ("contracted_hours", total(ServiceLedgerEntryType.GRANT), period.contracted_hours),
        ("carried_hours", total(ServiceLedgerEntryType.CARRY_IN), period.carried_hours),
        (
            "consumed_hours",
            -total(ServiceLedgerEntryType.DEBIT, ServiceLedgerEntryType.REVERSAL),
            period.consumed_hours,
        ),
        ("overage_hours", total(ServiceLedgerEntryType.OVERAGE_BILLED), period.overage_hours),
        (
            "discarded_by_cap_hours",
            -total(ServiceLedgerEntryType.EXPIRED_BY_CAP),
            period.discarded_by_cap_hours,
        ),
    ]

    discrepancies = [
        {"field": field, "ledger": str(_quantize(ledger)), "period": str(_quantize(stored))}
        for field, ledger, stored in expectations
        if _quantize(ledger) != _quantize(stored)
    ]

    ledger_total = sum(sums.values(), ZERO_HOURS)
    expected_total = ZERO_HOURS if period.status == ServicePeriodStatus.CLOSED else period.balance_hours

    if _quantize(ledger_total) != _quantize(expected_total):
        discrepancies.append(
            {
                "field": "ledger_total",
                "ledger": str(_quantize(ledger_total)),
                "period": str(_quantize(expected_total)),
            }
        )

    discrepancies.extend(_overage_amount_discrepancies(period_id=period.pk))

    return {
        "period_id": str(period.pk),
        "competence": period.competence_label,
        "is_consistent": not discrepancies,
        "discrepancies": discrepancies,
    }


def _overage_amount_discrepancies(*, period_id=None, allowance_id=None):
    """Check that a billed overage's value still equals its hours times its rate.

    **The only monetary reconciliation there is, and the reason there is only one is that
    Phase 6 added no monetary accumulator to reconcile against.** The value lives on the
    ``OVERAGE_BILLED`` ledger row beside the hours and the rate that produced it, so the
    single thing that can be wrong is the arithmetic between those three -- there is no
    second column holding a total that could drift from the journal. That is the payoff for
    reusing the ledger instead of opening a monetary one (D29's argument applied to reais).

    Reported in the same ``{"field", "ledger", "period"}`` shape the hour discrepancies use,
    because ``reconcile_service_periods`` prints those three keys and a fourth shape would
    print blank.

    Rows written before this phase carry no amount and are skipped rather than flagged:
    they are real history from before prices existed, which is the same accepted limitation
    that keeps ``service_ledger_amount_only_on_overage_billed`` one-directional.
    """
    entries = ServiceHourLedgerEntry.objects.filter(
        entry_type=ServiceLedgerEntryType.OVERAGE_BILLED,
        amount__isnull=False,
    )

    if period_id is not None:
        entries = entries.filter(period_id=period_id)
    else:
        entries = entries.filter(allowance_id=allowance_id)

    discrepancies = []

    for entry in entries:
        expected = overage_amount(overage_hours=entry.hours, overage_hour_rate=entry.applied_hour_rate)

        if quantize_money(entry.amount) != expected:
            discrepancies.append(
                {
                    "field": f"overage_amount[{entry.pk}]",
                    "ledger": str(expected),
                    "period": str(quantize_money(entry.amount)),
                }
            )

    return discrepancies


def repair_period(period):
    """Rewrite a period's totals from its ledger. The repair half of reconciliation.

    Used by the ``reconcile_service_periods`` management command with ``--repair``.

    **The ledger wins, always.** It is append-only and every row records its own cause,
    so it is the only one of the two that can be audited; the totals are a cache of it.
    That is also why this direction is the only one offered -- "fix the ledger from the
    totals" would mean inventing movements.

    This exists because the explicit branch added to the deletion cascade closes
    *today's* path to divergence, and any future ``update(deleted_at=...)`` on a work
    log reopens the same class of bug. A cheap, tested repair is the difference between
    that being an incident and being a command someone runs.
    """
    with transaction.atomic():
        locked = _lock_period(period)

        sums = {
            row["entry_type"]: row["total"]
            for row in ServiceHourLedgerEntry.objects.filter(period_id=locked.pk)
            .values("entry_type")
            .annotate(total=Sum("hours"))
        }

        def total(*entry_types):
            return sum((sums.get(entry_type, ZERO_HOURS) for entry_type in entry_types), ZERO_HOURS)

        locked.contracted_hours = _quantize(total(ServiceLedgerEntryType.GRANT))
        locked.carried_hours = _quantize(total(ServiceLedgerEntryType.CARRY_IN))
        locked.consumed_hours = _quantize(
            -total(ServiceLedgerEntryType.DEBIT, ServiceLedgerEntryType.REVERSAL)
        )
        locked.overage_hours = _quantize(total(ServiceLedgerEntryType.OVERAGE_BILLED))
        locked.discarded_by_cap_hours = _quantize(-total(ServiceLedgerEntryType.EXPIRED_BY_CAP))

        locked.save(
            update_fields=[
                "contracted_hours",
                "carried_hours",
                "consumed_hours",
                "overage_hours",
                "discarded_by_cap_hours",
                "updated_at",
            ]
        )

    return locked


# ---------------------------------------------------------------------------
# Renewal and termination -- section 8
# ---------------------------------------------------------------------------


class BalanceDestination:
    """Where a remaining balance goes when a contract is replaced. Section 8c."""

    TRANSFER = "transfer"
    EXPIRE = "expire"
    ISSUE_ALLOWANCE = "issue_allowance"


def renew_in_place(contract, *, ends_on, monthly_hours=None, actor):
    """Extend a contract, keeping it and its history. Section 8a, criterion 16.

    The accumulated balance carries because nothing interrupts it: the periods keep
    materialising from the same contract, so the next close carries out into the next
    month exactly as any other month does. That is the point of this path -- "mantém a
    continuidade do histórico" is not a nicety, it is the absence of a transfer.

    ``monthly_hours`` is optional and typically **lower** on a renewal, precisely
    because there is a balance built up. Both fields are in ``TRACKED_FIELDS``, so the
    change lands in the configuration audit trail with its old value.
    """
    from plane.utils.service_catalog import save_with_config_activity

    with transaction.atomic():
        contract.ends_on = ends_on

        if monthly_hours is not None:
            contract.monthly_hours = _quantize(monthly_hours)

        if contract.status == ServiceContract.Status.ENDED:
            contract.status = ServiceContract.Status.ACTIVE

        save_with_config_activity(contract, actor=actor)

    return contract


def renew_expiring_balance(contract, *, actor):
    """Extend nothing and write the leftover balance off. Section 8b.

    The hours and the moment are recorded, for history and for the next negotiation --
    section 8 requires it, and criterion 20 requires it of every path, not just the
    convenient ones. Uses ``EXPIRED_BY_CONTRACT_END`` rather than the validity type so a
    report can tell "we chose not to carry this" from "this aged out".
    """
    expired = []

    with transaction.atomic():
        for period in ServiceContractPeriod.objects.filter(
            contract_id=contract.pk, status=ServicePeriodStatus.OPEN
        ).order_by("competence_year", "competence_month"):
            locked = _lock_period(period)

            for origin, hours in remaining_parcels(locked):
                expired.append(
                    write_ledger_entry(
                        period=locked,
                        entry_type=ServiceLedgerEntryType.EXPIRED_BY_CONTRACT_END,
                        hours=-hours,
                        origin_period=origin,
                        actor=actor,
                        notes=(
                            f"{hours}h da competencia {origin.competence_label} expiradas na "
                            f"renovacao do contrato {contract.code}"
                        ),
                    )
                )

            locked.status = ServicePeriodStatus.CLOSED
            locked.closed_at = timezone.now()
            locked.closed_by = actor
            locked.save(update_fields=["status", "closed_at", "closed_by", "updated_at"])

    return expired


def end_and_create_successor(
    contract,
    *,
    code,
    name,
    monthly_hours,
    starts_on,
    ends_on,
    balance_destination,
    actor,
    target_issue=None,
    **contract_fields,
):
    """Close a contract and open a new one referencing it. Section 8c, criterion 17.

    The old contract becomes ``ENDED``, the new one records ``previous_contract`` so the
    chain stays navigable, and the remaining balance goes exactly one of three ways --
    transferred, expired, or converted into a work item allowance. Every one of them
    writes a ledger row naming who decided and when, which is criterion 20 applied to
    the path where hours are most likely to quietly vanish.

    ``issue_allowance`` credits the remaining balance into ``target_issue``'s allowance,
    **parcel by parcel**, each ``CREDIT`` carrying the competency it came from in
    ``origin_period``. That is what closes the third of criterion 17 that Phase 4 left
    open: the destination existed as an interface -- the entry type, the argument, and an
    explicit ``ISSUE_ALLOWANCE_NOT_AVAILABLE`` with HTTP 501 -- and Phase 5 supplied the
    entity it needed.

    Crediting per parcel rather than in one lump is deliberate: the hours arrive in the
    allowance labelled with where they came from, so a client asking "where did these 12h
    come from" is answered from the ledger instead of from a note.
    """
    from plane.utils.service_catalog import create_with_config_activity, save_with_config_activity

    if balance_destination == BalanceDestination.ISSUE_ALLOWANCE and target_issue is None:
        raise ServicePoolValidationError(ISSUE_ALLOWANCE_REQUIRES_TARGET_ISSUE, {})

    with transaction.atomic():
        successor = ServiceContract(
            workspace_id=contract.workspace_id,
            service_client_id=contract.service_client_id,
            code=code,
            name=name,
            monthly_hours=_quantize(monthly_hours),
            starts_on=starts_on,
            ends_on=ends_on,
            previous_contract=contract,
            carryover_months=contract_fields.pop("carryover_months", contract.carryover_months),
            accrual_cap_mode=contract_fields.pop("accrual_cap_mode", contract.accrual_cap_mode),
            accrual_cap_value=contract_fields.pop("accrual_cap_value", contract.accrual_cap_value),
            overage_policy=contract_fields.pop("overage_policy", contract.overage_policy),
            overage_hour_rate=contract_fields.pop("overage_hour_rate", contract.overage_hour_rate),
            **contract_fields,
        )
        create_with_config_activity(successor, actor=actor)

        moved = _drain_open_periods(
            contract,
            successor=successor,
            balance_destination=balance_destination,
            actor=actor,
            target_issue=target_issue,
        )

        contract.status = ServiceContract.Status.ENDED
        save_with_config_activity(contract, actor=actor)

    return successor, moved


def _drain_open_periods(contract, *, successor, balance_destination, actor, target_issue=None):
    """Empty every open period of a contract into its successor, or write it off.

    Three destinations, and each parcel leaves with a ledger row naming who decided and
    when -- criterion 20 applied to the path where hours are most likely to quietly
    vanish.
    """
    from plane.utils.service_allowance import credit_allowance

    moved = []
    target = resolve_period(successor, successor.starts_on, actor=actor) if successor else None

    for period in ServiceContractPeriod.objects.filter(
        contract_id=contract.pk, status=ServicePeriodStatus.OPEN
    ).order_by("competence_year", "competence_month"):
        locked = _lock_period(period)

        for origin, hours in remaining_parcels(locked):
            if balance_destination == BalanceDestination.TRANSFER:
                moved.append(
                    write_ledger_entry(
                        period=locked,
                        entry_type=ServiceLedgerEntryType.TRANSFERRED_TO_CONTRACT,
                        hours=-hours,
                        origin_period=origin,
                        actor=actor,
                        notes=(
                            f"{hours}h da competencia {origin.competence_label} transferidas para o "
                            f"contrato {successor.code}"
                        ),
                    )
                )
                write_ledger_entry(
                    period=target,
                    entry_type=ServiceLedgerEntryType.CARRY_IN,
                    hours=hours,
                    origin_period=origin,
                    actor=actor,
                    notes=(
                        f"{hours}h da competencia {origin.competence_label} recebidas do contrato "
                        f"{contract.code}"
                    ),
                )
                ServiceContractPeriod.objects.filter(pk=target.pk).update(
                    carried_hours=F("carried_hours") + _quantize(hours)
                )
            elif balance_destination == BalanceDestination.ISSUE_ALLOWANCE:
                # Criterion 17's third destination. The mirrored pair is the same shape as
                # CARRY_OUT / CARRY_IN: the balance leaves the period as
                # CONVERTED_TO_ISSUE_ALLOWANCE, and arrives in the allowance as a CREDIT
                # whose `origin_period` still names the competency it came from.
                moved.append(
                    write_ledger_entry(
                        period=locked,
                        entry_type=ServiceLedgerEntryType.CONVERTED_TO_ISSUE_ALLOWANCE,
                        hours=-hours,
                        origin_period=origin,
                        actor=actor,
                        notes=(
                            f"{hours}h da competencia {origin.competence_label} convertidas em "
                            f"bolsa do chamado {target_issue.pk}"
                        ),
                    )
                )
                credit_allowance(
                    target_issue,
                    hours,
                    actor=actor,
                    origin_period=origin,
                    credit_notes=(
                        f"{hours}h da competencia {origin.competence_label} recebidas do "
                        f"encerramento do contrato {contract.code}"
                    ),
                )
            else:
                moved.append(
                    write_ledger_entry(
                        period=locked,
                        entry_type=ServiceLedgerEntryType.EXPIRED_BY_CONTRACT_END,
                        hours=-hours,
                        origin_period=origin,
                        actor=actor,
                        notes=(
                            f"{hours}h da competencia {origin.competence_label} expiradas no "
                            f"encerramento do contrato {contract.code}"
                        ),
                    )
                )

        locked.status = ServicePeriodStatus.CLOSED
        locked.closed_at = timezone.now()
        locked.closed_by = actor
        locked.save(update_fields=["status", "closed_at", "closed_by", "updated_at"])

    return moved


# ---------------------------------------------------------------------------
# Reading a balance -- section 7
# ---------------------------------------------------------------------------


def contract_balance_statement(contract):
    """A contract's pool, month by month, with each carried parcel's origin. Section 7.

    Section 7 asks for the accumulated balance "com a competência de origem de cada
    parcela", which is the part a scalar total cannot answer and the reason the parcels
    are reconstructed here rather than summed.

    **Two queries regardless of how many periods the contract has.** This used to be one
    per period, which was invisible while the only caller was a single contract's detail
    page and became the spine of Phase 9's dashboard: a 36-month contract cost 37 round
    trips. ``period_parcels_bulk`` reads them all at once and the FIFO allocation is applied
    per period from memory.
    """
    periods = list(
        ServiceContractPeriod.objects.filter(contract_id=contract.pk).order_by(
            "competence_year", "competence_month"
        )
    )

    parcels_by_period = period_parcels_bulk(periods)

    return [
        {
            "period_id": str(period.pk),
            "competence": period.competence_label,
            "status": period.status,
            "contracted_hours": str(_quantize(period.contracted_hours)),
            "carried_hours": str(_quantize(period.carried_hours)),
            "consumed_hours": str(_quantize(period.consumed_hours)),
            "granted_hours": str(_quantize(period.granted_hours)),
            "balance_hours": str(_quantize(period.balance_hours)),
            "discarded_by_cap_hours": str(_quantize(period.discarded_by_cap_hours)),
            "overage_hours": str(_quantize(period.overage_hours)),
            "overage_settlement": period.overage_settlement,
            "parcels": [
                {"origin_competence": origin.competence_label, "hours": str(_quantize(hours))}
                for origin, hours in remaining_parcels(
                    period, parcels=parcels_by_period.get(period.pk, [])
                )
            ],
        }
        for period in periods
    ]


#: Which origin a work log on this work item would debit. Rule R6's hierarchy, named, so
#: that a panel never has to infer it from which key came back null -- "no period" and
#: "an allowance pays instead" are different facts and were previously the same payload.
ORIGIN_ISSUE_ALLOWANCE = "issue_allowance"
ORIGIN_CONTRACT_PERIOD = "contract_period"


def issue_pool_snapshot(issue, worked_on=None):
    """The pool position a work item's panel shows. Section 7.

    "Quanto do pool do mês foi consumido pelo cliente daquele chamado, incluindo o saldo
    acumulado disponível" -- plus the resolution failure, when there is one, because a
    panel that simply shows nothing is indistinguishable from a client with no
    consumption.

    **Asks rule R6 in R6's order.** When the work item, or an ancestor of it, has an
    allowance, that allowance is what pays and the contract is not consulted at all --
    which also means an allowance-funded work item in a project with no client resolves
    cleanly instead of reporting ``NO_SERVICE_CLIENT_FOR_PROJECT``.

    ``origin`` is always present and is the key a caller should branch on.
    """
    worked_on = worked_on or timezone.now().date()

    from plane.utils.service_allowance import issue_allowance_snapshot

    allowance = issue_allowance_snapshot(issue)

    if allowance is not None:
        return {
            "origin": ORIGIN_ISSUE_ALLOWANCE,
            "allowance": allowance,
            "contract_id": None,
            "contract_code": None,
            "warning": None,
            "period": None,
        }

    try:
        contract, warning = resolve_contract(issue, worked_on)
    except ServicePoolValidationError as error:
        return {"origin": None, "allowance": None, "error": error.code, "detail": error.detail}

    period = resolve_period(contract, worked_on, materialize=False)

    if period is None:
        return {
            "origin": ORIGIN_CONTRACT_PERIOD,
            "allowance": None,
            "contract_id": str(contract.pk),
            "contract_code": contract.code,
            "warning": warning,
            "period": None,
        }

    return {
        "origin": ORIGIN_CONTRACT_PERIOD,
        "allowance": None,
        "contract_id": str(contract.pk),
        "contract_code": contract.code,
        "warning": warning,
        "period": {
            "period_id": str(period.pk),
            "competence": period.competence_label,
            "status": period.status,
            "contracted_hours": str(_quantize(period.contracted_hours)),
            "carried_hours": str(_quantize(period.carried_hours)),
            "granted_hours": str(_quantize(period.granted_hours)),
            "consumed_hours": str(_quantize(period.consumed_hours)),
            "balance_hours": str(_quantize(period.balance_hours)),
        },
    }


def open_periods_for_workspace(workspace_id, *, contract_id=None):
    """Open periods of a workspace, for the alert panel and the reconciliation command."""
    queryset = ServiceContractPeriod.objects.filter(
        workspace_id=workspace_id, status=ServicePeriodStatus.OPEN
    ).select_related("contract", "contract__service_client")

    if contract_id:
        queryset = queryset.filter(contract_id=contract_id)

    return queryset.filter(~Q(contract__status=ServiceContract.Status.ENDED))
