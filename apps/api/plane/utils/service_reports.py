# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Consumption dashboards and billing reports, aggregated in the database. Phase 9.

**Every total here is a ``GROUP BY`` in Postgres, never a Python loop over work logs.**
That is a requirement of the phase and not a preference: a consumption chart spans an
unbounded number of competencies, so iterating rows would make the cost grow with the
history rather than with the answer. The scans it does are covered by indexes that already
exist -- ``svc_log_ws_settled_worked_idx`` for revenue, ``service_log_ws_worked_idx`` for
the global filters, ``svc_ledger_period_type_idx`` for overage.

**No materialised view and no aggregate table (D51).** At this scale -- on the order of
5.000 work log rows a month -- a 36 month range is a range scan of ~10^5 rows and takes
tens of milliseconds. An aggregate would buy that back and charge three things for it: a
second place where money exists, contradicting D23's "the ledger is the only place hours
move"; an invalidation path through all four of ``ServiceLog``'s deletion doors, where the
forgotten door is a wrong invoice nobody notices; and staleness in the one report that gets
read *before* an invoice goes out. The threshold to revisit is named rather than left to
taste: **500.000 rows in a single competency, or a p95 over 500 ms with ``EXPLAIN ANALYZE``
showing a sequential scan.**

---

**How the classification stays single-sourced while the aggregation happens in SQL.**

Revenue origin depends on whether the client held a contract covering *that* competency, so
the rule cannot be a pure column expression over a multi-month range -- the answer changes
per month. Rather than run one query per month, the series groups by **exactly the
classification inputs**::

    GROUP BY competency, client, route_deviation_reason, pricing_failure_reason

and then applies ``report_bucket_from`` to the **grouped rows**. The ``Sum`` happens in the
database over the full scan; the classification runs over a result whose cardinality is
months x clients x a handful, which is small by construction. So there is one rule, one
scan, and no per-row Python.

---

**Role projection is applied here, at the aggregation boundary (D50).**

