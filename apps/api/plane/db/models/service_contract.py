# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
from decimal import Decimal

# Django imports
from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q

# Module imports
from ..mixins import ChangeTrackerMixin
from .service_catalog import ServiceConfigEntity
from .workspace import WorkspaceBaseModel


class ServiceContractStatus(models.TextChoices):
    """Where a contract stands commercially.

    Declared at module level, not nested, because the check constraints in ``Meta``
    reference these values and a class nested in the model is not yet in scope while
    ``Meta`` is evaluated -- the same reason ``ServiceLogSource`` is at module level.
    Reachable as ``ServiceContract.Status``.

    **SUSPENDED changes no behaviour except which alert fires** (decision B4). It still
    debits, exactly like an expired contract does under D9. "Suspended blocks new work
    but accepts retroactive entries" would be a new business rule, and it is written
    nowhere -- not in the brief, not in DECISOES.md. Inventing it here would be
    inventing a financial rule, which section 7.5 of the master context forbids. What
    the distinct status buys is a distinct alert code, so the operations report can
    separate "contract lapsed" from "contract suspended" without a second column.
    """

    ACTIVE = "active", "Active"
    SUSPENDED = "suspended", "Suspended"
    ENDED = "ended", "Ended"


class ServiceAccrualCapMode(models.TextChoices):
    """How the ceiling on carried balance is expressed. Decision D5.

    Two nullable columns -- ``accrual_cap_hours`` and ``accrual_cap_multiple`` -- would
    admit a state with no meaning: both filled, and nothing saying which wins. An enum
    plus one value column makes the incoherent state **unrepresentable** rather than
    merely refused by a validation someone forgets to call.
    """

    NONE = "none", "No ceiling"
    ABSOLUTE = "absolute", "Absolute hours"
    MULTIPLE = "multiple", "Multiple of monthly hours"


class ServiceOveragePolicy(models.TextChoices):
    """What the contract *prefers* to do with a deficit at closing time.

    A preference, not a rule. Section 4 of the phase brief is explicit that the choice
    is commercial and "deve ser confirmável período a período", so the binding decision
    lives on the period as ``overage_settlement`` and this only pre-selects it.
    """

    CARRY_DEFICIT = "carry_deficit", "Carry the deficit forward"
    BILL_AMOUNT = "bill_amount", "Bill the overage"


class ServicePeriodStatus(models.TextChoices):
    """Whether a competency month still accepts movement.

    CLOSED is what protects an already invoiced month. There is no approval workflow
    for timesheets (D10); this lock is the whole protection.
    """

    OPEN = "open", "Open"
    CLOSED = "closed", "Closed"


class ServiceOverageSettlement(models.TextChoices):
    """How a closed period's deficit was actually settled. Section 4, decision B3.

    Null while the period is open, and null on a period that closed without a deficit
    -- there was nothing to settle.
    """

    CARRIED = "carried", "Deficit carried to the next period"
    BILLED = "billed", "Overage billed to the client"


class ServiceLedgerEntryType(models.TextChoices):
    """Every way hours can move in or out of a competency period.

    The set is closed on purpose: a movement that does not fit one of these is a
    movement nobody designed, and it would land in the balance without a name.

    Sign convention, **from the perspective of the period the entry belongs to**:

    ==========================  ======  ==================================================
    ``GRANT``                     +     the month's contracted quota
    ``CARRY_IN``                  +/-   a parcel arriving; negative when it is a deficit
    ``CARRY_OUT``                 -/+   the same parcel leaving at close, mirrored
    ``DEBIT``                     -     a work log's ``debited_hours``
    ``REVERSAL``                  +     that debit undone
    ``OVERAGE_BILLED``            +     a deficit cancelled by billing it in reais
    ``EXPIRED_BY_VALIDITY``       -     a parcel older than ``carryover_months``
    ``EXPIRED_BY_CAP``            -     balance above the accrual ceiling
    ``EXPIRED_BY_CONTRACT_END``   -     balance lost when the contract ended
    ``TRANSFERRED_TO_CONTRACT``   -     balance moved to a successor contract
    ``CONVERTED_TO_ISSUE_ALLOWANCE``  - balance turned into a work item allowance
    ``CREDIT``                    +     hours credited to a work item allowance
    ``EXPIRED_BY_ALLOWANCE_CLOSE``  -   an allowance's surplus written off at close
    ==========================  ======  ==================================================

    The last two belong to a **work item allowance** rather than to a competency
    period, and the sign convention reads against the allowance instead. Phase 5 reuses
    this table rather than opening a second journal, and the reason is not tidiness:
    the partial unique index on ``(service_log, entry_type)`` below then makes
    "debit the allowance **and** the contract for one work log" a state the **database**
    refuses, because both rows would be a ``DEBIT`` of the same work log. That is
    acceptance criterion 7 of Phase 5 held structurally instead of by an ``if``.

    Two invariants follow from the convention, and both are asserted by
    ``plane.utils.service_pool.reconcile_period`` -- and, for an allowance, by
    ``plane.utils.service_allowance.reconcile_allowance``:

    * an **OPEN** period's entries sum to its ``balance_hours``;
    * a **CLOSED** period's entries sum to exactly **zero** -- everything either left,
      was billed, or was written off, and every one of those is a row.

    That second invariant is what makes acceptance criterion 20 -- "saldo nunca
    desaparece sem registro de auditoria" -- true *by construction* rather than by
    diligence. There is no code path that reduces a balance without inserting a row,
    because the reduction **is** the row. A **closed allowance** sums to zero for the
    same reason and by the same mechanism.
    """

    GRANT = "grant", "Monthly quota granted"
    CARRY_IN = "carry_in", "Balance carried in"
    CARRY_OUT = "carry_out", "Balance carried out"
    DEBIT = "debit", "Work log debited"
    REVERSAL = "reversal", "Work log debit reversed"
    OVERAGE_BILLED = "overage_billed", "Deficit billed as overage"
    EXPIRED_BY_VALIDITY = "expired_by_validity", "Expired by carryover validity"
    EXPIRED_BY_CAP = "expired_by_cap", "Discarded by accrual cap"
    EXPIRED_BY_CONTRACT_END = "expired_by_contract_end", "Expired at contract end"
    TRANSFERRED_TO_CONTRACT = "transferred_to_contract", "Transferred to a successor contract"
    CONVERTED_TO_ISSUE_ALLOWANCE = "converted_to_issue_allowance", "Converted to a work item allowance"
    CREDIT = "credit", "Hours credited to a work item allowance"
    EXPIRED_BY_ALLOWANCE_CLOSE = "expired_by_allowance_close", "Allowance surplus written off at close"


