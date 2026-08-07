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

    **``overage_hour_rate`` is removed for anyone who is not a workspace Admin.** R11 keeps
    the value in reais away from technicians, and this endpoint is legitimately open to
    Members -- the balance indicator on the work item is for the technician about to log
    against it. So the hours stay and the price goes, by the same mechanism
    ``ServiceLogSerializer`` uses and with the same fail-safe default: absent unless the
    caller proved otherwise.
    """

    #: Removed for a non-Admin. Kept as a class attribute so the contract test asserts
    #: against the same list the code uses rather than restating it.
    #:
    #: Named ``MONEY_FIELDS`` to match ``ServiceLogSerializer`` after D57 split money from
    #: commercial state. Here the whole set is genuinely money -- an hourly rate -- so the
    #: split changes the name and nothing else. There is no commercial-state counterpart:
    #: an allowance has a ``status``, and that was never restricted.
    MONEY_FIELDS = ("overage_hour_rate",)

    balance_hours = serializers.DecimalField(max_digits=10, decimal_places=4, read_only=True)
    consumed_pct = serializers.DecimalField(
        max_digits=9, decimal_places=2, read_only=True, allow_null=True
    )
    closed_by_detail = UserAdminLiteSerializer(source="closed_by", read_only=True)
    issue_name = serializers.CharField(source="issue.name", read_only=True)

    def __init__(self, *args, **kwargs):
        """Drop the rate unless the context says the caller may see money. See the docstring."""
        super().__init__(*args, **kwargs)

        if not self.context.get("can_see_amounts", False):
            for field in self.MONEY_FIELDS:
                self.fields.pop(field, None)

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
            # Money, Phase 6. Removed from the payload for a non-Admin by `__init__`.
            "overage_hour_rate",
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

    # The rate an hour past this allowance costs, accepted here because the moment somebody
    # decides a project is worth 40 hours is the moment they know what an overage hour costs
    # -- an allowance is a separately negotiated sale, and the client's support price is the
    # wrong price for it.
    #
    # `required=False` and `allow_null=True` mean three distinct things, all of them real:
    # absent leaves the current rate alone, `null` clears it back to the client's base rate,
    # and a value sets it. The dedicated endpoint's serializer makes the field required, so
    # that "clear it" cannot be expressed there by simply omitting it.
    overage_hour_rate = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
        required=False,
        allow_null=True,
    )
