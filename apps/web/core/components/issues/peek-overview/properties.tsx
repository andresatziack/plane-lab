/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
import { UserRound } from "lucide-react";
// i18n
import { useTranslation } from "@plane/i18n";
// ui icons
import {
  CycleIcon,
  StatePropertyIcon,
  ModuleIcon,
  MembersPropertyIcon,
  PriorityPropertyIcon,
  StartDatePropertyIcon,
  DueDatePropertyIcon,
  LabelPropertyIcon,
  UserCirclePropertyIcon,
  EstimatePropertyIcon,
  ParentPropertyIcon,
} from "@plane/propel/icons";
import { cn, getDate, renderFormattedPayloadDate, shouldHighlightIssueDueDate } from "@plane/utils";
// components
import { DateDropdown } from "@/components/dropdowns/date";
import { EstimateDropdown } from "@/components/dropdowns/estimate";
import { ButtonAvatars } from "@/components/dropdowns/member/avatar";
import { MemberDropdown } from "@/components/dropdowns/member/dropdown";
import { PriorityDropdown } from "@/components/dropdowns/priority";
import { StateDropdown } from "@/components/dropdowns/state/dropdown";
import { SidebarPropertyListItem } from "@/components/common/layout/sidebar/property-list-item";
// helpers
import { useIssueDetail } from "@/hooks/store/use-issue-detail";
import { useMember } from "@/hooks/store/use-member";
import { useProject } from "@/hooks/store/use-project";
import { useProjectState } from "@/hooks/store/use-project-state";
import { clientWritableStateIds, type TWorkItemEditPermissions } from "@/hooks/use-work-item-edit-permissions";
// plane web components
import { IssueParentSelectRoot } from "@/components/issues/parent-select-root";
import type { TIssueOperations } from "../issue-detail";
import { IssueCycleSelect } from "../issue-detail/cycle-select";
import { IssueLabel } from "../issue-detail/label";
import { IssueModuleSelect } from "../issue-detail/module-select";
import { IssueServiceRequesterProperty } from "../issue-detail/service-requester-property";

interface IPeekOverviewProperties {
  workspaceSlug: string;
  projectId: string;
  issueId: string;
  editPermissions: TWorkItemEditPermissions;
  issueOperations: TIssueOperations;
}

