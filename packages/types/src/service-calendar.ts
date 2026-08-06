/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/**
 * Reach of a holiday. Recorded and displayed, with **no filtering logic** (decision D14).
 *
 * The column exists so a future phase can filter by locality without a migration. Adding
 * behaviour to it means revisiting D14 first.
 */
export type TServiceHolidayScope = "national" | "state" | "municipal";

/**
 * Which kind of day a classification window applies to.
 *
 * `holiday` is not a weekday and does not replace one: on a holiday the engine considers
 * the holiday windows **and** the windows of that calendar weekday together, resolving by
 * priority. That is what makes "a holiday falling on a Wednesday is 2.0 all day" a
 * consequence of configuration rather than a special case in code.
 */
export type TServiceDayScope =
  | "monday"
  | "tuesday"
  | "wednesday"
  | "thursday"
  | "friday"
  | "saturday"
  | "sunday"
  | "holiday";

/** The seven weekdays, in display order. `holiday` is deliberately not in here. */
export const SERVICE_WEEKDAY_SCOPES: TServiceDayScope[] = [
  "monday",
  "tuesday",
  "wednesday",
  "thursday",
  "friday",
  "saturday",
  "sunday",
];

/** Every scope the week has to cover for the engine to classify every instant. */
export const SERVICE_COVERABLE_SCOPES: TServiceDayScope[] = [...SERVICE_WEEKDAY_SCOPES, "holiday"];

/** A day the classification engine treats as a holiday. */
export interface IServiceHoliday {
  readonly id: string;
  readonly workspace_id: string;
  name: string;
  /** `YYYY-MM-DD`. For a recurring holiday the year records when it was first entered. */
  date: string;
  /**
   * Annual recurrence. Christmas repeats on the same date every year; Carnival moves and
   * has to be entered year by year.
   *
   * Load-bearing in the audit trail too: "added Christmas 2027" moves one day's invoice,
   * "added Christmas every year" moves a day in every future invoice.
   */
  is_recurring: boolean;
  scope: TServiceHolidayScope;
  is_active: boolean;
  /** `csv_import` for rows that came from a bulk import, so a bad import is identifiable. */
  readonly external_source: string | null;
  readonly external_id: string | null;
  readonly created_at: string;
  readonly updated_at: string;
  readonly created_by: string | null;
  readonly updated_by: string | null;
}

/** One holiday as it is *observed* in a particular year, with recurrence expanded. */
export interface IServiceHolidayOccurrence {
  readonly id: string;
  readonly name: string;
  /** `YYYY-MM-DD` in the requested year, not the stored year. */
  readonly date: string;
  readonly scope: TServiceHolidayScope;
  readonly is_recurring: boolean;
}

/** Response of the annual calendar endpoint. */
export interface IServiceHolidayCalendar {
  readonly year: number;
  readonly holidays: IServiceHolidayOccurrence[];
}

/**
 * A stretch of a kind of day that maps to one hour type.
 *
 * **Borders are `HH:MM` strings, and `24:00` is legal** -- it is how a window says "the end
 * of the day". The server stores whole minutes since midnight because `24:00` cannot be a
 * time value, but that is an implementation detail the API deliberately does not expose:
 * there is no `start_minute` here, and adding one would give callers two ways to say the
 * same thing.
 */
export interface IServiceClassificationWindow {
  readonly id: string;
  readonly workspace_id: string;
  hour_type: string;
  readonly hour_type_name: string | null;
  readonly hour_type_color: string | null;
  /** Lower wins. Seeded 10 for holidays, 20 for after hours, 30 for business hours. */
  readonly hour_type_priority: number | null;
  day_scope: TServiceDayScope;
  /** `HH:MM`. */
  start_time: string;
  /** `HH:MM`, and `24:00` for a window that runs to the end of the day. */
  end_time: string;
  /** True when `end_time` is at or before `start_time`, i.e. the window runs into the next day. */
  readonly crosses_midnight: boolean;
  readonly is_all_day: boolean;
  readonly duration_minutes: number;
  is_active: boolean;
  readonly created_at: string;
  readonly updated_at: string;
  readonly created_by: string | null;
  readonly updated_by: string | null;
}

/** Two windows the engine cannot decide between, because they share a day scope and a priority. */
export interface IServiceCoverageOverlap {
  readonly day_scope: TServiceDayScope;
  readonly priority: number;
  /** Both windows, already rendered readably, e.g. `["seg 18:00–08:00", "seg 10:00–11:00"]`. */
  readonly windows: string[];
  readonly hour_types: string[];
}

/**
 * The health of a workspace's classification configuration.
 *
 * Descriptive rather than a bare boolean on purpose. The coverage rule is a ratchet -- a
 * complete set may never become incomplete, but an already-incomplete one is allowed so an
 * admin can repair it -- which means a workspace can legitimately sit incomplete. An
 * incomplete set that were invisible would only ever surface as a blank hour type in the
 * work log form, where nobody would connect the two.
 */
export interface IServiceCoverageReport {
  readonly is_complete: boolean;
  readonly window_count: number;
  /** Uncovered ranges per day scope, already formatted, e.g. `{ monday: ["08:00–18:00"] }`. */
  readonly gaps: Partial<Record<TServiceDayScope, string[]>>;
  readonly overlaps: IServiceCoverageOverlap[];
}

/** One rejected row of a CSV import, with the line number a spreadsheet would show. */
export interface IServiceHolidayImportError {
  readonly line: number;
  readonly error: string;
  readonly value: string | null;
}

/** Response of a successful CSV import. */
export interface IServiceHolidayImportResult {
  readonly imported: number;
  readonly holidays: IServiceHoliday[];
}
