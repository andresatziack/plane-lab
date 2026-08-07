# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""The monetary arithmetic, with no database. Section 3 and section 4b.

Every test here runs against ``plane.utils.service_money``, which imports nothing from
Django -- so these are the tests that pin what a client is charged without a schema, a
settings module or a request in the way.

The acceptance criteria of the pricing brief that are pure arithmetic are 1, 2, 3, 5, 7, 8
and 13, and they are all here. The ones that need a database (6, 9, 10, 11, 12) are in
``test_service_pricing.py`` and ``test_service_billing.py``.
"""

# Python imports
from decimal import ROUND_HALF_EVEN, Decimal

# Third party imports
import pytest

# Module imports
from plane.utils.service_money import (
    MONEY_SCALE,
    ZERO_MONEY,
    amount_from_absolute_rate,
    amount_from_base_rate,
    format_money,
    overage_amount,
    quantize_money,
)


class TestTheBriefsWorkedExamples:
    """Acceptance criteria 1, 2 and 3, taken literally from section 3."""

    def test_one_commercial_hour_at_two_hundred_is_two_hundred(self):
        """Criterion 1. The simplest case, and the one every other one is a variation of."""
        assert amount_from_base_rate(
            equivalent_hours=Decimal("1.0000"), base_hour_rate=Decimal("200.00")
        ) == Decimal("200.00")

    def test_one_after_hours_hour_is_three_hundred_through_the_hours_not_the_rate(self):
        """Criterion 2, and the shape of the whole design.

        1h after hours at multiplier 1.5 is R$ 300,00 -- and the multiplier is **not** in
        this call. It entered when ``equivalent_hours`` was derived, which is why there is
        one price table per client rather than one per client per hour type (D16).
        """
        assert amount_from_base_rate(
            equivalent_hours=Decimal("1.5000"), base_hour_rate=Decimal("200.00")
        ) == Decimal("300.00")

    def test_one_hour_fifteen_of_commercial_time_is_two_hundred_and_fifty(self):
        """Criterion 3. R2's quarter-hour rounding has already happened upstream."""
        assert amount_from_base_rate(
            equivalent_hours=Decimal("1.2500"), base_hour_rate=Decimal("200.00")
        ) == Decimal("250.00")

    def test_a_three_hour_overage_at_two_fifty_is_seven_hundred_and_fifty(self):
        """Criterion 7."""
        assert overage_amount(
            overage_hours=Decimal("3.0000"), overage_hour_rate=Decimal("250.00")
        ) == Decimal("750.00")


class TestTheAbsoluteOverrideChargesTheMultiplierOnce:
    """Acceptance criterion 5, and the trap underneath it.

    An absolute override **replaces** ``base * multiplier``, so it already embeds the
    multiplier. Applying it to equivalent hours charges the multiplier twice -- criterion
    8's bug wearing criterion 5's clothes, in a place the brief does not warn about.
    """

    def test_a_sunday_hour_at_an_absolute_three_fifty_is_three_fifty(self):
        """Criterion 5. One hour on a Sunday, multiplier 2.0, override R$ 350,00.

        ``logged_hours`` is 1.0 and ``equivalent_hours`` is 2.0. The answer is R$ 350,00.
        """
        assert amount_from_absolute_rate(
            logged_hours=Decimal("1.0000"), absolute_rate=Decimal("350.00")
        ) == Decimal("350.00")

    def test_the_sabotage_using_equivalent_hours_would_double_the_charge(self):
        """**The sabotage, made explicit.** This is what the bug would produce.

        Not a test of the bug -- a test that *names the number the bug produces*, so that if
        someone ever routes equivalent hours into the absolute path, the failure message of
        the test above says R$ 700,00 and this one explains why that is exactly double.
        """
        correct = amount_from_absolute_rate(
            logged_hours=Decimal("1.0000"), absolute_rate=Decimal("350.00")
        )
        what_the_bug_produces = amount_from_absolute_rate(
            logged_hours=Decimal("2.0000"), absolute_rate=Decimal("350.00")
        )

        assert what_the_bug_produces == correct * 2
        assert what_the_bug_produces == Decimal("700.00")

    def test_the_two_formulas_are_separate_functions_with_separate_parameter_names(self):
        """The structural defence, asserted rather than trusted.

        Neither function accepts the other's parameter. A boolean flag in the wrong position
        is invisible at the call site; a wrong keyword raises.
        """
        with pytest.raises(TypeError):
            amount_from_absolute_rate(
                equivalent_hours=Decimal("1.0000"), absolute_rate=Decimal("350.00")
            )

        with pytest.raises(TypeError):
            amount_from_base_rate(
                logged_hours=Decimal("1.0000"), base_hour_rate=Decimal("200.00")
            )

    def test_neither_function_is_positional(self):
        """Keyword-only, so an argument cannot land in the wrong slot."""
        with pytest.raises(TypeError):
            amount_from_base_rate(Decimal("1.0000"), Decimal("200.00"))


