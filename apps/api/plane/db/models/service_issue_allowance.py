# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
from decimal import Decimal

# Django imports
from django.conf import settings
from django.db import models
from django.db.models import Q

# Module imports
from ..mixins import ChangeTrackerMixin
from .project import ProjectBaseModel
from .service_catalog import ServiceConfigEntity


class ServiceIssueAllowanceStatus(models.TextChoices):
    """Whether an allowance still accepts movement.

    Declared at module level, not nested, because the check constraints in ``Meta``
    reference these values and a class nested in the model is not yet in scope while
    ``Meta`` is evaluated -- the same reason ``ServiceLogSource`` and
    ``ServicePeriodStatus`` are at module level. Reachable as
    ``ServiceIssueAllowance.Status``.

    **CLOSED does not fall back to the contract pool, and that is the whole point.**
    Section 3 of the phase brief forbids the overflow of an allowance reaching the
    client's support pool "nem silenciosamente nem automaticamente"; letting a closed
    allowance route the next work log to the contract would be exactly that, arriving
    by omission instead of by decision. So a work log on a work item whose allowance is
    closed is **refused** (``ALLOWANCE_IS_CLOSED``), in the same way a closed
    competency period refuses one.
    """

    OPEN = "open", "Open"
    CLOSED = "closed", "Closed"


