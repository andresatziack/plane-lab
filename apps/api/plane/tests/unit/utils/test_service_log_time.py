# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Unit tests for the work log time arithmetic.

Rules R1, R2, R3 and R9, plus quantities 3 and 4 of section 4. The phase brief calls
these tests non-optional, and the master context calls section 5 "o coração
financeiro do sistema" -- every case in R2's reference table is here verbatim.

No database: the module under test imports nothing from Django, which is the whole
point of keeping it separate from ``plane.utils.service_log``.
"""

from datetime import time
from decimal import Decimal

import pytest

from plane.utils.service_log_time import (
    BLOCK_MINUTES,
    DURATION_MUST_BE_POSITIVE,
    HOUR_SCALE,
    INTERVAL_ENDPOINTS_MUST_DIFFER,
    INVALID_DURATION_FORMAT,
    LONG_ENTRY_WARNING_MINUTES,
    InvalidDurationError,
    debited_hours,
    duration_from_interval,
    equivalent_hours,
    format_duration,
    format_hours,
    parse_duration,
    round_to_block,
    to_decimal_hours,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# R2 -- the reference table
# ---------------------------------------------------------------------------

#: R2's table, transcribed exactly: (input text, raw minutes, billed minutes,
#: decimal hours). The master context says "usar exatamente como suíte de testes",
#: so this drives the parser, the rounding and the decimal conversion at once --
#: which also proves the three agree with each other.
R2_REFERENCE_TABLE = [
    ("1h", 60, 60, "1.0"),
    ("1h 05min", 65, 60, "1.0"),
    ("1h 07min", 67, 60, "1.0"),
    ("1h 08min", 68, 75, "1.25"),
    ("1h 15min", 75, 75, "1.25"),
    ("1h 20min", 80, 75, "1.25"),
    ("1h 23min", 83, 90, "1.5"),
    ("45min", 45, 45, "0.75"),
    ("22min", 22, 15, "0.25"),
    ("23min", 23, 30, "0.5"),
    ("8min", 8, 15, "0.25"),
    ("3min", 3, 15, "0.25"),
    ("30h", 1800, 1800, "30.0"),
]


class TestR2ReferenceTable:
    """The thirteen rows of R2, end to end."""

    @pytest.mark.parametrize(("text", "raw", "billed", "decimal_hours"), R2_REFERENCE_TABLE)
    def test_parses_to_the_documented_raw_minutes(self, text, raw, billed, decimal_hours):
        assert parse_duration(text) == raw

    @pytest.mark.parametrize(("text", "raw", "billed", "decimal_hours"), R2_REFERENCE_TABLE)
    def test_rounds_to_the_documented_billed_minutes(self, text, raw, billed, decimal_hours):
        assert round_to_block(raw) == billed

    @pytest.mark.parametrize(("text", "raw", "billed", "decimal_hours"), R2_REFERENCE_TABLE)
    def test_converts_to_the_documented_decimal(self, text, raw, billed, decimal_hours):
        assert to_decimal_hours(billed) == Decimal(decimal_hours)

    @pytest.mark.parametrize(("text", "raw", "billed", "decimal_hours"), R2_REFERENCE_TABLE)
    def test_full_pipeline_from_text_to_persisted_decimal(self, text, raw, billed, decimal_hours):
        """What the API actually does, in one line, for every documented case."""
        assert to_decimal_hours(round_to_block(parse_duration(text))) == Decimal(decimal_hours)


class TestRoundToBlock:
    """Rule R2 beyond the table."""

    def test_the_rounding_threshold_is_eight_not_seven_and_a_half(self):
        """R2 gives ``r >= 8`` as the authoritative integer implementation.

        The conceptual 7min30s tie is unreachable once section 4 truncates seconds,
        so 7 must round down and 8 must round up. This is the single most likely
        line for someone to "correct" into a nearest-neighbour rounding.
        """
        assert round_to_block(7) == BLOCK_MINUTES  # floor, not the block below
        assert round_to_block(52) == 45  # r = 7 -> down
        assert round_to_block(53) == 60  # r = 8 -> up

    def test_floor_of_fifteen_applies_to_any_time_worked(self):
        """Decision D1: anything under 6 minutes still bills a quarter hour."""
        for minutes in (1, 2, 3, 5, 6, 7):
            assert round_to_block(minutes) == BLOCK_MINUTES

    def test_zero_stays_zero(self):
        """The floor is conditional on ``bruto > 0``, so no time worked bills nothing."""
        assert round_to_block(0) == 0
        assert round_to_block(-5) == 0

    @pytest.mark.parametrize("raw", [1, 3, 8, 22, 23, 45, 60, 67, 68, 83, 90, 1800])
    def test_is_idempotent(self, raw):
        """Rounding an already rounded value must not inflate it.

        Required by the phase brief. This is what makes an edit or a recalculation
        safe: the multiplier can change and the value be recomputed without the
        duration creeping upward each time.
        """
        once = round_to_block(raw)
        assert round_to_block(once) == once

    @pytest.mark.parametrize("raw", list(range(1, 200)))
    def test_output_is_always_a_positive_multiple_of_the_block(self, raw):
        billed = round_to_block(raw)
        assert billed % BLOCK_MINUTES == 0
        assert billed >= BLOCK_MINUTES

    @pytest.mark.parametrize("raw", list(range(1, 200)))
    def test_never_distorts_by_more_than_one_block(self, raw):
        """A sanity bound: the floor may inflate a tiny entry, but nothing else may."""
        assert abs(round_to_block(raw) - raw) < BLOCK_MINUTES or raw < BLOCK_MINUTES


class TestParseDuration:
    """Rule R1."""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            # Every form R1 lists as required.
            ("1h", 60),
            ("30m", 30),
            ("30min", 30),
            ("1h15m", 75),
            ("1h 15min", 75),
            ("1,5h", 90),
            ("1.5h", 90),
            ("90min", 90),
            ("2 horas", 120),
        ],
    )
    def test_accepts_every_documented_format(self, text, expected):
        assert parse_duration(text) == expected

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("1H", 60),
            ("1H 15MIN", 75),
            ("2 HORAS", 120),
            ("1h15M", 75),
        ],
    )
    def test_is_case_insensitive(self, text, expected):
        """R1: "Case-insensitive, com ou sem espaço entre unidades"."""
        assert parse_duration(text) == expected

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("1h15min", 75),
            ("1h 15min", 75),
            ("1h  15min", 75),
            ("  1h 15min  ", 75),
            ("1 h 15 min", 75),
        ],
    )
    def test_whitespace_is_irrelevant(self, text, expected):
        assert parse_duration(text) == expected

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("1 hora", 60),
            ("2 horas", 120),
            ("1hr", 60),
            ("2hrs", 120),
            ("1minuto", 1),
            ("45minutos", 45),
            ("2mins", 2),
        ],
    )
    def test_accepts_the_spelled_out_and_abbreviated_units(self, text, expected):
        assert parse_duration(text) == expected

    def test_fractional_hours_use_decimal_not_float(self):
        """``1,1h`` is 66 minutes exactly; a float would make it 66.00000000000001."""
        assert parse_duration("1,1h") == 66
        assert parse_duration("0,25h") == 15
        assert parse_duration("2,5h") == 150

    def test_fractional_amounts_truncate_seconds(self):
        """Section 4: raw duration is whole minutes, seconds truncated.

        ``1,33h`` is 79.8 minutes. It becomes 79, not 80 -- truncation, not
        rounding, because rounding here would be a second rounding of time outside
        R2.
        """
        assert parse_duration("1,33h") == 79
        assert parse_duration("1,5min") == 1

    @pytest.mark.parametrize(
        "text",
        [
            "banana",  # acceptance criterion 5
            "",
            "   ",
            None,
            "abc1h",
            "1h banana",
            "banana 1h",
            "1x",
            "h",
            "min",
            "-1h",
            "1h -15min",
            "1:30",  # a plausible thing to type, and not in R1's list
            "1h30",  # trailing number with no unit
            "1,,5h",
            "1.5.5h",
        ],
    )
    def test_rejects_invalid_input(self, text):
        """Acceptance criterion 5, and R1's "nunca falha silenciosa"."""
        with pytest.raises(InvalidDurationError) as excinfo:
            parse_duration(text)
        assert excinfo.value.code == INVALID_DURATION_FORMAT

    @pytest.mark.parametrize("text", ["90", "1", "0.5", "1,5"])
    def test_rejects_a_bare_number(self, text):
        """``90`` is ambiguous between 90 minutes and 90 hours.

        R1 lists ``90min`` but no unitless form, and forbids guessing. Getting this
        wrong is a factor-of-60 billing error, so it fails instead.
        """
        with pytest.raises(InvalidDurationError) as excinfo:
            parse_duration(text)
        assert excinfo.value.code == INVALID_DURATION_FORMAT

    @pytest.mark.parametrize("text", ["1h 2h", "30min 15min", "1h 2hrs", "1hora 2h"])
    def test_rejects_a_repeated_unit(self, text):
        """``1h 2h`` is a typo, not a sum."""
        with pytest.raises(InvalidDurationError) as excinfo:
            parse_duration(text)
        assert excinfo.value.code == INVALID_DURATION_FORMAT

    @pytest.mark.parametrize("text", ["0h", "0min", "0h 0min", "0,0h"])
    def test_rejects_a_zero_total(self, text):
        """Well formed but meaningless. ``ServiceLog`` requires at least one minute."""
        with pytest.raises(InvalidDurationError) as excinfo:
            parse_duration(text)
        assert excinfo.value.code == DURATION_MUST_BE_POSITIVE

    def test_unit_order_is_not_significant(self):
        """``15min 1h`` is unusual but unambiguous, so it is accepted."""
        assert parse_duration("15min 1h") == 75

    def test_accepts_a_thirty_hour_entry(self):
        """Section 5: over 24h is a legitimate use case and must never be blocked."""
        assert parse_duration("30h") == 1800
        assert parse_duration("30h") > LONG_ENTRY_WARNING_MINUTES


