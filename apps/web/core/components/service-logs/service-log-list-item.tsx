/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
import { Clock, Pencil, Split, Trash2 } from "lucide-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { Tooltip } from "@plane/ui";
// components
import { ButtonAvatars } from "@/components/dropdowns/member/avatar";
// local imports
import type { TServiceLogBatch } from "@/store/issue/issue-details/service-log.store";
import { hasDistinctClientReading, isSplitBatch, toTimeInputValue } from "./service-log.helpers";

type Props = {
  batch: TServiceLogBatch;
  /** True when the viewer is the author. Section 6: for now, only the author may edit. */
  canModify: boolean;
  onEdit: () => void;
  onDelete: () => void;
};

/**
 * One work log entry in the list. Section 6 of the phase brief.
 *
 * Renders a *batch*, not a row: a submission that crossed a classification boundary
 * became several segments, and they are one thing the technician did.
 *
 * The R11 dual reading is the part that matters most here. Each segment shows the time
 * the technician logged and, when the multiplier is not 1, what the client will see
 * with the reason -- "1h 15min · Fora do expediente · o cliente vê 1,875h". Section 6
 * is explicit about why: without it the technician cannot notice they picked the wrong
 * hour type, and the error surfaces on the invoice instead.
 */
export const ServiceLogListItem = observer(function ServiceLogListItem(props: Props) {
  const { batch, canModify, onEdit, onDelete } = props;
  const { t } = useTranslation();

  const first = batch.segments[0];
  const isSplit = isSplitBatch(batch.segments);

  return (
    <div className="flex flex-col gap-2 rounded-md border border-subtle px-3 py-2.5">
      {/* Two blocks -- the reading of the entry and the badge/action cluster -- that together
          need far more than 390px. They stack while the container is narrow and go back to the
          original single row from `@lg` (512px) up. Keying off the container rather than the
          viewport means the row no longer un-stacks at 768px, where the peek halves to ~320px of
          content; `@container` is declared on `ServiceLogSection`. 512px and not 576px for the
          same reason the totals grid uses `@lg`: inside the side peek the container is exactly
          `viewport / 2 - 64`, so 576px is the container of a 1280px window with nothing to spare
          and Firefox's scrollbar takes ~12px of it -- one window, two layouts. At 512px the text
          column keeps ~250px beside a cluster that wraps internally anyway. */}
      <div className="flex flex-col gap-2 @lg:flex-row @lg:items-start @lg:justify-between">
        <div className="flex min-w-0 flex-col gap-1">
          <div className="text-xs text-custom-text-300 flex flex-wrap items-center gap-2">
            <span className="text-custom-text-200 font-medium">{first.worked_on}</span>
            {first.entry_mode === "interval" && first.start_time && (
              <span className="flex items-center gap-1">
                <Clock className="size-3" />
                {toTimeInputValue(first.start_time)}–
                {toTimeInputValue(batch.segments[batch.segments.length - 1].end_time)}
              </span>
            )}
            {first.author_detail && (
              <span className="flex items-center gap-1">
                <ButtonAvatars showTooltip={false} userIds={first.author} />
                {first.author_detail.display_name}
              </span>
            )}
            {isSplit && (
              <span className="text-custom-text-350 flex items-center gap-1">
                <Split className="size-3" />
                {t("work_item.service_log.segment_count", { count: batch.segments.length })}
              </span>
            )}
          </div>

          {/* One line per segment, each with both readings of R11. */}
          <div className="flex flex-col gap-0.5">
            {batch.segments.map((segment) => (
              <div key={segment.id} className="text-sm flex flex-wrap items-center gap-1.5">
                <span className="text-custom-text-100 font-medium">{segment.logged_hours_display}</span>

                {segment.hour_type_name && (
                  <span className="text-custom-text-300 flex items-center gap-1">
                    <span
                      className="size-2 rounded-full"
                      style={{ backgroundColor: segment.hour_type_color ?? "#60646C" }}
                    />
                    {segment.hour_type_name}
                  </span>
                )}

                {/* Only when the two readings actually differ -- repeating an identical
                    number would be noise. */}
                {hasDistinctClientReading(segment) && (
                  <span className="text-custom-text-350">
                    ·{" "}
                    {t("work_item.service_log.preview.client_sees", {
                      value: segment.equivalent_hours_display,
                    })}
                  </span>
                )}

                {segment.classification_reason && (
                  <span className="text-xs text-custom-text-400">· {segment.classification_reason}</span>
                )}

                {segment.is_hour_type_overridden && (
                  <Tooltip tooltipContent={t("work_item.service_log.badges.overridden")} position="top">
                    <span className="text-custom-text-400 text-[10px] italic line-through">
                      {segment.suggested_hour_type_name}
                    </span>
                  </Tooltip>
                )}
              </div>
            ))}
          </div>

          <p className="text-sm text-custom-text-300 break-words whitespace-pre-wrap">{first.description}</p>
        </div>

        {/* Up to four badges plus the two icon buttons. `flex-wrap` lets them flow onto a second
            line while the container is narrow; `@lg:shrink-0` restores the class this row had
            before at the same width the row itself becomes a row again, so once the two blocks
            share a line the cluster still refuses to be squeezed by the text column. */}
        <div className="flex flex-wrap items-center gap-1 @lg:shrink-0">
          {/* The badge section 6 asks for whenever the route does not charge. It shows
              the billing type name -- "Garantia" or "Cortesia" -- rather than a generic
              label, because R5 requires the two to stay distinguishable: one is a
              quality problem, the other is a discount. */}
          {!first.is_billable && (
            <Tooltip tooltipContent={t("work_item.service_log.badges.not_billable")} position="top">
              <span className="bg-amber-500/15 text-amber-600 rounded px-1.5 py-0.5 text-[11px] font-medium">
                {first.billing_type_name}
              </span>
            </Tooltip>
          )}
          {first.is_billable && first.billing_type_name && (
            <span className="bg-custom-background-80 text-custom-text-350 rounded px-1.5 py-0.5 text-[11px]">
              {first.billing_type_name}
            </span>
          )}

          {/* The value, Phase 6. Rendered only when the payload carries it, which is only for a
              workspace Admin -- R11(b) removes the keys rather than nulling them, so presence
              *is* the permission check and no role lookup happens here.

              `amount_display` is null when the row carries no value at all, and that stays
              blank rather than becoming "R$ 0,00": a row the pool paid for and a row nobody
              could price are different facts, and neither costs zero. The reason for the
              absence is shown beside it. */}
          {first.amount_display && (
            <span className="bg-custom-background-80 text-custom-text-100 rounded px-1.5 py-0.5 text-[11px] font-medium">
              {first.amount_display}
            </span>
          )}

          {first.pricing_failure_reason && first.pricing_failure_reason !== "internal_project_no_client" && (
            <Tooltip
              tooltipContent={t(`work_item.service_log.pricing_failure.${first.pricing_failure_reason}`)}
              position="top"
            >
              <span className="bg-red-500/15 text-red-600 rounded px-1.5 py-0.5 text-[11px] font-medium">
                {t("work_item.service_log.badges.no_price")}
              </span>
            </Tooltip>
          )}

          {first.route_deviation_reason && (
            <Tooltip
              tooltipContent={t(`work_item.service_log.route_deviation.${first.route_deviation_reason}`)}
              position="top"
            >
              <span className="bg-orange-500/15 text-orange-600 rounded px-1.5 py-0.5 text-[11px] font-medium">
                {t("work_item.service_log.badges.billed_as_standalone")}
              </span>
            </Tooltip>
          )}

          {canModify && (
            <>
              <button
                type="button"
                onClick={onEdit}
                aria-label={t("work_item.service_log.edit")}
                className="text-custom-text-300 hover:bg-custom-background-80 hover:text-custom-text-100 rounded p-1"
              >
                <Pencil className="size-3.5" />
              </button>
              <button
                type="button"
                onClick={onDelete}
                aria-label={t("work_item.service_log.delete.title")}
                className="text-custom-text-300 hover:bg-custom-background-80 hover:text-red-500 rounded p-1"
              >
                <Trash2 className="size-3.5" />
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
});
