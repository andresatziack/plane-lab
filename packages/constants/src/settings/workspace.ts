/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// plane imports
import type { TWorkspaceSettingsItem, TWorkspaceSettingsTabs } from "@plane/types";
import { EUserWorkspaceRoles } from "@plane/types";

export enum WORKSPACE_SETTINGS_CATEGORY {
  ADMINISTRATION = "administration",
  FEATURES = "features",
  DEVELOPER = "developer",
}

export const WORKSPACE_SETTINGS_CATEGORIES: WORKSPACE_SETTINGS_CATEGORY[] = [
  WORKSPACE_SETTINGS_CATEGORY.ADMINISTRATION,
  WORKSPACE_SETTINGS_CATEGORY.FEATURES,
  WORKSPACE_SETTINGS_CATEGORY.DEVELOPER,
];

export const WORKSPACE_SETTINGS_CATEGORY_LABELS: Record<WORKSPACE_SETTINGS_CATEGORY, string> = {
  [WORKSPACE_SETTINGS_CATEGORY.ADMINISTRATION]: "common.administration",
  [WORKSPACE_SETTINGS_CATEGORY.FEATURES]: "common.features",
  [WORKSPACE_SETTINGS_CATEGORY.DEVELOPER]: "common.developer",
};

export const WORKSPACE_SETTINGS: Record<TWorkspaceSettingsTabs, TWorkspaceSettingsItem> = {
  general: {
    key: "general",
    i18n_label: "workspace_settings.settings.general.title",
    href: `/settings`,
    access: [EUserWorkspaceRoles.ADMIN, EUserWorkspaceRoles.MEMBER],
    highlight: (pathname: string, baseUrl: string) => pathname === `${baseUrl}/settings/`,
  },
  members: {
    key: "members",
    i18n_label: "workspace_settings.settings.members.title",
    href: `/settings/members`,
    access: [EUserWorkspaceRoles.ADMIN, EUserWorkspaceRoles.MEMBER],
    highlight: (pathname: string, baseUrl: string) => pathname === `${baseUrl}/settings/members/`,
  },
  "billing-and-plans": {
    key: "billing-and-plans",
    i18n_label: "workspace_settings.settings.billing_and_plans.title",
    href: `/settings/billing`,
    access: [EUserWorkspaceRoles.ADMIN],
    highlight: (pathname: string, baseUrl: string) => pathname === `${baseUrl}/settings/billing/`,
  },
  export: {
    key: "export",
    i18n_label: "workspace_settings.settings.exports.title",
    href: `/settings/exports`,
    access: [EUserWorkspaceRoles.ADMIN, EUserWorkspaceRoles.MEMBER],
    highlight: (pathname: string, baseUrl: string) => pathname === `${baseUrl}/settings/exports/`,
  },
  "service-clients": {
    key: "service-clients",
    i18n_label: "workspace_settings.settings.service_clients.title",
    href: `/settings/service-clients`,
    // Admin only: client records carry billing configuration.
    access: [EUserWorkspaceRoles.ADMIN],
    highlight: (pathname: string, baseUrl: string) => pathname.startsWith(`${baseUrl}/settings/service-clients`),
  },
  worklog: {
    key: "worklog",
    i18n_label: "workspace_settings.settings.worklog.title",
    href: `/settings/worklog/hour-types`,
    // Admin only: the hour type payload carries the multiplier, which rule R11 keeps
    // away from clients, and these catalogues parameterise every invoice.
    access: [EUserWorkspaceRoles.ADMIN],
    // startsWith, not equality: the panel has one entry and several sections, and the
    // calendar and windows phase adds two more without touching this.
    highlight: (pathname: string, baseUrl: string) => pathname.startsWith(`${baseUrl}/settings/worklog`),
  },
  webhooks: {
    key: "webhooks",
    i18n_label: "workspace_settings.settings.webhooks.title",
    href: `/settings/webhooks`,
    access: [EUserWorkspaceRoles.ADMIN],
    highlight: (pathname: string, baseUrl: string) => pathname === `${baseUrl}/settings/webhooks/`,
  },
};

/**
 * Who may open each settings screen, keyed by path.
 *
 * **Every item contributes its section root as well as its href**, and that is not
 * redundancy. The guard in `settings/(workspace)/layout.tsx` resolves the key from the
 * *pathname*, and an item whose href points at a section -- `worklog`, whose href is
 * `/settings/worklog/hour-types` -- has several pathnames sharing one ACL. Keyed only by
 * href, the lookup for `/settings/worklog` and for `/settings/worklog/billing-types`
 * returned `undefined`, which the guard reads as "not allowed" and so **denied every role,
 * including ADMIN**. That screen was unreachable for everybody.
 *
 * Deriving the root here rather than widening the guard keeps one answer to "who may see
 * this": the item's own `access` list, whatever depth its href has.
 */
export const WORKSPACE_SETTINGS_ACCESS = Object.fromEntries(
  Object.values(WORKSPACE_SETTINGS).flatMap(({ href, access }) => {
    const segments = href.replace(/^\//, "").split("/");
    const entries: [string, typeof access][] = [[href, access]];

    if (segments.length > 2) entries.push([`/${segments.slice(0, 2).join("/")}`, access]);

    return entries;
  })
);

export const GROUPED_WORKSPACE_SETTINGS: Record<WORKSPACE_SETTINGS_CATEGORY, TWorkspaceSettingsItem[]> = {
  [WORKSPACE_SETTINGS_CATEGORY.ADMINISTRATION]: [
    WORKSPACE_SETTINGS["general"],
    WORKSPACE_SETTINGS["members"],
    WORKSPACE_SETTINGS["billing-and-plans"],
    WORKSPACE_SETTINGS["export"],
  ],
  [WORKSPACE_SETTINGS_CATEGORY.FEATURES]: [WORKSPACE_SETTINGS["service-clients"], WORKSPACE_SETTINGS["worklog"]],
  [WORKSPACE_SETTINGS_CATEGORY.DEVELOPER]: [WORKSPACE_SETTINGS["webhooks"]],
};
