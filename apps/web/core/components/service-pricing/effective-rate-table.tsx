/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
import { Pencil, Trash2 } from "lucide-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { Badge } from "@plane/propel/badge";
import type { IServiceEffectiveRate } from "@plane/types";
import { Tooltip } from "@plane/ui";

type Props = {
  rows: IServiceEffectiveRate[];
  /** Absent in read-only contexts, e.g. the work log form's preview. */
  onOverride?: (hourTypeId: string, currentRate: string) => void;
  onRemoveOverride?: (hourTypeId: string) => void;
};

/**
 * The rate that actually applies to each hour type. Section 1: "a tela deve exibir a tabela
 * efetiva".
 *
 * **Every number here arrives as a formatted decimal string and is rendered verbatim.** No
 * multiplication happens in the browser -- the whole point of one registered base rate is that
 * `base * multiplier` has exactly one implementation, and it is server side (D16, and section
 * 2b of the master context). Computing the rate here would be the second price table the
 * design exists to prevent.
 *
 * The multiplier is shown beside each rate so the derivation is visible rather than magic: an
 * admin who sees "R$ 300,00" next to "1,5x" can check the arithmetic without opening a
 * calculator, and an admin who sees an overridden row can see the multiplier it *ignores*.
 */
export const ServiceEffectiveRateTable = observer(function ServiceEffectiveRateTable(props: Props) {
  const { rows, onOverride, onRemoveOverride } = props;
  const { t } = useTranslation();

  if (rows.length === 0) {
    // An empty table is a legitimate state -- no vigency is in force on the date being viewed
    // -- and it must not be rendered as a table of zeros. Zero is a price; "no price
    // registered" is not.
    return (
      <p className="text-xs text-custom-text-350 py-2">
        {t("workspace_settings.settings.service_prices.no_table")}
      </p>
    );
  }

  return (
    <div className="flex flex-col divide-y divide-custom-border-200 rounded-md border border-custom-border-200">
      {rows.map((row) => (
        <div key={row.hour_type_id} className="flex items-center justify-between gap-3 px-3 py-2">
          <div className="flex min-w-0 flex-col">
            <span className="truncate text-sm text-custom-text-100">{row.hour_type_name}</span>
            <span className="text-xs text-custom-text-350">
              {t("workspace_settings.settings.service_prices.multiplier", { value: row.multiplier })}
            </span>
          </div>

          <div className="flex shrink-0 items-center gap-2">
            <span className="text-sm font-medium text-custom-text-100">R$ {row.rate}</span>

            {row.is_overridden && (
              <Tooltip
                tooltipContent={t("workspace_settings.settings.service_prices.override_hint")}
                position="top"
              >
                <Badge variant="warning" size="sm">
                  {t("workspace_settings.settings.service_prices.overridden")}
                </Badge>
              </Tooltip>
            )}

            {onOverride && (
              <button
                type="button"
                onClick={() => onOverride(row.hour_type_id, row.rate)}
                className="text-custom-text-350 hover:text-custom-text-100"
                aria-label={t("workspace_settings.settings.service_prices.set_override")}
              >
                <Pencil className="size-3.5" />
              </button>
            )}

            {row.is_overridden && onRemoveOverride && (
              <button
                type="button"
                onClick={() => onRemoveOverride(row.hour_type_id)}
                className="text-custom-text-350 hover:text-red-500"
                aria-label={t("workspace_settings.settings.service_prices.remove_override")}
              >
                <Trash2 className="size-3.5" />
              </button>
            )}
          </div>
        </div>
      ))}
    </div>
  );
});
