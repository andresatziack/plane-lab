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
import type { IServiceClient } from "@plane/types";
import { AlertModalCore } from "@plane/ui";
// hooks
import { useServiceClient } from "@/hooks/store/use-service-client";

type Props = {
  isOpen: boolean;
  handleClose: () => void;
  workspaceSlug: string;
  serviceClient: IServiceClient;
};

/**
 * Confirmation for deleting a client.
 *
 * Callers only open this for clients with no linked projects; a client that any
 * project points at can only be deactivated, and the list offers that instead.
 * The server enforces the same rule, so a stale screen still cannot delete one.
 */
export const DeleteServiceClientModal = observer(function DeleteServiceClientModal(props: Props) {
  const { isOpen, handleClose, workspaceSlug, serviceClient } = props;
  // states
  const [isDeleting, setIsDeleting] = useState(false);
  // plane hooks
  const { t } = useTranslation();
  // store hooks
  const { removeServiceClient } = useServiceClient();

  const handleDelete = async () => {
    setIsDeleting(true);
    try {
      await removeServiceClient(workspaceSlug, serviceClient.id);
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t("common.success"),
        message: t("workspace_settings.settings.service_clients.toasts.deleted"),
      });
      handleClose();
    } catch (error) {
      const errorDetail = error as { error?: string } | undefined;
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("common.errors.default.title"),
        message: errorDetail?.error ?? t("workspace_settings.settings.service_clients.toasts.delete_failed"),
      });
    }
    setIsDeleting(false);
  };

  return (
    <AlertModalCore
      handleClose={handleClose}
      handleSubmit={handleDelete}
      isSubmitting={isDeleting}
      isOpen={isOpen}
      title={t("workspace_settings.settings.service_clients.delete_modal.title")}
      content={t("workspace_settings.settings.service_clients.delete_modal.content", { name: serviceClient.name })}
    />
  );
});
