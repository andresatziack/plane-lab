# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""One filter descriptor, three consumers. Decision D49, sections 4 and 8 of Phase 9.

Section 4 asks for global filters, section 8 asks that clicking any total reaches the
work logs behind it, and criterion 1 asks that the chart agrees with the sum. Written as
three features that is three chances to disagree; written as **one object** it is zero,
because the number and the list are literally the same query.

So every bucket in every dashboard payload carries the descriptor that produced it, and:

* the **aggregate** is ``Sum`` over ``filterset.queryset()``;
* the **drill-down** is ``filterset.queryset()`` paginated;
* the **export** is ``filterset`` serialised into ``ExporterHistory.filters``.

The test this structure enables is a property test rather than a handful of assertions:
feed every bucket's descriptor back through the drill-down and assert the direct sum
equals the bucket. A new bucket arrives covered by construction, and a bucket whose
descriptor lies fails immediately.

---

**The competency basis is part of the descriptor, and that is D47 made structural.**

There are two different meanings of "which month is this" in this domain, and picking the
wrong one silently breaks acceptance criterion 7:

* work billed as ad-hoc belongs to the month of ``worked_on`` -- the service date (R7);
* work debited to a contract belongs to the competency of its **period**, which is *not*
  the same thing, because D31 makes a work log dated before the contract's vigency debit
  the **first** period. Its ``worked_on`` month has no period at all.