class TestDurationFromInterval:
    """Rule R9."""

    @pytest.mark.parametrize(
        ("start", "end", "expected"),
        [
            (time(14, 0), time(15, 30), 90),  # R9's own example
            (time(8, 0), time(18, 0), 600),
            (time(17, 0), time(20, 0), 180),
            (time(0, 0), time(1, 0), 60),
            (time(14, 0), time(14, 15), 15),
            (time(14, 0), time(14, 1), 1),
        ],
    )
    def test_computes_minutes_within_one_day(self, start, end, expected):
        assert duration_from_interval(start, end) == expected

    @pytest.mark.parametrize(
        ("start", "end", "expected"),
        [
            (time(22, 0), time(1, 0), 180),  # R10's continuous night window
            (time(23, 0), time(2, 0), 180),
            (time(18, 0), time(8, 0), 840),  # the whole "fora do expediente" window
            (time(23, 59), time(0, 1), 2),
        ],
    )
    def test_wraps_over_midnight(self, start, end, expected):
        """Required by R10's 18:00->08:00 window, which is one continuous range."""
        assert duration_from_interval(start, end) == expected

    def test_truncates_seconds_at_each_endpoint(self):
        """Section 4 truncates seconds; time pickers submit whole minutes anyway."""
        assert duration_from_interval(time(14, 0, 30), time(15, 30, 45)) == 90

    def test_identical_endpoints_are_rejected(self):
        """Ambiguous between zero and 24 hours, so it raises rather than guessing."""
        with pytest.raises(InvalidDurationError) as excinfo:
            duration_from_interval(time(14, 0), time(14, 0))
        assert excinfo.value.code == INTERVAL_ENDPOINTS_MUST_DIFFER

    @pytest.mark.parametrize(("start", "end"), [(None, time(1, 0)), (time(1, 0), None), (None, None)])
    def test_missing_endpoints_are_rejected(self, start, end):
        with pytest.raises(InvalidDurationError) as excinfo:
            duration_from_interval(start, end)
        assert excinfo.value.code == INVALID_DURATION_FORMAT


