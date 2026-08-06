/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
import { Repeat } from "lucide-react";
import { Controller, useForm } from "react-hook-form";
// plane imports
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { Input } from "@plane/propel/input";
import type { IServiceHoliday, TServiceHolidayScope } from "@plane/types";
import { CustomSelect } from "@plane/ui";

const HOLIDAY_SCOPES: TServiceHolidayScope[] = ["national", "state", "municipal"];

export type THolidayFormValues = {
  name: string;
  date: string;
  is_recurring: boolean;
  scope: TServiceHolidayScope;
};

type Props = {
  data?: IServiceHoliday;
  handleClose: () => void;
  onSubmit: (values: THolidayFormValues) => Promise<void>;
};

export const HolidayForm = observer(function HolidayForm(props: Props) {
  const { data, handleClose, onSubmit } = props;
  const { t } = useTranslation();

  const {
    control,
    formState: { errors, isSubmitting },
    handleSubmit,
    watch,
  } = useForm<THolidayFormValues>({
    defaultValues: {
      name: data?.name ?? "",
      date: data?.date ?? new Date().toISOString().slice(0, 10),
      is_recurring: data?.is_recurring ?? false,
      scope: data?.scope ?? "national",
    },
  });

  const isRecurring = watch("is_recurring");

  return (
    <form onSubmit={handleSubmit(onSubmit)} className="flex flex-col gap-4 p-5">
      <h3 className="text-lg font-medium text-primary">
        {data
          ? t("workspace_settings.settings.worklog.holidays.edit_title")
          : t("workspace_settings.settings.worklog.holidays.create_title")}
      </h3>

      <Controller
        control={control}
        name="name"
        rules={{
          validate: (value) =>
            value.trim().length > 0 ? true : t("workspace_settings.settings.worklog.holidays.errors.name_required"),
        }}
        render={({ field: { value, onChange } }) => (
          <div className="flex flex-col gap-1">
            <label className="text-sm font-medium text-secondary" htmlFor="holiday-name">
              {t("workspace_settings.settings.worklog.holidays.name")}
            </label>
            <Input
              id="holiday-name"
              value={value}
              onChange={(event) => onChange(event.target.value)}
              placeholder="Natal"
              hasError={Boolean(errors.name)}
            />
            {errors.name && <span className="text-xs text-red-500">{errors.name.message}</span>}
          </div>
        )}
      />

      <Controller
        control={control}
        name="date"
        rules={{ required: t("workspace_settings.settings.worklog.holidays.errors.date_required") }}
        render={({ field: { value, onChange } }) => (
          <div className="flex flex-col gap-1">
            <label className="text-sm font-medium text-secondary" htmlFor="holiday-date">
              {t("workspace_settings.settings.worklog.holidays.date")}
            </label>
            <input
              id="holiday-date"
              type="date"
              value={value}
              onChange={(event) => onChange(event.target.value)}
              className="text-sm rounded-md border border-subtle bg-surface-1 px-3 py-1.5"
            />
            {errors.date && <span className="text-xs text-red-500">{errors.date.message}</span>}
          </div>
        )}
      />

      {/* Recurrence is the field that decides how far the change reaches, so it gets a
          full explanation rather than a bare checkbox label. */}
      <Controller
        control={control}
        name="is_recurring"
        render={({ field: { value, onChange } }) => (
          <div className="flex flex-col gap-1.5">
            <button type="button" onClick={() => onChange(!value)} className="flex items-center gap-2 text-left">
              <span
                className={`flex size-4 shrink-0 items-center justify-center rounded border ${
                  value ? "border-primary bg-primary-100" : "border-subtle"
                }`}
              >
                {value && <Repeat className="size-2.5 text-white" />}
              </span>
              <span className="text-sm text-primary">
                {t("workspace_settings.settings.worklog.holidays.is_recurring")}
              </span>
            </button>
            <span className="text-xs text-tertiary">
              {isRecurring
                ? t("workspace_settings.settings.worklog.holidays.is_recurring_hint_on")
                : t("workspace_settings.settings.worklog.holidays.is_recurring_hint_off")}
            </span>
          </div>
        )}
      />

      <Controller
        control={control}
        name="scope"
        render={({ field: { value, onChange } }) => (
          <div className="flex flex-col gap-1">
            <span className="text-sm font-medium text-secondary">
              {t("workspace_settings.settings.worklog.holidays.scope")}
            </span>
            <CustomSelect
              value={value}
              onChange={onChange}
              label={t(`workspace_settings.settings.worklog.holidays.scopes.${value}`)}
              buttonClassName="border border-subtle"
            >
              {HOLIDAY_SCOPES.map((scope) => (
                <CustomSelect.Option key={scope} value={scope}>
                  {t(`workspace_settings.settings.worklog.holidays.scopes.${scope}`)}
                </CustomSelect.Option>
              ))}
            </CustomSelect>
            {/* Says plainly that the scope is recorded and not yet acted on, so nobody
                expects it to filter anything. Decision D14. */}
            <span className="text-xs text-tertiary">
              {t("workspace_settings.settings.worklog.holidays.scope_hint")}
            </span>
          </div>
        )}
      />

      <div className="flex justify-end gap-2 pt-1">
        <Button variant="secondary" size="sm" onClick={handleClose} type="button">
          {t("common.cancel")}
        </Button>
        <Button variant="primary" size="sm" type="submit" loading={isSubmitting}>
          {data ? t("common.update") : t("common.create")}
        </Button>
      </div>
    </form>
  );
});
