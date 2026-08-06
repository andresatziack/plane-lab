/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { observer } from "mobx-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import type { IServiceCatalogOption } from "@plane/types";
import { AlertModalCore } from "@plane/ui";
// hooks
import { useServiceCatalog } from "@/hooks/store/use-service-catalog";
// local imports
import type { TCatalogKind } from "./catalog-option-list";

type Props = {
  isOpen: boolean;
  handleClose: () => void;
  workspaceSlug: string;
  kind: TCatalogKind;
  option: IServiceCatalogOption;
};

/**
 * Confirmation for deleting a catalogue option.
 *
 * The list only offers this for non-default options, and the server refuses to delete
 * the default as well, so a stale screen cannot get around it.
 *
 * INHERITED CRITERION: section 5 of the phase brief also requires refusing to delete
 * an option that work logs reference, with an explanatory error. That check belongs
 * to `validate_catalog_delete` on the server and can only be written once work logs
 * exist. Until then the only reason a delete is refused here is the default flag. See
 * docs/worklog/03-worklog-core.md.
 */
export const DeleteCatalogOptionModal = observer(function DeleteCatalogOptionModal(props: Props) {
  const { isOpen, handleClose, workspaceSlug, kind, option } = props;
  // states
  const [isDeleting, setIsDeleting] = useState(false);
  // plane hooks
  const { t } = useTranslation();
  // store hooks
  const { removeHourType, removeBillingType } = useServiceCatalog();

  const handleDelete = async () => {
    setIsDeleting(true);
    try {
      if (kind === "hour-type") await removeHourType(workspaceSlug, option.id);
      else await removeBillingType(workspaceSlug, option.id);

      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t("common.success"),
        message: t("workspace_settings.settings.worklog.toasts.deleted"),
      });
      handleClose();
    } catch (error) {
      const errorDetail = error as { error?: string } | undefined;
      const message =
        errorDetail?.error === "DEFAULT_CANNOT_BE_DELETED"
          ? t("workspace_settings.settings.worklog.errors.default_cannot_be_deleted")
          : (errorDetail?.error ?? t("workspace_settings.settings.worklog.toasts.delete_failed"));

      setToast({ type: TOAST_TYPE.ERROR, title: t("common.errors.default.title"), message });
    }
    setIsDeleting(false);
  };

  return (
    <AlertModalCore
      handleClose={handleClose}
      handleSubmit={handleDelete}
      isSubmitting={isDeleting}
      isOpen={isOpen}
      title={t("workspace_settings.settings.worklog.delete_modal.title")}
      content={t("workspace_settings.settings.worklog.delete_modal.content", { name: option.name })}
    />
  );
});