class ServiceIssueAllowance(ChangeTrackerMixin, ProjectBaseModel):
    """A pool of hours credited to one work item, isolated from the client's contract.

    The use case, from section 3 of the master context and the phase brief: a client on
    a 30h/month support contract closes a separate 40h project. The project gets a work
    item, the work item gets 40h of allowance, and the technicians' work logs consume
    that allowance **without touching** the monthly support pool. Rule R6 makes this
    the *first* level of the debit hierarchy, and ``plane.utils.service_pool.apply_debit``
    consults it before it consults a contract.

    **The allowance is inherited down the work item tree**, nearest ancestor first --
    see ``plane.utils.service_allowance.resolve_work_item_allowance``. A 40h project is
    broken into sub-tasks by anyone who actually executes one, and without inheritance
    the parent would hold 40h that nobody logs against while every sub-task quietly
    debited the 30h support pool. That failure is silent and it bills the wrong pool,
    which is why the consequences decided this: no inheritance costs a query, and
    inheritance costs nothing anybody would notice.

    Named ``ServiceIssueAllowance`` rather than anything built on "budget" or
    "worklog": Plane's commercial edition owns the time-tracking vocabulary and this
    fork already carries its unused hooks. Same reasoning as ``ServiceLog`` and
    ``ServiceClient``; see section 6 of the master context.

    Inherited from ``ProjectBaseModel``: ``project`` (required) and ``workspace``,
    which ``save()`` denormalises from the project. That is the base section 6 of the
    master context names for this entity by name.

    **One allowance per work item**, by partial unique index. That is not tidiness --
    it is what makes the D27 question unaskable here. D27 draws the line at *ambiguity
    blocks, absence does not*; with several allowances per work item there would be an
    ambiguous resolution to design a rule for, and with one there is nothing to
    resolve. Additional credits (acceptance criterion 5, the aditivo de escopo) are
    therefore **ledger rows on the same allowance**, never a second allowance, and the
    history of each credit with its author is the ledger.

    **``ChangeTrackerMixin`` tracks exactly one field here, and the exclusions are the
    point.** Phase 5 carried no mixin at all, with the correct reasoning that crediting
    hours is *accounting movement*: it is a ``ServiceHourLedgerEntry`` carrying its own
    ``actor`` and ``created_at``, which is the "autoria e data do crédito" the brief asks
    for. That still holds -- ``credited_hours`` and ``consumed_hours`` are accumulators
    and auditing them here would duplicate the ledger, which section 6 of the master
    context forbids; closing is recorded by ``closed_at`` and ``closed_by`` plus its own
    ledger rows; ``reference`` and ``notes`` are labels, not money.

    Phase 6 added ``overage_hour_rate``, which is **configuration that decides money**
    rather than movement, and D22 requires exactly that to be audited. So the mixin
    arrived for one field, and one only.
    """

    Status = ServiceIssueAllowanceStatus

    # Only the rate. See the class docstring for why every other column on this model is
    # deliberately absent: they are either accumulators the ledger already audits, or
    # labels that never reach an invoice.
    TRACKED_FIELDS = ["overage_hour_rate"]
    CONFIG_ENTITY_NAME = ServiceConfigEntity.ISSUE_ALLOWANCE

    # ---------------------------------------------------------------- relations

    # CASCADE, and the only foreign key on this model that is not DO_NOTHING or
    # SET_NULL. Same choice and same reasoning as `ServiceLog.issue`: an allowance has
    # no meaning apart from the work item it was credited to, so it belongs in the bin
    # alongside it -- and because `soft_delete_related_objects` handles CASCADE by soft
    # deleting, the row survives in `all_objects` for any settlement that already
    # counted it.
    issue = models.ForeignKey(
        "db.Issue",
        on_delete=models.CASCADE,
        related_name="service_allowances",
    )

    # ------------------------------------------------------------ what it is for

    # The commercial reference of the project this allowance pays for, e.g.
    # "Proposta 2026-014". Section 1 of the brief asks for it so that an allowance can
    # be tied back to the document that sold it.
    reference = models.CharField(max_length=255, blank=True)

    notes = models.TextField(blank=True)

    # ------------------------------------------------------------- the totals

    # **This row carries the balance; the ledger carries the history** -- decision D23,
    # applied unchanged. The two are written in the same transaction, always, and
    # `plane.utils.service_allowance.reconcile_allowance` asserts they still agree
    # column by column.
    #
    # Scales fixed by section 4b of the master context. Do not widen locally.

    # Every credit ever made, summed. Acceptance criteria 1 and 5: 40h then 10h leaves
    # this at 50h, and the two credits stay individually visible in the ledger.
    credited_hours = models.DecimalField(max_digits=10, decimal_places=4, default=Decimal("0.0000"))

    # The accumulator every debit moves. Always written under `select_for_update()` on
    # this row and always with an `F()` expression -- never read-modify-write.
    consumed_hours = models.DecimalField(max_digits=10, decimal_places=4, default=Decimal("0.0000"))

    # ------------------------------------------------- what happened at closing

    # Both are records of the close, not terms in the live balance. `balance_hours` is
    # `credited - consumed`; these two explain where a closed allowance's balance went,
    # and they are what makes the ledger of a closed allowance sum to exactly zero.

    # A deficit settled by billing it, in hours. Section 3: on closing an allowance in
    # deficit the Admin either credits more hours -- which is a `CREDIT` *before*
    # closing, not a settlement mode -- or bills the overage. Phase 6 converts it to
    # reais through the same mechanism `ServiceContractPeriod.overage_hours` uses: the
    # amount lands on the `OVERAGE_BILLED` ledger row, priced by `overage_hour_rate`
    # below.
    overage_hours = models.DecimalField(max_digits=10, decimal_places=4, default=Decimal("0.0000"))

    # ------------------------------------------------------------ the price of overage

    # What an hour past this allowance costs, in reais. Null falls back to the client's
    # base hour rate for the vigency in force -- see
    # `plane.utils.service_pricing.resolve_overage_rate`.
    #
    # **This column exists because the allowance is a separately negotiated sale, and
    # the support price is the wrong price for it.** A project sold at R$ 180/h does not
    # have overage at R$ 200/h because R$ 200/h is what that client's support costs; the
    # two numbers differ roughly as often as the two things are sold separately, which is
    # always. Falling back to the client's base rate as the *only* answer would price
    # project overage at the support rate and be wrong almost every time.
    #
    # It is registered now rather than later for the reason `ServiceContract
    # .overage_hour_rate` gives for itself: the moment hours are credited to a work item
    # is the moment the value of that project is known, and adding an audited column
    # afterwards means back-filling an audit trail, which cannot be done honestly. Same
    # trade the contract phase already made and accepted.
    #
    # Nullable and tracked, so it is the one field on this model that relies on the
    # `CONFIG_VALUE_UNSET` sentinel (D4) to satisfy
    # `svc_cfg_activity_shape_matches_verb`. Monetary scale (12, 2) per section 4b.
    overage_hour_rate = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    # A surplus written off at close. The project came in under budget and the hours
    # are lost, because section 3 forbids them moving to the contract pool. There is no
    # "carry" here on purpose: unlike a competency month, an allowance has no successor.
    expired_hours = models.DecimalField(max_digits=10, decimal_places=4, default=Decimal("0.0000"))

    # ------------------------------------------------------------------ lifecycle

    status = models.CharField(
        max_length=20,
        choices=ServiceIssueAllowanceStatus.choices,
        default=ServiceIssueAllowanceStatus.OPEN,
    )

    closed_at = models.DateTimeField(null=True, blank=True)

    # SET_NULL, unlike the billing foreign keys. A trace of who clicked close, not a
    # term in a calculation, following `ServiceContractPeriod.closed_by`.
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="closed_service_issue_allowances",
        null=True,
        blank=True,
    )

    # Import provenance, following the convention used across plane.db.
    external_source = models.CharField(max_length=255, null=True, blank=True)
    external_id = models.CharField(max_length=255, null=True, blank=True)

    @property
    def balance_hours(self):
        """Credited minus consumed.

        A property rather than a column (decision D3), and the queryset equivalent is
        ``plane.utils.service_allowance.annotate_allowance_balance``. A
        ``GeneratedField`` was refused for the same reasons D3 gives.

        **Negative is a legitimate, expected state.** Section 3 is explicit that
        overflowing an allowance does not block work already performed -- the balance
        goes below zero and an alert fires, exactly as it does for a contract pool.
        """
        return self.credited_hours - self.consumed_hours

    @property
    def consumed_pct(self):
        """Consumption as a percentage of what was credited, or ``None`` before any credit.

        Section 4 of the brief wants the work item indicator to show "creditado,
        consumido, restante, e percentual de consumo", and the percentage is the one of
        the four that is not a subtraction.
        """
        if self.credited_hours <= 0:
            return None

        return (self.consumed_hours / self.credited_hours) * Decimal("100")

    def config_summary(self):
        """One line for the ``CREATED`` and ``DELETED`` rows of the audit trail.

        Present because ``ChangeTrackerMixin`` models carry one by convention. In
        practice only the ``UPDATED`` path is exercised for this model: an allowance row
        comes into being as a side effect of the first credit, and that event is already
        audited by the ``CREDIT`` ledger entry with its own actor -- recording it twice
        is the duplicate trail section 6 of the master context forbids.
        """
        rate = "sem valor/hora proprio" if self.overage_hour_rate is None else f"R$ {self.overage_hour_rate}/h"

        return f"Bolsa de {self.credited_hours}h ({rate})"

    class Meta:
        verbose_name = "Service Issue Allowance"
        verbose_name_plural = "Service Issue Allowances"
        db_table = "service_issue_allowances"
        ordering = ("-created_at",)

        # Uniqueness under soft delete follows the house pattern: unique_together
        # including deleted_at, plus a conditional UniqueConstraint for live rows.
        unique_together = [["issue", "deleted_at"]]

        constraints = [
            # ONE ALLOWANCE PER WORK ITEM. See the class docstring: this is what makes
            # allowance resolution total instead of ambiguous, and therefore what keeps
            # D27 from needing a second reading for this phase.
            models.UniqueConstraint(
                fields=["issue"],
                condition=Q(deleted_at__isnull=True),
                name="service_issue_allowance_unique_per_issue_when_deleted_at_null",
            ),
            # `consumed_hours` is deliberately absent from this: it is the one hour
            # column here that legitimately moves in both directions, because a
            # reversal subtracts from it.
            models.CheckConstraint(
                condition=Q(credited_hours__gte=0, overage_hours__gte=0, expired_hours__gte=0),
                name="service_issue_allowance_hours_are_not_negative",
            ),
            # An open allowance cannot have been closed by anyone, and a close with no
            # timestamp would be a decision with no date.
            models.CheckConstraint(
                condition=(
                    Q(status=ServiceIssueAllowanceStatus.OPEN, closed_at__isnull=True)
                    | Q(status=ServiceIssueAllowanceStatus.CLOSED, closed_at__isnull=False)
                ),
                name="service_issue_allowance_closed_at_matches_status",
            ),
            # Decision D20, in DDL: a rate of zero would be a second mechanism for "do
            # not charge", and this system has exactly one. Null is the legitimate "not
            # negotiated separately, use the client's base rate" state, which is why this
            # is written to permit null rather than as a plain `> 0`.
            models.CheckConstraint(
                condition=Q(overage_hour_rate__isnull=True) | Q(overage_hour_rate__gt=0),
                name="service_issue_allowance_overage_rate_is_positive",
            ),
        ]

        indexes = [
            # The debit path's first question, asked for the work item and then for
            # each ancestor up the chain.
            models.Index(fields=["issue"], name="svc_allowance_issue_idx"),
            # The workspace panel scans open allowances.
            models.Index(fields=["workspace", "status"], name="svc_allowance_ws_status_idx"),
            models.Index(fields=["project", "status"], name="svc_allowance_proj_status_idx"),
        ]

    def __str__(self):
        return f"{self.issue_id} {self.credited_hours}h"
