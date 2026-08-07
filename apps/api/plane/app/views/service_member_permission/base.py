# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions import ROLE, allow_permission
from plane.app.serializers import (
    ServiceMemberPermissionSerializer,
    ServiceMemberPermissionWriteSerializer,
)
from plane.db.models import ServiceMemberPermission, Workspace
from plane.utils.service_log import ServiceLogValidationError
from plane.utils.service_permission import (
    CAPABILITY_FIELDS,
    resolve_capabilities,
    set_member_capabilities,
)

from ..base import BaseAPIView


class ServiceMemberPermissionEndpoint(BaseAPIView):
    """Grant and revoke the three elevated work log capabilities. Section 2.

    **Workspace ADMIN only, for both reading and writing.** Reading is restricted as well
    as writing, and that is deliberate: the list of who may edit other people's billing
    records is itself sensitive -- it tells a would-be abuser exactly whose account is
    worth having. The same reasoning already restricts
    ``ServiceConfigActivity``'s read endpoint to ADMIN.

    **There is no new permissions screen.** These controls belong on the workspace members
    screen that already exists, which reaches this endpoint with a second call. The
    operator sees one place; the backend does not have to add three columns to
    ``WorkspaceMember`` and inherit a permanent merge conflict on a core file. See decision
    D45.

    Addressed by **member id**, not by the permission row's id, because on a first grant
    the row does not exist yet -- ``set_member_capabilities`` upserts. Same shape as the
    allowance being addressed through its work item: when there is exactly one row per
    parent, the parent is the identifier, and a collection route would invite a second row.
    """

    def _workspace(self, slug):
        return Workspace.objects.filter(slug=slug).first()

    @allow_permission(allowed_roles=[ROLE.ADMIN], level="WORKSPACE")
    def get(self, request, slug):
        """Every member of this workspace who holds at least one capability.

        Rows whose three flags are all false are **excluded**, even though they exist:
        revoking sets the flags rather than deleting the row (so the audit entries stay
        attached to something), and a member holding nothing is not a grant. Returning them
        would make the admin screen show people who have no elevated access.
        """
        workspace = self._workspace(slug)

        if workspace is None:
            return Response(
                {"error": "The required object does not exist."}, status=status.HTTP_404_NOT_FOUND
            )

        held = ServiceMemberPermission.objects.filter(workspace=workspace).select_related(
            "member", "member__avatar_asset"
        )

        granted = [
            permission
            for permission in held
            if any(getattr(permission, name) for name in CAPABILITY_FIELDS)
        ]

        return Response(
            ServiceMemberPermissionSerializer(granted, many=True).data, status=status.HTTP_200_OK
        )

    @allow_permission(allowed_roles=[ROLE.ADMIN], level="WORKSPACE")
    def patch(self, request, slug, member_id):
        """Grant or revoke, one member at a time, auditing every transition.

        Refuses a GUEST target with ``GUEST_CANNOT_HOLD_SERVICE_LOG_PERMISSIONS``. That
        refusal is the *second* of three layers and not the one that makes the system safe
        -- ``resolve_capabilities`` gates on the live role, so a row that somehow existed
        for a GUEST would still grant nothing. What this layer buys is that the stored data
        never claims something untrue.
        """
        workspace = self._workspace(slug)

        if workspace is None:
            return Response(
                {"error": "The required object does not exist."}, status=status.HTTP_404_NOT_FOUND
            )

        serializer = ServiceMemberPermissionWriteSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            permission = set_member_capabilities(
                workspace_id=workspace.pk,
                member_id=member_id,
                actor=request.user,
                **serializer.validated_data,
            )
        except ServiceLogValidationError as error:
            return Response({"error": error.code}, status=status.HTTP_400_BAD_REQUEST)

        # An unsaved instance comes back when the very first grant asked for nothing -- see
        # `set_member_capabilities`. Reporting the capabilities is still correct and
        # truthful; there is simply no row and no audit entry, because nothing happened.
        return Response(
            {
                "member": str(member_id),
                **{name: getattr(permission, name) for name in CAPABILITY_FIELDS},
            },
            status=status.HTTP_200_OK,
        )


class ServiceMemberPermissionMeEndpoint(BaseAPIView):
    """What the *requesting* user may do with work logs here.

    Open to ADMIN and MEMBER, unlike the endpoint above, because it discloses nothing about
    anybody else -- and the work log form needs it: section 3 of the brief says the "Autor"
    field is shown only to whoever may delegate, and the client cannot know that without
    asking. GUEST is excluded along with every other work log route.

    Returns the **resolved** capabilities, not the stored row, so an ADMIN correctly sees
    all three as true without holding a grant, and a GUEST would see all three false even
    if a row said otherwise. One source of truth, and it is the same function the write
    paths enforce with -- which is what keeps the form's affordances and the server's
    refusals from disagreeing.
    """

    @allow_permission(allowed_roles=[ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def get(self, request, slug):
        capabilities = resolve_capabilities(request.user, slug=slug)

        return Response(
            {
                "is_admin": capabilities.is_admin,
                **{name: getattr(capabilities, name) for name in CAPABILITY_FIELDS},
            },
            status=status.HTTP_200_OK,
        )