class TestEntryModeEquivalence:
    """R9: the two entry modes must converge on the same raw duration.

    The phase brief requires this explicitly, and acceptance criterion 6 is one of
    these rows. Without it the two modes could drift apart and the same work would
    bill differently depending on how the technician typed it.
    """

    @pytest.mark.parametrize(
        ("start", "end", "text"),
        [
            (time(14, 0), time(15, 30), "1h 30min"),  # acceptance criterion 6
            (time(8, 0), time(9, 0), "1h"),
            (time(9, 0), time(9, 45), "45min"),
            (time(22, 0), time(1, 0), "3h"),  # across midnight
            (time(13, 0), time(14, 8), "1h 08min"),  # a case that rounds up
        ],
    )
    def test_interval_and_duration_agree_at_every_stage(self, start, end, text):
        from_interval = duration_from_interval(start, end)
        from_text = parse_duration(text)

        assert from_interval == from_text
        assert round_to_block(from_interval) == round_to_block(from_text)
        assert to_decimal_hours(round_to_block(from_interval)) == to_decimal_hours(round_to_block(from_text))


class TestToDecimalHours:
    """Rule R3."""

    def test_uses_the_section_4b_scale(self):
        """Four decimal places, so the value matches its database column exactly."""
        assert to_decimal_hours(75) == Decimal("1.2500")
        assert str(to_decimal_hours(75)) == "1.2500"
        assert to_decimal_hours(75).as_tuple().exponent == HOUR_SCALE.as_tuple().exponent

    def test_is_exact_for_every_quarter_hour(self):
        """A multiple of 15 needs at most two places, so nothing is ever discarded."""
        for blocks in range(1, 400):
            minutes = blocks * BLOCK_MINUTES
            assert to_decimal_hours(minutes) * 60 == Decimal(minutes)

    def test_returns_decimal_never_float(self):
        """The master context forbids float in financial arithmetic."""
        assert isinstance(to_decimal_hours(75), Decimal)


