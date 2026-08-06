# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Brazilian CNPJ normalization and validation.

Pure domain layer: no Django models, no request context, no I/O. Imported by
serializers and models, but safe to unit test in isolation.

Two formats are accepted, because both are legally valid:

* **Numeric (legacy)** -- 14 digits. Every CNPJ issued up to June 2026.
* **Alphanumeric** -- 12 alphanumeric characters (``0-9``, ``A-Z``) followed by
  2 numeric check digits, still 14 characters in total. Issued by the Receita
  Federal from July 2026 onward under IN RFB 2.229/2024.

The check digit algorithm is the same for both: modulo 11 over the character
values, where a character's value is its ASCII code minus 48. That makes digits
map to themselves (``"0"`` -> 0 ... ``"9"`` -> 9) and letters continue upward
(``"A"`` -> 17 ... ``"Z"`` -> 42), so the legacy numeric format is simply a
subset of the alphanumeric one and needs no separate code path.
"""

# Python imports
import re
from typing import Optional

# Total length of a CNPJ, both formats.
CNPJ_LENGTH = 14

# Number of leading characters that may be alphanumeric. The remaining
# characters are the numeric check digits.
CNPJ_BASE_LENGTH = 12

# Offset applied to a character's ASCII code to obtain its numeric value.
# ord("0") == 48, so digits map to themselves.
_ASCII_OFFSET = 48

# Weights applied right to left over the base, per the modulo 11 rule.
_FIRST_CHECK_DIGIT_WEIGHTS = (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)
_SECOND_CHECK_DIGIT_WEIGHTS = (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)

# Characters used purely for visual masking, e.g. "12.345.678/0001-95".
_MASK_CHARACTERS_PATTERN = re.compile(r"[.\-/\s]")

# A normalized CNPJ: 12 alphanumeric characters plus 2 numeric check digits.
_NORMALIZED_CNPJ_PATTERN = re.compile(r"^[0-9A-Z]{12}[0-9]{2}$")


def normalize_cnpj(value: Optional[str]) -> Optional[str]:
    """Strip mask characters and upper-case a CNPJ.

    Returns ``None`` for ``None`` and for values that are blank once the mask is
    removed, so that callers can persist a real ``NULL`` instead of an empty
    string. Performs no validation: use :func:`is_valid_cnpj` for that.
    """
    if value is None:
        return None

    normalized = _MASK_CHARACTERS_PATTERN.sub("", str(value)).upper()
    return normalized or None


def _character_value(character: str) -> int:
    """Return the numeric value of a CNPJ character (ASCII code minus 48)."""
    return ord(character) - _ASCII_OFFSET


def _expected_check_digit(base: str, weights: tuple) -> int:
    """Compute one check digit for ``base`` using the modulo 11 rule."""
    total = sum(_character_value(character) * weight for character, weight in zip(base, weights))
    remainder = total % 11
    # A remainder of 0 or 1 yields a check digit of 0, never a negative or
    # two-character result.
    return 0 if remainder < 2 else 11 - remainder


def is_valid_cnpj(value: Optional[str]) -> bool:
    """Return whether ``value`` is a structurally valid CNPJ.

    Accepts masked input. Validates length, the alphabet of each position, and
    both check digits. Rejects strings made of a single repeated character
    (``"00000000000000"``), which satisfy the check digit rule but are never
    issued.
    """
    normalized = normalize_cnpj(value)

    if normalized is None or len(normalized) != CNPJ_LENGTH:
        return False

    if not _NORMALIZED_CNPJ_PATTERN.match(normalized):
        return False

    # Placeholder values such as "00000000000000" pass the modulo 11 check but
    # are not real registrations.
    if len(set(normalized)) == 1:
        return False

    base = normalized[:CNPJ_BASE_LENGTH]
    check_digits = normalized[CNPJ_BASE_LENGTH:]

    first = _expected_check_digit(base, _FIRST_CHECK_DIGIT_WEIGHTS)
    if first != int(check_digits[0]):
        return False

    second = _expected_check_digit(base + str(first), _SECOND_CHECK_DIGIT_WEIGHTS)
    return second == int(check_digits[1])


def format_cnpj(value: Optional[str]) -> Optional[str]:
    """Apply the conventional CNPJ mask for display: ``XX.XXX.XXX/XXXX-XX``.

    Returns the normalized value unchanged when it is not 14 characters long, so
    that display never raises on unexpected stored data.
    """
    normalized = normalize_cnpj(value)

    if normalized is None or len(normalized) != CNPJ_LENGTH:
        return normalized

    return f"{normalized[:2]}.{normalized[2:5]}.{normalized[5:8]}/{normalized[8:12]}-{normalized[12:]}"