#: Entry types that record a debit or its reversal against one work log. These are the
#: two the partial unique index covers, which is what makes both operations idempotent
#: at the database level instead of by an ``if`` in the application.
SERVICE_LOG_LEDGER_ENTRY_TYPES = [
    ServiceLedgerEntryType.DEBIT,
    ServiceLedgerEntryType.REVERSAL,
]

#: Entry types that may only ever remove hours. Guarded in DDL below: a positive
#: expiry would be a credit dressed as a write-off.
NEGATIVE_ONLY_LEDGER_ENTRY_TYPES = [
    ServiceLedgerEntryType.DEBIT,
    ServiceLedgerEntryType.EXPIRED_BY_VALIDITY,
    ServiceLedgerEntryType.EXPIRED_BY_CAP,
    ServiceLedgerEntryType.EXPIRED_BY_CONTRACT_END,
    ServiceLedgerEntryType.TRANSFERRED_TO_CONTRACT,
    ServiceLedgerEntryType.CONVERTED_TO_ISSUE_ALLOWANCE,
    ServiceLedgerEntryType.EXPIRED_BY_ALLOWANCE_CLOSE,
]

#: Entry types that may only ever add hours.
POSITIVE_ONLY_LEDGER_ENTRY_TYPES = [
    ServiceLedgerEntryType.REVERSAL,
    ServiceLedgerEntryType.OVERAGE_BILLED,
    ServiceLedgerEntryType.CREDIT,
]

#: Entry types that are legitimately signed either way, and therefore have no sign
#: guard in DDL.
#:
#: ``CARRY_IN`` and ``CARRY_OUT`` because a carried **deficit** is a negative parcel --
#: that is the whole mechanism behind acceptance criterion 8.
#:
#: ``GRANT`` is here for a less obvious reason, and it is the one place the closed set
#: of entry types had to bend. Decision B1 makes ``contracted_hours`` editable while a
#: period is OPEN, so correcting a partial month from 30h to 15h has to move the
#: balance -- and in an append-only journal a correction is a new row, never an edited
#: one. That row is a ``GRANT`` of ``-15``. The alternative was a twelfth entry type
#: meaning "grant adjustment", which would have made ``sum(GRANT) == contracted_hours``
#: -- the invariant reconciliation actually checks -- a two-term sum for no gain.
SIGNED_LEDGER_ENTRY_TYPES = [
    ServiceLedgerEntryType.GRANT,
    ServiceLedgerEntryType.CARRY_IN,
    ServiceLedgerEntryType.CARRY_OUT,
]


