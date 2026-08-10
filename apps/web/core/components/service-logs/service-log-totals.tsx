/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import type { IServiceLogTotals } from "@plane/types";
import { Tooltip } from "@plane/ui";

type Props = {
  totals: IServiceLogTotals;
};

/**
 * The three totals of a work item. Section 6 of the phase brief.
 *
 * "Os totais devem ser claramente rotulados e distintos. Confundi-los é o erro mais
 * provável desta feature." So: three separate cards, three distinct labels, and a
 * tooltip on each explaining what it counts. Never one combined "total".
 *
 * The values arrive already formatted from the server. Nothing here does arithmetic on
 * them -- section 4b requires every total to be a sum of persisted values, and the hour
 * quantities are decimal strings precisely so the browser cannot add them as floats.
 *
 * **The value in reais appears only when the payload contains it**, which happens only for a
 * workspace Admin (R11). This does not check a role: the field is simply **absent** from a
 * Member's payload, because R11(b) makes that a serializer's job rather than an interface's --
 * "esconder na interface e mandar no payload é vazamento". So the correct client-side
 * behaviour is to render what arrived, and a `!== undefined` check is the whole of it.
 */
export const ServiceLogTotals = observer(function ServiceLogTotals(props: Props) {
  const { totals } = props;
  const { t } = useTranslation();

  const cards = [
    {
      key: "logged",
      label: t("work_item.service_log.totals.logged"),
      hint: t("work_item.service_log.totals.logged_hint"),
      value: totals.logged_hours_display,
    },
    {
      key: "equivalent",
      label: t("work_item.service_log.totals.equivalent"),
      hint: t("work_item.service_log.totals.equivalent_hint"),
      value: totals.equivalent_hours_display,
    },
    {
      key: "debited",
      label: t("work_item.service_log.totals.debited"),
      hint: t("work_item.service_log.totals.debited_hint"),
      value: totals.debited_hours_display,
    },
  ];

  if (totals.amount_display !== undefined) {
    cards.push({
      key: "amount",
      label: t("work_item.service_log.totals.amount"),
      hint: t("work_item.service_log.totals.amount_hint"),
      value: totals.amount_display,
    });
  }

  /*
   * Two columns on a phone, the original three or four from `md` up.
   *
   * The peek is `w-full` below `md`, so on a 390px screen four columns left each card about
   * 60px of usable width -- and a grid item defaults to `min-width: auto`, so it refuses to
   * shrink under its content and pushes the text out of the bordered box instead. That is the
   * reported "Horas equivalent" clipping. `min-w-0` on the card is what actually lets the
   * column shrink; the two-column base is what leaves enough room for the label to wrap into.
   *
   * Mirrors `service-reports/consumption-tab.tsx` (`grid-cols-2 ... md:grid-cols-4`) rather
   * than inventing a second convention, and the `md:` classes are exactly the ones that were
   * unconditional before, so desktop geometry is unchanged.
   */
  return (
    <div className={`grid grid-cols-2 gap-2 ${cards.length === 4 ? "md:grid-cols-4" : "md:grid-cols-3"}`}>
      {cards.map((card) => (
        <Tooltip key={card.key} tooltipContent={card.hint} position="top">
          <div className="flex min-w-0 flex-col gap-0.5 rounded-md border border-subtle bg-surface-2 px-3 py-2">
            <span className="text-xs text-custom-text-350 leading-tight">{card.label}</span>
            {/* "R$ 12.345,67" and "1h 52min 30s" both have to survive a narrow card: one size
                step down below `md`, and `break-words` so a value that still does not fit wraps
                instead of escaping the border. */}
            <span className="text-sm text-custom-text-100 md:text-base font-semibold break-words">{card.value}</span>
          </div>
        </Tooltip>
      ))}
    </div>
  );
});
