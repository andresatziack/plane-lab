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
  /** True when the work item already has an allowance, which changes the copy only. */
  isTopUp: boolean;
  handleClose: () => void;
};

/**
 * Credits hours into a work item's allowance. Section 4, restricted to Admin.
 *
 * One modal for the first credit and for every later one, because the server treats them
 * as one operation: an aditivo de escopo is another `CREDIT` row on the same allowance,
 * never a second allowance. Only the wording changes, so that an admin topping up 40h to
 * 50h is not left wondering whether they are about to replace the 40.
 *
 * **The hours stay a string all the way to the server.** `Input` gives a string, the
 * payload carries a string, and the API parses it as a Decimal. Nothing here calls
 * `parseFloat` -- section 4b of the master context is explicit that money arithmetic is
 * `Decimal`, and a browser float is exactly how 0.2525 becomes 0.25249999999999999.
 *
 * The only validation done locally is "not empty" and "looks like a positive number", to
 * save a round trip. The authoritative refusal is the server's
 * `ALLOWANCE_CREDIT_MUST_BE_POSITIVE`, and it is surfaced verbatim rather than
 * pre-empted -- a form that guessed the rule would drift from it.
 */
export const CreditServiceAllowanceModal = observer(function CreditServiceAllowanceModal(props: Props) {
  const { isOpen, workspaceSlug, projectId, issueId, isTopUp, handleClose } = props;
  // plane hooks
  const { t } = useTranslation();
  // store hooks
  const { serviceAllowance } = useIssueDetail();
  // state
  const [hours, setHours] = useState("");
  const [reference, setReference] = useState("");
  const [notes, setNotes] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Reset on open rather than on close, so a reopened modal is never pre-filled with the
  // previous credit's hours -- crediting 40h twice by accident is a commercial mistake.
  useEffect(() => {
    if (!isOpen) return;
    setHours("");
    setReference("");
    setNotes("");
    setError(null);
  }, [isOpen]);

  const handleSubmit = async () => {
    const trimmed = hours.trim().replace(",", ".");

    if (!trimmed || Number.isNaN(Number(trimmed)) || Number(trimmed) <= 0) {
      setError(t("work_item.service_allowance.form.hours_invalid"));
      return;
    }

    setIsSubmitting(true);
    setError(null);

    try {
      await serviceAllowance.creditAllowance(workspaceSlug, projectId, issueId, {
        hours: trimmed,
        reference: reference.trim() || undefined,
        notes: notes.trim() || undefined,
      });
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t("work_item.service_allowance.toasts.credited.title"),
        message: t("work_item.service_allowance.toasts.credited.message"),
      });
      handleClose();
    } catch (caught) {
      const code = extractServiceLogErrorCode(caught);
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("work_item.service_allowance.toasts.not_credited.title"),
        message:
          code === "ALLOWANCE_IS_CLOSED"
            ? t("work_item.service_allowance.errors.closed")
            : code === "ALLOWANCE_CREDIT_MUST_BE_POSITIVE"
              ? t("work_item.service_allowance.form.hours_invalid")
              : t("work_item.service_allowance.toasts.not_credited.message"),
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
            {isTopUp ? t("work_item.service_allowance.form.title_top_up") : t("work_item.service_allowance.form.title")}
          </h3>
          <p className="text-sm text-custom-text-300">
            {isTopUp
              ? t("work_item.service_allowance.form.description_top_up")
              : t("work_item.service_allowance.form.description")}
          </p>
        </div>

        <div className="flex flex-col gap-1">
          <label htmlFor="allowance-hours" className="text-xs text-custom-text-300 font-medium">
            {t("work_item.service_allowance.form.hours")}
          </label>
          <Input
            id="allowance-hours"
            type="text"
            value={hours}
            onChange={(event) => {
              setHours(event.target.value);
              setError(null);
            }}
            placeholder={t("work_item.service_allowance.form.hours_placeholder")}
            hasError={Boolean(error)}
          />
          <span className="text-xs text-custom-text-350">{t("work_item.service_allowance.form.hours_hint")}</span>
          {error && <span className="text-xs text-red-500">{error}</span>}
        </div>

        <div className="flex flex-col gap-1">
          <label htmlFor="allowance-reference" className="text-xs text-custom-text-300 font-medium">
            {t("work_item.service_allowance.form.reference")}
          </label>
          <Input
            id="allowance-reference"
            type="text"
            value={reference}
            onChange={(event) => setReference(event.target.value)}
            placeholder={t("work_item.service_allowance.form.reference_placeholder")}
          />
        </div>

        <div className="flex flex-col gap-1">
          <label htmlFor="allowance-notes" className="text-xs text-custom-text-300 font-medium">
            {t("work_item.service_allowance.form.notes")}
          </label>
          <textarea
            id="allowance-notes"
            value={notes}
            onChange={(event) => setNotes(event.target.value)}
            rows={3}
            className="border-custom-border-200 bg-custom-background-100 text-sm text-custom-text-100 focus:border-custom-primary-100 rounded-md border px-3 py-2 outline-none"
          />
        </div>

        <div className="flex items-center justify-end gap-2">
          <Button variant="secondary" size="sm" onClick={handleClose} disabled={isSubmitting}>
            {t("common.cancel")}
          </Button>
          <Button variant="primary" size="sm" onClick={handleSubmit} loading={isSubmitting}>
            {isSubmitting ? t("common.saving") : t("work_item.service_allowance.form.submit")}
          </Button>
        </div>
      </div>
    </ModalCore>
  );
});
