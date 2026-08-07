/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useCallback, useEffect, useState } from "react";
import { observer } from "mobx-react";
import { CalendarClock, Plus, Trash2 } from "lucide-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { Badge } from "@plane/propel/badge";
import { Button } from "@plane/propel/button";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import type { IServiceClientPrice, IServiceClientPriceList } from "@plane/types";
import { Input, Loader } from "@plane/ui";
// services
import { ServicePricingService } from "@/services/service-pricing.service";
// local imports
import { ServiceEffectiveRateTable } from "./effective-rate-table";

const pricingService = new ServicePricingService();

type Props = {
  workspaceSlug: string;
  serviceClientId: string;
};

/**
 * A client's price sheets. Sections 1 and 2 of the pricing phase.
 *
 * **The shape of this screen is the shape of the model, and both say the same thing: a
 * readjustment adds a vigency, it does not edit one.** So "Add vigency" is the primary action
 * and editing an existing sheet is deliberately secondary -- editing is for correcting a sheet
 * registered wrong, and if it were the obvious action people would use it for readjustments
 * and quietly rewrite the price of work already invoiced.
 *
 * Each vigency renders its **own** effective table, because each is a complete self-contained
 * price sheet: a base rate plus its absolute overrides. That is why an override lives under
 * the vigency here rather than in a single list at the top of the screen.
 */
