# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Unit tests for CNPJ normalization and validation.

Two formats must be accepted, because both are legally valid:

* Numeric (legacy) -- 14 digits, every CNPJ issued up to June 2026.
* Alphanumeric -- 12 alphanumeric characters plus 2 numeric check digits, issued
  from July 2026 onward under IN RFB 2.229/2024.

A validator that only accepted digits would reject a newly issued, legitimate
CNPJ, which is why the alphanumeric cases below are not hypothetical.
"""

import pytest

from plane.utils.tax_id import format_cnpj, is_valid_cnpj, normalize_cnpj

# Real, well known CNPJs. These pin the check digit algorithm against reality:
# an implementation error would have to reproduce all of these to slip through.
VALID_NUMERIC_CNPJS = [
    "11.222.333/0001-81",
    "11222333000181",
    "19.131.243/0001-97",
    "00.000.000/0001-91",
    "34.028.316/0001-03",
]

# 12.ABC.345/01DE-35 is the Receita Federal's own published example of the
# alphanumeric format.
VALID_ALPHANUMERIC_CNPJS = [
    "12.ABC.345/01DE-35",
    "12ABC34501DE35",
    "A1B2C3D4E5F668",
    "ZZZZZZZZZZZZ62",
]


@pytest.mark.unit
class TestNormalizeCNPJ:
    def test_strips_mask_characters(self):
        assert normalize_cnpj("11.222.333/0001-81") == "11222333000181"

    def test_upper_cases_alphanumeric(self):
        assert normalize_cnpj("12.abc.345/01de-35") == "12ABC34501DE35"

    def test_strips_surrounding_whitespace(self):
        assert normalize_cnpj("  11222333000181 ") == "11222333000181"

    @pytest.mark.parametrize("value", [None, "", "   ", "--//..", "  .  "])
    def test_blank_input_becomes_none(self, value):
        """Blank must become NULL, not "".

        The uniqueness constraint on (workspace, tax_id) is partial and excludes
        NULLs. If blanks were stored as empty strings, the second client without a
        CNPJ would be rejected as a duplicate of the first.
        """
        assert normalize_cnpj(value) is None


@pytest.mark.unit
class TestIsValidCNPJ:
    @pytest.mark.parametrize("value", VALID_NUMERIC_CNPJS)
    def test_accepts_valid_numeric(self, value):
        assert is_valid_cnpj(value) is True

    @pytest.mark.parametrize("value", VALID_ALPHANUMERIC_CNPJS)
    def test_accepts_valid_alphanumeric(self, value):
        assert is_valid_cnpj(value) is True

    def test_rejects_wrong_check_digit(self):
        # Same base as a valid one, last digit off by one.
        assert is_valid_cnpj("11.222.333/0001-82") is False

    @pytest.mark.parametrize("value", ["1122233300018", "112223330001811", "112", ""])
    def test_rejects_wrong_length(self, value):
        assert is_valid_cnpj(value) is False

    @pytest.mark.parametrize(
        "value",
        ["00000000000000", "11111111111111", "99999999999999", "AAAAAAAAAAAAAA"],
    )
    def test_rejects_single_repeated_character(self, value):
        """These satisfy modulo 11 but are never issued, so they must be rejected."""
        assert is_valid_cnpj(value) is False

    @pytest.mark.parametrize(
        "value",
        [
            "11.222.333/0001-8X",  # check digits must be numeric
            "11.222.333/0001-A1",
            "11222333000-81",  # too short once the mask is stripped
            "11222333@00181",  # invalid character
            "11222333 00181",
            None,
        ],
    )
    def test_rejects_malformed(self, value):
        assert is_valid_cnpj(value) is False

    def test_check_digits_must_be_numeric_even_when_base_is_alphanumeric(self):
        # Valid base, but the check digit positions carry letters.
        assert is_valid_cnpj("12ABC34501DEAB") is False

    def test_accepts_masked_and_unmasked_equally(self):
        assert is_valid_cnpj("11.222.333/0001-81") == is_valid_cnpj("11222333000181")


@pytest.mark.unit
class TestFormatCNPJ:
    def test_applies_the_conventional_mask(self):
        assert format_cnpj("11222333000181") == "11.222.333/0001-81"

    def test_masks_alphanumeric_the_same_way(self):
        assert format_cnpj("12ABC34501DE35") == "12.ABC.345/01DE-35"

    def test_round_trips_with_normalize(self):
        assert normalize_cnpj(format_cnpj("11222333000181")) == "11222333000181"

    @pytest.mark.parametrize("value", [None, "", "123"])
    def test_returns_input_unchanged_when_not_full_length(self, value):
        """Display must never raise on unexpected stored data."""
        assert format_cnpj(value) == normalize_cnpj(value)
