/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import React, { useState } from "react";
import { useParams } from "next/navigation";
import useSWR from "swr";
import { useTranslation } from "@plane/i18n";
import type { TServiceReportFilters } from "@plane/types";
import { Loader } from "@plane/ui";
import { ServicePricingService } from "@/services/service-pricing.service";
import { ServiceReportsService } from "@/services/service-reports.service";
import { StackedCompetenceChart } from "./charts";
import { DrillDownModal } from "./drill-down-modal";
import { ReportInsightCard } from "./report-insight-card";
import { ReportToolbar } from "./report-toolbar";
import { toCompetence, useReportFilters } from "./use-report-filters";

const reports = new ServiceReportsService();
const pricing = new ServicePricingService();

/**
 * What to invoice. Section 2. **Workspace Admin only** -- a Member gets a 403 from the endpoint.
 *
 * The consolidation for the selected competency is the document somebody checks *before*
 * issuing an invoice, so it is shown as a table of clients and origins rather than as a chart:
 * the numbers get read one by one and compared against a system somewhere else.
 *
 * **The two pendency lists are never mixed and never summed into the total**, which is the whole
 * reason they are separate keys on the payload:
 *
 * - a **commercial** pendency (D33) *is* revenue -- the work is billable and the contract could
 *   not absorb it -- so its amount is already inside the origins above it. Shown as a lens, with
 *   a label saying so, because adding it as a fifth line would make the total stop adding up;
 * - a **registration** pendency cannot be invoiced at all yet, has no amount, and its absence
 *   from the total is the entire reason it is listed.
 */
