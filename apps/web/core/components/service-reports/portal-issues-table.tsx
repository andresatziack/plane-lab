/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import React, { useEffect, useMemo, useState } from "react";
import { X } from "lucide-react";
import useSWR from "swr";
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import type { TServiceReportFilters } from "@plane/types";
import { Loader } from "@plane/ui";
import { ServiceReportsService } from "@/services/service-reports.service";

const reports = new ServiceReportsService();

/** A page the client can scan without scrolling for a minute. Not the API's 1000 default. */
const PER_PAGE = 10;

type Props = {
  workspaceSlug: string;
  projectId: string;
  /** The dashboard's window. Passed verbatim so the table and the charts agree (D50). */
  filters: Partial<TServiceReportFilters>;
  /** The competency a chart click selected, or `null` for the whole window. */
  competence: string | null;
  onClearCompetence: () => void;
};

/**
 * The **chamados** behind the client's numbers. Phase 10, item 7.
 *
 * The dashboard used to end with the allowances, so a client could read that 37,5h had been
 * consumed and had no way to ask *which tickets*. This is that list, and every ticket links to
 * itself.
 *
 * **A table at the end of the page, not a modal.** Decided with the user: this is the section a
 * client actually wants, and a modal would put the primary answer behind a gesture nobody
 * discovers. The chart click still exists and it *narrows* this table rather than revealing it,
 * so the information is reachable without knowing the gesture and the gesture is a refinement.
 *
 * **Its own component rather than more of `PortalConsumptionDashboard`**, for one concrete
 * reason: it is the only section of the portal with its own server round trip. Paging and
 * filtering re-fetch *this* and must not re-fetch the dashboard's totals, statement and
 * allowances -- which is what a single SWR key over both would do on every click of "next".
 *
 * No money column, and that is a server property rather than a rendering choice: the rows carry
 * no amount for any role. See `issue_row` and D58 -- a ticket half absorbed by an allowance and
 * half billed has no single amount corresponding to an invoice line.
 */
