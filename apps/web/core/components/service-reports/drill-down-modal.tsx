/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import React from "react";
import { useParams } from "next/navigation";
import useSWR from "swr";
import { useTranslation } from "@plane/i18n";
import type { TServiceReportFilters } from "@plane/types";
import { EModalPosition, EModalWidth, Loader, ModalCore } from "@plane/ui";
import { ServiceReportsService } from "@/services/service-reports.service";

const service = new ServiceReportsService();

type Props = {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  /**
   * The descriptor the clicked bucket carried, **passed through untouched**.
   *
   * Not rebuilt from whatever the chart happened to know: the guarantee of D50 is that the
   * total and this list come from the same query, and reassembling the selection here is
   * exactly how they would start to differ.
   */
  filters: TServiceReportFilters;
};

/**
 * The work logs behind one number. Acceptance criterion 8.
 *
 * One modal for every chart in the phase, because there is one drill-down endpoint. The
 * server's own `extra_stats.totals` is shown at the top, so the sum is visible without paging
 * -- which is also how somebody checks by eye that the list really does add up to the figure
 * they clicked.
 *
 * Money columns are simply **absent** from the rows for a non-Admin (R11, D57): the serializer
 * removes them, so there is nothing to hide here. The commercial state -- the settled route and
 * the deviation reason -- *is* shown to a Member, because none of it reveals a value and a
 * technician who cannot see that a client's contract expired cannot warn anybody.
 */
export const DrillDownModal = React.memo(function DrillDownModal(props: Props) {
  const { isOpen, onClose, title, filters } = props;
  const { t } = useTranslation();
  const params = useParams();
  const workspaceSlug = params.workspaceSlug?.toString();

  const { data, isLoading } = useSWR(
    isOpen && workspaceSlug ? `service-report-logs-${workspaceSlug}-${JSON.stringify(filters)}` : null,
    isOpen && workspaceSlug ? () => service.fetchLogs(workspaceSlug, filters) : null
  );

  const rows = (data?.results ?? []) as Record<string, string | null>[];
  const totals = data?.extra_stats?.totals;

  return (
    <ModalCore isOpen={isOpen} handleClose={onClose} position={EModalPosition.TOP} width={EModalWidth.XXXXL}>
      <div className="flex flex-col gap-4 p-5">
        <div className="flex items-baseline justify-between gap-4">
          <h3 className="text-16 font-medium text-primary">{title}</h3>
          {totals ? (
            <div className="flex items-center gap-4 text-13 text-tertiary">
              <span>
                {data?.total_results} {t("service_reports.drill_down.entries")}
              </span>
              <span>{totals.equivalent_hours_display}</span>
              {/* Absent, not zero, for anyone who may not see it. */}
              {totals.amount_display ? <span className="text-primary">{totals.amount_display}</span> : null}
            </div>
          ) : null}
        </div>

        {isLoading ? (
          <Loader className="space-y-2">
            <Loader.Item height="32px" />
            <Loader.Item height="32px" />
            <Loader.Item height="32px" />
          </Loader>
        ) : (
          <div className="max-h-[60vh] overflow-y-auto">
            <table className="w-full text-13">
              <thead className="sticky top-0 bg-surface-1">
                <tr className="border-b border-subtle text-left text-tertiary">
                  <th className="font-normal py-2 pr-3">{t("service_reports.drill_down.worked_on")}</th>
                  <th className="font-normal py-2 pr-3">{t("service_reports.drill_down.author")}</th>
                  <th className="font-normal py-2 pr-3">{t("service_reports.drill_down.description")}</th>
                  <th className="font-normal py-2 pr-3">{t("service_reports.drill_down.hour_type")}</th>
                  <th className="font-normal py-2 pr-3 text-right">
                    {t("service_reports.drill_down.equivalent_hours")}
                  </th>
                  {/* Rendered only when the payload carries it, which is the R11 gate. */}
                  {rows.some((row) => row.amount_display !== undefined) ? (
                    <th className="font-normal py-2 text-right">{t("service_reports.drill_down.amount")}</th>
                  ) : null}
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={String(row.id)} className="border-b border-subtle/60">
                    <td className="py-2 pr-3 text-secondary">{row.worked_on}</td>
                    <td className="py-2 pr-3 text-secondary">
                      {(row.author_detail as unknown as { display_name?: string })?.display_name ?? "—"}
                    </td>
                    <td className="max-w-[24rem] truncate py-2 pr-3 text-secondary">{row.description}</td>
                    <td className="py-2 pr-3 text-secondary">{row.hour_type_name ?? "—"}</td>
                    <td className="py-2 pr-3 text-right text-primary">{row.equivalent_hours_display}</td>
                    {rows.some((candidate) => candidate.amount_display !== undefined) ? (
                      <td className="py-2 text-right text-primary">{row.amount_display ?? "—"}</td>
                    ) : null}
                  </tr>
                ))}
                {rows.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="py-6 text-center text-tertiary">
                      {t("service_reports.drill_down.empty")}
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </ModalCore>
  );
});
