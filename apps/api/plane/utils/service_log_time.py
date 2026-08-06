# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Time arithmetic for work logs: parsing, rounding, and the hour quantities.

Rules R1, R2, R3 and R9 of the master context, plus quantities 3 and 4 of section
4, live here and nowhere else.

This module imports nothing from Django, deliberately -- not even models. It is the
one place in the feature where that is achievable, and it is worth achieving: these
functions decide what a client is charged, so they should be testable without a
database, a settings module or a request. The model-aware half of the domain layer
is ``plane.utils.service_log``.

Naming note: the phase brief spells these functions in Portuguese
(``parse_duracao``, ``arredondar``, ``para_decimal``, ``formatar``,
``horas_equivalentes``). They are English here, following the convention Phases 1
and 2 established and that DECISOES.md, "Alinhamento de nomenclatura com o código
entregue", settled by realigning the documents to the code. The mapping is noted on
each function.
"""

# Python imports
import re
from decimal import ROUND_HALF_UP, Decimal

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Minimum billable unit, rule R2. The project bills in quarter hours.
BLOCK_MINUTES = 15

#: Remainder at or above which a duration rounds up to the next block.
#:
#: Eight, not seven and a half. R2 states the conceptual rule as "round to the
#: nearest multiple of 15, exact ties round up", and then gives the authoritative
#: integer implementation as ``r >= 8``. The two agree because the tie is
#: unreachable: section 4 truncates seconds, so 7min30s has already become 7
#: whole minutes by the time it gets here, and 7 rounds down. Only 8 and above
#: can round up. The reference table depends on exactly this -- 67 -> 60 but
#: 68 -> 75 -- so do not "fix" this to 7.5.
ROUND_UP_REMAINDER = 8

MINUTES_PER_HOUR = 60
MINUTES_PER_DAY = 24 * MINUTES_PER_HOUR

#: Scale of every hour quantity, fixed by section 4b of the master context.
#:
#: Four places is the proven minimum, not padding: logged hours are always a
#: multiple of 15 minutes (so p/4), the multiplier has two places (so m/100), and
#: the product p*m/400 terminates in at most four places because 400 = 2^4 * 5^2.
#: The worst case is reached: 0.25 * 1.01 = 0.2525. See the coupling warning on
#: ``ServiceHourType.multiplier`` before widening the multiplier.
HOUR_SCALE = Decimal("0.0001")

#: Scale of the hour type multiplier, also fixed by section 4b.
MULTIPLIER_SCALE = Decimal("0.01")

ZERO_HOURS = Decimal("0.0000")

#: Above this, the form asks for explicit confirmation. It never blocks -- section
#: 5 of the phase brief calls a 30h entry a legitimate use case (a project worked
#: end to end) and the confirmation exists only to catch a typo. Defined here so
#: that the API and the browser agree on the threshold instead of each hardcoding
#: its own.
LONG_ENTRY_WARNING_MINUTES = 24 * MINUTES_PER_HOUR

# Error codes, in the UPPER_SNAKE style the client entity and the catalogues
# established. The frontend maps them to translated strings; the API never returns
# Portuguese.
INVALID_DURATION_FORMAT = "INVALID_DURATION_FORMAT"
DURATION_MUST_BE_POSITIVE = "DURATION_MUST_BE_POSITIVE"
INTERVAL_ENDPOINTS_MUST_DIFFER = "INTERVAL_ENDPOINTS_MUST_DIFFER"
INVALID_CLOCK_FORMAT = "INVALID_CLOCK_FORMAT"


class InvalidDurationError(ValueError):
    """A duration could not be derived from the input.

    Carries an UPPER_SNAKE ``code`` rather than a sentence: R1 requires a clear
    validation error in the form, and the translation belongs to the frontend.
    """

    def __init__(self, code):
        self.code = code
        super().__init__(code)


# ---------------------------------------------------------------------------
# R1 -- the text parser
# ---------------------------------------------------------------------------

#: Minutes contributed by one unit of each accepted suffix.
#:
#: R1 requires `1h`, `30m`, `30min`, `1h15m`, `1h 15min`, `1,5h`, `1.5h`, `90min`
#: and `2 horas`. The spelled-out Portuguese forms are included because "2 horas"
#: is in that list, and their singulars and the `hr`/`hrs` abbreviations come along
#: for free -- a technician who types "1 hora" or "2 hrs" meant something
#: unambiguous, and rejecting it would be pedantry rather than safety.
_UNIT_MINUTES = {
    "h": MINUTES_PER_HOUR,
    "hr": MINUTES_PER_HOUR,
    "hrs": MINUTES_PER_HOUR,
    "hora": MINUTES_PER_HOUR,
    "horas": MINUTES_PER_HOUR,
    "m": 1,
    "min": 1,
    "mins": 1,
    "minuto": 1,
    "minutos": 1,
}

#: One "<number><unit>" pair, with optional whitespace anywhere around it. Applied
#: repeatedly with ``.match(text, position)`` so that the scan can insist on
#: consuming the entire string -- see the note about trailing text in
#: ``parse_duration``.
_TOKEN_PATTERN = re.compile(r"\s*(\d+(?:[.,]\d+)?)\s*([a-z]+)\s*")


def parse_duration(text):
    """Free text to whole minutes. Rule R1. (``parse_duracao``)

    ``"1h 15min"`` -> 75, ``"1,5h"`` -> 90, ``"90min"`` -> 90, ``"2 horas"`` -> 120.
    Case-insensitive, and the space between number and unit is optional.

    Raises ``InvalidDurationError`` on anything else. R1 is explicit that invalid
    input must fail loudly: "nunca falha silenciosa nem interpretação adivinhada".
    Three consequences worth stating, because each is a guess this refuses to make:

    * **A bare number is rejected.** ``"90"`` could be 90 minutes or 90 hours, and
      R1's accepted list contains ``90min`` but no unitless form. Guessing here
      would be a factor-of-60 billing error.
    * **A repeated unit is rejected.** ``"1h 2h"`` is a typo, not a sum.
    * **A zero or negative total is rejected.** Nobody types a duration meaning
      "no time passed", and ``ServiceLog`` requires at least one raw minute.

    Fractional amounts are truncated to whole minutes, following section 4's
    "segundos truncados": ``"1,33h"`` is 79.8 minutes and returns 79. Decimal is
    used throughout so that ``"1,1h"`` does not inherit a binary float artefact.
    """
    if text is None:
        raise InvalidDurationError(INVALID_DURATION_FORMAT)

    normalized = str(text).strip().lower()

    if not normalized:
        raise InvalidDurationError(INVALID_DURATION_FORMAT)

    total = Decimal(0)
    seen_units = set()
    position = 0

    while position < len(normalized):
        match = _TOKEN_PATTERN.match(normalized, position)

        # No match means either a bare number, a stray word, or trailing garbage
        # such as "1h banana". All three are the same answer to the caller.
        if match is None:
            raise InvalidDurationError(INVALID_DURATION_FORMAT)

        amount_text, unit_text = match.group(1), match.group(2)

        if unit_text not in _UNIT_MINUTES:
            raise InvalidDurationError(INVALID_DURATION_FORMAT)

        # Normalise to the canonical unit so that "1h 2hrs" is caught as a repeat.
        unit_minutes = _UNIT_MINUTES[unit_text]
        if unit_minutes in seen_units:
            raise InvalidDurationError(INVALID_DURATION_FORMAT)
        seen_units.add(unit_minutes)

        total += Decimal(amount_text.replace(",", ".")) * unit_minutes
        position = match.end()

    # int() on a Decimal truncates toward zero, which is the "seconds truncated"
    # behaviour section 4 asks for.
    minutes = int(total)

    if minutes <= 0:
        raise InvalidDurationError(DURATION_MUST_BE_POSITIVE)

    return minutes


# ---------------------------------------------------------------------------
# R9 -- the interval mode
# ---------------------------------------------------------------------------


def duration_from_interval(start, end):
    """Whole minutes between two wall-clock times. Rule R9. (``duracao_de_intervalo``)

    ``14:00`` to ``15:30`` -> 90, which is the same 90 that ``parse_duration`` gets
    from ``"1h 30min"``. R9 requires the two entry modes to converge on one raw
    duration, and the phase brief makes that equivalence an explicit test.

    **Midnight wrap.** An end at or before the start is read as the next day, so
    ``22:00`` to ``01:00`` is 180 minutes. This is required by R10's continuous
    18:00->08:00 window.

    **Seconds are truncated at each endpoint**, per section 4. Time pickers submit
    whole minutes, so this only matters for data arriving from elsewhere.

    Identical endpoints raise rather than resolve. ``14:00`` to ``14:00`` is
    genuinely ambiguous between zero minutes and a full 24 hours, and silently
    creating a 24-hour work log is exactly the kind of guess R1 forbids.
    """
    if start is None or end is None:
        raise InvalidDurationError(INVALID_DURATION_FORMAT)

    start_minutes = start.hour * MINUTES_PER_HOUR + start.minute
    end_minutes = end.hour * MINUTES_PER_HOUR + end.minute

    if start_minutes == end_minutes:
        raise InvalidDurationError(INTERVAL_ENDPOINTS_MUST_DIFFER)

    duration = end_minutes - start_minutes

    if duration < 0:
        duration += MINUTES_PER_DAY

    return duration


# ---------------------------------------------------------------------------
# R2 -- rounding to 15 minute blocks
# ---------------------------------------------------------------------------


def round_to_block(minutes):
    """Raw minutes to billed minutes, in 15 minute blocks. Rule R2. (``arredondar``)

    ``68 -> 75``, ``67 -> 60``, ``3 -> 15``. The full reference table in R2 is the
    test suite for this function.

    Two steps, in this order:

    1. Round to the nearest block, with a remainder of 8 or more going up. See
       ``ROUND_UP_REMAINDER`` for why the threshold is 8.
    2. Apply the 15 minute floor whenever any time at all was worked, so that a
       3 minute call is billed as a quarter hour rather than as nothing (D1).

    Idempotent: the output is a multiple of 15, whose remainder is 0, so a second
    application changes nothing. The phase brief requires a test for this, because
    a recalculation path that rounds an already-rounded value must not inflate it.

    This is the *only* rounding of time anywhere in the system. The master context
    is explicit: "Horas nunca são arredondadas além da regra R2".
    """
    if minutes <= 0:
        return 0

    remainder = minutes % BLOCK_MINUTES
    rounded = minutes - remainder

    if remainder >= ROUND_UP_REMAINDER:
        rounded += BLOCK_MINUTES

    return max(rounded, BLOCK_MINUTES)


# ---------------------------------------------------------------------------
# R3 -- decimal persistence
# ---------------------------------------------------------------------------


def to_decimal_hours(minutes):
    """Billed minutes to decimal hours at the 4b scale. Rule R3. (``para_decimal``)

    ``75 -> Decimal("1.2500")``.

    Expects minutes that have already been through ``round_to_block``. For a
    multiple of 15 the division is exact -- 15/60 is 0.25, and a quarter needs two
    places -- so the quantize here never actually rounds, it only fixes the scale so
    that the value matches its database column. Passing an unrounded value would
    make this the second rounding of time in the system, which the master context
    forbids; round first.
    """
    return (Decimal(minutes) / MINUTES_PER_HOUR).quantize(HOUR_SCALE, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Quantities 3 and 4 of section 4
# ---------------------------------------------------------------------------


def equivalent_hours(logged_hours, multiplier):
    """Logged hours times the hour type multiplier. (``horas_equivalentes``)

    Quantity 3 of section 4: ``1.25 * 1.5 = 1.875``. This is what generates a money
    amount, and per R11 the only hour quantity the client is ever shown.

    Exact by construction at this scale -- that is the whole argument of section 4b
    -- so the quantize fixes the scale rather than discarding anything. It is stated
    explicitly (rather than left to the column) so a caller doing arithmetic in
    memory gets the same value the database would store.
    """
    product = Decimal(logged_hours) * Decimal(multiplier)
    return product.quantize(HOUR_SCALE, rounding=ROUND_HALF_UP)


def debited_hours(equivalent, *, is_billable):
    """Equivalent hours, or zero when the billing route does not charge.

    Quantity 4 of section 4, and rules R5 and R11(a).

    Takes a boolean rather than the billing route itself, which keeps this module
    free of any Django import: the route enum lives on ``ServiceBillingType``, and
    duplicating its string value here would be a second source of truth for the one
    decision that determines whether a client is billed. Callers use
    ``plane.utils.service_log.is_billable_route``.

    Note what this does *not* do: a non-billable entry still has its equivalent
    hours calculated normally. R11(a) requires that, so that Garantia and Cortesia
    show the client the effort delivered alongside the 0h charged -- which
    evidences the concession instead of hiding the work.
    """
    if not is_billable:
        return ZERO_HOURS

    return Decimal(equivalent).quantize(HOUR_SCALE, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Display -- pt-BR
# ---------------------------------------------------------------------------


def format_duration(minutes):
    """Minutes as readable pt-BR time. Rule R3. (``formatar``)

    ``75 -> "1h 15min"``, ``60 -> "1h"``, ``45 -> "45min"``, ``1800 -> "30h"``.

    Minutes are zero padded only alongside an hour, matching how R1 itself writes
    ``1h 08min``. Never persisted: R3 requires the decimal to be the source of
    truth and this to be derived for display.
    """
    if minutes is None or minutes <= 0:
        return "0min"

    hours, remaining = divmod(int(minutes), MINUTES_PER_HOUR)

    if hours and remaining:
        return f"{hours}h {remaining:02d}min"

    if hours:
        return f"{hours}h"

    return f"{remaining}min"


def format_hours(hours):
    """Decimal hours as a readable pt-BR number. Rule R3.

    ``Decimal("1.2500") -> "1,25h"``, ``Decimal("1.8750") -> "1,875h"``,
    ``Decimal("2.0000") -> "2h"``.

    Trailing zeros are trimmed rather than fixed at two places, because section 6 of
    the phase brief renders the R11 dual reading as "o cliente vê 1,875h" -- three
    places. Fixing the scale would corrupt that number, and rounding it would be a
    rounding of hours outside R2.

    Formatted with ``:f`` rather than ``normalize()``: normalize turns
    ``Decimal("1800.0000")`` into ``1.8E+3``, and scientific notation in an hours
    column would be a bug report.
    """
    if hours is None:
        return "0h"

    text = f"{Decimal(hours).quantize(HOUR_SCALE, rounding=ROUND_HALF_UP):f}"

    if "." in text:
        text = text.rstrip("0").rstrip(".")

    return f"{text.replace('.', ',')}h"



# ---------------------------------------------------------------------------
# Clock borders -- minutes since midnight
# ---------------------------------------------------------------------------
#
# The classification windows of the calendar phase store their borders as whole
# minutes since midnight rather than as ``TimeField``, because a window has to be
# able to end at 24:00 and ``datetime.time`` cannot represent that hour. Keeping the
# whole feature in one unit -- raw duration, the 15 minute block, and now window
# borders -- also removes a class of conversion mistake.
#
# The cost is legibility: 1080 is not obviously 18:00. These two functions are how
# that cost is paid back, at the API boundary and in the model's own repr.

#: A whole day as a window: ``(0, MINUTES_PER_DAY)``.
_CLOCK_PATTERN = re.compile(r"^(\d{1,2}):([0-5]\d)$")


def minutes_to_clock(minutes):
    """Minutes since midnight as ``HH:MM``. ``1080 -> "18:00"``, ``1440 -> "24:00"``.

    24:00 is a legal output and is the whole reason these borders are not
    ``TimeField``: it is how a window says "the end of the day" without colliding
    with a window that genuinely starts at 00:00.
    """
    if minutes is None:
        return None

    hours, remaining = divmod(int(minutes), MINUTES_PER_HOUR)
    return f"{hours:02d}:{remaining:02d}"


def clock_to_minutes(clock):
    """``HH:MM`` to minutes since midnight. ``"18:00" -> 1080``, ``"24:00" -> 1440``.

    Accepts anything from ``00:00`` to ``24:00`` inclusive and rejects the rest, so a
    payload cannot smuggle in a border outside the day. Raises
    ``InvalidDurationError`` with ``INVALID_CLOCK_FORMAT``.
    """
    if clock is None:
        raise InvalidDurationError(INVALID_CLOCK_FORMAT)

    match = _CLOCK_PATTERN.match(str(clock).strip())

    if match is None:
        raise InvalidDurationError(INVALID_CLOCK_FORMAT)

    minutes = int(match.group(1)) * MINUTES_PER_HOUR + int(match.group(2))

    if minutes > MINUTES_PER_DAY:
        raise InvalidDurationError(INVALID_CLOCK_FORMAT)

    return minutes
