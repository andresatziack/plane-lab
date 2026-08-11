/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { CalendarDays, ChevronLeft, ChevronRight } from "lucide-react";
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { cn } from "@plane/utils";

export type TimesheetViewMode = "week" | "month";

type Props = {
  viewMode: TimesheetViewMode;
  onViewModeChange: (mode: TimesheetViewMode) => void;
  periodLabel: string;
  onPrev: () => void;
  onNext: () => void;
  onToday: () => void;
};

export function TimesheetToolbar({ viewMode, onViewModeChange, periodLabel, onPrev, onNext, onToday }: Props) {
  const { t } = useTranslation();

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-b border-subtle bg-surface-1 px-4 py-2 sm:px-6">
      {/* View mode toggle */}
      <div className="bg-custom-background-90 flex items-center gap-1 rounded-md border border-subtle p-0.5">
        <button
          type="button"
          onClick={() => onViewModeChange("week")}
          className={cn(
            "text-xs flex items-center gap-1 rounded px-2.5 py-1 font-medium transition-colors",
            viewMode === "week" ? "bg-custom-primary-100 text-white" : "text-custom-text-200 hover:text-custom-text-100"
          )}
        >
          <CalendarDays className="size-3" />
          {t("service_timesheet.view_week")}
        </button>
        <button
          type="button"
          onClick={() => onViewModeChange("month")}
          className={cn(
            "text-xs flex items-center gap-1 rounded px-2.5 py-1 font-medium transition-colors",
            viewMode === "month"
              ? "bg-custom-primary-100 text-white"
              : "text-custom-text-200 hover:text-custom-text-100"
          )}
        >
          <CalendarDays className="size-3" />
          {t("service_timesheet.view_month")}
        </button>
      </div>

      {/* Period navigation */}
      <div className="flex items-center gap-1.5">
        <Button variant="secondary" size="sm" onClick={onPrev}>
          <ChevronLeft className="size-3.5" />
        </Button>
        <span className="text-sm text-custom-text-100 min-w-[140px] text-center font-medium">{periodLabel}</span>
        <Button variant="secondary" size="sm" onClick={onNext}>
          <ChevronRight className="size-3.5" />
        </Button>
        <Button variant="secondary" size="sm" onClick={onToday}>
          {t("service_timesheet.today")}
        </Button>
      </div>
    </div>
  );
}