export const ServiceClientPrices = observer(function ServiceClientPrices(props: Props) {
  const { workspaceSlug, serviceClientId } = props;
  // states
  const [data, setData] = useState<IServiceClientPriceList | null>(null);
  const [isCreating, setIsCreating] = useState(false);
  const [newStartsOn, setNewStartsOn] = useState("");
  const [newRate, setNewRate] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  // Which override is being edited, and the value typed so far. An inline editor rather than a
  // browser prompt: this is a monetary value, and it deserves a numeric input with a step and a
  // minimum instead of a free-text dialog that accepts "abput 300".
  const [editingOverride, setEditingOverride] = useState<{
    priceId: string;
    hourTypeId: string;
    value: string;
  } | null>(null);
  // plane hooks
  const { t } = useTranslation();

  const load = useCallback(async () => {
    try {
      setData(await pricingService.fetchPrices(workspaceSlug, serviceClientId));
    } catch {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("common.errors.default.title"),
        message: t("workspace_settings.settings.service_prices.toasts.load_failed"),
      });
    }
  }, [workspaceSlug, serviceClientId, t]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleCreate = async () => {
    setIsSubmitting(true);
    try {
      await pricingService.createPrice(workspaceSlug, serviceClientId, {
        starts_on: newStartsOn,
        base_hour_rate: newRate,
      });
      setIsCreating(false);
      setNewStartsOn("");
      setNewRate("");
      await load();
    } catch (error) {
      // The API answers with UPPER_SNAKE codes; the two a user can actually cause are a
      // mid-month start date and a duplicate vigency. Both are explained rather than shown
      // raw, because "PRICE_VIGENCY_MUST_START_ON_THE_FIRST_OF_A_MONTH" is not a sentence.
      const code = (error as { error?: string; starts_on?: string[] })?.error;
      const fieldCode = (error as { starts_on?: string[] })?.starts_on?.[0];
      const key =
        code === "PRICE_VIGENCY_ALREADY_EXISTS"
          ? "duplicate"
          : fieldCode === "PRICE_VIGENCY_MUST_START_ON_THE_FIRST_OF_A_MONTH"
            ? "not_first_of_month"
            : "save_failed";

      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("common.errors.default.title"),
        message: t(`workspace_settings.settings.service_prices.toasts.${key}`),
      });
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleDelete = async (price: IServiceClientPrice) => {
    try {
      await pricingService.deletePrice(workspaceSlug, serviceClientId, price.price_id);
      await load();
    } catch (error) {
      const code = (error as { error?: string })?.error;

      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("common.errors.default.title"),
        message: t(
          code === "PRICE_VIGENCY_ALREADY_PRICED_WORK_LOGS"
            ? "workspace_settings.settings.service_prices.toasts.already_priced"
            : "workspace_settings.settings.service_prices.toasts.save_failed"
        ),
      });
    }
  };

  const handleOverrideSubmit = async (priceId: string, hourTypeId: string, entered: string) => {
    try {
      const existing = data?.prices
        .find((price) => price.price_id === priceId)
        ?.overrides.find((override) => override.hour_type === hourTypeId);

      if (existing) {
        await pricingService.updateHourTypeRate(workspaceSlug, priceId, existing.id, {
          absolute_rate: entered,
        });
      } else {
        await pricingService.createHourTypeRate(workspaceSlug, priceId, {
          hour_type: hourTypeId,
          absolute_rate: entered,
        });
      }

      setEditingOverride(null);
      await load();
    } catch {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("common.errors.default.title"),
        message: t("workspace_settings.settings.service_prices.toasts.save_failed"),
      });
    }
  };

  const handleRemoveOverride = async (priceId: string, hourTypeId: string) => {
    const existing = data?.prices
      .find((price) => price.price_id === priceId)
      ?.overrides.find((override) => override.hour_type === hourTypeId);

    if (!existing) return;

    try {
      await pricingService.deleteHourTypeRate(workspaceSlug, priceId, existing.id);
      await load();
    } catch {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("common.errors.default.title"),
        message: t("workspace_settings.settings.service_prices.toasts.save_failed"),
      });
    }
  };

  if (!data) {
    return (
      <Loader className="flex flex-col gap-2">
        <Loader.Item height="60px" />
        <Loader.Item height="60px" />
      </Loader>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-start justify-between gap-3">
        <div className="flex flex-col">
          <h4 className="text-sm font-medium text-custom-text-100">
            {t("workspace_settings.settings.service_prices.title")}
          </h4>
          <p className="text-xs text-custom-text-350">
            {t("workspace_settings.settings.service_prices.description")}
          </p>
        </div>

        <Button variant="secondary" size="sm" prependIcon={<Plus />} onClick={() => setIsCreating(true)}>
          {t("workspace_settings.settings.service_prices.add")}
        </Button>
      </div>

      {isCreating && (
        <div className="flex flex-col gap-2 rounded-md border border-custom-border-200 p-3">
          <div className="flex flex-wrap items-end gap-3">
            <label className="flex flex-col gap-1">
              <span className="text-xs text-custom-text-350">
                {t("workspace_settings.settings.service_prices.starts_on")}
              </span>
              {/* A date input, but only the 1st is accepted -- see the hint below. The database
                  enforces it, the serializer explains it, and this says so before the attempt. */}
              <Input
                type="date"
                value={newStartsOn}
                onChange={(event) => setNewStartsOn(event.target.value)}
                className="w-40"
              />
            </label>

            <label className="flex flex-col gap-1">
              <span className="text-xs text-custom-text-350">
                {t("workspace_settings.settings.service_prices.base_hour_rate")}
              </span>
              <Input
                type="number"
                step="0.01"
                min="0.01"
                value={newRate}
                onChange={(event) => setNewRate(event.target.value)}
                placeholder="200.00"
                className="w-32"
              />
            </label>

            <div className="flex gap-2">
              <Button variant="primary" size="sm" onClick={handleCreate} loading={isSubmitting}>
                {t("common.save")}
              </Button>
              <Button variant="secondary" size="sm" onClick={() => setIsCreating(false)}>
                {t("common.cancel")}
              </Button>
            </div>
          </div>

          <p className="text-xs text-custom-text-350">
            {t("workspace_settings.settings.service_prices.first_of_month_hint")}
          </p>
        </div>
      )}

      {data.prices.length === 0 && (
        <p className="text-xs text-custom-text-350">
          {t("workspace_settings.settings.service_prices.empty")}
        </p>
      )}

      {data.prices.map((price) => {
        const isInForce = price.price_id === data.in_force_price_id;

        return (
          <div key={price.price_id} className="flex flex-col gap-2 rounded-md border border-custom-border-200 p-3">
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-2">
                <CalendarClock className="size-4 text-custom-text-350" />
                <span className="text-sm text-custom-text-100">
                  {t("workspace_settings.settings.service_prices.from", { date: price.starts_on })}
                </span>
                <span className="text-sm font-medium text-custom-text-100">R$ {price.base_hour_rate}</span>
                {/* Which sheet applies *today*. Without it a list of three vigencies makes the
                    reader work out the rule, and the rule is the thing most worth not
                    reimplementing in a head. */}
                {isInForce && (
                  <Badge variant="success" size="sm">
                    {t("workspace_settings.settings.service_prices.in_force")}
                  </Badge>
                )}
              </div>

              <button
                type="button"
                onClick={() => handleDelete(price)}
                className="text-custom-text-350 hover:text-red-500"
                aria-label={t("common.delete")}
              >
                <Trash2 className="size-3.5" />
              </button>
            </div>

            {isInForce && (
              <>
                <ServiceEffectiveRateTable
                  rows={data.effective_table}
                  onOverride={(hourTypeId, currentRate) =>
                    setEditingOverride({ priceId: price.price_id, hourTypeId, value: currentRate })
                  }
                  onRemoveOverride={(hourTypeId) => handleRemoveOverride(price.price_id, hourTypeId)}
                />

                {editingOverride?.priceId === price.price_id && (
                  <div className="flex flex-wrap items-end gap-2 rounded-md bg-custom-background-90 p-2">
                    <label className="flex flex-col gap-1">
                      <span className="text-xs text-custom-text-350">
                        {t("workspace_settings.settings.service_prices.override_prompt")}
                      </span>
                      <Input
                        type="number"
                        step="0.01"
                        min="0.01"
                        value={editingOverride.value}
                        onChange={(event) =>
                          setEditingOverride({ ...editingOverride, value: event.target.value })
                        }
                        className="w-32"
                      />
                    </label>

                    <Button
                      variant="primary"
                      size="sm"
                      onClick={() =>
                        handleOverrideSubmit(
                          editingOverride.priceId,
                          editingOverride.hourTypeId,
                          editingOverride.value
                        )
                      }
                    >
                      {t("common.save")}
                    </Button>
                    <Button variant="secondary" size="sm" onClick={() => setEditingOverride(null)}>
                      {t("common.cancel")}
                    </Button>
                  </div>
                )}
              </>
            )}

            {!isInForce && price.overrides.length > 0 && (
              <p className="text-xs text-custom-text-350">
                {t("workspace_settings.settings.service_prices.override_count", {
                  count: price.overrides.length,
                })}
              </p>
            )}
          </div>
        );
      })}
    </div>
  );
});
