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
from .service_catalog import ServiceConfigEntity
from .workspace import WorkspaceBaseModel


class ServiceClientPrice(ChangeTrackerMixin, WorkspaceBaseModel):
    """One dated price sheet for a client: the base hour rate in force from a date.

    Section 1 of the phase brief asks that registering a new client require changing
    **a single number**, and that is what ``base_hour_rate`` is. The rate of an
    individual hour type is *derived*, not registered:
    ``base_hour_rate * ServiceHourType.multiplier``. Section 2b of the master context
    and D16 explain why -- two independently registered price tables can disagree with
    the multiplier that classified the hour, and then the same worked hour has two
    defensible prices.

    **A sheet is a version, not a mutable row.** Section 2 requires an annual
    readjustment that does not rewrite history, and the R4 snapshot on the work log is
    not enough on its own: a March work log typed in May, after an April readjustment,
    has no snapshot yet and must still price at March's rate. That retroactive case is
    the reason this table exists.

    **The vigency is resolved by "greatest ``starts_on`` not after the date"** -- see
    ``plane.utils.service_pricing.resolve_price_sheet``. There is deliberately **no
    ``ends_on``**: a ``(starts_on, ends_on)`` pair is two truths that can disagree, and
    they disagree in two ways that are both silent. A **gap** would leave a date with no
    price, and an **overlap** would leave a date with two. With this rule neither is
    representable -- overlap is refused by the partial unique index on
    ``(service_client, starts_on)``, and a gap cannot exist because a sheet holds until
    the next one starts. Same reasoning as decision D3: do not store what can be derived
    without ambiguity.

    A date **before** the earliest sheet is the one open case, and it is an absence of
    configuration rather than an ambiguity, so D27 and D9 apply in full: the work log is
    not blocked, it is priced at zero and carries
    ``ServicePricingFailure.NO_PRICE_SHEET_IN_FORCE``. See
    ``plane.utils.service_pricing``.

    Inherited from ``WorkspaceBaseModel``: ``workspace`` (required) plus a nullable
    ``project`` FK that is unused here and excluded from serializers -- a price belongs
    to a client, not to a project.
    """

    TRACKED_FIELDS = ["starts_on", "base_hour_rate"]
    CONFIG_ENTITY_NAME = ServiceConfigEntity.CLIENT_PRICE

    # ---------------------------------------------------------------- relations

    # DO_NOTHING, like every configuration foreign key that a work log's money depends
    # on. `plane.bgtasks.deletion_task.soft_delete_related_objects` branches on the
    # on_delete *name*, and everything except DO_NOTHING and SET_NULL falls into a
    # catch-all that soft deletes the related rows -- so CASCADE or PROTECT here would
    # mean an admin tidying clients silently soft deletes price history that invoices
    # were built from. Same choice, same reasons, as `ServiceContract.service_client`.
    service_client = models.ForeignKey(
        "db.ServiceClient",
        on_delete=models.DO_NOTHING,
        related_name="prices",
    )

    # ------------------------------------------------------------- the vigency

    # The day the sheet takes effect, inclusive. Constrained to the **first day of a
    # month** below, which is not tidiness -- see the constraint's own comment.
    starts_on = models.DateField()

    # The single number. Monetary scale (12, 2) per section 4b of the master context,
    # never the hour scale (10, 4).
    #
    # **Zero is refused, and that is decision D20 rather than hygiene.** A rate of zero
    # would be a second mechanism for "do not charge", and this system has exactly one:
    # a billing type whose route is NON_BILLABLE. Two mechanisms means a report that
    # filters on the route silently misses half the free hours. Same argument, same
    # shape, as `ServiceHourType.multiplier` refusing zero.
    base_hour_rate = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )

    notes = models.TextField(blank=True)

    def config_summary(self):
        """One line for the ``CREATED`` and ``DELETED`` rows of the audit trail."""
        return f"Vigencia de {self.starts_on:%d/%m/%Y}: R$ {self.base_hour_rate}/h"

    class Meta:
        verbose_name = "Service Client Price"
        verbose_name_plural = "Service Client Prices"
        db_table = "service_client_prices"

        # Newest vigency first: the question asked of this table is almost always
        # "what is in force now", and resolution reads the greatest `starts_on`.
        ordering = ("service_client", "-starts_on")

        # Uniqueness under soft delete follows the house pattern: unique_together
        # including deleted_at, plus a conditional UniqueConstraint for live rows.
        unique_together = [["service_client", "starts_on", "deleted_at"]]

        constraints = [
            # ONE SHEET PER CLIENT PER DATE. This is what makes an overlapping vigency
            # unrepresentable, and therefore what lets resolution be a single ordered
            # read with no tie to break. See the class docstring.
            models.UniqueConstraint(
                fields=["service_client", "starts_on"],
                condition=Q(deleted_at__isnull=True),
                name="service_client_price_unique_start_when_deleted_at_null",
            ),
            # Decision D20, in DDL: exactly one mechanism for "do not charge".
            models.CheckConstraint(
                condition=Q(base_hour_rate__gt=0),
                name="service_client_price_base_rate_is_positive",
            ),
            # A VIGENCY STARTS ON THE FIRST OF A MONTH, and this constraint is load
            # bearing rather than cosmetic.
            #
            # A contract period's overage is priced by the sheet in force when the
            # **competency began** (`period.starts_on`), while a work log is priced by
            # the sheet in force on its **service date** (`worked_on`). A sheet taking
            # effect on the 28th would make those two readings disagree *within one
            # invoice*: the month's logs would split across two rates while its overage
            # used only the first, and the readjustment would reach backwards over
            # three weeks of already-executed work in the direction that favours the
            # supplier. Forcing day 1 makes the two readings coincide by construction,
            # so there is no rule to get right and no pro-rata to invent -- which is
            # the same move D31 made when it abolished pro-rata for contract vigency.
            models.CheckConstraint(
                condition=Q(starts_on__day=1),
                name="service_client_price_starts_on_first_of_month",
            ),
        ]

        indexes = [
            # Resolution: greatest `starts_on` not after a date, for one client.
            models.Index(fields=["service_client", "-starts_on"], name="svc_client_price_vigency_idx"),
        ]

    def __str__(self):
        return f"{self.service_client_id} {self.starts_on} R$ {self.base_hour_rate}"


