/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { observer } from "mobx-react";
import useSWR from "swr";
// plane imports
import { EUserPermissions } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import { Tooltip } from "@plane/ui";
// components
import { MemberDropdown } from "@/components/dropdowns/member/dropdown";
// hooks
import { useMember } from "@/hooks/store/use-member";
// services
import { ServiceIssueRequesterService } from "@/services/service-issue-requester.service";

const requesterService = new ServiceIssueRequesterService();

type Props = {
  workspaceSlug: string;
  projectId: string;
  issueId: string;
  disabled?: boolean;
};

/**
 * Who, on the client's side, asked for this work. Decision D62. Phase 8b, criteria 10 to 14.
 *
 * **Setting this grants portal visibility**, which is why it is not a cosmetic label: a ticket a
 * technician opened on the phone has `created_by` set to the technician, so without an
 * attribution the person who actually asked cannot see their own request.
 *
 * **The dropdown offers only GUESTs of this project**, so a valid choice cannot produce the 400
 * the server would answer. Same shape as the state dropdown in Phase 8, which offers a client
 * only the state groups the API accepts. The server remains the authority -- the error is still
 * handled -- but a user should never be able to pick something that will be refused.
 */
export const IssueServiceRequesterProperty = observer(function IssueServiceRequesterProperty(props: Props) {
  const { workspaceSlug, projectId, issueId, disabled = false } = props;
  const { t } = useTranslation();
  const [isSubmitting, setIsSubmitting] = useState(false);
  // store hooks
  const {
    project: { projectMemberMap, getProjectMemberFetchStatus, fetchProjectMembers },
  } = useMember();

  const {
    data: attribution,
    isLoading,
    mutate,
  } = useSWR(
    workspaceSlug && projectId && issueId ? `SERVICE_ISSUE_REQUESTER_${issueId}` : null,
    workspaceSlug && projectId && issueId
      ? () => requesterService.fetchRequester(workspaceSlug, projectId, issueId)
      : null,
    { revalidateOnFocus: false }
  );

  /*
   * Passing `memberIds` explicitly turns OFF the dropdown's own lazy fetch on open -- it only
   * fetches when the prop is nullish -- so the members have to be loaded here. On a project page
   * `ProjectAuthWrapper` has already done it; in a peek opened from a workspace view it has not.
   */
  useSWR(
    workspaceSlug && projectId && !getProjectMemberFetchStatus(projectId) ? `PROJECT_MEMBERS_${projectId}` : null,
    workspaceSlug && projectId ? () => fetchProjectMembers(workspaceSlug, projectId) : null,
    { revalidateIfStale: false, revalidateOnFocus: false }
  );

  /*
   * The client's own users of this project.
   *
   * `original_role ?? role` because the store recomputes `role` for its own purposes while
   * `original_role` holds what the backend said -- the same resolution `getUserProjectRole` does.
   * Reading `role` alone would silently offer or hide the wrong people.
   *
   * Written here rather than with `getProjectMemberIds`, which can only *exclude* guests: that is
   * the assignee dropdown's question, and this is its exact inverse.
   */
  const guestIds = Object.values(projectMemberMap?.[projectId] ?? {})
    .filter((membership) => (membership.original_role ?? membership.role) === EUserPermissions.GUEST)
    .map((membership) => membership.member);

  const requesterId = attribution && "requester" in attribution ? attribution.requester : null;

  /*
   * The current value has to be in the option list, always.
   *
   * `MemberDropdown` resolves its label by looking `value` up in `memberIds`, so a value absent
   * from that list renders as the *placeholder* -- and the placeholder here reads "No requester".
   * A work item with an attribution therefore displayed exactly like one without, next to an
   * avatar of the very person it claimed was not set.
   *
   * `guestIds` comes from the project member map, which loads asynchronously, and this component
   * is reached from routes where `ProjectAuthWrapper` has not fetched it -- `/browse/<KEY>/` is
   * one. Waiting for the fetch would still leave the window; including the value closes it for
   * good, and also covers the legitimate case of a requester who has since stopped being a GUEST
   * of the project, whose name should still render on the ticket they asked for.
   */
  const memberIds = requesterId && !guestIds.includes(requesterId) ? [...guestIds, requesterId] : guestIds;

  const handleChange = async (value: string | null) => {
    if (value === requesterId) return;

    setIsSubmitting(true);
    try {
      // POST replaces, because the server does `update_or_create`. Clearing is the only DELETE.
      if (value) await requesterService.setRequester(workspaceSlug, projectId, issueId, { requester_id: value });
      else await requesterService.removeRequester(workspaceSlug, projectId, issueId);

      await mutate();
    } catch (error) {
      const code = (error as { error?: string } | undefined)?.error;

      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("common.errors.default.title"),
        message:
          code === "NOT_A_CLIENT_USER_OF_THIS_PROJECT"
            ? t("work_item.service_requester.errors.not_a_client_user")
            : t("work_item.service_requester.errors.default"),
      });
    } finally {
      setIsSubmitting(false);
    }
  };

  // A project with no client users yet: say so, rather than offering an empty list that reads
  // like the feature is broken.
  if (!isLoading && guestIds.length === 0 && !requesterId) {
    return (
      <Tooltip tooltipContent={t("work_item.service_requester.tooltip")} position="left">
        <span className="flex h-7.5 w-full cursor-default items-center truncate text-body-xs-regular text-placeholder">
          {t("work_item.service_requester.empty")}
        </span>
      </Tooltip>
    );
  }

  return (
    <MemberDropdown
      value={requesterId}
      onChange={handleChange}
      memberIds={memberIds}
      multiple={false}
      disabled={disabled || isSubmitting}
      placeholder={t("work_item.service_requester.placeholder")}
      tooltipContent={t("work_item.service_requester.tooltip")}
      buttonVariant="transparent-with-text"
      className="group w-full grow"
      buttonContainerClassName="w-full text-left h-7.5"
      buttonClassName={`text-body-xs-regular justify-between ${requesterId ? "" : "text-placeholder"}`}
      hideIcon={!requesterId}
      dropdownArrow
      dropdownArrowClassName="h-3.5 w-3.5 hidden group-hover:inline"
    />
  );
});
