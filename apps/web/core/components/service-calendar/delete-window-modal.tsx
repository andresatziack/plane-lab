/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { observer } from "mobx-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import type { IServiceClassificationWindow } from "@plane/types";
import { AlertModalCore } from "@plane/ui";
// hooks
import { useServiceCatalog } from "@/hooks/store/use-service-catalog";
// local imports
import { CALENDAR_ERROR_KEYS, extractCalendarErrorCode } from "./calendar.helpers";

type Props = {
  isOpen: boolean;
  workspaceSlug: string;
  /** Named in full because a bare `window` would shadow the DOM global. */
  classificationWindow: IServiceClassificationWindow | null;
  handleClose: () => void;
};

/**
 * Confirms removing a classification window.
 *
 * The copy warns that the range stops being classified, because that is the consequence the
 * admin will otherwise meet later as a blank hour type on a work log. The server may refuse
 * the delete outright when it would break a complete week -- that refusal is surfaced as the
 * error message rather than pre-empted here, since the browser does not evaluate coverage.
 */
export const DeleteWindowModal = observer(function DeleteWindowModal(props: Props) {
  const { isOpen, workspaceSlug, classificationWindow, handleClose } = props;
  // plane hooks
  const { t } = useTranslation();
  // store hooks
  const { removeClassificationWindow } = useServiceCatalog();
  // state
  const [isDeleting, setIsDeleting] = useState(false);

  const handleDelete = async () => {
    if (!classificationWindow) return;

    setIsDeleting(true);
    try {
      await removeClassificationWindow(workspaceSlug, classificationWindow.id);
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t("workspace_settings.settings.worklog.windows.toasts.removed.title"),
        message: t("workspace_settings.settings.worklog.windows.toasts.removed.message"),
      });
      handleClose();
    } catch (error) {
      const code = extractCalendarErrorCode(error);
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("workspace_settings.settings.worklog.windows.toasts.not_removed.title"),
        message: code
          ? t(CALENDAR_ERROR_KEYS[code] ?? "common.errors.default.message")
          : t("common.errors.default.message"),
      });
    } finally {
      setIsDeleting(false);
    }
  };

  return (
    <AlertModalCore
      isOpen={isOpen}
      handleClose={handleClose}
      handleSubmit={handleDelete}
      isSubmitting={isDeleting}
      title={t("workspace_settings.settings.worklog.windows.delete.title")}
      content={t("workspace_settings.settings.worklog.windows.delete.description", {
        range: classificationWindow ? `${classificationWindow.start_time}–${classificationWindow.end_time}` : "",
        hourType: classificationWindow?.hour_type_name ?? "",
      })}
      primaryButtonText={{ loading: t("common.deleting"), default: t("common.delete") }}
    />
  );
});
