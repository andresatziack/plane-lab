/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
import { AlertTriangle, CornerLeftUp } from "lucide-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import type { IServiceIssueAllowanceAlert, IServiceIssueAllowanceSummary } from "@plane/types";
import { Tooltip } from "@plane/ui";

type Props = {
  summary: IServiceIssueAllowanceSummary;
  alerts: IServiceIssueAllowanceAlert[];
};

/**
 * The allowance balance on a work item. Section 4 of the allowance phase brief.
 *
 * "Indicador de saldo da bolsa destacado no work item: creditado, consumido, restante, e
 * percentual de consumo." All four, and each one labelled -- the same discipline
 * `ServiceLogTotals` follows, and for the same reason: these numbers are only useful if
 * nobody has to guess which is which.
 *
 * Three things this deliberately does **not** do:
 *
 * 1. **No arithmetic.** Every figure arrives as a decimal string computed server side.
 *    Section 4b of the master context requires totals to be sums of persisted values, and
 *    parsing these into floats is how a rounding error reaches an invoice. The only number
 *    derived here is the width of the progress bar, which is decoration and is clamped.
 * 2. **No "0%" for an empty allowance.** `consumed_pct` is null before any credit exists,
 *    because zero of zero is not zero -- rendering "0% consumido" would state something
 *    false.
 * 3. **It says when the allowance is inherited, and from where.** A technician looking at
 *    a sub-task has to know the 40h belong to the parent project. Without that line the
 *    obvious next move is to ask for a second allowance on the sub-task, which the schema
 *    refuses.
 */
export const ServiceAllowanceIndicator = observer(function ServiceAllowanceIndicator(props: Props) {
  const { summary, alerts } = props;
  const { t } = useTranslation();

  // Presentation only. The bar is clamped to 100 so an overrun does not overflow its
  // track; the *numbers* below still tell the truth about the deficit, which is what
  // matters -- section 3 requires an overrun to be visible, not tidy.
  const consumedPct = summary.consumed_pct === null ? null : Number(summary.consumed_pct);
  const barWidth = consumedPct === null ? 0 : Math.min(Math.max(consumedPct, 0), 100);
  const isOverrun = summary.balance_hours.startsWith("-");
  const isClosed = summary.status === "closed";

  const cards = [
    { key: "credited", label: t("work_item.service_allowance.credited"), value: summary.credited_hours },
    { key: "consumed", label: t("work_item.service_allowance.consumed"), value: summary.consumed_hours },
    { key: "balance", label: t("work_item.service_allowance.balance"), value: summary.balance_hours },
  ];

  return (
    <div
      className={`flex flex-col gap-2 rounded-md border px-3 py-2 ${
        isOverrun ? "border-red-500/40 bg-red-500/5" : "border-custom-border-200 bg-custom-background-90"
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 flex-col">
          <Tooltip tooltipContent={t("work_item.service_allowance.title_hint")} position="top">
            <span className="text-sm text-custom-text-100 font-medium">{t("work_item.service_allowance.title")}</span>
          </Tooltip>
          {summary.reference && <span className="text-xs text-custom-text-350 truncate">{summary.reference}</span>}
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          {isClosed && (
            <span className="bg-custom-background-80 text-xs text-custom-text-300 rounded px-1.5 py-0.5">
              {t("work_item.service_allowance.closed")}
            </span>
          )}
          {consumedPct !== null && (
            <span className={`text-sm font-semibold ${isOverrun ? "text-red-500" : "text-custom-text-100"}`}>
              {t("work_item.service_allowance.consumed_pct", { pct: summary.consumed_pct })}
            </span>
          )}
        </div>
      </div>

      {/* Inheritance, stated rather than implied. */}
      {summary.is_inherited && (
        <div className="text-xs text-custom-text-300 flex items-start gap-1.5">
          <CornerLeftUp className="mt-0.5 size-3 shrink-0" />
          <span>{t("work_item.service_allowance.inherited")}</span>
        </div>
      )}

      {consumedPct !== null && (
        <div className="bg-custom-background-80 h-1.5 w-full overflow-hidden rounded-full">
          <div
            className={`h-full rounded-full ${isOverrun ? "bg-red-500" : "bg-custom-primary-100"}`}
            style={{ width: `${barWidth}%` }}
          />
        </div>
      )}

      <div className="grid grid-cols-3 gap-2">
        {cards.map((card) => (
          <div key={card.key} className="flex flex-col gap-0.5">
            <span className="text-xs text-custom-text-350">{card.label}</span>
            <span
              className={`text-sm font-semibold ${
                card.key === "balance" && isOverrun ? "text-red-500" : "text-custom-text-100"
              }`}
            >
              {t("work_item.service_allowance.hours", { hours: card.value })}
            </span>
          </div>
        ))}
      </div>

      {/* Section 3: overrunning does not block the work log, "a bolsa fica com saldo
          negativo e o alerta aparece". The alert also has to say what does NOT happen --
          the overflow never reaches the client's support contract -- because that is the
          question an admin seeing a negative balance asks first. */}
      {alerts.map((alert) => (
        <div key={alert.code} className="text-xs text-red-500 flex items-start gap-1.5">
          <AlertTriangle className="mt-0.5 size-3 shrink-0" />
          <span>
            {alert.code === "ALLOWANCE_NEGATIVE_BALANCE"
              ? t("work_item.service_allowance.alerts.negative_balance")
              : t("work_item.service_allowance.alerts.high_consumption")}
          </span>
        </div>
      ))}
    </div>
  );
});
