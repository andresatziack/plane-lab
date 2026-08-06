# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Third party imports
from rest_framework import serializers

# Module imports
from plane.db.models import ServiceBillingType, ServiceHourType, ServiceLog
from plane.utils.service_log import (
    ServiceLogValidationError,
    is_billable_route,
    validate_worked_on,
)
from plane.utils.service_log_time import (
    InvalidDurationError,
    duration_from_interval,
    format_duration,
    format_hours,
    parse_duration,
)

from .base import BaseSerializer
from .user import UserLiteSerializer


class ServiceLogSerializer(BaseSerializer):
    """A work log as the technician and the admin see it. Rule R11.

    Carries all four time quantities plus the multiplier, which means **this
    serializer must never be used by a client-facing endpoint**. R11(b) is explicit
    that the restriction is the serializer's job and not the interface's: "Esconder na
    interface e mandar no payload é vazamento". The client portal phase adds its own
    serializer that omits ``raw_duration_minutes``, ``logged_hours`` and
    ``applied_multiplier`` -- see ``ServiceLogClientSerializer`` below, which is the
    shape it starts from.

    The catalogue labels are resolved through ``all_objects``. The foreign keys are
    ``DO_NOTHING``, so they can point at a soft deleted option, and the default
    manager filters those out -- following the forward descriptor would raise
    ``DoesNotExist`` on exactly the historical rows Phase 2's criterion 3 is about.
    """

    author_detail = UserLiteSerializer(source="author", read_only=True)

    hour_type_name = serializers.SerializerMethodField()
    hour_type_color = serializers.SerializerMethodField()
    billing_type_name = serializers.SerializerMethodField()
    suggested_hour_type_name = serializers.SerializerMethodField()

    # Derived, and deliberately not persisted -- R3 requires the decimal to be the
    # source of truth and the readable form to be computed. Sent from the server so
    # the pt-BR rendering has one implementation and cannot drift between the list,
    # a report and an export.
    raw_duration_display = serializers.SerializerMethodField()
    logged_hours_display = serializers.SerializerMethodField()
    equivalent_hours_display = serializers.SerializerMethodField()

    is_billable = serializers.SerializerMethodField()

    class Meta:
        model = ServiceLog
        fields = [
            "id",
            "issue",
            "project_id",
            "workspace_id",
            "author",
            "author_detail",
            "worked_on",
            "description",
            "entry_mode",
            "start_time",
            "end_time",
            "source",
            # The four quantities of section 4, all four distinct.
            "raw_duration_minutes",
            "logged_hours",
            "equivalent_hours",
            "debited_hours",
            # R4 snapshots.
            "applied_multiplier",
            "applied_billing_route",
            "hour_type",
            "hour_type_name",
            "hour_type_color",
            "billing_type",
            "billing_type_name",
            "suggested_hour_type",
            "suggested_hour_type_name",
            "is_hour_type_overridden",
            "classification_reason",
            "batch_id",
            "segment_index",
            "is_billable",
            "raw_duration_display",
            "logged_hours_display",
            "equivalent_hours_display",
            "created_at",
            "updated_at",
            "created_by",
            "updated_by",
        ]
        # Everything computed is read only. A work log is never edited field by field:
        # an edit re-runs the whole pipeline through the domain layer, because changing
        # the duration or the hour type changes all three hour quantities and possibly
        # the number of segments.
        read_only_fields = [
            "id",
            "issue",
            "project_id",
            "workspace_id",
            "author",
            "source",
            "raw_duration_minutes",
            "logged_hours",
            "equivalent_hours",
            "debited_hours",
            "applied_multiplier",
            "applied_billing_route",
            "suggested_hour_type",
            "is_hour_type_overridden",
            "classification_reason",
            "batch_id",
            "segment_index",
            "created_at",
            "updated_at",
            "created_by",
            "updated_by",
        ]

    def _hour_type(self, obj, field="hour_type_id"):
        identifier = getattr(obj, field, None)
        if not identifier:
            return None
        return ServiceHourType.all_objects.filter(pk=identifier).first()

    def get_hour_type_name(self, obj):
        hour_type = self._hour_type(obj)
        return hour_type.name if hour_type else None

    def get_hour_type_color(self, obj):
        hour_type = self._hour_type(obj)
        return hour_type.color if hour_type else None

    def get_suggested_hour_type_name(self, obj):
        hour_type = self._hour_type(obj, field="suggested_hour_type_id")
        return hour_type.name if hour_type else None

    def get_billing_type_name(self, obj):
        if not obj.billing_type_id:
            return None
        billing_type = ServiceBillingType.all_objects.filter(pk=obj.billing_type_id).first()
        return billing_type.name if billing_type else None

    def get_is_billable(self, obj):
        """Drives the "Garantia" / "Cortesia" badge in the list. Section 6.

        Read from the snapshotted route, not from the billing type. A catalogue edit
        must not change what an existing row says it did (R4).
        """
        return is_billable_route(obj.applied_billing_route)

    def get_raw_duration_display(self, obj):
        return format_duration(obj.raw_duration_minutes)

    def get_logged_hours_display(self, obj):
        return format_duration(int(obj.logged_hours * 60))

    def get_equivalent_hours_display(self, obj):
        return format_hours(obj.equivalent_hours)


