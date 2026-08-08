/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import React, { useMemo } from "react";
import { BarChart } from "@plane/propel/charts/bar-chart";
import { LineChart } from "@plane/propel/charts/line-chart";
import { PieChart } from "@plane/propel/charts/pie-chart";
import type { TServiceDistributionSlice, TServiceReportBucket, TServiceSeriesPoint } from "@plane/types";

/**
 * The charts of sections 1, 1b and 3, built on the propel wrappers.
 *
 * **No new wrapper was added to propel, and one was deliberately refused.** Everything the
 * phase asks for is covered by `bar-chart`, `line-chart` and `pie-chart`. The one thing missing
 * is a dual Y axis -- hours on the left, reais on the right -- and that is not an omission to
 * fix: two units on one axis is a chart that lies. Section 1b wants both readings, and it gets
 * them as sibling charts with their own scales plus a table carrying hours and value on the
 * same row, which is where comparing them is honest.
 *
 * A shared palette, because a colour meaning "Domingos" in one chart and "Garantia" in the next
 * is worse than no colour at all.
 */
const PALETTE = [
  "#3F76FF",
  "#F59E0B",
  "#16A34A",
  "#DC2626",
  "#7C3AED",
  "#0891B2",
  "#DB2777",
  "#65A30D",
  "#EA580C",
  "#4B5563",
];

const colorAt = (index: number) => PALETTE[index % PALETTE.length];

/** A decimal string as a chart-axis number. Never used for display -- see `*_display`. */
const asNumber = (value?: string) => (value === undefined ? 0 : Number(value));

type SeriesProps = {
  data: TServiceSeriesPoint[];
  /** Which hour column to plot. A Guest payload has no `logged_hours`, so callers pick. */
  keys: { key: "logged_hours" | "equivalent_hours" | "debited_hours"; label: string }[];
  onPointClick?: (point: TServiceSeriesPoint) => void;
};

/**
 * Hours per competency, as lines.
 *
 * Lines rather than bars for the hour series: the question is a trend over months ("is this
 * client's consumption growing"), and section 9's low-consumption alert is about exactly that
 * shape. Two series on one axis is legitimate here because both are hours.
 */
export const HoursSeriesChart = React.memo(function HoursSeriesChart(props: SeriesProps) {
  const { data, keys } = props;

  const chartData = useMemo(
    () =>
      data.map((point) => ({
        competence: point.competence,
        ...Object.fromEntries(keys.map(({ key }) => [key, asNumber(point[key])])),
      })),
    [data, keys]
  );

  const lines = useMemo(
    () =>
      keys.map(({ key, label }, index) => ({
        key,
        label,
        dashedLine: false,
        fill: colorAt(index),
        showDot: true,
        smoothCurves: false,
        stroke: colorAt(index),
      })),
    [keys]
  );

  return (
    <LineChart
      className="h-64 w-full"
      data={chartData}
      lines={lines}
      xAxis={{ key: "competence" }}
      yAxis={{ key: keys[0]?.key ?? "equivalent_hours", allowDecimals: true }}
      legend={{ align: "left", verticalAlign: "bottom", layout: "horizontal" }}
      showTooltip
    />
  );
});

type StackedProps = {
  data: { competence: string; [series: string]: string | number }[];
  series: { key: string; label: string }[];
  className?: string;
  /**
   * Called with the competency of the clicked column, when the chart is a control.
   *
   * Passed through to propel's `onBarClick`, which reports a click anywhere in the category's
   * column rather than only on the rectangle -- so a month with two overage hours is as
   * clickable as a month with forty.
   */
  onCompetenceClick?: (competence: string) => void;
};

/**
 * Several quantities per competency, stacked.
 *
 * Used for consumed-versus-overage and for revenue by origin, where the parts genuinely sum to
 * a meaningful whole. **Not** used for anything whose parts overlap: commercial pendency is a
 * lens on revenue rather than a fifth slice of it, so stacking it next to the origins would
 * produce a total that does not add up.
 */
