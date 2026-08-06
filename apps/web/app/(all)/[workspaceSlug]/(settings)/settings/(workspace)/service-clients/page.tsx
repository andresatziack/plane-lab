/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { observer } from "mobx-react";
import useSWR from "swr";
// plane imports
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
// components
import { NotAuthorizedView } from "@/components/auth-screens/not-authorized-view";
import { PageHead } from "@/components/core/page-title";
import { SettingsContentWrapper } from "@/components/settings/content-wrapper";
import { SettingsHeading } from "@/components/settings/heading";
import { ServiceClientModal, ServiceClientsList } from "@/components/service-clients";
// hooks
import { useServiceClient } from "@/hooks/store/use-service-client";
import { useUserPermissions } from "@/hooks/store/user";
import { useWorkspace } from "@/hooks/store/use-workspace";
// local imports
import type { Route } from "./+types/page";
import { ServiceClientsWorkspaceSettingsHeader } from "./header";

function ServiceClientsSettingsPage({ params }: Route.ComponentProps) {
  // states
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);
  // router
  const { workspaceSlug } = params;
  // plane hooks
  const { t } = useTranslation();
  // mobx store
  const { workspaceUserInfo, allowPermissions } = useUserPermissions();
  const { serviceClients, serviceClientIds, fetchServiceClients } = useServiceClient();
  const { currentWorkspace } = useWorkspace();
  // derived values
  const canPerformWorkspaceAdminActions = allowPermissions([EUserPermissions.ADMIN], EUserPermissionsLevel.WORKSPACE);

  useSWR(
    canPerformWorkspaceAdminActions ? `SERVICE_CLIENTS_LIST_${workspaceSlug}` : null,
    canPerformWorkspaceAdminActions ? () => fetchServiceClients(workspaceSlug) : null
  );

  const pageTitle = currentWorkspace?.name
    ? `${currentWorkspace.name} - ${t("workspace_settings.settings.service_clients.title")}`
    : undefined;

  if (workspaceUserInfo && !canPerformWorkspaceAdminActions) {
    return <NotAuthorizedView section="settings" className="h-auto" />;
  }

  return (
    <SettingsContentWrapper header={<ServiceClientsWorkspaceSettingsHeader />}>
      <PageHead title={pageTitle} />
      <div className="w-full">
        <ServiceClientModal
          isOpen={isCreateModalOpen}
          handleClose={() => setIsCreateModalOpen(false)}
          workspaceSlug={workspaceSlug}
        />
        <SettingsHeading
          title={t("workspace_settings.settings.service_clients.title")}
          description={t("workspace_settings.settings.service_clients.description")}
          control={
            <Button variant="primary" size="lg" onClick={() => setIsCreateModalOpen(true)}>
              {t("workspace_settings.settings.service_clients.add")}
            </Button>
          }
        />
        {/* serviceClients is null until fetched, which is distinct from an empty workspace */}
        {serviceClients && serviceClientIds.length === 0 ? (
          <div className="mt-6 rounded-md border border-subtle bg-surface-2 p-6 text-center">
            <h6 className="text-sm font-medium text-primary">
              {t("workspace_settings.settings.service_clients.empty.title")}
            </h6>
            <p className="text-xs mt-1 text-tertiary">
              {t("workspace_settings.settings.service_clients.empty.description")}
            </p>
          </div>
        ) : (
          <div className="mt-4">
            <ServiceClientsList workspaceSlug={workspaceSlug} />
          </div>
        )}
      </div>
    </SettingsContentWrapper>
  );
}

export default observer(ServiceClientsSettingsPage);
