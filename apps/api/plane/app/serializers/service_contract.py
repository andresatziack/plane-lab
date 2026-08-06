# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Third party imports
from rest_framework import serializers

# Module imports
from plane.db.models import (
    ServiceAccrualCapMode,
    ServiceContract,
    ServiceContractPeriod,
    ServiceHourLedgerEntry,
    ServiceOverageSettlement,
)

from .base import BaseSerializer
from .user import UserAdminLiteSerializer


class ServiceContractSerializer(BaseSerializer):
    """A contract, as the admin panel reads and writes it.

    ``workspace`` comes from the URL and never from the payload, and the nullable
    ``project`` field inherited from ``WorkspaceBaseModel`` is unused by this model and
    is left out of ``fields`` entirely so it can never be set -- the same treatment the
    catalogues give it.

    The accrual cap pair is validated here as well as in DDL. The check constraint is
    the real guard, but letting it fire would surface as a generic 400 that tells an
    admin nothing about which of the two fields to fix.
    """

    service_client_name = serializers.CharField(source="service_client.name", read_only=True)
    accrual_cap_hours = serializers.SerializerMethodField()

    class Meta:
        model = ServiceContract
        fields = [
            "id",
            "service_client",
            "service_client_name",
            "code",
            "name",
            "monthly_hours",
            "starts_on",
            "ends_on",
            "carryover_months",
            "accrual_cap_mode",
            "accrual_cap_value",
            "accrual_cap_hours",
            "high_consumption_threshold_pct",
            "low_consumption_threshold_pct",
            "overage_policy",
            "overage_hour_rate",
            "status",
            "previous_contract",
            "is_default",
            "notes",
            "external_source",
            "external_id",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at", "previous_contract"]

    def get_accrual_cap_hours(self, obj):
        """The ceiling resolved to a number, so the UI never re-implements the enum."""
        cap = obj.accrual_cap_hours()
        return str(cap) if cap is not None else None

    def validate_code(self, value):
        code = (value or "").strip()

        if not code:
            raise serializers.ValidationError("CODE_IS_REQUIRED")

        return code

    def validate(self, attrs):
        """Refuse the two incoherent shapes before the database has to.

        Decision D5: the mode and the value are one fact expressed in two columns, and
        either half without the other is meaningless.
        """
        mode = attrs.get(
            "accrual_cap_mode",
            self.instance.accrual_cap_mode if self.instance else ServiceAccrualCapMode.NONE,
        )
        value = attrs.get(
            "accrual_cap_value", self.instance.accrual_cap_value if self.instance else None
        )

        if mode == ServiceAccrualCapMode.NONE and value is not None:
            raise serializers.ValidationError({"accrual_cap_value": "ACCRUAL_CAP_VALUE_REQUIRES_A_MODE"})

        if mode != ServiceAccrualCapMode.NONE and (value is None or value <= 0):
            raise serializers.ValidationError({"accrual_cap_value": "ACCRUAL_CAP_MODE_REQUIRES_A_POSITIVE_VALUE"})

        starts_on = attrs.get("starts_on", self.instance.starts_on if self.instance else None)
        ends_on = attrs.get("ends_on", self.instance.ends_on if self.instance else None)

        if starts_on and ends_on and ends_on < starts_on:
            raise serializers.ValidationError({"ends_on": "VIGENCY_MUST_END_AFTER_IT_STARTS"})

        return attrs


class ServiceContractLiteSerializer(BaseSerializer):
    """Just enough of a contract to label a period or a pool panel."""

    service_client_name = serializers.CharField(source="service_client.name", read_only=True)

    class Meta:
        model = ServiceContract
        fields = [
            "id",
            "code",
            "name",
            "monthly_hours",
            "status",
            "starts_on",
            "ends_on",
            "service_client",
            "service_client_name",
        ]
        read_only_fields = fields


class ServiceContractPeriodSerializer(BaseSerializer):
    """One competency month, with the two derived quantities spelled out.

    ``granted_hours`` and ``balance_hours`` are model properties rather than columns
    (decision D3), and they are exposed here so no client re-derives them. A client that
    computed ``contracted + carried - consumed`` itself would be a second implementation
    of the one formula this phase most needs to have only once.

    ``contracted_hours`` is **not** writable through this serializer even though
    decision B1 makes it editable, because the edit has to write a ledger row and land
    in the audit trail. That goes through ``update_contracted_hours``, which the
    dedicated endpoint calls.
    """

    contract_detail = ServiceContractLiteSerializer(source="contract", read_only=True)
    granted_hours = serializers.DecimalField(max_digits=10, decimal_places=4, read_only=True)
    balance_hours = serializers.DecimalField(max_digits=10, decimal_places=4, read_only=True)
    closed_by_detail = UserAdminLiteSerializer(source="closed_by", read_only=True)
    competence = serializers.CharField(source="competence_label", read_only=True)

    class Meta:
        model = ServiceContractPeriod
        fields = [
            "id",
            "contract",
            "contract_detail",
            "competence_year",
            "competence_month",
            "competence",
            "starts_on",
            "ends_on",
            "contracted_hours",
            "carried_hours",
            "consumed_hours",
            "granted_hours",
            "balance_hours",
            "discarded_by_cap_hours",
            "overage_hours",
            "overage_settlement",
            "status",
            "closed_at",
            "closed_by",
            "closed_by_detail",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class ServiceHourLedgerEntrySerializer(BaseSerializer):
    """One movement, as the period statement shows it.

    Read only, and there is no writable counterpart anywhere: the table is append-only
    and every row is written by ``plane.utils.service_pool``. A serializer that could
    create one would be a way to move a balance without moving the period's totals.
    """

    origin_competence = serializers.SerializerMethodField()
    actor_detail = UserAdminLiteSerializer(source="actor", read_only=True)

    class Meta:
        model = ServiceHourLedgerEntry
        fields = [
            "id",
            "contract",
            "period",
            "origin_period",
            "origin_competence",
            "hours",
            "entry_type",
            "service_log",
            "actor",
            "actor_detail",
            "notes",
            "created_at",
        ]
        read_only_fields = fields

    def get_origin_competence(self, obj):
        return obj.origin_period.competence_label if obj.origin_period_id else None


class ServiceContractPeriodCloseSerializer(serializers.Serializer):
    """The body of a period close. Section 4, and decision B3.

    ``overage_settlement`` is optional and only consulted when the period actually has a
    deficit; omitted, the contract's ``overage_policy`` decides. Section 4 requires the
    choice to be confirmable period by period, which is why it is accepted here at all
    rather than being read from the contract unconditionally.
    """

    overage_settlement = serializers.ChoiceField(
        choices=ServiceOverageSettlement.choices, required=False, allow_null=True
    )


class ServiceContractedHoursSerializer(serializers.Serializer):
    """The body of a contracted-hours edit on an open period. Decision B1."""

    contracted_hours = serializers.DecimalField(max_digits=10, decimal_places=4, min_value=0)


class ServiceContractRenewalSerializer(serializers.Serializer):
    """The body of a renewal. Section 8, paths (a) and (b).

    ``mode`` picks the path: ``in_place`` extends the contract and carries the balance
    with it, ``expire_balance`` extends nothing and writes the leftover off with an
    audit row. Path (c) is a different shape entirely -- it creates a second contract --
    so it has its own serializer.
    """

    MODE_IN_PLACE = "in_place"
    MODE_EXPIRE_BALANCE = "expire_balance"

    mode = serializers.ChoiceField(choices=[MODE_IN_PLACE, MODE_EXPIRE_BALANCE])
    ends_on = serializers.DateField(required=False)
    monthly_hours = serializers.DecimalField(
        max_digits=10, decimal_places=4, min_value=0, required=False
    )

    def validate(self, attrs):
        if attrs["mode"] == self.MODE_IN_PLACE and not attrs.get("ends_on"):
            raise serializers.ValidationError({"ends_on": "ENDS_ON_IS_REQUIRED_TO_RENEW_IN_PLACE"})

        return attrs


class ServiceContractSuccessorSerializer(serializers.Serializer):
    """The body of "end this contract and create a new one". Section 8c.

    ``balance_destination`` is mandatory and has no default on purpose. The whole point
    of criterion 17 is that somebody decides, on the record, what happens to the
    remaining hours -- a default would let them be disposed of by omission.
    """

    code = serializers.CharField(max_length=100)
    name = serializers.CharField(max_length=255)
    monthly_hours = serializers.DecimalField(max_digits=10, decimal_places=4, min_value=0)
    starts_on = serializers.DateField()
    ends_on = serializers.DateField()
    balance_destination = serializers.ChoiceField(choices=["transfer", "expire", "issue_allowance"])
    target_issue = serializers.UUIDField(required=False, allow_null=True)

    def validate(self, attrs):
        if attrs["ends_on"] < attrs["starts_on"]:
            raise serializers.ValidationError({"ends_on": "VIGENCY_MUST_END_AFTER_IT_STARTS"})

        return attrs


class ServiceAlertDismissalSerializer(serializers.Serializer):
    """The body of an alert dismissal. Section 9, and decision B2."""

    alert_code = serializers.CharField(max_length=64)