class TestOverageCannotBeMultipliedTwice:
    """Acceptance criterion 8, defended by a signature rather than by a branch."""

    def test_overage_amount_has_no_multiplier_parameter(self):
        """The first line of defence: there is nowhere to pass one.

        ``period.overage_hours`` is accumulated from ``debited_hours``, which is already
        equivalent. Committing criterion 8's bug requires *changing this function*, not
        mis-ordering a call to it.
        """
        with pytest.raises(TypeError):
            overage_amount(
                overage_hours=Decimal("3.0000"),
                overage_hour_rate=Decimal("250.00"),
                multiplier=Decimal("1.50"),
            )

    def test_an_after_hours_overage_is_not_multiplied_again(self):
        """3h of overage that arrived as 2h at 1.5x is still 3h of overage.

        The equivalent hours are the input. R$ 750,00, never R$ 1.125,00.
        """
        assert overage_amount(
            overage_hours=Decimal("3.0000"), overage_hour_rate=Decimal("250.00")
        ) == Decimal("750.00")


class TestRounding:
    """Section 4b: two decimal places, half-up, once per work log."""

    def test_the_scale_is_two_places_not_the_hour_scale(self):
        assert MONEY_SCALE == Decimal("0.01")
        assert ZERO_MONEY == Decimal("0.00")

    def test_a_half_cent_tie_rounds_up_not_to_even(self):
        """The reason ``ROUND_HALF_UP`` is passed explicitly rather than inherited.

        Ties are **reachable and commonplace**: hours are multiples of 0.25 and a rate has
        two places, so the product can land exactly on a half cent. 0.25h at R$ 180,50 is
        45.125000. Half-up gives R$ 45,13; the decimal context's default (half-even) gives
        R$ 45,12 -- and would round half of all ties against the supplier.
        """
        product = Decimal("0.2500") * Decimal("180.50")
        assert product == Decimal("45.125000"), "a genuine tie, not a near miss"

        assert amount_from_base_rate(
            equivalent_hours=Decimal("0.2500"), base_hour_rate=Decimal("180.50")
        ) == Decimal("45.13")

        # The positive control for the claim above: half-even really does differ here, so
        # this test would still be meaningful if someone dropped the `rounding` argument.
        assert product.quantize(MONEY_SCALE, rounding=ROUND_HALF_EVEN) == Decimal("45.12")

    @pytest.mark.parametrize(
        "hours,rate,expected",
        [
            (Decimal("0.2500"), Decimal("250.10"), Decimal("62.53")),
            (Decimal("0.2500"), Decimal("200.02"), Decimal("50.01")),
            (Decimal("0.5000"), Decimal("333.33"), Decimal("166.67")),
            (Decimal("0.7500"), Decimal("99.90"), Decimal("74.93")),
            (Decimal("1.2500"), Decimal("180.50"), Decimal("225.63")),
        ],
    )
    def test_every_reachable_tie_rounds_up(self, hours, rate, expected):
        """Five more real ties, all from hour figures the rounding rules can produce."""
        assert amount_from_base_rate(equivalent_hours=hours, base_hour_rate=rate) == expected

    def test_quantize_money_is_idempotent(self):
        once = quantize_money(Decimal("45.125"))
        assert quantize_money(once) == once


class TestNoCentIsLostOrCreated:
    """Acceptance criterion 13, which is a statement about **where** rounding happens."""

    def test_summing_rounded_values_is_the_number_that_matches_the_invoice(self):
        """The whole reason totals sum a persisted column instead of recomputing.

        Three work logs of 0.25h at R$ 180,50 each round to R$ 45,13, and the invoice says
        R$ 135,39. Rounding the un-rounded sum instead gives R$ 135,38 -- a cent that exists
        in the total and in none of the lines, which is precisely what a client checking the
        arithmetic would find.
        """
        per_log = amount_from_base_rate(
            equivalent_hours=Decimal("0.2500"), base_hour_rate=Decimal("180.50")
        )
        sum_of_rounded = per_log * 3

        assert per_log == Decimal("45.13")
        assert sum_of_rounded == Decimal("135.39")

        # The sabotage: one rounding at the end instead of one per work log.
        rounded_sum = quantize_money(Decimal("0.2500") * Decimal("180.50") * 3)
        assert rounded_sum == Decimal("135.38")
        assert rounded_sum != sum_of_rounded, "the two differ, which is why the order matters"

    def test_a_thousand_logs_do_not_drift(self):
        """Scale check: the discrepancy grows with volume rather than cancelling out."""
        per_log = amount_from_base_rate(
            equivalent_hours=Decimal("0.2500"), base_hour_rate=Decimal("180.50")
        )

        assert per_log * 1000 == Decimal("45130.00")
        assert quantize_money(Decimal("0.2500") * Decimal("180.50") * 1000) == Decimal("45125.00")


class TestFormatting:
    """One formatter, so the screen, the CSV and the API cannot disagree."""

    @pytest.mark.parametrize(
        "value,expected",
        [
            (Decimal("200"), "R$ 200,00"),
            (Decimal("1234.5"), "R$ 1.234,50"),
            (Decimal("0"), "R$ 0,00"),
            (Decimal("999.999"), "R$ 1.000,00"),
            (Decimal("1234567.89"), "R$ 1.234.567,89"),
            (Decimal("-1234.56"), "-R$ 1.234,56"),
        ],
    )
    def test_pt_br_currency(self, value, expected):
        assert format_money(value) == expected

    def test_trailing_zeros_are_kept_unlike_format_hours(self):
        """``R$ 200`` is a price written carelessly; ``R$ 200,00`` is a price."""
        assert format_money(Decimal("200.00")).endswith(",00")

    def test_the_grouping_does_not_depend_on_a_system_locale(self):
        """Built by hand on purpose: a container image does not guarantee pt_BR is installed,
        and a missing locale would silently produce a dot decimal separator on an invoice."""
        assert format_money(Decimal("1000")) == "R$ 1.000,00"
