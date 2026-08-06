# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Third party imports
from rest_framework import serializers

# Module imports
from plane.db.models import (
    ServiceClassificationWindow,
    ServiceHoliday,
    ServiceHolidayScope,
    ServiceHourType,
)
from plane.utils.service_calendar import validate_holiday
from plane.utils.service_log_time import (
    InvalidDurationError,
    clock_to_minutes,
    minutes_to_clock,
)

from .base import BaseSerializer


class ClockField(serializers.Field):
    """A window border, spoken as ``HH:MM`` and stored as minutes since midnight.

    The storage decision -- integer minutes rather than ``TimeField`` -- exists because a
    window has to be able to end at 24:00, which ``datetime.time`` cannot express. That is
    a good reason to store integers and a bad reason to make callers send them, so this
    field is where the two worlds meet: the API is ``"18:00"`` in and ``"18:00"`` out, and
    ``1080`` never crosses the boundary.

    ``"24:00"`` is accepted and returned, and is how a window says "the end of the day".
    """

    def to_representation(self, value):
        return minutes_to_clock(value)

    def to_internal_value(self, data):
        try:
            return clock_to_minutes(data)
        except InvalidDurationError as error:
            raise serializers.ValidationError(error.code) from error


class ServiceHolidaySerializer(BaseSerializer):
    """A day the classification engine treats as a holiday.

    ``workspace`` comes from the URL and never from the payload, and the nullable
    ``project`` field inherited from ``WorkspaceBaseModel`` is unused by this model and is
    left out of ``fields`` entirely so it can never be set.
    """

    class Meta:
        model = ServiceHoliday
        fields = [
            "id",
            "workspace_id",
            "name",
            "date",
            "is_recurring",
            "scope",
            "is_active",
            "external_source",
            "external_id",
            "created_at",
            "updated_at",
            "created_by",
            "updated_by",
        ]
        read_only_fields = ["workspace", "created_by", "updated_by", "deleted_at"]

    def validate_name(self, value):
        name = (value or "").strip()

        if not name:
            raise serializers.ValidationError("NAME_IS_REQUIRED")

        return name

    def validate(self, attrs):
        """Refuse a day already covered, in either direction.

        The database constraint catches two rows with the same stored date and scope. It
        cannot catch a recurrence collision, because those rows hold different dates that
        resolve to the same day, and whether they collide depends on the year -- so the
        check lives in the domain layer and is called from here.
        """
        instance = self.instance

        # Falls back to the MODEL DEFAULT, not to None, and that matters.
        #
        # DRF leaves a field with a model default out of `validated_data` when the payload
        # omits it. Passing None through to `validate_holiday` would make it filter on
        # `scope=None`, match nothing, and silently approve a collision -- so a holiday
        # created without an explicit scope would bypass the recurrence rule entirely. The
        # default has to be resolved here because the instance does not exist yet.
        date = attrs.get("date", getattr(instance, "date", None))
        scope = attrs.get("scope", getattr(instance, "scope", None) or ServiceHolidayScope.NATIONAL)
        is_recurring = attrs.get(
            "is_recurring", getattr(instance, "is_recurring", None) or False
        )

        error_code = validate_holiday(
            self.context.get("workspace_id"),
            date=date,
            scope=scope,
            is_recurring=is_recurring,
            instance=instance,
        )

        if error_code:
            raise serializers.ValidationError({"error": error_code})

        return attrs


class ServiceClassificationWindowSerializer(BaseSerializer):
    """A stretch of a kind of day that maps to one hour type.

    Note what is NOT in ``fields``: ``start_minute`` and ``end_minute``. The integers are
    an implementation detail of the storage, and exposing them alongside the clock strings
    would give callers two ways to say the same thing and a way to disagree with
    themselves.
    """

    start_time = ClockField(source="start_minute")
    end_time = ClockField(source="end_minute")

    hour_type_name = serializers.SerializerMethodField()
    hour_type_color = serializers.SerializerMethodField()
    hour_type_priority = serializers.SerializerMethodField()

    # Derived, and useful enough to be worth sending: the panel needs to show at a glance
    # which windows run past midnight, and computing it in the browser would mean
    # reimplementing the border comparison there.
    crosses_midnight = serializers.BooleanField(read_only=True)
    is_all_day = serializers.BooleanField(read_only=True)
    duration_minutes = serializers.IntegerField(read_only=True)

    class Meta:
        model = ServiceClassificationWindow
        fields = [
            "id",
            "workspace_id",
            "hour_type",
            "hour_type_name",
            "hour_type_color",
            "hour_type_priority",
            "day_scope",
            "start_time",
            "end_time",
            "crosses_midnight",
            "is_all_day",
            "duration_minutes",
            "is_active",
            "created_at",
            "updated_at",
            "created_by",
            "updated_by",
        ]
        read_only_fields = ["workspace", "created_by", "updated_by", "deleted_at"]

    def _hour_type(self, obj):
        """Resolved through ``all_objects``.

        The foreign key is ``DO_NOTHING``, so it can point at a soft deleted hour type.
        Following the forward descriptor would raise ``DoesNotExist`` on exactly the rows an
        admin most needs to see in order to clean them up.
        """
        if not obj.hour_type_id:
            return None
        return ServiceHourType.all_objects.filter(pk=obj.hour_type_id).first()

    def get_hour_type_name(self, obj):
        hour_type = self._hour_type(obj)
        return hour_type.name if hour_type else None

    def get_hour_type_color(self, obj):
        hour_type = self._hour_type(obj)
        return hour_type.color if hour_type else None

    def get_hour_type_priority(self, obj):
        hour_type = self._hour_type(obj)
        return hour_type.priority if hour_type else None

    def validate_hour_type(self, hour_type):
        """Keep the window inside its own workspace's catalogue.

        Same isolation concern as the client link on a project: without this, a crafted
        payload could attach a window to another workspace's hour type and classify work
        against a multiplier its admins cannot see.
        """
        workspace_id = self.context.get("workspace_id")

        if workspace_id and str(hour_type.workspace_id) != str(workspace_id):
            raise serializers.ValidationError("HOUR_TYPE_MUST_BELONG_TO_SAME_WORKSPACE")

        return hour_type

    def validate(self, attrs):
        """Reject a zero-length window before the database does.

        The check constraint is the real guard, but letting it fire would surface as
        ``BaseViewSet``'s generic "payload is not valid" 400, which tells an admin nothing.
        The whole day is ``00:00-24:00``, never ``00:00-00:00``.
        """
        start = attrs.get("start_minute", getattr(self.instance, "start_minute", None))
        end = attrs.get("end_minute", getattr(self.instance, "end_minute", None))

        if start is not None and start == end:
            raise serializers.ValidationError({"error": "WINDOW_BORDERS_MUST_DIFFER"})

        return attrs


class ServiceHolidayBulkImportSerializer(serializers.Serializer):
    """A CSV payload of holidays. Section 1 of the phase brief.

    The content arrives as text rather than as a multipart file upload: an admin pasting a
    year's holidays is the common case, the payloads are a few kilobytes, and text keeps
    the endpoint testable without constructing uploads.

    Expected header: ``name,date,is_recurring,scope``. Parsing and per-row validation live
    in ``plane.utils.service_calendar.parse_holiday_csv`` so they can be unit tested
    without HTTP.
    """

    csv_content = serializers.CharField(allow_blank=False, trim_whitespace=False)
