# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
from decimal import Decimal

# Django imports
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q

# Module imports
from ..mixins import ChangeTrackerMixin
from .workspace import WorkspaceBaseModel


class ServiceConfigEntity:
    """Stable identifiers for ``ServiceConfigActivity.entity_name``.

    Deliberately plain constants rather than model ``choices``: every later phase
    adds an audited entity (contract, contract period, client hour price), and
    ``choices`` would force a schema migration each time for no database benefit.
    The protection against a typo is that callers reference these names instead of
    writing a literal.
    """

    HOUR_TYPE = "service_hour_type"
    BILLING_TYPE = "service_billing_type"
    HOLIDAY = "service_holiday"
    CLASSIFICATION_WINDOW = "service_classification_window"


class ServiceCatalogBaseModel(ChangeTrackerMixin, WorkspaceBaseModel):
    """Shared shape of the workspace level catalogues that parameterise work logs.

    Catalogues live at the workspace level on purpose: the multiplier and the
    billing route have to be single valued across the whole operation, otherwise
    the same hour could be priced two ways. Per client deviation is expressed by
    the client's own default and, from the pricing phase on, by an absolute price
    override -- never by a second definition of the multiplier. See the master
    context, section 2b, and DECISOES.md, D16.

    Named ``Service*`` to stay clear of Plane's commercial Time Tracking feature,
    which owns the ``Worklog`` vocabulary. Same reasoning as ``ServiceClient``.

    Inherited from ``WorkspaceBaseModel``: ``workspace`` (required) plus a nullable
    ``project`` FK. The ``project`` field is unused here -- a catalogue belongs to a
    workspace, not to a project -- and is excluded from serializers.

    **Extension point, deliberately not built.** Section 4 of the phase brief
    allows *optionally* enabling a subset of catalogue options per project. That
    would be a join table in the shape of ``ProjectIssueType``
    (``project_service_hour_types``, columns ``project``, ``<option>``,
    ``is_enabled``), and nothing here blocks adding it later. It is absent because
    no acceptance criterion needs it, and because a join table whose empty state
    has to mean "everything enabled" is a denylist -- inverted semantics that would
    add a branch to every dropdown and every report for a feature nobody asked for
    yet.
    """

    # Display order only. See the note on ``sequence`` below.
    SEQUENCE_STEP = 15000

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)

    # Display order in the admin panel and in the work log form dropdown.
    #
    # This is the one FloatField in this feature, and the exception is deliberate:
    # the master context forbids float for financial arithmetic, and a display
    # ordinal is not financial arithmetic. It is a float because the house pattern
    # for drag reordering (``State.sequence``, ``Issue.sort_order``,
    # ``UserFavorite.sequence``) writes a midpoint between two neighbours and so
    # needs a dense ordering.
    #
    # Do NOT reuse this field as the classification priority that the calendar and
    # windows phase introduces. They are different concerns that happen to both be
    # orderings: `sequence` is what an admin drags, `priority` is what the
    # classification engine resolves by. Merging them would let a cosmetic drag
    # silently reclassify a holiday as regular business hours -- and therefore
    # change an invoice.
    sequence = models.FloatField(default=65535)

    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)

    # Import provenance, following the convention used across plane.db.
    external_source = models.CharField(max_length=255, null=True, blank=True)
    external_id = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        abstract = True
        ordering = ("sequence",)
        # Uniqueness under soft delete follows the house pattern: unique_together
        # including deleted_at, plus a conditional UniqueConstraint for live rows.
        unique_together = [["workspace", "name", "deleted_at"]]
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "name"],
                condition=Q(deleted_at__isnull=True),
                name="%(class)s_unique_name_workspace_when_deleted_at_null",
            ),
            # "Exactly one default per catalogue" is two halves. This constraint is
            # the *at most one* half, and it is the real guard: two concurrent
            # requests promoting different options cannot both win, whatever the
            # application layer does. The *at least one* half cannot be expressed
            # in DDL and is held by save() below plus the refusal to unset,
            # deactivate or delete the current default.
            models.UniqueConstraint(
                fields=["workspace"],
                condition=Q(is_default=True, deleted_at__isnull=True),
                name="%(class)s_single_default_per_workspace",
            ),
        ]

    def __str__(self):
        return f"{self.name} <{self.workspace.name}>"

    def config_summary(self):
        """One human-readable line describing this row, for a creation or deletion entry.

        Subclasses extend it with whatever changes the calculation. Kept deliberately
        short: it lands in a single audit column that someone reads under pressure while
        reconstructing an invoice, not in a debugger.
        """
        return f"{self.name}"

    def save(self, *args, **kwargs):
        if self._state.adding:
            largest_sequence = (
                type(self)
                .objects.filter(workspace_id=self.workspace_id)
                .aggregate(largest=models.Max("sequence"))["largest"]
            )

            if largest_sequence is None:
                # First live option of this catalogue. It becomes the default even
                # if the caller said otherwise, because a catalogue with options
                # and no default would break the pre-selection in the work log
                # form. This is the "at least one" half of the invariant.
                self.is_default = True
            else:
                self.sequence = largest_sequence + self.SEQUENCE_STEP

        super().save(*args, **kwargs)