Grouping contract consumption by ``TruncMonth(worked_on)`` would therefore drop those
hours into a month the contract never had, which is exactly the case D31 created. Rather
than leave that to whoever writes each query, ``competence_basis`` names it in the
descriptor, so a bucket cannot be built without having chosen -- and the choice travels
with the bucket into the drill-down, where it has to match or the totals diverge.
"""

# Python imports
from dataclasses import dataclass, field, replace
from datetime import date
from uuid import UUID

# Django imports
from django.db.models import F, IntegerField
from django.db.models.functions import Cast

# Module imports
from plane.db.models import ServiceBillingType, ServiceLog

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ServiceReportFilterError(Exception):
    """A filter that could not be parsed. Carries an UPPER_SNAKE code for the API.

    Same shape as ``ServicePoolValidationError`` and ``ServicePricingValidationError``: the
    view turns ``code`` into a 400 body and the frontend translates it. A filter the user
    typed wrong is a 400 with a name, never a 500 with a traceback.
    """

    def __init__(self, code, detail=None):
        self.code = code
        self.detail = detail or {}
        super().__init__(code)


# ---------------------------------------------------------------------------
# Competency
# ---------------------------------------------------------------------------


class CompetenceBasis:
    """Which column decides the month a quantity belongs to. See the module docstring.

    Two values and no default that "usually works": a caller that has not thought about
    which one it needs is a caller about to break criterion 7.
    """

    #: The service date. For ad-hoc revenue and for any operational view of work logs.
    WORKED_ON = "worked_on"

    #: The competency of the debited contract period, which already carries D31's clamp.
    #: The **only** correct basis for contract consumption.
    DEBITED_PERIOD = "debited_period"

    ALL = (WORKED_ON, DEBITED_PERIOD)


def competence_index(year, month):
    """A ``(year, month)`` pair as a single sortable integer.

    Comparing competencies as a pair needs either a composite SQL comparison or this. The
    integer is used identically in Python and in the query, so a range that includes
    December of one year and January of the next cannot behave differently in the filter
    and in the assertion. Mirrors ``ServiceContractPeriod.competence_index``.
    """
    return int(year) * 12 + (int(month) - 1)


def parse_competence(raw, *, field_name):
    """``"2026-03"`` as a ``(2026, 3)`` tuple, or a named 400.

    Accepts the tuple/list form too, so a descriptor that has already been parsed can be
    round-tripped without special-casing.
    """
    if raw is None or raw == "":
        return None

    if isinstance(raw, (tuple, list)):
        if len(raw) != 2:
            raise ServiceReportFilterError("INVALID_COMPETENCE", {"field": field_name})
        year, month = raw
    else:
        parts = str(raw).split("-")
        if len(parts) != 2:
            raise ServiceReportFilterError("INVALID_COMPETENCE", {"field": field_name})
        year, month = parts

    try:
        year = int(year)
        month = int(month)
    except (TypeError, ValueError) as error:
        raise ServiceReportFilterError("INVALID_COMPETENCE", {"field": field_name}) from error

    if not 1 <= month <= 12:
        raise ServiceReportFilterError("INVALID_COMPETENCE", {"field": field_name})

    # A four digit year, so that "26-3" fails loudly instead of resolving to the year 26
    # and producing an empty report that looks like "no work this month".
    if not 1000 <= year <= 9999:
        raise ServiceReportFilterError("INVALID_COMPETENCE", {"field": field_name})

    return (year, month)


def format_competence(competence):
    """A ``(year, month)`` tuple as ``"YYYY-MM"``. The inverse of ``parse_competence``."""
    if competence is None:
        return None

    year, month = competence
    return f"{year:04d}-{month:02d}"


def _parse_uuids(raw, *, field_name):
    """A comma separated list, a real list, or nothing, as a tuple of ``UUID``."""
    if raw is None or raw == "":
        return ()

    values = raw.split(",") if isinstance(raw, str) else list(raw)

    parsed = []
    for value in values:
        if value in (None, ""):
            continue
        try:
            parsed.append(value if isinstance(value, UUID) else UUID(str(value)))
        except (TypeError, ValueError) as error:
            raise ServiceReportFilterError(
                "INVALID_UUID_FILTER", {"field": field_name, "value": str(value)}
            ) from error

    # Sorted and deduplicated so that two descriptors expressing the same selection are
    # equal, which is what lets the round-trip test compare them directly.
    return tuple(sorted(set(parsed), key=str))


def _parse_choices(raw, *, field_name, allowed):
    """A comma separated list restricted to a known set of codes."""
    if raw is None or raw == "":
        return ()

    values = raw.split(",") if isinstance(raw, str) else list(raw)

    parsed = []
    for value in values:
        if value in (None, ""):
            continue
        if value not in allowed:
            raise ServiceReportFilterError(
                "INVALID_FILTER_VALUE", {"field": field_name, "value": str(value)}
            )
        parsed.append(value)

    return tuple(sorted(set(parsed)))


#: Read from the choice classes rather than restated, so a route or reason added in a later
#: phase becomes filterable without an edit here -- and so a typo in this module cannot
#: invent a code the database will never contain.
_ROUTES = tuple(choice[0] for choice in ServiceBillingType.BillingRoute.choices)
_DEVIATIONS = tuple(choice[0] for choice in ServiceLog.RouteDeviation.choices)
_FAILURES = tuple(choice[0] for choice in ServiceLog.PricingFailure.choices)

#: Only the two **log-based** origins are filterable here. The two overage origins are ledger
#: rows, not work logs, so a work log descriptor that accepted them would promise a list that
#: cannot exist -- see D56 and ``_descriptor_for_origin``.
_REVENUE_ORIGINS = ("standalone_log", "out_of_scope_log")


def _clients_with_a_contract_in(workspace_id, competence):
    """Clients holding a contract covering one competency.

    The same overlap rule ``service_billing._clients_with_a_contract_covering`` uses -- a
    contract that ended mid-month still covered part of that month -- restated against
    competency columns instead of day bounds because that is what a descriptor carries.
    ``None`` competency means no window, and then no client can be established as contracted
    for "whenever", so the set is empty and everything classifies as standalone.
    """
    from plane.db.models import ServiceContract

    if competence is None:
        return set()

    year, month = competence
    first_day = date(year, month, 1)
    last_day = date(year + (month // 12), (month % 12) + 1, 1)

    return set(
        ServiceContract.objects.filter(
            workspace_id=workspace_id, starts_on__lt=last_day, ends_on__gte=first_day
        ).values_list("service_client_id", flat=True)
    )


@dataclass(frozen=True)
class ServiceLogFilterSet:
    """The selection of work logs a number was computed from. D49.

    Frozen, because a bucket's descriptor is a **record of what was already summed**. A
    mutable one could be edited between producing the number and producing the list, which
    is the exact divergence this class exists to make impossible. Narrowing produces a new
    instance through ``narrow()``.

    Every collection field is a sorted tuple rather than a list or a set, so that two
    descriptors describing the same selection compare equal -- which is what makes the
    round-trip test (``from_params(to_params(x)) == x``) meaningful.
    """

    #: Which column decides the competency. See ``CompetenceBasis`` and D47.
    competence_basis: str = CompetenceBasis.WORKED_ON

    #: Inclusive competency window. Either end may be ``None`` for an open range.
    competence_from: tuple | None = None
    competence_to: tuple | None = None

    service_client_ids: tuple = field(default_factory=tuple)
    project_ids: tuple = field(default_factory=tuple)
    issue_ids: tuple = field(default_factory=tuple)
    author_ids: tuple = field(default_factory=tuple)
    hour_type_ids: tuple = field(default_factory=tuple)
    billing_type_ids: tuple = field(default_factory=tuple)

    #: Commercial state, not money (D57), so these are legitimate filters for a Member.
    #:
    #: The tri-state "include non-billable / only non-billable / exclude" of section 4 is
    #: expressed here rather than as its own flag: it is exactly a choice of route set, and
    #: a separate flag could contradict this field with no way to resolve which wins.
    settled_routes: tuple = field(default_factory=tuple)
    route_deviation_reasons: tuple = field(default_factory=tuple)
    pricing_failure_reasons: tuple = field(default_factory=tuple)

    #: Direct targeting, used by drill-downs from a contract or allowance bucket.
    debited_period_ids: tuple = field(default_factory=tuple)
    debited_allowance_ids: tuple = field(default_factory=tuple)

    #: Revenue origins (D36), applied through ``report_bucket_expression``.
    #:
    #: **Needed because "standalone" is not expressible as a conjunction of columns.** A
    #: standalone work log is one with a deviation recorded *or* one on a client holding no
    #: contract for that competency -- a disjunction, and one whose second half is derived
    #: rather than stored. Without this field the origin buckets of the revenue chart would
    #: have no honest drill-down, and criterion 8 would be satisfied only for the buckets
    #: that happened to be simple.
    #:
    #: Requires a **single** competency, enforced below: the contract-coverage half of the
    #: classification is per-month, so a multi-month window would need a different client set
    #: per month and one query cannot carry both.
    revenue_origins: tuple = field(default_factory=tuple)

    #: ``True`` restricts to work logs that debited **no** pool at all -- neither a period
    #: nor an allowance. Needed by the operational view, where "billed ad-hoc" and "debited
    #: somewhere" are different populations that a route filter alone cannot separate,
    #: because a non-billable log also debits nothing.
    without_pool_origin: bool = False

    #: ``True`` restricts to work logs on projects with **no** client -- internal work.
    #:
    #: ``service_client_id`` is the one nullable dimension a distribution can be cut by, so
    #: without this the "internal work" slice of a per-client chart had no way to describe
    #: itself and fell back to the unnarrowed descriptor -- a bucket claiming its own hours
    #: while pointing at every row in the window. The property test found exactly that, which
    #: is the argument for having written it as a property rather than as examples.
    without_service_client: bool = False

    # ----------------------------------------------------------------- validation

    def __post_init__(self):
        if self.competence_basis not in CompetenceBasis.ALL:
            raise ServiceReportFilterError(
                "INVALID_COMPETENCE_BASIS", {"value": self.competence_basis}
            )

        if (
            self.competence_from is not None
            and self.competence_to is not None
            and competence_index(*self.competence_from) > competence_index(*self.competence_to)
        ):
            raise ServiceReportFilterError(
                "COMPETENCE_RANGE_IS_INVERTED",
                {
                    "competence_from": format_competence(self.competence_from),
                    "competence_to": format_competence(self.competence_to),
                },
            )

        if self.revenue_origins and self.competence_from != self.competence_to:
            # Refused rather than silently approximated. Whether a client held a contract is
            # a per-competency fact, so classifying a multi-month window would need one
            # client set per month; picking any single set would misclassify every month on
            # the other side of a renewal, and the report would look right.
            raise ServiceReportFilterError(
                "REVENUE_ORIGIN_NEEDS_ONE_COMPETENCE",
                {
                    "competence_from": format_competence(self.competence_from),
                    "competence_to": format_competence(self.competence_to),
                },
            )

    # ----------------------------------------------------------------- narrowing

    def narrow(self, **overrides):
        """A copy with extra constraints. How a bucket descriptor is built.

        **Targeting a period switches the basis, and drops nothing silently.** A bucket of
        one contract period is defined by that period, and D31 means some of its work logs
        have a ``worked_on`` outside the period's own month; keeping a ``worked_on`` window
        alongside the period id would filter those out of the drill-down while the
        aggregate still counted them, and the two numbers would disagree by exactly the
        retroactive entries acceptance criterion 7 is about.

        So narrowing to ``debited_period_ids`` clears the competency window and sets the
        basis, because the period **is** the competency. The same holds for an allowance,
        which has no competency at all until it is closed.
        """
        if overrides.get("debited_period_ids"):
            overrides.setdefault("competence_basis", CompetenceBasis.DEBITED_PERIOD)
            overrides.setdefault("competence_from", None)
            overrides.setdefault("competence_to", None)

        if overrides.get("debited_allowance_ids"):
            overrides.setdefault("competence_from", None)
            overrides.setdefault("competence_to", None)

        return replace(self, **overrides)

    # ----------------------------------------------------------------- the query

    def queryset(self, workspace_id, *, queryset=None):
        """The work logs this descriptor selects, in ``workspace_id``.

        The single place any of the three consumers gets its rows. ``workspace_id`` is a
        required argument rather than a field of the descriptor: a descriptor travels to the
        browser and back, and a workspace that arrived from a query parameter would be a
        tenancy boundary decided by the caller. The view resolves it from the URL and the
        permission decorator.
        """
        rows = ServiceLog.objects.filter(workspace_id=workspace_id) if queryset is None else queryset

        rows = self._apply_competence(rows)

        for attribute, lookup in (
            ("service_client_ids", "project__service_client_id__in"),
            ("project_ids", "project_id__in"),
            ("issue_ids", "issue_id__in"),
            ("author_ids", "author_id__in"),
            ("hour_type_ids", "hour_type_id__in"),
            ("billing_type_ids", "billing_type_id__in"),
            ("settled_routes", "settled_billing_route__in"),
            ("route_deviation_reasons", "route_deviation_reason__in"),
            ("pricing_failure_reasons", "pricing_failure_reason__in"),
            ("debited_period_ids", "debited_period_id__in"),
            ("debited_allowance_ids", "debited_allowance_id__in"),
        ):
            values = getattr(self, attribute)
            if values:
                rows = rows.filter(**{lookup: list(values)})

        if self.without_pool_origin:
            rows = rows.filter(debited_period__isnull=True, debited_allowance__isnull=True)

        if self.without_service_client:
            rows = rows.filter(project__service_client__isnull=True)

        if self.revenue_origins:
            rows = self._apply_revenue_origins(rows, workspace_id)

        return rows

    def _apply_revenue_origins(self, rows, workspace_id):
        """Filter by revenue origin, through the expression D48 proved.

        Imported at call time rather than at module level: ``service_billing`` imports the
        pool and pricing layers, and a top-level import here would put this module in the
        middle of that chain for every caller that only wants to filter by project.

        The classification is the **same expression** the exhaustive differential covers, so
        a drill-down cannot classify differently from the chart it was clicked on -- which is
        the whole point of not writing a second ``CASE`` here.
        """
        from plane.utils.service_billing import report_bucket_expression

        contracted = _clients_with_a_contract_in(workspace_id, self.competence_from)

        return rows.annotate(_revenue_origin=report_bucket_expression(contracted)).filter(
            _revenue_origin__in=list(self.revenue_origins)
        )

    def _apply_competence(self, rows):
        """The competency window, resolved according to ``competence_basis``. D47."""
        if self.competence_from is None and self.competence_to is None:
            return rows

        if self.competence_basis == CompetenceBasis.WORKED_ON:
            # R7: the service date. A date range rather than month arithmetic, so the
            # existing (workspace, worked_on) indexes are usable.
            if self.competence_from is not None:
                rows = rows.filter(worked_on__gte=date(*self.competence_from, 1))
            if self.competence_to is not None:
                year, month = self.competence_to
                last_day = date(year + (month // 12), (month % 12) + 1, 1)
                rows = rows.filter(worked_on__lt=last_day)
            return rows

        # D47: the period's own competency, which already carries D31's clamp. Compared as
        # a single integer so that a window crossing a year boundary cannot be evaluated
        # differently here and in an assertion.
        rows = rows.annotate(
            _competence_index=(
                Cast(F("debited_period__competence_year"), IntegerField()) * 12
                + Cast(F("debited_period__competence_month"), IntegerField())
                - 1
            )
        )

        if self.competence_from is not None:
            rows = rows.filter(_competence_index__gte=competence_index(*self.competence_from))
        if self.competence_to is not None:
            rows = rows.filter(_competence_index__lte=competence_index(*self.competence_to))

        return rows

    # ----------------------------------------------------------------- round trip

    def to_params(self):
        """The descriptor as flat query parameters.

        Used for three things that must agree: the URL a chart click navigates to, the body
        the drill-down endpoint receives, and ``ExporterHistory.filters`` -- which is how
        criterion 9 ("o CSV contém os mesmos números da tela") stops being a manual
        comparison and becomes the same descriptor reaching a second consumer.

        Keys with nothing in them are **omitted** rather than sent empty, so a descriptor
        with no filters serialises to ``{}`` and a URL stays readable.
        """
        params = {"competence_basis": self.competence_basis}

        if self.competence_from is not None:
            params["competence_from"] = format_competence(self.competence_from)
        if self.competence_to is not None:
            params["competence_to"] = format_competence(self.competence_to)

        for attribute in (
            "service_client_ids",
            "project_ids",
            "issue_ids",
            "author_ids",
            "hour_type_ids",
            "billing_type_ids",
            "settled_routes",
            "route_deviation_reasons",
            "pricing_failure_reasons",
            "debited_period_ids",
            "debited_allowance_ids",
            "revenue_origins",
        ):
            values = getattr(self, attribute)
            if values:
                params[attribute] = ",".join(str(value) for value in values)

        if self.without_pool_origin:
            params["without_pool_origin"] = "true"

        if self.without_service_client:
            params["without_service_client"] = "true"

        return params

    @classmethod
    def from_export_filters(cls, filters):
        """Parse ``ExporterHistory.filters``, accepting Phase 6's shape as well as this one.

        **Criterion 9 is why this exists.** "O CSV contém os mesmos números da tela" stops
        being a manual comparison the moment the export consumes the *same descriptor* the
        screen did: one filter, two consumers, nothing to reconcile.

        Phase 6 persisted ``{"year": "2026", "month": "3", "service_client_id": "..."}`` and
        those rows are already in the database, with finished files attached. Rather than keep
        two filtering paths -- the failure mode being an old row that silently exports
        everything -- the legacy keys are **translated** into a descriptor and there is one
        path afterwards. A competency of one month on the ``worked_on`` basis is exactly what
        Phase 6 meant by it (R7).
        """
        filters = filters or {}

        if filters.get("year") and filters.get("month"):
            competence = format_competence(
                (int(filters["year"]), int(filters["month"]))
            )
            translated = {
                "competence_basis": CompetenceBasis.WORKED_ON,
                "competence_from": competence,
                "competence_to": competence,
            }

            if filters.get("service_client_id"):
                translated["service_client_ids"] = str(filters["service_client_id"])

            return cls.from_params(translated)

        return cls.from_params(filters)

    @classmethod
    def from_params(cls, params):
        """Parse query parameters into a descriptor. The inverse of ``to_params``.

        Accepts a plain dict or a ``QueryDict``. Unknown keys are ignored rather than
        rejected: a frontend adding a UI-only parameter to the URL must not 400 the report.
        """
        get = params.get

        return cls(
            competence_basis=get("competence_basis") or CompetenceBasis.WORKED_ON,
            competence_from=parse_competence(get("competence_from"), field_name="competence_from"),
            competence_to=parse_competence(get("competence_to"), field_name="competence_to"),
            service_client_ids=_parse_uuids(
                get("service_client_ids"), field_name="service_client_ids"
            ),
            project_ids=_parse_uuids(get("project_ids"), field_name="project_ids"),
            issue_ids=_parse_uuids(get("issue_ids"), field_name="issue_ids"),
            author_ids=_parse_uuids(get("author_ids"), field_name="author_ids"),
            hour_type_ids=_parse_uuids(get("hour_type_ids"), field_name="hour_type_ids"),
            billing_type_ids=_parse_uuids(get("billing_type_ids"), field_name="billing_type_ids"),
            settled_routes=_parse_choices(
                get("settled_routes"), field_name="settled_routes", allowed=_ROUTES
            ),
            route_deviation_reasons=_parse_choices(
                get("route_deviation_reasons"),
                field_name="route_deviation_reasons",
                allowed=_DEVIATIONS,
            ),
            pricing_failure_reasons=_parse_choices(
                get("pricing_failure_reasons"),
                field_name="pricing_failure_reasons",
                allowed=_FAILURES,
            ),
            debited_period_ids=_parse_uuids(
                get("debited_period_ids"), field_name="debited_period_ids"
            ),
            debited_allowance_ids=_parse_uuids(
                get("debited_allowance_ids"), field_name="debited_allowance_ids"
            ),
            revenue_origins=_parse_choices(
                get("revenue_origins"), field_name="revenue_origins", allowed=_REVENUE_ORIGINS
            ),
            without_pool_origin=str(get("without_pool_origin") or "").lower()
            in ("true", "1", "yes"),
            without_service_client=str(get("without_service_client") or "").lower()
            in ("true", "1", "yes"),
        )