R11(b) makes withholding money a serializer's job, but an aggregate has no serializer -- it
is a dict. So the barrier moves one level down: a ``ReportViewer`` that cannot see money
means the money is **never computed**, not computed and then stripped. Phase 6 already paid
for the difference, when an Admin's serialised payload reached ``IssueActivity`` and leaked
the value to every Member of the project. A dict that has been through a stripping step
looks identical to one that never held the key, right up until somebody logs it.
"""

# Python imports
from decimal import ROUND_HALF_UP, Decimal

# Django imports
from django.db.models import Count, DecimalField, F, Q, Sum, Value
from django.db.models.functions import Coalesce

# Module imports
from plane.db.models import (
    ServiceBillingType,
    ServiceContract,
    ServiceHourLedgerEntry,
    ServiceLedgerEntryType,
    ServiceLog,
)
from plane.utils.service_billing import ReportBucket, RevenueOrigin, report_bucket_from
from plane.utils.service_log_time import ZERO_HOURS, format_hours, quantize_hours
from plane.utils.service_money import ZERO_MONEY, format_money, quantize_money
from plane.utils.service_reports_filters import (
    CompetenceBasis,
    ServiceLogFilterSet,
    competence_index,
    format_competence,
)

_BILL = ServiceBillingType.BillingRoute.BILL_AMOUNT
_FREE = ServiceBillingType.BillingRoute.NON_BILLABLE

_HOURS_FIELD = DecimalField(max_digits=10, decimal_places=4)
_MONEY_FIELD = DecimalField(max_digits=12, decimal_places=2)


# ---------------------------------------------------------------------------
# Who is asking -- D50
# ---------------------------------------------------------------------------


class ReportViewer:
    """What one role is allowed to have computed for it. R11, D50, D57.

    Not a boolean, because there are **three** audiences and two of them differ in the hour
    columns rather than in the money. Collapsing that into ``can_see_amounts`` is what would
    make the client portal reach for a flag that does not fit and invent its own filtering
    in a view.

    ``hour_fields`` is the projection R11's table describes:

    * an **Admin** sees everything, money included;
    * a **Member** sees all three hour quantities and no money -- but *does* see commercial
      state, per D57, because the settled route and the deviation reveal no value and a
      technician who cannot see that a contract expired cannot warn anybody;
    * a **Guest** -- the client -- sees ``equivalent_hours`` and ``debited_hours`` only.
      Never ``logged_hours`` and never ``raw_duration_minutes``, because the gap between
      logged and equivalent *is* the multiplier, and R11(c) warns that showing it under the
      wrong label turns a contractual rule into an accusation of inflated hours.
    """

    ADMIN = "admin"
    MEMBER = "member"
    GUEST = "guest"

    _HOUR_FIELDS = {
        ADMIN: ("logged_hours", "equivalent_hours", "debited_hours"),
        MEMBER: ("logged_hours", "equivalent_hours", "debited_hours"),
        GUEST: ("equivalent_hours", "debited_hours"),
    }

    _MONEY = {ADMIN: True, MEMBER: False, GUEST: False}

    def __init__(self, role):
        if role not in self._HOUR_FIELDS:
            raise ValueError(f"unknown report viewer role: {role}")

        self.role = role

    @classmethod
    def admin(cls):
        return cls(cls.ADMIN)

    @classmethod
    def member(cls):
        return cls(cls.MEMBER)

    @classmethod
    def guest(cls):
        """The client's projection. **Built and tested here, routed by Phase 8 (D55).**

        The portal is Phase 8 and this phase mounts no route for it. The projection lives
        here anyway because R11(b) makes the field allowlist a domain concern: leaving Phase
        8 to rediscover which columns leak is how the rule becomes a decision taken in a
        template. Phase 8 inherits the criterion, and only has to expose it.
        """
        return cls(cls.GUEST)

    @property
    def hour_fields(self):
        return self._HOUR_FIELDS[self.role]

    @property
    def can_see_money(self):
        return self._MONEY[self.role]

    def __eq__(self, other):
        return isinstance(other, ReportViewer) and other.role == self.role

    def __repr__(self):
        return f"ReportViewer({self.role!r})"


# ---------------------------------------------------------------------------
# Buckets -- a number and the filter that produced it
# ---------------------------------------------------------------------------


def _hour_aggregates(viewer):
    """``Sum`` for each hour column this viewer may see, coalesced to a typed zero.

    ``Value(ZERO_HOURS)`` with an explicit ``output_field`` rather than ``Value(0)``: an
    integer zero lets Django infer the output type, and an inferred type on a decimal sum is
    how a total comes back as a float. Section 4b forbids a float anywhere near these
    numbers. Same pattern ``issue_service_log_totals`` established.
    """
    return {
        field: Coalesce(Sum(field), Value(ZERO_HOURS), output_field=_HOURS_FIELD)
        for field in viewer.hour_fields
    }


def _money_aggregate():
    """``Sum`` over the **persisted** ``amount`` column.

    Never ``hours * rate``. The rounding happened once, per work log, when the value was
    derived (section 4b); summing rounded products and rounding a sum of products are
    different numbers, and only the first matches the lines a client will check. Acceptance
    criterion 13, "nenhum centavo perdido ou criado".
    """
    return {"amount": Coalesce(Sum("amount"), Value(ZERO_MONEY), output_field=_MONEY_FIELD)}


def bucket(*, viewer, filters, hours=None, amount=None, entries=0, drill_down=None, **extra):
    """One reportable number, with the descriptor that produced it. D49.

    ``filters`` is what makes acceptance criterion 8 and criterion 1 the same guarantee
    rather than two: the drill-down replays this exact descriptor, so the list cannot
    disagree with the number above it.

    **``filters=None`` is how a bucket says it has no work log list, and it is deliberate
    (D56).** A billed overage is the settlement of a deficit, not a sum of priced work logs --
    the logs that produced the deficit debited the pool and carry ``amount = 0`` by check
    constraint, so no list of them adds up to it. Emitting a descriptor anyway, even one
    correctly flagged undrillable, means the key exists and something will eventually follow
    it: the first version of this function did exactly that and the property test caught a
    bucket reporting zero hours while pointing at every row in the month. So an undrillable
    bucket carries ``drill_down`` instead, naming where the explanation actually lives.

    Money is **absent** for a viewer who may not see it, not zero. R11(b) forbids sending a
    number the caller may not have, and a zero is a number -- one that would additionally be
    a lie, since the work usually is worth something.
    """
    if (filters is None) == (drill_down is None):
        raise ValueError(
            "a bucket needs exactly one of `filters` (a work log selection) or `drill_down` "
            "(where to look instead) -- see D56"
        )

    payload = {"entries": entries}

    if filters is not None:
        payload["filters"] = filters.to_params()
    else:
        payload["drill_down"] = drill_down

    for field, value in (hours or {}).items():
        quantized = quantize_hours(value or ZERO_HOURS)
        payload[field] = str(quantized)
        payload[f"{field}_display"] = format_hours(quantized)

    if amount is not None and viewer.can_see_money:
        quantized = quantize_money(amount or ZERO_MONEY)
        payload["amount"] = str(quantized)
        payload["amount_display"] = format_money(quantized)

    payload.update(extra)

    return payload


# ---------------------------------------------------------------------------
# Competency series
# ---------------------------------------------------------------------------


def _competence_group(filterset):
    """The ``values()`` keys that identify a competency, per the descriptor's basis. D47.

    Two shapes, because the two bases live in different columns, and this is the function
    that keeps that difference from leaking into every caller. For contract consumption the
    competency is the **period's**, which already carries D31's clamp; grouping such a
    series by ``worked_on`` would file a retroactive work log in a month the contract never
    had, which is acceptance criterion 7's failure exactly.
    """
    if filterset.competence_basis == CompetenceBasis.DEBITED_PERIOD:
        return ("debited_period__competence_year", "debited_period__competence_month")

    return ("_year", "_month")


def _annotate_competence(rows, filterset):
    """Add whatever the group keys need. Only the ``worked_on`` basis needs annotating."""
    if filterset.competence_basis == CompetenceBasis.DEBITED_PERIOD:
        return rows

    from django.db.models.functions import ExtractMonth, ExtractYear

    return rows.annotate(_year=ExtractYear("worked_on"), _month=ExtractMonth("worked_on"))


def _competence_of(row, filterset):
    keys = _competence_group(filterset)
    return (row[keys[0]], row[keys[1]])


def hours_series(workspace_id, filterset, viewer):
    """Hours per competency, as one ``GROUP BY``. Sections 1 and 1b.

    The spine of the consumption chart. Which column decides the month comes from the
    descriptor, so a contract series and an ad-hoc series are the same code with different
    descriptors rather than two functions that could drift apart.

    Every bucket carries a descriptor narrowed to its own competency, so clicking a bar
    reaches exactly the rows the bar was drawn from.
    """
    rows = _annotate_competence(filterset.queryset(workspace_id), filterset)

    grouped = (
        rows.values(*_competence_group(filterset))
        .annotate(entries=Count("id"), **_hour_aggregates(viewer))
        .order_by(*_competence_group(filterset))
    )

    series = []

    for row in grouped:
        year, month = _competence_of(row, filterset)

        if year is None:
            # A work log with no debited period, reached on the period basis. Not a
            # competency, so it cannot be a point on the series -- and silently folding it
            # into some month would be inventing one.
            continue

        series.append(
            bucket(
                viewer=viewer,
                filters=filterset.narrow(
                    competence_from=(year, month), competence_to=(year, month)
                ),
                hours={field: row[field] for field in viewer.hour_fields},
                entries=row["entries"],
                competence=format_competence((year, month)),
            )
        )

    return series


def revenue_series(workspace_id, filterset, viewer):
    """Revenue per competency, split by the four origins of D36. Sections 1b and 2.

    **ADMIN only in practice**, because every number in it is money; the viewer is still a
    parameter so the barrier is one place rather than a caller's memory, and a non-Admin
    gets the hours with no amounts rather than a 403 from deep inside an aggregation.

    Grouped by the classification inputs and classified afterwards -- see the module
    docstring. The two log-based origins come from ``ServiceLog``; the two overage origins
    are **ledger rows**, and each carries its own competency rule, which is why they are
    three separate queries and not one union:

    * a work log belongs to the month of ``worked_on`` (R7);
    * a contract overage belongs to the **period's** competency;
    * an allowance overage belongs to the month the allowance was **closed**, because an
      allowance has no competency of its own.
    """
    series = {}

    def point(year, month):
        key = (year, month)
        if key not in series:
            series[key] = {
                origin: {"hours": ZERO_HOURS, "amount": ZERO_MONEY, "entries": 0}
                for origin in (
                    RevenueOrigin.STANDALONE_LOG,
                    RevenueOrigin.OUT_OF_SCOPE_LOG,
                    RevenueOrigin.CONTRACT_OVERAGE,
                    RevenueOrigin.ALLOWANCE_OVERAGE,
                )
            }
        return series[key]

    # ---- the two log origins -------------------------------------------------------
    billed = filterset.narrow(settled_routes=(_BILL,))
    rows = _annotate_competence(billed.queryset(workspace_id), billed)

    grouped = rows.values(
        "_year",
        "_month",
        "project__service_client_id",
        "route_deviation_reason",
        "pricing_failure_reason",
    ).annotate(
        entries=Count("id"),
        hours=Coalesce(Sum("equivalent_hours"), Value(ZERO_HOURS), output_field=_HOURS_FIELD),
        **_money_aggregate(),
    )

    contracted = _contracted_clients_by_competence(workspace_id)

    for row in grouped:
        year, month = row["_year"], row["_month"]
        client_id = row["project__service_client_id"]

        classified = report_bucket_from(
            settled_billing_route=_BILL,
            route_deviation_reason=row["route_deviation_reason"],
            pricing_failure_reason=row["pricing_failure_reason"],
            client_has_contract=client_id in contracted.get((year, month), set()),
            has_client=client_id is not None,
        )

        if classified in (
            ReportBucket.NOT_REVENUE,
            ReportBucket.INTERNAL_WORK,
            ReportBucket.REGISTRATION_PENDENCY,
        ):
            continue

        target = point(year, month)[classified]
        target["hours"] += row["hours"]
        target["amount"] += row["amount"]
        target["entries"] += row["entries"]

    # ---- contract overage, by the PERIOD's competency ------------------------------
    for row in _overage_rows(workspace_id, filterset, by_allowance=False):
        target = point(row["year"], row["month"])[RevenueOrigin.CONTRACT_OVERAGE]
        target["hours"] += row["hours"]
        target["amount"] += row["amount"]
        target["entries"] += row["entries"]

    # ---- allowance overage, by the CLOSING month -----------------------------------
    for row in _overage_rows(workspace_id, filterset, by_allowance=True):
        target = point(row["year"], row["month"])[RevenueOrigin.ALLOWANCE_OVERAGE]
        target["hours"] += row["hours"]
        target["amount"] += row["amount"]
        target["entries"] += row["entries"]

    return [
        {
            "competence": format_competence(key),
            "origins": {
                origin: _origin_bucket(viewer, filterset, origin, key, totals)
                for origin, totals in origins.items()
            },
            **_series_total(viewer, origins),
        }
        for key, origins in sorted(series.items(), key=lambda item: competence_index(*item[0]))
    ]


def _origin_bucket(viewer, filterset, origin, competence, totals):
    """One origin's bucket, drillable to work logs or explicitly not. D56."""
    filters, drill_down = _origin_bucket_target(filterset, origin, competence)

    return bucket(
        viewer=viewer,
        filters=filters,
        drill_down=drill_down,
        hours={"equivalent_hours": totals["hours"]},
        amount=totals["amount"],
        entries=totals["entries"],
    )