export const BillingTab = React.memo(function BillingTab() {
  const { t } = useTranslation();
  const params = useParams();
  const workspaceSlug = params.workspaceSlug?.toString();

  const thisMonth = toCompetence(new Date());
  const { filters, setFilter, setFilters, key } = useReportFilters({
    competence_basis: "worked_on",
    competence_from: thisMonth,
    competence_to: thisMonth,
  });

  const [drillDown, setDrillDown] = useState<{ title: string; filters: TServiceReportFilters } | null>(null);
  const [isExporting, setIsExporting] = useState(false);

  const { data, isLoading, error } = useSWR(
    workspaceSlug ? `service-report-billing-${workspaceSlug}-${key}` : null,
    workspaceSlug ? () => reports.fetchBilling(workspaceSlug, filters) : null
  );

  const handleExport = async () => {
    if (!workspaceSlug) return;

    setIsExporting(true);
    try {
      await pricing.requestExport(workspaceSlug, { provider: "csv", filters } as never);
    } finally {
      setIsExporting(false);
    }
  };

  if (error) {
    // A 403 here is the expected answer for a Member, not a failure to work around.
    return <div className="p-8 text-center text-13 text-tertiary">{t("service_reports.billing.admin_only")}</div>;
  }

  if (isLoading) {
    return (
      <Loader className="space-y-4 p-4">
        <Loader.Item height="80px" />
        <Loader.Item height="240px" />
      </Loader>
    );
  }

  if (!data) return null;

  const clients = (data.consolidation.clients ?? []) as Record<string, never>[];

  return (
    <div className="flex flex-col gap-6 p-4">
      <ReportToolbar
        filters={filters}
        setFilter={setFilter}
        setFilters={setFilters}
        onExport={handleExport}
        isExporting={isExporting}
      />

      <div className="grid grid-cols-2 gap-6 rounded-lg border border-subtle p-4 md:grid-cols-4">
        <ReportInsightCard label={t("service_reports.billing.total")} value={data.consolidation.total_amount_display} />
        <ReportInsightCard label={t("service_reports.billing.competence")} value={data.consolidation.competence} />
        <ReportInsightCard
          label={t("service_reports.totals.equivalent_hours")}
          value={data.totals.equivalent_hours_display}
          onDrillDown={() => setDrillDown({ title: data.consolidation.competence, filters: data.totals.filters })}
        />
        {/* Internal work is reported OUTSIDE revenue, never as a fifth origin worth zero: a
            project with no client is not billable to anyone, so nobody has to act on it. */}
        <ReportInsightCard
          label={t("service_reports.billing.internal_work")}
          value={data.consolidation.internal_work.hours_display}
          hint={t("service_reports.billing.internal_work_hint")}
        />
      </div>

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
        <div className="mt-4 flex flex-wrap gap-2">
          {data.revenue_series.flatMap((point) =>
            Object.entries(point.origins).map(([origin, bucket]) => {
              if (bucket.entries === 0) return null;

              // D56: only the two log origins carry a descriptor. The overage buckets have a
              // `drill_down` instead, because no list of work logs sums to a settled deficit.
              if (!bucket.filters) {
                return (
                  <span
                    key={`${point.competence}-${origin}`}
                    className="rounded border border-subtle px-2 py-1 text-11 text-tertiary"
                    title={t("service_reports.revenue.no_log_list")}
                  >
                    {point.competence} · {t(`service_reports.revenue.${origin}`)}: {bucket.amount_display}
                  </span>
                );
              }

              return (
                <button
                  key={`${point.competence}-${origin}`}
                  type="button"
                  className="rounded border border-subtle px-2 py-1 text-11 text-tertiary hover:bg-surface-2"
                  onClick={() =>
                    setDrillDown({
                      title: `${point.competence} · ${t(`service_reports.revenue.${origin}`)}`,
                      filters: bucket.filters as TServiceReportFilters,
                    })
                  }
                >
                  {point.competence} · {t(`service_reports.revenue.${origin}`)}: {bucket.amount_display}
                </button>
              );
            })
          )}
        </div>
      </section>

      <section className="rounded-lg border border-subtle p-4">
        <h3 className="mb-3 text-14 font-medium text-primary">
          {t("service_reports.billing.consolidation", { competence: data.consolidation.competence })}
        </h3>
        <table className="w-full text-13">
          <thead>
            <tr className="border-b border-subtle text-left text-tertiary">
              <th className="font-normal py-2 pr-3">{t("service_reports.billing.client")}</th>
              <th className="font-normal py-2 pr-3 text-right">{t("service_reports.revenue.standalone")}</th>
              <th className="font-normal py-2 pr-3 text-right">{t("service_reports.revenue.out_of_scope")}</th>
              <th className="font-normal py-2 pr-3 text-right">{t("service_reports.revenue.contract_overage")}</th>
              <th className="font-normal py-2 pr-3 text-right">{t("service_reports.revenue.allowance_overage")}</th>
              <th className="font-normal py-2 text-right">{t("service_reports.billing.total")}</th>
            </tr>
          </thead>
          <tbody>
            {clients.map((client) => {
              const origins = client.origins as unknown as Record<string, { amount_display: string }>;

              return (
                <tr key={client.service_client_id as unknown as string} className="border-b border-subtle/60">
                  <td className="py-2 pr-3 text-secondary">{client.service_client_name as unknown as string}</td>
                  <td className="py-2 pr-3 text-right text-secondary">{origins.standalone_log.amount_display}</td>
                  <td className="py-2 pr-3 text-right text-secondary">{origins.out_of_scope_log.amount_display}</td>
                  <td className="py-2 pr-3 text-right text-secondary">{origins.contract_overage.amount_display}</td>
                  <td className="py-2 pr-3 text-right text-secondary">{origins.allowance_overage.amount_display}</td>
                  <td className="py-2 text-right font-medium text-primary">
                    {client.total_amount_display as unknown as string}
                  </td>
                </tr>
              );
            })}
          </tbody>
          <tfoot>
            <tr>
              <td className="py-2 pr-3 text-13 font-medium text-primary" colSpan={5}>
                {t("service_reports.billing.total")}
              </td>
              <td className="py-2 text-right text-13 font-medium text-primary">
                {data.consolidation.total_amount_display}
              </td>
            </tr>
          </tfoot>
        </table>
      </section>

      {clients.some((client) => (client.commercial_pendencies as unknown as unknown[]).length > 0) ? (
        <section className="rounded-lg border border-warning-strong/40 p-4">
          <h3 className="mb-1 text-14 font-medium text-primary">
            {t("service_reports.billing.commercial_pendencies")}
          </h3>
          {/* A LENS on the revenue above, not a fifth bucket. The amounts are already inside the
              origins, so presenting these as parcels would make the total stop adding up. */}
          <p className="mb-3 text-12 text-tertiary">{t("service_reports.billing.commercial_pendencies_hint")}</p>
          <ul className="space-y-1">
            {clients.flatMap((client) =>
              (
                client.commercial_pendencies as unknown as {
                  reason: string;
                  hours_display: string;
                  amount_display: string;
                }[]
              ).map((pendency) => (
                <li key={`${client.service_client_id}-${pendency.reason}`} className="text-13 text-secondary">
                  {client.service_client_name as unknown as string} ·{" "}
                  {t(`service_reports.deviation.${pendency.reason}`, { defaultValue: pendency.reason })} ·{" "}
                  {pendency.hours_display} · {pendency.amount_display}
                </li>
              ))
            )}
          </ul>
        </section>
      ) : null}

      {clients.some((client) => (client.registration_pendencies as unknown as unknown[]).length > 0) ? (
        <section className="rounded-lg border border-danger-strong/40 p-4">
          <h3 className="mb-1 text-14 font-medium text-primary">
            {t("service_reports.billing.registration_pendencies")}
          </h3>
          {/* NOT revenue. No amount, and its absence from the total is why it is listed. */}
          <p className="mb-3 text-12 text-tertiary">{t("service_reports.billing.registration_pendencies_hint")}</p>
          <ul className="space-y-1">
            {clients.flatMap((client) =>
              (client.registration_pendencies as unknown as { reason: string; hours_display: string }[]).map(
                (pendency) => (
                  <li key={`${client.service_client_id}-${pendency.reason}`} className="text-13 text-secondary">
                    {client.service_client_name as unknown as string} ·{" "}
                    {t(`service_reports.pricing_failure.${pendency.reason}`, { defaultValue: pendency.reason })} ·{" "}
                    {pendency.hours_display}
                  </li>
                )
              )
            )}
          </ul>
        </section>
      ) : null}

      {drillDown ? (
        <DrillDownModal isOpen onClose={() => setDrillDown(null)} title={drillDown.title} filters={drillDown.filters} />
      ) : null}
    </div>
  );
});
