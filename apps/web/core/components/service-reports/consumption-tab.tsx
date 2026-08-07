/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import React, { useMemo, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useParams } from "next/navigation";
import useSWR from "swr";
import { useTranslation } from "@plane/i18n";
import type { TServiceReportFilters } from "@plane/types";
import { Loader } from "@plane/ui";
import { cn } from "@plane/utils";
import { ServiceClientService } from "@/services/service-client.service";
import { ServicePricingService } from "@/services/service-pricing.service";
import { ServiceReportsService } from "@/services/service-reports.service";
import { DistributionChart, DistributionTable, HoursSeriesChart, StackedCompetenceChart } from "./charts";
import { DrillDownModal } from "./drill-down-modal";
import { ReportInsightCard } from "./report-insight-card";
import { ReportToolbar } from "./report-toolbar";
import { useReportFilters } from "./use-report-filters";

const reports = new ServiceReportsService();
const clientsService = new ServiceClientService();
const pricing = new ServicePricingService();

/**
 * Consumption, for a client under contract or billed ad-hoc. Sections 1, 1b and 1c.
 *
 * **The shape comes from the server.** Whether a client holds a contract covering the window is
 * a property of the data, and the payload says which dashboard it returned in `shape` -- so
 * this component renders what arrived rather than deciding which report to ask for.
 *
 * Money appears only where the payload carries it. For a Member the keys are simply not there,
 * because the server never computed them (D50), so every money block here is behind a presence
 * check rather than behind a permission check duplicated in the browser.
 */
