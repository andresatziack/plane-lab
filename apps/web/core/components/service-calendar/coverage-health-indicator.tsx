/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
import { AlertTriangle, CheckCircle2, Layers } from "lucide-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import type { IServiceCoverageReport, TServiceDayScope } from "@plane/types";

type Props = {
  coverage: IServiceCoverageReport | null;
};

/**
 * Whether the classification configuration can classify every instant of the week.
 *
 * **This exists because the coverage rule is a ratchet, not an absolute.** A complete set
 * may never become incomplete, but a set that is *already* incomplete is allowed to be
 * saved -- otherwise an admin could never create the first window of a hand-built
 * workspace, nor repair a broken one. So "incomplete" is a state a workspace can genuinely
 * sit in.
 *
 * And an incomplete state that were invisible here would surface nowhere else except as a
 * blank hour type in the work log form, where nobody would connect the two. So this names
 * the day scopes that are short **and the exact ranges** that are missing: "incomplete" on
 * its own gives an admin nothing to act on.
 *
 * The overlap list is the other half. Two windows sharing a day scope and a priority are
 * something the engine cannot decide between; the server refuses to create them, but a
 * configuration that predates the rule can still contain them.
 */
export const CoverageHealthIndicator = observer(function CoverageHealthIndicator({ coverage }: Props) {
  const { t } = useTranslation();

  // null means "not fetched yet", which must not render as a problem.
  if (!coverage) return null;

  const gapScopes = Object.keys(coverage.gaps) as TServiceDayScope[];
  const hasOverlaps = coverage.overlaps.length > 0;

  if (coverage.is_complete && !hasOverlaps) {
    return (
      <div className="mt-4 flex items-start gap-2 rounded-md border border-subtle bg-surface-2 px-3 py-2">
        <CheckCircle2 className="text-green-600 mt-0.5 size-4 shrink-0" />
        <div className="flex flex-col">
          <span className="text-sm font-medium text-primary">
            {t("workspace_settings.settings.worklog.windows.health.complete")}
          </span>
          <span className="text-xs text-tertiary">
            {t("workspace_settings.settings.worklog.windows.health.complete_description", {
              count: coverage.window_count,
            })}
          </span>
        </div>
      </div>
    );
  }

  return (
    <div className="border-amber-500/40 bg-amber-500/10 mt-4 flex flex-col gap-2 rounded-md border px-3 py-2.5">
      <div className="flex items-start gap-2">
        <AlertTriangle className="text-amber-600 mt-0.5 size-4 shrink-0" />
        <div className="flex flex-col">
          <span className="text-sm text-amber-700 font-medium">
            {t("workspace_settings.settings.worklog.windows.health.incomplete")}
          </span>
          <span className="text-xs text-tertiary">
            {/* Says what happens as a consequence, not just that something is wrong: work
                logged in an uncovered range comes back with no suggested hour type and the
                technician has to choose. That is the symptom an admin will otherwise be
                asked about without knowing why. */}
            {t("workspace_settings.settings.worklog.windows.health.incomplete_description")}
          </span>
        </div>
      </div>

      {gapScopes.length > 0 && (
        <ul className="ml-6 flex flex-col gap-0.5">
          {gapScopes.map((scope) => (
            <li key={scope} className="text-xs text-primary">
              <span className="font-medium">{t(`workspace_settings.settings.worklog.windows.day_scope.${scope}`)}</span>
              {": "}
              <span className="text-tertiary">{(coverage.gaps[scope] ?? []).join(", ")}</span>
            </li>
          ))}
        </ul>
      )}

      {hasOverlaps && (
        <div className="border-amber-500/30 ml-6 flex flex-col gap-0.5 border-t pt-1.5">
          <span className="text-xs text-amber-700 flex items-center gap-1 font-medium">
            <Layers className="size-3" />
            {t("workspace_settings.settings.worklog.windows.health.overlaps")}
          </span>
          {/* Keyed by the conflicting pair itself rather than by index: the rendered window
              strings are what identifies an overlap, and the server emits each pair in a
              deterministic order (windows are loaded ordered by priority, day scope and
              start), so a key stays stable when an unrelated overlap ahead of it is
              repaired. */}
          {coverage.overlaps.map((overlap) => (
            <span
              key={`${overlap.day_scope}-${overlap.priority}-${overlap.windows.join("|")}`}
              className="text-xs text-tertiary"
            >
              {overlap.windows.join(" / ")} — {overlap.hour_types.join(" / ")}
            </span>
          ))}
        </div>
      )}
    </div>
  );
});
