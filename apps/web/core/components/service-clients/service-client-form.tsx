/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { Controller, useForm } from "react-hook-form";
// plane imports
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { Input } from "@plane/propel/input";
import type { IServiceBillingType, IServiceClient } from "@plane/types";
import { CustomSelect } from "@plane/ui";
import { formatCNPJ, isValidCNPJ } from "@plane/utils";

export type TServiceClientFormValues = {
  name: string;
  trade_name: string;
  tax_id: string;
  default_billing_type: string | null;
  parent: string | null;
  contact_name: string;
  contact_email: string;
  contact_phone: string;
  notes: string;
  is_active: boolean;
};

type Props = {
  data?: IServiceClient;
  /** Other clients of the workspace, offered as the corporate parent. */
  parentOptions: IServiceClient[];
  /**
   * Active billing types of the workspace.
   *
   * Replaces the two-value billing mode dropdown this form had before the catalogue
   * existed, so a client can now default to any configured route -- including one an
   * admin created, which the old enum could not express.
   */
  billingTypeOptions: IServiceBillingType[];
  handleClose: () => void;
  onSubmit: (values: TServiceClientFormValues) => Promise<void>;
};

const DEFAULT_VALUES: TServiceClientFormValues = {
  name: "",
  trade_name: "",
  tax_id: "",
  // null means "use the catalogue default", resolved at work log time.
  default_billing_type: null,
  parent: null,
  contact_name: "",
  contact_email: "",
  contact_phone: "",
  notes: "",
  is_active: true,
};