export const PortalIssuesTable = React.memo(function PortalIssuesTable(props: Props) {
  const { workspaceSlug, projectId, filters, competence, onClearCompetence } = props;
  const { t } = useTranslation();
  const [cursor, setCursor] = useState<string | undefined>(undefined);

  // The selected competency replaces the window on both ends, because a bar identifies exactly
  // one month. Spread first so a `competence_from` already in `filters` cannot win.
  const query = useMemo(
    () => (competence ? { ...filters, competence_from: competence, competence_to: competence } : filters),
    [filters, competence]
  );

  // **Resetting the cursor is not tidiness, it is correctness.** The cursor encodes an offset,
  // so keeping page 3 while narrowing four pages down to one asks the server for rows past the
  // end -- and the client's click on a bar would answer with an empty table.
  useEffect(() => {
    setCursor(undefined);
  }, [competence]);

  const { data, isLoading } = useSWR(
    workspaceSlug && projectId
      ? `service-report-portal-issues-${workspaceSlug}-${projectId}-${competence ?? "all"}-${cursor ?? "first"}`
      : null,
    workspaceSlug && projectId
      ? () => reports.fetchPortalIssues(workspaceSlug, projectId, query, { cursor, per_page: PER_PAGE })
      : null,
    // The rows are a report, not live state: refetching them because a tab regained focus would
    // move the page under a reader's cursor for no new information.
    { revalidateOnFocus: false }
  );

  const rows = data?.results ?? [];
  const totals = data?.extra_stats?.totals;

  return (
    <section className="rounded-lg border border-subtle p-4">
      <div className="mb-1 flex flex-wrap items-center gap-2">
        <h3 className="text-14 font-medium text-primary">{t("service_reports.portal.tickets.title")}</h3>
        {competence ? (
          <button
            type="button"
            onClick={onClearCompetence}
            className="border-accent/40 bg-accent/10 text-accent hover:bg-accent/20 flex items-center gap-1 rounded border px-2 py-0.5 text-11"
          >
            {competence}
            <X className="h-3 w-3" />
          </button>
        ) : null}
        {competence ? (
          <button type="button" onClick={onClearCompetence} className="text-11 text-tertiary hover:underline">
            {t("service_reports.portal.tickets.clear_filter")}
          </button>
        ) : null}
      </div>

      {/* The header states the filtered count and the filtered hours, both from `extra_stats` --
          the same descriptor as the rows, so a narrowed table cannot report the window's total. */}
      <p className="mb-3 text-12 text-tertiary">
        {totals
          ? t("service_reports.portal.tickets.summary", {
              count: totals.issues,
              hours: totals.equivalent_hours_display,
            })
          : t("service_reports.portal.tickets.hint")}
      </p>

      {isLoading && rows.length === 0 ? (
        <Loader className="space-y-2">
          <Loader.Item height="32px" />
          <Loader.Item height="32px" />
          <Loader.Item height="32px" />
        </Loader>
      ) : (
        <table className="w-full text-13">
          <thead>
            <tr className="border-b border-subtle text-left text-tertiary">
              <th className="font-normal py-2 pr-3">{t("service_reports.portal.tickets.ticket")}</th>
              <th className="font-normal py-2 pr-3">{t("service_reports.portal.tickets.state")}</th>
              <th className="font-normal py-2 pr-3">{t("service_reports.portal.tickets.last_worked_on")}</th>
              <th className="font-normal py-2 pr-3 text-right">{t("service_reports.totals.entries")}</th>
              <th className="font-normal py-2 pr-3 text-right">{t("service_reports.portal.consumed_hours")}</th>
              <th className="font-normal py-2 text-right">{t("service_reports.totals.debited_hours")}</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={6} className="py-6 text-center text-12 text-tertiary">
                  {t("service_reports.portal.tickets.empty")}
                </td>
              </tr>
            ) : (
              rows.map((row) => {
                // The readable key, which is what a client recognises and can paste to support.
                const reference = `${row.project_identifier}-${row.sequence_id}`;

                return (
                  <tr key={row.issue_id} className="border-b border-subtle/60 hover:bg-surface-2">
                    <td className="py-2 pr-3">
                      <a
                        href={`/${workspaceSlug}/browse/${reference}/`}
                        className="text-accent hover:underline"
                        title={row.name}
                      >
                        <span className="font-medium">{reference}</span>
                        <span className="ml-2 text-secondary">{row.name}</span>
                      </a>
                    </td>
                    <td className="py-2 pr-3 text-tertiary">{row.state_name ?? "—"}</td>
                    <td className="py-2 pr-3 text-tertiary">
                      {row.last_worked_on
                        ? new Date(`${row.last_worked_on}T00:00:00`).toLocaleDateString("pt-BR")
                        : "—"}
                    </td>
                    <td className="py-2 pr-3 text-right text-tertiary">{row.entries}</td>
                    <td className="py-2 pr-3 text-right text-primary">{row.equivalent_hours_display}</td>
                    <td className="py-2 text-right text-secondary">{row.debited_hours_display}</td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      )}

      {data && (data.prev_page_results || data.next_page_results) ? (
        <div className="mt-3 flex items-center justify-end gap-2">
          <span className="mr-auto text-11 text-tertiary">
            {t("service_reports.portal.tickets.page_of", {
              shown: rows.length,
              total: data.total_count,
            })}
          </span>
          {/* propel's Button, `secondary`, matching `report-toolbar` and `attention-tab` in this
              same folder. `@plane/ui`'s Button and its `neutral-primary` also typecheck, which is
              the trap: it would have been the only control in the feature from a different
              family, and the icon of PR #29 was exactly that kind of drift. */}
          <Button
            variant="secondary"
            size="base"
            disabled={!data.prev_page_results}
            onClick={() => setCursor(data.prev_cursor)}
          >
            {t("service_reports.portal.tickets.previous")}
          </Button>
          <Button
            variant="secondary"
            size="base"
            disabled={!data.next_page_results}
            onClick={() => setCursor(data.next_cursor)}
          >
            {t("service_reports.portal.tickets.next")}
          </Button>
        </div>
      ) : null}
    </section>
  );
});
