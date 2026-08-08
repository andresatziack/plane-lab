/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// plane imports
import { API_BASE_URL } from "@plane/constants";
import type {
  IServiceMemberCapabilities,
  IServiceMemberPermission,
  IServiceMemberPermissionUpdateResponse,
  TServiceMemberPermissionPayload,
} from "@plane/types";
// services
import { APIService } from "@/services/api.service";

/**
 * The three work log grants of Phase 7. Decision D45.
 *
 * **Note the route asymmetry, which is not a mistake to be tidied up**: the collection URL serves
 * GET and the `<member_id>` URL serves PATCH. Both are the same view class, but its `get` takes
 * only the slug and its `patch` requires the member positionally, so calling either on the wrong
 * URL fails on a missing argument rather than with a clean 405.
 */
export class ServiceMemberPermissionService extends APIService {
  constructor() {
    super(API_BASE_URL);
  }

  /**
   * Every member holding at least one grant. **Workspace ADMIN only**, read as well as write.
   *
   * Members holding nothing are absent rather than present with three false flags -- revocation
   * keeps the row so the audit trail survives, and the server filters those out. Admins are absent
   * too, because they hold all three implicitly with no row.
   */
  async fetchAll(workspaceSlug: string): Promise<IServiceMemberPermission[]> {
    return this.get(`/api/workspaces/${workspaceSlug}/service-member-permissions/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /**
   * The caller's own **resolved** capabilities. ADMIN and MEMBER.
   *
   * Resolved and not stored: an Admin gets all three `true` with no row. This is the call a work
   * log form should make before offering the delegation field, rather than deriving it from a role
   * and having to know about `is_admin OR flag`.
   */
  async fetchMine(workspaceSlug: string): Promise<IServiceMemberCapabilities> {
    return this.get(`/api/workspaces/${workspaceSlug}/service-member-permissions/me/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /**
   * Grant or revoke, one flag at a time or several. **Workspace ADMIN only.**
   *
   * Only the keys present are touched, so a caller changing one flag need not restate the others
   * and cannot revoke a second one by omission.
   *
   * Refuses with **400** `GUEST_CANNOT_HOLD_SERVICE_LOG_PERMISSIONS` for a client user, and
   * `NOT_AN_ACTIVE_WORKSPACE_MEMBER` for somebody who has left. An empty body is
   * `NO_CAPABILITY_SPECIFIED`. All three are 400 rather than 403: the caller is entitled, the
   * target is not eligible.
   *
   * There is no revoke-on-demotion call to make from here -- demoting a member to GUEST revokes
   * all three server side, inside the same request that changes the role.
   */
  async update(
    workspaceSlug: string,
    memberId: string,
    data: TServiceMemberPermissionPayload
  ): Promise<IServiceMemberPermissionUpdateResponse> {
    return this.patch(`/api/workspaces/${workspaceSlug}/service-member-permissions/${memberId}/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }
}
