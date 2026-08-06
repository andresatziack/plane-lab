/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { observer } from "mobx-react";
import { Moon, Pencil, Plus, Sun, Trash2 } from "lucide-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import type { IServiceClassificationWindow, TServiceDayScope } from "@plane/types";
import { EModalPosition, EModalWidth, ModalCore, Tooltip } from "@plane/ui";
// hooks
import { useServiceCatalog } from "@/hooks/store/use-service-catalog";
// local imports
import { CALENDAR_ERROR_KEYS, DAY_SCOPE_ORDER, extractCalendarErrorCode } from "./calendar.helpers";
import { DeleteWindowModal } from "./delete-window-modal";
import type { TWindowFormValues } from "./window-form";
import { WindowForm } from "./window-form";

type Props = {
  workspaceSlug: string;
};

/**
 * The classification windows, grouped by the kind of day they apply to.
 *
 * Grouped by day rather than by hour type, deliberately. Coverage is a per-day question --
 * "is every minute of Tuesday classified?" -- so a day-shaped list is the one where a gap is
 * visible. An hour-type-shaped list would scatter Tuesday across three sections.
 */
export const WindowList = observer(function WindowList({ workspaceSlug }: Props) {
  // states
  const [creatingForScope, setCreatingForScope] = useState<TServiceDayScope | null>(null);
  const [editingWindow, setEditingWindow] = useState<IServiceClassificationWindow | null>(null);
  const [deletingWindow, setDeletingWindow] = useState<IServiceClassificationWindow | null>(null);
  // plane hooks
  const { t } = useTranslation();
  // store hooks
  const { windowsByDayScope, coverage, createClassificationWindow, updateClassificationWindow } = useServiceCatalog();

  const translateError = (error: unknown) => {
    const code = extractCalendarErrorCode(error);
    return code ? t(CALENDAR_ERROR_KEYS[code] ?? "common.errors.default.message") : t("common.errors.default.message");
  };

  const handleCreate = async (values: TWindowFormValues) => {
    try {
      await createClassificationWindow(workspaceSlug, values);
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t("workspace_settings.settings.worklog.windows.toasts.created.title"),
        message: t("workspace_settings.settings.worklog.windows.toasts.created.message"),
      });
      setCreatingForScope(null);
    } catch (error) {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("workspace_settings.settings.worklog.windows.toasts.not_created.title"),
        message: translateError(error),
      });
    }
  };

  const handleUpdate = async (values: TWindowFormValues) => {
    if (!editingWindow) return;
    try {
      await updateClassificationWindow(workspaceSlug, editingWindow.id, values);
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t("workspace_settings.settings.worklog.windows.toasts.updated.title"),
        message: t("workspace_settings.settings.worklog.windows.toasts.updated.message"),
      });
      setEditingWindow(null);
    } catch (error) {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("workspace_settings.settings.worklog.windows.toasts.not_updated.title"),
        message: translateError(error),
      });
    }
  };

  return (
    <div className="flex flex-col gap-3">
      {DAY_SCOPE_ORDER.map((scope) => {
        const windows = windowsByDayScope[scope] ?? [];
        // The gaps come from the server's own coverage report, so the panel and the engine
        // cannot disagree about which minutes are uncovered.
        const gaps = coverage?.gaps?.[scope] ?? [];

        return (
          <div key={scope} className="rounded-md border border-subtle">
            <div className="flex items-center justify-between border-b border-subtle px-3 py-2">
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium text-primary">
                  {t(`workspace_settings.settings.worklog.windows.day_scope.${scope}`)}
                </span>
                {gaps.length > 0 && (
                  <Tooltip
                    tooltipContent={t("workspace_settings.settings.worklog.windows.health.gap_tooltip")}
                    position="top"
                  >
                    <span className="bg-amber-500/15 text-amber-700 rounded px-1.5 py-0.5 text-[11px] font-medium">
                      {gaps.join(", ")}
                    </span>
                  </Tooltip>
                )}
              </div>
              <Button variant="secondary" size="sm" prependIcon={<Plus />} onClick={() => setCreatingForScope(scope)}>
                {t("workspace_settings.settings.worklog.windows.add")}
              </Button>
            </div>

            {windows.length === 0 ? (
              <p className="text-xs px-3 py-3 text-tertiary">
                {t("workspace_settings.settings.worklog.windows.empty_day")}
              </p>
            ) : (
              <ul className="divide-y divide-subtle">
                {windows.map((classificationWindow) => (
                  <li key={classificationWindow.id} className="flex items-center justify-between gap-2 px-3 py-2">
                    <div className="flex min-w-0 items-center gap-2">
                      <span
                        className="size-2 shrink-0 rounded-full"
                        style={{ backgroundColor: classificationWindow.hour_type_color ?? "#60646C" }}
                      />
                      <span className="text-sm text-primary">
                        {classificationWindow.start_time}–{classificationWindow.end_time}
                      </span>
                      <span className="text-sm truncate text-tertiary">{classificationWindow.hour_type_name}</span>
                      <span className="text-xs shrink-0 text-tertiary">
                        {t("workspace_settings.settings.worklog.windows.priority_badge", {
                          value: classificationWindow.hour_type_priority,
                        })}
                      </span>
                      {classificationWindow.is_all_day && (
                        <Tooltip
                          tooltipContent={t("workspace_settings.settings.worklog.windows.whole_day_hint")}
                          position="top"
                        >
                          <Sun className="size-3.5 shrink-0 text-tertiary" />
                        </Tooltip>
                      )}
                      {classificationWindow.crosses_midnight && !classificationWindow.is_all_day && (
                        <Tooltip
                          tooltipContent={t("workspace_settings.settings.worklog.windows.crosses_midnight_hint")}
                          position="top"
                        >
                          <Moon className="size-3.5 shrink-0 text-tertiary" />
                        </Tooltip>
                      )}
                    </div>

                    <div className="flex shrink-0 items-center gap-1">
                      <button
                        type="button"
                        onClick={() => setEditingWindow(classificationWindow)}
                        aria-label={t("common.edit")}
                        className="rounded p-1 text-tertiary hover:bg-surface-2 hover:text-primary"
                      >
                        <Pencil className="size-3.5" />
                      </button>
                      <button
                        type="button"
                        onClick={() => setDeletingWindow(classificationWindow)}
                        aria-label={t("common.delete")}
                        className="hover:text-red-500 rounded p-1 text-tertiary hover:bg-surface-2"
                      >
                        <Trash2 className="size-3.5" />
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>
        );
      })}

      <ModalCore
        isOpen={creatingForScope !== null}
        handleClose={() => setCreatingForScope(null)}
        position={EModalPosition.TOP}
        width={EModalWidth.XL}
      >
        {creatingForScope !== null && (
          <WindowForm
            defaultDayScope={creatingForScope}
            handleClose={() => setCreatingForScope(null)}
            onSubmit={handleCreate}
          />
        )}
      </ModalCore>

      <ModalCore
        isOpen={editingWindow !== null}
        handleClose={() => setEditingWindow(null)}
        position={EModalPosition.TOP}
        width={EModalWidth.XL}
      >
        {editingWindow && (
          <WindowForm
            key={editingWindow.id}
            data={editingWindow}
            handleClose={() => setEditingWindow(null)}
            onSubmit={handleUpdate}
          />
        )}
      </ModalCore>

      <DeleteWindowModal
        isOpen={deletingWindow !== null}
        workspaceSlug={workspaceSlug}
        classificationWindow={deletingWindow}
        handleClose={() => setDeletingWindow(null)}
      />
    </div>
  );
});
