/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { observer } from "mobx-react";
import { Building2, Pencil, Trash2 } from "lucide-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { Badge } from "@plane/propel/badge";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import type { IServiceClient } from "@plane/types";
import { CustomMenu, ToggleSwitch } from "@plane/ui";
import { formatCNPJ } from "@plane/utils";
// hooks
import { useServiceClient } from "@/hooks/store/use-service-client";
// local imports
import { DeleteServiceClientModal } from "./delete-service-client-modal";
import { ServiceClientModal } from "./service-client-modal";

type ItemProps = {
  workspaceSlug: string;
  serviceClient: IServiceClient;
};

const ServiceClientsListItem = observer(function ServiceClientsListItem(props: ItemProps) {
  const { workspaceSlug, serviceClient } = props;
  // states
  const [isEditModalOpen, setIsEditModalOpen] = useState(false);
  const [isDeleteModalOpen, setIsDeleteModalOpen] = useState(false);
  // plane hooks
  const { t } = useTranslation();
  // store hooks
  const { updateServiceClient, getServiceClientById } = useServiceClient();

  const parent = getServiceClientById(serviceClient.parent);
  const hasLinkedProjects = serviceClient.project_count > 0;

  const handleToggleActive = async () => {
    try {
      await updateServiceClient(workspaceSlug, serviceClient.id, { is_active: !serviceClient.is_active });
    } catch {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("common.errors.default.title"),
        message: t("workspace_settings.settings.service_clients.toasts.save_failed"),
      });
    }
  };

  const handleDeleteClick = () => {
    // A client with projects can only be deactivated. Explain rather than open a
    // dialog whose submit the server would reject.
    if (hasLinkedProjects) {
      setToast({
        type: TOAST_TYPE.INFO,
        title: t("workspace_settings.settings.service_clients.delete_modal.title"),
        message: t("workspace_settings.settings.service_clients.delete_modal.blocked", {
          count: serviceClient.project_count,
        }),
      });
      return;
    }
    setIsDeleteModalOpen(true);
  };

  return (
    <>
      <ServiceClientModal
        isOpen={isEditModalOpen}
        handleClose={() => setIsEditModalOpen(false)}
        workspaceSlug={workspaceSlug}
        data={serviceClient}
      />
      <DeleteServiceClientModal
        isOpen={isDeleteModalOpen}
        handleClose={() => setIsDeleteModalOpen(false)}
        workspaceSlug={workspaceSlug}
        serviceClient={serviceClient}
      />

      <div className="flex items-center justify-between gap-4 border-b border-subtle py-4">
        <div className="flex min-w-0 items-center gap-3">
          <span className="grid size-8 flex-shrink-0 place-items-center rounded bg-surface-2 text-tertiary">
            <Building2 className="size-4" />
          </span>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h5 className="text-sm truncate font-medium text-primary">{serviceClient.name}</h5>
              {!serviceClient.is_active && (
                <Badge variant="neutral">{t("workspace_settings.settings.service_clients.inactive")}</Badge>
              )}
            </div>
            <div className="text-xs mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-tertiary">
              {serviceClient.trade_name && <span className="truncate">{serviceClient.trade_name}</span>}
              {serviceClient.tax_id && <span>{formatCNPJ(serviceClient.tax_id)}</span>}
              <span>
                {t("workspace_settings.settings.service_clients.billing_mode." + serviceClient.default_billing_mode)}
              </span>
              <span>
                {t("workspace_settings.settings.service_clients.project_count", {
                  count: serviceClient.project_count,
                })}
              </span>
              {parent && (
                <span>{t("workspace_settings.settings.service_clients.parent_of", { name: parent.name })}</span>
              )}
            </div>
          </div>
        </div>

        <div className="flex flex-shrink-0 items-center gap-3">
          <ToggleSwitch value={serviceClient.is_active} onChange={handleToggleActive} size="sm" />
          <CustomMenu ellipsis placement="bottom-end" closeOnSelect>
            <CustomMenu.MenuItem onClick={() => setIsEditModalOpen(true)}>
              <span className="flex items-center gap-2">
                <Pencil className="size-3.5" />
                {t("common.edit")}
              </span>
            </CustomMenu.MenuItem>
            <CustomMenu.MenuItem onClick={handleDeleteClick}>
              <span className="text-danger flex items-center gap-2">
                <Trash2 className="size-3.5" />
                {t("common.delete")}
              </span>
            </CustomMenu.MenuItem>
          </CustomMenu>
        </div>
      </div>
    </>
  );
});

export const ServiceClientsList = observer(function ServiceClientsList({ workspaceSlug }: { workspaceSlug: string }) {
  const { serviceClientIds, getServiceClientById } = useServiceClient();

  return (
    <div>
      {serviceClientIds.map((serviceClientId) => {
        const serviceClient = getServiceClientById(serviceClientId);
        if (!serviceClient) return null;
        return (
          <ServiceClientsListItem key={serviceClientId} workspaceSlug={workspaceSlug} serviceClient={serviceClient} />
        );
      })}
    </div>
  );
});
