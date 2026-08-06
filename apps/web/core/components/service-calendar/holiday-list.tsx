/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { observer } from "mobx-react";
import { CalendarDays, ChevronLeft, ChevronRight, Pencil, Repeat, Trash2 } from "lucide-react";
import useSWR from "swr";
// plane imports
import { useTranslation } from "@plane/i18n";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import type { IServiceHoliday } from "@plane/types";
import { AlertModalCore, EModalPosition, EModalWidth, ModalCore, Tooltip } from "@plane/ui";
// hooks
import { useServiceCatalog } from "@/hooks/store/use-service-catalog";
// local imports
import { CALENDAR_ERROR_KEYS, extractCalendarErrorCode } from "./calendar.helpers";
import type { THolidayFormValues } from "./holiday-form";
import { HolidayForm } from "./holiday-form";

type Props = {
  workspaceSlug: string;
};

/**
 * The holiday calendar: an annual view plus the rows behind it.
 *
 * The **annual view is the primary one**, because section 1 of the phase brief asks for a
 * year an admin can eyeball rather than a list to be trusted. It comes from the server with
 * recurrence already expanded, which the stored rows cannot answer: a recurring holiday is
 * stored once and observed every year.
 *
 * The row list below it is what gets edited, and it shows the stored form -- including the
 * recurrence flag, since that is what decides whether an edit touches one year or all of
 * them.
 */
