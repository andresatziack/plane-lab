# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Domain logic for the holiday calendar and the classification windows.

Two responsibilities, kept in one module because they are two halves of one question
("what kind of day is this, and what does that hour cost"):

* the configuration invariants -- no overlap at the same priority, and a set of windows
  that covers the whole week;
* the classification engine itself, which turns a date and an optional interval into
  segments.

Pure functions over models, with no dependency on views, serializers or DRF, as
section 6 of the master context requires.

TIMEZONE STRATEGY -- section 5b of the phase brief, which this module closes.

The source of truth is ``Workspace.timezone``. Not the project's: the classification
windows are a commercial parameter of the *workspace* (D16), and resolving against a
project's timezone would let the same worked hour fall in different price bands
depending on which project the work item lives in. And never the *active* timezone --
``TimezoneMixin`` activates the requesting user's per request, and whether an hour is
billed at 1.0 or 2.0 must not depend on who had the tab open.

What makes this cheap: no conversion happens here at all. The work log phase stores
``worked_on`` as a local ``DateField`` and the borders as local ``TimeField``s, and the
windows are local wall clock too, so everything is already in one frame. The workspace
timezone is needed only where an *instant* has to become a local date, which today is
exactly one place -- ``workspace_today`` in ``plane.utils.service_log``, used to reject
a future service date.

KNOWN LIMITATION: DAYLIGHT SAVING TIME.

Classification is wall-clock arithmetic, so a day on which the clock shifts is counted
in wall-clock hours rather than elapsed hours.

This is **configuration-dependent, not geographically impossible**. Brazil abolished DST
in 2019, but ``Workspace.timezone`` accepts any zone in ``pytz.common_timezones``, so an
installation configured to, say, ``Europe/Lisbon`` is exposed.

What actually breaks, concretely:

* On a spring-forward day, the seeded "Fora do expediente" window 18:00->08:00 spans 13
  hours of elapsed time while this module measures the 14 hours of wall clock between
  its borders. On a fall-back day it spans 15 and is measured as 14.
* A work log whose interval crosses the transition therefore gets a segment duration
  wrong by one hour, which flows straight into ``equivalent_hours`` and into the
  invoice. It is a **financial error** -- small, rare, and real.

