# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
from decimal import Decimal

# Django imports
from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.db.models import Q

# Module imports
from .project import ProjectBaseModel
from .service_catalog import ServiceBillingType


class ServiceLogSource(models.TextChoices):
    """How the record got here. Decision D12.

    No import tooling exists yet and none is planned for this phase. The column is
    here because it is nearly free now and expensive later: once a spreadsheet-era
    backlog is loaded, telling migrated rows from ones a technician typed is the
    difference between a defensible audit and a guess.

    Declared at module level, not nested in ``ServiceLog``, because the check
    constraints in ``Meta`` need to reference these values and a class nested in the
    model is not yet in scope while ``Meta`` is being evaluated. Reachable as
    ``ServiceLog.Source`` for callers, which keeps the house style of the catalogues.
    """

    MANUAL = "manual", "Entered by hand"
    IMPORTED = "imported", "Migrated from another system"
    API = "api", "Created through the API"


class ServiceLogEntryMode(models.TextChoices):
    """How the time was expressed. Rule R9.

    The mode is persisted rather than inferred from whether the times are set,
    because it changes what the record *means*: only interval mode establishes when
    the work happened, and therefore only interval mode may be split into segments
    (D13). A later reclassification has to be able to tell the two apart even for a
    single-segment entry.
    """

    DURATION = "duration", "Free text duration"
    INTERVAL = "interval", "Start and end time"


class ServiceRateBasis(models.TextChoices):
    """Which of the two pricing formulas produced ``ServiceLog.amount``.

    Persisted rather than inferred, because **the two formulas multiply different hour
    quantities** and the choice is not recoverable from the numbers: with a multiplier of
    1.00 both produce the same product, so dividing the amount back out is ambiguous.

    ``BASE_MULTIPLIER`` multiplies ``equivalent_hours`` by the client's base rate -- the
    multiplier has already entered through the hours. ``ABSOLUTE_OVERRIDE`` multiplies
    ``logged_hours`` by the hour type's absolute rate, because an absolute rate
    *replaces* ``base * multiplier`` and therefore already embeds the multiplier.
    Applying it to equivalent hours would double-charge, which is acceptance criterion
    8's bug wearing acceptance criterion 5's clothes. See
    ``plane.utils.service_money``.
    """

    BASE_MULTIPLIER = "base_multiplier", "Client base rate times the hour type multiplier"
    ABSOLUTE_OVERRIDE = "absolute_override", "Absolute rate registered for the hour type"


class ServiceRouteDeviation(models.TextChoices):
    """Why a work log that chose the contract pool was billed in reais instead.

    Decision D33: a work log whose billing type routes to the pool, on a client with no
    contract, a suspended contract or an expired one, is **billed as avulso** rather
    than silently consuming nothing. The chosen route and the settled route must both be
    recoverable, plus the reason -- so ``applied_billing_route`` keeps the choice,
    ``settled_billing_route`` records what was done, and this says why they differ.

    All four values mean **commercial pendency with a deadline**, which is what
    distinguishes them from a client who is avulso by design. Section 5 of the phase
    brief requires the consolidation to show that difference, because one is a business
    model and the other is somebody's overdue renewal.

    ``CONTRACT_ENDED`` and ``CONTRACT_EXPIRED`` are separate on purpose: the first is an
    explicit administrative act (``status = ENDED``), the second is a contract nobody
    renewed in time. Both are avulso now, but only the second is somebody forgetting.
    """

    NO_CONTRACT_FOR_CLIENT = "no_contract_for_client", "The client has no contract"
    CONTRACT_SUSPENDED = "contract_suspended", "The contract is suspended"
    CONTRACT_ENDED = "contract_ended", "The contract was ended"
    CONTRACT_EXPIRED = "contract_expired", "The work date is past the contract vigency"