export function ServiceClientForm(props: Props) {
  const { data, parentOptions, billingTypeOptions, handleClose, onSubmit } = props;
  // plane hooks
  const { t } = useTranslation();
  // form
  const {
    control,
    formState: { errors, isSubmitting },
    handleSubmit,
    register,
  } = useForm<TServiceClientFormValues>({
    defaultValues: data
      ? {
          name: data.name,
          trade_name: data.trade_name ?? "",
          tax_id: data.tax_id ? formatCNPJ(data.tax_id) : "",
          default_billing_type: data.default_billing_type ?? null,
          parent: data.parent ?? null,
          contact_name: data.contact_name ?? "",
          contact_email: data.contact_email ?? "",
          contact_phone: data.contact_phone ?? "",
          notes: data.notes ?? "",
          is_active: data.is_active,
        }
      : DEFAULT_VALUES,
  });

  return (
    <form
      onSubmit={handleSubmit(onSubmit)}
      onKeyDown={(event) => {
        if (event.key === "Enter") event.preventDefault();
      }}
    >
      <div className="space-y-4 p-5">
        <h3 className="text-xl font-medium text-primary">
          {data
            ? t("workspace_settings.settings.service_clients.form.edit_title")
            : t("workspace_settings.settings.service_clients.form.create_title")}
        </h3>

        <div className="space-y-3">
          <div>
            <label className="text-sm mb-1 block font-medium text-secondary" htmlFor="name">
              {t("workspace_settings.settings.service_clients.form.name")}
            </label>
            <Input
              id="name"
              type="text"
              {...register("name", {
                required: t("workspace_settings.settings.service_clients.form.name_required"),
                maxLength: { value: 255, message: t("workspace_settings.settings.service_clients.form.name_too_long") },
              })}
              hasError={Boolean(errors.name)}
              placeholder={t("workspace_settings.settings.service_clients.form.name_placeholder")}
              className="w-full"
            />
            {errors.name && <span className="text-xs text-danger">{errors.name.message}</span>}
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div>
              <label className="text-sm mb-1 block font-medium text-secondary" htmlFor="trade_name">
                {t("workspace_settings.settings.service_clients.form.trade_name")}
              </label>
              <Input
                id="trade_name"
                type="text"
                {...register("trade_name", { maxLength: 255 })}
                placeholder={t("workspace_settings.settings.service_clients.form.trade_name_placeholder")}
                className="w-full"
              />
            </div>

            <div>
              <label className="text-sm mb-1 block font-medium text-secondary" htmlFor="tax_id">
                {t("workspace_settings.settings.service_clients.form.tax_id")}
              </label>
              <Input
                id="tax_id"
                type="text"
                {...register("tax_id", {
                  validate: (value) =>
                    // Optional: foreign companies and individuals have no CNPJ.
                    !value ||
                    value.trim() === "" ||
                    isValidCNPJ(value) ||
                    t("workspace_settings.settings.service_clients.form.tax_id_invalid"),
                })}
                hasError={Boolean(errors.tax_id)}
                placeholder="00.000.000/0000-00"
                className="w-full"
              />
              {errors.tax_id && <span className="text-xs text-danger">{errors.tax_id.message}</span>}
            </div>
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div>
              <label className="text-sm mb-1 block font-medium text-secondary" htmlFor="default_billing_type">
                {t("workspace_settings.settings.service_clients.form.default_billing_type")}
              </label>
              <Controller
                control={control}
                name="default_billing_type"
                render={({ field: { value, onChange } }) => (
                  <CustomSelect
                    value={value}
                    label={
                      <span className="text-sm">
                        {billingTypeOptions.find((option) => option.id === value)?.name ??
                          t("workspace_settings.settings.service_clients.form.default_billing_type_inherit")}
                      </span>
                    }
                    onChange={onChange}
                    buttonClassName="w-full justify-between"
                    input
                  >
                    <CustomSelect.Option value={null}>
                      {t("workspace_settings.settings.service_clients.form.default_billing_type_inherit")}
                    </CustomSelect.Option>
                    {billingTypeOptions.map((option) => (
                      <CustomSelect.Option key={option.id} value={option.id}>
                        {option.name}
                      </CustomSelect.Option>
                    ))}
                  </CustomSelect>
                )}
              />
              <span className="text-xs mt-1 block text-tertiary">
                {t("workspace_settings.settings.service_clients.form.default_billing_type_hint")}
              </span>
            </div>

            <div>
              <label className="text-sm mb-1 block font-medium text-secondary" htmlFor="parent">
                {t("workspace_settings.settings.service_clients.form.parent")}
              </label>
              <Controller
                control={control}
                name="parent"
                render={({ field: { value, onChange } }) => (
                  <CustomSelect
                    value={value}
                    label={
                      <span className="text-sm">
                        {parentOptions.find((option) => option.id === value)?.name ?? t("common.none")}
                      </span>
                    }
                    onChange={onChange}
                    buttonClassName="w-full justify-between"
                    input
                  >
                    <CustomSelect.Option value={null}>{t("common.none")}</CustomSelect.Option>
                    {parentOptions.map((option) => (
                      <CustomSelect.Option key={option.id} value={option.id}>
                        {option.name}
                      </CustomSelect.Option>
                    ))}
                  </CustomSelect>
                )}
              />
              <span className="text-xs mt-1 block text-tertiary">
                {t("workspace_settings.settings.service_clients.form.parent_hint")}
              </span>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <div>
              <label className="text-sm mb-1 block font-medium text-secondary" htmlFor="contact_name">
                {t("workspace_settings.settings.service_clients.form.contact_name")}
              </label>
              <Input id="contact_name" type="text" {...register("contact_name")} className="w-full" />
            </div>
            <div>
              <label className="text-sm mb-1 block font-medium text-secondary" htmlFor="contact_email">
                {t("workspace_settings.settings.service_clients.form.contact_email")}
              </label>
              <Input id="contact_email" type="email" {...register("contact_email")} className="w-full" />
            </div>
            <div>
              <label className="text-sm mb-1 block font-medium text-secondary" htmlFor="contact_phone">
                {t("workspace_settings.settings.service_clients.form.contact_phone")}
              </label>
              <Input id="contact_phone" type="text" {...register("contact_phone")} className="w-full" />
            </div>
          </div>

          <div>
            <label className="text-sm mb-1 block font-medium text-secondary" htmlFor="notes">
              {t("workspace_settings.settings.service_clients.form.notes")}
            </label>
            <textarea
              id="notes"
              {...register("notes")}
              rows={3}
              className="text-sm focus:border-accent-primary w-full rounded-md border border-strong bg-surface-1 px-3 py-2 outline-none"
            />
          </div>
        </div>
      </div>

      <div className="flex items-center justify-end gap-2 border-t border-subtle px-5 py-4">
        <Button variant="secondary" size="sm" onClick={handleClose}>
          {t("common.cancel")}
        </Button>
        <Button variant="primary" size="sm" type="submit" loading={isSubmitting}>
          {data ? t("common.update") : t("common.create")}
        </Button>
      </div>
    </form>
  );
}
