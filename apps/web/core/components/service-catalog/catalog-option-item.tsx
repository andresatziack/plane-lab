/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { ReactNode } from "react";
import { useEffect, useRef, useState } from "react";
import { combine } from "@atlaskit/pragmatic-drag-and-drop/combine";
import type {
  DragLocationHistory,
  DropTargetRecord,
} from "@atlaskit/pragmatic-drag-and-drop/dist/types/internal-types";
import type { ElementDragPayload } from "@atlaskit/pragmatic-drag-and-drop/element/adapter";
import { draggable, dropTargetForElements } from "@atlaskit/pragmatic-drag-and-drop/element/adapter";
import { pointerOutsideOfPreview } from "@atlaskit/pragmatic-drag-and-drop/element/pointer-outside-of-preview";
import { setCustomNativeDragPreview } from "@atlaskit/pragmatic-drag-and-drop/element/set-custom-native-drag-preview";
import { attachInstruction } from "@atlaskit/pragmatic-drag-and-drop-hitbox/tree-item";
import { GripVertical, Pencil, Star, Trash2 } from "lucide-react";
import { observer } from "mobx-react";
import { createRoot } from "react-dom/client";
// plane imports
import { useTranslation } from "@plane/i18n";
import { Badge } from "@plane/propel/badge";
import type { InstructionType, IServiceCatalogOption } from "@plane/types";
import { CustomMenu, DropIndicator, ToggleSwitch } from "@plane/ui";
import { cn } from "@plane/utils";
// local imports
import { getCatalogCanDrop, getCatalogInstruction } from "./catalog.helpers";

type Props = {
  option: IServiceCatalogOption;
  /** The type-specific badge: the multiplier, or the billing route label. */
  detail: ReactNode;
  /** Optional colour swatch, used by hour types. */
  swatchColor?: string;
  isLastChild: boolean;
  handleDrop: (self: DropTargetRecord, source: ElementDragPayload, location: DragLocationHistory) => void;
  handleToggleActive: () => void;
  handleMarkDefault: () => void;
  handleEdit: () => void;
  handleDelete: () => void;
};

export const CatalogOptionItem = observer(function CatalogOptionItem(props: Props) {
  const {
    option,
    detail,
    swatchColor,
    isLastChild,
    handleDrop,
    handleToggleActive,
    handleMarkDefault,
    handleEdit,
    handleDelete,
  } = props;
  // states
  const [isDragging, setIsDragging] = useState(false);
  const [instruction, setInstruction] = useState<InstructionType | undefined>(undefined);
  // refs
  const elementRef = useRef<HTMLDivElement>(null);
  // plane hooks
  const { t } = useTranslation();

  useEffect(() => {
    const element = elementRef.current;
    if (!element) return;

    const initialData = { id: option.id };

    return combine(
      draggable({
        element,
        dragHandle: element,
        getInitialData: () => initialData,
        onDragStart: () => setIsDragging(true),
        onDrop: () => setIsDragging(false),
        onGenerateDragPreview: ({ nativeSetDragImage }) => {
          setCustomNativeDragPreview({
            getOffset: pointerOutsideOfPreview({ x: "0px", y: "0px" }),
            render: ({ container }) => {
              const root = createRoot(container);
              root.render(<div className="rounded-sm bg-surface-1 p-1 pr-2 text-13">{option.name}</div>);
              return () => root.unmount();
            },
            nativeSetDragImage,
          });
        },
      }),
      dropTargetForElements({
        element,
        canDrop: ({ source }) => getCatalogCanDrop(source, option.id),
        getData: ({ input, element }) => {
          // A catalogue is flat, so nesting is always blocked. "reorder-below" is only
          // meaningful on the last row; anywhere else it duplicates the next row's
          // "reorder-above".
          const blockedStates: InstructionType[] = ["make-child"];
          if (!isLastChild) blockedStates.push("reorder-below");

          return attachInstruction(initialData, {
            input,
            element,
            currentLevel: 1,
            indentPerLevel: 0,
            mode: isLastChild ? "last-in-group" : "standard",
            block: blockedStates,
          });
        },
        onDrag: ({ self, source }) => setInstruction(getCatalogInstruction(self, source)),
        onDragLeave: () => setInstruction(undefined),
        onDrop: ({ self, source, location }) => {
          setInstruction(undefined);
          handleDrop(self, source, location);
        },
      })
    );
  }, [option.id, isLastChild, option.name, handleDrop]);

  return (
    <div>
      <DropIndicator isVisible={instruction === "reorder-above"} />
      <div
        ref={elementRef}
        className={cn("group flex items-center justify-between gap-4 border-b border-subtle py-3", {
          "cursor-grabbing bg-layer-1": isDragging,
        })}
      >
        <div className="flex min-w-0 items-center gap-2">
          <GripVertical className="text-quaternary size-4 flex-shrink-0 cursor-grab" />
          {swatchColor && (
            <span
              className="size-3 flex-shrink-0 rounded-full border border-subtle"
              style={{ backgroundColor: swatchColor }}
            />
          )}
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h5 className="text-sm truncate font-medium text-primary">{option.name}</h5>
              {option.is_default && (
                <Badge variant="brand">{t("workspace_settings.settings.worklog.default_badge")}</Badge>
              )}
              {!option.is_active && (
                <Badge variant="neutral">{t("workspace_settings.settings.worklog.inactive")}</Badge>
              )}
            </div>
            <div className="text-xs mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-tertiary">
              {detail}
              {option.description && <span className="truncate">{option.description}</span>}
            </div>
          </div>
        </div>

        <div className="flex flex-shrink-0 items-center gap-3">
          {/* The default option's switch is disabled rather than hidden: the server
              refuses to deactivate it, and a control that silently fails is worse
              than one that is visibly unavailable. */}
          <ToggleSwitch value={option.is_active} onChange={handleToggleActive} size="sm" disabled={option.is_default} />
          <CustomMenu ellipsis placement="bottom-end" closeOnSelect>
            <CustomMenu.MenuItem onClick={handleEdit}>
              <span className="flex items-center gap-2">
                <Pencil className="size-3.5" />
                {t("common.edit")}
              </span>
            </CustomMenu.MenuItem>
            {!option.is_default && option.is_active && (
              <CustomMenu.MenuItem onClick={handleMarkDefault}>
                <span className="flex items-center gap-2">
                  <Star className="size-3.5" />
                  {t("workspace_settings.settings.worklog.mark_default")}
                </span>
              </CustomMenu.MenuItem>
            )}
            {!option.is_default && (
              <CustomMenu.MenuItem onClick={handleDelete}>
                <span className="text-danger flex items-center gap-2">
                  <Trash2 className="size-3.5" />
                  {t("common.delete")}
                </span>
              </CustomMenu.MenuItem>
            )}
          </CustomMenu>
        </div>
      </div>
      {isLastChild && <DropIndicator isVisible={instruction === "reorder-below"} />}
    </div>
  );
});