class ServicePricingFailure(models.TextChoices):
    """Why a work log that should carry money carries zero.

    **R$ 0,00 is true for five different reasons, and they are five different bugs.**
    Two of them need no column -- a ``NON_BILLABLE`` route is stated by
    ``applied_billing_route``, and a pool debit is stated by ``debited_period`` or
    ``debited_allowance``. The three here are the ones nothing else records, and the
    house rule that every absence asserts its reason code is what makes them columns
    instead of a shrug.

    They fall into **two classes that a financial panel must not mix**, and the split is
    exposed as ``PRICING_PENDENCY_FAILURES`` / ``INTERNAL_WORK_FAILURES`` below:

    * **Registration pendency** -- ``NO_PRICE_SHEET_FOR_CLIENT`` and
      ``NO_PRICE_SHEET_IN_FORCE``. Real money that cannot be invoiced yet. Someone must
      act. These two are themselves opposite mistakes: never registered, versus
      registered with a start date later than the work.
    * **Internal work** -- ``INTERNAL_PROJECT_NO_CLIENT``. Section 1 of Phase 1 is
      explicit that a project with no client is internal work, "não apontável para
      faturamento". **Nobody must act**, so the consolidation excludes it from revenue
      rather than listing it as a zeroed pendency. A company that does real internal
      work would otherwise face a panel full of "failures" that are not failures, and
      the operator would learn to ignore the list -- which is how the one genuine
      pendency goes unnoticed. Noise in a financial panel costs the panel's credibility.

    None of the three blocks the work log. It is work already executed, and D27, D9 and
    D21 say the same thing three times: absence of configuration must never cost a
    technician their record.
    """

    INTERNAL_PROJECT_NO_CLIENT = "internal_project_no_client", "Internal project, no client to invoice"
    NO_PRICE_SHEET_FOR_CLIENT = "no_price_sheet_for_client", "The client has no price sheet at all"
    NO_PRICE_SHEET_IN_FORCE = "no_price_sheet_in_force", "No price sheet was in force on the service date"


# The two classes of pricing failure, at module level so the constraints in ``Meta`` and
# the consolidation can both reference them and cannot drift apart. See
# ``ServicePricingFailure``: the first class is money waiting on somebody, the second is
# work that is nobody's revenue.
PRICING_PENDENCY_FAILURES = [
    ServicePricingFailure.NO_PRICE_SHEET_FOR_CLIENT,
    ServicePricingFailure.NO_PRICE_SHEET_IN_FORCE,
]

INTERNAL_WORK_FAILURES = [ServicePricingFailure.INTERNAL_PROJECT_NO_CLIENT]


