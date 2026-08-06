/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
// plane imports
import { useTranslation } from "@plane/i18n";
// components
import { ProjectServiceClientSelect } from "@/components/service-clients/project-service-client-select";
import { SettingsBoxedControlItem } from "@/components/settings/boxed-control-item";

type Props = {
  workspaceSlug: string;
  projectId: string;
  isAdmin: boolean;
};

/**
 * Client link for a project, on the project's general settings screen.
 *
 * Editing is restricted to admins because the link determines which client the
 * project's work is attributed to and billed against.
 */
export const ProjectServiceClientSection = observer(function ProjectServiceClientSection(props: Props) {
  const { workspaceSlug, projectId, isAdmin } = props;
  // plane hooks
  const { t } = useTranslation();

  return (
    <div className="my-4 rounded-lg border border-subtle bg-layer-2">
      <SettingsBoxedControlItem
        className="border-0"
        title={t("project_settings.service_client.label")}
        description={t("project_settings.service_client.hint")}
        control={
          <div className="w-64">
            <ProjectServiceClientSelect workspaceSlug={workspaceSlug} projectId={projectId} disabled={!isAdmin} />
          </div>
        }
      />
    </div>
  );
});
