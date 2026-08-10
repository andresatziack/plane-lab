/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { observer } from "mobx-react";
import useSWR from "swr";
// plane imports
import { useTranslation } from "@plane/i18n";
import type { IServiceLogClientResponse } from "@plane/types";
import { Tooltip } from "@plane/ui";
// services
import { ServiceLogService } from "@/services/service-log.service";

type Props = {
  workspaceSlug: string;
  projectId: string;
  issueId: string;
};

const serviceLogService = new ServiceLogService();

/**
 * The work logs of a work item, as the client sees them. R11, D58, D65. Criteria 9, 11, 12.
 *
 * **A separate component from `ServiceLogSection`, not a mode of it.** The same reasoning the
 * server uses for having a second endpoint and a second serializer: a single component with a
 * role branch has one render path that can reach either shape, and the branch that forgets is
 * the leak. Here there is nothing to forget, because `IServiceLogClientRow` has no
 * `logged_hours` to reference and the fetch cannot return one.
 *
 * What this deliberately does **not** have, so the omissions are visible in one place:
 *
 * - no "add work log" button, no edit, no delete — criterion 13, a client never writes one
 * - no `logged_hours` card — R11(c), see the label note below
 * - no rate, no multiplier, no pricing failure reason
 *
 * **The labels are the part most likely to be got wrong.** R11(c) warns that the client's
 * total will not match the technician's: an hour worked after hours shows as 1,5h. Labelling
 * that "hours worked" turns a contractual rule into an accusation of inflated hours, so the
 * label is "hours consumed" and the tooltip says where the difference comes from.
 *
 * Read-only, so it holds no store state and fetches straight through SWR. There is no write
 * to keep in sync, and a store slice for it would be a second cache of numbers an invoice is
 * built from.
 */
export const ServiceLogClientSection = observer(function ServiceLogClientSection(props: Props) {
  const { workspaceSlug, projectId, issueId } = props;
  const { t } = useTranslation();
  const [isExpanded, setIsExpanded] = useState(true);

  const { data, isLoading } = useSWR<IServiceLogClientResponse>(
    workspaceSlug && projectId && issueId ? `SERVICE_LOGS_CLIENT_${workspaceSlug}_${projectId}_${issueId}` : null,
    workspaceSlug && projectId && issueId
      ? () => serviceLogService.fetchClientServiceLogs(workspaceSlug, projectId, issueId)
      : null,
    { revalidateOnFocus: false }
  );

  const rows = data?.service_logs ?? [];
  const totals = data?.totals;

  // Nothing logged and nothing loading: say nothing rather than show an empty panel. A client
  // opening a request nobody has worked on yet should not be shown a work log section at all.
  if (!isLoading && rows.length === 0) return null;

  const cards = totals
    ? [
        {
          key: "consumed",
          label: t("work_item.service_log.client.consumed"),
          hint: t("work_item.service_log.client.consumed_hint"),
          value: totals.equivalent_hours_display,
        },
        {
          key: "charged",
          label: t("work_item.service_log.client.charged"),
          hint: t("work_item.service_log.client.charged_hint"),
          value: totals.debited_hours_display,
        },
        // Present only when a row on this work item was billed to this client (D58). Rendered
        // on presence, never on a role check, and never as a zero when absent.
        ...(totals.amount_display !== undefined
          ? [
              {
                key: "amount",
                label: t("work_item.service_log.client.amount"),
                hint: t("work_item.service_log.client.amount_hint"),
                value: totals.amount_display,
              },
            ]
          : []),
      ]
    : [];

  return (
    <div className="flex flex-col gap-3 py-3">
      <button
        type="button"
        onClick={() => setIsExpanded((previous) => !previous)}
        className="text-custom-text-200 flex w-fit items-center gap-1 text-body-sm-medium"
      >
        {t("work_item.service_log.client.title")}
        <span className="text-custom-text-350">({rows.length})</span>
      </button>

      {isExpanded && (
        <>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {cards.map((card) => (
              <Tooltip key={card.key} tooltipContent={card.hint}>
                {/* The grid already had a mobile fallback; the cards did not. `min-w-0` lets the
                    column shrink and `break-words` keeps "1h 52min 30s" and "R$ 12.345,67"
                    inside the border at 390px. */}
                <div className="flex min-w-0 flex-col gap-0.5 rounded-md border border-subtle px-3 py-2">
                  <span className="text-xs text-custom-text-350 leading-tight">{card.label}</span>
                  <span className="text-body-sm-medium break-words">{card.value}</span>
                </div>
              </Tooltip>
            ))}
          </div>

          <div className="flex flex-col gap-2">
            {rows.map((row) => (
              <div key={row.id} className="flex flex-col gap-1 rounded-md border border-subtle px-3 py-2">
                {/* Hours on the left, date on the right. Both are short, but the hours reading is
                    now a duration ("1h 52min 30s") rather than "1,875h", so the row wraps instead
                    of cutting one of the two. */}
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="text-body-sm-medium break-words">{row.equivalent_hours_display}</span>
                  <span className="text-xs text-custom-text-350">{row.worked_on}</span>
                </div>

                {row.description && <span className="text-xs text-custom-text-200 break-words">{row.description}</span>}

                <div className="text-xs text-custom-text-350 flex flex-wrap items-center gap-2">
                  {row.author_detail && <span>{row.author_detail.display_name}</span>}
                  {row.hour_type_name && <span>· {row.hour_type_name}</span>}
                  {/* The billing type label, e.g. "Warranty". Paired with the zero below it is
                      what makes a courtesy visible as effort performed and nothing charged,
                      which R11(a) asks for rather than hiding the work. */}
                  {row.billing_type_name && <span>· {row.billing_type_name}</span>}
                  {row.debited_hours === "0.0000" && <span>· {t("work_item.service_log.client.not_charged")}</span>}
                  {/* Why a charge exists despite a contract, e.g. it had expired. Travels with
                      the amount by D65: showing the charge and hiding its reason is the error
                      D58 rejects. */}
                  {row.route_deviation_reason && (
                    <span>
                      ·{" "}
                      {t(`work_item.service_log.client.deviation.${row.route_deviation_reason}`, {
                        defaultValue: row.route_deviation_reason,
                      })}
                    </span>
                  )}
                  {row.amount_display !== undefined && <span>· {row.amount_display}</span>}
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
});
