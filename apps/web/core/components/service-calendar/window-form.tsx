/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
import { Info } from "lucide-react";
import { Controller, useForm } from "react-hook-form";
// plane imports
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { Input } from "@plane/propel/input";
import type { IServiceClassificationWindow, TServiceDayScope } from "@plane/types";
import { CustomSelect } from "@plane/ui";
// hooks
import { useServiceCatalog } from "@/hooks/store/use-service-catalog";
// local imports
import { DAY_SCOPE_ORDER, hasZeroLength, isValidClockTime } from "./calendar.helpers";

export type TWindowFormValues = {
  hour_type: string;
  day_scope: TServiceDayScope;
  start_time: string;
  end_time: string;
};

type Props = {
  data?: IServiceClassificationWindow;
  /** Pre-selects the day being edited, so adding a window to Tuesday opens on Tuesday. */
  defaultDayScope?: TServiceDayScope;
  handleClose: () => void;
  onSubmit: (values: TWindowFormValues) => Promise<void>;
};

export const WindowForm = observer(function WindowForm(props: Props) {
  const { data, defaultDayScope, handleClose, onSubmit } = props;
  // plane hooks
  const { t } = useTranslation();
  // store hooks
  const { activeHourTypes } = useServiceCatalog();
  // form
  const {
    control,
    formState: { errors, isSubmitting },
    handleSubmit,
    watch,
  } = useForm<TWindowFormValues>({
    defaultValues: {
      hour_type: data?.hour_type ?? activeHourTypes[0]?.id ?? "",
      day_scope: data?.day_scope ?? defaultDayScope ?? "monday",
      start_time: data?.start_time ?? "08:00",
      end_time: data?.end_time ?? "18:00",
    },
  });

  const startTime = watch("start_time");
  const endTime = watch("end_time");

  // Both are informational, computed the same way the server does. Neither blocks the
  // submit: the server is the authority on whether the set stays valid, and a window that
  // crosses midnight is legitimate rather than a mistake.
  const crossesMidnight =
    isValidClockTime(startTime) && isValidClockTime(endTime) && !hasZeroLength(startTime, endTime)
      ? endTime <= startTime
      : false;
  const isWholeDay = startTime === "00:00" && endTime === "24:00";

  return (
    <form onSubmit={handleSubmit(onSubmit)} className="flex flex-col gap-4 p-5">
      <h3 className="text-lg font-medium text-primary">
        {data
          ? t("workspace_settings.settings.worklog.windows.edit_title")
          : t("workspace_settings.settings.worklog.windows.create_title")}
      </h3>

      <Controller
        control={control}
        name="hour_type"
        rules={{ required: t("workspace_settings.settings.worklog.windows.hour_type_required") }}
        render={({ field: { value, onChange } }) => (
          <div className="flex flex-col gap-1">
            <span className="text-sm font-medium text-secondary">
              {t("workspace_settings.settings.worklog.windows.hour_type")}
            </span>
            <CustomSelect
              value={value}
              onChange={onChange}
              label={activeHourTypes.find((option) => option.id === value)?.name ?? "—"}
              buttonClassName="border border-subtle"
            >
              {activeHourTypes.map((option) => (
                <CustomSelect.Option key={option.id} value={option.id}>
                  <span className="flex items-center gap-2">
                    <span className="size-2 rounded-full" style={{ backgroundColor: option.color }} />
                    {option.name}
                    {/* The priority is shown here because it is what decides the winner when
                        two windows contain the same instant, and choosing an hour type
                        without seeing it would be choosing blind. */}
                    <span className="text-xs text-tertiary">
                      {t("workspace_settings.settings.worklog.windows.priority_badge", {
                        value: option.priority,
                      })}
                    </span>
                  </span>
                </CustomSelect.Option>
              ))}
            </CustomSelect>
            {errors.hour_type && <span className="text-xs text-red-500">{errors.hour_type.message}</span>}
          </div>
        )}
      />

      <Controller
        control={control}
        name="day_scope"
        render={({ field: { value, onChange } }) => (
          <div className="flex flex-col gap-1">
            <span className="text-sm font-medium text-secondary">
              {t("workspace_settings.settings.worklog.windows.day_scope_label")}
            </span>
            <CustomSelect
              value={value}
              onChange={onChange}
              label={t(`workspace_settings.settings.worklog.windows.day_scope.${value}`)}
              buttonClassName="border border-subtle"
            >
              {DAY_SCOPE_ORDER.map((scope) => (
                <CustomSelect.Option key={scope} value={scope}>
                  {t(`workspace_settings.settings.worklog.windows.day_scope.${scope}`)}
                </CustomSelect.Option>
              ))}
            </CustomSelect>
          </div>
        )}
      />

      <div className="flex gap-3">
        {(["start_time", "end_time"] as const).map((fieldName) => (
          <Controller
            key={fieldName}
            control={control}
            name={fieldName}
            rules={{
              required: t("workspace_settings.settings.worklog.windows.border_required"),
              validate: (value) =>
                isValidClockTime(value) ? true : t("workspace_settings.settings.worklog.windows.errors.invalid_clock"),
            }}
            render={({ field: { value, onChange } }) => (
              <div className="flex flex-1 flex-col gap-1">
                <label className="text-sm font-medium text-secondary" htmlFor={`window-${fieldName}`}>
                  {t(`workspace_settings.settings.worklog.windows.${fieldName}`)}
                </label>
                <Input
                  id={`window-${fieldName}`}
                  value={value}
                  onChange={(event) => onChange(event.target.value)}
                  placeholder={fieldName === "start_time" ? "08:00" : "18:00"}
                  hasError={Boolean(errors[fieldName])}
                />
                {errors[fieldName] && <span className="text-xs text-red-500">{errors[fieldName]?.message}</span>}
              </div>
            )}
          />
        ))}
      </div>

      {/* 24:00 is the only way to say "the end of the day", so it has to be discoverable. */}
      <span className="text-xs text-tertiary">{t("workspace_settings.settings.worklog.windows.border_hint")}</span>

      {hasZeroLength(startTime, endTime) && (
        <span className="text-xs text-red-500">
          {t("workspace_settings.settings.worklog.windows.errors.borders_must_differ")}
        </span>
      )}

      {(crossesMidnight || isWholeDay) && (
        <div className="flex items-start gap-2 rounded-md bg-surface-2 px-3 py-2">
          <Info className="mt-0.5 size-3.5 shrink-0 text-tertiary" />
          <span className="text-xs text-tertiary">
            {isWholeDay
              ? t("workspace_settings.settings.worklog.windows.whole_day_hint")
              : t("workspace_settings.settings.worklog.windows.crosses_midnight_hint")}
          </span>
        </div>
      )}

      <div className="flex justify-end gap-2 pt-1">
        <Button variant="secondary" size="sm" onClick={handleClose} type="button">
          {t("common.cancel")}
        </Button>
        <Button
          variant="primary"
          size="sm"
          type="submit"
          loading={isSubmitting}
          disabled={hasZeroLength(startTime, endTime)}
        >
          {data ? t("common.update") : t("common.create")}
        </Button>
      </div>
    </form>
  );
});