export const ConsumptionTab = React.memo(function ConsumptionTab() {
  const { t } = useTranslation();
  const params = useParams();
  const workspaceSlug = params.workspaceSlug?.toString();

  const { filters, setFilter, setFilters, key } = useReportFilters();
  const [drillDown, setDrillDown] = useState<{ title: string; filters: TServiceReportFilters } | null>(null);
  const [expandedAllowance, setExpandedAllowance] = useState<string | null>(null);
  const [isExporting, setIsExporting] = useState(false);

  const { data: clients } = useSWR(
    workspaceSlug ? `service-clients-${workspaceSlug}` : null,
    workspaceSlug ? () => clientsService.fetchServiceClients(workspaceSlug) : null
  );

  const { data, isLoading } = useSWR(
    workspaceSlug ? `service-report-consumption-${workspaceSlug}-${key}` : null,
    workspaceSlug ? () => reports.fetchConsumption(workspaceSlug, filters) : null
  );

  const overageSeries = useMemo(() => {
    if (!data?.contracts) return [];

    // One row per competency across every contract listed. Contracts are NOT summed into a
    // single pool -- section 1c is explicit about that -- so this chart is per contract and the
    // caller renders one per contract below.
    return data.contracts.map((contract) => ({
      contract,
      rows: contract.statement.map((row) => ({
        competence: row.competence,
        consumed: Number(row.consumed_hours),
        overage: Number(row.overage_hours),
      })),
    }));
  }, [data?.contracts]);

  const handleExport = async () => {
    if (!workspaceSlug) return;

    setIsExporting(true);
    try {
      // Criterion 9: the CSV is the selection on screen, because it is the same descriptor.
      await pricing.requestExport(workspaceSlug, { provider: "csv", filters } as never);
    } finally {
      setIsExporting(false);
    }
  };

  if (isLoading) {
    return (
      <Loader className="space-y-4 p-4">
        <Loader.Item height="80px" />
        <Loader.Item height="240px" />
        <Loader.Item height="240px" />
      </Loader>
    );
  }

  if (!data) return null;

  const totals = data.totals;

  return (
    <div className="flex flex-col gap-6 p-4">
      <ReportToolbar
        filters={filters}
        setFilter={setFilter}
        setFilters={setFilters}
        clients={clients?.map((client) => ({ id: client.id, name: client.name }))}
        onExport={handleExport}
        isExporting={isExporting}
      />

      <div className="grid grid-cols-2 gap-6 rounded-lg border border-subtle p-4 md:grid-cols-4">
        <ReportInsightCard
          label={t("service_reports.totals.equivalent_hours")}
          value={totals.equivalent_hours_display}
          onDrillDown={() =>
            setDrillDown({ title: t("service_reports.totals.equivalent_hours"), filters: totals.filters })
          }
        />
        <ReportInsightCard
          label={t("service_reports.totals.debited_hours")}
          value={totals.debited_hours_display}
          hint={t("service_reports.totals.debited_hint")}
        />
        <ReportInsightCard label={t("service_reports.totals.entries")} value={String(totals.entries)} />
        {/* Absent for a Member, so the card is not rendered at all rather than shown empty. */}
        {totals.amount_display ? (
          <ReportInsightCard
            label={t("service_reports.totals.amount")}
            value={totals.amount_display}
            hint={
              totals.average_amount_per_issue_display
                ? t("service_reports.totals.average_per_issue", {
                    value: totals.average_amount_per_issue_display,
                  })
                : undefined
            }
          />
        ) : null}
      </div>

      <section className="rounded-lg border border-subtle p-4">
        <h3 className="mb-3 text-14 font-medium text-primary">
          {data.shape === "contract"
            ? t("service_reports.consumption.contract_series")
            : t("service_reports.consumption.standalone_series")}
        </h3>
        <p className="mb-3 text-12 text-tertiary">
          {data.shape === "contract"
            ? t("service_reports.consumption.contract_series_hint")
            : t("service_reports.consumption.standalone_series_hint")}
        </p>
        <HoursSeriesChart
          data={data.consumption_series}
          keys={[
            ...(data.consumption_series[0]?.logged_hours !== undefined
              ? ([{ key: "logged_hours", label: t("service_reports.series.logged") }] as const)
              : []),
            { key: "equivalent_hours", label: t("service_reports.series.equivalent") },
          ]}
        />
        <div className="mt-3 flex flex-wrap gap-2">
          {data.consumption_series.map((point) => (
            <button
              key={point.competence}
              type="button"
              className="rounded border border-subtle px-2 py-1 text-11 text-tertiary hover:bg-surface-2"
              onClick={() =>
                point.filters ? setDrillDown({ title: point.competence, filters: point.filters }) : undefined
              }
            >
              {point.competence}: {point.equivalent_hours_display}
            </button>
          ))}
        </div>
      </section>

      {overageSeries.map(({ contract, rows }) => (
        <section key={contract.contract_id} className="rounded-lg border border-subtle p-4">
          <h3 className="mb-1 text-14 font-medium text-primary">
            {contract.code} — {contract.name}
          </h3>
          <p className="mb-3 text-12 text-tertiary">{contract.service_client_name}</p>
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
                <th className="font-normal py-2">{t("service_reports.statement.parcels")}</th>
              </tr>
            </thead>
            <tbody>
              {contract.statement.map((row) => (
                <tr key={row.period_id} className="border-b border-subtle/60">
                  <td className="py-2 pr-3 text-secondary">{row.competence}</td>
                  <td className="py-2 pr-3 text-right text-secondary">{row.granted_hours}</td>
                  <td className="py-2 pr-3 text-right text-secondary">{row.consumed_hours}</td>
                  <td
                    className={cn(
                      "py-2 pr-3 text-right",
                      Number(row.balance_hours) < 0 ? "text-danger" : "text-primary"
                    )}
                  >
                    {row.balance_hours}
                  </td>
                  <td className="py-2 text-11 text-tertiary">
                    {/* Section 7's "competência de origem de cada parcela" -- the part a scalar
                        balance cannot answer. */}
                    {row.parcels.map((parcel) => `${parcel.origin_competence}: ${parcel.hours}`).join(" · ") || "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ))}

      <div className="grid gap-6 md:grid-cols-2">
        <section className="rounded-lg border border-subtle p-4">
          <h3 className="mb-3 text-14 font-medium text-primary">{t("service_reports.distribution.hour_type")}</h3>
          <DistributionChart slices={data.distributions.hour_type} nameKey="hour_type_name" />
          <div className="mt-4">
            <DistributionTable
              slices={data.distributions.hour_type}
              nameKey="hour_type_name"
              nameLabel={t("service_reports.distribution.hour_type")}
              onRowClick={(slice) =>
                slice.filters ? setDrillDown({ title: slice.hour_type_name ?? "", filters: slice.filters }) : undefined
              }
            />
          </div>
        </section>

        <section className="rounded-lg border border-subtle p-4">
          <h3 className="mb-1 text-14 font-medium text-primary">{t("service_reports.non_billable.title")}</h3>
          {/* R5: never one number. The breakdown is the point -- an obligation honoured and
              goodwill spent are different conversations. */}
          <p className="mb-3 text-12 text-tertiary">{t("service_reports.non_billable.hint")}</p>
          <DistributionTable
            slices={data.non_billable}
            nameKey="billing_type_name"
            nameLabel={t("service_reports.distribution.billing_type")}
            onRowClick={(slice) =>
              slice.filters ? setDrillDown({ title: slice.billing_type_name ?? "", filters: slice.filters }) : undefined
            }
          />
        </section>
      </div>

      {data.revenue_series ? (
        <section className="rounded-lg border border-subtle p-4">
          <h3 className="mb-1 text-14 font-medium text-primary">{t("service_reports.revenue.title")}</h3>
          <p className="mb-3 text-12 text-tertiary">{t("service_reports.revenue.hint")}</p>
          <StackedCompetenceChart
            data={data.revenue_series.map((point) => ({
              competence: point.competence,
              standalone_log: Number(point.origins.standalone_log.amount ?? 0),
              out_of_scope_log: Number(point.origins.out_of_scope_log.amount ?? 0),
              contract_overage: Number(point.origins.contract_overage.amount ?? 0),
              allowance_overage: Number(point.origins.allowance_overage.amount ?? 0),
            }))}
            series={[
              { key: "standalone_log", label: t("service_reports.revenue.standalone") },
              { key: "out_of_scope_log", label: t("service_reports.revenue.out_of_scope") },
              { key: "contract_overage", label: t("service_reports.revenue.contract_overage") },
              { key: "allowance_overage", label: t("service_reports.revenue.allowance_overage") },
            ]}
          />
        </section>
      ) : null}

      <section className="rounded-lg border border-subtle p-4">
        <h3 className="mb-3 text-14 font-medium text-primary">{t("service_reports.allowances.active")}</h3>
        {data.allowances.active.length === 0 ? (
          <p className="text-13 text-tertiary">{t("service_reports.allowances.none")}</p>
        ) : (
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
                    {allowance.consumed_hours} / {allowance.credited_hours}
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
                    {/* The credit history section 3b named as missing. The API already returned
                        it; this is the consumer. */}
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
        )}
      </section>

      {data.allowances.pending_closure.length > 0 ? (
        <section className="rounded-lg border border-warning-strong/40 bg-warning-subtle/20 p-4">
          <h3 className="mb-1 text-14 font-medium text-primary">{t("service_reports.allowances.pending_closure")}</h3>
          {/* The revised D35: the system informs, a human decides. Nothing is written off here. */}
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

      {drillDown ? (
        <DrillDownModal isOpen onClose={() => setDrillDown(null)} title={drillDown.title} filters={drillDown.filters} />
      ) : null}
    </div>
  );
});
