# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Third party imports
from rest_framework import serializers

# Module imports
from plane.db.models import Project, ServiceClient
from plane.utils.tax_id import is_valid_cnpj, normalize_cnpj

from .base import BaseSerializer


class ServiceClientSerializer(BaseSerializer):
    """Full representation, used by the workspace admin panel."""

    # Number of projects currently linked. Annotated by the viewset queryset; the
    # default keeps the serializer usable when it is not.
    project_count = serializers.IntegerField(read_only=True, default=0)

    # Declared explicitly so that masked input is accepted. The model column is 14
    # characters, and a masked CNPJ such as "11.222.333/0001-81" is 18, so the
    # length inferred from the model would reject it before validate_tax_id ever
    # got the chance to strip the mask.
    tax_id = serializers.CharField(max_length=18, required=False, allow_null=True, allow_blank=True)

    class Meta:
        model = ServiceClient
        fields = [
            "id",
            "workspace_id",
            "name",
            "trade_name",
            "tax_id",
            "is_active",
            "default_billing_type",
            "parent",
            "contact_name",
            "contact_email",
            "contact_phone",
            "notes",
            "project_count",
            "created_at",
            "updated_at",
            "created_by",
            "updated_by",
        ]
        # workspace comes from the URL, never from the payload. The inherited
        # nullable `project` field is unused by this model and deliberately
        # omitted from `fields` above so it can never be set.
        read_only_fields = ["workspace", "created_by", "updated_by", "deleted_at"]

    def validate_tax_id(self, value):
        """Normalize and validate the CNPJ, accepting both formats.

        Empty input is stored as NULL rather than "" so that the partial unique
        constraint on (workspace, tax_id) does not treat several blank clients as
        duplicates of one another.
        """
        normalized = normalize_cnpj(value)

        if normalized is None:
            return None

        if not is_valid_cnpj(normalized):
            raise serializers.ValidationError("INVALID_CNPJ")

        return normalized

    def validate_default_billing_type(self, value):
        """Keep the default billing type inside the same workspace and usable.

        Replaces the ``default_billing_mode`` enum this serializer exposed before the
        catalogue existed. An inactive type is refused rather than silently ignored:
        the resolver would fall back to the catalogue default, so accepting it would
        set a default that never takes effect.
        """
        if value is None:
            return None

        workspace_id = self.context.get("workspace_id")
        if workspace_id and str(value.workspace_id) != str(workspace_id):
            raise serializers.ValidationError("BILLING_TYPE_MUST_BELONG_TO_SAME_WORKSPACE")

        if not value.is_active:
            raise serializers.ValidationError("BILLING_TYPE_IS_INACTIVE")

        return value

    def validate_parent(self, value):
        """Keep the corporate parent inside the same workspace and acyclic.

        This field carries no business behaviour (D18), but a parent pointing at
        another workspace would leak a client across workspaces through the
        expanded representation, and a cycle would hang any future group report.
        """
        if value is None:
            return None

        workspace_id = self.context.get("workspace_id")
        if workspace_id and str(value.workspace_id) != str(workspace_id):
            raise serializers.ValidationError("PARENT_MUST_BELONG_TO_SAME_WORKSPACE")

        if self.instance:
            if value.id == self.instance.id:
                raise serializers.ValidationError("PARENT_CANNOT_BE_SELF")

            # Walk up the chain to reject cycles, e.g. A -> B -> A.
            seen = {self.instance.id}
            ancestor = value
            while ancestor is not None:
                if ancestor.id in seen:
                    raise serializers.ValidationError("PARENT_WOULD_CREATE_A_CYCLE")
                seen.add(ancestor.id)
                ancestor = ancestor.parent

        return value


class ServiceClientLiteSerializer(BaseSerializer):
    """Minimal representation for embedding elsewhere.

    This is what work items expose as their derived client, and what the project
    settings screen shows. It carries no billing configuration, so it is safe to
    embed in responses read by any project member.
    """

    class Meta:
        model = ServiceClient
        fields = ["id", "name", "trade_name", "is_active"]
        read_only_fields = fields


class ServiceClientProjectAssignSerializer(serializers.Serializer):
    """Payload for the bulk assignment action in the admin panel."""

    project_ids = serializers.ListField(child=serializers.UUIDField(), allow_empty=False)
    guest_view_all_features = serializers.BooleanField(required=False)
    is_time_tracking_enabled = serializers.BooleanField(required=False)

    def validate_project_ids(self, value):
        # Deduplicate while preserving order so the response count matches the
        # number of projects actually touched.
        return list(dict.fromkeys(value))


class ProjectLiteWithServiceClientSerializer(BaseSerializer):
    """Project identity plus its client, for the admin panel's project list."""

    service_client_detail = ServiceClientLiteSerializer(source="service_client", read_only=True)

    class Meta:
        model = Project
        fields = [
            "id",
            "name",
            "identifier",
            "logo_props",
            "service_client",
            "service_client_detail",
            "guest_view_all_features",
            "is_time_tracking_enabled",
        ]
        read_only_fields = fields