class ServiceContract(ChangeTrackerMixin, WorkspaceBaseModel):
    """A client's agreement to buy a monthly pool of hours.

    One client may hold **several active contracts at once** -- support 10h/month and
    infrastructure 20h/month, with separate pools (section 1b of the phase brief, and
    the addendum to D19). Which one a work log debits is resolved by
    ``plane.utils.service_pool.resolve_contract``: the contract pinned on the project
    first, then the client's default contract in force on the service date. An
    ambiguous or empty resolution **fails explicitly** -- debiting the wrong pool is
    worse than refusing the entry.

    **Extends ``WorkspaceBaseModel`` directly, and NOT ``ServiceCatalogBaseModel``,
    even though it also carries an ``is_default`` flag.** That base's single-default
    constraint is declared ``fields=["workspace"]``, so inheriting it would enforce one
    default contract per *workspace* when what this needs is one per *client*.
    Verified in ``service_catalog.py``. Reusing the base would have produced a
    constraint that is wrong in a way no test of a single-client fixture would catch.

    Inherited from ``WorkspaceBaseModel``: ``workspace`` (required) plus a nullable
    ``project`` FK that this model does not use and that serializers exclude. The link
    to a project runs the other way, through ``Project.service_contract``, because a
    contract may serve several projects.

    **This phase holds no money.** ``overage_hour_rate`` is stored and audited but has
    no consumer here, and ``ServiceClient`` deliberately gains no base hour rate --
    creating one would invade the pricing phase. Overage is recorded in **hours**
    (``ServiceContractPeriod.overage_hours``); converting it to reais is Phase 6's job
    and is a documented extension point, not an omission.
    """

    Status = ServiceContractStatus
    AccrualCapMode = ServiceAccrualCapMode
    OveragePolicy = ServiceOveragePolicy

    # Every field here decides what the client is charged or how much of the pool
    # exists, so every one of them is audited. The two alert thresholds are the
    # deliberate exceptions: they change who gets *warned*, not what anything costs.
    #
    # Three of these are NULLABLE -- `carryover_months`, `accrual_cap_value` and
    # `overage_hour_rate` -- and that is exactly the bill the calendar phase left for
    # this one. `svc_cfg_activity_shape_matches_verb` requires old_value and new_value
    # to be non-null on an `updated` row, and `serialize_config_value` renders None as
    # None. The sentinel in `plane.utils.service_catalog.CONFIG_VALUE_UNSET` is what
    # reconciles the two; see decision D4.
    TRACKED_FIELDS = [
        "monthly_hours",
        "starts_on",
        "ends_on",
        "status",
        "accrual_cap_mode",
        "accrual_cap_value",
        "carryover_months",
        "overage_policy",
        "overage_hour_rate",
    ]
    CONFIG_ENTITY_NAME = ServiceConfigEntity.CONTRACT

    # ---------------------------------------------------------------- relations

    # DO_NOTHING, the house rule for a configuration foreign key that a work log's
    # billing depends on. `soft_delete_related_objects` dispatches on the on_delete
    # name and funnels everything except DO_NOTHING and SET_NULL into a catch-all that
    # soft deletes the related rows -- so CASCADE or PROTECT here would mean an admin
    # tidying the client list silently deletes contracts, and with them the pools that
    # invoices were built from. Same choice and same reasons as
    # `Project.service_client` and `ServiceLog.hour_type`.
    service_client = models.ForeignKey(
        "db.ServiceClient",
        on_delete=models.DO_NOTHING,
        related_name="contracts",
    )

    # The contract number as the client knows it. Unique per client rather than per
    # workspace: two clients may legitimately both call their agreement "001".
    code = models.CharField(max_length=100)

    # What the pool is for, e.g. "Suporte" or "Infraestrutura". Portuguese because
    # these rows are data for a Brazilian operation, not UI chrome.
    name = models.CharField(max_length=255)

    # ------------------------------------------------------------------ the pool

    # Scale fixed by section 4b of the master context. Do not widen locally.
    #
    # This is the *template*, not the truth of any given month: each period snapshots
    # it into `contracted_hours` (R4). Raising it in June must not reprice January.
    monthly_hours = models.DecimalField(
        max_digits=10,
        decimal_places=4,
        validators=[MinValueValidator(Decimal("0.0001"))],
    )

    starts_on = models.DateField()
    ends_on = models.DateField()

    # How many months a carried parcel stays usable. Empty means it never expires,
    # which section 3 of the brief makes the explicit default.
    #
    # Counted in *carries*, and the boundary is worth stating because off-by-one here
    # silently deletes a client's hours: with 2, a parcel originating in January is
    # usable in February and March and expires at the close of March. The rule is
    # `months_between(origin, closing_period) >= carryover_months`, and there is a
    # test named after this sentence.
    carryover_months = models.PositiveSmallIntegerField(null=True, blank=True)

    # Decision D5. The pair is kept coherent by a check constraint below, not by
    # validation: `mode = NONE` if and only if `value IS NULL`, and the value must be
    # positive otherwise.
    accrual_cap_mode = models.CharField(
        max_length=20,
        choices=ServiceAccrualCapMode.choices,
        default=ServiceAccrualCapMode.NONE,
    )
    accrual_cap_value = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)

    # ------------------------------------------------------------------- alerts

    # Section 9 of the brief: the two extremes of consumption are both actionable.
    # High consumption risks unpaid work; low consumption is a churn signal, because a
    # client paying for a pool they do not use will question the renewal.
    #
    # NOT in TRACKED_FIELDS, and non-nullable, both deliberately. They change who is
    # warned, never what an hour costs, so auditing them would dilute a trail whose
    # whole value is that every row in it explains a number on an invoice. Non-nullable
    # keeps them out of the sentinel's blast radius entirely.
    high_consumption_threshold_pct = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("80.00"))
    low_consumption_threshold_pct = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("30.00"))

    # ----------------------------------------------------------------- overage

    overage_policy = models.CharField(
        max_length=20,
        choices=ServiceOveragePolicy.choices,
        default=ServiceOveragePolicy.CARRY_DEFICIT,
    )

    # An optional override of the client's base hour rate, for billing overage.
    #
    # It was stored before it was read because it is a *term of the contract*, so the
    # moment a contract is registered is the moment its value is known, and adding an
    # audited column later means back-filling an audit trail that cannot be back-filled
    # honestly. Monetary scale (12, 2) per section 4b, not the hour scale.
    #
    # **Phase 6 consumes it**: `plane.utils.service_pricing.resolve_overage_rate` reads
    # it first and falls back to the client's base hour rate for the vigency in force.
    # Null therefore means "bill overage at whatever support costs", not "free" -- and
    # the check constraint below is what keeps zero from becoming a third answer.
    overage_hour_rate = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    # ------------------------------------------------------------------ lifecycle

    status = models.CharField(
        max_length=20,
        choices=ServiceContractStatus.choices,
        default=ServiceContractStatus.ACTIVE,
    )

    # The renewal chain, so "encerrar e criar contrato novo" (section 8c) keeps the
    # history navigable. DO_NOTHING and self-referential; null on the first contract of
    # a chain.
    previous_contract = models.ForeignKey(
        "self",
        on_delete=models.DO_NOTHING,
        related_name="successors",
        null=True,
        blank=True,
    )

    # The contract a work log falls back to when its project pins none. Enforced as
    # *at most one per client* by a partial unique index below.
    #
    # There is deliberately no "at least one" guarantee here, unlike the catalogues. A
    # client may genuinely have every contract pinned to a project, and forcing a
    # default would make the first contract of a client special for no reason. The
    # absence of a default surfaces as `NO_DEFAULT_CONTRACT_FOR_CLIENT` at resolution
    # time, which is a legible error rather than a silent wrong pool.
    is_default = models.BooleanField(default=False)

    notes = models.TextField(blank=True)

    # Import provenance, following the convention used across plane.db.
    external_source = models.CharField(max_length=255, null=True, blank=True)
    external_id = models.CharField(max_length=255, null=True, blank=True)

    def config_summary(self):
        """One line for a creation or deletion audit row.

        Carries the two terms that decide the size of the pool -- the monthly hours and
        the vigency -- because those are what a reader reconstructing a disputed
        invoice needs before anything else.
        """
        return f"{self.code} - {self.name} ({self.monthly_hours}h/mes, {self.starts_on} a {self.ends_on})"

    def covers(self, target_date):
        """Whether ``target_date`` falls inside the contract's vigency.

        Vigency is inclusive at both ends. Being outside it does **not** stop a debit
        (D9): it raises an alert and marks the entry as out of contractual vigency.
        """
        return self.starts_on <= target_date <= self.ends_on

    def accrual_cap_hours(self):
        """The ceiling on carried balance, in hours, or ``None`` when uncapped.

        Resolves the enum into a number so that callers never branch on the mode. A
        multiple is taken against ``monthly_hours``, which is what section 1 of the
        brief means by "2x": a 30h/month contract capped at 2x carries at most 60h.
        """
        if self.accrual_cap_mode == ServiceAccrualCapMode.ABSOLUTE:
            return self.accrual_cap_value

        if self.accrual_cap_mode == ServiceAccrualCapMode.MULTIPLE:
            return self.monthly_hours * self.accrual_cap_value

        return None

    class Meta:
        verbose_name = "Service Contract"
        verbose_name_plural = "Service Contracts"
        db_table = "service_contracts"
        ordering = ("service_client__name", "code")

        # Uniqueness under soft delete follows the house pattern: unique_together
        # including deleted_at, plus a conditional UniqueConstraint for live rows.
        unique_together = [["service_client", "code", "deleted_at"]]

        constraints = [
            models.UniqueConstraint(
                fields=["service_client", "code"],
                condition=Q(deleted_at__isnull=True),
                name="service_contract_unique_code_per_client_when_deleted_at_null",
            ),
            # PER CLIENT, not per workspace. This is the constraint that made
            # inheriting ServiceCatalogBaseModel impossible -- see the class docstring.
            models.UniqueConstraint(
                fields=["service_client"],
                condition=Q(is_default=True, deleted_at__isnull=True),
                name="service_contract_single_default_per_client",
            ),
            models.CheckConstraint(
                condition=Q(ends_on__gte=models.F("starts_on")),
                name="service_contract_vigency_is_ordered",
            ),
            models.CheckConstraint(
                condition=Q(monthly_hours__gt=0),
                name="service_contract_monthly_hours_is_positive",
            ),
            # Decision D5, in DDL. The incoherent state is not refused, it is
            # unrepresentable: a mode with no value, or a value with no mode, cannot
            # be written by any code path including a bulk update or a data migration.
            models.CheckConstraint(
                condition=(
                    Q(accrual_cap_mode=ServiceAccrualCapMode.NONE, accrual_cap_value__isnull=True)
                    | (
                        ~Q(accrual_cap_mode=ServiceAccrualCapMode.NONE)
                        & Q(accrual_cap_value__isnull=False, accrual_cap_value__gt=0)
                    )
                ),
                name="service_contract_accrual_cap_is_coherent",
            ),
            # Added by Phase 6, on a column Phase 4 left unguarded because nothing read
            # it. Now that overage is priced, a negative rate would credit the client for
            # exceeding their pool and a zero rate would be a second mechanism for "do
            # not charge" (D20). Null stays legal and means "fall back to the client's
            # base hour rate", which is why this permits null rather than being a plain
            # `> 0`.
            models.CheckConstraint(
                condition=Q(overage_hour_rate__isnull=True) | Q(overage_hour_rate__gt=0),
                name="service_contract_overage_rate_is_positive",
            ),
        ]

        indexes = [
            # Resolution reads a client's contracts and filters by vigency.
            models.Index(fields=["service_client", "status"], name="svc_contract_client_status_idx"),
            models.Index(fields=["workspace", "status"], name="svc_contract_ws_status_idx"),
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"


class ServiceContractPeriod(ChangeTrackerMixin, WorkspaceBaseModel):
    """One competency month of one contract: the pool that work logs debit.

    **This row carries the balance; the ledger carries the history** (design decision
    D2). Reading the whole ledger on every debit would not scale and would make the
    lock awkward, so the totals live here, this is the row that
    ``select_for_update()`` locks, and both are written **in the same transaction,
    always**. ``plane.utils.service_pool.reconcile_period`` asserts that the two
    agree, with a dedicated test and a repair management command behind it -- because
    two numbers that are supposed to be equal diverge silently otherwise, and nobody
    finds out until a client disputes the invoice.

    ``granted_hours`` and ``balance_hours`` are **properties, not columns** (decision
    D3). A ``GeneratedField`` was considered and refused: it would be the first in the
    repository, introduced in the phase that handles money, and ``annotate()`` gives
    the alert panel the same filter without storing anything new. The table holds one
    row per contract per month -- roughly 3.000 rows after five years of operation --
    so there is no query to optimise yet.

    Audited as ``CONTRACT_PERIOD`` for one field only: ``contracted_hours``, which
    decision B1 makes editable while the period is OPEN.
    """

    Status = ServicePeriodStatus
    OverageSettlement = ServiceOverageSettlement

    # Only `contracted_hours`. Decision B1 makes it editable so that a partial first
    # month is *informed by the admin* rather than guessed by a pro-rata rule the
    # system invented -- and an editable snapshot of a billing quantity that is not
    # audited is an invitation to rewrite an invoiced month quietly.
    #
    # `consumed_hours` and `carried_hours` are deliberately NOT tracked: they move on
    # every work log, and auditing them here would duplicate the ledger, which already
    # records every movement with its cause. Section 6 of the master context forbids a
    # second audit trail, and this is where that could most easily creep in.
    TRACKED_FIELDS = ["contracted_hours"]
    CONFIG_ENTITY_NAME = ServiceConfigEntity.CONTRACT_PERIOD

    contract = models.ForeignKey(
        "db.ServiceContract",
        on_delete=models.DO_NOTHING,
        related_name="periods",
    )

    # Competency as two integers rather than a date. The pool of "January 2026" is not
    # an instant, and storing a first-of-month date would invite arithmetic that
    # accidentally depends on the day.
    competence_year = models.SmallIntegerField()
    competence_month = models.SmallIntegerField()

    # The real boundaries of this period, which can be partial: a contract starting on
    # the 10th has a first period from the 10th to the end of that month. Kept as real
    # dates so that "which period does this service date belong to" never has to guess.
    starts_on = models.DateField()
    ends_on = models.DateField()

    # ----------------------------------------------------------- the pool totals

    # R4's snapshot, and the thing that stops a change to `monthly_hours` from
    # rewriting a month that was already invoiced. Defaults to the contract's
    # `monthly_hours` at materialisation time.
    #
    # EDITABLE WHILE THE PERIOD IS OPEN (decision B1), which is how a partial first or
    # last month is expressed. Refused on a CLOSED period, and every edit lands in the
    # configuration audit trail. Pro-rata is therefore a *datum the admin supplies when
    # the contract says so*, not a policy column and not a guess.
    contracted_hours = models.DecimalField(max_digits=10, decimal_places=4)

    # **Can be negative.** A deficit carried from the previous period is exactly this
    # column holding a negative number, which is what makes acceptance criterion 8 --
    # "o mês seguinte abre com 27h em vez de 30h" -- a subtraction rather than a
    # special case.
    carried_hours = models.DecimalField(max_digits=10, decimal_places=4, default=Decimal("0.0000"))

    # The accumulator every debit moves. Always written under `select_for_update()` on
    # this row and always with an `F()` expression -- never read-modify-write, which is
    # the failure acceptance criterion 14 exists to catch.
    consumed_hours = models.DecimalField(max_digits=10, decimal_places=4, default=Decimal("0.0000"))

    # ------------------------------------------------- what happened at closing

    # Both are records of the close, **not terms in the live balance**. `balance_hours`
    # is `granted - consumed`, exactly as section 2 of the brief defines it; these two
    # explain where a closed period's balance went.

    # Acceptance criterion 15. Visible on purpose: hours discarded by the ceiling are
    # the clearest signal that a client is paying for hours they do not use, which
    # section 9 treats as a churn risk rather than a footnote.
    discarded_by_cap_hours = models.DecimalField(max_digits=10, decimal_places=4, default=Decimal("0.0000"))

    # Acceptance criterion 9, in hours. The conversion to reais is Phase 6.
    overage_hours = models.DecimalField(max_digits=10, decimal_places=4, default=Decimal("0.0000"))

    # Null while open, and null on a period that closed with no deficit to settle.
    overage_settlement = models.CharField(
        max_length=20,
        choices=ServiceOverageSettlement.choices,
        null=True,
        blank=True,
    )

    status = models.CharField(
        max_length=20,
        choices=ServicePeriodStatus.choices,
        default=ServicePeriodStatus.OPEN,
    )

    closed_at = models.DateTimeField(null=True, blank=True)

    # SET_NULL, unlike the billing foreign keys. This one is a trace of who clicked
    # close, not a term in a calculation, and it follows the convention of
    # `IssueActivity.actor` and `created_by`.
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="closed_service_contract_periods",
        null=True,
        blank=True,
    )

    @property
    def granted_hours(self):
        """Contracted plus carried. Section 2 of the phase brief.

        A property rather than a column (D3). The queryset equivalent, for filtering
        and reporting, is ``plane.utils.service_pool.annotate_period_balance``.
        """
        return self.contracted_hours + self.carried_hours

    @property
    def balance_hours(self):
        """Granted minus consumed. Section 2 of the phase brief.

        Negative is a legitimate, expected state -- D4 and section 4 are explicit that
        work already performed is never blocked for want of balance.
        """
        return self.granted_hours - self.consumed_hours

    @property
    def competence_label(self):
        """``YYYY-MM``, for audit notes and error payloads."""
        return f"{self.competence_year:04d}-{self.competence_month:02d}"

    @property
    def competence_index(self):
        """Months since year zero, so two competencies can be compared and subtracted.

        The only correct way to ask "how many months apart are these two periods",
        which is what the carryover validity rule needs.
        """
        return self.competence_year * 12 + (self.competence_month - 1)

    def config_summary(self):
        """One line for a creation or deletion audit row."""
        return f"{self.contract.code} {self.competence_label} ({self.contracted_hours}h contratadas)"

    class Meta:
        verbose_name = "Service Contract Period"
        verbose_name_plural = "Service Contract Periods"
        db_table = "service_contract_periods"
        ordering = ("contract", "competence_year", "competence_month")

        unique_together = [["contract", "competence_year", "competence_month", "deleted_at"]]

        constraints = [
            # The index concurrent materialisation resolves against: two requests
            # racing to create the same competency both call `get_or_create`, one
            # loses here, and the loser retries and reads the winner's row.
            models.UniqueConstraint(
                fields=["contract", "competence_year", "competence_month"],
                condition=Q(deleted_at__isnull=True),
                name="service_period_unique_competence_when_deleted_at_null",
            ),
            models.CheckConstraint(
                condition=Q(competence_month__gte=1, competence_month__lte=12),
                name="service_period_competence_month_is_valid",
            ),
            models.CheckConstraint(
                condition=Q(ends_on__gte=models.F("starts_on")),
                name="service_period_bounds_are_ordered",
            ),
            # `carried_hours` is deliberately absent from these: it is the one hour
            # column on this model that may legitimately be negative.
            models.CheckConstraint(
                condition=Q(contracted_hours__gte=0),
                name="service_period_contracted_hours_is_not_negative",
            ),
            models.CheckConstraint(
                condition=Q(discarded_by_cap_hours__gte=0, overage_hours__gte=0),
                name="service_period_closing_records_are_not_negative",
            ),
            # An open period cannot have settled anything, and a settlement without a
            # close would be a decision nobody took.
            models.CheckConstraint(
                condition=(
                    Q(status=ServicePeriodStatus.OPEN, overage_settlement__isnull=True)
                    | Q(status=ServicePeriodStatus.CLOSED)
                ),
                name="service_period_settlement_requires_closed",
            ),
        ]

        indexes = [
            models.Index(
                fields=["contract", "competence_year", "competence_month"],
                name="svc_period_competence_idx",
            ),
            models.Index(fields=["contract", "status"], name="svc_period_contract_status_idx"),
            # The alert panel scans open periods across a workspace.
            models.Index(fields=["workspace", "status"], name="svc_period_ws_status_idx"),
        ]

    def __str__(self):
        return f"{self.contract_id} {self.competence_label}"


