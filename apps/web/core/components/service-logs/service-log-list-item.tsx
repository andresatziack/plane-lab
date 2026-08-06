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
    <div className="border-custom-border-200 flex flex-col gap-2 rounded-md border px-3 py-2.5">
      <div className="flex items-start justify-between gap-2">
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
                    <span className="bg-custom-background-80 text-custom-text-350 rounded px-1 py-0.5 text-[10px]">
                      {segment.suggested_hour_type_name}
                    </span>
                  </Tooltip>
                )}
              </div>
            ))}
          </div>

          <p className="text-sm text-custom-text-300 break-words whitespace-pre-wrap">{first.description}</p>
        </div>

        <div className="flex shrink-0 items-center gap-1">
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
