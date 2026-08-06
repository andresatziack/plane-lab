/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { IServiceLog, IServiceLogPayload, TServiceLogEntryMode } from "@plane/types";

/**
 * Where the chosen entry mode is remembered.
 *
 * Section 1 of the phase brief: "O modo escolhido deve ser lembrado como preferência
 * do usuário." Kept in localStorage rather than on the server -- it is a UI habit, not
 * a business parameter, and a round trip to read it would delay opening the form.
 */
const ENTRY_MODE_STORAGE_KEY = "service-log-entry-mode";

export const readPreferredEntryMode = (): TServiceLogEntryMode => {
  if (typeof window === "undefined") return "duration";
  const stored = window.localStorage.getItem(ENTRY_MODE_STORAGE_KEY);
  return stored === "interval" ? "interval" : "duration";
};

export const writePreferredEntryMode = (entryMode: TServiceLogEntryMode) => {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(ENTRY_MODE_STORAGE_KEY, entryMode);
};

/**
 * `HH:MM` from the `HH:MM:SS` the API returns, for a time input.
 *
 * The seconds are always zero -- section 4 truncates them -- but a `<input type="time">`
 * will not accept the longer form.
 */
export const toTimeInputValue = (value: string | null): string => (value ? value.slice(0, 5) : "");

/**
 * Whether the hour type shown on a row differs from what the client is charged for.
 *
 * Drives the R11 dual reading in the list. A multiplier of exactly 1 means the two
 * readings are the same number, and repeating it would be noise -- so the converted
 * figure is only shown when it actually differs.
 *
 * Compared as a string. Section 4b and the type definition both forbid parsing these
 * into numbers, and "1.00" is the only value the API can send for a multiplier of one.
 */
export const hasDistinctClientReading = (serviceLog: IServiceLog): boolean => serviceLog.applied_multiplier !== "1.00";

/** True when a submission produced more than one row, i.e. the engine split it. */
export const isSplitBatch = (segments: IServiceLog[]): boolean => segments.length > 1;

/**
 * The payload that would recreate an existing entry, for the edit form.
 *
 * Duration mode sends the *rounded* logged hours rather than the raw minutes, because
 * that is what the technician sees on screen and R2 is idempotent -- re-submitting an
 * already rounded value cannot inflate it. Sending the raw minutes back would silently
 * re-round and could change the value on an edit that touched nothing else.
 */
export const toEditPayload = (segments: IServiceLog[]): IServiceLogPayload => {
  const first = segments[0];
  const isInterval = first.entry_mode === "interval";

  // A split entry spans from the first segment's start to the last one's end.
  const last = segments[segments.length - 1];

  return {
    worked_on: first.worked_on,
    description: first.description,
    entry_mode: first.entry_mode,
    duration: isInterval ? null : formatHoursForInput(totalLoggedHours(segments)),
    start_time: isInterval ? toTimeInputValue(first.start_time) : null,
    end_time: isInterval ? toTimeInputValue(last.end_time) : null,
    hour_type_id: first.hour_type,
    billing_type_id: first.billing_type,
  };
};

/**
 * Sum the logged hours of a batch as a decimal string, without float arithmetic.
 *
 * Works in whole minutes: every logged value is a multiple of 15 minutes by
 * construction (R2), so scaling by 60 is exact and the result is an integer. Parsing
 * the strings into floats and adding them would be the one place in the browser where
 * money-adjacent arithmetic could drift.
 */
const totalLoggedHours = (segments: IServiceLog[]): number =>
  segments.reduce((total, segment) => total + Math.round(Number(segment.logged_hours) * 60), 0);

/** Whole minutes back into something the duration parser reads, e.g. 75 -> "1h 15min". */
const formatHoursForInput = (minutes: number): string => {
  const hours = Math.floor(minutes / 60);
  const remaining = minutes % 60;

  if (hours && remaining) return `${hours}h ${remaining}min`;
  if (hours) return `${hours}h`;
  return `${remaining}min`;
};

/**
 * Map an API error code to its translation key.
 *
 * The API returns UPPER_SNAKE codes and never Portuguese, following the convention the
 * client entity established. Anything unmapped falls back to a generic message rather
 * than showing a raw code to a technician.
 */
export const SERVICE_LOG_ERROR_KEYS: Record<string, string> = {
  INVALID_DURATION_FORMAT: "work_item.service_log.form.duration_invalid",
  DURATION_MUST_BE_POSITIVE: "work_item.service_log.form.duration_invalid",
  DURATION_IS_REQUIRED: "work_item.service_log.form.duration_required",
  INTERVAL_ENDPOINTS_MUST_DIFFER: "work_item.service_log.form.interval_same",
  INTERVAL_REQUIRES_START_AND_END_TIME: "work_item.service_log.form.interval_required",
  WORKED_ON_CANNOT_BE_IN_THE_FUTURE: "work_item.service_log.form.worked_on_future",
  HOUR_TYPE_REQUIRED: "work_item.service_log.form.hour_type_required",
  HOUR_TYPE_NOT_FOUND: "work_item.service_log.errors.hour_type_not_found",
  BILLING_TYPE_NOT_FOUND: "work_item.service_log.errors.billing_type_not_found",
  TIME_TRACKING_DISABLED_FOR_PROJECT: "work_item.service_log.errors.time_tracking_disabled",
  ONLY_THE_AUTHOR_CAN_CHANGE_A_SERVICE_LOG: "work_item.service_log.errors.only_author",
  SERVICE_LOG_DELEGATION_NOT_AVAILABLE: "work_item.service_log.errors.delegation_unavailable",
};

/** Pull the code out of the several error shapes DRF can produce. */
export const extractServiceLogErrorCode = (error: unknown): string | undefined => {
  const detail = error as { error?: unknown } | undefined;
  const raw = detail?.error;

  if (typeof raw === "string") return raw;
  if (Array.isArray(raw) && typeof raw[0] === "string") return raw[0];

  return undefined;
};
