# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Third party imports
from rest_framework import serializers

# Module imports
from plane.db.models import ServiceMemberPermission
from plane.utils.service_permission import CAPABILITY_FIELDS

from .base import BaseSerializer
from .user import UserLiteSerializer


class ServiceMemberPermissionSerializer(BaseSerializer):
    """The three elevated work log capabilities held by one member.

    Read shape only -- writes go through
    ``plane.utils.service_permission.set_member_capabilities``, never through
    ``serializer.save()``. That is not ceremony: the write has to refuse a GUEST target
    and record every transition in ``ServiceConfigActivity``, and a ``ModelSerializer``
    that could save directly would be a second write path with neither guard. The three
    booleans are therefore read-only here, and ``ServiceMemberPermissionWriteSerializer``
    below validates the input.
    """

    member_detail = UserLiteSerializer(source="member", read_only=True)

    class Meta:
        model = ServiceMemberPermission
        fields = [
            "id",
            "workspace",
            "member",
            "member_detail",
            *CAPABILITY_FIELDS,
            "created_at",
            "updated_at",
            "created_by",
            "updated_by",
        ]
        read_only_fields = fields


class ServiceMemberPermissionWriteSerializer(serializers.Serializer):
    """Validates a grant or revocation.

    All three flags are optional so the client can PATCH one without restating the other
    two -- the capabilities are independent (D45), and forcing a caller to resend the
    others would make a stale form silently revoke something.

    Not a ``ModelSerializer``: the target is addressed by *member*, and the row may not
    exist yet. See ``set_member_capabilities``, which upserts.
    """

    can_manage_others = serializers.BooleanField(required=False)
    can_delegate = serializers.BooleanField(required=False)
    can_reassign_author = serializers.BooleanField(required=False)

    def validate(self, attrs):
        """Refuse a PATCH that names no capability at all.

        An empty body would resolve to "change nothing" and return 200, which reads as a
        successful grant to anything driving this from a form.
        """
        if not any(name in attrs for name in CAPABILITY_FIELDS):
            raise serializers.ValidationError({"error": "NO_CAPABILITY_SPECIFIED"})

        return attrs
