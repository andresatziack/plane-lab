/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import * as React from "react";

import type { ISvgIcons } from "./type";

/**
 * Three bars, drawn as an outline.
 *
 * **Outline, and stroked at 1.25, because that is what every other navigation icon is.** This
 * used to be three solid filled shapes, and beside `intake`, `cycle`, `page` and the rest -- all
 * `fill="none" stroke="currentColor" strokeWidth="1.25"` -- it read as heavier and from a
 * different family. The sidebar's client consumption item is the only place it is used, so it was
 * the one item in the list that looked out of place.
 *
 * The geometry is deliberately the same three bars at the same heights and gaps: this is a change
 * of weight, not of symbol.
 */
export function BarIcon({ className = "", ...rest }: ISvgIcons) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.25"
      strokeLinecap="round"
      strokeLinejoin="round"
      xmlns="http://www.w3.org/2000/svg"
      {...rest}
    >
      <rect x="1.5" y="12" width="5" height="10.5" rx="1.5" />
      <rect x="9.5" y="1.5" width="5" height="21" rx="1.5" />
      <rect x="17.5" y="7.75" width="5" height="14.75" rx="1.5" />
    </svg>
  );
}
