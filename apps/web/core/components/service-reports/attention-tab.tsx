/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import React from "react";
import { AlertTriangle, BellOff, Clock, TrendingDown } from "lucide-react";
import { useParams } from "next/navigation";
import useSWR from "swr";
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import type { TServiceAlert, TServiceAlertSeverity } from "@plane/types";
import { Loader } from "@plane/ui";
import { cn } from "@plane/utils";
import { ServiceContractService } from "@/services/service-contract.service";
import { ServiceIssueAllowanceService } from "@/services/service-issue-allowance.service";
import { ServiceReportsService } from "@/services/service-reports.service";

const reports = new ServiceReportsService();
const contracts = new ServiceContractService();
const allowances = new ServiceIssueAllowanceService();

/**
 * Everything that needs somebody to act. Section 3b.
 *
 * **Grouped by severity, not by entity**, because the response an operator has to make differs
 * by severity and not by whether the thing is a contract or an allowance:
 *
 * - `high_consumption` -- work that may never get paid for, act now;
 * - `low_consumption` -- the churn signal, and section 9 is explicit that it is "tão acionável
 *   quanto o alerta de estouro". Rendered with the same weight as the other extreme, because
 *   a client paying for a pool they do not use will question the renewal;
 * - `contract` -- the agreement itself has a problem;
 * - `pending_action` -- a decision is waiting, and this one gets a **button** rather than a
 *   colour. It is the revised D35: the grace period ran out, the balance is still there, and a
 *   human closes the allowance deliberately. Nothing is written off by a timer.
 *
 * Dismissal is available to Members as well as Admins, because section 9 puts the panel in front
 * of technicians and an alert nobody present can quiet is an alert everybody learns to ignore.
 */

const SEVERITY_ICON: Record<TServiceAlertSeverity, React.ReactNode> = {
  high_consumption: <AlertTriangle className="text-danger h-3.5 w-3.5" />,
  low_consumption: <TrendingDown className="text-warning h-3.5 w-3.5" />,
  contract: <Clock className="text-warning h-3.5 w-3.5" />,
  pending_action: <BellOff className="h-3.5 w-3.5 text-tertiary" />,
};

const SEVERITY_ORDER: TServiceAlertSeverity[] = ["high_consumption", "pending_action", "contract", "low_consumption"];

export const AttentionTab = React.memo(function AttentionTab() {
  const { t } = useTranslation();
  const params = useParams();
  const workspaceSlug = params.workspaceSlug?.toString();

  const { data, isLoading, mutate } = useSWR(
    workspaceSlug ? `service-report-attention-${workspaceSlug}` : null,
    workspaceSlug ? () => reports.fetchAttention(workspaceSlug) : null
  );

  const dismissPeriodAlert = async (periodId: string, alertCode: string) => {
    if (!workspaceSlug) return;
    await contracts.dismissAlert(workspaceSlug, periodId, alertCode);
    await mutate();
  };

  const dismissAllowanceAlert = async (allowanceId: string, alertCode: string) => {
    if (!workspaceSlug) return;
    // The route Phase 5 could not offer: the dismissal table had a mandatory foreign key to a
    // contract period until migration 0132 gave it the D54 nullable pair.
    await allowances.dismissAlert(workspaceSlug, allowanceId, alertCode);
    await mutate();
  };

  if (isLoading) {
    return (
      <Loader className="space-y-3 p-4">
        <Loader.Item height="60px" />
        <Loader.Item height="60px" />
      </Loader>
    );
  }

  if (!data) return null;

  const alertRow = (
    alert: TServiceAlert,
    label: string,
    detail: string,
    onDismiss: () => void,
    action?: React.ReactNode
  ) => (
    <li key={`${label}-${alert.code}`} className="flex items-center gap-3 py-2">
      <span className="flex-shrink-0">{SEVERITY_ICON[alert.severity]}</span>
      <div className="min-w-0 flex-1">
        <div className="truncate text-13 text-secondary">{label}</div>
        <div className="text-11 text-tertiary">
          {t(`service_reports.alerts.${alert.code}`, { defaultValue: alert.code })} · {detail}
        </div>
      </div>
      {action}
      <Button variant="link" size="sm" onClick={onDismiss}>
        {t("service_reports.alerts.dismiss")}
      </Button>
    </li>
  );

  const bySeverity = (severity: TServiceAlertSeverity) => {
    const rows: React.ReactNode[] = [];

    for (const entry of data.periods) {
      for (const alert of entry.alerts.filter((candidate) => candidate.severity === severity)) {
        rows.push(
          alertRow(
            alert,
            `${entry.contract_code} — ${entry.service_client_name}`,
            `${entry.competence} · ${t("service_reports.alerts.balance")}: ${entry.balance_hours_display}`,
            () => dismissPeriodAlert(entry.period_id, alert.code)
          )
        );
      }
    }

    for (const entry of data.allowances) {
      for (const alert of entry.alerts.filter((candidate) => candidate.severity === severity)) {
        rows.push(
          alertRow(
            alert,
            entry.issue_name,
            `${entry.project_name} · ${t("service_reports.alerts.balance")}: ${entry.balance_hours_display}`,
            () => dismissAllowanceAlert(entry.allowance_id, alert.code),
            // The action the revised D35 needs: closing is a deliberate human act, and the link
            // goes to the work item where the close endpoint already lives.
            alert.code === "ALLOWANCE_PENDING_CLOSURE" ? (
              <a
                href={`/${workspaceSlug}/browse/${entry.issue_id}/`}
                className="text-accent flex-shrink-0 text-12 hover:underline"
              >
                {t("service_reports.alerts.close_allowance")}
              </a>
            ) : undefined
          )
        );
      }
    }

    return rows;
  };

  return (
    <div className="flex flex-col gap-6 p-4">
      <div className="text-13 text-tertiary">{t("service_reports.alerts.count", { count: data.count })}</div>

      {SEVERITY_ORDER.map((severity) => {
        const rows = bySeverity(severity);

        if (rows.length === 0) return null;

        return (
          <section
            key={severity}
            className={cn(
              "rounded-lg border p-4",
              severity === "high_consumption" ? "border-danger-strong/40" : "border-subtle"
            )}
          >
            <h3 className="mb-1 flex items-center gap-2 text-14 font-medium text-primary">
              {SEVERITY_ICON[severity]}
              {t(`service_reports.alerts.severity.${severity}`)}
            </h3>
            <p className="mb-2 text-12 text-tertiary">{t(`service_reports.alerts.severity_hint.${severity}`)}</p>
            <ul className="divide-y divide-subtle/60">{rows}</ul>
          </section>
        );
      })}

      {data.clients_without_a_default_contract.length > 0 ? (
        <section className="rounded-lg border border-subtle p-4">
          <h3 className="mb-1 text-14 font-medium text-primary">{t("service_reports.alerts.no_default.title")}</h3>
          {/* A configuration fault rather than a consumption reading, and it is here because the
              symptom otherwise appears at the worst moment: the first work log on the project
              fails in front of a technician who cannot fix it. */}
          <p className="text-12 text-tertiary">{t("service_reports.alerts.no_default.hint")}</p>
          <p className="mt-2 text-13 text-secondary">
            {data.clients_without_a_default_contract.length} {t("service_reports.alerts.no_default.clients")}
          </p>
        </section>
      ) : null}

      {data.count === 0 ? (
        <div className="rounded-lg border border-subtle p-8 text-center text-13 text-tertiary">
          {t("service_reports.alerts.empty")}
        </div>
      ) : null}
    </div>
  );
});
