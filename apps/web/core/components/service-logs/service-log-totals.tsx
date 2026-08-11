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
   * `ServiceLogSection` declares `@container`, so `@lg:` here asks about the width this grid
   * actually has. That distinction is the whole point: the side peek is `md:w-[50%]`, so a
   * viewport breakpoint of `md` fired at the exact width where the available space halved, and
   * four columns at a 768px viewport left about 50px of card interior -- narrower than the
   * 390px phone case this set out to fix.
   *
   * The arithmetic behind `@lg` (32rem = 512px): four cards with `gap-2` (8px) leave
   * (512 - 24) / 4 = 122px per card, and `px-3` leaves 98px inside. "1h 52min 30s" at
   * `text-base` needs roughly 96px, and "Horas equivalentes" wraps to two lines and fits. Below
   * that the two-column base gives each card at least ~150px of interior. A grid item also
   * defaults to `min-width: auto`, so it refuses to shrink under its content and pushes text
   * out of the bordered box -- that, not a missing `truncate`, is the reported "Horas
   * equivalent" clipping, and `min-w-0` is what actually fixes it.
   *
   * Why 512px and not the 576px of `@xl`, which the interior arithmetic would also allow: inside
   * the side peek this container is exactly `viewport / 2 - 64`, so 576px IS the container of a
   * 1280px window -- the most common laptop width -- with nothing to spare, and the peek scrolls
   * in `.vertical-scrollbar`, whose stylesheet hides the webkit scrollbar but leaves Firefox's
   * ~12px in place. The same window would show four columns in one browser and two in the other.
   * 512px clears 1280px by ~50px in both, and clears the full work item page as well (~574px
   * inside `px-9` beside an expanded sidebar). Pick a threshold that no common viewport lands on.
   */
  return (
    <div className={`grid grid-cols-2 gap-2 ${cards.length === 4 ? "@lg:grid-cols-4" : "@lg:grid-cols-3"}`}>
      {cards.map((card) => (
        <Tooltip key={card.key} tooltipContent={card.hint} position="top">
          <div className="flex min-w-0 flex-col gap-0.5 rounded-md border border-subtle bg-surface-2 px-3 py-2">
            <span className="text-xs text-custom-text-350 leading-tight">{card.label}</span>
            {/* "R$ 12.345,67" and "1h 52min 30s" both have to survive a narrow card: one size
                step down until the container reaches `@sm` (384px, where two columns are already
                ~164px of interior each), and `break-words` so a value that still does not fit
                wraps instead of escaping the border. `@sm` rather than `@md` for the same reason
                the grid uses `@lg`: 448px is exactly this container at a 1024px window, so the
                step would flip with a scrollbar. */}
            <span className="text-sm text-custom-text-100 @sm:text-base font-semibold break-words">{card.value}</span>
          </div>
        </Tooltip>
      ))}
    </div>
  );
});
