/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useState } from "react";
import { observer } from "mobx-react";
import { AlertTriangle } from "lucide-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import type { IServiceOveragePreview } from "@plane/types";
import { Loader, ModalCore } from "@plane/ui";
// services
import { ServicePricingService } from "@/services/service-pricing.service";

const pricingService = new ServicePricingService();

type Props = {
  isOpen: boolean;
  onClose: () => void;
  onConfirm: () => Promise<void>;
  workspaceSlug: string;
  /** Exactly one of these. A period and an allowance read different endpoints. */
  periodId?: string;
  allowanceId?: string;
};

/**
 * Confirm billing an overage, showing what it will cost and stating that it is final.
 *
 * **This dialog exists because billing an overage cannot be undone.** There is no reversal in
 * this system and none is planned for this phase -- see DECISOES.md D42 for what one would
 * require -- so the only protection an Admin has is knowing the number and the finality
 * *before* they click. An irreversible mistake has to be deliberate rather than careless.
 *
 * Two things it deliberately does:
 *
 * 1. **Reads the value from the server**, through the same functions the close itself uses, so
 *    the number shown is the number that will be written. A confirmation that estimated the
 *    amount client side would be a confirmation that can lie about the one fact it exists to
 *    convey.
 * 2. **Surfaces `OVERAGE_RATE_NOT_CONFIGURED` here**, at the point the Admin can still choose
 *    to carry the deficit instead. Discovering the missing price sheet only when the close
 *    fails would be the same information arriving after the decision.
 */
export const OverageBillingConfirmation = observer(function OverageBillingConfirmation(props: Props) {
  const { isOpen, onClose, onConfirm, workspaceSlug, periodId, allowanceId } = props;
  // states
  const [preview, setPreview] = useState<IServiceOveragePreview | null>(null);
  const [errorCode, setErrorCode] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  // plane hooks
  const { t } = useTranslation();

  useEffect(() => {
    if (!isOpen) return;

    setPreview(null);
    setErrorCode(null);

    const load = async () => {
      try {
        if (periodId) {
          setPreview(await pricingService.fetchPeriodOveragePreview(workspaceSlug, periodId));
        } else if (allowanceId) {
          const response = await pricingService.fetchAllowanceOveragePreview(workspaceSlug, allowanceId);
          setPreview(response.overage_preview);
        }
      } catch (error) {
        setErrorCode((error as { error?: string })?.error ?? "UNKNOWN");
      }
    };

    void load();
  }, [isOpen, workspaceSlug, periodId, allowanceId]);

  const handleConfirm = async () => {
    setIsSubmitting(true);
    try {
      await onConfirm();
      onClose();
    } catch {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("common.errors.default.title"),
        message: t("work_item.service_overage.toasts.failed"),
      });
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <ModalCore isOpen={isOpen} handleClose={onClose}>
      <div className="flex flex-col gap-4 p-5">
        <div className="flex items-start gap-3">
          <span className="bg-red-500/15 grid size-10 shrink-0 place-items-center rounded-full">
            <AlertTriangle className="text-red-500 size-5" />
          </span>
          <div className="flex flex-col gap-1">
            <h3 className="text-lg text-custom-text-100 font-medium">{t("work_item.service_overage.title")}</h3>
            <p className="text-sm text-custom-text-300">{t("work_item.service_overage.description")}</p>
          </div>
        </div>

        {errorCode === "OVERAGE_RATE_NOT_CONFIGURED" && (
          <div className="border-red-500/40 bg-red-500/5 rounded-md border px-3 py-2">
            <p className="text-sm text-custom-text-200">{t("work_item.service_overage.no_rate")}</p>
          </div>
        )}

        {errorCode && errorCode !== "OVERAGE_RATE_NOT_CONFIGURED" && (
          <p className="text-sm text-red-500">{t("common.errors.default.message")}</p>
        )}

        {!errorCode && !preview && (
          <Loader>
            <Loader.Item height="72px" />
          </Loader>
        )}

        {preview && (
          <div className="border-custom-border-200 flex flex-col gap-2 rounded-md border p-3">
            <div className="flex items-center justify-between">
              <span className="text-xs text-custom-text-350">{t("work_item.service_overage.hours")}</span>
              <span className="text-sm text-custom-text-100">{preview.overage_hours_display}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-xs text-custom-text-350">{t("work_item.service_overage.rate")}</span>
              <span className="text-sm text-custom-text-100">
                R$ {preview.overage_hour_rate}
                {/* Where the rate came from. A contract term, an allowance's own negotiated
                    price, or the client's support rate as a fallback -- and the third is the
                    one worth noticing before billing, because it usually means nobody
                    registered a price for this specifically. */}
                <span className="text-xs text-custom-text-350 ml-1">
                  ({t(`work_item.service_overage.rate_source.${preview.rate_source}`)})
                </span>
              </span>
            </div>
            <div className="border-custom-border-200 flex items-center justify-between border-t pt-2">
              <span className="text-sm text-custom-text-100 font-medium">{t("work_item.service_overage.amount")}</span>
              <span className="text-base text-custom-text-100 font-semibold">R$ {preview.amount}</span>
            </div>
          </div>
        )}

        {/* Stated, not implied. `is_reversible` comes from the API as a constant `false` so the
            client does not have to know this on its own. */}
        {preview && !preview.is_reversible && (
          <p className="text-sm text-red-500 font-medium">{t("work_item.service_overage.irreversible")}</p>
        )}

        <div className="flex justify-end gap-2">
          <Button variant="secondary" size="sm" onClick={onClose}>
            {t("common.cancel")}
          </Button>
          <Button variant="error-fill" size="sm" onClick={handleConfirm} loading={isSubmitting} disabled={!preview}>
            {t("work_item.service_overage.confirm")}
          </Button>
        </div>
      </div>
    </ModalCore>
  );
});
