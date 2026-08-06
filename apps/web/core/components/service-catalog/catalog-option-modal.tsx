/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import type { IServiceBillingType, IServiceHourType } from "@plane/types";
import { EModalPosition, EModalWidth, ModalCore } from "@plane/ui";
// hooks
import { useServiceCatalog } from "@/hooks/store/use-service-catalog";
// local imports
import type { TCatalogOptionFormValues } from "./catalog-option-form";
import { CatalogOptionForm } from "./catalog-option-form";
import type { TCatalogKind } from "./catalog-option-list";

type Props = {
  isOpen: boolean;
  handleClose: () => void;
  workspaceSlug: string;
  kind: TCatalogKind;
  data?: IServiceHourType | IServiceBillingType;
};

export const CatalogOptionModal = observer(function CatalogOptionModal(props: Props) {
  const { isOpen, handleClose, workspaceSlug, kind, data } = props;
  // plane hooks
  const { t } = useTranslation();
  // store hooks
  const { createHourType, updateHourType, createBillingType, updateBillingType } = useServiceCatalog();

  const isHourType = kind === "hour-type";

  const handleFormSubmit = async (values: TCatalogOptionFormValues) => {
    // Only the fields the chosen catalogue actually has. The multiplier keeps the dot
    // as its decimal separator on the wire: the comma is a display convention, and
    // the value is a decimal string all the way to the database.
    const payload = isHourType
      ? {
          name: values.name.trim(),
          description: values.description,
          multiplier: values.multiplier.trim().replace(",", "."),
          color: values.color,
        }
      : {
          name: values.name.trim(),
          description: values.description,
          billing_route: values.billing_route,
        };

    try {
      if (data) {
        if (isHourType) await updateHourType(workspaceSlug, data.id, payload);
        else await updateBillingType(workspaceSlug, data.id, payload);
        setToast({
          type: TOAST_TYPE.SUCCESS,
          title: t("common.success"),
          message: t("workspace_settings.settings.worklog.toasts.updated"),
        });
      } else {
        if (isHourType) await createHourType(workspaceSlug, payload);
        else await createBillingType(workspaceSlug, payload);
        setToast({
          type: TOAST_TYPE.SUCCESS,
          title: t("common.success"),
          message: t("workspace_settings.settings.worklog.toasts.created"),
        });
      }
      handleClose();
    } catch (error) {
      // Surface the server's own code where there is one. A duplicate name and a
      // refused default need different corrections, and a single generic message
      // would leave the admin guessing which.
      const errorDetail = error as Record<string, string[] | string> | undefined;
      const firstError = errorDetail ? Object.values(errorDetail).flat().find(Boolean) : undefined;
      const knownCodes: Record<string, string> = {
        NAME_ALREADY_EXISTS: "workspace_settings.settings.worklog.errors.name_already_exists",
        NAME_IS_REQUIRED: "workspace_settings.settings.worklog.form.name_required",
        DEFAULT_CANNOT_BE_UNSET: "workspace_settings.settings.worklog.errors.default_cannot_be_unset",
        DEFAULT_CANNOT_BE_DEACTIVATED: "workspace_settings.settings.worklog.errors.default_cannot_be_deactivated",
      };
      const translationKey = typeof firstError === "string" ? knownCodes[firstError] : undefined;

      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("common.errors.default.title"),
        message: translationKey
          ? t(translationKey)
          : typeof firstError === "string"
            ? firstError
            : t("workspace_settings.settings.worklog.toasts.save_failed"),
      });
    }
  };

  return (
    <ModalCore isOpen={isOpen} handleClose={handleClose} position={EModalPosition.TOP} width={EModalWidth.XXL}>
      <CatalogOptionForm kind={kind} data={data} handleClose={handleClose} onSubmit={handleFormSubmit} />
    </ModalCore>
  );
});
