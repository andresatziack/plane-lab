/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
import useSWR from "swr";
// plane imports
import { useTranslation } from "@plane/i18n";
import { Tooltip } from "@plane/ui";
// hooks
import { useProject } from "@/hooks/store/use-project";
import { useServiceClient } from "@/hooks/store/use-service-client";

type Props = {
  workspaceSlug: string;
  projectId: string;
};

/**
 * The client company a work item belongs to.
 *
 * Read only by design. The client is derived from the work item's project, so it
 * can never disagree with the project the item lives in, and there is nothing to
 * keep in sync when an item is moved. To change the client of a work item, move
 * it to a project that belongs to that client. See docs/worklog/DECISOES.md, D19.
 */
export const IssueServiceClientProperty = observer(function IssueServiceClientProperty(props: Props) {
  const { workspaceSlug, projectId } = props;
  // plane hooks
  const { t } = useTranslation();
  // store hooks
  const { getProjectById } = useProject();
  const { getServiceClientNameById, serviceClients, fetchServiceClients } = useServiceClient();

  // The work item carries only the client id; names come from the workspace list,
  // fetched once and shared, so rendering the chip costs no request per item.
  useSWR(
    workspaceSlug ? `SERVICE_CLIENTS_LIST_${workspaceSlug}` : null,
    workspaceSlug ? () => fetchServiceClients(workspaceSlug) : null,
    { revalidateIfStale: false, revalidateOnFocus: false }
  );

  const project = getProjectById(projectId);
  const serviceClientId = project?.service_client ?? null;

  // Nothing to show while the client list is loading, and nothing to show for
  // internal work. Rendering "None" here would suggest the field is unset rather
  // than that the project is internal.
  if (!serviceClientId) return null;

  const serviceClientName = serviceClients ? getServiceClientNameById(serviceClientId) : undefined;

  return (
    <Tooltip tooltipContent={t("work_item.service_client.tooltip")} position="left">
      <span className="flex h-7.5 w-full cursor-default items-center truncate text-body-xs-regular">
        {serviceClientName ?? "..."}
      </span>
    </Tooltip>
  );
});
