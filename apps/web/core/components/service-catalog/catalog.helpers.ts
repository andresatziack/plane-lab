/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { extractInstruction } from "@atlaskit/pragmatic-drag-and-drop-hitbox/tree-item";
import type { InstructionType, TDropTarget } from "@plane/types";

export type TCatalogDragData = {
  id: string;
};

/**
 * Resolve the drop instruction for a flat, single level list.
 *
 * Simpler than the home widget equivalent on purpose: a catalogue has no groups and
 * no nesting, so "make-child" is never valid and is folded into "reorder-above".
 */
export const getCatalogInstruction = (dropTarget: TDropTarget, source: TDropTarget): InstructionType | undefined => {
  const dropTargetData = dropTarget?.data as TCatalogDragData | undefined;
  const sourceData = source?.data as TCatalogDragData | undefined;

  if (!dropTargetData || !sourceData) return undefined;

  const instruction = extractInstruction(dropTargetData)?.type;

  if (instruction === "instruction-blocked" || instruction === "make-child") return "reorder-above";

  return instruction;
};

/** An option cannot be dropped onto itself. */
export const getCatalogCanDrop = (source: TDropTarget, optionId: string) => {
  const sourceData = source?.data as TCatalogDragData | undefined;
  if (!sourceData) return false;
  return sourceData.id !== optionId;
};

/**
 * Format a decimal multiplier for display without turning it into a number.
 *
 * The API sends it as a string and it stays one: parsing it would put binary
 * floating point into the factor that every hour and every amount is multiplied by.
 * Only the decimal separator is localised.
 */
export const formatMultiplier = (multiplier: string, locale = "pt-BR") =>
  locale.startsWith("pt") ? multiplier.replace(".", ",") : multiplier;
