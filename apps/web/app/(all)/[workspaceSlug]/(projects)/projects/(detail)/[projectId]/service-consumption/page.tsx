/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
// plane imports
import { useTranslation } from "@plane/i18n";
// components
import { NotAuthorizedView } from "@/components/auth-screens/not-authorized-view";
import { PageHead } from "@/components/core/page-title";
import { IssuePeekOverview } from "@/components/issues/peek-overview";
import { PortalConsumptionDashboard } from "@/components/service-reports";
// hooks
import { useProject } from "@/hooks/store/use-project";
import { useWorkItemEditPermissions } from "@/hooks/use-work-item-edit-permissions";
import type { Route } from "./+types/page";

/**
 * The client's own consumption dashboard, inside the project. Phase 8b, criteria 1 to 3, 9.
 *
 * **Two guards, and neither is a new role check.** Membership is the route's: nesting under the
 * `[projectId]` layout means `ProjectAuthWrapper` has already refused a non-member with
 * `ProjectAccessRestriction`, so criterion 3 holds for a typed URL without anything here. What
 * is left is criterion 9 -- a technician must not land on the client's dashboard -- and that is
 * `restrictedFields`.
 */
function ServiceConsumptionPage({ params }: Route.ComponentProps) {
  const { workspaceSlug, projectId } = params;
  const { t } = useTranslation();
  const { getProjectById } = useProject();

  /*
   * `restrictedFields` is being read as "this caller is a client". **That is a coupling worth
   * naming, because the two are not the same statement.** What the flag means is "may edit the
   * fields a client may not" -- title, assignees, dates, estimate -- and what it is standing in
   * for here is "is a client's own user".
   *
   * They coincide today because both resolve to "project ADMIN or MEMBER", and the substitution
   * fails safe in both directions: a Member who somehow landed on the client side would see a
   * projection with *less* than they are entitled to, and a GUEST wrongly excluded gets
   * `NotAuthorizedView` rather than somebody else's numbers.
   *
   * It is still the right discriminant, and deliberately so: the alternative is a second role
   * check written here, and one source of truth for "who is the client" is worth more than
   * semantic precision spread across two. But if the two ever diverge -- if `restrictedFields`
   * grows to mean something narrower -- this page and the navigation item that leads to it
   * appear for the wrong audience, and this comment is the pointer to why.
   */
  const { restrictedFields } = useWorkItemEditPermissions(workspaceSlug, projectId);

  const project = getProjectById(projectId);
  const pageTitle = project?.name
    ? t("service_reports.portal.page_label", { project: project.name })
    : t("service_reports.portal.label");

  if (restrictedFields) return <NotAuthorizedView section="settings" isProjectView className="h-auto" />;

  return (
    <>
      <PageHead title={pageTitle} />
      <PortalConsumptionDashboard workspaceSlug={workspaceSlug} projectId={projectId} />
      <IssuePeekOverview />
    </>
  );
}

export default observer(ServiceConsumptionPage);