class ServiceHourLedgerEntry(WorkspaceBaseModel):
    """One movement of hours in or out of a competency period. Append-only.

    Four requirements of the phase brief are the same requirement wearing different
    clothes, and this table is the one mechanism that answers all four:

    * tracking each parcel's competency of origin, so the right one expires
      (criterion 6);
    * an idempotent reversal (criteria 11 and 12);
    * "saldo nunca desaparece sem registro de auditoria" (criterion 20);
    * concurrent debits that do not corrupt the balance (criterion 14).

    Without a journal each of those needs its own machinery. With one, criterion 20 is
    satisfied **by construction**: there is no path that reduces a balance without
    inserting a row, because the reduction *is* the row.

    **This is not the "second audit table" that section 6 of the master context
    forbids.** The division is explicit and worth keeping straight:
    ``ServiceConfigActivity`` records **configuration** -- a contract was created, its
    monthly hours were changed. This records **accounting movement**. It is the datum,
    not the log of the datum. Deleting a row here would change a balance; deleting a
    row there would only lose the explanation of one.

    **Never updated and never deleted.** A correction is a new row of the opposite
    sign, which is why ``REVERSAL`` exists as its own type rather than as a ``DEBIT``
    edited to zero.
    """

    EntryType = ServiceLedgerEntryType

    # ------------------------------------------------------------------- target
    #
    # An entry moves the balance of **exactly one** of two things: a competency period
    # of a contract, or a work item allowance (Phase 5). The three columns below are
    # nullable for that reason and kept coherent by
    # `service_ledger_entry_has_exactly_one_target` in DDL.
    #
    # **The nullability is paid for, not merely accepted.** The constraint makes the
    # table *more* restricted than it was when `contract` and `period` were both
    # mandatory: it also forbids an entry whose `contract` disagrees with its period's
    # own contract, which was representable before. What it buys is that Phase 5 reuses
    # this journal instead of opening a second one, which is what makes a double debit
    # impossible at the database level -- see the note on the unique index below.

    contract = models.ForeignKey(
        "db.ServiceContract",
        on_delete=models.DO_NOTHING,
        related_name="ledger_entries",
        null=True,
        blank=True,
    )

    # The period whose balance this entry moves. Null on an allowance entry.
    period = models.ForeignKey(
        "db.ServiceContractPeriod",
        on_delete=models.DO_NOTHING,
        related_name="ledger_entries",
        null=True,
        blank=True,
    )

    # The work item allowance whose balance this entry moves. Null on a period entry.
    #
    # DO_NOTHING, like every other billing foreign key here. Not CASCADE: an allowance
    # is soft deleted when its work item is, and `soft_delete_related_objects` funnels
    # CASCADE into soft deleting the related rows -- which would soft delete ledger
    # entries, and this table is append-only. Not SET_NULL either, for the same reason
    # spelled out on `service_log`: nulling it would strand the entry with no target and
    # break the constraint above.
    allowance = models.ForeignKey(
        "db.ServiceIssueAllowance",
        on_delete=models.DO_NOTHING,
        related_name="ledger_entries",
        null=True,
        blank=True,
    )

    # Which competency the hours in this parcel originally came from, which is what
    # makes carryover validity enforceable (criterion 6). A `GRANT` points at its own
    # period; a `CARRY_IN` points back at wherever the parcel started, however many
    # months ago.
    #
    # Null on the entry types that do not belong to a single parcel: `DEBIT`,
    # `REVERSAL` and `OVERAGE_BILLED`. A debit is deliberately **not** allocated to a
    # parcel at debit time -- see the note on the unique index below.
    #
    # It also carries **provenance across the two kinds of target**: when a contract's
    # remaining balance is converted into a work item allowance (Phase 4's criterion 17,
    # closed in Phase 5), the `CREDIT` row lands on the allowance with `period` null and
    # `origin_period` pointing at the competency the hours came from. So "these 12h came
    # from the 2026-03 competency of contract X" stays queryable without a new column
    # and without a new entry type.
    origin_period = models.ForeignKey(
        "db.ServiceContractPeriod",
        on_delete=models.DO_NOTHING,
        related_name="originated_ledger_entries",
        null=True,
        blank=True,
    )

    # Signed, per the convention documented on `ServiceLedgerEntryType`. Positive adds
    # to the period's balance, negative removes from it. Scale per section 4b.
    hours = models.DecimalField(max_digits=10, decimal_places=4)

    entry_type = models.CharField(max_length=32, choices=ServiceLedgerEntryType.choices)

    # The work log that caused a `DEBIT` or a `REVERSAL`, and the key that makes both
    # idempotent.
    #
    # DO_NOTHING, and SET_NULL WOULD BE A BUG -- worth spelling out, because SET_NULL
    # is the obvious-looking choice. `soft_delete_related_objects` has a branch that
    # nulls SET_NULL columns on the related rows. A work log is soft deleted on every
    # ordinary edit (`replace_service_log_batch` deletes and recreates), so SET_NULL
    # would clear this link exactly when `reverse_debit` needs it to find the debit --
    # and a second delete would then reverse a second time and credit hours that never
    # existed.
    #
    # Nullable for one narrow reason, and it is not ordinary deletion: the nightly
    # `hard_delete` task purges work items after a retention window and hard-cascades
    # to their work logs. `plane.bgtasks.deletion_task.hard_delete` detaches these
    # entries first, because at that point the work log is gone forever, idempotency is
    # moot, and the alternative is an IntegrityError that stops the whole purge. The
    # `hours` stay, so reconciliation is unaffected.
    service_log = models.ForeignKey(
        "db.ServiceLog",
        on_delete=models.DO_NOTHING,
        related_name="ledger_entries",
        null=True,
        blank=True,
    )

    # Who caused the movement. SET_NULL and nullable: an automated close or a
    # reconciliation repair has no user, and a system movement is a legitimate kind of
    # movement. `notes` carries the explanation in that case.
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="service_hour_ledger_entries",
        null=True,
        blank=True,
    )

    notes = models.TextField(blank=True)

    # -------------------------------------------------------- the money (Phase 6)

    # **The money lives on the entry that moved it, and there is no second journal.**
    # Decision D29's test applied to reais: of the three monetary events in the pricing
    # phase, one is not a pool movement at all (a priced work log, whose journal is the
    # `service_logs` table itself) and the other two -- a contract period's overage and
    # an allowance's overage -- are *already rows in this table*, carrying only the
    # hours. Opening a parallel monetary table would record the reais of an event
    # somewhere other than the event, and section 6 of the master context forbids the
    # second audit trail that would result.
    #
    # Both null on every entry type that moves only hours, which is all of them but
    # `OVERAGE_BILLED`. The check constraint below holds that line, so a future entry
    # type has to relax it deliberately rather than inherit money by accident.

    # The value billed by this entry, in reais. Monetary scale (12, 2) per section 4b.
    amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    # The rate that produced it -- an R4 snapshot for the same reason
    # `ServiceLog.applied_hour_rate` is one: the contract's overage rate and the client's
    # price sheet both change, and a closed period's invoice must not move when they do.
    applied_hour_rate = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    class Meta:
        verbose_name = "Service Hour Ledger Entry"
        verbose_name_plural = "Service Hour Ledger Entries"
        db_table = "service_hour_ledger_entries"
        ordering = ("period", "created_at")

        constraints = [
            # IDEMPOTENCY, IN THE DATABASE. One debit and one reversal per work log,
            # guaranteed by an index rather than by an `if` that a concurrent request
            # can race past.
            #
            # **`REVERSAL` is covered as well as `DEBIT`, and that is not symmetry for
            # its own sake.** The deletion cascade runs in a Celery task, and Celery
            # retries tasks. Without this, a retry reverses twice and credits hours
            # that never existed -- the exact mirror of the double debit the index
            # exists to prevent.
            #
            # **No `deleted_at` clause, deliberately.** The house pattern pairs a
            # partial index with `deleted_at__isnull=True`, but that pattern is for
            # rows that get soft deleted, and these never do -- the table is
            # append-only. Adding the clause would *weaken* the guarantee: a soft
            # deleted DEBIT would let a second one through.
            #
            # An edit survives this index because `replace_service_log_batch` creates
            # rows with new primary keys, so the new DEBIT points at a different work
            # log than the old one. Verified against that function's docstring.
            # **This index is also what holds Phase 5's acceptance criterion 7**, and
            # that is the reason the allowance reuses this table instead of getting a
            # journal of its own. "Estourar a bolsa não debita do contrato em nenhuma
            # circunstância" and "sem débito parcial nos dois" would otherwise be an
            # ordering property of an `if` in `apply_debit`. Here both rows would be a
            # `DEBIT` of the same work log, so the **database** refuses the second one --
            # a state made unrepresentable rather than merely validated against.
            models.UniqueConstraint(
                fields=["service_log", "entry_type"],
                condition=Q(entry_type__in=SERVICE_LOG_LEDGER_ENTRY_TYPES, service_log__isnull=False),
                name="service_ledger_unique_debit_reversal_per_service_log",
            ),
            # EXACTLY ONE TARGET, in DDL. An entry moves a competency period's balance or
            # a work item allowance's balance, never both and never neither.
            #
            # This is what pays for `contract` and `period` becoming nullable, and it
            # leaves the table stricter than it was: "a period entry whose `contract`
            # points somewhere other than that period's contract" was representable
            # before and is not now.
            models.CheckConstraint(
                condition=(
                    Q(period__isnull=False, contract__isnull=False, allowance__isnull=True)
                    | Q(allowance__isnull=False, period__isnull=True, contract__isnull=True)
                ),
                name="service_ledger_entry_has_exactly_one_target",
            ),
            # Signs match meaning, in DDL. An `EXPIRED_BY_CAP` of +5 would be a credit
            # wearing a write-off's name, and it would reconcile perfectly.
            models.CheckConstraint(
                condition=(
                    Q(entry_type__in=NEGATIVE_ONLY_LEDGER_ENTRY_TYPES, hours__lte=0)
                    | Q(entry_type__in=POSITIVE_ONLY_LEDGER_ENTRY_TYPES, hours__gte=0)
                    | Q(entry_type__in=SIGNED_LEDGER_ENTRY_TYPES)
                ),
                name="service_ledger_hours_sign_matches_entry_type",
            ),
            # ---------------------------------------------------- the money, Phase 6
            #
            # **ACCEPTANCE CRITERION 9, IN DDL: "faturar excedente duas vezes no mesmo
            # período é rejeitado".**
            #
            # It was an `if` until this phase -- `close_period` raises
            # `PERIOD_ALREADY_CLOSED` -- and the index above does not help, because it is
            # conditioned on `service_log__isnull=False` and an `OVERAGE_BILLED` row has no
            # work log. So two overage billings for one period were *representable*, and
            # only the ordering of a Python guard stood between the client and a duplicate
            # invoice line. Once the row carries reais that is no longer good enough, for
            # the same reason D29 gave: a money invariant held by a check is a money
            # invariant a concurrent request can race past.
            #
            # Two constraints rather than one because the target is an XOR of two nullable
            # columns; a single index over both would treat the nulls as distinct and
            # enforce nothing.
            models.UniqueConstraint(
                fields=["period"],
                condition=Q(entry_type=ServiceLedgerEntryType.OVERAGE_BILLED, period__isnull=False),
                name="service_ledger_unique_overage_billed_per_period",
            ),
            models.UniqueConstraint(
                fields=["allowance"],
                condition=Q(entry_type=ServiceLedgerEntryType.OVERAGE_BILLED, allowance__isnull=False),
                name="service_ledger_unique_overage_billed_per_allowance",
            ),
            # Money appears only on the entry type that bills it, and a value never
            # travels without the rate that produced it.
            #
            # **One direction only, and the asymmetry is an accepted limitation rather than
            # an oversight.** The natural biconditional -- `OVERAGE_BILLED` implies an
            # amount -- cannot be added: rows written before this phase are real history
            # with no rate and no amount, and inventing values to satisfy a constraint is
            # precisely what the migration conventions forbid. So the strong invariant
            # ("billing overage requires a resolvable rate") lives in
            # `plane.utils.service_pool._settle_deficit`, which refuses with
            # `OVERAGE_RATE_NOT_CONFIGURED`, and in a test that pins it.
            models.CheckConstraint(
                condition=(
                    Q(amount__isnull=True, applied_hour_rate__isnull=True)
                    | Q(
                        amount__isnull=False,
                        applied_hour_rate__isnull=False,
                        amount__gte=0,
                        applied_hour_rate__gt=0,
                        entry_type=ServiceLedgerEntryType.OVERAGE_BILLED,
                    )
                ),
                name="service_ledger_amount_only_on_overage_billed",
            ),
        ]

        indexes = [
            # Reconciliation and the period statement read a period's entries.
            models.Index(fields=["period", "entry_type"], name="svc_ledger_period_type_idx"),
            # FIFO parcel resolution reads the parcels of a period by origin.
            models.Index(fields=["period", "origin_period"], name="svc_ledger_period_origin_idx"),
            # "What did this work log do to the pool", for the work item panel.
            models.Index(fields=["service_log"], name="svc_ledger_service_log_idx"),
            models.Index(fields=["contract", "created_at"], name="svc_ledger_contract_idx"),
            # The allowance counterpart of the first two: reconciliation and the credit
            # history read one allowance's entries.
            models.Index(fields=["allowance", "entry_type"], name="svc_ledger_allowance_idx"),
        ]

    def __str__(self):
        return f"{self.entry_type} {self.hours} @ {self.period_id}"


