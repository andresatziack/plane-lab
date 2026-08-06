/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/**
 * Brazilian CNPJ normalization and validation.
 *
 * This mirrors the server side implementation in `plane/utils/tax_id.py`. It
 * exists so the form can reject a typo before a round trip; the server remains
 * the authority.
 *
 * Two formats are accepted, because both are legally valid:
 *
 * - Numeric (legacy): 14 digits. Every CNPJ issued up to June 2026.
 * - Alphanumeric: 12 alphanumeric characters (0-9, A-Z) followed by 2 numeric
 *   check digits, still 14 characters in total. Issued by the Receita Federal
 *   from July 2026 onward under IN RFB 2.229/2024.
 *
 * The check digit algorithm is the same for both: modulo 11 over the character
 * values, where a character's value is its ASCII code minus 48. Digits therefore
 * map to themselves and letters continue upward, which makes the legacy numeric
 * format a subset of the alphanumeric one.
 */

const CNPJ_LENGTH = 14;
const CNPJ_BASE_LENGTH = 12;
const ASCII_OFFSET = 48;

const FIRST_CHECK_DIGIT_WEIGHTS = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2];
const SECOND_CHECK_DIGIT_WEIGHTS = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2];

/** Characters used purely for visual masking, e.g. "12.345.678/0001-95". */
const MASK_CHARACTERS = /[.\-/\s]/g;
const NORMALIZED_CNPJ = /^[0-9A-Z]{12}[0-9]{2}$/;

/**
 * Strip mask characters and upper-case a CNPJ. Performs no validation.
 * Returns an empty string for blank input so callers can send null upstream.
 */
export const normalizeCNPJ = (value: string | null | undefined): string => {
  if (!value) return "";
  return value.replace(MASK_CHARACTERS, "").toUpperCase();
};

const characterValue = (character: string): number => character.charCodeAt(0) - ASCII_OFFSET;

const expectedCheckDigit = (base: string, weights: number[]): number => {
  const total = base
    .split("")
    .reduce((sum, character, index) => sum + characterValue(character) * (weights[index] ?? 0), 0);
  const remainder = total % 11;
  // A remainder of 0 or 1 yields a check digit of 0.
  return remainder < 2 ? 0 : 11 - remainder;
};

/**
 * Whether the value is a structurally valid CNPJ. Accepts masked input.
 * Rejects strings made of a single repeated character, which satisfy the modulo
 * 11 rule but are never issued.
 */
export const isValidCNPJ = (value: string | null | undefined): boolean => {
  const normalized = normalizeCNPJ(value);

  if (normalized.length !== CNPJ_LENGTH) return false;
  if (!NORMALIZED_CNPJ.test(normalized)) return false;
  if (new Set(normalized).size === 1) return false;

  const base = normalized.slice(0, CNPJ_BASE_LENGTH);
  const checkDigits = normalized.slice(CNPJ_BASE_LENGTH);

  const first = expectedCheckDigit(base, FIRST_CHECK_DIGIT_WEIGHTS);
  if (first !== Number(checkDigits[0])) return false;

  const second = expectedCheckDigit(`${base}${first}`, SECOND_CHECK_DIGIT_WEIGHTS);
  return second === Number(checkDigits[1]);
};

/**
 * Apply the conventional CNPJ mask for display: XX.XXX.XXX/XXXX-XX.
 * Returns the input unchanged when it is not 14 characters long, so display
 * never throws on unexpected stored data.
 */
export const formatCNPJ = (value: string | null | undefined): string => {
  const normalized = normalizeCNPJ(value);
  if (normalized.length !== CNPJ_LENGTH) return normalized;

  return `${normalized.slice(0, 2)}.${normalized.slice(2, 5)}.${normalized.slice(5, 8)}/${normalized.slice(
    8,
    12
  )}-${normalized.slice(12)}`;
};
