/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { observer } from "mobx-react";
import { AlertTriangle } from "lucide-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import type { IServiceHolidayImportError } from "@plane/types";
import { EModalPosition, EModalWidth, ModalCore } from "@plane/ui";
// hooks
import { useServiceCatalog } from "@/hooks/store/use-service-catalog";
// local imports
import { CALENDAR_ERROR_KEYS, HOLIDAY_CSV_TEMPLATE } from "./calendar.helpers";

type Props = {
  isOpen: boolean;
  workspaceSlug: string;
  handleClose: () => void;
};

type TImportRejection = {
  code?: string;
  rows: IServiceHolidayImportError[];
};

/**
 * Bulk import of holidays from CSV.
 *
 * Text area rather than a file picker: the common case is an admin pasting a year's holidays
 * out of a spreadsheet, and a paste is fewer steps than an export-then-upload. A file input
 * can be layered on later without changing the endpoint.
 *
 * **Every rejected row is listed, with its line number.** The import is all-or-nothing, so a
 * rejection means nothing was stored -- which only works as a design if the admin can fix
 * every problem in one pass. Reporting the first error and stopping would turn a paste into
 * a dozen round trips.
 */
export const HolidayCsvImportModal = observer(function HolidayCsvImportModal(props: Props) {
  const { isOpen, workspaceSlug, handleClose } = props;
  // plane hooks
  const { t } = useTranslation();
  // store hooks
  const { importHolidays } = useServiceCatalog();
  // states
  const [csvContent, setCsvContent] = useState("");
  const [isImporting, setIsImporting] = useState(false);
  const [rejection, setRejection] = useState<TImportRejection | null>(null);

  const reset = () => {
    setCsvContent("");
    setRejection(null);
  };

  const handleImport = async () => {
    setIsImporting(true);
    setRejection(null);

    try {
      const result = await importHolidays(workspaceSlug, csvContent);
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t("workspace_settings.settings.worklog.holidays.import.toasts.imported.title"),
        message: t("workspace_settings.settings.worklog.holidays.import.toasts.imported.message", {
          count: result.imported,
        }),
      });
      reset();
      handleClose();
    } catch (error) {
      const detail = error as { error?: string; rows?: IServiceHolidayImportError[] };
      setRejection({ code: detail?.error, rows: detail?.rows ?? [] });
    } finally {
      setIsImporting(false);
    }
  };

  return (
    <ModalCore
      isOpen={isOpen}
      handleClose={() => {
        reset();
        handleClose();
      }}
      position={EModalPosition.TOP}
      width={EModalWidth.XXL}
    >
      <div className="flex flex-col gap-4 p-5">
        <div className="flex flex-col gap-1">
          <h3 className="text-lg font-medium text-primary">
            {t("workspace_settings.settings.worklog.holidays.import.title")}
          </h3>
          <p className="text-xs text-tertiary">
            {t("workspace_settings.settings.worklog.holidays.import.description")}
          </p>
        </div>

        <div className="flex flex-col gap-1">
          <label className="text-sm font-medium text-secondary" htmlFor="holiday-csv">
            {t("workspace_settings.settings.worklog.holidays.import.content")}
          </label>
          <textarea
            id="holiday-csv"
            value={csvContent}
            onChange={(event) => setCsvContent(event.target.value)}
            rows={10}
            spellCheck={false}
            placeholder={HOLIDAY_CSV_TEMPLATE}
            className="font-mono text-xs resize-y rounded-md border border-subtle bg-surface-1 px-3 py-2"
          />
          <div className="flex items-center justify-between">
            <span className="text-xs text-tertiary">
              {t("workspace_settings.settings.worklog.holidays.import.format_hint")}
            </span>
            <button
              type="button"
              onClick={() => setCsvContent(HOLIDAY_CSV_TEMPLATE)}
              className="text-xs text-primary underline"
            >
              {t("workspace_settings.settings.worklog.holidays.import.use_template")}
            </button>
          </div>
        </div>

        {rejection && (
          <div className="border-red-500/40 bg-red-500/10 flex flex-col gap-1.5 rounded-md border px-3 py-2">
            <span className="text-sm text-red-600 flex items-center gap-2 font-medium">
              <AlertTriangle className="size-3.5 shrink-0" />
              {t("workspace_settings.settings.worklog.holidays.import.errors.nothing_imported")}
            </span>

            {rejection.rows.length > 0 ? (
              <ul className="ml-5 flex max-h-40 flex-col gap-0.5 overflow-y-auto">
                {rejection.rows.map((row) => (
                  <li key={`${row.line}-${row.error}`} className="text-xs text-primary">
                    <span className="font-medium">
                      {t("workspace_settings.settings.worklog.holidays.import.line", { line: row.line })}
                    </span>
                    {": "}
                    <span className="text-tertiary">
                      {t(CALENDAR_ERROR_KEYS[row.error] ?? "common.errors.default.message")}
                      {row.value ? ` (${row.value})` : ""}
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <span className="text-xs ml-5 text-tertiary">
                {t(CALENDAR_ERROR_KEYS[rejection.code ?? ""] ?? "common.errors.default.message")}
              </span>
            )}
          </div>
        )}

        <div className="flex justify-end gap-2">
          <Button
            variant="secondary"
            size="sm"
            onClick={() => {
              reset();
              handleClose();
            }}
          >
            {t("common.cancel")}
          </Button>
          <Button
            variant="primary"
            size="sm"
            onClick={handleImport}
            loading={isImporting}
            disabled={csvContent.trim().length === 0}
          >
            {t("workspace_settings.settings.worklog.holidays.import.submit")}
          </Button>
        </div>
      </div>
    </ModalCore>
  );
});
