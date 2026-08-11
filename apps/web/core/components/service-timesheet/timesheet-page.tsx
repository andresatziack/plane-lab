/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useCallback, useMemo, useState } from "react";
import { observer } from "mobx-react";
import useSWR from "swr";
import { useTranslation } from "@plane/i18n";
import { Loader } from "@plane/ui";
import { useWorkspace } from "@/hooks/store/use-workspace";
import { ServiceReportsService } from "@/services/service-reports.service";
import { TimesheetGrid } from "./timesheet-grid";
import { TimesheetToolbar, type TimesheetViewMode } from "./timesheet-toolbar";

const serviceReports = new ServiceReportsService();

/** Get Monday of the week containing `date`. */
function getMonday(date: Date): Date {
  const d = new Date(date);
  const day = d.getDay();
  const diff = day === 0 ? -6 : 1 - day;
  d.setDate(d.getDate() + diff);
  return d;
}

/** Get the first day of the month containing `date`. */
function getFirstOfMonth(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth(), 1);
}

/** Format a Date as YYYY-MM-DD. */
function toISO(date: Date): string {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

/** Generate an array of date strings for all days in the period. */
function generateDays(start: Date, end: Date): string[] {
  const days: string[] = [];
  const endTime = end.getTime();
  for (let d = new Date(start); d.getTime() <= endTime; d.setDate(d.getDate() + 1)) {
    days.push(toISO(d));
  }
  return days;
}

/** Format the period label for display. */
function formatPeriodLabel(start: Date, end: Date, mode: TimesheetViewMode): string {
  if (mode === "month") {
    return start.toLocaleDateString(undefined, { year: "numeric", month: "long" });
  }
  const startStr = start.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  const endStr = end.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
  return `${startStr} - ${endStr}`;
}

export const TimesheetPage = observer(function TimesheetPage() {
  const { t } = useTranslation();
  const { currentWorkspace } = useWorkspace();
  const workspaceSlug = currentWorkspace?.slug;

  const [viewMode, setViewMode] = useState<TimesheetViewMode>("week");
  const [anchorDate, setAnchorDate] = useState<Date>(() => new Date());

  const { startDate, endDate } = useMemo(() => {
    if (viewMode === "week") {
      const monday = getMonday(anchorDate);
      const sunday = new Date(monday);
      sunday.setDate(monday.getDate() + 6);
      return { startDate: monday, endDate: sunday };
    }
    const first = getFirstOfMonth(anchorDate);
    const last = new Date(first.getFullYear(), first.getMonth() + 1, 0);
    return { startDate: first, endDate: last };
  }, [viewMode, anchorDate]);

  const days = useMemo(() => generateDays(startDate, endDate), [startDate, endDate]);

  const periodLabel = useMemo(() => formatPeriodLabel(startDate, endDate, viewMode), [startDate, endDate, viewMode]);

  const swrKey = workspaceSlug ? `timesheet-${workspaceSlug}-${toISO(startDate)}-${toISO(endDate)}` : null;

  const { data, isLoading } = useSWR(swrKey, () =>
    serviceReports.fetchTimesheet(workspaceSlug!, {
      worked_on_from: toISO(startDate),
      worked_on_to: toISO(endDate),
    })
  );

  const handlePrev = useCallback(() => {
    setAnchorDate((current) => {
      if (viewMode === "week") {
        const d = new Date(current);
        d.setDate(d.getDate() - 7);
        return d;
      }
      return new Date(current.getFullYear(), current.getMonth() - 1, 1);
    });
  }, [viewMode]);

  const handleNext = useCallback(() => {
    setAnchorDate((current) => {
      if (viewMode === "week") {
        const d = new Date(current);
        d.setDate(d.getDate() + 7);
        return d;
      }
      return new Date(current.getFullYear(), current.getMonth() + 1, 1);
    });
  }, [viewMode]);

  const handleToday = useCallback(() => {
    setAnchorDate(new Date());
  }, []);

  const handleViewModeChange = useCallback((mode: TimesheetViewMode) => {
    setViewMode(mode);
  }, []);

  if (!workspaceSlug) return null;

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <TimesheetToolbar
        viewMode={viewMode}
        onViewModeChange={handleViewModeChange}
        periodLabel={periodLabel}
        onPrev={handlePrev}
        onNext={handleNext}
        onToday={handleToday}
      />
      <div className="flex-1 overflow-auto">
        {isLoading ? (
          <div className="p-6">
            <Loader className="space-y-3">
              <Loader.Item height="40px" />
              <Loader.Item height="40px" />
              <Loader.Item height="40px" />
              <Loader.Item height="40px" />
              <Loader.Item height="40px" />
            </Loader>
          </div>
        ) : data ? (
          <TimesheetGrid data={data} days={days} />
        ) : (
          <div className="text-xs text-custom-text-300 flex h-64 items-center justify-center">
            {t("service_timesheet.empty")}
          </div>
        )}
      </div>
    </div>
  );
});
