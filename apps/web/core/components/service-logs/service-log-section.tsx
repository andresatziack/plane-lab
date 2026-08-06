/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useState } from "react";
import { observer } from "mobx-react";
import { Plus } from "lucide-react";
import useSWR from "swr";
// plane imports
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
// hooks
import { useIssueDetail } from "@/hooks/store/use-issue-detail";
import { useProject } from "@/hooks/store/use-project";
import { useServiceCatalog } from "@/hooks/store/use-service-catalog";
import { useUser } from "@/hooks/store/user";
// local imports
import type { TServiceLogBatch } from "@/store/issue/issue-details/service-log.store";
import { DeleteServiceLogModal } from "./delete-service-log-modal";
import { ServiceLogListItem } from "./service-log-list-item";
import { ServiceLogModal } from "./service-log-modal";
import { ServiceLogTotals } from "./service-log-totals";
import { toEditPayload } from "./service-log.helpers";

type Props = {
  workspaceSlug: string;
  projectId: string;
  issueId: string;
  disabled?: boolean;
};

/**
 * The work log section of a work item. Sections 6 and 7 of the phase brief.
 *
 * Shown to technicians and admins. GUEST never reaches this: the endpoints it reads
 * refuse that role outright, because the payload carries the raw duration and the
 * multiplier that R11 keeps from clients. The client portal is a separate screen in a
 * later phase.
 */
export const ServiceLogSection = observer(function ServiceLogSection(props: Props) {
  const { workspaceSlug, projectId, issueId, disabled = false } = props;
  // plane hooks
  const { t } = useTranslation();
  // store hooks
  const { serviceLog } = useIssueDetail();
  const { getProjectById } = useProject();
  const { fetchHourTypes, fetchBillingTypes, hourTypes, billingTypes } = useServiceCatalog();
  const { data: currentUser } = useUser();
  // state
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [editingBatch, setEditingBatch] = useState<TServiceLogBatch | null>(null);
  const [deletingBatch, setDeletingBatch] = useState<TServiceLogBatch | null>(null);

  // derived values
  const project = getProjectById(projectId);
  const isTimeTrackingEnabled = Boolean(project?.is_time_tracking_enabled);

  useSWR(
    workspaceSlug && projectId && issueId ? `SERVICE_LOGS_${workspaceSlug}_${projectId}_${issueId}` : null,
    workspaceSlug && projectId && issueId ? () => serviceLog.fetchServiceLogs(workspaceSlug, projectId, issueId) : null,
    { revalidateIfStale: false, revalidateOnFocus: false }
  );

  // The form's dropdowns need the catalogues. Only the active options, because a
  // retired option must not be selectable for new work -- Phase 2, criterion 3.
  useEffect(() => {
    if (!workspaceSlug) return;
    if (!hourTypes) void fetchHourTypes(workspaceSlug);
    if (!billingTypes) void fetchBillingTypes(workspaceSlug);
  }, [workspaceSlug, hourTypes, billingTypes, fetchHourTypes, fetchBillingTypes]);

  const batches = serviceLog.getServiceLogBatchesByIssueId(issueId);
  const totals = serviceLog.getTotalsByIssueId(issueId);
  const canLogTime = !disabled && isTimeTrackingEnabled;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <h4 className="text-base text-custom-text-100 font-medium">{t("work_item.service_log.title")}</h4>
        {canLogTime && (
          <Button variant="secondary" size="sm" onClick={() => setIsCreateOpen(true)} prependIcon={<Plus />}>
            {t("work_item.service_log.add")}
          </Button>
        )}
      </div>

      {/* The three labelled totals. Shown even at zero, so the section reads the same
          before and after the first entry. */}
      <ServiceLogTotals totals={totals} />

      {/* The toggle is off: say so, and say who can change it. Reads still work, so any
          previously logged time stays visible below. */}
      {!isTimeTrackingEnabled && (
        <div className="bg-custom-background-90 flex flex-col gap-0.5 rounded-md px-3 py-2">
          <span className="text-sm text-custom-text-200">{t("work_item.service_log.disabled")}</span>
          <span className="text-xs text-custom-text-350">{t("work_item.service_log.disabled_description")}</span>
        </div>
      )}

      {batches.length === 0 ? (
        <div className="border-custom-border-200 flex flex-col gap-0.5 rounded-md border border-dashed px-3 py-4 text-center">
          <span className="text-sm text-custom-text-200">{t("work_item.service_log.empty")}</span>
          <span className="text-xs text-custom-text-350">{t("work_item.service_log.empty_description")}</span>
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          {batches.map((batch) => (
            <ServiceLogListItem
              key={batch.batchId}
              batch={batch}
              // Section 6: "por ora, apenas o autor". The server enforces it too --
              // this only avoids showing a button that would be refused.
              canModify={!disabled && batch.segments[0].author === currentUser?.id}
              onEdit={() => setEditingBatch(batch)}
              onDelete={() => setDeletingBatch(batch)}
            />
          ))}
        </div>
      )}

      <ServiceLogModal
        isOpen={isCreateOpen}
        workspaceSlug={workspaceSlug}
        projectId={projectId}
        issueId={issueId}
        handleClose={() => setIsCreateOpen(false)}
      />

      {editingBatch && (
        <ServiceLogModal
          isOpen
          workspaceSlug={workspaceSlug}
          projectId={projectId}
          issueId={issueId}
          batchId={editingBatch.batchId}
          initialValues={toEditPayload(editingBatch.segments)}
          handleClose={() => setEditingBatch(null)}
        />
      )}

      <DeleteServiceLogModal
        isOpen={Boolean(deletingBatch)}
        workspaceSlug={workspaceSlug}
        projectId={projectId}
        issueId={issueId}
        batch={deletingBatch}
        handleClose={() => setDeletingBatch(null)}
      />
    </div>
  );
});