class TestEquivalentHours:
    """Quantity 3 of section 4."""

    def test_applies_the_multiplier(self):
        """Section 4's own example, and acceptance criterion 7."""
        assert equivalent_hours(Decimal("1.25"), Decimal("1.5")) == Decimal("1.8750")
        assert equivalent_hours(Decimal("1.0"), Decimal("1.5")) == Decimal("1.5000")

    def test_a_multiplier_of_one_changes_nothing(self):
        assert equivalent_hours(Decimal("1.2500"), Decimal("1.00")) == Decimal("1.2500")

    def test_the_section_4b_worst_case_is_exact(self):
        """0.25 * 1.01 = 0.2525 -- the case that proves four places are needed.

        If this ever fails, either the multiplier gained a decimal place or an hour
        column was narrowed. Both are silent truncation of money.
        """
        assert equivalent_hours(Decimal("0.25"), Decimal("1.01")) == Decimal("0.2525")

    @pytest.mark.parametrize("multiplier", ["0.01", "0.50", "1.00", "1.01", "1.50", "2.00", "2.50", "99.99"])
    def test_no_product_ever_needs_more_than_four_places(self, multiplier):
        """The whole argument of section 4b, checked exhaustively over quarter hours.

        Every multiplier with two places, times every multiple of 0.25, must
        terminate within four places -- otherwise the quantize would be discarding
        value rather than fixing scale.
        """
        for blocks in range(1, 200):
            logged = to_decimal_hours(blocks * BLOCK_MINUTES)
            exact = logged * Decimal(multiplier)
            assert equivalent_hours(logged, multiplier) == exact

    def test_multipliers_below_one_are_allowed(self):
        """Travel time at half rate is legitimate; only zero is refused, on the model."""
        assert equivalent_hours(Decimal("2.0000"), Decimal("0.50")) == Decimal("1.0000")


