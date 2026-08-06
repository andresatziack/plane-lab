/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { TServiceDayScope } from "@plane/types";

/** `HH:MM`, with `24:00` accepted as "the end of the day". */
const CLOCK_PATTERN = /^([01]\d|2[0-4]):([0-5]\d)$/;

/**
 * Whether a border is a clock time the API will accept.
 *
 * Validated in the browser as well as on the server so the admin gets the error next to the
 * field instead of as a toast. The server remains the authority -- this is convenience, not
 * the rule.
 *
 * `24:00` is legal and `24:30` is not. The pattern alone would allow the second, because it
 * has to admit hour 24 at all, so the minutes are checked separately for that one hour.
 */
export const isValidClockTime = (value: string): boolean => {
  if (!CLOCK_PATTERN.test(value)) return false;
  const [hours, minutes] = value.split(":");
  return hours !== "24" || minutes === "00";
};

/**
 * Minutes since midnight, for ordering and for the zero-length check.
 *
 * Deliberately NOT used to build a payload: the API speaks `HH:MM` and the integer is the
 * server's business. This exists so the form can compare two borders without asking the
 * server first.
 */
export const clockToMinutes = (value: string): number => {
  const [hours, minutes] = value.split(":").map(Number);
  return hours * 60 + minutes;
};

/** True when a window would have no duration at all. The whole day is `00:00-24:00`. */
export const hasZeroLength = (start: string, end: string): boolean =>
  isValidClockTime(start) && isValidClockTime(end) && clockToMinutes(start) === clockToMinutes(end);

/**
 * Map an API error code to its translation key.
 *
 * The API returns UPPER_SNAKE codes and never Portuguese, following the convention the rest
 * of the feature uses. Anything unmapped falls back to a generic message rather than
 * showing a raw code to an admin.
 */
export const CALENDAR_ERROR_KEYS: Record<string, string> = {
  // Windows
  WINDOWS_OVERLAP_AT_SAME_PRIORITY: "workspace_settings.settings.worklog.windows.errors.overlap",
  WINDOWS_WOULD_NOT_COVER_THE_WEEK: "workspace_settings.settings.worklog.windows.errors.would_break_coverage",
  WINDOW_BORDERS_MUST_DIFFER: "workspace_settings.settings.worklog.windows.errors.borders_must_differ",
  INVALID_CLOCK_FORMAT: "workspace_settings.settings.worklog.windows.errors.invalid_clock",
  HOUR_TYPE_MUST_BELONG_TO_SAME_WORKSPACE: "workspace_settings.settings.worklog.windows.errors.foreign_hour_type",
  HOUR_TYPE_IN_USE_BY_CLASSIFICATION_WINDOWS: "workspace_settings.settings.worklog.errors.hour_type_has_windows",
  // Holidays
  HOLIDAY_ALREADY_COVERED_BY_RECURRING: "workspace_settings.settings.worklog.holidays.errors.covered_by_recurring",
  RECURRING_HOLIDAY_COLLIDES_WITH_SPECIFIC:
    "workspace_settings.settings.worklog.holidays.errors.collides_with_specific",
  NAME_IS_REQUIRED: "workspace_settings.settings.worklog.holidays.errors.name_required",
  // CSV import
  CSV_HEADER_IS_INVALID: "workspace_settings.settings.worklog.holidays.import.errors.header",
  CSV_ROW_IS_INVALID: "workspace_settings.settings.worklog.holidays.import.errors.row",
  CSV_DATE_IS_INVALID: "workspace_settings.settings.worklog.holidays.import.errors.date",
  CSV_SCOPE_IS_INVALID: "workspace_settings.settings.worklog.holidays.import.errors.scope",
  CSV_HAS_INVALID_ROWS: "workspace_settings.settings.worklog.holidays.import.errors.has_invalid_rows",
  CSV_HAS_NO_ROWS: "workspace_settings.settings.worklog.holidays.import.errors.no_rows",
};

/** Pull the code out of the several error shapes DRF can produce. */
export const extractCalendarErrorCode = (error: unknown): string | undefined => {
  const detail = error as { error?: unknown } | undefined;
  const raw = detail?.error;

  if (typeof raw === "string") return raw;
  if (Array.isArray(raw) && typeof raw[0] === "string") return raw[0];

  return undefined;
};

/** The day scopes in the order the panel shows them: the week, then the holiday scope. */
export const DAY_SCOPE_ORDER: TServiceDayScope[] = [
  "monday",
  "tuesday",
  "wednesday",
  "thursday",
  "friday",
  "saturday",
  "sunday",
  "holiday",
];

/** A CSV template an admin can start from, offered by the import dialog. */
export const HOLIDAY_CSV_TEMPLATE = [
  "name,date,is_recurring,scope",
  "Confraternização Universal,2026-01-01,true,national",
  "Carnaval,2026-02-17,false,national",
  "Aniversário da cidade,2026-03-15,true,municipal",
].join("\n");
