# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""How a work log is settled: from the hour pool, in reais, or not at all.

This module owns **one decision**, made in one place: given a persisted work log, does it
debit a pool, get priced in reais, or neither. Rule R6's hierarchy and decision D33's
fallback both live here, and the two halves that carry them out live elsewhere --
``plane.utils.service_pool`` moves hours, ``plane.utils.service_pricing`` computes money.

**Why the decision moved out of ``apply_debit``.** Until Phase 6 the answer was binary and
local: a pool route debited, anything else did nothing. D33 changed that -- a work log that
*chose* the pool but whose client has no usable contract is now **billed as avulso** rather
than silently consuming nothing. Making that call requires the contract resolution
``apply_debit`` was doing privately, so either the resolution happens twice (two chances to
disagree about which pool pays) or it happens once, above both halves. It happens once,
here.

**What a settlement writes on the row.** Three columns come from the decision --
``settled_billing_route``, ``route_deviation_reason`` and the debit origin -- and three
from the pricing -- ``applied_hour_rate``, ``applied_rate_basis``, ``amount`` -- plus
``pricing_failure_reason``. The check constraints on ``ServiceLog`` make every incoherent
combination unrepresentable, so a settlement that got half-written cannot be stored.
"""

# Django imports
from django.db import transaction
from django.db.models import Case, CharField, Q, Value, When

# Module imports
from plane.db.models import (
    INTERNAL_WORK_FAILURES,
    PRICING_PENDENCY_FAILURES,
    ServiceBillingType,
    ServiceContract,
    ServiceHourLedgerEntry,
    ServiceLedgerEntryType,
    ServiceLog,
    ServiceRouteDeviation,
)
from plane.utils.service_log_time import ZERO_HOURS, format_hours_human
from plane.utils.service_money import ZERO_MONEY, format_money
from plane.utils.service_pool import (
    ServicePoolValidationError,
    debit_resolved_pool,
    month_bounds,
    resolve_contract,
)
from plane.utils.service_pricing import resolve_log_price

# ---------------------------------------------------------------------------
# The decision
# ---------------------------------------------------------------------------

#: The three ways a work log can be settled. Deliberately the billing routes themselves
#: rather than a parallel vocabulary: the settled route IS one of these, it is stored in
#: ``ServiceLog.settled_billing_route``, and inventing a fourth word for the same fact
#: would be a second truth to keep in sync.
_POOL = ServiceBillingType.BillingRoute.DEBIT_POOL
_BILL = ServiceBillingType.BillingRoute.BILL_AMOUNT
_FREE = ServiceBillingType.BillingRoute.NON_BILLABLE

#: Which contract states D33 turns into a monetary charge. Mapped rather than branched so
#: that the set of "commercial pendencies that still get billed" is readable in one place
#: and so the consolidation and this module cannot disagree about it.
_DEVIATION_FOR_STATUS = {
    ServiceContract.Status.SUSPENDED: ServiceRouteDeviation.CONTRACT_SUSPENDED,
    ServiceContract.Status.ENDED: ServiceRouteDeviation.CONTRACT_ENDED,
}


def resolve_settlement(service_log):
    """Decide how one work log is settled. No writes, no exceptions for absent config.

    Returns a dict with ``route``, ``deviation``, ``allowance`` and ``contract``:

    * ``route`` is the ``BillingRoute`` that will be **applied**, which is what lands in
      ``ServiceLog.settled_billing_route``;
    * ``deviation`` is a ``ServiceRouteDeviation`` when the chosen route was the pool and
      the applied route is a charge, else ``None``;
    * ``allowance`` / ``contract`` are whichever target was resolved, so the caller does
      not resolve again.

    The order is R6's hierarchy, and the order is the part that is easy to break:

    1. **Not billable at all.** ``NON_BILLABLE`` settles as itself and stops. Garantia and
       Cortesia never become billable, no matter what the contract is doing -- a warranty
       repair during a suspended contract is still a warranty repair.
    2. **Already a monetary route.** ``BILL_AMOUNT`` settles as itself, with no deviation:
       the client is avulso by design, which is not a pendency.
    3. **Zero debited hours.** Settles as the pool with nothing to move. Reachable only
       through a route that debits, since R5 zeroes only non-billable rows.
    4. **A work item allowance exists.** It pays, and the contract is *not consulted* --
       section 3 of the Phase 5 brief forbids allowance overflow reaching the support pool,
       and consulting the contract here is how that would start happening by accident.
       Note this comes **before** the D33 check, so a client whose contract expired still
       debits their project allowance rather than being billed twice for the same sale.
    5. **A usable contract exists.** It debits.
    6. **No usable contract.** D33: bill it, and record which of the four reasons applied.

    ``AMBIGUOUS_CONTRACT_RESOLUTION`` and a contract pinned from another client still
    **raise**, untouched by this phase. D27's line holds: ambiguity means a *wrong pool
    could be chosen*, and billing the client in reais to avoid choosing is not a repair,
    it is a different wrong answer.
    """
    chosen = service_log.applied_billing_route

    if chosen == _FREE:
        return {"route": _FREE, "deviation": None, "allowance": None, "contract": None}

    if chosen == _BILL:
        return {"route": _BILL, "deviation": None, "allowance": None, "contract": None}

    if service_log.debited_hours == ZERO_HOURS:
        return {"route": _POOL, "deviation": None, "allowance": None, "contract": None}

    # Function level, like every cross-import between the service domain modules, because
    # `service_allowance` imports `service_pool` and a module-scope import here would close
    # the loop at load time. Uses the public resolver rather than `service_pool`'s private
    # wrapper; both return an allowance **regardless of status**, which is deliberate --
    # filtering to open ones would let a closed allowance fall through to the contract pool,
    # and that fallback is what section 3 of the Phase 5 brief forbids outright.
    from plane.utils.service_allowance import resolve_work_item_allowance

    allowance = resolve_work_item_allowance(service_log.issue)

    if allowance is not None:
        return {"route": _POOL, "deviation": None, "allowance": allowance, "contract": None}

    try:
        contract, _warning = resolve_contract(service_log.issue, service_log.worked_on)
    except ServicePoolValidationError as error:
        if error.blocks_the_work_log:
            # Ambiguity, or a pin to another client's contract. Still fatal -- see the
            # docstring.
            raise

        # D33's first case. The client has no contract, or the project has no client at
        # all; either way there is no pool, so the work is billed rather than absorbed.
        # `NO_SERVICE_CLIENT_FOR_PROJECT` lands here too and is priced as internal work by
        # `resolve_log_price`, which is where that distinction is made.
        return {
            "route": _BILL,
            "deviation": ServiceRouteDeviation.NO_CONTRACT_FOR_CLIENT,
            "allowance": None,
            "contract": None,
        }

    deviation = _deviation_for(contract, service_log.worked_on)

    if deviation is not None:
        return {"route": _BILL, "deviation": deviation, "allowance": None, "contract": contract}

    return {"route": _POOL, "deviation": None, "allowance": None, "contract": contract}


def _deviation_for(contract, worked_on):
    """Why this contract cannot absorb work on this date, or ``None`` if it can. D33.

    Three of D33's four cases; the fourth (no contract at all) is decided by the caller,
    which is the only one that can see a resolution failure.

    **A vigency that has not started yet is not a deviation.** D31 sends that work to the
    contract's first period, because a future pool exists to borrow from. Only a vigency
    that has *ended* leaves nothing to debit -- and that asymmetry is the whole reason
    ``contract_warnings`` no longer treats both directions as one warning.
    """
    mapped = _DEVIATION_FOR_STATUS.get(contract.status)

    if mapped is not None:
        return mapped

    if worked_on > contract.ends_on:
        return ServiceRouteDeviation.CONTRACT_EXPIRED

    return None


# ---------------------------------------------------------------------------
# Carrying it out
# ---------------------------------------------------------------------------


def settle_service_log(service_log, actor=None):
    """Settle one persisted work log: move hours, or price it, or neither.

    Returns a dict with ``route``, ``deviation``, ``amount`` and ``ledger_entry``.

    Idempotent in the way that matters: the hour half is guarded by the ledger's partial
    unique index, and the money half is a write of derived columns onto the row itself, so
    settling twice recomputes the same value from the same inputs. The value cannot drift
    between the two calls because ``resolve_log_price`` reads the price sheet by
    ``worked_on``, which does not change.
    """
    settlement = resolve_settlement(service_log)

    with transaction.atomic():
        if settlement["route"] == _POOL:
            entry = _settle_from_pool(service_log, settlement, actor=actor)

            return {
                "route": _POOL,
                "deviation": None,
                "amount": ZERO_MONEY,
                "ledger_entry": entry,
            }

        if settlement["route"] == _BILL:
            fields = _settle_as_charge(service_log, settlement)

            return {
                "route": _BILL,
                "deviation": settlement["deviation"],
                "amount": fields["amount"],
                "ledger_entry": None,
            }

        # NON_BILLABLE. R5, and acceptance criterion 4: the zero comes out of the route.
        # Nothing is written -- the row's defaults already say zero, and the check
        # constraint `service_log_non_billable_carries_no_amount` guarantees no code path
        # could have put money on it.
        return {"route": _FREE, "deviation": None, "amount": ZERO_MONEY, "ledger_entry": None}


def _settle_from_pool(service_log, settlement, actor=None):
    """Debit the resolved allowance or contract period. No money is written."""
    if service_log.debited_hours == ZERO_HOURS:
        return None

    if settlement["allowance"] is not None:
        from plane.utils.service_allowance import debit_allowance

        return debit_allowance(service_log, settlement["allowance"], actor=actor)

    if settlement["contract"] is None:
        return None

    return debit_resolved_pool(service_log, contract=settlement["contract"], actor=actor)


def settlement_fields(service_log, settlement=None):
    """The six columns a settlement would write, computed without writing anything.

    Exists so the **preview and the save cannot disagree**. Section 4 of the phase brief
    wants the work log form to show the value before it is saved, and the only honest way
    to do that is to run the real calculation -- a preview computed by different code than
    the save is a preview that can lie, which is the same argument ``build_batch_rows``
    makes for existing separately from ``create_service_log_batch``.

    Safe on an **unsaved** row: everything it reads is either already on the instance or
    reachable from ``issue``, and nothing here writes. That is what lets the preview
    endpoint call it on rows that have no primary key yet.

    ``service_client_id`` is read through ``project``, never stored on the log -- D19.
    """
    settlement = settlement or resolve_settlement(service_log)

    if settlement["route"] != _BILL:
        # A pool debit and a non-billable row both carry no money, and the check
        # constraints require all three of rate, basis and amount to agree on that.
        return {
            "settled_billing_route": settlement["route"],
            "route_deviation_reason": settlement["deviation"],
            "applied_hour_rate": None,
            "applied_rate_basis": None,
            "amount": ZERO_MONEY,
            "pricing_failure_reason": None,
        }

    price = resolve_log_price(service_log, service_client_id=service_log.project.service_client_id)

    return {
        "settled_billing_route": settlement["route"],
        "route_deviation_reason": settlement["deviation"],
        "applied_hour_rate": price["applied_hour_rate"],
        "applied_rate_basis": price["applied_rate_basis"],
        "amount": price["amount"],
        "pricing_failure_reason": price["pricing_failure_reason"],
    }


def _settle_as_charge(service_log, settlement):
    """Price the work log and persist the six settlement columns.

    Written with ``update()`` on ``all_objects`` for the reason ``apply_debit`` gives for
    the same choice: saving the model would re-run ``ServiceLog.save``'s side effects from
    inside a settlement. The in-memory instance is updated to match so the caller's
    response serialises the value that was actually stored.
    """
    fields = settlement_fields(service_log, settlement)

    ServiceLog.all_objects.filter(pk=service_log.pk).update(**fields)

    for name, value in fields.items():
        setattr(service_log, name, value)

    return fields


def settle_service_log_batch(rows, actor=None):
    """Settle every segment of a batch, all of it or none of it.

    Replaces ``apply_batch_debit`` as the API's entry point. Same atomicity argument: a
    batch can straddle two competencies -- 31/01 23:00 to 01/02 01:00 -- and each segment
    settles on its own ``worked_on`` (R7), so a batch where one segment failed must not be
    left half settled. Half a batch billed is worse than a batch rejected.

    Returns the list of ledger entries actually written, matching
    ``apply_batch_debit``'s return shape so the views' response building is unchanged.
    """
    with transaction.atomic():
        entries = []

        for row in rows:
            result = settle_service_log(row, actor=actor)

            if result["ledger_entry"] is not None:
                entries.append(result["ledger_entry"])

        return entries



# ---------------------------------------------------------------------------
# The monthly consolidation -- section 5, and decision D36
# ---------------------------------------------------------------------------


class RevenueOrigin:
    """The four things a client can be invoiced for. Decision D36.

    Plain constants rather than ``TextChoices``, because nothing stores them: the origin of
    a row is **derived** at report time from columns that are already persisted. Storing it
    would be a fifth truth that could disagree with the four it was derived from, which is
    the same objection ``ServiceLog`` records against having an ``applied_contract`` column.

    * ``STANDALONE_LOG`` -- an avulso work log. Covers **two commercially different
      things**, told apart by ``route_deviation_reason``: a null reason is a client whose
      model is avulso, and a non-null one is D33's commercial pendency, with a deadline. The
      consolidation breaks the origin down by reason for exactly that reason; section 5
      requires the difference to be visible, because one is a business model and the other
      is somebody's overdue renewal.
    * ``OUT_OF_SCOPE_LOG`` -- a work log billed on a client who **does** hold a contract
      covering the competency. Contracted support plus something sold outside its scope.
    * ``CONTRACT_OVERAGE`` -- a competency period's deficit, billed at close.
    * ``ALLOWANCE_OVERAGE`` -- a work item allowance's deficit, billed at close.

    **Internal work is not here, and that is the point.** A project with no client is
    internal work (section 1 of Phase 1: "não apontável para faturamento"), so it is not a
    fifth origin worth zero -- it is **excluded from revenue entirely** and reported
    separately. Listing it as a zeroed pendency would fill a financial panel with entries
    nobody can act on, and an operator who learns to ignore the list is an operator who
    misses the one entry that mattered.
    """

    STANDALONE_LOG = "standalone_log"
    OUT_OF_SCOPE_LOG = "out_of_scope_log"
    CONTRACT_OVERAGE = "contract_overage"
    ALLOWANCE_OVERAGE = "allowance_overage"


def revenue_origin_of(service_log, *, client_has_contract):
    """Which origin one billed work log belongs to. **The only place the rule lives.**

    A function rather than four report queries each with their own ``CASE``, for the reason
    ``compute_hour_quantities`` is one function: four reports that each classify revenue
    slightly differently produce four different invoices from one month of work.

    Returns ``None`` for a row that is not revenue -- a pool debit, a non-billable row, or
    internal work. The caller decides what to do with those; the consolidation reports the
    last two separately and ignores the first.

    ``client_has_contract`` is passed in rather than looked up, because the caller resolves
    it once per client for the whole competency instead of once per work log.
    """
    if service_log.settled_billing_route != _BILL:
        return None

    if service_log.pricing_failure_reason in INTERNAL_WORK_FAILURES:
        return None

    return _origin_from(
        route_deviation_reason=service_log.route_deviation_reason,
        client_has_contract=client_has_contract,
    )


def _origin_from(*, route_deviation_reason, client_has_contract):
    """Standalone or out of scope. **The one statement of that half of the rule.**

    Shared by ``revenue_origin_of`` (which answers "which origin" for a row that is already
    known to be revenue) and ``report_bucket_from`` (which answers the prior question of
    whether it is revenue at all). Two callers, one rule -- before this existed the D33
    precedence was written twice, which is precisely the duplication the module docstring
    warns turns one month of work into two different invoices.

    D33: a deviation means the client may well hold a contract that could not absorb this
    work. That is avulso-with-a-reason, never "out of scope" -- out of scope means a service
    type deliberately sold outside the contract, which is a decision rather than a lapse.
    """
    if route_deviation_reason is not None:
        return RevenueOrigin.STANDALONE_LOG

    return RevenueOrigin.OUT_OF_SCOPE_LOG if client_has_contract else RevenueOrigin.STANDALONE_LOG


class ReportBucket:
    """Where one work log lands in a financial report. The four origins plus the two
    populations that are **not** revenue and must still be accounted for.

    ``revenue_origin_of`` answers "which origin"; this answers the prior question "is this
    revenue at all, and if not, which kind of not". Phase 6 made that dispatch inline in
    ``consolidated_billing``; Phase 9 needs the same dispatch in SQL for the time series, so
    it is named here and both consumers go through it.
    """

    #: A project with no client, or a log whose pricing failed *because* of that. Section 1
    #: of Phase 1: not billable. Excluded from revenue entirely, counted separately, never a
    #: fifth origin worth zero.
    INTERNAL_WORK = "internal_work"

    #: Billed work that **cannot be invoiced yet** because no price sheet applies. Not
    #: revenue, and its absence from any total is the entire reason it is listed.
    REGISTRATION_PENDENCY = "registration_pendency"

    #: A pool debit or a non-billable row. Real work, no money, nothing for a financial
    #: report to add up. ``None`` rather than a string so that a caller filtering on the
    #: annotation gets SQL ``NULL`` and can use ``isnull``.
    NOT_REVENUE = None


def report_bucket_from(
    *,
    settled_billing_route,
    route_deviation_reason,
    pricing_failure_reason,
    client_has_contract,
    has_client,
):
    """Which report bucket these classification inputs describe. **The rule itself.**

    Takes scalars rather than a work log, because Phase 9's series aggregates in SQL and
    then classifies the **grouped rows** -- the group key carries exactly these five facts
    and there is no ``ServiceLog`` instance to hand over. A version that needed a model
    instance would have forced either a second copy of the rule or a fake object, and both
    are worse than a signature with five keywords.

    Order matters and is not arbitrary:

    1. **Route first.** A pool debit or a non-billable row is not revenue whatever else is
       true of it, and asking about its client would be asking about the wrong thing.
    2. **Internal work before pendency.** A client-less project has no price sheet *by
       definition*, so a row that is both would otherwise be filed as a pendency somebody
       is expected to fix -- and nobody can register a price sheet for a client that does
       not exist.
    3. **Pendency before origin.** A row with no resolvable price has no amount, so filing
       it under an origin would add a zero to a revenue column and make a missing price
       sheet look like completed work worth nothing.
    4. **A deviation beats the contract.** D33: the client may hold a contract that could
       not absorb this work, and that is ad-hoc-with-a-reason, never out of scope.
    """
    if settled_billing_route != _BILL:
        return ReportBucket.NOT_REVENUE

    if not has_client or pricing_failure_reason in INTERNAL_WORK_FAILURES:
        return ReportBucket.INTERNAL_WORK

    if pricing_failure_reason in PRICING_PENDENCY_FAILURES:
        return ReportBucket.REGISTRATION_PENDENCY

    return _origin_from(
        route_deviation_reason=route_deviation_reason, client_has_contract=client_has_contract
    )


def report_bucket_of(service_log, *, client_has_contract, has_client):
    """``report_bucket_from`` for a persisted work log. The row-shaped front door.

    Extracted from ``consolidated_billing``, which now calls it, so that the dispatch has
    one implementation rather than one per report.
    """
    return report_bucket_from(
        settled_billing_route=service_log.settled_billing_route,
        route_deviation_reason=service_log.route_deviation_reason,
        pricing_failure_reason=service_log.pricing_failure_reason,
        client_has_contract=client_has_contract,
        has_client=has_client,
    )


def report_bucket_expression(contracted_client_ids):
    """``report_bucket_of`` as a ``Case/When``, for aggregating in the database.

    **A second implementation of one rule, which is normally indefensible.** It is
    defensible here for a reason that has to be checked rather than assumed: the input
    space is *finite and small*. Three settled routes, five deviation values, four pricing
    failure values, and two client facts -- and the check constraints on ``ServiceLog`` cut
    that down to **66 representable combinations**.

    So ``test_service_billing_origins.py`` enumerates **every one of them**, builds the row,
    and asserts the two implementations agree. That is a proof over the whole input space,
    not a sample, and it is what makes the duplication safe: adding a value to
    ``ServiceRouteDeviation`` changes the enumerated count and turns the test red by
    arithmetic rather than by luck.

    ``contracted_client_ids`` is the set resolved once per competency by
    ``_clients_with_a_contract_covering``, passed in for the same reason
    ``client_has_contract`` is passed to the Python version: one query per report instead of
    one per row.
    """
    is_internal = Q(project__service_client_id__isnull=True) | Q(
        pricing_failure_reason__in=INTERNAL_WORK_FAILURES
    )

    return Case(
        # 1. Not revenue at all.
        When(~Q(settled_billing_route=_BILL), then=Value(None, output_field=CharField())),
        # 2. Internal work, before pendency -- a client-less project cannot have a sheet.
        When(is_internal, then=Value(ReportBucket.INTERNAL_WORK)),
        # 3. No resolvable price: not revenue, and not a zero in a revenue column either.
        When(
            Q(pricing_failure_reason__in=PRICING_PENDENCY_FAILURES),
            then=Value(ReportBucket.REGISTRATION_PENDENCY),
        ),
        # 4. D33: a deviation makes it ad-hoc-with-a-reason, never out of scope, whether or
        # not the client holds a contract.
        When(
            Q(route_deviation_reason__isnull=False),
            then=Value(RevenueOrigin.STANDALONE_LOG),
        ),
        # 5. The client holds a contract covering the competency, so this is something sold
        # outside its scope.
        When(
            Q(project__service_client_id__in=list(contracted_client_ids)),
            then=Value(RevenueOrigin.OUT_OF_SCOPE_LOG),
        ),
        default=Value(RevenueOrigin.STANDALONE_LOG),
        output_field=CharField(),
    )


def _clients_with_a_contract_covering(workspace_id, first_day, last_day):
    """Ids of clients holding a contract whose vigency overlaps the competency.

    **Derived at report time and deliberately not snapshotted.** R4 requires a snapshot of
    everything that decides *money*, and this does not: the value is already persisted on
    the row. This only decides which column of a report the value appears in, and contracts
    are soft deleted rather than removed, so the fact stays recoverable.

    Overlap, not containment: a contract that ended mid-month still covered part of the
    competency, and a work log from before it ended is contracted support.
    """
    return set(
        ServiceContract.objects.filter(
            workspace_id=workspace_id,
            starts_on__lte=last_day,
            ends_on__gte=first_day,
        ).values_list("service_client_id", flat=True)
    )


def _empty_bucket():
    return {"hours": ZERO_HOURS, "amount": ZERO_MONEY, "entries": 0}


def _add(bucket, *, hours, amount):
    bucket["hours"] += hours
    bucket["amount"] += amount
    bucket["entries"] += 1


def consolidated_billing(workspace_id, year, month, *, service_client_id=None):
    """Everything billable in one competency, by client and by origin. Section 5.

    Returns a dict with ``competence``, ``clients`` (one entry per client with something to
    report), ``total_amount`` and ``internal_work``.

    **Every total is a sum of persisted ``amount`` values.** Acceptance criterion 13 --
    "nenhum centavo perdido ou criado" -- is why nothing here multiplies hours by a rate:
    the rounding happened once, per work log, when the value was derived (section 4b), and
    summing rounded products is not the same number as rounding a sum of products. Only the
    first matches the invoice lines a client will check.

    **The competency of each origin is its own** (R7), and they genuinely differ:

    * a work log belongs to the month of ``worked_on`` -- the service date, never the date
      it was typed, which is criterion 12;
    * a contract overage belongs to the **period's** competency, which is the month it
      settles, not the month it was closed in;
    * an allowance overage belongs to the month it was **closed**, because an allowance has
      no competency of its own -- it is a sale that ends when someone ends it.

    Pendencies are reported in **two separate lists that a financial panel must not mix**:
    ``commercial_pendencies`` are billed work whose contract could not absorb it (D33, with
    a deadline), and ``registration_pendencies`` are work that **cannot be invoiced at all
    yet** because no price sheet applies. The first is revenue; the second is not, and its
    absence from ``total_amount`` is the whole reason it is listed.
    """
    first_day, last_day = month_bounds(year, month)
    contracted_clients = _clients_with_a_contract_covering(workspace_id, first_day, last_day)

    clients = {}
    internal = {"hours": ZERO_HOURS, "entries": 0}

    def bucket_for(client_id, client_name):
        if client_id not in clients:
            clients[client_id] = {
                "service_client_id": str(client_id),
                "service_client_name": client_name,
                "origins": {
                    RevenueOrigin.STANDALONE_LOG: _empty_bucket(),
                    RevenueOrigin.OUT_OF_SCOPE_LOG: _empty_bucket(),
                    RevenueOrigin.CONTRACT_OVERAGE: _empty_bucket(),
                    RevenueOrigin.ALLOWANCE_OVERAGE: _empty_bucket(),
                },
                "commercial_pendencies": {},
                "registration_pendencies": {},
            }

        return clients[client_id]

    # ---- work logs, by service date -------------------------------------------------
    logs = (
        ServiceLog.objects.filter(
            workspace_id=workspace_id,
            settled_billing_route=_BILL,
            worked_on__gte=first_day,
            worked_on__lte=last_day,
        )
        .select_related("project", "project__service_client")
        .only(
            "amount",
            "equivalent_hours",
            "settled_billing_route",
            "route_deviation_reason",
            "pricing_failure_reason",
            "project__service_client__name",
        )
    )

    if service_client_id is not None:
        logs = logs.filter(project__service_client_id=service_client_id)

    for log in logs.iterator():
        client = log.project.service_client

        # The dispatch is `report_bucket_of` rather than inline `if`s, because Phase 9 needs
        # the identical dispatch in SQL for its time series and two hand-written copies of a
        # revenue rule is how one month of work becomes two different invoices. This is the
        # oracle the SQL expression is differentially tested against.
        bucket = report_bucket_of(
            log,
            client_has_contract=client is not None and client.pk in contracted_clients,
            has_client=client is not None,
        )

        if bucket == ReportBucket.INTERNAL_WORK:
            # Excluded from revenue, counted so the hours are not simply unaccounted for --
            # "not billable" and "lost" are different facts.
            internal["hours"] += log.equivalent_hours
            internal["entries"] += 1
            continue

        if bucket == ReportBucket.NOT_REVENUE:
            continue

        entry = bucket_for(client.pk, client.name)

        if bucket == ReportBucket.REGISTRATION_PENDENCY:
            # Real money that cannot be invoiced yet. Deliberately NOT added to any origin
            # bucket, so it cannot inflate `total_amount` with a zero it does not have.
            pendency = entry["registration_pendencies"].setdefault(
                log.pricing_failure_reason, {"hours": ZERO_HOURS, "entries": 0}
            )
            pendency["hours"] += log.equivalent_hours
            pendency["entries"] += 1
            continue

        _add(entry["origins"][bucket], hours=log.equivalent_hours, amount=log.amount)

        if log.route_deviation_reason is not None:
            # D33's reason, surfaced beside the revenue rather than instead of it: the money
            # is real and billable, and the pendency is a separate fact about why.
            deviation = entry["commercial_pendencies"].setdefault(
                log.route_deviation_reason, {"hours": ZERO_HOURS, "amount": ZERO_MONEY, "entries": 0}
            )
            deviation["hours"] += log.equivalent_hours
            deviation["amount"] += log.amount
            deviation["entries"] += 1

    # ---- contract overage, by the PERIOD's competency ------------------------------
    contract_overages = ServiceHourLedgerEntry.objects.filter(
        workspace_id=workspace_id,
        entry_type=ServiceLedgerEntryType.OVERAGE_BILLED,
        period__isnull=False,
        period__competence_year=year,
        period__competence_month=month,
        amount__isnull=False,
    ).select_related("contract", "contract__service_client")

    if service_client_id is not None:
        contract_overages = contract_overages.filter(contract__service_client_id=service_client_id)

    for overage in contract_overages.iterator():
        client = overage.contract.service_client
        entry = bucket_for(client.pk, client.name)
        _add(
            entry["origins"][RevenueOrigin.CONTRACT_OVERAGE],
            hours=overage.hours,
            amount=overage.amount,
        )

    # ---- allowance overage, by the CLOSING month -----------------------------------
    allowance_overages = ServiceHourLedgerEntry.objects.filter(
        workspace_id=workspace_id,
        entry_type=ServiceLedgerEntryType.OVERAGE_BILLED,
        allowance__isnull=False,
        allowance__closed_at__date__gte=first_day,
        allowance__closed_at__date__lte=last_day,
        amount__isnull=False,
    ).select_related("allowance", "allowance__project", "allowance__project__service_client")

    if service_client_id is not None:
        allowance_overages = allowance_overages.filter(
            allowance__project__service_client_id=service_client_id
        )

    for overage in allowance_overages.iterator():
        client = overage.allowance.project.service_client

        if client is None:
            # An allowance on an internal project. Nobody to invoice; counted with the rest
            # of the internal work rather than dropped.
            internal["hours"] += overage.hours
            internal["entries"] += 1
            continue

        entry = bucket_for(client.pk, client.name)
        _add(
            entry["origins"][RevenueOrigin.ALLOWANCE_OVERAGE],
            hours=overage.hours,
            amount=overage.amount,
        )

    return _render_consolidation(clients, internal, year, month)


def _render_consolidation(clients, internal, year, month):
    """Stringify the accumulated buckets. Decimals as strings, money also formatted.

    Strings for every number, following ``contract_balance_statement``: a ``Decimal`` that
    passes through JSON as a float has already lost the guarantee this whole phase is built
    on. The pt-BR rendering travels beside it so the screen, the CSV and the API cannot
    disagree about a separator.

    Hours ship rendered too, as a clock duration (D68). They used to travel only as
    ``"1.2500"`` and the billing screen appended a literal "h" to it, which is how a report
    ended up printing "1.2500h" at a client.
    """
    rendered = []
    grand_total = ZERO_MONEY

    for entry in sorted(clients.values(), key=lambda item: item["service_client_name"]):
        client_total = sum((bucket["amount"] for bucket in entry["origins"].values()), ZERO_MONEY)
        grand_total += client_total

        rendered.append(
            {
                "service_client_id": entry["service_client_id"],
                "service_client_name": entry["service_client_name"],
                "origins": {
                    origin: {
                        "hours": str(bucket["hours"]),
                        "hours_display": format_hours_human(bucket["hours"]),
                        "amount": str(bucket["amount"]),
                        "amount_display": format_money(bucket["amount"]),
                        "entries": bucket["entries"],
                    }
                    for origin, bucket in entry["origins"].items()
                },
                "total_amount": str(client_total),
                "total_amount_display": format_money(client_total),
                # D33: billed, and someone has a deadline.
                "commercial_pendencies": [
                    {
                        "reason": reason,
                        "hours": str(detail["hours"]),
                        "hours_display": format_hours_human(detail["hours"]),
                        "amount": str(detail["amount"]),
                        "amount_display": format_money(detail["amount"]),
                        "entries": detail["entries"],
                    }
                    for reason, detail in sorted(entry["commercial_pendencies"].items())
                ],
                # NOT billed, and cannot be until somebody registers a price. Carries no
                # amount on purpose -- there is no amount, and printing R$ 0,00 would make a
                # missing price sheet look like a finished calculation.
                "registration_pendencies": [
                    {
                        "reason": reason,
                        "hours": str(detail["hours"]),
                        "hours_display": format_hours_human(detail["hours"]),
                        "entries": detail["entries"],
                    }
                    for reason, detail in sorted(entry["registration_pendencies"].items())
                ],
            }
        )

    return {
        "competence": f"{year:04d}-{month:02d}",
        "clients": rendered,
        "total_amount": str(grand_total),
        "total_amount_display": format_money(grand_total),
        # Reported, never invoiced. See `RevenueOrigin`.
        "internal_work": {
            "hours": str(internal["hours"]),
            "hours_display": format_hours_human(internal["hours"]),
            "entries": internal["entries"],
        },
    }
