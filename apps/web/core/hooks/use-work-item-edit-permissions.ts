/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import type { IState, TStateGroups } from "@plane/types";
import { useUserPermissions } from "@/hooks/store/user";

/**
 * Which fields of a work item this user may change. Decision D64.
 *
 * **Why this is not a boolean.** A client's own user is a GUEST of the Cliente's project and
 * may change the priority and the state -- and nothing else. `allowPermissions` does plain
 * array membership with no `>=` hierarchy, so adding `GUEST` to the single `isEditable` gate
 * would have enabled the title, the description, the responsible party, the labels, the
 * dates, the estimate, the parent, the widgets and the work log section along with it.
 *
 * So the gate stops being one boolean and becomes one boolean *per field group*, each built
 * from its own independent `allowPermissions` call. Independent calls can only narrow: there
 * is no path by which granting a client the state also grants them the parent.
 *
 * Mirrors `CLIENT_WRITABLE_ISSUE_FIELDS` in `plane/utils/service_portal.py`. The backend is
 * the authority -- this is the affordance. If the two ever disagree the API refuses, which is
 * the correct direction to fail.
 */
export type TWorkItemEditPermissions = {
  /**
   * Title, description, work item type, assignees, labels, dates, estimate, parent, modules,
   * cycle, the detail widgets and the work log section. Everything R11 and the field allowlist
   * keep away from a client.
   */
  restrictedFields: boolean;
  /** Also open to a client's own user: closing and reopening. */
  state: boolean;
  /** Also open to a client's own user: how urgent it is. */
  priority: boolean;
  /**
   * Whether *any* field is editable. Used only for container dimming -- a client must not see
   * the whole property panel greyed out when two of its controls are live.
   */
  anyField: boolean;
};

/**
 * The state groups a client's own user may move a work item into.
 *
 * Mirrors `CLIENT_ALLOWED_STATE_GROUPS` on the backend, which refuses anything else with a
 * 400. Kept here so the dropdown never *offers* a state the API would reject: a client
 * choosing "Backlog" and receiving an error is a worse outcome than not being offered it.
 *
 * `triage` is absent from `TStateGroups` altogether, so only `backlog` has to be excluded
 * explicitly.
 */
export const CLIENT_WRITABLE_STATE_GROUPS: TStateGroups[] = ["unstarted", "started", "completed", "cancelled"];

/**
 * The subset of a project's states a client may choose, as ids for `StateDropdown`.
 *
 * Returns `undefined` when the caller may edit the restricted fields too, which is
 * `StateDropdown`'s own "no filter" value -- a technician keeps the full list, including
 * backlog.
 */
export const clientWritableStateIds = (
  states: IState[] | undefined,
  permissions: TWorkItemEditPermissions
): string[] | undefined => {
  if (permissions.restrictedFields) return undefined;

  return (states ?? []).filter((state) => CLIENT_WRITABLE_STATE_GROUPS.includes(state.group)).map((state) => state.id);
};

export const useWorkItemEditPermissions = (
  workspaceSlug: string | undefined,
  projectId: string | undefined
): TWorkItemEditPermissions => {
  const { allowPermissions } = useUserPermissions();

  const restrictedFields = allowPermissions(
    [EUserPermissions.ADMIN, EUserPermissions.MEMBER],
    EUserPermissionsLevel.PROJECT,
    workspaceSlug,
    projectId
  );

  // The two the client may touch. A separate call rather than `restrictedFields || isGuest`,
  // so that each field group states its own permitted roles and neither can widen the other.
  const clientWritable = allowPermissions(
    [EUserPermissions.ADMIN, EUserPermissions.MEMBER, EUserPermissions.GUEST],
    EUserPermissionsLevel.PROJECT,
    workspaceSlug,
    projectId
  );

  return {
    restrictedFields,
    state: clientWritable,
    priority: clientWritable,
    anyField: restrictedFields || clientWritable,
  };
};

/**
 * The same permissions with every field closed once the work item is archived.
 *
 * Exists because the sidebar takes no `isArchived` prop of its own and has always received a
 * pre-combined boolean. Folding the archive check in here keeps that shape and keeps the
 * combination in one place, rather than repeating `&& !isArchived` beside eleven controls.
 */
export const withArchived = (permissions: TWorkItemEditPermissions, isArchived: boolean): TWorkItemEditPermissions => {
  if (!isArchived) return permissions;

  return { restrictedFields: false, state: false, priority: false, anyField: false };
};
