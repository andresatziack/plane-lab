/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useState } from "react";
import { observer } from "mobx-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import { EModalPosition, EModalWidth, Input, ModalCore } from "@plane/ui";
// hooks
import { useIssueDetail } from "@/hooks/store/use-issue-detail";
// local imports
import { extractServiceLogErrorCode } from "./service-log.helpers";

type Props = {
  isOpen: boolean;
  workspaceSlug: string;
  projectId: string;
  issueId: string;
  currentReference: string;
  currentNotes: string;
  handleClose: () => void;
};

/**
 * Edits the reference and notes of a work item's allowance. Admin only.
 *
 * Only metadata is mutable here. Hour figures are managed through credits and debits
 * exclusively, following decision D23. An admin correcting the commercial reference of
 * a project that was already credited should not need a second credit to accomplish it.
 */
export const EditServiceAllowanceModal = observer(function EditServiceAllowanceModal(props: Props) {
  const { isOpen, workspaceSlug, projectId, issueId, currentReference, currentNotes, handleClose } = props;
  // plane hooks
  const { t } = useTranslation();
  // store hooks
  const { serviceAllowance } = useIssueDetail();
  // state
  const [reference, setReference] = useState("");
  const [notes, setNotes] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Populate with current values when the modal opens.
  useEffect(() => {
    if (!isOpen) return;
    setReference(currentReference || "");
    setNotes(currentNotes || "");
  }, [isOpen, currentReference, currentNotes]);

  const handleSubmit = async () => {
    setIsSubmitting(true);

    try {
      await serviceAllowance.updateAllowance(workspaceSlug, projectId, issueId, {
        reference: reference.trim(),
        notes: notes.trim(),
      });
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t("work_item.service_allowance.toasts.updated.title"),
        message: t("work_item.service_allowance.toasts.updated.message"),
      });
      handleClose();
    } catch (caught) {
      const code = extractServiceLogErrorCode(caught);
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("work_item.service_allowance.toasts.not_updated.title"),
        message:
          code === "ALLOWANCE_IS_CLOSED"
            ? t("work_item.service_allowance.errors.closed")
            : t("work_item.service_allowance.toasts.not_updated.message"),
      });
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <ModalCore isOpen={isOpen} handleClose={handleClose} position={EModalPosition.CENTER} width={EModalWidth.XL}>
      <div className="flex flex-col gap-4 p-5">
        <div className="flex flex-col gap-1">
          <h3 className="text-lg text-custom-text-100 font-medium">
            {t("work_item.service_allowance.edit.title")}
          </h3>
          <p className="text-sm text-custom-text-300">
            {t("work_item.service_allowance.edit.description")}
          </p>
        </div>

        <div className="flex flex-col gap-1">
          <label htmlFor="edit-allowance-reference" className="text-xs text-custom-text-300 font-medium">
            {t("work_item.service_allowance.form.reference")}
          </label>
          <Input
            id="edit-allowance-reference"
            type="text"
            value={reference}
            onChange={(event) => setReference(event.target.value)}
            placeholder={t("work_item.service_allowance.form.reference_placeholder")}
          />
        </div>

        <div className="flex flex-col gap-1">
          <label htmlFor="edit-allowance-notes" className="text-xs text-custom-text-300 font-medium">
            {t("work_item.service_allowance.form.notes")}
          </label>
          <textarea
            id="edit-allowance-notes"
            value={notes}
            onChange={(event) => setNotes(event.target.value)}
            rows={3}
            className="text-sm text-custom-text-100 focus:border-custom-primary-100 rounded-md border border-subtle bg-surface-1 px-3 py-2 outline-none"
          />
        </div>

        <div className="flex items-center justify-end gap-2">
          <Button variant="secondary" size="sm" onClick={handleClose} disabled={isSubmitting}>
            {t("common.cancel")}
          </Button>
          <Button variant="primary" size="sm" onClick={handleSubmit} loading={isSubmitting}>
            {isSubmitting ? t("common.saving") : t("common.save")}
          </Button>
        </div>
      </div>
    </ModalCore>
  );
});
