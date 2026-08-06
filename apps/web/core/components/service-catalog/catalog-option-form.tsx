/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { AlertTriangle } from "lucide-react";
import { Controller, useForm } from "react-hook-form";
// plane imports
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { Input } from "@plane/propel/input";
import type { IServiceBillingType, IServiceHourType, TServiceBillingRoute } from "@plane/types";
import { CustomSelect } from "@plane/ui";
// local imports
import type { TCatalogKind } from "./catalog-option-list";

/** Two decimal places, at least 0.01. Mirrors the model validator and section 4b. */
const MULTIPLIER_PATTERN = /^\d{1,2}([.,]\d{1,2})?$/;

const BILLING_ROUTES: TServiceBillingRoute[] = ["debit_pool", "bill_amount", "non_billable"];

export type TCatalogOptionFormValues = {
  name: string;
  description: string;
  /** Hour types only. Kept as text so no float ever touches the multiplier. */
  multiplier: string;
  /** Hour types only. */
  color: string;
  /** Billing types only. */
  billing_route: TServiceBillingRoute;
};

type Props = {
  kind: TCatalogKind;
  data?: IServiceHourType | IServiceBillingType;
  handleClose: () => void;
  onSubmit: (values: TCatalogOptionFormValues) => Promise<void>;
};

const DEFAULT_VALUES: TCatalogOptionFormValues = {
  name: "",
  description: "",
  multiplier: "1.00",
  color: "#60646C",
  billing_route: "debit_pool",
};

