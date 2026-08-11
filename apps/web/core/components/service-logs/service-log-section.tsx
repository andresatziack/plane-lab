/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useState } from "react";
import { observer } from "mobx-react";
import { Pencil, Plus, Trash2, Wallet } from "lucide-react";
import useSWR from "swr";
// plane imports
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
// hooks
import { useIssueDetail } from "@/hooks/store/use-issue-detail";
import { useProject } from "@/hooks/store/use-project";
import { useServiceCatalog } from "@/hooks/store/use-service-catalog";
import { useUser, useUserPermissions } from "@/hooks/store/user";
// local imports
import { ServiceMemberPermissionService } from "@/services/service-member-permission.service";
import type { TServiceLogBatch } from "@/store/issue/issue-details/service-log.store";
import { CreditServiceAllowanceModal } from "./credit-service-allowance-modal";
import { DeleteServiceAllowanceModal } from "./delete-service-allowance-modal";
import { DeleteServiceLogModal } from "./delete-service-log-modal";
import { EditServiceAllowanceModal } from "./edit-service-allowance-modal";
import { ServiceAllowanceIndicator } from "./service-allowance-indicator";
import { ServiceLogListItem } from "./service-log-list-item";
import { ServiceLogModal } from "./service-log-modal";
import { ServiceLogTotals } from "./service-log-totals";
import { toEditPayload } from "./service-log.helpers";

