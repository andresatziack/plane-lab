# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
from decimal import Decimal

# Third party imports
from rest_framework import serializers

# Module imports
from plane.db.models import ServiceBillingType, ServiceConfigActivity, ServiceHourType
from plane.utils.service_catalog import save_with_config_activity, validate_catalog_update

from .base import BaseSerializer
from .user import UserAdminLiteSerializer


class ServiceCatalogBaseSerializer(BaseSerializer):
    """Shared validation for both catalogues.

    ``workspace`` comes from the URL and never from the payload, and the nullable
    ``project`` field inherited from WorkspaceBaseModel is unused by these models and
    is left out of ``fields`` entirely so it can never be set.
    """

    def _workspace_id(self):
        return self.context.get("workspace_id")

    def validate_name(self, value):
        """Reject a duplicate name with a usable code instead of an IntegrityError.

        The partial unique constraint is the real guard, but letting it fire would
        surface as BaseViewSet's generic "The payload is not valid" 400, which tells
        an admin nothing.
        """
        name = (value or "").strip()

        if not name:
            raise serializers.ValidationError("NAME_IS_REQUIRED")

        queryset = self.Meta.model.objects.filter(workspace_id=self._workspace_id(), name__iexact=name)

        if self.instance:
            queryset = queryset.exclude(pk=self.instance.pk)

        if queryset.exists():
            raise serializers.ValidationError("NAME_ALREADY_EXISTS")

        return name

    def validate(self, attrs):
        """Hold the "exactly one default" invariant on the way in.

        Only applies to edits of an existing row: on create the model forces the
        first option of an empty catalogue to be the default, and no incoming payload
        can produce a catalogue with zero defaults.
        """
        if self.instance:
            error_code = validate_catalog_update(
                self.instance,
                is_default=attrs.get("is_default"),
                is_active=attrs.get("is_active"),
            )

            if error_code:
                raise serializers.ValidationError({"error": error_code})

        return attrs

    def update(self, instance, validated_data):
        """Save through the domain helper so the audit trail is written atomically.

        ``ChangeTrackerMixin`` compares against the values captured when the instance
        was loaded, so the change has to be captured between the setattr loop and the
        save -- which is why this cannot be left to DRF's default implementation.
        """
        for attribute, value in validated_data.items():
            setattr(instance, attribute, value)

        save_with_config_activity(instance, actor=self.context.get("actor"))

        return instance


class ServiceHourTypeSerializer(ServiceCatalogBaseSerializer):
    """Full representation, for the workspace admin panel and the work log form.

    R11 NOTE, for the client portal phase: ``multiplier`` is in this payload and a
    client must never see it. The portal reads the hour type *label* through the work
    log's own client-facing serializer, never through this one, and the catalogue
    endpoints are closed to GUEST for that reason.
    """

    # Declared explicitly so the scale is visible at the API boundary and the error
    # is a clean 400 rather than a database-level failure. Scale fixed by section 4b
    # of the master context: max_digits=4, decimal_places=2.
    multiplier = serializers.DecimalField(max_digits=4, decimal_places=2, min_value=Decimal("0.01"))

    class Meta:
        model = ServiceHourType
        fields = [
            "id",
            "workspace_id",
            "name",
            "description",
            "multiplier",
            "color",
            "sequence",
            "is_active",
            "is_default",
            "external_source",
            "external_id",
            "created_at",
            "updated_at",
            "created_by",
            "updated_by",
        ]
        read_only_fields = ["workspace", "created_by", "updated_by", "deleted_at"]


class ServiceBillingTypeSerializer(ServiceCatalogBaseSerializer):
    """Full representation, for the workspace admin panel and the work log form."""

    class Meta:
        model = ServiceBillingType
        fields = [
            "id",
            "workspace_id",
            "name",
            "description",
            "billing_route",
            "sequence",
            "is_active",
            "is_default",
            "external_source",
            "external_id",
            "created_at",
            "updated_at",
            "created_by",
            "updated_by",
        ]
        read_only_fields = ["workspace", "created_by", "updated_by", "deleted_at"]


class ServiceConfigActivitySerializer(BaseSerializer):
    """Read-only view of the configuration audit trail.

    ``actor`` is expanded rather than returned as a bare UUID. The scenario this
    endpoint serves is someone answering a client who is disputing an invoice; if
    finding out *who* changed the multiplier took a second request, the endpoint
    would not do the job it was built for.

    ``UserAdminLiteSerializer`` and not ``UserLiteSerializer`` because only the former
    carries ``email``, and the endpoint is workspace admin only.
    """

    actor_detail = UserAdminLiteSerializer(source="actor", read_only=True)

    class Meta:
        model = ServiceConfigActivity
        fields = [
            "id",
            "workspace_id",
            "entity_name",
            "entity_identifier",
            # `verb` distinguishes an edit from the creation or removal of the whole row.
            # For a holiday or a classification window, creation and deletion ARE the
            # financial events, so a consumer that ignored this field would miss the
            # entries that matter most in those two tables.
            "verb",
            # Null when verb is created or deleted: those concern the whole row, not one
            # field. The summary is in new_value or old_value respectively.
            "field_name",
            "old_value",
            "new_value",
            "actor",
            "actor_detail",
            "created_at",
        ]
        # The whole model is read only. The trail is append-only and there is no
        # write endpoint at all -- an audit trail that can be edited or deleted is
        # not an audit trail.
        read_only_fields = fields