def _series_total(viewer, origins):
    """The competency's total, or nothing at all for a viewer without money."""
    if not viewer.can_see_money:
        return {}

    total = sum((totals["amount"] for totals in origins.values()), ZERO_MONEY)

    return {"total_amount": str(quantize_money(total)), "total_amount_display": format_money(total)}


#: The two origins a work log drill-down can honestly express. D56.
_DRILLABLE_ORIGINS = (RevenueOrigin.STANDALONE_LOG, RevenueOrigin.OUT_OF_SCOPE_LOG)

#: Where the two overage origins are explained instead. D56.
_OVERAGE_DRILL_DOWN = {
    RevenueOrigin.CONTRACT_OVERAGE: "contract_period_statement",
    RevenueOrigin.ALLOWANCE_OVERAGE: "allowance_statement",
}


def _origin_bucket_target(filterset, origin, competence):
    """``(filters, drill_down)`` for one origin bucket -- exactly one of them set. D56.

    The two log origins narrow through ``revenue_origins``, which applies the very expression
    D48's differential covers, so a drill-down cannot classify differently from the chart it
    was clicked on.

    The two overage origins get ``None`` for the descriptor and a named destination instead,
    because there is no work log list that sums to a settled deficit.
    """
    if origin in _DRILLABLE_ORIGINS:
        narrowed = filterset.narrow(
            competence_from=competence,
            competence_to=competence,
            settled_routes=(_BILL,),
            revenue_origins=(origin,),
        )
        return narrowed, None

    return None, {"kind": _OVERAGE_DRILL_DOWN[origin], "competence": format_competence(competence)}


