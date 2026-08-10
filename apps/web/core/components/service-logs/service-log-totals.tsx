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
   * Two columns until the CONTAINER is wide enough for four, not until the viewport is.
   *
   * `ServiceLogSection` declares `@container`, so `@xl:` here asks about the width this grid
   * actually has. That distinction is the whole point: the side peek is `md:w-[50%]`, so a
   * viewport breakpoint of `md` fired at the exact width where the available space halved, and
   * four columns at a 768px viewport left about 50px of card interior -- narrower than the
   * 390px phone case this set out to fix.
   *
   * The arithmetic behind `@xl` (36rem = 576px): four cards with `gap-2` (8px) leave
   * (576 - 24) / 4 = 138px per card, and `px-3` leaves 114px inside. "1h 52min 30s" at
   * `text-base` needs roughly 96px, and "Horas equivalentes" wraps to two lines and fits. Below
   * that the two-column base gives each card at least ~150px of interior. A grid item also
   * defaults to `min-width: auto`, so it refuses to shrink under its content and pushes text
   * out of the bordered box -- that, not a missing `truncate`, is the reported "Horas
   * equivalent" clipping, and `min-w-0` is what actually fixes it.
   */
  return (
    <div className={`grid grid-cols-2 gap-2 ${cards.length === 4 ? "@xl:grid-cols-4" : "@xl:grid-cols-3"}`}>
      {cards.map((card) => (
        <Tooltip key={card.key} tooltipContent={card.hint} position="top">
          <div className="flex min-w-0 flex-col gap-0.5 rounded-md border border-subtle bg-surface-2 px-3 py-2">
            <span className="text-xs text-custom-text-350 leading-tight">{card.label}</span>
            {/* "R$ 12.345,67" and "1h 52min 30s" both have to survive a narrow card: one size
                step down until the container reaches `@md` (448px, where two columns are already
                ~200px wide), and `break-words` so a value that still does not fit wraps instead
                of escaping the border. */}
            <span className="text-sm text-custom-text-100 @md:text-base font-semibold break-words">{card.value}</span>
          </div>
        </Tooltip>
      ))}
    </div>
  );
});