export function CatalogOptionForm(props: Props) {
  const { kind, data, handleClose, onSubmit } = props;
  // plane hooks
  const { t } = useTranslation();
  // derived values
  const isHourType = kind === "hour-type";
  const isEditing = Boolean(data);
  // form
  const {
    control,
    formState: { errors, isSubmitting },
    handleSubmit,
    register,
  } = useForm<TCatalogOptionFormValues>({
    defaultValues: data
      ? {
          name: data.name,
          description: data.description ?? "",
          multiplier: (data as IServiceHourType).multiplier ?? "1.00",
          color: (data as IServiceHourType).color ?? "#60646C",
          billing_route: (data as IServiceBillingType).billing_route ?? "debit_pool",
        }
      : DEFAULT_VALUES,
  });

  const routeLabels = Object.fromEntries(
    BILLING_ROUTES.map((route) => [route, t(`workspace_settings.settings.worklog.billing_routes.${route}`)])
  ) as Record<TServiceBillingRoute, string>;

  const titleKey = isHourType
    ? `workspace_settings.settings.worklog.hour_types.${isEditing ? "edit_title" : "create_title"}`
    : `workspace_settings.settings.worklog.billing_types.${isEditing ? "edit_title" : "create_title"}`;

  return (
    <form
      onSubmit={handleSubmit(onSubmit)}
      onKeyDown={(event) => {
        if (event.key === "Enter") event.preventDefault();
      }}
    >
      <div className="space-y-4 p-5">
        <h3 className="text-xl font-medium text-primary">{t(titleKey)}</h3>

        {/*
          Rule R4: changing configuration never recalculates existing work logs,
          because each one persists the multiplier and rate it was recorded with.
          That is a guarantee, but an admin editing 1.5 into 1.8 expecting last
          month's invoice to change would be surprised the other way, so say it
          before the edit rather than explain it afterwards. Shown only when editing:
          on a new option there is nothing historical to reassure anyone about.
        */}
        {isEditing && (
          <div className="text-xs flex items-start gap-2 rounded-md border border-subtle bg-surface-2 p-3 text-tertiary">
            <AlertTriangle className="text-warning mt-0.5 size-4 flex-shrink-0" />
            <span>
              {isHourType
                ? t("workspace_settings.settings.worklog.hour_types.edit_warning")
                : t("workspace_settings.settings.worklog.billing_types.edit_warning")}
            </span>
          </div>
        )}

        <div className="space-y-3">
          <div>
            <label className="text-sm mb-1 block font-medium text-secondary" htmlFor="name">
              {t("workspace_settings.settings.worklog.form.name")}
            </label>
            <Input
              id="name"
              type="text"
              {...register("name", {
                required: t("workspace_settings.settings.worklog.form.name_required"),
                maxLength: { value: 255, message: t("workspace_settings.settings.worklog.form.name_too_long") },
              })}
              hasError={Boolean(errors.name)}
              placeholder={t("workspace_settings.settings.worklog.form.name_placeholder")}
              className="w-full"
            />
            {errors.name && <span className="text-xs text-danger">{errors.name.message}</span>}
          </div>

          {isHourType ? (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div>
                <label className="text-sm mb-1 block font-medium text-secondary" htmlFor="multiplier">
                  {t("workspace_settings.settings.worklog.hour_types.multiplier")}
                </label>
                <Input
                  id="multiplier"
                  type="text"
                  inputMode="decimal"
                  {...register("multiplier", {
                    required: t("workspace_settings.settings.worklog.hour_types.multiplier_required"),
                    validate: (value) => {
                      if (!MULTIPLIER_PATTERN.test(value.trim())) {
                        return t("workspace_settings.settings.worklog.hour_types.multiplier_invalid");
                      }
                      // Zero is refused server side too: it would be a third way to
                      // make work free, competing with the non-billable route.
                      // Values below 1 are legitimate -- travel time at half rate.
                      if (Number(value.trim().replace(",", ".")) < 0.01) {
                        return t("workspace_settings.settings.worklog.hour_types.multiplier_too_small");
                      }
                      return true;
                    },
                  })}
                  hasError={Boolean(errors.multiplier)}
                  placeholder="1,50"
                  className="w-full"
                />
                {errors.multiplier ? (
                  <span className="text-xs text-danger">{errors.multiplier.message}</span>
                ) : (
                  <span className="text-xs mt-1 block text-tertiary">
                    {t("workspace_settings.settings.worklog.hour_types.multiplier_hint")}
                  </span>
                )}
              </div>

              <div>
                <label className="text-sm mb-1 block font-medium text-secondary" htmlFor="color">
                  {t("workspace_settings.settings.worklog.hour_types.color")}
                </label>
                <Controller
                  control={control}
                  name="color"
                  render={({ field: { value, onChange } }) => (
                    <div className="flex items-center gap-2">
                      <input
                        id="color"
                        type="color"
                        value={value}
                        onChange={(event) => onChange(event.target.value)}
                        className="h-8 w-12 cursor-pointer rounded border border-strong bg-surface-1"
                      />
                      <span className="text-sm text-tertiary">{value}</span>
                    </div>
                  )}
                />
              </div>
            </div>
          ) : (
            <div>
              <label className="text-sm mb-1 block font-medium text-secondary" htmlFor="billing_route">
                {t("workspace_settings.settings.worklog.billing_types.billing_route")}
              </label>
              <Controller
                control={control}
                name="billing_route"
                render={({ field: { value, onChange } }) => (
                  <CustomSelect
                    value={value}
                    label={<span className="text-sm">{routeLabels[value]}</span>}
                    onChange={onChange}
                    buttonClassName="w-full justify-between"
                    input
                  >
                    {BILLING_ROUTES.map((route) => (
                      <CustomSelect.Option key={route} value={route}>
                        <div>
                          <div className="text-sm">{routeLabels[route]}</div>
                          <div className="text-xs text-tertiary">
                            {t(`workspace_settings.settings.worklog.billing_routes.${route}_hint`)}
                          </div>
                        </div>
                      </CustomSelect.Option>
                    ))}
                  </CustomSelect>
                )}
              />
            </div>
          )}

          <div>
            <label className="text-sm mb-1 block font-medium text-secondary" htmlFor="description">
              {t("workspace_settings.settings.worklog.form.description")}
            </label>
            <textarea
              id="description"
              {...register("description")}
              rows={3}
              className="text-sm focus:border-accent-primary w-full rounded-md border border-strong bg-surface-1 px-3 py-2 outline-none"
              placeholder={t("workspace_settings.settings.worklog.form.description_placeholder")}
            />
          </div>
        </div>
      </div>

      <div className="flex items-center justify-end gap-2 border-t border-subtle px-5 py-4">
        <Button variant="secondary" size="sm" onClick={handleClose}>
          {t("common.cancel")}
        </Button>
        <Button variant="primary" size="sm" type="submit" loading={isSubmitting}>
          {isEditing ? t("common.update") : t("common.create")}
        </Button>
      </div>
    </form>
  );
}
