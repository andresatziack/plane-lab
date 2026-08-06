/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { observer } from "mobx-react";
import { AlertTriangle, Clock, Info } from "lucide-react";
import { Controller, useForm } from "react-hook-form";
// plane imports
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { Input } from "@plane/propel/input";
import type { IServiceLogPayload, IServiceLogPreview, TServiceLogEntryMode } from "@plane/types";
import { CustomSelect } from "@plane/ui";
// hooks
import { useServiceCatalog } from "@/hooks/store/use-service-catalog";
import { useIssueDetail } from "@/hooks/store/use-issue-detail";
// local imports
import {
  extractServiceLogErrorCode,
  readPreferredEntryMode,
  SERVICE_LOG_ERROR_KEYS,
  writePreferredEntryMode,
} from "./service-log.helpers";

type Props = {
  workspaceSlug: string;
  projectId: string;
  issueId: string;
  /** Present when editing; the whole batch is replaced. */
  batchId?: string;
  initialValues?: IServiceLogPayload;
  handleClose: () => void;
  onSubmitted?: () => void;
};

const todayIsoDate = () => new Date().toISOString().slice(0, 10);

/**
 * The work log form. Section 1 of the phase brief.
 *
 * Three things here are load bearing rather than decorative:
 *
 * 1. **The preview comes from the server.** R1 wants a live conversion, and acceptance
 *    criterion 2 wants the rounding announced. Both are computed by the same server
 *    code that will persist the entry, so what the technician is shown cannot disagree
 *    with what gets stored. Reimplementing the parser and R2 in the browser would be a
 *    second source of truth for the arithmetic an invoice is built from.
 * 2. **The over-24h confirmation never blocks.** Section 5 calls a long entry a
 *    legitimate use case, so the second submit goes through regardless.
 * 3. **No optimistic write.** The server decides the rounding, the multiplier snapshot
 *    and how many rows an interval becomes; the browser cannot predict any of it.
 */
