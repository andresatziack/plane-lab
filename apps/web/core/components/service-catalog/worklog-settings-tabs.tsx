/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { Link } from "react-router";
// plane imports
import { useTranslation } from "@plane/i18n";
import { TabNavigationItem, TabNavigationList } from "@plane/propel/tab-navigation";

/**
 * The sections of the work log administration panel.
 *
 * Section 6 of the catalogue phase asked for the navigation to be structured for what came
 * next, and it paid off: the calendar and windows phase added its two sections as two
 * entries below plus one route each, with no change to the sidebar or to this component.
 */
export const WORKLOG_SETTINGS_SECTIONS = [
  { key: "hour-types", i18nLabel: "workspace_settings.settings.worklog.hour_types.title" },
  { key: "billing-types", i18nLabel: "workspace_settings.settings.worklog.billing_types.title" },
  { key: "holidays", i18nLabel: "workspace_settings.settings.worklog.holidays.title" },
  {
    key: "classification-windows",
    i18nLabel: "workspace_settings.settings.worklog.windows.title",
  },
] as const;

export type TWorklogSettingsSection = (typeof WORKLOG_SETTINGS_SECTIONS)[number]["key"];

export const DEFAULT_WORKLOG_SETTINGS_SECTION: TWorklogSettingsSection = "hour-types";

export const isWorklogSettingsSection = (value: string | undefined): value is TWorklogSettingsSection =>
  WORKLOG_SETTINGS_SECTIONS.some((section) => section.key === value);

type Props = {
  workspaceSlug: string;
  activeSection: TWorklogSettingsSection;
};

export function WorklogSettingsTabs({ workspaceSlug, activeSection }: Props) {
  const { t } = useTranslation();

  return (
    <TabNavigationList className="mb-4">
      {WORKLOG_SETTINGS_SECTIONS.map((section) => (
        <Link key={section.key} to={`/${workspaceSlug}/settings/worklog/${section.key}/`}>
          <TabNavigationItem isActive={section.key === activeSection}>{t(section.i18nLabel)}</TabNavigationItem>
        </Link>
      ))}
    </TabNavigationList>
  );
}
