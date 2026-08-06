/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { observer } from "mobx-react";
import { Navigate } from "react-router";
import useSWR from "swr";
// plane imports
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
// components
import { NotAuthorizedView } from "@/components/auth-screens/not-authorized-view";
import { PageHead } from "@/components/core/page-title";
import {
  CatalogOptionList,
  CatalogOptionModal,
  DEFAULT_WORKLOG_SETTINGS_SECTION,
  isWorklogSettingsSection,
  WorklogSettingsTabs,
} from "@/components/service-catalog";
import { SettingsContentWrapper } from "@/components/settings/content-wrapper";
import { SettingsHeading } from "@/components/settings/heading";
// hooks
import { useServiceCatalog } from "@/hooks/store/use-service-catalog";
import { useUserPermissions } from "@/hooks/store/user";
import { useWorkspace } from "@/hooks/store/use-workspace";
// local imports
import type { Route } from "./+types/page";
import { WorklogWorkspaceSettingsHeader } from "./header";

function WorklogSettingsPage({ params }: Route.ComponentProps) {
  // states
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);
  // router
  const { workspaceSlug, section } = params;
  // plane hooks
  const { t } = useTranslation();
  // store hooks
  const { workspaceUserInfo, allowPermissions } = useUserPermissions();
  const { currentWorkspace } = useWorkspace();
  const { hourTypes, billingTypes, hourTypeIds, billingTypeIds, fetchHourTypes, fetchBillingTypes } =
    useServiceCatalog();
  // derived values
  const canPerformWorkspaceAdminActions = allowPermissions([EUserPermissions.ADMIN], EUserPermissionsLevel.WORKSPACE);

  // Both catalogues are fetched regardless of the active tab: they are small, the
  // panel switches between them without a round trip, and the counts are needed to
  // decide between the list and the empty state.
  useSWR(
    canPerformWorkspaceAdminActions ? `SERVICE_HOUR_TYPES_${workspaceSlug}` : null,
    canPerformWorkspaceAdminActions ? () => fetchHourTypes(workspaceSlug) : null
  );
  useSWR(
    canPerformWorkspaceAdminActions ? `SERVICE_BILLING_TYPES_${workspaceSlug}` : null,
    canPerformWorkspaceAdminActions ? () => fetchBillingTypes(workspaceSlug) : null
  );

  // An unknown section in the URL redirects rather than rendering nothing, so a stale
  // bookmark from a renamed tab still lands somewhere useful.
  if (!isWorklogSettingsSection(section)) {
    return <Navigate to={`/${workspaceSlug}/settings/worklog/${DEFAULT_WORKLOG_SETTINGS_SECTION}/`} replace />;
  }

  if (workspaceUserInfo && !canPerformWorkspaceAdminActions) {
    return <NotAuthorizedView section="settings" className="h-auto" />;
  }

  const isHourTypes = section === "hour-types";
  const sectionKey = isHourTypes ? "hour_types" : "billing_types";
  // null means "not fetched yet", which is distinct from an empty catalogue.
  const isLoaded = isHourTypes ? Boolean(hourTypes) : Boolean(billingTypes);
  const isEmpty = isLoaded && (isHourTypes ? hourTypeIds.length === 0 : billingTypeIds.length === 0);

  const pageTitle = currentWorkspace?.name
    ? `${currentWorkspace.name} - ${t(`workspace_settings.settings.worklog.${sectionKey}.title`)}`
    : undefined;

  return (
    <SettingsContentWrapper header={<WorklogWorkspaceSettingsHeader />}>
      <PageHead title={pageTitle} />
      <div className="w-full">
        <CatalogOptionModal
          isOpen={isCreateModalOpen}
          handleClose={() => setIsCreateModalOpen(false)}
          workspaceSlug={workspaceSlug}
          kind={isHourTypes ? "hour-type" : "billing-type"}
        />

        <SettingsHeading
          title={t("workspace_settings.settings.worklog.title")}
          description={t("workspace_settings.settings.worklog.description")}
        />

        <WorklogSettingsTabs workspaceSlug={workspaceSlug} activeSection={section} />

        <div className="flex items-start justify-between gap-4">
          <p className="text-sm text-tertiary">{t(`workspace_settings.settings.worklog.${sectionKey}.description`)}</p>
          <Button variant="primary" size="sm" onClick={() => setIsCreateModalOpen(true)} className="flex-shrink-0">
            {t(`workspace_settings.settings.worklog.${sectionKey}.add`)}
          </Button>
        </div>

        {isEmpty ? (
          <div className="mt-6 rounded-md border border-subtle bg-surface-2 p-6 text-center">
            <h6 className="text-sm font-medium text-primary">
              {t(`workspace_settings.settings.worklog.${sectionKey}.empty.title`)}
            </h6>
            <p className="text-xs mt-1 text-tertiary">
              {t(`workspace_settings.settings.worklog.${sectionKey}.empty.description`)}
            </p>
          </div>
        ) : (
          <div className="mt-4">
            <CatalogOptionList workspaceSlug={workspaceSlug} kind={isHourTypes ? "hour-type" : "billing-type"} />
          </div>
        )}
      </div>
    </SettingsContentWrapper>
  );
}

export default observer(WorklogSettingsPage);
