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
import { extractServiceLogErrorCode } from "./service-log.helpers";

type Props = {
  isOpen: boolean;
  workspaceSlug: string;
  projectId: string;
  issueId: string;
  handleClose: () => void;
};

/**
 * Confirms deleting a work item's allowance. Admin only.
 *
 * Deleting reverses all debits that targeted the allowance and moves the affected work
 * logs back to the contract pool. The operation is irreversible in the sense that the
 * ledger history of the allowance is gone, though a new allowance can be created later.
 *
 * **Refused on a closed allowance** because a closed allowance has been settled and its
 * ledger has been invoiced.
 */
export const DeleteServiceAllowanceModal = observer(function DeleteServiceAllowanceModal(props: Props) {
  const { isOpen, workspaceSlug, projectId, issueId, handleClose } = props;
  // plane hooks
  const { t } = useTranslation();
  // store hooks
  const { serviceAllowance, serviceLog } = useIssueDetail();
  // state
  const [isDeleting, setIsDeleting] = useState(false);

  const handleDelete = async () => {
    setIsDeleting(true);
    try {
      await serviceAllowance.deleteAllowance(workspaceSlug, projectId, issueId);
      // Re-fetch service logs to reflect the new debit origins (moved back to pool).
      await serviceLog.fetchServiceLogs(workspaceSlug, projectId, issueId);
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t("work_item.service_allowance.toasts.deleted.title"),
        message: t("work_item.service_allowance.toasts.deleted.message"),
      });
      handleClose();
    } catch (caught) {
      const code = extractServiceLogErrorCode(caught);
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("work_item.service_allowance.toasts.not_deleted.title"),
        message:
          code === "ALLOWANCE_CANNOT_DELETE_CLOSED"
            ? t("work_item.service_allowance.errors.cannot_delete_closed")
            : t("work_item.service_allowance.toasts.not_deleted.message"),
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
      title={t("work_item.service_allowance.delete.title")}
      content={t("work_item.service_allowance.delete.description")}
      primaryButtonText={{
        loading: t("common.deleting"),
        default: t("work_item.service_allowance.delete.confirm"),
      }}
    />
  );
});
