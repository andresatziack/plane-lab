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
import { AlertModalCore } from "@plane/ui";
// hooks
import { useIssueDetail } from "@/hooks/store/use-issue-detail";
// local imports
import type { TServiceLogBatch } from "@/store/issue/issue-details/service-log.store";
import { extractServiceLogErrorCode, isSplitBatch, SERVICE_LOG_ERROR_KEYS } from "./service-log.helpers";

type Props = {
  isOpen: boolean;
  workspaceSlug: string;
  projectId: string;
  issueId: string;
  batch: TServiceLogBatch | null;
  handleClose: () => void;
};

/**
 * Confirms deleting a work log entry. Acceptance criterion 11.
 *
 * The copy says how many segments will go when the entry was split, because the
 * technician submitted one thing and would not otherwise expect two rows to disappear.
 */
export const DeleteServiceLogModal = observer(function DeleteServiceLogModal(props: Props) {
  const { isOpen, workspaceSlug, projectId, issueId, batch, handleClose } = props;
  // plane hooks
  const { t } = useTranslation();
  // store hooks
  const { serviceLog } = useIssueDetail();
  // state
  const [isDeleting, setIsDeleting] = useState(false);

  const handleDelete = async () => {
    if (!batch) return;

    setIsDeleting(true);
    try {
      await serviceLog.removeServiceLogBatch(workspaceSlug, projectId, issueId, batch.batchId);
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t("work_item.service_log.toasts.removed.title"),
        message: t("work_item.service_log.toasts.removed.message"),
      });
      handleClose();
    } catch (error) {
      const code = extractServiceLogErrorCode(error);
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("work_item.service_log.toasts.not_removed.title"),
        message: code
          ? t(SERVICE_LOG_ERROR_KEYS[code] ?? "work_item.service_log.toasts.not_removed.message")
          : t("work_item.service_log.toasts.not_removed.message"),
      });
    } finally {
      setIsDeleting(false);
    }
  };

  const isSplit = batch ? isSplitBatch(batch.segments) : false;

  return (
    <AlertModalCore
      isOpen={isOpen}
      handleClose={handleClose}
      handleSubmit={handleDelete}
      isSubmitting={isDeleting}
      title={t("work_item.service_log.delete.title")}
      content={
        isSplit
          ? t("work_item.service_log.delete.description_batch", { count: batch?.segments.length ?? 0 })
          : t("work_item.service_log.delete.description")
      }
      primaryButtonText={{
        loading: t("common.deleting"),
        default: t("work_item.service_log.delete.confirm"),
      }}
    />
  );
});