class ServiceLog(ProjectBaseModel):
    """Time a technician spent on a work item, and what it costs the client.

    Named ``ServiceLog`` rather than ``Worklog``, and deliberately so. Plane's
    commercial edition ships a Time Tracking feature that owns the ``Worklog``
    vocabulary -- this fork already carries its unused hooks, the
    ``Project.is_time_tracking_enabled`` flag and the ``issue_worklogs`` export
    choice. Sharing the name would collide on table name, route and meaning if this
    fork is ever merged with upstream or migrated to the Commercial Edition. Same
    reasoning as ``ServiceClient`` and the ``Service*`` catalogues. See section 6 of
    the master context.

    Inherited from ``ProjectBaseModel``: ``project`` (required) and ``workspace``,
    which ``save()`` denormalises from the project. There is no client foreign key
    here on purpose -- the client of a work log is derived through
    ``project.service_client``, never stored, per DECISOES.md D19.

    **The four time quantities are four separate columns** (section 4 of the master
    context), and conflating them is the most likely error in this feature:

    ==========================  ====================================================
    ``raw_duration_minutes``    what the technician entered, whole minutes
    ``logged_hours``            after R2 rounding -- the official chronological time
    ``equivalent_hours``        after the multiplier -- the only one the client sees
    ``debited_hours``           equivalent, or 0 when the route does not charge
    ==========================  ====================================================

    **There is no ``garantia`` flag** (DECISOES.md D20). "Do not charge" has exactly
    one mechanism: a billing type whose route is ``NON_BILLABLE``. Garantia and
    Cortesia are two such types, kept distinct because a month heavy with garantia is
    a delivery quality problem while a month heavy with cortesia is a discount that
    was chosen -- opposite management actions, so reports must never sum them.

    **The money is Phase 6**, and it lives in six columns that are all R4 obligations
    rather than conveniences: ``settled_billing_route`` and ``route_deviation_reason``
    (what was actually done with the row, and why it differs from what was chosen --
    decision D33), ``applied_hour_rate``, ``applied_rate_basis`` and ``amount`` (the
    snapshot that makes acceptance criterion 6 true), and ``pricing_failure_reason``
    (why a row that should carry money carries zero). Two check constraints make
    "debited a pool" and "billed in reais" mutually exclusive states of one row, so
    double-charging is refused by the database rather than by the order of an ``if``.
    """

    # Aliases so callers read ``ServiceLog.Source`` / ``ServiceLog.EntryMode``, the
    # same shape as ``ServiceBillingType.BillingRoute``. The definitions are at module
    # level because ``Meta`` below needs them; see the note on ``ServiceLogSource``.
    Source = ServiceLogSource
    EntryMode = ServiceLogEntryMode
    RateBasis = ServiceRateBasis
    RouteDeviation = ServiceRouteDeviation
    PricingFailure = ServicePricingFailure

    # ---------------------------------------------------------------- relations

    # CASCADE, and the only foreign key on this model that is not DO_NOTHING.
    #
    # Read the comment on `hour_type` before changing either: the catalogue links
    # must not cascade, this one must. A work log has no meaning apart from the work
    # item it describes, so it belongs in the bin alongside it -- and because
    # `soft_delete_related_objects` handles CASCADE by soft deleting, the rows
    # survive in `all_objects` for any billing period that already counted them.
    issue = models.ForeignKey("db.Issue", on_delete=models.CASCADE, related_name="service_logs")

    # Who did the work, which is not necessarily who typed it in -- rule R8 requires
    # both, and Phase 7 adds delegation so a coordinator can log on a technician's
    # behalf. The other half of the pair is `created_by`, inherited from
    # UserAuditModel.
    #
    # DO_NOTHING rather than the SET_NULL that `IssueActivity.actor` and `created_by`
    # use. This is a deliberate departure: those are traces, this is a billing
    # record, and a work log whose author has been nulled cannot be defended in an
    # invoice dispute. `User` is not a SoftDeleteModel, so `soft_delete_related_objects`
    # never runs for it and the hazard described on `hour_type` does not apply here;
    # account deletion in this codebase is a deactivation (`is_active = False`), not a
    # row delete. What DO_NOTHING buys is that a genuine hard delete of a user who
    # logged billable hours raises IntegrityError instead of quietly orphaning it.
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.DO_NOTHING,
        related_name="service_logs",
    )

    # DO_NOTHING on both catalogue links. This is a MODELLING CONSTRAINT, not a
    # preference, and `PROTECT` is the trap:
    #
    #   * `plane.bgtasks.deletion_task.soft_delete_related_objects` walks reverse
    #     relations and branches on the on_delete name. DO_NOTHING is skipped,
    #     SET_NULL nulls the column, and *everything else falls into one catch-all
    #     branch that soft deletes the related rows*. PROTECT has no branch of its
    #     own. So the one Django option whose entire purpose is to refuse a delete
    #     is, in this codebase, a silent data-loss path: an admin tidying the
    #     catalogue would soft delete work logs. Verified in deletion_task.py.
    #   * SET_NULL would strip the historical label, which makes Phase 2's
    #     acceptance criterion 3 -- "apontamentos antigos continuam exibindo o nome
    #     corretamente" -- impossible to satisfy.
    #
    # Because Django still emits the database constraint, a hard delete of a
    # referenced option raises IntegrityError. The delete is refused before that by
    # `plane.utils.service_log.catalog_option_in_use`, wired into
    # `validate_catalog_delete`. Same choice, same reasons, as
    # `Project.service_client` and `ServiceClient.default_billing_type`.
    hour_type = models.ForeignKey(
        "db.ServiceHourType",
        on_delete=models.DO_NOTHING,
        related_name="service_logs",
    )
    billing_type = models.ForeignKey(
        "db.ServiceBillingType",
        on_delete=models.DO_NOTHING,
        related_name="service_logs",
    )

    # What the classification engine proposed, kept beside what was actually chosen.
    #
    # Null means nothing was proposed: no engine has run (it arrives with the
    # calendar and windows phase), or the date fell on a weekday in duration mode
    # where R10 says the technician must choose. Storing the suggestion is what makes
    # "sobrescrita registrada na auditoria quando divergir" answerable months later
    # rather than only at the moment of the click.
    suggested_hour_type = models.ForeignKey(
        "db.ServiceHourType",
        on_delete=models.DO_NOTHING,
        related_name="suggested_service_logs",
        null=True,
        blank=True,
    )
    is_hour_type_overridden = models.BooleanField(default=False)

    # Why the engine classified this segment the way it did, e.g. "Feriado: Natal" or
    # "Fora do expediente: 18:00-08:00". Snapshotted text, not a rule reference:
    # section 7 of the calendar phase requires the reason to be visible, and R4
    # forbids a later calendar edit from changing what an existing log says. A
    # foreign key to the window would break that the first time an admin edits it.
    classification_reason = models.CharField(max_length=255, blank=True)

    # ------------------------------------------------------------------ what and when

    # The date the work happened, which drives billing competency (R7): a
    # backdated entry debits the backdated month, not the month it was typed in.
    #
    # A DateField holding a local calendar date, paired with the two local wall clock
    # TimeFields below -- deliberately not a UTC datetime. "The whole day" for a
    # holiday and the continuous 18:00->08:00 window are only well defined against a
    # local calendar, and R10's windows are a commercial parameter of the workspace.
    # Storing instants would force a timezone conversion on every read and let the
    # same worked hour classify differently. The full timezone strategy is section 5b
    # of the calendar and windows phase; what this phase fixes is only that these are
    # local values, and that "is this date in the future" resolves against the
    # workspace timezone rather than the requesting user's.
    #
    # In a split entry each segment carries its own date, derived from its real
    # position on the timeline -- required by R7, because 31/01 23:00 to 01/02 01:00
    # straddles two competencies.
    worked_on = models.DateField()

    description = models.TextField()

    entry_mode = models.CharField(
        max_length=20, choices=ServiceLogEntryMode.choices, default=ServiceLogEntryMode.DURATION
    )

    # Null in duration mode, both set in interval mode -- enforced by a check
    # constraint below. Local wall clock, per the note on `worked_on`. `end_time`
    # earlier than `start_time` means the work crossed midnight.
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)

    source = models.CharField(max_length=20, choices=ServiceLogSource.choices, default=ServiceLogSource.MANUAL)

    # ------------------------------------------------- the four time quantities

    # Quantity 1: exactly what was entered, before R2 touched it. Kept so that a
    # technician can be shown what they typed, and so a rounding complaint can be
    # audited without reversing the arithmetic.
    raw_duration_minutes = models.IntegerField(validators=[MinValueValidator(1)])

    # Quantities 2, 3 and 4. All three at four decimal places, fixed by section 4b of
    # the master context.
    #
    # COUPLING TO PRESERVE -- the same warning as ``ServiceHourType.multiplier``, and
    # this is the other end of it. Four places is the proven minimum, not padding:
    # `logged_hours` is always a multiple of 15 minutes (p/4), the multiplier has two
    # places (m/100), and the product p*m/400 terminates in at most four places
    # because 400 = 2^4 * 5^2. The worst case is reached: 0.25 * 1.01 = 0.2525. If
    # the multiplier ever gains a third decimal place these need five
    # (0.25 * 1.001 = 0.25025), or every equivalent-hour figure silently truncates.
    #
    # `logged_hours` only *needs* two places -- it is exact by construction. It uses
    # four anyway so that every hour column in the system shares one scale and values
    # copied or summed between entities cannot pick up an accidental truncation. The
    # pool columns in Phase 4 follow for the same reason.
    logged_hours = models.DecimalField(max_digits=10, decimal_places=4)
    equivalent_hours = models.DecimalField(max_digits=10, decimal_places=4)
    debited_hours = models.DecimalField(max_digits=10, decimal_places=4)

    # ------------------------------------------------------------- R4 snapshots

    # Rule R4: changing catalogue configuration must never recalculate a work log
    # that already exists. These two columns are what make that true, so nothing here
    # may become a lookup through the foreign keys above.
    applied_multiplier = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal("1.00"))

    # The billing route of the billing type that was **chosen**, as it stood at
    # creation. R4 names only the multiplier, but the route is what decided whether the
    # client was charged at all, and two billing types can share one route -- so without
    # this snapshot a historical report cannot separate Garantia from Cortesia after a
    # catalogue edit, and cannot explain a zero. Added by D20, which removed the boolean
    # that used to answer this.
    #
    # **This is the choice, not the outcome.** Since D33 the two can differ: a log that
    # chose the pool is billed in reais when the client has no usable contract. What was
    # actually done is `settled_billing_route`, and the reason they differ is
    # `route_deviation_reason`. This column was deliberately NOT redefined to mean the
    # outcome -- doing so would erase the choice, which is exactly the fact that lets the
    # consolidation tell "avulso because that is the client's model" from "avulso because
    # a renewal is overdue".
    applied_billing_route = models.CharField(
        max_length=20,
        choices=ServiceBillingType.BillingRoute.choices,
        default=ServiceBillingType.BillingRoute.DEBIT_POOL,
    )

    # The route that was actually **applied**. Equal to `applied_billing_route` except in
    # the D33 case, where a pool route with no usable contract settles as `BILL_AMOUNT`.
    # The biconditional between this, the column above and `route_deviation_reason` is in
    # DDL below, so "deviated with no reason" and "reason with no deviation" are both
    # unrepresentable rather than merely unlikely.
    #
    # An indexed column rather than a derivation, because it is what every revenue query
    # groups by; deriving it would put a CASE in each report and let four reports each
    # have their own version of the rule.
    settled_billing_route = models.CharField(
        max_length=20,
        choices=ServiceBillingType.BillingRoute.choices,
        default=ServiceBillingType.BillingRoute.DEBIT_POOL,
    )

    # Null means the chosen route was honoured. Non-null is D33's commercial pendency,
    # and section 5 of the brief requires it to reach the consolidation: the last two
    # values are somebody's overdue renewal, with a deadline attached.
    route_deviation_reason = models.CharField(
        max_length=32,
        choices=ServiceRouteDeviation.choices,
        null=True,
        blank=True,
    )

    # The competency period this row actually debited. Step 5 of section 5 of the
    # contract phase brief asks for it by name, and it completes R4's snapshot: it is
    # the answer to "which pool paid for this hour", which cannot be re-derived later
    # because the resolution depends on configuration that may since have changed --
    # the project's pinned contract, the client's default, the contract's vigency.
    #
    # Null means no pool was debited, and the reason is always recoverable rather than
    # guessed: `applied_billing_route` says whether the route even debits a pool, and a
    # NON_BILLABLE row has `debited_hours = 0` by check constraint. That distinction
    # matters because "não faturável" and "não existe contrato" produce the same
    # balance and are opposite bugs.
    #
    # There is deliberately NO `applied_contract` column beside it. The period already
    # navigates to its contract, and a second snapshot would be a second truth that
    # could disagree with the first.
    debited_period = models.ForeignKey(
        "db.ServiceContractPeriod",
        on_delete=models.DO_NOTHING,
        related_name="service_logs",
        null=True,
        blank=True,
    )

    # The work item allowance this row debited instead, when rule R6's first level
    # applied. Section 2 of the Phase 5 brief asks for it by name: "o apontamento
    # persiste explicitamente qual origem foi debitada", because without it the Phase 9
    # reports cannot separate project revenue from support consumption.
    #
    # **This and `debited_period` are mutually exclusive, and in DDL** -- see
    # `service_log_debits_at_most_one_origin` below. Two nullable columns that could
    # both be filled would be a state with no meaning, and criterion 7 forbids a partial
    # debit against both origins. The constraint makes that unrepresentable rather than
    # refused by a validation someone forgets to call, which is the reasoning of D5.
    #
    # Both null is legitimate and means one of three things, all recoverable rather than
    # guessed: the route does not debit a pool, the debited hours are zero, or the
    # contract configuration is absent (D27).
    debited_allowance = models.ForeignKey(
        "db.ServiceIssueAllowance",
        on_delete=models.DO_NOTHING,
        related_name="service_logs",
        null=True,
        blank=True,
    )

    # ------------------------------------------------------- the money (Phase 6)

    # Rule R4 again, and acceptance criterion 6 depends entirely on these three columns:
    # "reajustar o valor base do cliente não altera o valor de apontamentos já
    # registrados". Nothing here may become a lookup through `project.service_client`
    # into the price sheets -- the moment it does, last year's invoices start moving.
    #
    # Monetary scale (12, 2) per section 4b, never the hour scale (10, 4).

    # The rate that was applied, in reais per hour. Null means no money was made, and
    # which of the five reasons applies is always recoverable -- see
    # `ServicePricingFailure`.
    applied_hour_rate = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    # Which formula turned that rate into `amount`. Not derivable from the numbers; see
    # `ServiceRateBasis`.
    applied_rate_basis = models.CharField(
        max_length=20,
        choices=ServiceRateBasis.choices,
        null=True,
        blank=True,
    )

    # The money. Acceptance criterion 13 -- "nenhum centavo perdido ou criado" -- is why
    # every total in this system is a `Sum` over **this persisted column** and never a
    # recomputation from hours in an aggregate: section 4b fixes one rounding per work
    # log, and rounding a sum of products is not the same number as summing rounded
    # products.
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    # Why a row that should carry money carries zero. Null when there is nothing to
    # explain. Two classes, and the consolidation treats them oppositely -- see
    # `ServicePricingFailure`.
    pricing_failure_reason = models.CharField(
        max_length=32,
        choices=ServicePricingFailure.choices,
        null=True,
        blank=True,
    )

    # ------------------------------------------------------------------- batching

    # Groups the segments that one submission produced (R10, and section 3 of the
    # phase brief). An interval crossing 18:00 on a weekday becomes two rows -- 1h
    # commercial plus 2h after hours -- which have to be editable and deletable
    # together and transactionally.
    #
    # Always populated, so a single entry is a batch of one. That removes a null
    # branch from every query and leaves one code path for "delete the whole entry"
    # instead of two. A bare UUID rather than its own table because nothing needs to
    # hang off the group: the submission's own values are recoverable from the
    # segments, and a table would add a join to every read for no invariant.
    batch_id = models.UUIDField(db_index=True)

    # Position on the timeline within the batch, so the preview and the list render
    # segments in the order the work actually happened rather than by insertion.
    segment_index = models.PositiveSmallIntegerField(default=0)

    # Import provenance, following the convention used across plane.db.
    external_source = models.CharField(max_length=255, null=True, blank=True)
    external_id = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        verbose_name = "Service Log"
        verbose_name_plural = "Service Logs"
        db_table = "service_logs"

        # Section 6 of the phase brief: most recent service date first. created_at
        # breaks ties so that two entries on the same date keep a stable order, and
        # so the segments of one batch do not interleave with unrelated rows.
        ordering = ("-worked_on", "-created_at")

        # Uniqueness under soft delete follows the house pattern: unique_together
        # including deleted_at, plus a conditional UniqueConstraint for live rows.
        unique_together = [["batch_id", "segment_index", "deleted_at"]]

        constraints = [
            models.UniqueConstraint(
                fields=["batch_id", "segment_index"],
                condition=Q(deleted_at__isnull=True),
                name="service_log_unique_batch_segment_when_deleted_at_null",
            ),
            # The three CheckConstraints below are the first in this repository.
            # They are here because each encodes a rule that decides what a client is
            # charged, and a rule held only in Python is a rule that the next
            # queryset `.update()` bypasses -- which is exactly how Phase 1's
            # criterion 11 came to have a second, unguarded write path.
            models.CheckConstraint(
                condition=Q(raw_duration_minutes__gte=1),
                name="service_log_raw_duration_is_positive",
            ),
            # Rule R9, in DDL: duration mode has no times, interval mode has both.
            # A half-filled interval would make the entry unclassifiable and
            # unreproducible.
            models.CheckConstraint(
                condition=(
                    Q(entry_mode=ServiceLogEntryMode.DURATION, start_time__isnull=True, end_time__isnull=True)
                    | Q(entry_mode=ServiceLogEntryMode.INTERVAL, start_time__isnull=False, end_time__isnull=False)
                ),
                name="service_log_entry_mode_matches_times",
            ),
            # Rules R5, R11(a) and quantity 4 of section 4, in DDL. This is the most
            # important constraint on the model: it makes "a non-billable entry
            # charges nothing, and a billable one charges exactly its equivalent
            # hours" impossible to violate from any code path, including a future
            # bulk update or a data migration.
            #
            # Written as a biconditional on purpose. The weaker form -- non-billable
            # implies zero -- would still allow a billable row to be silently
            # under-debited, which is the same class of error pointing the other way.
            models.CheckConstraint(
                condition=(
                    Q(applied_billing_route=ServiceBillingType.BillingRoute.NON_BILLABLE, debited_hours=0)
                    | (
                        ~Q(applied_billing_route=ServiceBillingType.BillingRoute.NON_BILLABLE)
                        & Q(debited_hours=models.F("equivalent_hours"))
                    )
                ),
                name="service_log_debited_hours_follows_billing_route",
            ),
            # Rule R6, in DDL: "nunca debitar de dois lugares". The allowance and the
            # competency period are the two origins, and a row may name at most one.
            #
            # Both null stays legal, and has to: it is what a non-billable row, a
            # zero-hour row, and a row with no contract configured (D27) all look like.
            # What this forbids is the one combination with no meaning.
            models.CheckConstraint(
                condition=~Q(debited_period__isnull=False, debited_allowance__isnull=False),
                name="service_log_debits_at_most_one_origin",
            ),
            # Rule R5 again, from the other side, and Phase 5's acceptance criterion 9:
            # a non-billable entry consumes **no** origin -- not a contract pool and not
            # a work item allowance.
            #
            # The constraint above it already guarantees `debited_hours = 0` on a
            # non-billable row, so a debit engine that read the column would move nothing.
            # This one closes the remaining gap: a code path that stamped the origin
            # without moving hours would leave the row *claiming* an allowance paid for
            # it, which is what a Phase 9 report would then bill against. One direction
            # only, deliberately -- a `DEBIT_POOL` row with no origin is the ordinary
            # D27 case and must stay representable.
            models.CheckConstraint(
                condition=(
                    ~Q(applied_billing_route=ServiceBillingType.BillingRoute.NON_BILLABLE)
                    | Q(debited_period__isnull=True, debited_allowance__isnull=True)
                ),
                name="service_log_non_billable_debits_no_origin",
            ),
            # ---------------------------------------------------- the money, Phase 6
            #
            # A WORK LOG DEBITS A POOL OR IS BILLED IN REAIS. NEVER BOTH.
            #
            # This is the monetary counterpart of what D29 achieved for hours: charging
            # the client in reais *and* consuming their contracted hours for the same work
            # log is refused by the database, not by the order of an `if`. It mirrors
            # `service_log_debits_at_most_one_origin` directly, and it is the reason the
            # pricing step can be written without a "did something already pay for this"
            # pre-flight check that two concurrent requests would both pass.
            models.CheckConstraint(
                condition=(
                    ~Q(settled_billing_route=ServiceBillingType.BillingRoute.BILL_AMOUNT)
                    | Q(debited_period__isnull=True, debited_allowance__isnull=True)
                ),
                name="service_log_billed_debits_no_pool",
            ),
            # The other direction: a row the pool paid for carries no money. Together with
            # the constraint above, "hours were debited" and "reais were charged" become
            # mutually exclusive states of one row rather than two independent columns that
            # a future bulk update could set at once.
            models.CheckConstraint(
                condition=(
                    ~Q(settled_billing_route=ServiceBillingType.BillingRoute.DEBIT_POOL)
                    | Q(amount=0, applied_hour_rate__isnull=True, applied_rate_basis__isnull=True)
                ),
                name="service_log_pool_route_carries_no_amount",
            ),
            # R5 and acceptance criterion 4, for money: "Garantia e Cortesia ... valor R$
            # 0,00". The zero comes out of the route, not out of an `if` that a later code
            # path can forget. A non-billable row also can never deviate -- there is no
            # commercial pendency that turns a warranty repair into billable work.
            models.CheckConstraint(
                condition=(
                    ~Q(applied_billing_route=ServiceBillingType.BillingRoute.NON_BILLABLE)
                    | Q(
                        amount=0,
                        applied_hour_rate__isnull=True,
                        applied_rate_basis__isnull=True,
                        settled_billing_route=ServiceBillingType.BillingRoute.NON_BILLABLE,
                        route_deviation_reason__isnull=True,
                    )
                ),
                name="service_log_non_billable_carries_no_amount",
            ),
            # Decision D33, in DDL, as a biconditional. The only legal deviation is "chose
            # the pool, was billed in reais"; every other pair -- a deviation with no
            # reason, a reason with no deviation, or a deviation from any other route -- is
            # unrepresentable. The weaker one-directional form would let a row claim it was
            # billed for a reason while showing the route it actually honoured.
            models.CheckConstraint(
                condition=(
                    Q(route_deviation_reason__isnull=True, settled_billing_route=models.F("applied_billing_route"))
                    | Q(
                        route_deviation_reason__isnull=False,
                        applied_billing_route=ServiceBillingType.BillingRoute.DEBIT_POOL,
                        settled_billing_route=ServiceBillingType.BillingRoute.BILL_AMOUNT,
                    )
                ),
                name="service_log_route_deviation_is_coherent",
            ),
            # A rate, its basis and a positive amount travel together, or none of them
            # exist. `amount > 0` is only safe to require because `base_hour_rate > 0` and
            # `absolute_rate > 0` are themselves constraints and `equivalent_hours` is
            # always positive -- the same D20 chain that keeps "free" to one mechanism. If
            # a rate of zero were ever allowed this constraint would have to weaken, which
            # is the tell that the two decisions are connected.
            models.CheckConstraint(
                condition=(
                    Q(applied_hour_rate__isnull=True, applied_rate_basis__isnull=True, amount=0)
                    | Q(applied_hour_rate__isnull=False, applied_rate_basis__isnull=False, amount__gt=0)
                ),
                name="service_log_amount_requires_a_rate",
            ),
            # A pricing failure only exists where there was money to make: a billed row
            # with no rate and no amount. This is what keeps the three failure codes from
            # being pasted onto a non-billable row or a pool debit, where they would
            # describe an absence that is not an absence.
            models.CheckConstraint(
                condition=(
                    Q(pricing_failure_reason__isnull=True)
                    | Q(
                        settled_billing_route=ServiceBillingType.BillingRoute.BILL_AMOUNT,
                        applied_hour_rate__isnull=True,
                        amount=0,
                    )
                ),
                name="service_log_pricing_failure_means_no_amount",
            ),
        ]

        indexes = [
            # The work item panel: a project's logs for one issue, newest first.
            models.Index(fields=["issue", "-worked_on"], name="service_log_issue_worked_idx"),
            # Section 7's aggregation, and the competency lookup R7 needs. Also the
            # index Phase 4's monthly pool debit will read.
            models.Index(fields=["project", "worked_on"], name="service_log_project_worked_idx"),
            # Cross-client reporting, which Admins and Members can do because they
            # see every project.
            models.Index(fields=["workspace", "worked_on"], name="service_log_ws_worked_idx"),
            # Hours per technician over a period.
            models.Index(fields=["author", "worked_on"], name="service_log_author_worked_idx"),
            # The billing consolidation of section 5: a workspace's billed rows for one
            # competency month, grouped by what was actually done with them. Name kept to
            # 29 characters because Django's models.E034 caps index names at 30.
            models.Index(
                fields=["workspace", "settled_billing_route", "worked_on"],
                name="svc_log_ws_settled_worked_idx",
            ),
        ]

    def delete(self, using=None, soft=True, *args, **kwargs):
        """Soft delete this work log, reversing its pool debit first.

        **The reversal hangs here, on the model, rather than on the batch helpers, and
        that placement is the whole point.** A work log is deleted through four
        different doors -- ``delete_service_log_batch``, ``replace_service_log_batch``
        (which is an edit), the API's batch delete, and the cascade from a deleted work
        item -- and a reversal wired into any of them would be missing from the others.
        Acceptance criteria 11 and 12 have to hold for all four.

        Same transaction as the soft delete, so a balance is never left debited for a
        row that is already gone. Idempotent twice over: the reversal is skipped when
        the row is already soft deleted, and ``reverse_debit`` is itself guarded by the
        partial unique index on the ledger.

        The import is function level because ``plane.utils.service_pool`` imports these
        models; at module scope the two would import each other at load time. Same
        pattern as ``validate_catalog_delete``'s helpers.
        """
        if soft and self.deleted_at is None:
            from plane.utils.service_pool import reverse_debit

            with transaction.atomic():
                reverse_debit(self)
                return super().delete(using=using, soft=soft, *args, **kwargs)

        return super().delete(using=using, soft=soft, *args, **kwargs)

    def __str__(self):
        return f"{self.issue_id} {self.worked_on} {self.logged_hours}h"