class ServiceHourType(ServiceCatalogBaseModel):
    """When the work happened, and what that costs relative to business hours.

    Seeded with Horário comercial (1.00), Fora do expediente (1.50) and Domingos e
    feriados (2.00). The names are Portuguese because these rows are *data* for a
    Brazilian operation, editable by the admin, and a work log has to keep
    displaying the name it was recorded against -- not UI chrome to be localised.

    Classification is driven from here by ``priority`` plus the reverse ``windows``
    relation (``ServiceClassificationWindow``). An hour type with **no window is never
    suggested** by the engine and remains selectable by hand only, which is what makes
    a newly created type inert until an admin gives it a range.
    """

    # `priority` is tracked for the same reason as `multiplier`: it decides which hour
    # type the classification engine picks, so changing it alters future billing with
    # exactly the same weight. The audit row is written automatically by
    # `save_with_config_activity`.
    TRACKED_FIELDS = ["multiplier", "priority", "is_active"]
    CONFIG_ENTITY_NAME = ServiceConfigEntity.HOUR_TYPE

    #: Priority of an hour type that the admin created without thinking about it.
    #: Far above the seeded values so a new type never silently outranks them. It is
    #: inert until the type is given a window, because an hour type with no window is
    #: never suggested.
    DEFAULT_PRIORITY = 1000

    # Scale fixed by section 4b of the master context. Do not widen locally.
    #
    # COUPLING TO PRESERVE: the four decimal places used by every hour quantity in
    # this system are a consequence of two constraints -- 15 minute blocks (R2),
    # which make logged hours a multiple of 0.25, and this multiplier's two
    # decimals. The product is p*m/400 and 400 = 2^4 * 5^2, so it terminates in at
    # most four decimal places (worst case reached: 0.25 * 1.01 = 0.2525). If this
    # multiplier ever gains a third decimal place, hour fields need five
    # (0.25 * 1.001 = 0.25025) or the calculation silently truncates.
    multiplier = models.DecimalField(
        max_digits=4,
        decimal_places=2,
        default=Decimal("1.00"),
        # Zero is refused on purpose. It would be a third way to make work free,
        # competing with the non-billable billing route -- and there has to be
        # exactly one mechanism for "do not charge". Values below 1 are legitimate
        # and allowed: travel time billed at half rate is common practice.
        validators=[MinValueValidator(Decimal("0.01"))],
    )

    color = models.CharField(max_length=255, default="#60646C")

    # Resolution order for the classification engine: LOWER WINS. Seeded 10 for
    # Domingos e feriados, 20 for Fora do expediente, 30 for Horário comercial, so the
    # holiday window beats the weekday window that covers the same instant.
    #
    # A SECOND ORDERING, AND NOT `sequence`. `sequence` is what the admin drags in the
    # panel and is cosmetic; this is what the engine resolves by and is financial.
    # Merging them would let a drag reclassify hours and therefore change an invoice --
    # the warning has been on `sequence` since the catalogue was built, and this field
    # is the other half of it.
    #
    # The gaps are deliberate: they leave room for the kind of configuration the
    # calendar phase promises without a deploy. A "Plantão de madrugada" at 15 sits
    # between the seeded 10 and 20, so it beats Fora do expediente and still loses to
    # a holiday.
    priority = models.IntegerField(default=DEFAULT_PRIORITY)

    def config_summary(self):
        """Includes the multiplier and the priority: both decide what an hour costs."""
        return f"{self.name} (multiplicador {self.multiplier}, prioridade {self.priority})"

    class Meta(ServiceCatalogBaseModel.Meta):
        verbose_name = "Service Hour Type"
        verbose_name_plural = "Service Hour Types"
        db_table = "service_hour_types"
        indexes = [
            models.Index(fields=["workspace", "is_active"], name="svc_hour_type_active_idx"),
            # The engine reads active hour types of a workspace in priority order.
            models.Index(fields=["workspace", "priority"], name="svc_hour_type_priority_idx"),
        ]


class ServiceBillingType(ServiceCatalogBaseModel):
    """Where the logged time goes: a contracted pool, an invoice, or nowhere.

    This is the heart of the debit hierarchy rule (R6). Seeded with Contrato
    (debit_pool, the default), Avulso (bill_amount), Garantia (non_billable) and
    Cortesia (non_billable).

    Garantia and Cortesia share a route but stay separate rows on purpose. Both
    mean "not charged", yet they are opposite management signals: garantia is
    rework the client already paid for, so a month heavy with it is a delivery
    quality problem, while cortesia is a discount that was chosen, so a month
    heavy with it is a commercial decision. Reports group by billing type, so the
    distinction survives without an extra column.
    """

    TRACKED_FIELDS = ["billing_route", "is_active"]
    CONFIG_ENTITY_NAME = ServiceConfigEntity.BILLING_TYPE

    class BillingRoute(models.TextChoices):
        """Stored values are English, following the house convention; the
        Portuguese names in the phase brief map as noted."""

        DEBIT_POOL = "debit_pool", "Debit hour pool"  # DEBITA_POOL
        BILL_AMOUNT = "bill_amount", "Bill amount"  # FATURA_REAIS
        NON_BILLABLE = "non_billable", "Non billable"  # NAO_FATURAVEL

    billing_route = models.CharField(
        max_length=20,
        choices=BillingRoute.choices,
        default=BillingRoute.DEBIT_POOL,
    )

    def config_summary(self):
        """Includes the route, which decides whether the client is charged at all."""
        return f"{self.name} (rota {self.billing_route})"

    class Meta(ServiceCatalogBaseModel.Meta):
        verbose_name = "Service Billing Type"
        verbose_name_plural = "Service Billing Types"
        db_table = "service_billing_types"
        indexes = [models.Index(fields=["workspace", "is_active"], name="svc_billing_type_active_idx")]