class ServiceAlertDismissal(WorkspaceBaseModel):
    """An alert an admin has acknowledged, so it stops being noise.

    Section 9 requires alerts to be dismissible "para não virar ruído". The naive shape
    -- unique on ``(period, alert_code)`` and nothing else -- has a hole that decision B2
    closes: dismissing the negative-balance alert at -5h would silence it at -200h too.
    An alert that fires once, on the cheapest day, is not a guardrail.

    So the **balance at the moment of dismissal is recorded**, and the alert re-fires
    when things get worse by more than one band. The band is
    ``plane.utils.service_pool_alerts.ALERT_REARM_BAND_HOURS``.

    **The target is a period or an allowance, exactly one of the two (D54).** Phase 5
    left allowance alerts undismissable and named it as debt owed by Phase 9, because
    ``period`` was mandatory. The fix follows **D29 literally**: a nullable pair with the
    exclusivity in DDL, which is the shape ``ServiceHourLedgerEntry`` already uses for the
    same problem a few classes up in this very module. Inventing a sibling table now
    would be two solutions to one problem.

    What makes the pair cheap here is that ``balance_at_dismissal`` was already generic:
    ``ServiceContractPeriod`` and ``ServiceIssueAllowance`` **both** expose
    ``balance_hours``, so the re-arm comparison in ``is_alert_dismissed`` works for either
    target through one code path, with no branch on type.

    Renamed from ``ServiceContractAlertDismissal``, keeping the table name: the table now
    holds rows with no contract anywhere in them, and a class saying "Contract" would send
    the next reader looking for a foreign key that is not there.

    **The accepted limitation, registered rather than hidden:** there is still no
    ceiling on the deficit itself. Nothing stops a period reaching -200h; the system
    only guarantees somebody was told repeatedly on the way there. A hard ceiling would
    contradict D4 and section 4, which forbid blocking work that was already performed.
    A characterisation test fixes this behaviour so that a future phase changes it
    deliberately.
    """

    # Nullable half of the D54 pair. Exactly one of `period` and `allowance` is set,
    # enforced by `service_alert_dismissal_has_exactly_one_target` below rather than by
    # application code, for the same reason D29 gave for the ledger: a rule about which
    # columns may coexist is a rule the database can keep, and one it keeps against every
    # writer including a shell session.
    period = models.ForeignKey(
        "db.ServiceContractPeriod",
        on_delete=models.DO_NOTHING,
        related_name="alert_dismissals",
        null=True,
        blank=True,
    )

    allowance = models.ForeignKey(
        "db.ServiceIssueAllowance",
        on_delete=models.DO_NOTHING,
        related_name="alert_dismissals",
        null=True,
        blank=True,
    )

    # The UPPER_SNAKE code from `plane.utils.service_pool_alerts`. A plain string, not
    # choices, for the same reason `ServiceConfigActivity.entity_name` is: the alert
    # catalogue grows with the dashboard phase, and choices would force a migration
    # per addition for no database benefit.
    alert_code = models.CharField(max_length=64)

    # Decision B2. The balance when the dismissal was made, which is what re-arming
    # compares against. Signed, and usually negative -- these are mostly deficit
    # alerts.
    balance_at_dismissal = models.DecimalField(max_digits=10, decimal_places=4)

    dismissed_at = models.DateTimeField(auto_now_add=True)

    dismissed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="service_contract_alert_dismissals",
        null=True,
        blank=True,
    )

    class Meta:
        verbose_name = "Service Alert Dismissal"
        verbose_name_plural = "Service Alert Dismissals"
        # Unchanged across the rename. Renaming the table would buy nothing and would
        # turn a state-only migration into one that rewrites a table.
        db_table = "service_contract_alert_dismissals"
        ordering = ("-dismissed_at",)

        unique_together = [["period", "allowance", "alert_code", "deleted_at"]]

        constraints = [
            # Two partial uniques rather than one, and the `__isnull=False` halves matter.
            # In Postgres NULLs are distinct inside a unique index, so a single unique on
            # (period, alert_code) would silently stop protecting anything the moment
            # `period` became nullable: every allowance dismissal has period NULL, so they
            # would all be mutually distinct regardless of alert_code. The condition makes
            # each index cover only the rows that target its own entity.
            models.UniqueConstraint(
                fields=["period", "alert_code"],
                condition=Q(deleted_at__isnull=True, period__isnull=False),
                name="service_alert_dismissal_unique_per_period_when_deleted_at_null",
            ),
            models.UniqueConstraint(
                fields=["allowance", "alert_code"],
                condition=Q(deleted_at__isnull=True, allowance__isnull=False),
                name="service_alert_dismissal_unique_per_allowance_when_deleted_at_null",
            ),
            # D54, mirroring `service_ledger_entry_has_exactly_one_target` from D29.
            # Exactly one target: never both, and never neither. "Never neither" is the
            # half that is easy to forget and the one that would let an orphan dismissal
            # silence nothing while looking like a record of somebody's decision.
            models.CheckConstraint(
                condition=(
                    Q(period__isnull=False, allowance__isnull=True)
                    | Q(period__isnull=True, allowance__isnull=False)
                ),
                name="service_alert_dismissal_has_exactly_one_target",
            ),
        ]

        indexes = [
            models.Index(fields=["period", "alert_code"], name="svc_alert_dismissal_idx"),
            models.Index(
                fields=["allowance", "alert_code"], name="svc_alert_dismissal_allow_idx"
            ),
        ]

    def __str__(self):
        return f"{self.alert_code} @ {self.period_id}"
