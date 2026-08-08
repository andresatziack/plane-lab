/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useMemo, useCallback } from "react";
// plane imports
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import { BarIcon, CycleIcon, IntakeIcon, ModuleIcon, PageIcon, ViewsIcon, WorkItemsIcon } from "@plane/propel/icons";
import type { EUserProjectRoles, IPartialProject } from "@plane/types";
import type { TNavigationItem } from "@/components/navigation/tab-navigation-root";
import { useWorkItemEditPermissions } from "@/hooks/use-work-item-edit-permissions";

type UseNavigationItemsProps = {
  workspaceSlug: string;
  projectId: string;
  project?: IPartialProject;
  allowPermissions: (
    access: EUserPermissions[] | EUserProjectRoles[],
    level: EUserPermissionsLevel,
    workspaceSlug: string,
    projectId: string
  ) => boolean;
};

export const useNavigationItems = ({
  workspaceSlug,
  projectId,
  project,
  allowPermissions,
}: UseNavigationItemsProps): TNavigationItem[] => {
  // Who is a client's own user. See the note on the consumption item below and its twin in
  // `project-navigation.tsx`.
  const { restrictedFields } = useWorkItemEditPermissions(workspaceSlug, projectId);
  // Base navigation items
  const baseNavigation = useCallback(
    // oxlint-disable-next-line no-shadow
    (workspaceSlug: string, projectId: string): TNavigationItem[] => [
      {
        i18n_key: "sidebar.work_items",
        key: "work_items",
        name: "Work items",
        href: `/${workspaceSlug}/projects/${projectId}/issues`,
        icon: WorkItemsIcon,
        access: [EUserPermissions.ADMIN, EUserPermissions.MEMBER, EUserPermissions.GUEST],
        shouldRender: true,
        sortOrder: 1,
      },
      {
        i18n_key: "sidebar.cycles",
        key: "cycles",
        name: "Cycles",
        href: `/${workspaceSlug}/projects/${projectId}/cycles`,
        icon: CycleIcon,
        access: [EUserPermissions.ADMIN, EUserPermissions.MEMBER],
        shouldRender: !!project?.cycle_view,
        sortOrder: 2,
      },
      {
        i18n_key: "sidebar.modules",
        key: "modules",
        name: "Modules",
        href: `/${workspaceSlug}/projects/${projectId}/modules`,
        icon: ModuleIcon,
        access: [EUserPermissions.ADMIN, EUserPermissions.MEMBER],
        shouldRender: !!project?.module_view,
        sortOrder: 3,
      },
      {
        i18n_key: "sidebar.views",
        key: "views",
        name: "Views",
        href: `/${workspaceSlug}/projects/${projectId}/views`,
        icon: ViewsIcon,
        access: [EUserPermissions.ADMIN, EUserPermissions.MEMBER, EUserPermissions.GUEST],
        shouldRender: !!project?.issue_views_view,
        sortOrder: 4,
      },
      {
        i18n_key: "sidebar.pages",
        key: "pages",
        name: "Pages",
        href: `/${workspaceSlug}/projects/${projectId}/pages`,
        icon: PageIcon,
        access: [EUserPermissions.ADMIN, EUserPermissions.MEMBER, EUserPermissions.GUEST],
        shouldRender: !!project?.page_view,
        sortOrder: 5,
      },
      {
        i18n_key: "sidebar.intake",
        key: "intake",
        name: "Intake",
        href: `/${workspaceSlug}/projects/${projectId}/intake`,
        icon: IntakeIcon,
        access: [EUserPermissions.ADMIN, EUserPermissions.MEMBER, EUserPermissions.GUEST],
        shouldRender: !!project?.inbox_view,
        sortOrder: 6,
      },
      {
        // The client's own consumption dashboard. Phase 8b, criteria 1, 2 and 9.
        //
        // **The twin of the item in `project-navigation.tsx`, and it has to stay a twin.** The
        // two arrays are duplicated in this codebase, one per navigation mode, so an item added
        // to only one is invisible in the other -- a bug that survives manual review because
        // whoever checks is in the mode that works. Same class of miss as the second edit gate in
        // the peek overview in Phase 8.
        //
        // See that file for why `restrictedFields` is the discriminant and `access` is not.
        i18n_key: "sidebar.service_consumption",
        key: "service_consumption",
        name: "My consumption",
        href: `/${workspaceSlug}/projects/${projectId}/service-consumption`,
        icon: BarIcon,
        access: [EUserPermissions.ADMIN, EUserPermissions.MEMBER, EUserPermissions.GUEST],
        shouldRender: !restrictedFields && !!project?.service_client && !!project?.is_time_tracking_enabled,
        sortOrder: 7,
      },
    ],
    [project, restrictedFields]
  );

  // Combine, filter, and sort navigation items
  const navigationItems = useMemo(() => {
    const navItems = baseNavigation(workspaceSlug, projectId);

    // Filter by permissions and shouldRender
    const filteredItems = navItems.filter((item) => {
      if (!item.shouldRender) return false;
      const hasAccess = allowPermissions(item.access, EUserPermissionsLevel.PROJECT, workspaceSlug, project?.id ?? "");
      return hasAccess;
    });

    // Sort by sortOrder
    // oxlint-disable-next-line unicorn/no-array-sort
    return filteredItems.sort((a, b) => (a.sortOrder || 0) - (b.sortOrder || 0));
  }, [workspaceSlug, projectId, baseNavigation, allowPermissions, project?.id]);

  return navigationItems;
};