export const HolidayList = observer(function HolidayList({ workspaceSlug }: Props) {
  // states
  const [year, setYear] = useState(new Date().getFullYear());
  const [editingHoliday, setEditingHoliday] = useState<IServiceHoliday | null>(null);
  const [deletingHoliday, setDeletingHoliday] = useState<IServiceHoliday | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  // plane hooks
  const { t } = useTranslation();
  // store hooks
  const { holidayIds, holidays, fetchHolidayCalendar, updateHoliday, removeHoliday } = useServiceCatalog();

  // Re-fetched per year rather than cached across years: the expansion is server side and
  // the payload is a few dozen rows.
  const { data: calendar } = useSWR(
    workspaceSlug ? `SERVICE_HOLIDAY_CALENDAR_${workspaceSlug}_${year}` : null,
    workspaceSlug ? () => fetchHolidayCalendar(workspaceSlug, year) : null
  );

  const translateError = (error: unknown) => {
    const code = extractCalendarErrorCode(error);
    return code ? t(CALENDAR_ERROR_KEYS[code] ?? "common.errors.default.message") : t("common.errors.default.message");
  };

  const handleUpdate = async (values: THolidayFormValues) => {
    if (!editingHoliday) return;
    try {
      await updateHoliday(workspaceSlug, editingHoliday.id, values);
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t("workspace_settings.settings.worklog.holidays.toasts.updated.title"),
        message: t("workspace_settings.settings.worklog.holidays.toasts.updated.message"),
      });
      setEditingHoliday(null);
    } catch (error) {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("workspace_settings.settings.worklog.holidays.toasts.not_updated.title"),
        message: translateError(error),
      });
    }
  };

  const handleDelete = async () => {
    if (!deletingHoliday) return;
    setIsDeleting(true);
    try {
      await removeHoliday(workspaceSlug, deletingHoliday.id);
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t("workspace_settings.settings.worklog.holidays.toasts.removed.title"),
        message: t("workspace_settings.settings.worklog.holidays.toasts.removed.message"),
      });
      setDeletingHoliday(null);
    } catch (error) {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("workspace_settings.settings.worklog.holidays.toasts.not_removed.title"),
        message: translateError(error),
      });
    } finally {
      setIsDeleting(false);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      {/* Annual view */}
      <div className="rounded-md border border-subtle">
        <div className="flex items-center justify-between border-b border-subtle px-3 py-2">
          <span className="text-sm flex items-center gap-2 font-medium text-primary">
            <CalendarDays className="size-4" />
            {t("workspace_settings.settings.worklog.holidays.observed_in", { year })}
          </span>
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => setYear((current) => current - 1)}
              aria-label={t("workspace_settings.settings.worklog.holidays.previous_year")}
              className="rounded p-1 text-tertiary hover:bg-surface-2 hover:text-primary"
            >
              <ChevronLeft className="size-4" />
            </button>
            <span className="text-sm w-12 text-center text-primary">{year}</span>
            <button
              type="button"
              onClick={() => setYear((current) => current + 1)}
              aria-label={t("workspace_settings.settings.worklog.holidays.next_year")}
              className="rounded p-1 text-tertiary hover:bg-surface-2 hover:text-primary"
            >
              <ChevronRight className="size-4" />
            </button>
          </div>
        </div>

        {calendar && calendar.holidays.length === 0 ? (
          <p className="text-xs px-3 py-3 text-tertiary">
            {t("workspace_settings.settings.worklog.holidays.none_observed", { year })}
          </p>
        ) : (
          <ul className="grid grid-cols-1 gap-x-4 px-3 py-2 sm:grid-cols-2 lg:grid-cols-3">
            {(calendar?.holidays ?? []).map((occurrence) => (
              <li key={`${occurrence.id}-${occurrence.date}`} className="text-xs flex items-center gap-2 py-1">
                <span className="font-mono text-tertiary">{occurrence.date}</span>
                <span className="truncate text-primary">{occurrence.name}</span>
                {occurrence.is_recurring && (
                  <Tooltip
                    tooltipContent={t("workspace_settings.settings.worklog.holidays.recurring_badge")}
                    position="top"
                  >
                    <Repeat className="size-3 shrink-0 text-tertiary" />
                  </Tooltip>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* The stored rows, which are what gets edited */}
      {holidayIds.length > 0 && (
        <div className="rounded-md border border-subtle">
          <div className="border-b border-subtle px-3 py-2">
            <span className="text-sm font-medium text-primary">
              {t("workspace_settings.settings.worklog.holidays.registered")}
            </span>
          </div>
          <ul className="divide-y divide-subtle">
            {holidayIds.map((id) => {
              const holiday = holidays?.[id];
              if (!holiday) return null;

              return (
                <li key={id} className="flex items-center justify-between gap-2 px-3 py-2">
                  <div className="flex min-w-0 items-center gap-2">
                    <span className="font-mono text-xs text-tertiary">{holiday.date}</span>
                    <span className="text-sm truncate text-primary">{holiday.name}</span>
                    {holiday.is_recurring && (
                      <span className="shrink-0 rounded bg-surface-2 px-1.5 py-0.5 text-[11px] text-tertiary">
                        {t("workspace_settings.settings.worklog.holidays.recurring_badge")}
                      </span>
                    )}
                    <span className="text-xs shrink-0 text-tertiary">
                      {t(`workspace_settings.settings.worklog.holidays.scopes.${holiday.scope}`)}
                    </span>
                    {!holiday.is_active && (
                      <span className="shrink-0 rounded bg-surface-2 px-1.5 py-0.5 text-[11px] text-tertiary">
                        {t("workspace_settings.settings.worklog.inactive")}
                      </span>
                    )}
                    {holiday.external_source === "csv_import" && (
                      <Tooltip
                        tooltipContent={t("workspace_settings.settings.worklog.holidays.imported_tooltip")}
                        position="top"
                      >
                        <span className="shrink-0 text-[11px] text-tertiary">CSV</span>
                      </Tooltip>
                    )}
                  </div>

                  <div className="flex shrink-0 items-center gap-1">
                    <button
                      type="button"
                      onClick={() => setEditingHoliday(holiday)}
                      aria-label={t("common.edit")}
                      className="rounded p-1 text-tertiary hover:bg-surface-2 hover:text-primary"
                    >
                      <Pencil className="size-3.5" />
                    </button>
                    <button
                      type="button"
                      onClick={() => setDeletingHoliday(holiday)}
                      aria-label={t("common.delete")}
                      className="hover:text-red-500 rounded p-1 text-tertiary hover:bg-surface-2"
                    >
                      <Trash2 className="size-3.5" />
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      <ModalCore
        isOpen={editingHoliday !== null}
        handleClose={() => setEditingHoliday(null)}
        position={EModalPosition.TOP}
        width={EModalWidth.XL}
      >
        {editingHoliday && (
          <HolidayForm
            key={editingHoliday.id}
            data={editingHoliday}
            handleClose={() => setEditingHoliday(null)}
            onSubmit={handleUpdate}
          />
        )}
      </ModalCore>

      <AlertModalCore
        isOpen={deletingHoliday !== null}
        handleClose={() => setDeletingHoliday(null)}
        handleSubmit={handleDelete}
        isSubmitting={isDeleting}
        title={t("workspace_settings.settings.worklog.holidays.delete.title")}
        content={
          deletingHoliday?.is_recurring
            ? t("workspace_settings.settings.worklog.holidays.delete.description_recurring", {
                name: deletingHoliday?.name ?? "",
              })
            : t("workspace_settings.settings.worklog.holidays.delete.description", {
                name: deletingHoliday?.name ?? "",
              })
        }
        primaryButtonText={{ loading: t("common.deleting"), default: t("common.delete") }}
      />
    </div>
  );
});
