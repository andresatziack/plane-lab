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

    **``filters`` was added by Phase 9 and is what closes acceptance criterion 9.** It takes a
    whole ``ServiceLogFilterSet`` as query-parameter shaped keys, so an export can be the
    *same selection the screen was showing* -- technician, project, hour type, route, a range
    of competencies. Before it, the screen could filter by things the export could not
    express, and "o CSV contém os mesmos números da tela" was a comparison somebody did by
    hand and hoped about.

    The Phase 6 ``year``/``month``/``service_client_id`` trio is kept rather than removed:
    those keys are in ``ExporterHistory.filters`` rows that already exist with finished files
    attached, and ``ServiceLogFilterSet.from_export_filters`` translates them so there is one
    filtering path. Sending both is refused -- two selections in one request has no obvious
    winner, and picking one silently is how an export comes back with the wrong month.
    """

    provider = serializers.ChoiceField(choices=["csv", "xlsx", "json"], default="csv")
    year = serializers.IntegerField(required=False, min_value=2000, max_value=2999)
    month = serializers.IntegerField(required=False, min_value=1, max_value=12)
    service_client_id = serializers.UUIDField(required=False, allow_null=True)
    project = serializers.ListField(child=serializers.UUIDField(), required=False)

    #: A `ServiceLogFilterSet` in its query-parameter form, exactly as `to_params()` emits it.
    filters = serializers.DictField(required=False)

    def validate(self, attrs):
        if ("year" in attrs) != ("month" in attrs):
            raise serializers.ValidationError(
                {"month": "COMPETENCE_REQUIRES_BOTH_YEAR_AND_MONTH"}
            )

        if attrs.get("filters") and ("year" in attrs or "service_client_id" in attrs):
            raise serializers.ValidationError(
                {"filters": "FILTERS_AND_LEGACY_COMPETENCE_ARE_EXCLUSIVE"}
            )

        if attrs.get("filters"):
            # Parsed here so a malformed descriptor is a 400 on the request that sent it,
            # rather than a task that fails minutes later with a status of "failed" and a
            # traceback in the reason column.
            from plane.utils.service_reports_filters import (
                ServiceLogFilterSet,
                ServiceReportFilterError,
            )

            try:
                ServiceLogFilterSet.from_params(attrs["filters"])
            except ServiceReportFilterError as error:
                raise serializers.ValidationError({"filters": error.code}) from error

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