Fixing it would mean storing instants instead of local dates and times, which would
break the "whole day" semantics that a holiday depends on. The trade was taken
deliberately. ``test_service_calendar_engine.py`` carries a characterization test that
pins the current behaviour on a transition day, so that if someone ever fixes this, the
test says exactly what changed rather than the invoice saying it.
"""

# Python imports
import csv
import datetime
import io
from dataclasses import dataclass

# Module imports
from plane.db.models import (
    COVERABLE_SCOPES,
    DAY_SCOPE_SHORT_LABELS,
    WEEKDAY_SCOPES,
    ServiceClassificationWindow,
    ServiceDayScope,
    ServiceHoliday,
    ServiceHolidayScope,
)
from plane.utils.service_log_time import MINUTES_PER_DAY, minutes_to_clock

# Error codes, in the UPPER_SNAKE style the rest of the feature uses. The frontend maps
# them to translated strings; the API never returns Portuguese.
WINDOWS_OVERLAP_AT_SAME_PRIORITY = "WINDOWS_OVERLAP_AT_SAME_PRIORITY"
WINDOWS_WOULD_NOT_COVER_THE_WEEK = "WINDOWS_WOULD_NOT_COVER_THE_WEEK"
HOLIDAY_ALREADY_COVERED_BY_RECURRING = "HOLIDAY_ALREADY_COVERED_BY_RECURRING"
RECURRING_HOLIDAY_COLLIDES_WITH_SPECIFIC = "RECURRING_HOLIDAY_COLLIDES_WITH_SPECIFIC"
CSV_HEADER_IS_INVALID = "CSV_HEADER_IS_INVALID"
CSV_ROW_IS_INVALID = "CSV_ROW_IS_INVALID"
CSV_DATE_IS_INVALID = "CSV_DATE_IS_INVALID"
CSV_SCOPE_IS_INVALID = "CSV_SCOPE_IS_INVALID"

#: Reason text for an instant no window covers. Surfaced to the technician so a
#: configuration gap reads as a gap rather than as a silent blank.
UNCLASSIFIED_REASON = "Sem janela de classificação configurada"


# ---------------------------------------------------------------------------
# Ranges, in minutes since midnight
# ---------------------------------------------------------------------------


def window_ranges(window):
    """A window as one or two half-open ``(start, end)`` ranges inside a single day.

    A window that crosses midnight is two ranges -- ``(1080, 1440)`` and ``(0, 480)``
    for 18:00->08:00 -- because coverage and overlap are both questions about *one*
    day scope. Splitting here is what lets everything downstream treat every range as
    ordinary and ordered.
    """
    if window.crosses_midnight:
        return ((window.start_minute, MINUTES_PER_DAY), (0, window.end_minute))

    return ((window.start_minute, window.end_minute),)


def _merge_ranges(ranges):
    """Merge overlapping and touching ranges into a minimal sorted list."""
    merged = []

    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    return merged


def _gaps_in_day(ranges):
    """The parts of a day that ``ranges`` leave uncovered, as ``(start, end)`` pairs."""
    gaps = []
    cursor = 0

    for start, end in _merge_ranges(ranges):
        if start > cursor:
            gaps.append((cursor, start))
        cursor = max(cursor, end)

    if cursor < MINUTES_PER_DAY:
        gaps.append((cursor, MINUTES_PER_DAY))

    return gaps


def _ranges_overlap(first, second):
    """Whether two half-open ranges share at least one minute."""
    return first[0] < second[1] and second[0] < first[1]


# ---------------------------------------------------------------------------
# Reading the configuration
# ---------------------------------------------------------------------------


def load_windows(workspace_id):
    """Every active window of a workspace, with its hour type's priority attached.

    One query, and the hour type is joined rather than followed lazily: the engine
    needs ``priority``, ``multiplier`` and ``name`` for each window, and touching the
    descriptor per row would be a query per window.

    ``select_related`` is safe here even though the foreign key is DO_NOTHING, because
    the join is an INNER JOIN on the raw table and never goes through the soft-delete
    filtered manager. A window pointing at a soft deleted hour type simply drops out,
    which is the behaviour that matters: a retired hour type must not keep classifying
    new work.
    """
    return list(
        ServiceClassificationWindow.objects.filter(workspace_id=workspace_id, is_active=True)
        .select_related("hour_type")
        .filter(hour_type__deleted_at__isnull=True, hour_type__is_active=True)
        .order_by("hour_type__priority", "day_scope", "start_minute")
    )


def load_holidays(workspace_id):
    """Every active holiday of a workspace.

    Loaded whole and resolved in Python rather than queried per date. The table holds
    dozens of rows per workspace, and annual recurrence is a month-and-day match that
    would otherwise need a functional index on ``EXTRACT``.

    EXTENSION POINT: this is the obvious thing to cache, keyed by workspace and
    invalidated on any holiday write. Deliberately not cached yet -- the query is
    trivial, and a stale classification cache is a category of bug worth avoiding until
    there is a measurement that asks for it.
    """
    return list(ServiceHoliday.objects.filter(workspace_id=workspace_id, is_active=True))


def holidays_on(holidays, target):
    """The holidays that fall on ``target``, recurrence included."""
    return [holiday for holiday in holidays if holiday.matches(target)]


def day_scopes_for(target, holidays):
    """The day scopes that apply to a date, most specific first.

    A holiday contributes the ``holiday`` scope **in addition to** its calendar
    weekday, never instead of it. Both sets of windows then compete on priority, which
    is what section 2 of the phase brief means by "FERIADO tem a maior prioridade e
    portanto vence sobre a janela do dia da semana" -- the holiday wins because its
    hour type is seeded at priority 10, not because the weekday was discarded.

    That distinction is what keeps the panel's promise open: an hour type at priority 5
    with a night window would outrank even a holiday, without a code change.
    """
    scopes = [WEEKDAY_SCOPES[target.weekday()]]

    if holidays_on(holidays, target):
        scopes.append(ServiceDayScope.HOLIDAY)

    return scopes


# ---------------------------------------------------------------------------
# Configuration invariants
# ---------------------------------------------------------------------------


def find_overlaps_within_day_scope_and_priority(windows):
    """Windows that collide on the same day scope AND the same priority.

    **Scoped by the pair, and that is the whole point.** Two windows only conflict when
    they describe the same kind of day *and* their hour types share a priority, because
    only then is there no rule to decide between them. Monday 08:00-18:00 and Tuesday
    08:00-18:00 both sit at priority 30 and do not conflict at all -- they never apply
    to the same instant. Checking priority alone would reject the seeded configuration.

    Overlap across *different* priorities is not merely allowed, it is how the model
    works: the holiday window covers the whole day on top of the weekday window, and
    priority picks the winner.

    Returns a list of ``(first, second)`` pairs, empty when the configuration is sound.
    """
    buckets = {}

    for window in windows:
        # `hour_type.priority`, not the window's -- priority lives on the hour type,
        # because it is a property of "what kind of hour is this", not of one range.
        key = (window.day_scope, window.hour_type.priority)
        buckets.setdefault(key, []).append(window)

    collisions = []

    for bucket in buckets.values():
        for index, first in enumerate(bucket):
            for second in bucket[index + 1 :]:
                # Two ranges of the *same* window never conflict with each other, and a
                # window is never compared with itself because of the slicing above.
                if any(
                    _ranges_overlap(first_range, second_range)
                    for first_range in window_ranges(first)
                    for second_range in window_ranges(second)
                ):
                    collisions.append((first, second))

    return collisions


def find_coverage_gaps(windows):
    """The parts of the week that no window covers.

    Returns ``{day_scope: [(start_minute, end_minute), ...]}``, containing only the
    scopes that have a gap. An empty dict means the set covers the week and the engine
    can classify every instant.

    Every priority counts towards coverage: an instant is classified as long as *some*
    window contains it, whichever wins.

    This is also what the panel's health indicator renders. A gap that exists but is
    invisible would only ever surface as a blank hour type in the work log form, where
    nobody would connect the two.
    """
    by_scope = {scope: [] for scope in COVERABLE_SCOPES}

    for window in windows:
        if window.day_scope in by_scope:
            by_scope[window.day_scope].extend(window_ranges(window))

    return {scope: gaps for scope in COVERABLE_SCOPES if (gaps := _gaps_in_day(by_scope[scope]))}


def describe_coverage_gaps(gaps):
    """Coverage gaps rendered as readable clock ranges, for the panel and for messages.

    ``{"monday": [(0, 480)]}`` becomes ``{"monday": ["00:00–08:00"]}``.
    """
    return {
        scope: [f"{minutes_to_clock(start)}–{minutes_to_clock(end)}" for start, end in scope_gaps]
        for scope, scope_gaps in gaps.items()
    }


def coverage_report(workspace_id):
    """The health of a workspace's classification configuration.

    Consumed by the panel's health indicator and by the API. Deliberately descriptive
    rather than a bare boolean: "incomplete" on its own gives an admin nothing to act
    on, so this says which day scopes are short and which ranges are missing.
    """
    windows = load_windows(workspace_id)
    gaps = find_coverage_gaps(windows)
    collisions = find_overlaps_within_day_scope_and_priority(windows)

    return {
        "is_complete": not gaps,
        "window_count": len(windows),
        "gaps": describe_coverage_gaps(gaps),
        "overlaps": [
            {
                "day_scope": first.day_scope,
                "priority": first.hour_type.priority,
                "windows": [str(first), str(second)],
                "hour_types": [first.hour_type.name, second.hour_type.name],
            }
            for first, second in collisions
        ],
    }


def validate_window_set(workspace_id, *, was_complete_before):
    """Check the invariants of a workspace's whole window set after a change.

    Called **after** the write and inside its transaction, so a failure rolls the write
    back. It has to be a set-level check rather than a per-row one, and deletion is why:
    removing a window breaks coverage without any row being invalid on its own.

    Two rules, enforced differently on purpose:

    * **Overlap at the same day scope and priority is always refused.** There is no
      transitional excuse for two windows that cannot be decided between.
    * **Coverage is a ratchet: a complete set may never become incomplete.** Requiring
      completeness unconditionally would make it impossible to build the first window of
      a workspace whose configuration was assembled by hand, and impossible to repair an
      already broken one -- the admin would be locked out of the only screen that could
      fix it. A seeded workspace starts complete, so in practice every installation is
      protected from the first day.

    ``was_complete_before`` must be read *before* the write, with
    ``is_window_set_complete``.

    Returns ``None`` when the change is allowed, otherwise an error code.
    """
    windows = load_windows(workspace_id)

    if find_overlaps_within_day_scope_and_priority(windows):
        return WINDOWS_OVERLAP_AT_SAME_PRIORITY

    if was_complete_before and find_coverage_gaps(windows):
        return WINDOWS_WOULD_NOT_COVER_THE_WEEK

    return None


def is_window_set_complete(workspace_id):
    """Whether the workspace currently covers the whole week.

    Read before a write so ``validate_window_set`` can apply the ratchet.
    """
    return not find_coverage_gaps(load_windows(workspace_id))


# ---------------------------------------------------------------------------
# Holiday invariants
# ---------------------------------------------------------------------------


def validate_holiday(workspace_id, *, date, scope, is_recurring, instance=None):
    """Refuse a holiday that would duplicate a day already covered, in either direction.

    The database constraint catches two entries with the same stored date and scope. It
    cannot catch a recurrence collision, because those rows hold *different* dates that
    nonetheless resolve to the same day -- and whether they collide depends on the year.

    **Both directions are checked, because a one-way check just moves the duplicate to
    the other door:**

    * a specific 25/12/2027 when a recurring 25/12 already exists, and
    * a recurring 25/12 when a specific 25/12/2027 already exists.

    Scope is part of the comparison, so a national and a municipal holiday may share a
    day -- which is the point of recording scope at all (D14).

    ``instance`` excludes the row being edited from the comparison. Returns ``None`` when
    allowed, otherwise an error code.
    """
    siblings = ServiceHoliday.objects.filter(workspace_id=workspace_id, scope=scope, is_active=True)

    if instance is not None and instance.pk:
        siblings = siblings.exclude(pk=instance.pk)

    if is_recurring:
        # A recurring entry claims this month and day in every year, so it collides with
        # any sibling -- recurring or not -- that falls on the same month and day.
        collides = siblings.filter(date__month=date.month, date__day=date.day).exists()
        return RECURRING_HOLIDAY_COLLIDES_WITH_SPECIFIC if collides else None

    # A specific entry collides with a recurring sibling on the same month and day...
    if siblings.filter(is_recurring=True, date__month=date.month, date__day=date.day).exists():
        return HOLIDAY_ALREADY_COVERED_BY_RECURRING

    # ...and the same-date case is left to the database constraint, which already covers
    # it and gives a clearer failure than a second check here would.
    return None


def holiday_calendar(workspace_id, year):
    """Every holiday observed in ``year``, with recurrence expanded to real dates.

    Powers the annual calendar view of section 1, which exists so an admin can eyeball
    the year rather than trust a list of rows.

    Deduplicated by ``(date, scope)``: a recurring entry and a specific one can resolve
    to the same observed day, which the engine does not care about but a calendar would
    render twice. Non-recurring entries win the tie, because a specific entry is the more
    deliberate record of that particular year.
    """
    observed = {}

    for holiday in load_holidays(workspace_id):
        if holiday.is_recurring:
            try:
                resolved = datetime.date(year, holiday.date.month, holiday.date.day)
            except ValueError:
                # 29 February in a non-leap year: the day does not exist, so there is
                # nothing to observe.
                continue
        elif holiday.date.year == year:
            resolved = holiday.date
        else:
            continue

        key = (resolved, holiday.scope)

        if key not in observed or not holiday.is_recurring:
            observed[key] = {
                "id": str(holiday.id),
                "name": holiday.name,
                "date": resolved,
                "scope": holiday.scope,
                "is_recurring": holiday.is_recurring,
            }

    return sorted(observed.values(), key=lambda entry: (entry["date"], entry["name"]))


# ---------------------------------------------------------------------------
# Hour type deletion guard
# ---------------------------------------------------------------------------


def hour_type_has_windows(hour_type):
    """Whether an hour type still has classification windows.

    Wired into ``validate_catalog_delete``, at the extension point the catalogue phase
    documented for exactly this. Deleting an hour type that still has windows would tear
    a hole in the week's coverage without any window being individually invalid --
    precisely the failure the coverage ratchet exists to prevent, arriving through a
    different door.

    ``all_objects``, matching how the rest of the feature counts references: the database
    foreign key does not care that a window is flagged deleted.
    """
    return ServiceClassificationWindow.all_objects.filter(hour_type_id=hour_type.pk).exists()



# ---------------------------------------------------------------------------
# The classification engine
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Segment:
    """One stretch of time with a single classification.

    The return shape of the engine, matching the ``Segmento`` tuple of section 3 of the
    phase brief: ``(data, hora_inicio, hora_fim, duracao_minutos, tipo_de_hora, motivo)``.

    Defined here rather than in ``plane.utils.service_log`` -- where it started life --
    so the dependency runs one way: the work log module imports the engine, and the
    engine imports nothing from it. ``service_log`` re-exports this name, so existing
    callers and tests are unaffected.

    ``worked_on`` is the segment's **own** date, derived from its real position on the
    timeline rather than copied from the start of the entry. Required by R7: a log from
    31/01 23:00 to 01/02 01:00 straddles two billing competencies, and using the start
    date for both would misbill the month boundary.

    ``suggested_hour_type`` is ``None`` when nothing could be proposed -- either a
    weekday in duration mode, where R10 says the technician must choose, or an instant
    no window covers. ``reason`` says which.

    ``raw_duration_minutes`` is raw. The R2 rounding is applied per segment by
    ``build_batch_rows`` in the work log module, because rounding is that phase's
    business and each segment is an independently billable unit (D17).
    """

    worked_on: datetime.date
    start_time: datetime.time | None
    end_time: datetime.time | None
    raw_duration_minutes: int
    suggested_hour_type: object | None = None
    reason: str = ""


def _minute_to_time(minute):
    """Minutes since midnight as a ``time``. 1440 wraps to 00:00, ending the day."""
    normalized = minute % MINUTES_PER_DAY
    return datetime.time(hour=normalized // 60, minute=normalized % 60)


def _describe(window, holidays_today):
    """Why a window won, in the words section 7 of the phase brief asks for.

    Examples it produces: ``Feriado: Natal``, ``Fora do expediente: 18:00–08:00``,
    ``Domingos e feriados: dom o dia inteiro``.

    The holiday name is used when the *winning* window is a holiday window, because that
    is the fact that explains the price. A holiday that lost to a higher-priority window
    is not mentioned -- it did not decide anything.
    """
    if window.day_scope == ServiceDayScope.HOLIDAY and holidays_today:
        return f"Feriado: {', '.join(holiday.name for holiday in holidays_today)}"

    label = DAY_SCOPE_SHORT_LABELS.get(window.day_scope, window.day_scope)

    if window.is_all_day:
        return f"{window.hour_type.name}: {label} o dia inteiro"

    return f"{window.hour_type.name}: {window.start_clock}–{window.end_clock}"


def _resolve_instant(absolute_minute, *, worked_on, windows, holidays):
    """The winning window at one instant, plus its explanation.

    Returns ``(window_or_None, reason)``.

    The winner is the candidate whose hour type has the **lowest priority** number. Ties
    are broken deterministically by start border and then hour type name -- a tie only
    happens when the configuration has an overlap at the same day scope and priority,
    which validation refuses, but the engine still has to be a function rather than a
    coin flip.
    """
    day_offset, minute_of_day = divmod(absolute_minute, MINUTES_PER_DAY)
    day = worked_on + datetime.timedelta(days=day_offset)

    holidays_today = holidays_on(holidays, day)
    scopes = day_scopes_for(day, holidays)

    candidates = [
        window
        for window in windows
        if window.day_scope in scopes and window.contains_minute(minute_of_day)
    ]

    if not candidates:
        # TOTAL BY DESIGN: never raise. See `classify`.
        return None, UNCLASSIFIED_REASON

    winner = min(
        candidates,
        key=lambda window: (window.hour_type.priority, window.start_minute, window.hour_type.name),
    )

    return winner, _describe(winner, holidays_today)


def _breakpoints(start_absolute, end_absolute, *, worked_on, windows, holidays):
    """Every absolute minute inside the span where the classification could change.

    Walking breakpoints rather than minute by minute. The set is the union of:

    * the borders of every window that applies to any day the span touches, and
    * every midnight in the span, because the day scope itself changes there -- a new
      weekday, or a holiday starting or ending.

    Sorted, deduplicated, and strictly inside the span. Note what is *not* here: a
    midnight is a candidate, not a cut. If the classification is the same on both sides
    the runs merge, which is what keeps "Seg 22:00 → Ter 01:00" a single segment.
    """
    candidates = {start_absolute, end_absolute}

    first_day = start_absolute // MINUTES_PER_DAY
    last_day = (end_absolute - 1) // MINUTES_PER_DAY

    for day_offset in range(first_day, last_day + 1):
        day_start = day_offset * MINUTES_PER_DAY
        candidates.add(day_start)

        day = worked_on + datetime.timedelta(days=day_offset)
        scopes = day_scopes_for(day, holidays)

        for window in windows:
            if window.day_scope not in scopes:
                continue
            for range_start, range_end in window_ranges(window):
                candidates.add(day_start + range_start)
                candidates.add(day_start + range_end)

    return sorted(point for point in candidates if start_absolute <= point <= end_absolute)


def classify(
    *,
    workspace_id,
    worked_on,
    raw_duration_minutes,
    start_time=None,
    end_time=None,
    service_client=None,
):
    """Split an entry into classified segments. Rule R10, and section 3 of the brief.

    ``classificar(data, hora_inicio, hora_fim, cliente)`` in the brief's notation, with
    ``raw_duration_minutes`` added because duration mode has no times to derive it from.

    **THIS FUNCTION IS TOTAL. It never raises, and it never returns an empty list.**
    That is a requirement, not a nicety. R10 says "classificação é conveniência, não
    trava", and the coverage ratchet deliberately allows a workspace to sit in an
    incomplete state -- so an instant with no window is a state that can really happen.
    If that raised, an admin halfway through editing windows would block every
    technician in the workspace from recording work that was already performed, which is
    the one thing D4 and D9 both refuse. Instead the segment comes back with
    ``suggested_hour_type = None`` and a reason naming the gap, and the technician picks
    the hour type exactly as they do on a weekday in duration mode.

    **Duration mode** (no times) classifies by date alone and never splits (D13, R10):

    * a whole-day window on the date's scope decides it -- holiday, Sunday, Saturday in
      the seeded configuration;
    * on a weekday there is no way to know when the work happened, so the suggestion is
      ``None`` and the technician chooses.

    **Interval mode** walks the timeline, resolves the window in force at each instant by
    priority, and emits a segment at every *change* of classification -- not at every
    midnight. ``service_client`` is accepted and unused: windows are a workspace
    parameter (D16), and the argument is in the signature so a future per-client scope
    would not change any caller.
    """
    windows = load_windows(workspace_id)
    holidays = load_holidays(workspace_id)

    if start_time is None or end_time is None:
        return _classify_by_date(
            worked_on=worked_on,
            raw_duration_minutes=raw_duration_minutes,
            windows=windows,
            holidays=holidays,
        )

    return _classify_interval(
        worked_on=worked_on,
        start_time=start_time,
        end_time=end_time,
        windows=windows,
        holidays=holidays,
    )


def _classify_by_date(*, worked_on, raw_duration_minutes, windows, holidays):
    """Duration mode. One segment, classified by the date only."""
    holidays_today = holidays_on(holidays, worked_on)
    scopes = day_scopes_for(worked_on, holidays)

    applicable = [window for window in windows if window.day_scope in scopes]

    # Only whole-day windows can decide a duration entry. A ranged window would be a
    # guess about when the work happened, and R10 forbids inferring the hour on a
    # weekday precisely because that guess changes the price.
    candidates = [window for window in applicable if window.is_all_day]

    if candidates:
        winner = min(
            candidates,
            key=lambda window: (window.hour_type.priority, window.hour_type.name),
        )
        suggested, reason = winner.hour_type, _describe(winner, holidays_today)
    elif applicable:
        # The ordinary weekday case. There ARE windows for this day, none of them
        # whole-day, so nothing can be inferred without knowing the hour -- which is
        # exactly what R10 prescribes, not a fault. No reason, because there is nothing
        # to explain: the technician simply chooses.
        suggested, reason = None, ""
    else:
        # NOT the same thing, and telling them apart matters. No window applies to this
        # kind of day at all, which is a hole in the configuration rather than a rule.
        # Both states produce `suggested_hour_type = None`, so without distinct reasons a
        # misconfigured workspace would be indistinguishable from a normal Tuesday -- and
        # the panel's health indicator would be the only place the gap ever showed.
        suggested, reason = None, UNCLASSIFIED_REASON

    return [
        Segment(
            worked_on=worked_on,
            start_time=None,
            end_time=None,
            raw_duration_minutes=raw_duration_minutes,
            suggested_hour_type=suggested,
            reason=reason,
        )
    ]


def _classify_interval(*, worked_on, start_time, end_time, windows, holidays):
    """Interval mode. One segment per change of classification along the timeline."""
    start_absolute = start_time.hour * 60 + start_time.minute
    end_absolute = end_time.hour * 60 + end_time.minute

    # An end at or before the start means the work ran past midnight, which is how R10's
    # continuous 18:00->08:00 window is entered.
    if end_absolute <= start_absolute:
        end_absolute += MINUTES_PER_DAY

    points = _breakpoints(
        start_absolute, end_absolute, worked_on=worked_on, windows=windows, holidays=holidays
    )

    runs = []

    for run_start, run_end in zip(points, points[1:]):
        if run_start == run_end:
            continue

        window, reason = _resolve_instant(
            run_start, worked_on=worked_on, windows=windows, holidays=holidays
        )
        hour_type = window.hour_type if window else None

        # Merge with the previous run when the hour type is unchanged. Merging on the
        # hour type and not on the reason is deliberate: crossing midnight moves from
        # Monday's 18:00-08:00 window to Tuesday's, a different window with a different
        # reason but the same price, and the brief requires that to stay ONE segment.
        # The first reason wins, because it is the one that explains where the work
        # started.
        previous_hour_type_id = getattr(runs[-1]["hour_type"], "pk", None) if runs else object()
        current_hour_type_id = getattr(hour_type, "pk", None)

        if runs and previous_hour_type_id == current_hour_type_id:
            runs[-1]["end"] = run_end
        else:
            runs.append({"start": run_start, "end": run_end, "hour_type": hour_type, "reason": reason})

    return [
        Segment(
            worked_on=worked_on + datetime.timedelta(days=run["start"] // MINUTES_PER_DAY),
            start_time=_minute_to_time(run["start"]),
            end_time=_minute_to_time(run["end"]),
            raw_duration_minutes=run["end"] - run["start"],
            suggested_hour_type=run["hour_type"],
            reason=run["reason"],
        )
        for run in runs
    ]



# ---------------------------------------------------------------------------
# CSV bulk import -- section 1 of the phase brief
# ---------------------------------------------------------------------------

#: The columns the import expects. ``name`` and ``date`` are required; the rest default.
HOLIDAY_CSV_COLUMNS = ("name", "date", "is_recurring", "scope")

#: Accepted date formats. ISO first because it is unambiguous, then the Brazilian form
#: because that is what a spreadsheet in pt-BR exports.
_HOLIDAY_CSV_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y")

_TRUTHY = {"true", "1", "sim", "s", "yes", "y", "verdadeiro"}
_FALSY = {"false", "0", "nao", "não", "n", "no", "falso", ""}


def _parse_csv_date(raw):
    for date_format in _HOLIDAY_CSV_DATE_FORMATS:
        try:
            return datetime.datetime.strptime(raw.strip(), date_format).date()
        except ValueError:
            continue
    return None


def _parse_csv_bool(raw):
    """Returns ``True``/``False``, or ``None`` when the value is not recognisable.

    ``None`` rather than a silent ``False``: a row saying ``is_recurring=maybe`` is a row
    whose author had an intention, and guessing it wrong means either one day or every
    future year is priced differently. Better to reject the row and say which.
    """
    normalized = (raw or "").strip().lower()

    if normalized in _TRUTHY:
        return True
    if normalized in _FALSY:
        return False
    return None


def parse_holiday_csv(content):
    """Parse a holiday CSV into rows ready for creation, plus per-row errors.

    Returns ``(rows, errors)``. Each error is ``{"line": n, "error": CODE, "value": ...}``
    with ``line`` counting the way a spreadsheet does -- the header is line 1 -- so an admin
    can go straight to the offending row.

    **Parses everything and reports everything.** It does not stop at the first bad row,
    because an admin pasting a year of holidays wants the whole list of problems, not a
    dozen round trips. Whether a partially valid import is *applied* is the caller's
    decision, not this function's.

    Nothing here touches the database, so recurrence collisions between rows of the same
    file, or against rows already stored, are not detected here. The caller applies
    ``validate_holiday`` per row, which is the single place that rule lives.
    """
    stream = io.StringIO((content or "").strip())

    try:
        reader = csv.DictReader(stream)
        fieldnames = [name.strip().lower() for name in (reader.fieldnames or [])]
    except csv.Error:
        return [], [{"line": 1, "error": CSV_HEADER_IS_INVALID, "value": None}]

    if "name" not in fieldnames or "date" not in fieldnames:
        return [], [{"line": 1, "error": CSV_HEADER_IS_INVALID, "value": ",".join(fieldnames)}]

    valid_scopes = {choice[0] for choice in ServiceHolidayScope.choices}

    rows = []
    errors = []

    for index, raw_row in enumerate(reader, start=2):
        # DictReader keys carry the original header casing; normalise to match fieldnames.
        row = {(key or "").strip().lower(): (value or "") for key, value in raw_row.items()}

        name = row.get("name", "").strip()
        raw_date = row.get("date", "").strip()

        if not name or not raw_date:
            errors.append({"line": index, "error": CSV_ROW_IS_INVALID, "value": name or raw_date})
            continue

        parsed_date = _parse_csv_date(raw_date)

        if parsed_date is None:
            errors.append({"line": index, "error": CSV_DATE_IS_INVALID, "value": raw_date})
            continue

        is_recurring = _parse_csv_bool(row.get("is_recurring", ""))

        if is_recurring is None:
            errors.append(
                {"line": index, "error": CSV_ROW_IS_INVALID, "value": row.get("is_recurring")}
            )
            continue

        scope = (row.get("scope", "") or ServiceHolidayScope.NATIONAL).strip().lower()

        if scope not in valid_scopes:
            errors.append({"line": index, "error": CSV_SCOPE_IS_INVALID, "value": scope})
            continue

        rows.append(
            {
                "line": index,
                "name": name,
                "date": parsed_date,
                "is_recurring": is_recurring,
                "scope": scope,
            }
        )

    return rows, errors