def _contracted_clients_by_competence(workspace_id):
    """``{(year, month): {client_id}}`` for every competency any contract covers.

    One query over a small table, then the months are expanded in Python. Whether a client
    held a contract is per-competency -- a contract that ended in June makes June out of
    scope and July ad-hoc -- so a single set for a whole range would misclassify the months
    on either side of every renewal.

    Overlap, not containment, matching ``_clients_with_a_contract_covering``: a contract that
    ended mid-month still covered part of that competency.
    """
    by_competence = {}

    for contract in ServiceContract.objects.filter(workspace_id=workspace_id).values(
        "service_client_id", "starts_on", "ends_on"
    ):
        first = competence_index(contract["starts_on"].year, contract["starts_on"].month)
        last = competence_index(contract["ends_on"].year, contract["ends_on"].month)

        for index in range(first, last + 1):
            key = (index // 12, index % 12 + 1)
            by_competence.setdefault(key, set()).add(contract["service_client_id"])

    return by_competence


def _overage_rows(workspace_id, filterset, *, by_allowance):
    """Billed overage grouped by its own competency rule, in one query per origin."""
    entries = ServiceHourLedgerEntry.objects.filter(
        workspace_id=workspace_id,
        entry_type=ServiceLedgerEntryType.OVERAGE_BILLED,
        amount__isnull=False,
    )

    if by_allowance:
        entries = entries.filter(allowance__isnull=False)

        if filterset.service_client_ids:
            entries = entries.filter(
                allowance__project__service_client_id__in=list(filterset.service_client_ids)
            )

        from django.db.models.functions import ExtractMonth, ExtractYear

        grouped = (
            entries.annotate(
                _year=ExtractYear("allowance__closed_at"),
                _month=ExtractMonth("allowance__closed_at"),
            )
            .values("_year", "_month")
            .annotate(
                entries=Count("id"),
                hours=Coalesce(Sum("hours"), Value(ZERO_HOURS), output_field=_HOURS_FIELD),
                **_money_aggregate(),
            )
        )
        year_key, month_key = "_year", "_month"
    else:
        entries = entries.filter(period__isnull=False)

        if filterset.service_client_ids:
            entries = entries.filter(
                contract__service_client_id__in=list(filterset.service_client_ids)
            )

        grouped = entries.values(
            "period__competence_year", "period__competence_month"
        ).annotate(
            entries=Count("id"),
            hours=Coalesce(Sum("hours"), Value(ZERO_HOURS), output_field=_HOURS_FIELD),
            **_money_aggregate(),
        )
        year_key, month_key = "period__competence_year", "period__competence_month"

    window = _competence_window(filterset)

    rows = []
    for row in grouped:
        year, month = row[year_key], row[month_key]

        if year is None:
            # An allowance billed but never stamped with a closing date. Cannot be placed in
            # a month, so it is left out of the series rather than guessed into one.
            continue

        if window is not None and not window[0] <= competence_index(year, month) <= window[1]:
            continue

        rows.append(
            {
                "year": year,
                "month": month,
                "hours": row["hours"],
                "amount": row["amount"],
                "entries": row["entries"],
            }
        )

    return rows


def _competence_window(filterset):
    """The descriptor's competency range as an index pair, or ``None`` for an open range.

    Applied in Python for the ledger queries because their competency lives in three
    different shapes -- a period's two columns, an allowance's closing timestamp -- and the
    grouped result is at most one row per month. Filtering a handful of rows in Python is not
    the loop this module refuses; the loop it refuses is one over work logs.
    """
    if filterset.competence_from is None and filterset.competence_to is None:
        return None

    low = (
        competence_index(*filterset.competence_from)
        if filterset.competence_from is not None
        else -(10**9)
    )
    high = (
        competence_index(*filterset.competence_to)
        if filterset.competence_to is not None
        else 10**9
    )

    return (low, high)


# ---------------------------------------------------------------------------
# Distributions
# ---------------------------------------------------------------------------

#: The dimensions a distribution can be cut by, and the columns each one needs.
#:
#: A table rather than a free-form field name, because a caller passing an arbitrary lookup
#: could reach ``project__service_client__notes`` -- or a money column for a Member. The
#: allowlist is the same defence the export schema uses.
DISTRIBUTION_DIMENSIONS = {
    "hour_type": ("hour_type_id", "hour_type__name"),
    "billing_type": ("billing_type_id", "billing_type__name"),
    "author": ("author_id", "author__display_name"),
    "project": ("project_id", "project__name"),
    "service_client": ("project__service_client_id", "project__service_client__name"),
}

#: Which filter field a distribution bucket narrows, per dimension. This is what makes the
#: slice of a pie chart clickable: the bucket's descriptor selects exactly its own slice.
_DIMENSION_FILTER = {
    "hour_type": "hour_type_ids",
    "billing_type": "billing_type_ids",
    "author": "author_ids",
    "project": "project_ids",
    "service_client": "service_client_ids",
}


def _slice_descriptor(filterset, dimension, value):
    """The descriptor that selects exactly one slice of a distribution.

    ``value`` is ``None`` only for ``service_client``, the one nullable dimension: a project
    with no client is internal work. That slice needs ``without_service_client`` rather than
    an empty id list, because narrowing with nothing selects *everything* -- the bucket would
    report its own hours while pointing at the whole window, and clicking it would show a
    list bearing no relation to the number. The property test caught precisely that, so the
    fallback is a named error now rather than a silent widening.
    """
    if value is not None:
        return filterset.narrow(**{_DIMENSION_FILTER[dimension]: (value,)})

    if dimension == "service_client":
        return filterset.narrow(without_service_client=True)

    raise ValueError(
        f"dimension {dimension!r} produced a null key and has no way to describe that slice"
    )


def distribution(workspace_id, filterset, viewer, *, dimension):
    """Hours, and money for an Admin, cut by one dimension. Sections 1, 1b and 3.

    One ``GROUP BY``, one row per value of the dimension, each carrying the descriptor that
    selects its own slice so the chart is clickable.

    Used for the non-billable breakdown too, which R5 requires to be **by billing type and
    never a single number**: "Garantia" and "Cortesia" are different commercial facts and a
    combined "não faturável: 12h" hides which conversation to have.
    """
    if dimension not in DISTRIBUTION_DIMENSIONS:
        raise ValueError(f"unknown distribution dimension: {dimension}")

    id_field, name_field = DISTRIBUTION_DIMENSIONS[dimension]

    aggregates = dict(_hour_aggregates(viewer))
    if viewer.can_see_money:
        aggregates.update(_money_aggregate())

    grouped = (
        filterset.queryset(workspace_id)
        .values(id_field, name_field)
        .annotate(entries=Count("id"), **aggregates)
        .order_by("-equivalent_hours")
    )

    return [
        bucket(
            viewer=viewer,
            filters=_slice_descriptor(filterset, dimension, row[id_field]),
            hours={field: row[field] for field in viewer.hour_fields},
            amount=row.get("amount"),
            entries=row["entries"],
            **{
                f"{dimension}_id": str(row[id_field]) if row[id_field] else None,
                f"{dimension}_name": row[name_field],
            },
        )
        for row in grouped
    ]


# ---------------------------------------------------------------------------
# Headline totals
# ---------------------------------------------------------------------------


def headline_totals(workspace_id, filterset, viewer):
    """The insight cards above a dashboard. One ``aggregate()``.

    ``average_amount_per_issue`` is **presentation only (D52)**: a quotient of a money total
    by a count is not representable in two decimals, so it is quantized once, here, with
    ``ROUND_HALF_UP``, and it is **never** fed back into any total. The numerator is the
    ``Sum`` of the persisted ``amount`` column, never hours times a rate -- the same rule as
    every other money figure in this module.
    """
    aggregates = dict(_hour_aggregates(viewer))
    aggregates["entries"] = Count("id")
    aggregates["issues"] = Count("issue_id", distinct=True)

    if viewer.can_see_money:
        aggregates.update(_money_aggregate())

    totals = filterset.queryset(workspace_id).aggregate(**aggregates)

    payload = {
        "entries": totals["entries"],
        "issues": totals["issues"],
        "filters": filterset.to_params(),
    }

    for field in viewer.hour_fields:
        quantized = quantize_hours(totals[field] or ZERO_HOURS)
        payload[field] = str(quantized)
        payload[f"{field}_display"] = format_hours(quantized)

    # The average in HOURS is available to everyone who sees hours: it is a workload
    # figure, not a price, and a technician planning capacity needs it.
    if totals["issues"]:
        per_issue = Decimal(totals["equivalent_hours"] or ZERO_HOURS) / Decimal(totals["issues"])
        payload["average_equivalent_hours_per_issue"] = str(
            per_issue.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        )

    if viewer.can_see_money:
        amount = quantize_money(totals["amount"] or ZERO_MONEY)
        payload["amount"] = str(amount)
        payload["amount_display"] = format_money(amount)

        if totals["issues"]:
            average = Decimal(amount) / Decimal(totals["issues"])
            payload["average_amount_per_issue"] = str(quantize_money(average))
            payload["average_amount_per_issue_display"] = format_money(average)

    return payload


# ---------------------------------------------------------------------------
# Non-billable -- R5
# ---------------------------------------------------------------------------


def non_billable_breakdown(workspace_id, filterset, viewer):
    """Non-billable work, **by billing type**. R5, acceptance criterion 2.

    A function of its own rather than a caller remembering to pass ``settled_routes``,
    because R5's requirement is easy to satisfy accidentally wrongly: a single
    "não faturável: 12h" number is the failure, and it is what you get by filtering the
    route and forgetting the dimension. Here the dimension is not optional.

    Garantia and Cortesia are different commercial facts -- one is an obligation being
    honoured, the other is goodwill being spent -- and which one it was decides whether
    anybody should be doing something about it.
    """
    return distribution(
        workspace_id,
        filterset.narrow(settled_routes=(_FREE,)),
        viewer,
        dimension="billing_type",
    )
