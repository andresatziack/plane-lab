/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { GROUPED_WORKSPACE_SETTINGS, PROJECT_SETTINGS_FLAT_MAP } from "@plane/constants";

const hrefToLabelMap = (options: Record<string, Array<{ href: string; i18n_label: string; [key: string]: any }>>) =>
  Object.values(options)
    .flat()
    .reduce(
      (acc, setting) => {
        acc[setting.href] = setting.i18n_label;

        // The section root too, for the same reason `WORKSPACE_SETTINGS_ACCESS` carries it:
        // `getWorkspaceActivePath` below looks the pathname up two segments deep, so an item
        // whose href is three deep (`/settings/worklog/hour-types`) had no label and left the
        // mobile settings header blank.
        const segments = setting.href.replace(/^\//, "").split("/");
        if (segments.length > 2) acc[`/${segments.slice(0, 2).join("/")}`] = setting.i18n_label;

        return acc;
      },
      {} as Record<string, string>
    );

const workspaceHrefToLabelMap = hrefToLabelMap(GROUPED_WORKSPACE_SETTINGS);

const projectHrefToLabelMap = PROJECT_SETTINGS_FLAT_MAP.reduce(
  (acc, setting) => {
    acc[setting.href] = setting.i18n_label;
    return acc;
  },
  {} as Record<string, string>
);

export const pathnameToAccessKey = (pathname: string) => {
  const pathArray = pathname.replace(/^\/|\/$/g, "").split("/"); // Regex removes leading and trailing slashes
  const workspaceSlug = pathArray[0];
  // Two segments: `/settings/<tab>`. Items whose href is deeper than that also register their
  // section root in `WORKSPACE_SETTINGS_ACCESS`, so this key always finds an entry.
  const accessKey = pathArray.slice(1, 3).join("/");
  // `|| ""` used to sit here and was dead: a template literal starting with "/" is never falsy.
  return { workspaceSlug, accessKey: `/${accessKey}` };
};

export const getWorkspaceActivePath = (pathname: string) => {
  const parts = pathname.split("/").filter(Boolean);
  const settingsIndex = parts.indexOf("settings");
  if (settingsIndex === -1) return null;
  const subPath = "/" + parts.slice(settingsIndex, settingsIndex + 2).join("/");
  return workspaceHrefToLabelMap[subPath];
};

export const getProjectActivePath = (pathname: string) => {
  const parts = pathname.split("/").filter(Boolean);
  const settingsIndex = parts.indexOf("settings");
  if (settingsIndex === -1) return null;
  const subPath = parts.slice(settingsIndex + 3, settingsIndex + 4).join("/");
  return subPath ? projectHrefToLabelMap["/" + subPath] : projectHrefToLabelMap[subPath];
};
