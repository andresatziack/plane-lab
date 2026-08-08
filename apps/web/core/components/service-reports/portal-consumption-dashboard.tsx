/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import React, { useMemo, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import useSWR from "swr";
import { useTranslation } from "@plane/i18n";
import { Loader } from "@plane/ui";
import { cn } from "@plane/utils";
import { ServiceReportsService } from "@/services/service-reports.service";
import { DistributionChart, DistributionTable, HoursSeriesChart, StackedCompetenceChart } from "./charts";
import { ReportInsightCard } from "./report-insight-card";
import { competenceRange } from "./use-report-filters";

const reports = new ServiceReportsService();

type Props = {
  workspaceSlug: string;
  projectId: string;
};

/** How far back the client can see. Named here because it is a limitation, not a default. */
const WINDOW_MONTHS = 11;

/**
 * What the client's own user sees about their consumption. Section 3c. Phase 8b, criteria 1 to 8.
 *
 * Mirrors the layout of `ConsumptionTab` deliberately, and reuses its chart primitives, but is
 * **its own component rather than a mode of it**. Four of the things that make it the client's
 * dashboard are absences, and each of them would have been a conditional inside that file:
 *
 * - **No money anywhere.** `ReportViewer.guest()` does not compute it, so there is no card to
 *   hide behind a presence check -- R11, and criterion 4.
 * - **No `logged_hours` series.** The client legitimately sees `equivalent_hours`, so offering
 *   both would give up the multiplier, which R11(c) forbids by name. `ConsumptionTab` adds that
 *   series when the key is present; here it is never offered, so a payload that started carrying
 *   it could not leak it through this screen.
 * - **No drill-down.** Every bucket carries its descriptor, but the endpoint that replays it,
 *   `service-reports/logs/`, is ADMIN and MEMBER only -- a client clicking through would get a
 *   403. A control that can only fail is worse than no control, so the tables and chips here are
 *   not clickable.
 * - **No toolbar.** It carries a Cliente selector and a CSV export, and the client chooses no
 *   company: the project they are in *is* the choice (D67).
 *
 * **Accepted limitation:** the window is the last twelve competencies, fixed. A contract longer
 * than that has months the client cannot reach from this screen. Not a criterion of this phase,
 * and named in the PR body rather than left as a silent default.
 */
export const PortalConsumptionDashboard = React.memo(function PortalConsumptionDashboard(props: Props) {
  const { workspaceSlug, projectId } = props;
  const { t } = useTranslation();
  const [expandedAllowance, setExpandedAllowance] = useState<string | null>(null);

  // Fixed, and not `useReportFilters`: that hook exists to hold a toolbar's state, and there is
  // no toolbar here. `competence_basis` is never set -- the server decides it from what is being
  // measured (D48), and a contract dashboard grouped by service date would file retroactive
  // hours in a month the contract never had.
  const filters = useMemo(() => competenceRange(WINDOW_MONTHS), []);

  const { data, isLoading, error } = useSWR(
    workspaceSlug && projectId ? `service-report-portal-${workspaceSlug}-${projectId}` : null,
    workspaceSlug && projectId ? () => reports.fetchPortal(workspaceSlug, projectId, filters) : null
  );

  const contractSeries = useMemo(() => {
    if (!data?.contracts) return [];

    // One section per contract. A client holding two contracts must never see one balance:
    // section 2 of Phase 8 says they are independent, and summing them here would be the same
    // consolidation the endpoint now refuses to produce.
    return data.contracts.map((contract) => ({
      contract,
      rows: contract.statement.map((row) => ({
        competence: row.competence,
        consumed: Number(row.consumed_hours),
        overage: Number(row.overage_hours),
      })),
    }));
  }, [data?.contracts]);

  if (isLoading) {
    return (
      <Loader className="space-y-4 p-4">
        <Loader.Item height="80px" />
        <Loader.Item height="240px" />
        <Loader.Item height="240px" />
      </Loader>
    );
  }

  if (error || !data) return null;

  const totals = data.totals;
  // An empty payload is a legitimate 200 with the full key set, not a failure (criterion 8):
  // a project with no hours yet and a project that is not the caller's produce the same shape.
  const isEmpty = totals.entries === 0 && contractSeries.length === 0;

  return (
    <div className="flex flex-col gap-6 p-4">
      <div className="grid grid-cols-2 gap-6 rounded-lg border border-subtle p-4 md:grid-cols-3">
        <ReportInsightCard
          label={t("service_reports.portal.consumed_hours")}
          value={totals.equivalent_hours_display}
          hint={t("service_reports.portal.consumed_hours_hint")}
        />
        <ReportInsightCard
          label={t("service_reports.totals.debited_hours")}
          value={totals.debited_hours_display}
          hint={t("service_reports.totals.debited_hint")}
        />
        <ReportInsightCard label={t("service_reports.totals.entries")} value={String(totals.entries)} />
      </div>

      {isEmpty ? (
        <section className="rounded-lg border border-subtle p-8 text-center">
          <p className="text-13 text-tertiary">{t("service_reports.portal.empty")}</p>
        </section>
      ) : null}

      {!isEmpty ? (
        <section className="rounded-lg border border-subtle p-4">
          <h3 className="mb-1 text-14 font-medium text-primary">
            {data.shape === "contract"
              ? t("service_reports.consumption.contract_series")
              : t("service_reports.portal.series_title")}
          </h3>
          <p className="mb-3 text-12 text-tertiary">
            {data.shape === "contract"
              ? t("service_reports.consumption.contract_series_hint")
              : t("service_reports.consumption.standalone_series_hint")}
          </p>
          {/* `logged_hours` is deliberately not offered. See the note on this component. */}
          <HoursSeriesChart
            data={data.consumption_series}
            keys={[
              { key: "equivalent_hours", label: t("service_reports.portal.consumed_hours") },
              { key: "debited_hours", label: t("service_reports.totals.debited_hours") },
            ]}
          />
          <div className="mt-3 flex flex-wrap gap-2">
            {/* Plain spans, not buttons: the drill-down endpoint refuses a client. */}
            {data.consumption_series.map((point) => (
              <span key={point.competence} className="rounded border border-subtle px-2 py-1 text-11 text-tertiary">
                {point.competence}: {point.equivalent_hours_display}
              </span>
            ))}
          </div>
          <p className="mt-3 text-11 text-tertiary">{t("service_reports.portal.window_hint")}</p>
        </section>
      ) : null}

      {contractSeries.map(({ contract, rows }) => (
        <section key={contract.contract_id} className="rounded-lg border border-subtle p-4">
          {/* No `service_client_name`: the portal payload does not carry it, and repeating the
              Cliente's own name back to them would be noise. */}
          <h3 className="mb-3 text-14 font-medium text-primary">
            {contract.code} — {contract.name}
          </h3>
          <StackedCompetenceChart
            data={rows}
            series={[
              { key: "consumed", label: t("service_reports.consumption.consumed") },
              { key: "overage", label: t("service_reports.consumption.overage") },
            ]}
          />
          <table className="mt-4 w-full text-13">
            <thead>
              <tr className="border-b border-subtle text-left text-tertiary">
                <th className="font-normal py-2 pr-3">{t("service_reports.statement.competence")}</th>
                <th className="font-normal py-2 pr-3 text-right">{t("service_reports.statement.granted")}</th>
                <th className="font-normal py-2 pr-3 text-right">{t("service_reports.statement.consumed")}</th>
                <th className="font-normal py-2 pr-3 text-right">{t("service_reports.statement.balance")}</th>
                <th className="font-normal py-2 pr-3 text-right">{t("service_reports.consumption.overage")}</th>
                <th className="font-normal py-2">{t("service_reports.statement.parcels")}</th>
              </tr>
            </thead>
            <tbody>
              {contract.statement.map((row) => (
                <tr key={row.period_id} className="border-b border-subtle/60">
                  <td className="py-2 pr-3 text-secondary">{row.competence}</td>
                  <td className="py-2 pr-3 text-right text-secondary">{row.granted_hours_display}</td>
                  <td className="py-2 pr-3 text-right text-secondary">{row.consumed_hours_display}</td>
                  <td
                    className={cn(
                      "py-2 pr-3 text-right",
                      Number(row.balance_hours) < 0 ? "text-danger" : "text-primary"
                    )}
                  >
                    {row.balance_hours_display}
                  </td>
                  <td
                    className={cn(
                      "py-2 pr-3 text-right",
                      Number(row.overage_hours) > 0 ? "text-danger" : "text-tertiary"
                    )}
                  >
                    {row.overage_hours_display}
                  </td>
                  <td className="py-2 text-11 text-tertiary">
                    {/* The origin competency of each carried parcel: what a single balance figure
                        cannot answer, and what a client asks first when the number surprises them. */}
                    {row.parcels.map((parcel) => `${parcel.origin_competence}: ${parcel.hours_display}`).join(" · ") || "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ))}

      {!isEmpty ? (
        <div className="grid gap-6 md:grid-cols-2">
          <section className="rounded-lg border border-subtle p-4">
            <h3 className="mb-3 text-14 font-medium text-primary">{t("service_reports.distribution.hour_type")}</h3>
            <DistributionChart slices={data.distributions.hour_type} nameKey="hour_type_name" />
            <div className="mt-4">
              {/* No `onRowClick`: the rows would 403. */}
              <DistributionTable
                slices={data.distributions.hour_type}
                nameKey="hour_type_name"
                nameLabel={t("service_reports.distribution.hour_type")}
              />
            </div>
          </section>

          <section className="rounded-lg border border-subtle p-4">
            <h3 className="mb-1 text-14 font-medium text-primary">{t("service_reports.non_billable.title")}</h3>
            {/* R5, and for the client most of all: "não faturável" as one number is what turns a
                courtesy into an argument. Warranty honoured and goodwill given are different
                conversations, so they are different rows. */}
            <p className="mb-3 text-12 text-tertiary">{t("service_reports.non_billable.hint")}</p>
            <DistributionTable
              slices={data.non_billable}
              nameKey="billing_type_name"
              nameLabel={t("service_reports.distribution.billing_type")}
            />
          </section>
        </div>
      ) : null}

      {data.allowances.active.length > 0 ? (
        <section className="rounded-lg border border-subtle p-4">
          <h3 className="mb-3 text-14 font-medium text-primary">{t("service_reports.allowances.active")}</h3>
          <ul className="divide-y divide-subtle/60">
            {data.allowances.active.map((allowance) => (
              <li key={allowance.id} className="py-2">
                <button
                  type="button"
                  className="flex w-full items-center gap-2 text-left"
                  onClick={() => setExpandedAllowance(expandedAllowance === allowance.id ? null : allowance.id)}
                >
                  {expandedAllowance === allowance.id ? (
                    <ChevronDown className="h-3.5 w-3.5 text-tertiary" />
                  ) : (
                    <ChevronRight className="h-3.5 w-3.5 text-tertiary" />
                  )}
                  <span className="flex-1 truncate text-13 text-secondary">{allowance.issue_name}</span>
                  <span className="text-13 text-tertiary">
                    {allowance.consumed_hours_display} / {allowance.credited_hours_display}
                  </span>
                  <span
                    className={cn(
                      "w-20 text-right text-13",
                      Number(allowance.balance_hours) < 0 ? "text-danger" : "text-primary"
                    )}
                  >
                    {allowance.balance_hours}
                  </span>
                </button>
                {expandedAllowance === allowance.id ? (
                  <div className="mt-2 pl-6">
                    <h4 className="mb-1 text-12 font-medium text-tertiary">
                      {t("service_reports.allowances.credit_history")}
                    </h4>
                    <ul className="space-y-1">
                      {allowance.credits.map((credit) => (
                        <li key={credit.entry_id} className="text-12 text-tertiary">
                          {credit.hours}h · {new Date(credit.created_at).toLocaleDateString("pt-BR")} ·{" "}
                          {credit.actor_display_name ?? "—"}
                          {credit.origin_competence
                            ? ` · ${t("service_reports.allowances.from_competence", {
                                value: credit.origin_competence,
                              })}`
                            : ""}
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {data.allowances.pending_closure.length > 0 ? (
        <section className="rounded-lg border border-warning-strong/40 bg-warning-subtle/20 p-4">
          <h3 className="mb-1 text-14 font-medium text-primary">{t("service_reports.allowances.pending_closure")}</h3>
          {/* The revised D35: the system informs, a human decides. Shown to the client because
              the balance is theirs, and nothing here writes anything off. */}
          <p className="mb-3 text-12 text-tertiary">{t("service_reports.allowances.pending_closure_hint")}</p>
          <ul className="divide-y divide-subtle/60">
            {data.allowances.pending_closure.map((allowance) => (
              <li key={allowance.id} className="flex items-center gap-2 py-2">
                <span className="flex-1 truncate text-13 text-secondary">{allowance.issue_name}</span>
                <span className="text-13 text-tertiary">
                  {t("service_reports.allowances.balance")}: {allowance.balance_hours}
                </span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
});
