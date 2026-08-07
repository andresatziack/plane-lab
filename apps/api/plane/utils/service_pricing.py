# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Price sheets: which rate is in force, what a work log costs, what overage costs.

Sections 1, 2 and 3 of the pricing phase brief. The model-free arithmetic is in
``plane.utils.service_money``; this module is the half that reads the database.

**The vigency rule, once, because everything here depends on it:** the sheet in force on
a date is the one with the greatest ``starts_on`` that is not after it. There is no
``ends_on`` column -- see ``ServiceClientPrice`` for why a closed interval would be two
truths that can disagree -- so there is no gap and no overlap to resolve, and no tie to
break.

**Which date?** For a work log, ``worked_on`` -- the service date, never the date it was
typed. Work done in March is billed at March's price even when the technician types it in
May, which is R7 and acceptance criterion 12 applied to money instead of competency. The
alternative makes the March invoice unreproducible: two logs for the same March day typed
a month apart would carry different prices. For a contract period's overage,
``period.starts_on`` -- the price in force when the competency *began*, so a readjustment
never reaches backwards into a month that is already partly worked.

**Absence never blocks.** Every failure here is recorded on the work log and reported, not
raised: D27, D9 and D21 all say that configuration a technician does not control must
never cost them their record. The one exception is billing a contract period's overage,
which is a deliberate administrative act with an alternative available -- see
``resolve_overage_rate``.
"""

# Python imports
from decimal import Decimal

# Module imports
from plane.db.models import (
    ServiceBillingType,
    ServiceClientHourTypeRate,
    ServiceClientPrice,
    ServiceHourType,
    ServiceLog,
    ServicePricingFailure,
    ServiceRateBasis,
)
from plane.utils.service_money import ZERO_MONEY, amount_from_absolute_rate, amount_from_base_rate

# ---------------------------------------------------------------------------
# Failure codes
# ---------------------------------------------------------------------------
#
# These are the model's own choices, re-exported so callers in the domain layer do not
# each reach into `plane.db.models` for a string. Same shape as `service_pool`'s codes.

#: Section 1 of Phase 1: a project with no client is internal work, "não apontável para
#: faturamento". **Not a failure**, and the consolidation excludes it from revenue rather
#: than listing it as a zeroed pendency -- a company that does real internal work would
#: otherwise face a panel full of "problems" nobody can act on, and an operator who learns
#: to ignore the list misses the one entry that mattered.
INTERNAL_PROJECT_NO_CLIENT = ServicePricingFailure.INTERNAL_PROJECT_NO_CLIENT

#: Registration pendency: real money that cannot be invoiced yet. Somebody must act.
#: These two are **opposite mistakes** and are therefore separate codes -- never
#: registered at all, versus registered with a start date later than the work.
NO_PRICE_SHEET_FOR_CLIENT = ServicePricingFailure.NO_PRICE_SHEET_FOR_CLIENT
NO_PRICE_SHEET_IN_FORCE = ServicePricingFailure.NO_PRICE_SHEET_IN_FORCE

#: Raised only by ``resolve_overage_rate`` when asked to bill, never by work log pricing.
#: See that function for why this is the one place in the phase that blocks.
OVERAGE_RATE_NOT_CONFIGURED = "OVERAGE_RATE_NOT_CONFIGURED"


class ServicePricingValidationError(ValueError):
    """A pricing operation was refused.

    Carries an UPPER_SNAKE ``code`` and a ``detail`` dict, the same shape as
    ``plane.utils.service_pool.ServicePoolValidationError``, so the views translate both
    with one branch.

    There is deliberately **no ``blocks_the_work_log`` property** here, unlike the pool's
    exception. Nothing in work log pricing raises: the three pricing failures are
    *recorded* on the row. This class exists for the overage path alone.
    """

    def __init__(self, code, detail=None):
        self.code = code
        self.detail = detail or {}
        super().__init__(code)


# ---------------------------------------------------------------------------
# Resolving the sheet in force
# ---------------------------------------------------------------------------


def resolve_price_sheet(service_client_id, on_date):
    """The price sheet in force for a client on a date, or ``None``.

    The greatest ``starts_on`` not after ``on_date``. Returns ``None`` both when the
    client has no sheets at all and when every sheet starts later -- the caller
    distinguishes those two with ``client_has_any_price_sheet``, because they are
    different mistakes and the work log has to say which one happened.

    Reads through the default manager, so a soft deleted sheet is invisible. That is
    correct rather than incidental: deleting a sheet that was registered by mistake must
    make the previous one take over again, and it does, because resolution is a fresh
    ordered read every time.
    """
    return (
        ServiceClientPrice.objects.filter(service_client_id=service_client_id, starts_on__lte=on_date)
        .order_by("-starts_on")
        .first()
    )


def client_has_any_price_sheet(service_client_id):
    """Whether a client has any price sheet at all, in force or not.

    Only reason this exists: telling ``NO_PRICE_SHEET_FOR_CLIENT`` from
    ``NO_PRICE_SHEET_IN_FORCE``. "Nobody ever registered a price" and "somebody registered
    it with the wrong start date" need different people to do different things, and a
    single "no price" code would hide which.
    """
    return ServiceClientPrice.objects.filter(service_client_id=service_client_id).exists()


def sheet_overrides(price_sheet):
    """``{hour_type_id: absolute_rate}`` for one sheet.

    A dict rather than a queryset because both callers need random access by hour type,
    and because a sheet has at most as many overrides as the workspace has hour types --
    single digits in practice.
    """
    if price_sheet is None:
        return {}

    return {
        str(hour_type_id): absolute_rate
        for hour_type_id, absolute_rate in ServiceClientHourTypeRate.objects.filter(
            price_id=price_sheet.pk
        ).values_list("hour_type_id", "absolute_rate")
    }


# ---------------------------------------------------------------------------
# The effective table -- section 1's screen
# ---------------------------------------------------------------------------


def effective_rate_table(service_client_id, on_date, *, workspace_id):
    """Every active hour type with the rate that actually applies, for one date.

    Section 1 of the brief: "a tela deve exibir a tabela efetiva". Derived on read and
    never stored -- storing it would be the second independent price table that section 2b
    of the master context and D16 exist to prevent, and it would drift from the multiplier
    the moment either changed.

    Returns a list of dicts, one per active hour type, ordered by the catalogue's own
    ``sequence`` so the screen matches every other list of hour types:

    ``hour_type_id``, ``hour_type_name``, ``multiplier``, ``rate`` (string, monetary
    scale), ``basis`` (a ``ServiceRateBasis`` value), and ``is_overridden``.

    Returns ``[]`` when no sheet is in force -- there is no table to show, and inventing
    one from a zero base would put R$ 0,00 on a screen next to real hour types.
    """
    price_sheet = resolve_price_sheet(service_client_id, on_date)

    if price_sheet is None:
        return []

    overrides = sheet_overrides(price_sheet)
    rows = []

    for hour_type in ServiceHourType.objects.filter(workspace_id=workspace_id, is_active=True).order_by("sequence"):
        override = overrides.get(str(hour_type.pk))

        if override is not None:
            # The absolute rate IS the rate for one logged hour -- it replaces
            # `base * multiplier` rather than adjusting it.
            rate = override
            basis = ServiceRateBasis.ABSOLUTE_OVERRIDE
        else:
            # Displayed as the rate per *logged* hour so the two rows of this table are
            # comparable to each other. This product is presentation only and is never
            # what prices a work log: pricing multiplies equivalent hours by the base, one
            # rounding, per section 4b. Quantising here and multiplying later would round
            # twice.
            rate = (price_sheet.base_hour_rate * hour_type.multiplier).quantize(Decimal("0.01"))
            basis = ServiceRateBasis.BASE_MULTIPLIER

        rows.append(
            {
                "hour_type_id": str(hour_type.pk),
                "hour_type_name": hour_type.name,
                "multiplier": str(hour_type.multiplier),
                "rate": str(rate),
                "basis": basis,
                "is_overridden": override is not None,
            }
        )

    return rows


def price_sheet_summary(price_sheet):
    """One sheet as a dict, with its overrides. Used by the settings screen and the API."""
    return {
        "price_id": str(price_sheet.pk),
        "starts_on": str(price_sheet.starts_on),
        "base_hour_rate": str(price_sheet.base_hour_rate),
        "notes": price_sheet.notes,
        "overrides": [
            {
                "override_id": str(override.pk),
                "hour_type_id": str(override.hour_type_id),
                "absolute_rate": str(override.absolute_rate),
            }
            for override in ServiceClientHourTypeRate.objects.filter(price_id=price_sheet.pk).order_by("hour_type")
        ],
    }


# ---------------------------------------------------------------------------
# Pricing a work log -- section 3
# ---------------------------------------------------------------------------


def resolve_log_price(service_log, *, service_client_id):
    """What one work log is worth, as the four columns the row will carry.

    Returns a dict with exactly the keys ``applied_hour_rate``, ``applied_rate_basis``,
    ``amount`` and ``pricing_failure_reason`` -- the shape ``ServiceLog`` stores, so the
    caller assigns without reshaping and the check constraints see a coherent row.

    **Prices from ``worked_on``**, per the module docstring. **Never blocks**: a client
    with no usable sheet yields a zeroed result carrying the reason, because the work was
    already performed.

    The two formulas and why they take different hour quantities are documented on
    ``plane.utils.service_money``; the only thing this function decides is *which* one
    applies, and an override on the log's hour type is what decides it.
    """
    if service_client_id is None:
        # Internal work. Not a pendency -- see `INTERNAL_PROJECT_NO_CLIENT`.
        return _unpriced(INTERNAL_PROJECT_NO_CLIENT)

    price_sheet = resolve_price_sheet(service_client_id, service_log.worked_on)

    if price_sheet is None:
        # Two different mistakes, told apart so the panel can name the right one.
        if client_has_any_price_sheet(service_client_id):
            return _unpriced(NO_PRICE_SHEET_IN_FORCE)

        return _unpriced(NO_PRICE_SHEET_FOR_CLIENT)

    override = sheet_overrides(price_sheet).get(str(service_log.hour_type_id))

    if override is not None:
        # Acceptance criterion 5. LOGGED hours: the absolute rate already embeds the
        # multiplier, because it replaced `base * multiplier`.
        return {
            "applied_hour_rate": override,
            "applied_rate_basis": ServiceRateBasis.ABSOLUTE_OVERRIDE,
            "amount": amount_from_absolute_rate(logged_hours=service_log.logged_hours, absolute_rate=override),
            "pricing_failure_reason": None,
        }

    # Acceptance criteria 1, 2 and 3. EQUIVALENT hours: the multiplier is already inside
    # them, so the base rate multiplies once and only once.
    return {
        "applied_hour_rate": price_sheet.base_hour_rate,
        "applied_rate_basis": ServiceRateBasis.BASE_MULTIPLIER,
        "amount": amount_from_base_rate(
            equivalent_hours=service_log.equivalent_hours,
            base_hour_rate=price_sheet.base_hour_rate,
        ),
        "pricing_failure_reason": None,
    }


def _unpriced(reason):
    """The zeroed shape, with the reason that explains it.

    A helper rather than four repeated literals, because the check constraint
    ``service_log_amount_requires_a_rate`` demands all three of rate, basis and amount
    agree -- and three call sites writing that agreement by hand is three chances to get
    it wrong.
    """
    return {
        "applied_hour_rate": None,
        "applied_rate_basis": None,
        "amount": ZERO_MONEY,
        "pricing_failure_reason": reason,
    }


# ---------------------------------------------------------------------------
# Pricing overage -- section 6
# ---------------------------------------------------------------------------


def resolve_overage_rate(*, service_client_id, on_date, contract=None, allowance=None):
    """The rate to bill an hour of overage at. Raises rather than returning zero.

    Resolution order, and each fallback is a deliberate commercial statement:

    1. ``allowance.overage_hour_rate`` when billing a work item allowance. **An allowance
       is a separately negotiated sale, so the support price is the wrong price for it**:
       a project sold at R$ 180/h does not have overage at R$ 200/h merely because that is
       what the client's support costs. This is the column D22's argument earned -- the
       moment hours are credited is the moment that project's value is known.
    2. ``contract.overage_hour_rate`` when billing a contract period. A term of the
       contract, stored since Phase 4 for exactly this.
    3. The client's ``base_hour_rate`` in force on ``on_date``. What support costs, used
       when nothing more specific was negotiated.

    **THIS IS THE ONE PLACE IN THE PHASE THAT BLOCKS**, and the asymmetry with work log
    pricing is deliberate rather than inconsistent. D27, D9 and D21 protect *work already
    executed by a technician*: refusing it would destroy a record of real labour over
    configuration the technician does not control. Billing overage is not that. It is a
    **deliberate administrative act, at a moment of choice, with a legitimate alternative
    already available** -- a contract period can settle as ``CARRIED`` instead. Billing at
    R$ 0,00 would silently zero a real deficit, destroying the debt with no trace, which
    is the worst of the three outcomes. So it raises, and the admin decides.

    Note the rate is resolved by ``on_date`` -- ``period.starts_on`` for a contract
    period, so a readjustment never reaches backwards into a competency that is already
    partly worked.
    """
    if allowance is not None and allowance.overage_hour_rate is not None:
        return allowance.overage_hour_rate

    if contract is not None and contract.overage_hour_rate is not None:
        return contract.overage_hour_rate

    price_sheet = resolve_price_sheet(service_client_id, on_date) if service_client_id is not None else None

    if price_sheet is None:
        raise ServicePricingValidationError(
            OVERAGE_RATE_NOT_CONFIGURED,
            {
                "service_client_id": str(service_client_id) if service_client_id else None,
                "on_date": str(on_date),
                "contract_id": str(contract.pk) if contract is not None else None,
                "allowance_id": str(allowance.pk) if allowance is not None else None,
            },
        )

    return price_sheet.base_hour_rate


# ---------------------------------------------------------------------------
# Reads for the work item panel
# ---------------------------------------------------------------------------


def issue_pricing_snapshot(issue, worked_on=None):
    """What the work item panel shows an Admin about money, before anything is typed.

    Returns the client's effective table for the date, so the form can preview a value,
    plus the reason when there is no table. ``None`` for ``worked_on`` means today.

    **Admin only.** R11 puts the value in reais out of a technician's reach, and R11(b)
    says that restriction is a serializer's job, not the interface's -- so the caller is
    responsible for not handing this to a MEMBER. The view enforces it.
    """
    from django.utils import timezone

    on_date = worked_on or timezone.now().date()
    service_client_id = issue.project.service_client_id

    if service_client_id is None:
        return {"effective_table": [], "reason": INTERNAL_PROJECT_NO_CLIENT, "on_date": str(on_date)}

    table = effective_rate_table(service_client_id, on_date, workspace_id=issue.workspace_id)

    if not table:
        reason = (
            NO_PRICE_SHEET_IN_FORCE if client_has_any_price_sheet(service_client_id) else NO_PRICE_SHEET_FOR_CLIENT
        )

        return {"effective_table": [], "reason": reason, "on_date": str(on_date)}

    return {"effective_table": table, "reason": None, "on_date": str(on_date)}


def price_sheet_in_use(price_sheet):
    """Whether any work log was priced by this sheet's vigency, for the delete guard.

    ``validate_catalog_delete``'s docstring named this as the pricing phase's extension
    point -- "referenced by a client price override". A sheet whose vigency already
    priced work logs must not vanish, because the R4 snapshot on those rows would then
    point at a rate nobody can look up to verify an invoice.

    Compares by date range rather than by a foreign key, because a work log deliberately
    does **not** reference its sheet: R4 requires the *value* to be snapshotted, and a
    link would invite a later join that recomputes history. The range is
    ``[this sheet's start, the next sheet's start)``.
    """
    successor = (
        ServiceClientPrice.objects.filter(
            service_client_id=price_sheet.service_client_id,
            starts_on__gt=price_sheet.starts_on,
        )
        .order_by("starts_on")
        .first()
    )

    priced = ServiceLog.objects.filter(
        project__service_client_id=price_sheet.service_client_id,
        settled_billing_route=ServiceBillingType.BillingRoute.BILL_AMOUNT,
        applied_hour_rate__isnull=False,
        worked_on__gte=price_sheet.starts_on,
    )

    if successor is not None:
        priced = priced.filter(worked_on__lt=successor.starts_on)

    return priced.exists()