class TestDebitedHours:
    """Quantity 4 of section 4, and rules R5 and R11(a)."""

    def test_billable_debits_the_equivalent_hours(self):
        assert debited_hours(Decimal("1.8750"), is_billable=True) == Decimal("1.8750")

    def test_non_billable_debits_zero(self):
        """Acceptance criterion 12, for both Garantia and Cortesia."""
        assert debited_hours(Decimal("1.8750"), is_billable=False) == Decimal("0.0000")

    def test_non_billable_keeps_the_equivalent_hours_untouched(self):
        """R11(a): the client sees the effort delivered next to the 0h charged.

        Zeroing the equivalent hours instead would hide the work rather than
        evidence the concession.
        """
        equivalent = equivalent_hours(Decimal("1.25"), Decimal("1.5"))
        assert equivalent == Decimal("1.8750")
        assert debited_hours(equivalent, is_billable=False) == Decimal("0.0000")
        assert equivalent == Decimal("1.8750")

    def test_result_is_always_at_the_column_scale(self):
        assert str(debited_hours(Decimal("1.5"), is_billable=True)) == "1.5000"
        assert str(debited_hours(Decimal("1.5"), is_billable=False)) == "0.0000"


class TestFormatDuration:
    """Rule R3's readable output."""

    @pytest.mark.parametrize(
        ("minutes", "expected"),
        [
            (75, "1h 15min"),
            (60, "1h"),
            (45, "45min"),
            (15, "15min"),
            (90, "1h 30min"),
            (65, "1h 05min"),  # padded, as R1 itself writes it
            (1800, "30h"),
            (1, "1min"),
            (0, "0min"),
            (None, "0min"),
        ],
    )
    def test_renders_pt_br_duration(self, minutes, expected):
        assert format_duration(minutes) == expected

    @pytest.mark.parametrize(("text", "raw", "billed", "decimal_hours"), R2_REFERENCE_TABLE)
    def test_round_trips_every_reference_row(self, text, raw, billed, decimal_hours):
        """Each billed value renders to something the parser reads back identically."""
        assert parse_duration(format_duration(billed)) == billed


class TestFormatHours:
    """Rule R3's readable number."""

    @pytest.mark.parametrize(
        ("hours", "expected"),
        [
            (Decimal("1.2500"), "1,25h"),
            (Decimal("1.8750"), "1,875h"),  # section 6's own example of R11
            (Decimal("1.0000"), "1h"),
            (Decimal("2.0000"), "2h"),
            (Decimal("0.2525"), "0,2525h"),
            (Decimal("0.7500"), "0,75h"),
            (Decimal("0.0000"), "0h"),
            (None, "0h"),
        ],
    )
    def test_renders_pt_br_decimal_with_a_comma(self, hours, expected):
        assert format_hours(hours) == expected

    def test_large_values_are_not_rendered_in_scientific_notation(self):
        """``Decimal.normalize()`` would turn 1800 into 1.8E+3."""
        assert format_hours(Decimal("1800.0000")) == "1800h"
        assert format_hours(Decimal("30.0000")) == "30h"

    def test_does_not_round_away_significant_places(self):
        """Trimming trailing zeros must not become rounding to two places.

        R11's dual reading shows three places, and the master context allows no
        rounding of hours outside R2.
        """
        assert format_hours(Decimal("1.8750")) == "1,875h"
        assert format_hours(Decimal("0.2525")) == "0,2525h"
