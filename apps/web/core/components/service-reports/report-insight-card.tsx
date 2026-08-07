/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import React from "react";
import { Loader } from "@plane/ui";
import { cn } from "@plane/utils";

type Props = {
  label: string;
  /** Already formatted by the server. Never format a decimal or a currency in the browser. */
  value?: string;
  hint?: string;
  isLoading?: boolean;
  /** Present when the number can be clicked through to the work logs behind it. */
  onDrillDown?: () => void;
};

/**
 * One headline number.
 *
 * Modelled on `components/analytics/insight-card` and deliberately **not** a copy of it: that
 * one takes an `IAnalyticsResponseFields` and renders `count` as a number, and every figure
 * here is a **pre-formatted string** -- hours in pt-BR, money as `R$ 1.234,56`. Parsing them to
 * render would reintroduce browser-side formatting, which is what §4b keeps to one
 * implementation so a report, an export and a screen cannot disagree about a separator.
 *
 * `value` being `undefined` renders an em dash rather than a zero. A figure the caller was not
 * allowed to have and a figure that is genuinely zero are different facts (R11, D50), and
 * showing both as `0` would hide the first.
 */
export const ReportInsightCard = React.memo(function ReportInsightCard(props: Props) {
  const { label, value, hint, isLoading = false, onDrillDown } = props;

  return (
    <div className="flex flex-col gap-2">
      <div className="text-13 text-tertiary">{label}</div>
      {isLoading ? (
        <Loader.Item height="34px" width="100%" />
      ) : (
        <button
          type="button"
          disabled={!onDrillDown}
          onClick={onDrillDown}
          className={cn(
            "w-fit text-left text-20 font-bold text-primary",
            onDrillDown && "cursor-pointer hover:underline"
          )}
        >
          {value ?? "—"}
        </button>
      )}
      {hint ? <div className="text-11 text-tertiary">{hint}</div> : null}
    </div>
  );
});