export const StackedCompetenceChart = React.memo(function StackedCompetenceChart(props: StackedProps) {
  const { data, series, className, onCompetenceClick } = props;

  const bars = useMemo(
    () =>
      series.map(({ key, label }, index) => ({
        key,
        label,
        fill: colorAt(index),
        textClassName: "text-white",
        stackId: "competence",
      })),
    [series]
  );

  return (
    <BarChart
      className={className ?? "h-64 w-full"}
      data={data}
      bars={bars}
      xAxis={{ key: "competence" }}
      yAxis={{ key: series[0]?.key ?? "value", allowDecimals: true }}
      legend={{ align: "left", verticalAlign: "bottom", layout: "horizontal" }}
      showTooltip
      onBarClick={
        onCompetenceClick
          ? (datum) => {
              const competence = (datum as { competence?: string }).competence;
              if (competence) onCompetenceClick(competence);
            }
          : undefined
      }
    />
  );
});

type DistributionProps = {
  slices: TServiceDistributionSlice[];
  /** Which `*_name` key labels a slice. */
  nameKey: "hour_type_name" | "billing_type_name" | "author_name" | "project_name" | "service_client_name";
  onSliceClick?: (slice: TServiceDistributionSlice) => void;
};

/**
 * A distribution, as a donut.
 *
 * Plots **equivalent hours**, which is the one hour quantity every role may see (R11) -- so the
 * same component serves an Admin, a technician and, once Phase 8 mounts it, a client.
 */
export const DistributionChart = React.memo(function DistributionChart(props: DistributionProps) {
  const { slices, nameKey } = props;

  const chartData = useMemo(
    () =>
      slices.map((slice) => ({
        name: (slice[nameKey] as string | null) ?? "—",
        value: asNumber(slice.equivalent_hours),
      })),
    [slices, nameKey]
  );

  const cells = useMemo(
    () => chartData.map((datum, index) => ({ key: datum.name, fill: colorAt(index) })),
    [chartData]
  );

  return (
    <PieChart
      className="h-64 w-full"
      data={chartData}
      dataKey="value"
      cells={cells}
      innerRadius="55%"
      outerRadius="80%"
      showLabel={false}
      legend={{ align: "right", verticalAlign: "middle", layout: "vertical" }}
      showTooltip
    />
  );
});

type TableProps = {
  slices: TServiceDistributionSlice[];
  nameKey: DistributionProps["nameKey"];
  nameLabel: string;
  onRowClick?: (slice: TServiceDistributionSlice) => void;
};

/**
 * A distribution as a table, with hours **and** value on the same row.
 *
 * This is the answer to section 1b's "horas e valor em cada faixa" and the reason no dual-axis
 * chart was added: comparing two units is honest in a row and dishonest on a shared axis. Every
 * row is clickable, which is where criterion 8 is satisfied for the distributions.
 *
 * The money column appears only when the payload carries it, so a Member sees a narrower table
 * rather than a column of dashes.
 */
export const DistributionTable = React.memo(function DistributionTable(props: TableProps) {
  const { slices, nameKey, nameLabel, onRowClick } = props;

  const showsMoney = slices.some((slice) => slice.amount_display !== undefined);

  return (
    <table className="w-full text-13">
      <thead>
        <tr className="border-b border-subtle text-left text-tertiary">
          <th className="font-normal py-2 pr-3">{nameLabel}</th>
          <th className="font-normal py-2 pr-3 text-right">Horas equivalentes</th>
          <th className="font-normal py-2 pr-3 text-right">Apontamentos</th>
          {showsMoney ? <th className="font-normal py-2 text-right">Valor</th> : null}
        </tr>
      </thead>
      <tbody>
        {slices.map((slice) => {
          const label = (slice[nameKey] as string | null) ?? "Trabalho interno";

          return (
            <tr
              key={label}
              className={
                onRowClick && slice.filters
                  ? "cursor-pointer border-b border-subtle/60 hover:bg-surface-2"
                  : "border-b border-subtle/60"
              }
              onClick={onRowClick && slice.filters ? () => onRowClick(slice) : undefined}
            >
              <td className="py-2 pr-3 text-secondary">{label}</td>
              <td className="py-2 pr-3 text-right text-primary">{slice.equivalent_hours_display}</td>
              <td className="py-2 pr-3 text-right text-tertiary">{slice.entries}</td>
              {showsMoney ? <td className="py-2 text-right text-primary">{slice.amount_display ?? "—"}</td> : null}
            </tr>
          );
        })}
      </tbody>
    </table>
  );
});

/** Shared by the callers that need a bucket's label for the drill-down modal title. */
export const bucketLabel = (bucket: TServiceReportBucket & { competence?: string }): string => bucket.competence ?? "";
