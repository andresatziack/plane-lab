# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
from decimal import Decimal

# Third party imports
from rest_framework import serializers

# Module imports
from plane.db.models import ServiceIssueAllowance

from .base import BaseSerializer
from .user import UserAdminLiteSerializer


class ServiceIssueAllowanceSerializer(BaseSerializer):
    """A work item allowance, as the work item panel reads it.

    ``balance_hours`` and ``consumed_pct`` are model properties rather than columns
    (decision D3) and are exposed here so that no client re-derives them. A browser
    computing ``credited - consumed`` itself would be a second implementation of the one
    subtraction this phase most needs to have only once -- and the percentage is the one
    the UI would get wrong, because zero credited hours has **no** percentage rather than
    a percentage of zero.

    **Read only, entirely.** Every hour figure here moves only through
    ``plane.utils.service_allowance``, which writes the ledger row in the same
    transaction. A writable ``credited_hours`` would be a way to move a balance without
    an audit row, which is exactly what decision D23 exists to prevent. The credit and
    close actions have their own serializers below and their own endpoints.
    """

    balance_hours = serializers.DecimalField(max_digits=10, decimal_places=4, read_only=True)
    consumed_pct = serializers.DecimalField(
        max_digits=9, decimal_places=2, read_only=True, allow_null=True
    )
    closed_by_detail = UserAdminLiteSerializer(source="closed_by", read_only=True)
    issue_name = serializers.CharField(source="issue.name", read_only=True)

    class Meta:
        model = ServiceIssueAllowance
        fields = [
            "id",
            "issue",
            "issue_name",
            "project",
            "reference",
            "notes",
            "credited_hours",
            "consumed_hours",
            "balance_hours",
            "consumed_pct",
            "overage_hours",
            "expired_hours",
            "status",
            "closed_at",
            "closed_by",
            "closed_by_detail",
            "external_source",
            "external_id",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class ServiceIssueAllowanceCreditSerializer(serializers.Serializer):
    """The body of a credit. Acceptance criteria 1 and 5.

    One serializer for both the first credit and every later one, because they are the
    same operation: criterion 5's aditivo de escopo is a second ``CREDIT`` row on the same
    allowance, never a second allowance.

    ``hours`` has ``min_value`` above zero rather than at zero. A credit of nothing is not
    a no-op worth accepting -- it would write a ledger row that moves no balance, which is
    noise in the one table that has to be readable line by line. Removing hours is not a
    negative credit either; that is a close and a settlement.

    ``reference`` and ``notes`` are optional and are applied when present, on the first
    credit or a later one, so the commercial reference can be corrected without a second
    entity and without a separate endpoint.
    """

    # A Decimal bound, not a float: a float bound on a Decimal field is how a rounding
    # difference gets into a validator.
    hours = serializers.DecimalField(max_digits=10, decimal_places=4, min_value=Decimal("0.0001"))
    reference = serializers.CharField(max_length=255, required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)