class ServiceClientHourTypeRate(ChangeTrackerMixin, WorkspaceBaseModel):
    """An absolute hour rate that replaces ``base * multiplier`` for one hour type.

    Section 1 of the phase brief: "opcionalmente, sobrescrever o valor de um Tipo de
    Hora específico com um valor absoluto". Acceptance criterion 5 requires the override
    to prevail over the derived rate.

    **This hangs off the price sheet, not off the client, and that placement is the
    whole design.** An override pinned to the client would survive a readjustment
    untouched -- and being an *absolute* value, it would silently stop being readjusted:
    the base moves from R$ 200 to R$ 220 and the Sunday rate stays at last year's R$
    350 forever. It would also be impossible to *remove* an override at a readjustment,
    or to have one in 2026 and not in 2027. As a child of the sheet, **every vigency is
    a complete, self-contained price sheet**: a base plus its exceptions. Readjusting is
    copying the sheet forward and editing it, and reading a price is one lookup of the
    sheet in force plus its overrides -- with no merge rule across vigencies to get
    wrong, which is where this class of bug lives.

    **This shape also answers the warning D22 left for this phase.** D22 recorded that
    tracking a *nullable* column costs the audit trail, because
    ``svc_cfg_activity_shape_matches_verb`` requires non-null values on an ``updated``
    row, and it named "the pricing phase's optional absolute override" as the obvious
    candidate. The override here is **a row, not a nullable column**: registering one is
    ``CREATED`` and removing one is ``DELETED`` -- precisely the two verbs D22 added for
    events where no existing field changes -- and ``absolute_rate`` is non-nullable, so
    the ``updated`` path is clean too. The ``CONFIG_VALUE_UNSET`` sentinel still exists
    and is still needed by ``ServiceContract.overage_hour_rate``, which does not change
    shape.

    **The absolute rate multiplies ``logged_hours``, never ``equivalent_hours``** --
    see ``plane.utils.service_money.amount_from_absolute_rate``. The override replaces
    ``base * multiplier``, so it already embeds the multiplier; applying it again is the
    same double-charge as acceptance criterion 8's, in a place the brief does not warn
    about.
    """

    TRACKED_FIELDS = ["absolute_rate"]
    CONFIG_ENTITY_NAME = ServiceConfigEntity.CLIENT_HOUR_TYPE_RATE

    # ---------------------------------------------------------------- relations

    price = models.ForeignKey(
        "db.ServiceClientPrice",
        on_delete=models.DO_NOTHING,
        related_name="hour_type_rates",
    )

    # DO_NOTHING for the reasons on `ServiceLog.hour_type`, and note that this field is
    # deliberately **absent from TRACKED_FIELDS**: `ChangeTrackerMixin` snapshots its
    # tracked fields with `getattr` in `__init__`, and this DO_NOTHING foreign key may
    # point at a soft-deleted hour type -- which would raise `DoesNotExist` merely by
    # loading the row. `ServiceClassificationWindow.hour_type` documents the same trap.
    hour_type = models.ForeignKey(
        "db.ServiceHourType",
        on_delete=models.DO_NOTHING,
        related_name="client_rates",
    )

    # Non-nullable: the row's existence *is* the override, so there is no "unset" state
    # to serialise and no sentinel needed. Monetary scale (12, 2) per section 4b. Zero
    # refused for the D20 reason given on `ServiceClientPrice.base_hour_rate`.
    absolute_rate = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )

    def config_summary(self):
        """One line for the ``CREATED`` and ``DELETED`` rows of the audit trail.

        Reads ``all_objects`` so that an override on a soft-deleted hour type still
        renders -- the audit trail has to stay legible after the catalogue is tidied.
        Same approach as ``ServiceClassificationWindow.config_summary``.
        """
        from .service_catalog import ServiceHourType

        hour_type = ServiceHourType.all_objects.filter(pk=self.hour_type_id).first()
        label = hour_type.name if hour_type is not None else str(self.hour_type_id)

        return f"{label}: R$ {self.absolute_rate}/h absoluto"

    class Meta:
        verbose_name = "Service Client Hour Type Rate"
        verbose_name_plural = "Service Client Hour Type Rates"
        db_table = "service_client_hour_type_rates"
        ordering = ("price", "hour_type")

        unique_together = [["price", "hour_type", "deleted_at"]]

        constraints = [
            # One override per hour type per sheet. Two would make the effective rate
            # ambiguous, which is the state D5 says to make unrepresentable rather than
            # refuse in Python.
            models.UniqueConstraint(
                fields=["price", "hour_type"],
                condition=Q(deleted_at__isnull=True),
                name="svc_client_hour_rate_unique_type_when_deleted_at_null",
            ),
            # Decision D20 again: no second mechanism for "free".
            models.CheckConstraint(
                condition=Q(absolute_rate__gt=0),
                name="svc_client_hour_rate_is_positive",
            ),
        ]

        indexes = [
            # Reading a sheet's exceptions, which is the second half of every price
            # resolution.
            models.Index(fields=["price", "hour_type"], name="svc_client_hour_rate_idx"),
        ]

    def __str__(self):
        return f"{self.price_id} {self.hour_type_id} R$ {self.absolute_rate}"