export const ServiceLogForm = observer(function ServiceLogForm(props: Props) {
  const { workspaceSlug, projectId, issueId, batchId, initialValues, handleClose, onSubmitted } = props;
  // plane hooks
  const { t } = useTranslation();
  // store hooks
  const { serviceLog } = useIssueDetail();
  const { activeHourTypes, activeBillingTypes, defaultHourType, resolveDefaultBillingTypeId } = useServiceCatalog();
  // state
  const [preview, setPreview] = useState<IServiceLogPreview | null>(null);
  const [previewError, setPreviewError] = useState<string | undefined>(undefined);
  const [hasConfirmedLongEntry, setHasConfirmedLongEntry] = useState(false);
  const [submitError, setSubmitError] = useState<string | undefined>(undefined);
  // Guards against a slow preview response overwriting a newer one.
  const previewRequestRef = useRef(0);

  const defaultValues = useMemo<IServiceLogPayload>(
    () =>
      initialValues ?? {
        worked_on: todayIsoDate(),
        description: "",
        // Remembered preference, per section 1.
        entry_mode: readPreferredEntryMode(),
        duration: "",
        start_time: "",
        end_time: "",
        // Pre-selected from the catalogue default; the client's own default drives the
        // billing type, which is decision D7 resolved by the store.
        hour_type_id: defaultHourType?.id ?? null,
        billing_type_id: resolveDefaultBillingTypeId(undefined) ?? "",
      },
    [initialValues, defaultHourType, resolveDefaultBillingTypeId]
  );

  const {
    control,
    formState: { errors, isSubmitting },
    handleSubmit,
    watch,
  } = useForm<IServiceLogPayload>({ defaultValues });

  const entryMode = watch("entry_mode");
  const duration = watch("duration");
  const startTime = watch("start_time");
  const endTime = watch("end_time");
  const workedOn = watch("worked_on");
  const hourTypeId = watch("hour_type_id");
  const billingTypeId = watch("billing_type_id");

  const isIntervalMode = entryMode === "interval";

  /**
   * Ask the server what this submission would create.
   *
   * Debounced, because it fires on every keystroke in the duration field. Skipped when
   * the inputs cannot possibly form a valid entry, so the technician does not see an
   * error for a field they are still typing into.
   */
  const refreshPreview = useCallback(
    async (payload: IServiceLogPayload) => {
      const requestId = previewRequestRef.current + 1;
      previewRequestRef.current = requestId;

      try {
        const response = await serviceLog.previewServiceLog(workspaceSlug, projectId, issueId, payload);
        if (previewRequestRef.current !== requestId) return;
        setPreview(response);
        setPreviewError(undefined);
      } catch (error) {
        if (previewRequestRef.current !== requestId) return;
        setPreview(null);
        setPreviewError(extractServiceLogErrorCode(error));
      }
    },
    [serviceLog, workspaceSlug, projectId, issueId]
  );

  useEffect(() => {
    const hasEnoughInput = isIntervalMode ? Boolean(startTime && endTime) : Boolean(duration?.trim());

    if (!hasEnoughInput || !billingTypeId || !hourTypeId) {
      setPreview(null);
      setPreviewError(undefined);
      return;
    }

    const timeout = setTimeout(() => {
      void refreshPreview({
        worked_on: workedOn,
        description: "preview",
        entry_mode: entryMode,
        duration: isIntervalMode ? null : duration,
        start_time: isIntervalMode ? startTime : null,
        end_time: isIntervalMode ? endTime : null,
        hour_type_id: hourTypeId,
        billing_type_id: billingTypeId,
      });
    }, 350);

    return () => clearTimeout(timeout);
  }, [duration, startTime, endTime, workedOn, entryMode, hourTypeId, billingTypeId, isIntervalMode, refreshPreview]);

  // A long entry has to be confirmed again if the duration changes after confirming.
  useEffect(() => {
    setHasConfirmedLongEntry(false);
  }, [duration, startTime, endTime]);

  const needsLongEntryConfirmation = Boolean(preview?.warning) && !hasConfirmedLongEntry;

  const onSubmit = async (values: IServiceLogPayload) => {
    if (needsLongEntryConfirmation) {
      setHasConfirmedLongEntry(true);
      return;
    }

    setSubmitError(undefined);

    const payload: IServiceLogPayload = {
      ...values,
      duration: isIntervalMode ? null : values.duration,
      start_time: isIntervalMode ? values.start_time : null,
      end_time: isIntervalMode ? values.end_time : null,
    };

    try {
      if (batchId) {
        await serviceLog.updateServiceLogBatch(workspaceSlug, projectId, issueId, batchId, payload);
      } else {
        await serviceLog.createServiceLog(workspaceSlug, projectId, issueId, payload);
        writePreferredEntryMode(values.entry_mode);
      }
      onSubmitted?.();
      handleClose();
    } catch (error) {
      setSubmitError(extractServiceLogErrorCode(error));
    }
  };

  const translateCode = (code: string | undefined) =>
    code ? t(SERVICE_LOG_ERROR_KEYS[code] ?? "work_item.service_log.toasts.not_created.message") : undefined;

  return (
    <form onSubmit={handleSubmit(onSubmit)} className="flex flex-col gap-4">
      {/* Entry mode toggle -- rule R9 */}
      <Controller
        control={control}
        name="entry_mode"
        render={({ field: { value, onChange } }) => (
          <div className="flex flex-col gap-1.5">
            <span className="text-sm text-custom-text-200 font-medium">
              {t("work_item.service_log.form.entry_mode")}
            </span>
            <div className="border-custom-border-200 flex w-fit rounded-md border p-0.5">
              {(["duration", "interval"] as TServiceLogEntryMode[]).map((mode) => (
                <button
                  key={mode}
                  type="button"
                  onClick={() => onChange(mode)}
                  className={`text-sm rounded px-3 py-1 transition-colors ${
                    value === mode
                      ? "bg-custom-primary-100 text-white"
                      : "text-custom-text-300 hover:text-custom-text-100"
                  }`}
                >
                  {t(`work_item.service_log.form.mode_${mode}`)}
                </button>
              ))}
            </div>
          </div>
        )}
      />

      {/* Duration mode -- rule R1 */}
      {!isIntervalMode && (
        <Controller
          control={control}
          name="duration"
          rules={{
            validate: (value) =>
              isIntervalMode || (value ?? "").trim().length > 0
                ? true
                : t("work_item.service_log.form.duration_required"),
          }}
          render={({ field: { value, onChange } }) => (
            <div className="flex flex-col gap-1">
              <label className="text-sm text-custom-text-200 font-medium" htmlFor="service-log-duration">
                {t("work_item.service_log.form.duration")}
              </label>
              <Input
                id="service-log-duration"
                value={value ?? ""}
                onChange={(event) => onChange(event.target.value)}
                placeholder={t("work_item.service_log.form.duration_placeholder")}
                hasError={Boolean(errors.duration) || Boolean(previewError)}
              />
              <span className="text-xs text-custom-text-400">{t("work_item.service_log.form.duration_hint")}</span>
              {errors.duration && <span className="text-xs text-red-500">{errors.duration.message}</span>}
            </div>
          )}
        />
      )}

      {/* Interval mode -- rule R9 */}
      {isIntervalMode && (
        <div className="flex gap-3">
          {(["start_time", "end_time"] as const).map((fieldName) => (
            <Controller
              key={fieldName}
              control={control}
              name={fieldName}
              rules={{
                validate: (value) =>
                  !isIntervalMode || value ? true : t("work_item.service_log.form.interval_required"),
              }}
              render={({ field: { value, onChange } }) => (
                <div className="flex flex-1 flex-col gap-1">
                  <label className="text-sm text-custom-text-200 font-medium" htmlFor={`service-log-${fieldName}`}>
                    {t(`work_item.service_log.form.${fieldName}`)}
                  </label>
                  <input
                    id={`service-log-${fieldName}`}
                    type="time"
                    value={value ?? ""}
                    onChange={(event) => onChange(event.target.value)}
                    className="border-custom-border-200 bg-custom-background-100 text-sm rounded-md border px-3 py-1.5"
                  />
                  {errors[fieldName] && <span className="text-xs text-red-500">{errors[fieldName]?.message}</span>}
                </div>
              )}
            />
          ))}
        </div>
      )}

      {/* Live conversion -- R1, and the rounding notice of acceptance criterion 2 */}
      {preview && (
        <div className="bg-custom-background-90 flex flex-col gap-1 rounded-md px-3 py-2">
          <div className="text-sm text-custom-text-200 flex items-center gap-2">
            <Clock className="size-3.5 shrink-0" />
            <span className="font-medium">
              {t("work_item.service_log.preview.converted", {
                duration: preview.segments[0]?.logged_hours_display ?? "",
                hours: preview.segments[0]?.equivalent_hours_display ?? "",
              })}
            </span>
          </div>
          {preview.was_rounded && (
            <span className="text-xs text-custom-text-350">
              {t("work_item.service_log.preview.rounded", {
                value: preview.segments[0]?.logged_hours_display ?? "",
              })}
            </span>
          )}
          {/* Split preview -- section 3 requires it before saving */}
          {preview.segments.length > 1 && (
            <div className="border-custom-border-200 mt-1 flex flex-col gap-0.5 border-t pt-1.5">
              <span className="text-xs text-custom-text-200 font-medium">
                {t("work_item.service_log.preview.segments_title", { count: preview.segments.length })}
              </span>
              {preview.segments.map((segment) => (
                <span key={segment.segment_index} className="text-xs text-custom-text-350">
                  {segment.logged_hours_display} · {segment.hour_type_name}
                  {segment.classification_reason ? ` · ${segment.classification_reason}` : ""}
                </span>
              ))}
              <span className="text-xs text-custom-text-200 font-medium">
                {t("work_item.service_log.preview.total", { value: preview.totals.logged_hours })}
              </span>
            </div>
          )}
        </div>
      )}

      {previewError && (
        <div className="text-xs text-red-500 flex items-center gap-2">
          <AlertTriangle className="size-3.5 shrink-0" />
          <span>{translateCode(previewError)}</span>
        </div>
      )}

      {/* Date of service -- R7 */}
      <Controller
        control={control}
        name="worked_on"
        rules={{ required: t("work_item.service_log.form.worked_on_required") }}
        render={({ field: { value, onChange } }) => (
          <div className="flex flex-col gap-1">
            <label className="text-sm text-custom-text-200 font-medium" htmlFor="service-log-worked-on">
              {t("work_item.service_log.form.worked_on")}
            </label>
            <input
              id="service-log-worked-on"
              type="date"
              value={value}
              // The server is the authority on "future" -- it resolves today in the
              // workspace timezone. This only stops the obvious case in the picker.
              max={todayIsoDate()}
              onChange={(event) => onChange(event.target.value)}
              className="border-custom-border-200 bg-custom-background-100 text-sm rounded-md border px-3 py-1.5"
            />
            <span className="text-xs text-custom-text-400">{t("work_item.service_log.form.worked_on_hint")}</span>
            {errors.worked_on && <span className="text-xs text-red-500">{errors.worked_on.message}</span>}
          </div>
        )}
      />

      {/* Catalogue dropdowns -- Phase 2 criteria 1 and 2 */}
      <div className="flex gap-3">
        <Controller
          control={control}
          name="hour_type_id"
          rules={{ required: t("work_item.service_log.form.hour_type_required") }}
          render={({ field: { value, onChange } }) => (
            <div className="flex flex-1 flex-col gap-1">
              <span className="text-sm text-custom-text-200 font-medium">
                {t("work_item.service_log.form.hour_type")}
              </span>
              <CustomSelect
                value={value}
                onChange={onChange}
                label={activeHourTypes.find((option) => option.id === value)?.name ?? "—"}
                buttonClassName="border border-custom-border-200"
              >
                {/* Ordered by `sequence`, which is the order the admin dragged them
                    into, and filtered to active options by the store. */}
                {activeHourTypes.map((option) => (
                  <CustomSelect.Option key={option.id} value={option.id}>
                    {option.name}
                  </CustomSelect.Option>
                ))}
              </CustomSelect>
              {errors.hour_type_id && <span className="text-xs text-red-500">{errors.hour_type_id.message}</span>}
            </div>
          )}
        />

        <Controller
          control={control}
          name="billing_type_id"
          rules={{ required: t("work_item.service_log.form.billing_type_required") }}
          render={({ field: { value, onChange } }) => (
            <div className="flex flex-1 flex-col gap-1">
              <span className="text-sm text-custom-text-200 font-medium">
                {t("work_item.service_log.form.billing_type")}
              </span>
              <CustomSelect
                value={value}
                onChange={onChange}
                label={activeBillingTypes.find((option) => option.id === value)?.name ?? "—"}
                buttonClassName="border border-custom-border-200"
              >
                {activeBillingTypes.map((option) => (
                  <CustomSelect.Option key={option.id} value={option.id}>
                    {option.name}
                  </CustomSelect.Option>
                ))}
              </CustomSelect>
              {errors.billing_type_id && <span className="text-xs text-red-500">{errors.billing_type_id.message}</span>}
            </div>
          )}
        />
      </div>

      {/* Description -- the client reads this */}
      <Controller
        control={control}
        name="description"
        rules={{
          validate: (value) => (value.trim().length > 0 ? true : t("work_item.service_log.form.description_required")),
        }}
        render={({ field: { value, onChange } }) => (
          <div className="flex flex-col gap-1">
            <label className="text-sm text-custom-text-200 font-medium" htmlFor="service-log-description">
              {t("work_item.service_log.form.description")}
            </label>
            <textarea
              id="service-log-description"
              value={value}
              onChange={(event) => onChange(event.target.value)}
              placeholder={t("work_item.service_log.form.description_placeholder")}
              rows={3}
              className="border-custom-border-200 bg-custom-background-100 text-sm resize-none rounded-md border px-3 py-2"
            />
            {errors.description && <span className="text-xs text-red-500">{errors.description.message}</span>}
          </div>
        )}
      />

      {/* Over 24 hours -- warn and ask, never block. Section 5. */}
      {preview?.warning && (
        <div className="bg-amber-500/10 flex items-start gap-2 rounded-md px-3 py-2">
          <Info className="text-amber-500 mt-0.5 size-3.5 shrink-0" />
          <div className="flex flex-col">
            <span className="text-xs text-amber-600 font-medium">
              {t("work_item.service_log.warnings.over_24_hours")}
            </span>
            <span className="text-xs text-custom-text-350">
              {t("work_item.service_log.warnings.over_24_hours_confirm")}
            </span>
          </div>
        </div>
      )}

      {submitError && (
        <div className="text-xs text-red-500 flex items-center gap-2">
          <AlertTriangle className="size-3.5 shrink-0" />
          <span>{translateCode(submitError)}</span>
        </div>
      )}

      <div className="flex justify-end gap-2 pt-1">
        <Button variant="secondary" size="sm" onClick={handleClose} type="button">
          {t("work_item.service_log.form.cancel")}
        </Button>
        <Button variant="primary" size="sm" type="submit" loading={isSubmitting}>
          {needsLongEntryConfirmation
            ? t("work_item.service_log.warnings.confirm")
            : batchId
              ? t("work_item.service_log.form.update")
              : t("work_item.service_log.form.submit")}
        </Button>
      </div>
    </form>
  );
});
