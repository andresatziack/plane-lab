/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { Clock } from "lucide-react";
import { useTranslation } from "@plane/i18n";
import { cn } from "@plane/utils";
import type { TServiceTimesheetReport } from "@plane/types";

type Props = {
  data: TServiceTimesheetReport;
  days: string[];
};

/** Format a date string as a short weekday + day number (e.g. "Mon 12"). */
function formatDayHeader(dateStr: string): string {
  const d = new Date(dateStr + "T00:00:00");
  const weekday = d.toLocaleDateString(undefined, { weekday: "short" });
  return `${weekday} ${d.getDate()}`;
}

/** Format hours for display: show nothing for zero, otherwise show one decimal place. */
function formatHours(value: string | undefined): string {
  if (!value) return "";
  const num = parseFloat(value);
  if (num === 0) return "";
  return num % 1 === 0 ? num.toString() : num.toFixed(1);
}

/** Check if a date is a weekend (Saturday or Sunday). */
function isWeekend(dateStr: string): boolean {
  const d = new Date(dateStr + "T00:00:00");
  const day = d.getDay();
  return day === 0 || day === 6;
}

/** Split an array of day strings into chunks of 7 for weekly rows on mobile. */
function chunkDays(days: string[]): string[][] {
  const chunks: string[][] = [];
  for (let i = 0; i < days.length; i += 7) {
    chunks.push(days.slice(i, i + 7));
  }
  return chunks;
}

export function TimesheetGrid({ data, days }: Props) {
  const { t } = useTranslation();

  if (data.rows.length === 0) {
    return (
      <div className="flex h-64 flex-col items-center justify-center gap-3">
        <Clock className="text-custom-text-400 size-10" />
        <p className="text-sm text-custom-text-300">{t("service_timesheet.empty")}</p>
      </div>
    );
  }

  return (
    <>
      {/* Desktop grid - hidden on mobile */}
      <div className="hidden overflow-x-auto md:block">
        <table className="text-sm w-full min-w-max border-collapse">
          <thead>
            <tr className="border-b border-subtle bg-surface-2">
              <th className="text-custom-text-300 sticky left-0 z-10 bg-surface-2 px-3 py-2 text-left text-13 leading-5 font-medium">
                {t("service_timesheet.technician")}
              </th>
              {days.map((day) => (
                <th
                  key={day}
                  className={cn(
                    "text-custom-text-300 min-w-[56px] px-2 py-2 text-center text-13 leading-5 font-medium"
                  )}
                  style={isWeekend(day) ? { backgroundColor: "rgba(255, 251, 235, 0.5)" } : undefined}
                >
                  {formatDayHeader(day)}
                </th>
              ))}
              <th className="text-custom-text-100 min-w-[64px] px-3 py-2 text-center text-13 leading-5 font-semibold">
                {t("service_timesheet.total")}
              </th>
            </tr>
          </thead>
          <tbody>
            {data.rows.map((row) => (
              <tr key={row.author_id} className="hover:bg-custom-background-80/30 border-b border-subtle">
                <td className="text-custom-text-200 sticky left-0 z-10 bg-surface-2 px-3 py-2 text-13 leading-5 font-medium">
                  {row.author_name}
                </td>
                {days.map((day) => {
                  const cell = row.days[day];
                  const hours = formatHours(cell?.logged_hours);
                  const isZero = cell && parseFloat(cell.logged_hours) === 0;
                  return (
                    <td
                      key={day}
                      className={cn(
                        "text-custom-text-200 px-2 py-2 text-center text-13 leading-5",
                        hours ? "font-medium" : isZero ? "text-custom-text-400" : ""
                      )}
                      style={isWeekend(day) ? { backgroundColor: "rgba(255, 251, 235, 0.5)" } : undefined}
                    >
                      {hours || (isZero ? "0" : "")}
                    </td>
                  );
                })}
                <td className="bg-custom-background-90 text-custom-text-100 px-3 py-2 text-center text-13 leading-5 font-semibold">
                  {formatHours(row.total_logged)}
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr className="bg-custom-background-90 border-t border-subtle">
              <td className="bg-custom-background-90 text-custom-text-100 sticky left-0 z-10 px-3 py-2 text-13 leading-5 font-semibold">
                {t("service_timesheet.total")}
              </td>
              {days.map((day) => {
                const col = data.column_totals[day];
                const hours = formatHours(col?.logged_hours);
                return (
                  <td
                    key={day}
                    className="text-custom-text-100 px-2 py-2 text-center text-13 leading-5 font-semibold"
                    style={isWeekend(day) ? { backgroundColor: "rgba(255, 251, 235, 0.5)" } : undefined}
                  >
                    {hours}
                  </td>
                );
              })}
              <td className="text-custom-text-100 px-3 py-2 text-center text-13 leading-5 font-bold">
                {formatHours(data.grand_total.logged_hours)}
              </td>
            </tr>
          </tfoot>
        </table>
      </div>

      {/* Mobile cards - shown only on small screens */}
      <div className="flex flex-col gap-3 p-4 md:hidden">
        {data.rows.map((row) => (
          <div key={row.author_id} className="rounded-lg border border-subtle bg-surface-2 p-3">
            <div className="mb-2 flex items-center justify-between">
              <span className="text-sm text-custom-text-100 font-medium">{row.author_name}</span>
              <span className="text-xs text-custom-text-200 font-semibold">
                {formatHours(row.total_logged)}h {t("service_timesheet.total").toLowerCase()}
              </span>
            </div>
            {chunkDays(days).map((weekDays) => (
              <div key={weekDays[0]} className="mb-1 grid grid-cols-7 gap-1">
                {weekDays.map((day) => {
                  const cell = row.days[day];
                  const hours = formatHours(cell?.logged_hours);
                  return (
                    <div
                      key={day}
                      className={cn(
                        "flex flex-col items-center rounded p-1",
                        isWeekend(day) && "bg-custom-background-80/50",
                        hours && "bg-custom-primary-100/10"
                      )}
                    >
                      <span className="text-custom-text-400 text-[10px]">
                        {new Date(day + "T00:00:00").toLocaleDateString(undefined, { weekday: "narrow" })}
                      </span>
                      <span className="text-custom-text-400 text-[10px]">{new Date(day + "T00:00:00").getDate()}</span>
                      <span
                        className={cn("text-xs font-medium", hours ? "text-custom-text-100" : "text-custom-text-400")}
                      >
                        {hours || "-"}
                      </span>
                    </div>
                  );
                })}
              </div>
            ))}
          </div>
        ))}
      </div>
    </>
  );
}
