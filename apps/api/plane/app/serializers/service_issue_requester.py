# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Third party imports
from rest_framework import serializers

# Module imports
from plane.db.models import ServiceIssueRequester

from .base import BaseSerializer
from .user import UserLiteSerializer


class ServiceIssueRequesterSerializer(BaseSerializer):
    """Who asked for a work item, on read and on write. Section 5, D62.

    ``requester_id`` is write-only and ``requester_detail`` read-only, the shape the rest of
    this fork uses: a caller sends an id, a screen renders a name.

    Carries no client and no contract. Both are properties of the *project* the work item
    lives in (D19), and restating them on the attribution would be a second place they could
    disagree with the project.
    """

    requester_id = serializers.UUIDField(write_only=True)
    requester_detail = UserLiteSerializer(source="requester", read_only=True)

    class Meta:
        model = ServiceIssueRequester
        fields = [
            "id",
            "issue",
            "requester",
            "requester_id",
            "requester_detail",
            "created_at",
            "created_by",
        ]
        read_only_fields = ["id", "issue", "requester", "created_at", "created_by"]
