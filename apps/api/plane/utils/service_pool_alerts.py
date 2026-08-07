# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""The clients-needing-attention panel. Section 9 of the contract phase brief.

Split from ``plane.utils.service_pool`` on purpose: that module *moves* balances and
every function in it is inside a transaction and a row lock. Nothing here writes
anything except a dismissal. Keeping the read-only side separate is what lets the panel
be scanned across a whole workspace without touching the locks that debits depend on.

**Both extremes are alerts, and the low end is not the lesser one.** High consumption
risks work that never gets paid for. Low consumption is a churn signal: a client paying
for a pool they do not use will question the renewal, and section 9 is explicit that
this is "tão acionável quanto o alerta de estouro". They are returned in one list with a
``severity`` so a caller cannot accidentally render only half of them.
"""

# Python imports
from datetime import datetime, time, timedelta
from decimal import Decimal

# Django imports
from django.db.models import Count, Q
from django.utils import timezone

# Module imports
from plane.db.models import (
    ServiceAlertDismissal,
    ServiceContract,
    ServiceContractPeriod,
    ServiceIssueAllowance,
    ServiceLog,
)
from plane.utils.service_allowance import (
    open_allowances_for_workspace,
    workspace_allowance_hours_by_period,
)
from plane.utils.service_pool import (
    CONTRACT_OUT_OF_VIGENCY,
    CONTRACT_SUSPENDED,
    contract_warnings,
    open_periods_for_workspace,
)

# ---------------------------------------------------------------------------
# Alert codes
# ---------------------------------------------------------------------------

#: Consumption is already past the contract's high-consumption threshold and the month
#: is not half over. Acceptance criterion 18: 90% of the pool on the 10th.
HIGH_CONSUMPTION_BEFORE_MIDMONTH = "HIGH_CONSUMPTION_BEFORE_MIDMONTH"

#: The month's balance has gone negative. Acceptance criterion 7. Never a block -- the
#: work log is saved and the balance goes below zero, per D4 and section 4.
NEGATIVE_BALANCE = "NEGATIVE_BALANCE"

#: Consumption so far, extended over the rest of the month at the same daily rate, would
#: exhaust the pool before the month ends.
PROJECTED_OVERRUN = "PROJECTED_OVERRUN"

#: The month is nearly over and consumption never reached the low-consumption
#: threshold. The churn signal section 9 asks to treat as an opportunity.
LOW_CONSUMPTION_AT_MONTH_END = "LOW_CONSUMPTION_AT_MONTH_END"

#: Carried balance has grown past several months of contracted hours -- the client is
#: paying for a pool far larger than they consume.
ACCRUED_BALANCE_ABOVE_THRESHOLD = "ACCRUED_BALANCE_ABOVE_THRESHOLD"

#: Hours were thrown away by the accrual ceiling. The sharpest form of the same signal:
#: the client paid for hours the contract would not even let them keep.
HOURS_DISCARDED_BY_CAP = "HOURS_DISCARDED_BY_CAP"

#: Not one work log all month. Acceptance criterion 19.
#:
#: **This alert is legitimately triggered by a client who is being actively served**, and
#: the detail is what stops it lying. Work paid by a work item allowance has
#: ``debited_period`` null, so it does not count here -- which is correct, because this
#: alert is about consumption *of the contract*, and a client whose work all goes to
#: project allowances genuinely is not consuming the support they pay for. That is a real
#: renewal signal.
#:
#: What would be wrong is the *wording* a naive reader takes from it. "Cliente sem
#: atendimento" about a client with 40h of project work that month gets somebody to make
#: an embarrassing phone call. So the alert carries ``allowance_hours_in_period``, and the
#: frontend renders "sem apontamentos no contrato (Nh em bolsas de projeto)". The logic is
#: unchanged; only the context is added. Fixed by a characterisation test.
NO_SERVICE_LOGS_IN_MONTH = "NO_SERVICE_LOGS_IN_MONTH"

#: A work item allowance has gone negative. Section 3 of the Phase 5 brief: overflowing an
#: allowance never blocks the work log, "a bolsa fica com saldo negativo e o alerta
#: aparece". Never a block, for the same reason ``NEGATIVE_BALANCE`` is not.
ALLOWANCE_NEGATIVE_BALANCE = "ALLOWANCE_NEGATIVE_BALANCE"

#: A work item allowance is past its consumption threshold but still positive -- the
#: warning that arrives while there is still time to negotiate an aditivo de escopo
#: rather than after the hours are gone.
ALLOWANCE_HIGH_CONSUMPTION = "ALLOWANCE_HIGH_CONSUMPTION"

#: **The read-only form of D35.** The work item closed more than ``ALLOWANCE_GRACE_DAYS``
#: ago and its allowance is still open, so the grace period has run out and somebody has
#: to decide what happens to the remaining balance.
#:
#: D35 was originally going to be a periodic task that closed these automatically. That
#: was **refused**, and the reason is worth keeping next to the code that replaced it:
#: closing an allowance with a **positive** balance writes off hours the client *paid
#: for*. A client who bought 40h and used 30h would have 10h of credit deleted by a
#: timer, and the answer to "I still have 10 hours there" would be that the system
#: removed them unattended. The grace period exists precisely **to allow a negotiation**;
#: executing it automatically removes the decision it was created to make possible.
#:
#: So the clause still holds -- the balance *is* forfeit after 30 days -- but the system
#: **informs and a human confirms**, through the close endpoint Phase 5 already shipped.
#: That keeps this whole phase read-only and keeps the write-off a deliberate act.
#:
#: This alert is what stops the panel rotting. Without it no allowance ever leaves
#: ``OPEN``, so section 1's "bolsas ativas" and section 3b's attention list would fill
#: with allowances from tickets closed months ago, and the panel that exists to surface
#: what needs action would become the noise it was built to prevent.
ALLOWANCE_PENDING_CLOSURE = "ALLOWANCE_PENDING_CLOSURE"

#: Severities, so a caller cannot render one extreme and silently drop the other.
SEVERITY_HIGH_CONSUMPTION = "high_consumption"
SEVERITY_LOW_CONSUMPTION = "low_consumption"
SEVERITY_CONTRACT = "contract"

#: Something an operator has to *do*, as opposed to a consumption reading to interpret.
#:
#: A fourth severity rather than folding pending closure into ``SEVERITY_CONTRACT``,
#: because the frontend groups by severity and the two call for different responses: a
#: contract alert says "this client's agreement has a problem", and this one says "there
#: is a decision waiting on you, here is the button". Reusing the contract severity would
#: have put an action item in a column of readings.
SEVERITY_PENDING_ACTION = "pending_action"

_SEVERITY_BY_CODE = {
    HIGH_CONSUMPTION_BEFORE_MIDMONTH: SEVERITY_HIGH_CONSUMPTION,
    NEGATIVE_BALANCE: SEVERITY_HIGH_CONSUMPTION,
    PROJECTED_OVERRUN: SEVERITY_HIGH_CONSUMPTION,
    LOW_CONSUMPTION_AT_MONTH_END: SEVERITY_LOW_CONSUMPTION,
    ACCRUED_BALANCE_ABOVE_THRESHOLD: SEVERITY_LOW_CONSUMPTION,
    HOURS_DISCARDED_BY_CAP: SEVERITY_LOW_CONSUMPTION,
    NO_SERVICE_LOGS_IN_MONTH: SEVERITY_LOW_CONSUMPTION,
    CONTRACT_OUT_OF_VIGENCY: SEVERITY_CONTRACT,
    CONTRACT_SUSPENDED: SEVERITY_CONTRACT,
    ALLOWANCE_NEGATIVE_BALANCE: SEVERITY_HIGH_CONSUMPTION,
    ALLOWANCE_HIGH_CONSUMPTION: SEVERITY_HIGH_CONSUMPTION,
    ALLOWANCE_PENDING_CLOSURE: SEVERITY_PENDING_ACTION,
}

#: D35's grace period, in days from the closure of the work item.
#:
#: A module constant rather than a column, for the same reason
#: ``ALLOWANCE_HIGH_CONSUMPTION_PCT`` is: the clause is the same for every allowance
#: because it comes from the commercial policy, not from an individual negotiation. A
#: per-allowance field would be a setting nobody fills in, and a per-allowance grace
#: period is not something D35 contemplates.
ALLOWANCE_GRACE_DAYS = 30

#: When a work item allowance starts warning, as a percentage of what was credited.
#:
#: A module constant rather than a column on the allowance, unlike
#: ``ServiceContract.high_consumption_threshold_pct``. A contract is a negotiated
#: agreement whose thresholds are part of what was agreed; an allowance is one project's
#: pool, and giving every one of them its own threshold field would be a setting nobody
#: fills in. If a real need for per-allowance thresholds appears, that is the moment to
#: add the column, with the case that justified it.
ALLOWANCE_HIGH_CONSUMPTION_PCT = Decimal("80.00")

#: How much worse a balance has to get before a dismissed alert fires again.
#: Decision B2, and the hole it closes: with a plain unique on ``(period,
#: alert_code)``, dismissing the negative-balance alert at -5h silences it at -200h. An
#: alert that fires once, on the cheapest day, is not a guardrail.
#:
#: A **fixed** band rather than a percentage, deliberately. A percentage of a 10h/month
#: contract re-arms on almost every work log, and a percentage of a 200h contract stays
#: quiet through a disaster; the thing an operator actually wants is "tell me again when
#: it moves by about a morning's work", and that is an absolute quantity.
ALERT_REARM_BAND_HOURS = Decimal("5.0000")

#: How many months of contracted hours a carried balance may reach before it becomes a
#: churn signal. Section 9's "saldo acumulado acima de N meses de horas contratadas"
#: leaves N open; two months is the point at which the balance can no longer plausibly
#: be absorbed by ordinary use.
ACCRUED_BALANCE_MONTHS_THRESHOLD = Decimal("2")

#: A month counts as half over from the 16th. Acceptance criterion 18 puts its example
#: on the 10th, so the boundary only has to be after that and before the end; the 15th
#: is the natural reading of "antes do meio do mês".
MIDMONTH_DAY = 15

#: How close to the end of the period the low-consumption check starts looking. Section
#: 9 says "ao fim do mês"; a week's notice is what makes the alert actionable, since a
#: warning that arrives once the month is over cannot be acted on at all.
MONTH_END_WINDOW_DAYS = 7


def _pct(part, whole):
    """``part`` as a percentage of ``whole``, or ``None`` when there is no pool."""
    if whole is None or whole <= 0:
        return None

    return (Decimal(part) / Decimal(whole)) * Decimal("100")


def period_service_log_count(period):
    """How many live work logs debited this period. Acceptance criterion 19.

    Counts through ``debited_period``, the snapshot the debit engine writes, rather than
    by re-resolving contracts from dates. Re-resolving would answer a different question
    -- "which logs *would* debit this period now" -- and would change its answer when
    configuration changed, which is exactly what R4 forbids.
    """
    return ServiceLog.objects.filter(debited_period_id=period.pk).count()


def evaluate_period(period, *, reference_date=None, service_log_count=None, allowance_hours=None):
    """Every alert that applies to one open competency period.

    Returns a list of dicts carrying the code, the severity and the numbers that
    justified it, so the panel does not have to recompute anything to explain itself.

    Dismissals are **not** applied here. Filtering them is
    ``visible_alerts_for_period``, kept separate so that the evaluation can be tested
    without a dismissal fixture and so a report can legitimately ask for the unfiltered
    set.
    """
    reference_date = reference_date or timezone.now().date()
    contract = period.contract

    granted = period.granted_hours
    consumed = period.consumed_hours
    balance = period.balance_hours
    consumed_pct = _pct(consumed, granted)

    alerts = []

    def add(code, **detail):
        alerts.append({"code": code, "severity": _SEVERITY_BY_CODE[code], **detail})

    # ------------------------------------------------------------ high consumption

    if balance < 0:
        add(NEGATIVE_BALANCE, balance_hours=str(balance), granted_hours=str(granted))

    if (
        consumed_pct is not None
        and reference_date.day <= MIDMONTH_DAY
        and consumed_pct >= contract.high_consumption_threshold_pct
        and period.starts_on <= reference_date <= period.ends_on
    ):
        add(
            HIGH_CONSUMPTION_BEFORE_MIDMONTH,
            consumed_pct=str(consumed_pct.quantize(Decimal("0.01"))),
            threshold_pct=str(contract.high_consumption_threshold_pct),
            day_of_month=reference_date.day,
        )

    projected = _projected_consumption(period, consumed, reference_date)

    if projected is not None and granted > 0 and projected > granted and balance >= 0:
        add(
            PROJECTED_OVERRUN,
            projected_hours=str(projected.quantize(Decimal("0.0001"))),
            granted_hours=str(granted),
        )

    # ------------------------------------------------------------- low consumption

    if service_log_count is None:
        service_log_count = period_service_log_count(period)

    if service_log_count == 0 and period.starts_on <= reference_date:
        # The context, not a change of logic. See the note on NO_SERVICE_LOGS_IN_MONTH:
        # without this number the alert reads as "this client is not being served", which
        # is false for a client whose work is all on project allowances. Computed only
        # when the alert actually fires, so the cost is paid only for clients that look
        # idle.
        if allowance_hours is None:
            from plane.utils.service_allowance import workspace_allowance_hours_by_period

            allowance_hours = workspace_allowance_hours_by_period([period.pk]).get(
                period.pk, Decimal("0.0000")
            )

        add(
            NO_SERVICE_LOGS_IN_MONTH,
            competence=period.competence_label,
            allowance_hours_in_period=str(allowance_hours),
        )

    if (
        consumed_pct is not None
        and _is_near_month_end(period, reference_date)
        and consumed_pct < contract.low_consumption_threshold_pct
    ):
        add(
            LOW_CONSUMPTION_AT_MONTH_END,
            consumed_pct=str(consumed_pct.quantize(Decimal("0.01"))),
            threshold_pct=str(contract.low_consumption_threshold_pct),
        )

    accrued_ceiling = contract.monthly_hours * ACCRUED_BALANCE_MONTHS_THRESHOLD

    if period.carried_hours > accrued_ceiling:
        add(
            ACCRUED_BALANCE_ABOVE_THRESHOLD,
            carried_hours=str(period.carried_hours),
            threshold_hours=str(accrued_ceiling),
            months=str(ACCRUED_BALANCE_MONTHS_THRESHOLD),
        )

    if period.discarded_by_cap_hours > 0:
        add(HOURS_DISCARDED_BY_CAP, discarded_hours=str(period.discarded_by_cap_hours))

    # ------------------------------------------------------------------- contract

    for code in contract_warnings(contract, reference_date):
        add(code, contract_status=contract.status)

    return alerts


def _projected_consumption(period, consumed, reference_date):
    """Consumption extended over the rest of the period at the same daily rate.

    ``None`` outside the period's own bounds: projecting a month that has not started
    divides by zero, and projecting one that is over is not a projection.
    """
    if not (period.starts_on <= reference_date <= period.ends_on):
        return None

    elapsed = Decimal((reference_date - period.starts_on).days + 1)
    total = Decimal((period.ends_on - period.starts_on).days + 1)

    if elapsed <= 0:
        return None

    return (Decimal(consumed) / elapsed) * total


def _is_near_month_end(period, reference_date):
    """Whether the period is within its last week, or already over."""
    if reference_date > period.ends_on:
        return True

    return (period.ends_on - reference_date).days < MONTH_END_WINDOW_DAYS


# ---------------------------------------------------------------------------
# Dismissals -- section 9, and decision B2
# ---------------------------------------------------------------------------


def is_alert_dismissed(dismissal, current_balance):
    """Whether a recorded dismissal still silences its alert. Decision B2.

    It stops silencing once the balance has fallen more than ``ALERT_REARM_BAND_HOURS``
    below what it was when the dismissal was made. Without the recorded balance there
    would be nothing to compare against, which is why the column exists rather than the
    dismissal being a bare flag.

    **The accepted limitation, stated rather than buried:** this re-arms the *alert*, it
    does not cap the *deficit*. Nothing here stops a period reaching -200h; what it
    guarantees is that somebody was told again every 5 hours of the way down. A hard
    ceiling would contradict D4 and section 4, which forbid blocking work that has
    already been performed. A characterisation test fixes this so a later phase changes
    it on purpose.
    """
    if dismissal is None:
        return False

    return Decimal(current_balance) >= (Decimal(dismissal.balance_at_dismissal) - ALERT_REARM_BAND_HOURS)


def _dismissal_target_field(target):
    """Which of the D54 pair this target occupies: ``period`` or ``allowance``.

    The **only** place in the dismissal code that looks at the type. Everything after it
    -- recording, matching, re-arming -- runs through one path, which is possible because
    ``ServiceContractPeriod`` and ``ServiceIssueAllowance`` both expose ``balance_hours``
    and ``workspace_id``. If a third dismissable entity ever appears, this function and
    the constraint are the two places that change.
    """
    if isinstance(target, ServiceContractPeriod):
        return "period"

    if isinstance(target, ServiceIssueAllowance):
        return "allowance"

    raise TypeError(f"an alert dismissal cannot target {type(target).__name__}")


def _dismissals_for(target):
    """Recorded dismissals of one target, keyed by alert code."""
    field = _dismissal_target_field(target)

    return {
        dismissal.alert_code: dismissal
        for dismissal in ServiceAlertDismissal.objects.filter(**{f"{field}_id": target.pk})
    }


def _undismissed(alerts, target):
    """The alerts of one target that a recorded dismissal is not currently silencing."""
    dismissals = _dismissals_for(target)
    balance = target.balance_hours

    return [
        alert
        for alert in alerts
        if not is_alert_dismissed(dismissals.get(alert["code"]), balance)
    ]


def visible_alerts_for_period(
    period, *, reference_date=None, service_log_count=None, allowance_hours=None
):
    """The alerts of one period that have not been dismissed away.

    Section 9 requires alerts to be dismissible "para não virar ruído", and decision B2
    requires the dismissal to expire when things get materially worse.
    """
    return _undismissed(
        evaluate_period(
            period,
            reference_date=reference_date,
            service_log_count=service_log_count,
            allowance_hours=allowance_hours,
        ),
        period,
    )


def visible_alerts_for_allowance(allowance, *, reference_date=None):
    """The alerts of one work item allowance that have not been dismissed away. D54.

    Phase 5 could not offer this: ``period`` was a mandatory foreign key on the dismissal
    table, so there was nowhere to record the acknowledgement, and it was named as debt
    owed by this phase. With the D54 nullable pair in place, allowance alerts become
    dismissible through the **same** code path as period alerts -- including B2's
    re-arming, which works unchanged because an allowance has a ``balance_hours`` too.
    """
    return _undismissed(
        evaluate_allowance(allowance, reference_date=reference_date), allowance
    )


def dismiss_alert(target, alert_code, actor):
    """Record that an alert has been acknowledged for a period or an allowance. B2, D54.

    The balance at this moment is stored, because that is what re-arming compares
    against. Re-dismissing an alert that has re-armed **updates** the recorded balance
    rather than failing on the unique index -- the admin is acknowledging the worse
    number, and refusing would leave them unable to quiet an alert they have just seen.

    ``target`` is a ``ServiceContractPeriod`` or a ``ServiceIssueAllowance``. Which one it
    is decides a single keyword; the exclusivity between the two columns is the database's
    job, per D54, so this function cannot produce a row with two targets even if a caller
    passes something strange.
    """
    field = _dismissal_target_field(target)

    dismissal, created = ServiceAlertDismissal.objects.update_or_create(
        alert_code=alert_code,
        deleted_at=None,
        **{field: target},
        defaults={
            "workspace_id": target.workspace_id,
            "balance_at_dismissal": target.balance_hours,
            "dismissed_by": actor,
        },
    )

    return dismissal, created


# ---------------------------------------------------------------------------
# The panel -- section 9
# ---------------------------------------------------------------------------


def workspace_alert_panel(workspace_id, *, reference_date=None, contract_id=None):
    """Every client in a workspace that needs attention, with why.

    Visible to technicians and to the Admin, per section 9. Grouped by contract, because
    the pool is per contract: a client with support and infrastructure agreements can be
    healthy on one and overrunning on the other, and merging them would hide exactly the
    case that needs the call.

    Reads log counts for every period in **one** query rather than per period. The panel
    is the only place in this phase that scans a whole workspace, so it is the only place
    where an N+1 would be felt.
    """
    reference_date = reference_date or timezone.now().date()

    periods = list(open_periods_for_workspace(workspace_id, contract_id=contract_id))
    counts = _service_log_counts([period.pk for period in periods])

    # Batched for the same reason the counts are: this is the only place in the feature
    # that scans a whole workspace, so it is the only place an N+1 would be felt. Only
    # the periods that look idle need the number at all.
    idle_period_ids = [period.pk for period in periods if counts.get(period.pk, 0) == 0]
    allowance_hours = workspace_allowance_hours_by_period(idle_period_ids)

    entries = []

    for period in periods:
        alerts = visible_alerts_for_period(
            period,
            reference_date=reference_date,
            service_log_count=counts.get(period.pk, 0),
            allowance_hours=allowance_hours.get(period.pk, Decimal("0.0000")),
        )

        if not alerts:
            continue

        contract = period.contract

        entries.append(
            {
                "contract_id": str(contract.pk),
                "contract_code": contract.code,
                "contract_name": contract.name,
                "service_client_id": str(contract.service_client_id),
                "service_client_name": contract.service_client.name,
                "period_id": str(period.pk),
                "competence": period.competence_label,
                "granted_hours": str(period.granted_hours),
                "consumed_hours": str(period.consumed_hours),
                "balance_hours": str(period.balance_hours),
                "alerts": alerts,
                "has_high_consumption": any(
                    alert["severity"] == SEVERITY_HIGH_CONSUMPTION for alert in alerts
                ),
                "has_low_consumption": any(
                    alert["severity"] == SEVERITY_LOW_CONSUMPTION for alert in alerts
                ),
            }
        )

    return entries


def _service_log_counts(period_ids):
    """Live work log counts per period, in one query."""
    if not period_ids:
        return {}

    rows = (
        ServiceLog.objects.filter(debited_period_id__in=period_ids)
        .values("debited_period_id")
        .annotate(total=Count("id"))
    )

    return {row["debited_period_id"]: row["total"] for row in rows}


# ---------------------------------------------------------------------------
# Work item allowances -- section 3 of the Phase 5 brief
# ---------------------------------------------------------------------------


def grace_cutoff(reference_date=None):
    """The instant a work item must have closed before for D35's grace period to be over.

    Returned as a datetime because ``Issue.completed_at`` is one. Callers that only have a
    date pass it and get midnight, which is the conservative edge: an allowance becomes
    pending on the day *after* the 30th, never on it.
    """
    reference = reference_date or timezone.now().date()
    midnight = datetime.combine(reference, time.min)

    if timezone.is_naive(midnight):
        midnight = timezone.make_aware(midnight)

    return midnight - timedelta(days=ALLOWANCE_GRACE_DAYS)


def pending_closure_q(reference_date=None):
    """``Q`` matching allowances whose grace period has run out. D35, read-only form.

    Exists as a ``Q`` rather than a Python predicate for two reasons. It has to be
    **subtracted** from section 1's list of active allowances -- an allowance awaiting a
    decision is not an active pool, and showing it as one is what makes the panel rot --
    and doing that with ``exclude()`` keeps it one query instead of fetching everything to
    filter in Python.

    **Reopening the ticket cancels the countdown for free.** ``Issue._sync_completed_at``
    sets ``completed_at`` back to ``None`` on any move out of the completed group, so a
    reopened work item stops matching this without a line of code here. D35's "contada do
    fechamento do chamado" is the core's own notion of closure, reused rather than
    restated.

    **Named limitation:** a *cancelled* work item has ``completed_at`` null in Plane --
    only the completed group sets it -- so a cancelled ticket's allowance never becomes
    pending by this rule. Treating cancellation as closure for billing purposes is a
    commercial decision D35 does not make, and inventing a second definition of "closed"
    inside the reporting layer is exactly the divergence this phase is trying to avoid.
    Registered rather than silently patched.
    """
    return Q(
        status=ServiceIssueAllowance.Status.OPEN,
        issue__completed_at__isnull=False,
        issue__completed_at__lt=grace_cutoff(reference_date),
    )


def evaluate_allowance(allowance, *, reference_date=None):
    """Every alert that applies to one open work item allowance.

    The two consumption alerts are mutually exclusive by construction: a negative
    balance, or high consumption while still positive. Emitting both for the same
    allowance would be one fact reported twice, and a panel counting alerts would double
    it.

    **Pending closure is additive to those, not exclusive with them**, and the asymmetry
    is deliberate. The consumption pair are two readings of one quantity, so only one can
    be true. Pending closure is about the *work item*, not about the balance, so it can
    coexist -- and when it does, the two facts need two different actions: bill the
    overage first (section 3 of Phase 5 requires the deficit resolved *before* closing),
    then close. Collapsing them would hide the ordering.

    **Nothing here blocks anything.** Section 3 is explicit that overflowing an allowance
    does not stop work already performed -- the balance goes negative and the alert
    appears. Same reasoning as D4 and ``NEGATIVE_BALANCE`` for a contract pool. And
    pending closure does not write anything either: see ``ALLOWANCE_PENDING_CLOSURE`` for
    why D35 informs instead of executing.
    """
    balance = allowance.balance_hours
    credited = allowance.credited_hours
    consumed_pct = _pct(allowance.consumed_hours, credited)

    alerts = []

    def add(code, **detail):
        alerts.append({"code": code, "severity": _SEVERITY_BY_CODE[code], **detail})

    if balance < 0:
        add(
            ALLOWANCE_NEGATIVE_BALANCE,
            balance_hours=str(balance),
            credited_hours=str(credited),
            consumed_hours=str(allowance.consumed_hours),
        )
    elif consumed_pct is not None and consumed_pct >= ALLOWANCE_HIGH_CONSUMPTION_PCT:
        add(
            ALLOWANCE_HIGH_CONSUMPTION,
            consumed_pct=str(consumed_pct.quantize(Decimal("0.01"))),
            threshold_pct=str(ALLOWANCE_HIGH_CONSUMPTION_PCT),
            balance_hours=str(balance),
        )

    completed_at = getattr(allowance.issue, "completed_at", None)

    if (
        allowance.status == ServiceIssueAllowance.Status.OPEN
        and completed_at is not None
        and completed_at < grace_cutoff(reference_date)
    ):
        add(
            ALLOWANCE_PENDING_CLOSURE,
            # The balance is carried because it is what the decision is *about*: a
            # positive one is the client's unused credit and a negative one is an
            # unbilled overage, and those are opposite conversations.
            balance_hours=str(balance),
            issue_completed_on=completed_at.date().isoformat(),
            grace_days=ALLOWANCE_GRACE_DAYS,
            days_since_closure=(
                (reference_date or timezone.now().date()) - completed_at.date()
            ).days,
        )

    return alerts


def workspace_allowance_alerts(workspace_id, *, project_id=None, reference_date=None):
    """Every work item allowance in a workspace that needs attention, with why.

    Returned as its own list rather than folded into ``workspace_alert_panel``'s entries,
    because that structure is keyed by contract and competency and an allowance has
    neither. Merging them would have meant giving every entry a nullable contract, which
    is the shape that makes a caller guess.

    **These are dismissible as of D54.** Phase 5 shipped them undismissable, because
    ``ServiceAlertDismissal.period`` was mandatory and there was nowhere to record the
    acknowledgement; it was named as debt owed by this phase rather than hidden in a
    comment. The D54 nullable pair closed it, and the dismissal now runs through the same
    ``_undismissed`` path period alerts use, re-arming included.
    """
    entries = []

    allowances = open_allowances_for_workspace(workspace_id, project_id=project_id)

    for allowance in allowances.select_related("issue", "project"):
        alerts = visible_alerts_for_allowance(allowance, reference_date=reference_date)

        if not alerts:
            continue

        entries.append(
            {
                "allowance_id": str(allowance.pk),
                "issue_id": str(allowance.issue_id),
                "issue_name": allowance.issue.name,
                "project_id": str(allowance.project_id),
                "project_name": allowance.project.name,
                "reference": allowance.reference,
                "credited_hours": str(allowance.credited_hours),
                "consumed_hours": str(allowance.consumed_hours),
                "balance_hours": str(allowance.balance_hours),
                "alerts": alerts,
            }
        )

    return entries


def contracts_without_default(workspace_id):
    """Clients holding several contracts with none marked as the default.

    A configuration fault rather than a consumption alert, and it is here because the
    symptom appears at the worst possible moment otherwise: the first work log on a
    project with no pinned contract fails with ``AMBIGUOUS_CONTRACT_RESOLUTION`` in
    front of a technician who cannot fix it. Surfacing it on the admin's panel turns a
    blocked entry into a setting somebody adjusts beforehand.
    """
    client_ids = (
        ServiceContract.objects.filter(workspace_id=workspace_id)
        .values_list("service_client_id", flat=True)
        .distinct()
    )

    faulty = []

    for client_id in client_ids:
        contracts = ServiceContract.objects.filter(service_client_id=client_id)

        if contracts.count() > 1 and not contracts.filter(is_default=True).exists():
            faulty.append(str(client_id))

    return faulty