const serviceMemberPermissionService = new ServiceMemberPermissionService();

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
  const { serviceLog, serviceAllowance } = useIssueDetail();
  const { getProjectById } = useProject();
  const { fetchHourTypes, fetchBillingTypes, hourTypes, billingTypes } = useServiceCatalog();
  const { data: currentUser } = useUser();
  const { allowPermissions } = useUserPermissions();
  // state
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [isCreditOpen, setIsCreditOpen] = useState(false);
  const [editingBatch, setEditingBatch] = useState<TServiceLogBatch | null>(null);
  const [deletingBatch, setDeletingBatch] = useState<TServiceLogBatch | null>(null);
  const [isEditAllowanceOpen, setIsEditAllowanceOpen] = useState(false);
  const [isDeleteAllowanceOpen, setIsDeleteAllowanceOpen] = useState(false);

  // derived values
  const project = getProjectById(projectId);
  const isTimeTrackingEnabled = Boolean(project?.is_time_tracking_enabled);

  // Crediting hours is a commercial act -- it decides a project is worth 40 hours -- so
  // it takes a **workspace** admin, matching the endpoint and every other money-writing
  // route in this feature. A project admin is not a workspace admin in this fork. The
  // server is the real guard; this only avoids offering a button that would be refused.
  const canCreditAllowance = allowPermissions([EUserPermissions.ADMIN], EUserPermissionsLevel.WORKSPACE);

  /*
   * May this viewer change somebody else's work log?
   *
   * **Asked of the server, not derived from the role.** `can_manage_others` is `is_admin OR the
   * granted flag`, so an admin holds it with no row and a member holds it only when an admin
   * granted it -- a role check here would get the second case wrong, and Phase 7 exists to make
   * that second case possible. `/service-member-permissions/me/` returns the resolved answer and
   * is the same function `validate_can_change` calls on the write path.
   *
   * Until it arrives this stays false, so the buttons appear rather than disappear: offering an
   * edit that is then refused is a worse failure than a button showing up a moment late.
   */
  const { data: capabilities } = useSWR(
    workspaceSlug ? `SERVICE_LOG_CAPABILITIES_${workspaceSlug}` : null,
    workspaceSlug ? () => serviceMemberPermissionService.fetchMine(workspaceSlug) : null,
    { revalidateIfStale: false, revalidateOnFocus: false }
  );
  const canManageOthersLogs = Boolean(capabilities?.can_manage_others);

  useSWR(
    workspaceSlug && projectId && issueId ? `SERVICE_LOGS_${workspaceSlug}_${projectId}_${issueId}` : null,
    workspaceSlug && projectId && issueId ? () => serviceLog.fetchServiceLogs(workspaceSlug, projectId, issueId) : null,
    { revalidateIfStale: false, revalidateOnFocus: false }
  );

  // A separate key from the work logs above, deliberately: the allowance is a different
  // resource with a different permission, and a technician who cannot read one must still
  // get the other rather than one failure blanking the whole section.
  useSWR(
    workspaceSlug && projectId && issueId ? `SERVICE_ALLOWANCE_${workspaceSlug}_${projectId}_${issueId}` : null,
    workspaceSlug && projectId && issueId
      ? () => serviceAllowance.fetchAllowance(workspaceSlug, projectId, issueId)
      : null,
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

  // `undefined` is "not loaded yet" and `null` is "no allowance", and they render
  // differently: showing the credit call-to-action before the answer has arrived would
  // flash a wrong statement about how this work item is billed.
  const allowanceSummary = serviceAllowance.getSummaryByIssueId(issueId);
  const allowanceAlerts = serviceAllowance.getAlertsByIssueId(issueId);
  const hasLoadedAllowance = serviceAllowance.hasLoadedByIssueId(issueId);

  /*
   * `@container` is load-bearing, not decoration.
   *
   * This section renders inside three shells of very different widths: the side peek, which is
   * `w-full` below `md` and `md:w-[50%]` above it; the full-screen peek, whose content column
   * is the viewport minus a 400px rail; and the full work item page. A viewport breakpoint gets
   * that backwards -- `md:grid-cols-4` fires at 768px, which is exactly the width where the
   * side peek halves, so the cards had LESS room at 768px (~50px of card interior) than at
   * 390px (~151px). Every breakpoint inside this section therefore queries this element's own
   * width with `@`-prefixed variants (Tailwind 4 has container queries built in, and
   * `settings/content-wrapper.tsx` already uses them), so a card gets more room only when the
   * container really is wider.
   *
   * Container sizes used below: `@sm` 24rem/384px, `@md` 28rem/448px, `@xl` 36rem/576px.
   */
  return (
    <div className="@container flex flex-col gap-3">
      {/* The action cluster can hold four buttons (Editar, Excluir, Bolsa de horas, Adicionar
          apontamento) and they do not fit beside the title on a phone. Wrapping is the whole
          fix: no button is hidden or dropped, and no permission condition below changed --
          hiding an action on a small screen would make the feature unusable on a phone rather
          than merely ugly.
          `flex-wrap` here is unconditional, deliberately and at every width: a narrow desktop
          window with four buttons and a long translated title used to push the cluster past the
          edge, and wrapping is the better of the two failure modes. Only a ROW gap is added, so
          a row that fits is laid out exactly as before. */}
      <div className="flex flex-wrap items-center justify-between gap-y-2">
        <h4 className="text-base text-custom-text-100 font-medium">{t("work_item.service_log.title")}</h4>
        <div className="flex flex-wrap items-center justify-end gap-2">
          {canCreditAllowance &&
            hasLoadedAllowance &&
            allowanceSummary &&
            !allowanceSummary.is_inherited &&
            allowanceSummary.status !== "closed" && (
              <>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => setIsEditAllowanceOpen(true)}
                  prependIcon={<Pencil />}
                >
                  {t("common.edit")}
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => setIsDeleteAllowanceOpen(true)}
                  prependIcon={<Trash2 />}
                >
                  {t("common.delete")}
                </Button>
              </>
            )}
          {canCreditAllowance && hasLoadedAllowance && (
            <Button variant="secondary" size="sm" onClick={() => setIsCreditOpen(true)} prependIcon={<Wallet />}>
              {allowanceSummary ? t("work_item.service_allowance.top_up") : t("work_item.service_allowance.add")}
            </Button>
          )}
          {canLogTime && (
            <Button variant="secondary" size="sm" onClick={() => setIsCreateOpen(true)} prependIcon={<Plus />}>
              {t("work_item.service_log.add")}
            </Button>
          )}
        </div>
      </div>

      {/* Rule R6's first level, above the contract totals because that is the order the
          debit engine asks in: when this is present, it is what pays. */}
      {allowanceSummary && <ServiceAllowanceIndicator summary={allowanceSummary} alerts={allowanceAlerts} />}

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
        <div className="flex flex-col gap-0.5 rounded-md border border-dashed border-subtle px-3 py-4 text-center">
          <span className="text-sm text-custom-text-200">{t("work_item.service_log.empty")}</span>
          <span className="text-xs text-custom-text-350">{t("work_item.service_log.empty_description")}</span>
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          {batches.map((batch) => (
            <ServiceLogListItem
              key={batch.batchId}
              batch={batch}
              /*
               * The author, or anybody holding `can_manage_others`.
               *
               * Phase 3 wrote "por ora, apenas o autor" and Phase 7 lifted it -- the backend's
               * `may_edit` returns true for `can_manage_others` before it ever looks at the
               * author. This condition kept the Phase 3 half, so an **admin saw the list and no
               * buttons**: the server would have accepted the edit and the UI never offered it.
               *
               * The server is still the guard; this only decides what to offer.
               */
              canModify={!disabled && (canManageOthersLogs || batch.segments[0].author === currentUser?.id)}
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

      <CreditServiceAllowanceModal
        isOpen={isCreditOpen}
        workspaceSlug={workspaceSlug}
        projectId={projectId}
        issueId={issueId}
        // A top-up only when the allowance belongs to *this* work item. An inherited one
        // belongs to an ancestor, and crediting here would create a second, separate
        // allowance on the sub-task -- so the copy has to say "create", not "add to".
        isTopUp={Boolean(allowanceSummary && !allowanceSummary.is_inherited)}
        handleClose={() => setIsCreditOpen(false)}
      />

      <EditServiceAllowanceModal
        isOpen={isEditAllowanceOpen}
        workspaceSlug={workspaceSlug}
        projectId={projectId}
        issueId={issueId}
        currentReference={allowanceSummary?.reference ?? ""}
        currentNotes={allowanceSummary?.notes ?? ""}
        handleClose={() => setIsEditAllowanceOpen(false)}
      />

      <DeleteServiceAllowanceModal
        isOpen={isDeleteAllowanceOpen}
        workspaceSlug={workspaceSlug}
        projectId={projectId}
        issueId={issueId}
        handleClose={() => setIsDeleteAllowanceOpen(false)}
      />
    </div>
  );
});
