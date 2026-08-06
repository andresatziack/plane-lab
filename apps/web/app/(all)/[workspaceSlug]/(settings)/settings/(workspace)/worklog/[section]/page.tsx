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
  CoverageHealthIndicator,
  HolidayCsvImportModal,
  HolidayList,
  HolidayModal,
  WindowList,
} from "@/components/service-calendar";
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

/** Maps each section to the i18n namespace holding its title, description and add label. */
const SECTION_I18N_KEY = {
  "hour-types": "hour_types",
  "billing-types": "billing_types",
  holidays: "holidays",
  "classification-windows": "windows",
} as const;

function WorklogSettingsPage({ params }: Route.ComponentProps) {
  // states
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);
  const [isImportModalOpen, setIsImportModalOpen] = useState(false);
  // router
  const { workspaceSlug, section } = params;
  // plane hooks
  const { t } = useTranslation();
  // store hooks
  const { workspaceUserInfo, allowPermissions } = useUserPermissions();
  const { currentWorkspace } = useWorkspace();
  const {
    hourTypes,
    billingTypes,
    hourTypeIds,
    billingTypeIds,
    holidays,
    classificationWindows,
    coverage,
    fetchHourTypes,
    fetchBillingTypes,
    fetchHolidays,
    fetchClassificationWindows,
    fetchCoverage,
  } = useServiceCatalog();
  // derived values
  const canPerformWorkspaceAdminActions = allowPermissions([EUserPermissions.ADMIN], EUserPermissionsLevel.WORKSPACE);

  // Everything is fetched regardless of the active tab: the payloads are small, the panel
  // switches between four sections without a round trip, and the counts are needed to decide
  // between a list and an empty state.
  //
  // The hour types matter beyond their own tab -- the window form's dropdown is built from
  // them, and the window list shows their names, colours and priorities.
  useSWR(
    canPerformWorkspaceAdminActions ? `SERVICE_HOUR_TYPES_${workspaceSlug}` : null,
    canPerformWorkspaceAdminActions ? () => fetchHourTypes(workspaceSlug) : null
  );
  useSWR(
    canPerformWorkspaceAdminActions ? `SERVICE_BILLING_TYPES_${workspaceSlug}` : null,
    canPerformWorkspaceAdminActions ? () => fetchBillingTypes(workspaceSlug) : null
  );
  useSWR(
    canPerformWorkspaceAdminActions ? `SERVICE_HOLIDAYS_${workspaceSlug}` : null,
    canPerformWorkspaceAdminActions ? () => fetchHolidays(workspaceSlug) : null
  );
  useSWR(
    canPerformWorkspaceAdminActions ? `SERVICE_WINDOWS_${workspaceSlug}` : null,
    canPerformWorkspaceAdminActions ? () => fetchClassificationWindows(workspaceSlug) : null
  );
  // The health report is fetched once here and then refreshed by the store after every
  // window write, since any write can change it -- including one the server refused.
  useSWR(
    canPerformWorkspaceAdminActions ? `SERVICE_WINDOW_COVERAGE_${workspaceSlug}` : null,
    canPerformWorkspaceAdminActions ? () => fetchCoverage(workspaceSlug) : null
  );

  // An unknown section in the URL redirects rather than rendering nothing, so a stale
  // bookmark from a renamed tab still lands somewhere useful.
  if (!isWorklogSettingsSection(section)) {
    return <Navigate to={`/${workspaceSlug}/settings/worklog/${DEFAULT_WORKLOG_SETTINGS_SECTION}/`} replace />;
  }

  if (workspaceUserInfo && !canPerformWorkspaceAdminActions) {
    return <NotAuthorizedView section="settings" className="h-auto" />;
  }

  const sectionKey = SECTION_I18N_KEY[section];
  const isCatalogueSection = section === "hour-types" || section === "billing-types";
  const isHourTypes = section === "hour-types";
  const isHolidays = section === "holidays";
  const isWindows = section === "classification-windows";

  // null means "not fetched yet", which is distinct from an empty collection.
  const isLoaded = {
    "hour-types": Boolean(hourTypes),
    "billing-types": Boolean(billingTypes),
    holidays: Boolean(holidays),
    "classification-windows": Boolean(classificationWindows),
  }[section];

  const isEmpty =
    isLoaded &&
    {
      "hour-types": hourTypeIds.length === 0,
      "billing-types": billingTypeIds.length === 0,
      // The holiday section is never "empty" in the blocking sense: the annual view is worth
      // showing even with nothing registered, because it is how an admin confirms that.
      holidays: false,
      // Same for the windows: the per-day list has to render so the day headers, and the
      // gaps beside them, are visible.
      "classification-windows": false,
    }[section];

  const pageTitle = currentWorkspace?.name
    ? `${currentWorkspace.name} - ${t(`workspace_settings.settings.worklog.${sectionKey}.title`)}`
    : undefined;

  return (
    <SettingsContentWrapper header={<WorklogWorkspaceSettingsHeader />}>
      <PageHead title={pageTitle} />
      <div className="w-full">
        {isCatalogueSection && (
          <CatalogOptionModal
            isOpen={isCreateModalOpen}
            handleClose={() => setIsCreateModalOpen(false)}
            workspaceSlug={workspaceSlug}
            kind={isHourTypes ? "hour-type" : "billing-type"}
          />
        )}

        {isHolidays && (
          <>
            <HolidayModal
              isOpen={isCreateModalOpen}
              workspaceSlug={workspaceSlug}
              handleClose={() => setIsCreateModalOpen(false)}
            />
            <HolidayCsvImportModal
              isOpen={isImportModalOpen}
              workspaceSlug={workspaceSlug}
              handleClose={() => setIsImportModalOpen(false)}
            />
          </>
        )}

        <SettingsHeading
          title={t("workspace_settings.settings.worklog.title")}
          description={t("workspace_settings.settings.worklog.description")}
        />

        <WorklogSettingsTabs workspaceSlug={workspaceSlug} activeSection={section} />

        <div className="flex items-start justify-between gap-4">
          <p className="text-sm text-tertiary">{t(`workspace_settings.settings.worklog.${sectionKey}.description`)}</p>

          <div className="flex flex-shrink-0 items-center gap-2">
            {isHolidays && (
              <Button variant="secondary" size="sm" onClick={() => setIsImportModalOpen(true)}>
                {t("workspace_settings.settings.worklog.holidays.import.action")}
              </Button>
            )}
            {/* The windows section has no top-level add button on purpose: a window belongs to
                a specific kind of day, so it is created from that day's row, which pre-selects
                the scope and removes a step. */}
            {!isWindows && (
              <Button variant="primary" size="sm" onClick={() => setIsCreateModalOpen(true)}>
                {t(`workspace_settings.settings.worklog.${sectionKey}.add`)}
              </Button>
            )}
          </div>
        </div>

        {/* The health indicator sits directly under the windows description, because it is
            the thing an admin most needs to see before editing anything on this screen. */}
        {isWindows && <CoverageHealthIndicator coverage={coverage} />}

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
            {isCatalogueSection && (
              <CatalogOptionList workspaceSlug={workspaceSlug} kind={isHourTypes ? "hour-type" : "billing-type"} />
            )}
            {isHolidays && <HolidayList workspaceSlug={workspaceSlug} />}
            {isWindows && <WindowList workspaceSlug={workspaceSlug} />}
          </div>
        )}
      </div>
    </SettingsContentWrapper>
  );
}

export default observer(WorklogSettingsPage);