class ServiceLogClientSerializer(BaseSerializer):
    """A work log as the *client* is allowed to see it. Rule R11.

    Not routed anywhere in this phase -- section 6 of the phase brief says "não expor
    apontamento a papel GUEST em nenhum endpoint", and the client portal is Phase 8.

    It exists now because R11(b) makes the field allowlist a serializer concern, and
    writing it while the rule is in front of me is safer than leaving Phase 8 to
    rediscover which fields leak. What must stay absent, per R11's table:

    * ``raw_duration_minutes`` -- what the technician typed
    * ``logged_hours`` -- the chronological time before the multiplier
    * ``applied_multiplier`` -- the numeric multiplier
    * money, once Phase 6 adds it, except for ad-hoc clients

    What the client does see is ``equivalent_hours``, which R11(c) warns must never be
    labelled "horas trabalhadas" -- an hour worked after hours shows as 1,5h, and the
    wrong label turns a contractual rule into an accusation of inflated hours.
    """

    author_detail = UserLiteSerializer(source="author", read_only=True)
    hour_type_name = serializers.SerializerMethodField()
    billing_type_name = serializers.SerializerMethodField()
    equivalent_hours_display = serializers.SerializerMethodField()

    class Meta:
        model = ServiceLog
        fields = [
            "id",
            "issue",
            "worked_on",
            "description",
            "author",
            "author_detail",
            "equivalent_hours",
            "equivalent_hours_display",
            "debited_hours",
            "hour_type_name",
            "billing_type_name",
            "created_at",
        ]
        read_only_fields = fields

    def get_hour_type_name(self, obj):
        if not obj.hour_type_id:
            return None
        hour_type = ServiceHourType.all_objects.filter(pk=obj.hour_type_id).first()
        return hour_type.name if hour_type else None

    def get_billing_type_name(self, obj):
        if not obj.billing_type_id:
            return None
        billing_type = ServiceBillingType.all_objects.filter(pk=obj.billing_type_id).first()
        return billing_type.name if billing_type else None

    def get_equivalent_hours_display(self, obj):
        return format_hours(obj.equivalent_hours)


class ServiceLogWriteSerializer(serializers.Serializer):
    """Validates a work log submission. Not a ModelSerializer, on purpose.

    One submission can produce several rows (R10 splits an interval into segments), so
    there is no one-to-one mapping between payload and instance for a ModelSerializer
    to manage. This validates and normalises the input; ``plane.utils.service_log``
    turns it into rows.

    The duration arrives as free text (R1) and is parsed here so the error is a clean
    400 with an UPPER_SNAKE code rather than an exception from the domain layer.
    """

    worked_on = serializers.DateField()
    description = serializers.CharField(allow_blank=False, trim_whitespace=True)
    entry_mode = serializers.ChoiceField(choices=ServiceLog.EntryMode.choices)

    # Duration mode. Free text per R1: "1h 15min", "1,5h", "90min".
    #
    # Blank and null are both accepted at the field level and rejected in validate()
    # only when the mode actually needs them. A form toggling from duration to interval
    # will send the abandoned field as "" or null, and refusing that would be a
    # validation error about a field the technician cannot even see.
    duration = serializers.CharField(required=False, allow_blank=True, allow_null=True)

    # Interval mode.
    start_time = serializers.TimeField(required=False, allow_null=True)
    end_time = serializers.TimeField(required=False, allow_null=True)

    hour_type_id = serializers.UUIDField(required=False, allow_null=True)
    billing_type_id = serializers.UUIDField()

    # Delegation is Phase 7. Accepted here so the field exists at the boundary, but
    # the view refuses any value other than the requesting user until that phase adds
    # the permission that makes delegating legitimate.
    author_id = serializers.UUIDField(required=False, allow_null=True)

    def validate(self, attrs):
        """Cross-field rules: mode coherence, then the duration itself.

        Resolves the raw duration for both modes so that R9's promise -- the two modes
        converge on one duration -- is kept in one place instead of at each call site.
        """
        entry_mode = attrs["entry_mode"]

        if entry_mode == ServiceLog.EntryMode.INTERVAL:
            if not attrs.get("start_time") or not attrs.get("end_time"):
                raise serializers.ValidationError({"error": "INTERVAL_REQUIRES_START_AND_END_TIME"})

            try:
                attrs["raw_duration_minutes"] = duration_from_interval(
                    attrs["start_time"], attrs["end_time"]
                )
            except InvalidDurationError as error:
                raise serializers.ValidationError({"error": error.code}) from error
        else:
            if not (attrs.get("duration") or "").strip():
                raise serializers.ValidationError({"error": "DURATION_IS_REQUIRED"})

            try:
                attrs["raw_duration_minutes"] = parse_duration(attrs["duration"])
            except InvalidDurationError as error:
                # Acceptance criterion 5: "banana" blocks the submission with a clear
                # message. The code is translated by the frontend.
                raise serializers.ValidationError({"error": error.code}) from error

            # Duration mode knows nothing about when the work happened, so the times
            # are dropped rather than stored misleadingly. The model's check
            # constraint would reject them anyway.
            attrs["start_time"] = None
            attrs["end_time"] = None

        workspace = self.context.get("workspace")

        if workspace is not None:
            try:
                validate_worked_on(attrs["worked_on"], workspace)
            except ServiceLogValidationError as error:
                raise serializers.ValidationError({"error": error.code}) from error

        return attrs
