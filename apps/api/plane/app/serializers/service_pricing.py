# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
from decimal import Decimal

# Third party imports
from rest_framework import serializers

# Module imports
from plane.db.models import ExporterHistory, ServiceClientHourTypeRate, ServiceClientPrice

from .base import BaseSerializer
from .user import UserLiteSerializer


class ServiceClientPriceSerializer(BaseSerializer):
    """One dated price sheet of a client.

    ``service_client`` and ``workspace`` both come from the URL, so neither is in
    ``fields`` -- the same choice ``ServiceContractSerializer`` makes, and for the same
    reason: a body that could name a different client than the path is a body that can
    write another client's prices.
    """

    class Meta:
        model = ServiceClientPrice
        fields = ["id", "starts_on", "base_hour_rate", "notes", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_starts_on(self, value):
        """A vigency starts on the first of a month.

        Also a check constraint in DDL, so this is not the guarantee -- it is the *message*.
        Without it the client would get a Postgres constraint name and a 500; with it they
        get a code they can render. See ``ServiceClientPrice.Meta`` for why the rule exists
        at all: it makes the work log's reading (by ``worked_on``) and the overage's reading
        (by the period's start) coincide by construction.
        """
        if value.day != 1:
            raise serializers.ValidationError("PRICE_VIGENCY_MUST_START_ON_THE_FIRST_OF_A_MONTH")

        return value

    def validate_base_hour_rate(self, value):
        """Zero is refused, and it is decision D20 rather than hygiene.

        A rate of zero would be a second mechanism for "do not charge", and this system has
        exactly one: a billing type whose route is ``NON_BILLABLE``. Two mechanisms means a
        report that filters on the route silently misses half the free hours.
        """
        if value <= Decimal("0.00"):
            raise serializers.ValidationError("BASE_HOUR_RATE_MUST_BE_POSITIVE")

        return value


class ServiceClientHourTypeRateSerializer(BaseSerializer):
    """An absolute rate that replaces ``base * multiplier`` for one hour type.

    ``price`` comes from the URL. ``hour_type`` is writable, and is validated against the
    workspace by the view -- a rate pointing at another workspace's hour type would be
    unreachable configuration that still shows up in an audit trail.
    """

    hour_type_name = serializers.CharField(source="hour_type.name", read_only=True)

    class Meta:
        model = ServiceClientHourTypeRate
        fields = [
            "id",
            "hour_type",
            "hour_type_name",
            "absolute_rate",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_absolute_rate(self, value):
        """D20 again: no second mechanism for free."""
        if value <= Decimal("0.00"):
            raise serializers.ValidationError("ABSOLUTE_RATE_MUST_BE_POSITIVE")

        return value


class ServiceAllowanceOverageRateSerializer(serializers.Serializer):
    """Body for setting a work item allowance's own overage rate.

    A plain ``Serializer``, following the convention every action endpoint in this feature
    uses -- there is no model to bind, only one field to accept.

    ``allow_null`` is deliberate and load bearing: ``None`` **clears** the rate and restores
    the fallback to the client's base rate, which is a real and reversible decision rather
    than a missing value. ``required=False`` would make "clear it" indistinguishable from
    "leave it alone".
    """

    overage_hour_rate = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
        allow_null=True,
    )



class ServiceLogExportRequestSerializer(serializers.Serializer):
    """The body of a work log export request. Section 5, acceptance criterion 11.

    ``year`` and ``month`` are optional and travel together -- ``validate`` refuses one
    without the other, because a "month" with no year is not a competency and exporting
    every March ever recorded is not what anyone asking for March meant.
    """

    provider = serializers.ChoiceField(choices=["csv", "xlsx", "json"], default="csv")
    year = serializers.IntegerField(required=False, min_value=2000, max_value=2999)
    month = serializers.IntegerField(required=False, min_value=1, max_value=12)
    service_client_id = serializers.UUIDField(required=False, allow_null=True)
    project = serializers.ListField(child=serializers.UUIDField(), required=False)

    def validate(self, attrs):
        if ("year" in attrs) != ("month" in attrs):
            raise serializers.ValidationError(
                {"month": "COMPETENCE_REQUIRES_BOTH_YEAR_AND_MONTH"}
            )

        return attrs


class ServiceLogExportHistorySerializer(BaseSerializer):
    """One row of the work log export history.

    Deliberately **not** ``ExporterHistorySerializer``: that one omits ``type``, ``reason``
    and ``filters``. For an export of issues those omissions are harmless, but a billing
    export that failed has to say **why** -- an operator waiting on a month's numbers needs
    the reason, not a status of ``failed`` and silence. ``filters`` says which competency the
    finished file covers, which is the difference between a useful history and a list of
    identical rows.
    """

    initiated_by_detail = UserLiteSerializer(source="initiated_by", read_only=True)

    class Meta:
        model = ExporterHistory
        fields = [
            "id",
            "created_at",
            "updated_at",
            "type",
            "provider",
            "status",
            "reason",
            "url",
            "filters",
            "initiated_by",
            "initiated_by_detail",
            "token",
        ]
        read_only_fields = fields
