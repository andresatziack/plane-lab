/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import type {
  DragLocationHistory,
  DropTargetRecord,
  ElementDragPayload,
} from "@atlaskit/pragmatic-drag-and-drop/dist/types/internal-types";
import { observer } from "mobx-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { Badge } from "@plane/propel/badge";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import type { IServiceBillingType, IServiceCatalogOption, IServiceHourType } from "@plane/types";
// hooks
import { useServiceCatalog } from "@/hooks/store/use-service-catalog";
// local imports
import type { TCatalogDragData } from "./catalog.helpers";
import { formatMultiplier, getCatalogInstruction } from "./catalog.helpers";
import { CatalogOptionItem } from "./catalog-option-item";
import { CatalogOptionModal } from "./catalog-option-modal";
import { DeleteCatalogOptionModal } from "./delete-catalog-option-modal";

export type TCatalogKind = "hour-type" | "billing-type";

/** Either catalogue's concrete option. The modal needs the type-specific fields. */
type TCatalogOption = IServiceHourType | IServiceBillingType;

type Props = {
  workspaceSlug: string;
  kind: TCatalogKind;
};

export const CatalogOptionList = observer(function CatalogOptionList({ workspaceSlug, kind }: Props) {
  // states
  const [editingOption, setEditingOption] = useState<TCatalogOption | null>(null);
  const [deletingOption, setDeletingOption] = useState<TCatalogOption | null>(null);
  // plane hooks
  const { t } = useTranslation();
  // store hooks
  const {
    hourTypeIds,
    billingTypeIds,
    getHourTypeById,
    getBillingTypeById,
    updateHourType,
    updateBillingType,
    markHourTypeDefault,
    markBillingTypeDefault,
    reorderHourType,
    reorderBillingType,
  } = useServiceCatalog();

  const isHourType = kind === "hour-type";
  const optionIds = isHourType ? hourTypeIds : billingTypeIds;
  const getOptionById = (id: string): TCatalogOption | null =>
    isHourType ? getHourTypeById(id) : getBillingTypeById(id);

  const notifyFailure = (messageKey: string) =>
    setToast({
      type: TOAST_TYPE.ERROR,
      title: t("common.errors.default.title"),
      message: t(messageKey),
    });

  const handleToggleActive = async (option: IServiceCatalogOption) => {
    try {
      const payload = { is_active: !option.is_active };
      if (isHourType) await updateHourType(workspaceSlug, option.id, payload);
      else await updateBillingType(workspaceSlug, option.id, payload);
    } catch {
      notifyFailure("workspace_settings.settings.worklog.toasts.save_failed");
    }
  };

  const handleMarkDefault = async (option: IServiceCatalogOption) => {
    try {
      if (isHourType) await markHourTypeDefault(workspaceSlug, option.id);
      else await markBillingTypeDefault(workspaceSlug, option.id);
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t("common.success"),
        message: t("workspace_settings.settings.worklog.toasts.default_changed"),
      });
    } catch {
      notifyFailure("workspace_settings.settings.worklog.toasts.save_failed");
    }
  };

  const handleDrop = (self: DropTargetRecord, source: ElementDragPayload, location: DragLocationHistory) => {
    const dropTargets = location?.current?.dropTargets ?? [];
    if (dropTargets.length === 0) return;

    const dropTarget = dropTargets[0];
    const dropTargetData = dropTarget?.data as TCatalogDragData | undefined;
    const sourceData = source?.data as TCatalogDragData | undefined;

    if (!dropTargetData?.id || !sourceData?.id) return;

    const instruction = getCatalogInstruction(dropTarget, source);
    if (instruction !== "reorder-above" && instruction !== "reorder-below") return;

    const reorder = isHourType ? reorderHourType : reorderBillingType;

    reorder(workspaceSlug, sourceData.id, dropTargetData.id, instruction).catch(() =>
      notifyFailure("workspace_settings.settings.worklog.toasts.reorder_failed")
    );
  };

  /** The badge that distinguishes one catalogue from the other. */
  const renderDetail = (option: IServiceCatalogOption) => {
    if (isHourType) {
      const hourType = option as IServiceHourType;
      return (
        <Badge variant="neutral">
          {t("workspace_settings.settings.worklog.hour_types.multiplier_badge", {
            value: formatMultiplier(hourType.multiplier),
          })}
        </Badge>
      );
    }

    const billingType = option as IServiceBillingType;
    return (
      <Badge variant="neutral">
        {t(`workspace_settings.settings.worklog.billing_routes.${billingType.billing_route}`)}
      </Badge>
    );
  };

  return (
    <div>
      {editingOption && (
        <CatalogOptionModal
          isOpen
          handleClose={() => setEditingOption(null)}
          workspaceSlug={workspaceSlug}
          kind={kind}
          data={editingOption}
        />
      )}
      {deletingOption && (
        <DeleteCatalogOptionModal
          isOpen
          handleClose={() => setDeletingOption(null)}
          workspaceSlug={workspaceSlug}
          kind={kind}
          option={deletingOption}
        />
      )}

      {optionIds.map((optionId, index) => {
        const option = getOptionById(optionId);
        if (!option) return null;

        return (
          <CatalogOptionItem
            key={optionId}
            option={option}
            detail={renderDetail(option)}
            swatchColor={isHourType ? (option as IServiceHourType).color : undefined}
            isLastChild={index === optionIds.length - 1}
            handleDrop={handleDrop}
            handleToggleActive={() => handleToggleActive(option)}
            handleMarkDefault={() => handleMarkDefault(option)}
            handleEdit={() => setEditingOption(option)}
            handleDelete={() => setDeletingOption(option)}
          />
        );
      })}
    </div>
  );
});