export const PeekOverviewProperties = observer(function PeekOverviewProperties(props: IPeekOverviewProperties) {
  const { workspaceSlug, projectId, issueId, issueOperations, editPermissions } = props;
  const { t } = useTranslation();
  // store hooks
  const { getProjectById } = useProject();
  const {
    issue: { getIssueById },
  } = useIssueDetail();
  const { getStateById, getProjectStates } = useProjectState();
  const { getUserDetails } = useMember();
  // derived values
  const issue = getIssueById(issueId);
  if (!issue) return <></>;
  const createdByDetails = getUserDetails(issue?.created_by);
  const projectDetails = getProjectById(issue.project_id);
  const isEstimateEnabled = projectDetails?.estimate;
  const stateDetails = getStateById(issue.state_id);

  const minDate = getDate(issue.start_date);
  minDate?.setDate(minDate.getDate());

  const maxDate = getDate(issue.target_date);
  maxDate?.setDate(maxDate.getDate());

  return (
    <div>
      <h6 className="text-body-xs-medium">{t("common.properties")}</h6>
      <div className={`mt-3 w-full space-y-3 ${!editPermissions.anyField ? "opacity-60" : ""}`}>
        <SidebarPropertyListItem icon={StatePropertyIcon} label={t("common.state")}>
          <StateDropdown
            value={issue?.state_id}
            onChange={(val) => issueOperations.update(workspaceSlug, projectId, issueId, { state_id: val })}
            projectId={projectId}
            disabled={!editPermissions.state}
            // Only the groups the API accepts, so a client cannot pick one that 400s.
            stateIds={clientWritableStateIds(getProjectStates(projectId), editPermissions)}
            buttonVariant="transparent-with-text"
            className="group w-full grow"
            buttonContainerClassName="w-full text-left h-7.5"
            buttonClassName={`text-body-xs-medium ${issue?.state_id ? "" : "text-placeholder"}`}
            dropdownArrow
            dropdownArrowClassName="h-3.5 w-3.5 hidden group-hover:inline"
          />
        </SidebarPropertyListItem>

        {/* Who, on the client's side, asked for this. D62.
            **The twin of the row in `issue-detail/sidebar.tsx`, and it has to stay a twin.** Two
            panels render a work item's properties in this codebase, and a control added to only
            one of them is a control that is missing wherever the user happened to open the item
            from -- the same miss as the second edit gate this panel needed in Phase 8.
            Hidden rather than disabled: see the note in the sidebar. */}
        {editPermissions.restrictedFields && projectDetails?.service_client && (
          <SidebarPropertyListItem icon={UserRound} label={t("work_item.service_requester.label")}>
            <IssueServiceRequesterProperty workspaceSlug={workspaceSlug} projectId={projectId} issueId={issueId} />
          </SidebarPropertyListItem>
        )}

        <SidebarPropertyListItem icon={MembersPropertyIcon} label={t("common.assignees")}>
          <MemberDropdown
            value={issue?.assignee_ids ?? undefined}
            onChange={(val) => issueOperations.update(workspaceSlug, projectId, issueId, { assignee_ids: val })}
            disabled={!editPermissions.restrictedFields}
            projectId={projectId}
            placeholder={t("issue.add.assignee")}
            multiple
            buttonVariant={issue?.assignee_ids?.length > 1 ? "transparent-without-text" : "transparent-with-text"}
            className="group w-full grow"
            buttonContainerClassName="w-full text-left h-7.5"
            buttonClassName={`text-body-xs-medium justify-between ${issue?.assignee_ids?.length > 0 ? "" : "text-placeholder"}`}
            hideIcon={issue.assignee_ids?.length === 0}
            dropdownArrow
            dropdownArrowClassName="h-3.5 w-3.5 hidden group-hover:inline"
          />
        </SidebarPropertyListItem>

        <SidebarPropertyListItem icon={PriorityPropertyIcon} label={t("common.priority")}>
          <PriorityDropdown
            value={issue?.priority}
            onChange={(val) => issueOperations.update(workspaceSlug, projectId, issueId, { priority: val })}
            disabled={!editPermissions.priority}
            buttonVariant="transparent-with-text"
            className="h-7.5 w-full grow rounded-sm"
            buttonContainerClassName="w-full text-left h-7.5"
            buttonClassName={`text-body-xs-medium whitespace-nowrap [&_svg]:size-3.5 ${!issue?.priority || issue?.priority === "none" ? "text-placeholder" : ""}`}
          />
        </SidebarPropertyListItem>

        {createdByDetails && (
          <SidebarPropertyListItem
            icon={UserCirclePropertyIcon}
            label={t("common.created_by")}
            childrenClassName="px-2"
          >
            <ButtonAvatars
              showTooltip
              userIds={createdByDetails?.display_name?.includes("-intake") ? null : createdByDetails?.id}
            />
            <span className="grow truncate text-body-xs-medium leading-5 text-secondary">
              {createdByDetails?.display_name?.includes("-intake") ? "Plane" : createdByDetails?.display_name}
            </span>
          </SidebarPropertyListItem>
        )}

        <SidebarPropertyListItem icon={StartDatePropertyIcon} label={t("common.order_by.start_date")}>
          <DateDropdown
            value={issue.start_date}
            onChange={(val) =>
              issueOperations.update(workspaceSlug, projectId, issueId, {
                start_date: val ? renderFormattedPayloadDate(val) : null,
              })
            }
            placeholder={t("issue.add.start_date")}
            buttonVariant="transparent-with-text"
            maxDate={maxDate ?? undefined}
            disabled={!editPermissions.restrictedFields}
            className="group w-full grow"
            buttonContainerClassName="w-full text-left h-7.5"
            buttonClassName={`text-body-xs-medium ${issue?.start_date ? "" : "text-placeholder"}`}
            hideIcon
            clearIconClassName="h-3 w-3 hidden group-hover:inline"
          />
        </SidebarPropertyListItem>

        <SidebarPropertyListItem icon={DueDatePropertyIcon} label={t("common.order_by.due_date")}>
          <div className="flex w-full items-center gap-2">
            <DateDropdown
              value={issue.target_date}
              onChange={(val) =>
                issueOperations.update(workspaceSlug, projectId, issueId, {
                  target_date: val ? renderFormattedPayloadDate(val) : null,
                })
              }
              placeholder={t("issue.add.due_date")}
              buttonVariant="transparent-with-text"
              minDate={minDate ?? undefined}
              disabled={!editPermissions.restrictedFields}
              className="group w-full grow"
              buttonContainerClassName="w-full text-left h-7.5"
              buttonClassName={cn("text-body-xs-medium", {
                "text-placeholder": !issue.target_date,
                "text-danger-primary": shouldHighlightIssueDueDate(issue.target_date, stateDetails?.group),
              })}
              hideIcon
              clearIconClassName="h-3 w-3 hidden group-hover:inline text-primary"
            />
          </div>
        </SidebarPropertyListItem>

        {isEstimateEnabled && (
          <SidebarPropertyListItem icon={EstimatePropertyIcon} label={t("common.estimate")}>
            <EstimateDropdown
              value={issue.estimate_point ?? undefined}
              onChange={(val) => issueOperations.update(workspaceSlug, projectId, issueId, { estimate_point: val })}
              projectId={projectId}
              disabled={!editPermissions.restrictedFields}
              buttonVariant="transparent-with-text"
              className="group w-full grow"
              buttonContainerClassName="w-full text-left h-7.5"
              buttonClassName={`text-body-xs-medium ${issue?.estimate_point !== undefined ? "" : "text-placeholder"}`}
              placeholder="None"
              hideIcon
              dropdownArrow
              dropdownArrowClassName="h-3.5 w-3.5 hidden group-hover:inline"
            />
          </SidebarPropertyListItem>
        )}

        {projectDetails?.module_view && (
          <SidebarPropertyListItem icon={ModuleIcon} label={t("common.modules")}>
            <IssueModuleSelect
              className="w-full grow"
              workspaceSlug={workspaceSlug}
              projectId={projectId}
              issueId={issueId}
              issueOperations={issueOperations}
              disabled={!editPermissions.restrictedFields}
            />
          </SidebarPropertyListItem>
        )}

        {projectDetails?.cycle_view && (
          <SidebarPropertyListItem icon={CycleIcon} label={t("common.cycle")} appendElement={null}>
            <IssueCycleSelect
              className="h-7.5 w-full grow"
              workspaceSlug={workspaceSlug}
              projectId={projectId}
              issueId={issueId}
              issueOperations={issueOperations}
              disabled={!editPermissions.restrictedFields}
            />
          </SidebarPropertyListItem>
        )}

        <SidebarPropertyListItem icon={ParentPropertyIcon} label={t("common.parent")}>
          <IssueParentSelectRoot
            className="h-7.5 w-full grow"
            disabled={!editPermissions.restrictedFields}
            issueId={issueId}
            issueOperations={issueOperations}
            projectId={projectId}
            workspaceSlug={workspaceSlug}
          />
        </SidebarPropertyListItem>

        <SidebarPropertyListItem icon={LabelPropertyIcon} label={t("common.labels")}>
          <IssueLabel
            workspaceSlug={workspaceSlug}
            projectId={projectId}
            issueId={issueId}
            disabled={!editPermissions.restrictedFields}
          />
        </SidebarPropertyListItem>
      </div>
    </div>
  );
});
