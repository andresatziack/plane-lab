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
from plane.utils.service_money import format_money

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

    **The money is removed for anyone who is not a workspace Admin.** R11 puts the value
    in reais out of a technician's reach, and R11(b) makes that the serializer's job
    rather than the interface's -- so the monetary keys are *deleted from the payload*,
    not merely hidden by the frontend. The caller passes ``context["can_see_amounts"]``;
    the default is ``False``, because the safe direction for a money field is to be
    absent unless somebody proved otherwise.

    **Money and commercial state are two different sets, and only the first is
    restricted (D57).** Phase 6 put them in one bucket and that classification was too
    wide. ``settled_billing_route``, ``route_deviation_reason`` and
    ``pricing_failure_reason`` do not reveal a value, a rate or a multiplier -- they say
    *which commercial situation* the row landed in. Three things show the wide version
    was wrong:

    * Phase 4 section 9 defines the attention panel as visible **to technicians**, and it
      already shows negative balance and contract status. If the route were money-class,
      that panel would have been violating R11 since Phase 4.
    * R11 lets even the **client** see the Tipo de Atendimento, and the route is a
      property of the Tipo de Atendimento. So the route was never secret. What D33 added
      was the *deviation*, which is commercial state, not price.
    * R11 names what a technician does not see: "Valor em R$". It does not name the route.

    Operationally, hiding it is worse than showing it: a technician who cannot see that
    the client's contract has expired cannot warn anybody before spending another ten
    hours that will land as ad-hoc billing.
    """

    #: Value, rate, rate basis. **Removed for a non-Admin.** Kept as a class attribute so
    #: the contract test can assert against the same list the code uses, instead of
    #: restating it and drifting.
    MONEY_FIELDS = (
        "amount",
        "amount_display",
        "applied_hour_rate",
        "applied_rate_basis",
    )

    #: Commercial state. **Visible to a Member** (D57). Not removed by anything; listed
    #: as a class attribute anyway so the contract test can assert their *presence* for a
    #: Member against a named set, which is what stops a future phase from quietly
    #: folding them back into ``MONEY_FIELDS``.
    COMMERCIAL_STATE_FIELDS = (
        "settled_billing_route",
        "route_deviation_reason",
        "pricing_failure_reason",
    )

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

    # Formatted server side for the same reason the hour displays are: the list, the
    # consolidation and the CSV export must agree on a thousands separator, and three
    # formatters is three chances to disagree.
    amount_display = serializers.SerializerMethodField()

    def __init__(self, *args, **kwargs):
        """Remove the monetary fields unless the context says the caller may see them.

        Done here rather than with a second serializer class because the same endpoint
        serves both audiences: an Admin and a Member both GET the work item's logs, and
        branching on the class in every view is a branch each new view can forget. Removing
        from ``self.fields`` fails safe -- a view that passes no context gets no money.
        """
        super().__init__(*args, **kwargs)

        if not self.context.get("can_see_amounts", False):
            for field in self.MONEY_FIELDS:
                self.fields.pop(field, None)

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
            # Commercial state, Phase 6 + D33. Stays for a Member -- see
            # COMMERCIAL_STATE_FIELDS and D57 in the class docstring.
            "settled_billing_route",
            "route_deviation_reason",
            "pricing_failure_reason",
            # The money, Phase 6. Removed from the payload for a non-Admin by `__init__`
            # -- see MONEY_FIELDS.
            "applied_hour_rate",
            "applied_rate_basis",
            "amount",
            "amount_display",
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
            # Every monetary column is read only. The value is derived by the domain layer
            # from the price sheet in force on `worked_on`; a writable amount would be a
            # second way to set a price, and it would bypass both the R4 snapshot and the
            # check constraints that keep a pool debit and a charge mutually exclusive.
            "settled_billing_route",
            "route_deviation_reason",
            "applied_hour_rate",
            "applied_rate_basis",
            "amount",
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

    def get_amount_display(self, obj):
        """The value as pt-BR currency, or ``None`` when the row carries no money.

        ``None`` rather than ``"R$ 0,00"`` on purpose: a row with no amount and a row
        genuinely worth nothing are different facts, and rendering both as R$ 0,00 would
        make a missing price sheet look like a completed calculation. The reason for the
        absence travels in ``pricing_failure_reason``.
        """
        if obj.applied_hour_rate is None:
            return None

        return format_money(obj.amount)


class ServiceLogClientSerializer(BaseSerializer):
    """A work log as the *client* is allowed to see it. Rule R11.

    Routed by Phase 8 at ``.../issues/<issue_id>/service-logs/client/``. Written in Phase
    6 because R11(b) makes the field allowlist a serializer concern, and writing it while
    the rule was in front of that phase was safer than leaving this one to rediscover
    which fields leak.

    What must stay absent, per R11's table:

    * ``raw_duration_minutes`` -- what the technician typed
    * ``logged_hours`` -- the chronological time before the multiplier
    * ``applied_multiplier`` -- the numeric multiplier

    Those three are absent by construction rather than stripped: they are not in
    ``Meta.fields``, so no context flag can restore them and no caller can ask for them.
    That is the difference from ``ServiceLogSerializer``, which owns them and removes the
    monetary subset conditionally.

    ``logged_hours`` is the one to guard hardest, and not because it is the largest
    number. The client sees ``equivalent_hours`` legitimately; given both, one division
    returns the multiplier. R11(c) exists to prevent exactly that derivation.

    What the client does see is ``equivalent_hours``, which R11(c) warns must never be
    labelled "horas trabalhadas" -- an hour worked after hours shows as 1,5h, and the
    wrong label turns a contractual rule into an accusation of inflated hours.

    **Money is present per row, not per client. D58.** R11's table abbreviates the rule as
    "só se avulso", but section 4 of the Phase 6 brief is the more specific text: in a
    contract client, hours only -- *except* for logs on the FATURA_REAIS route recorded
    outside the contract's scope, and for invoiced overage. So the deciding fact is the
    settled route of the individual row (D44: settled is the route actually applied, not
    the one chosen), and a client-level flag would answer the wrong question. ``avulso``
    is not a field on ``ServiceClient`` in any case -- that enum became
    ``default_billing_type`` in Phase 2.

    The criterion that makes this the only defensible reading: the client sees money
    exactly where they will receive an invoice line. Showing a value for something they
    will not be charged and hiding a value for something they will are both wrong.

    **Commercial state: two of the three, deliberately. D65.** ``settled_billing_route``
    and ``route_deviation_reason`` are shown, because when the amount is visible the
    client will ask why they are being charged despite holding a contract, and "contract
    expired" is the answer. Showing the charge and hiding its reason is the same error
    shape D58 rejects. ``pricing_failure_reason`` is *not* shown: it names an internal
    configuration gap ("no price sheet for this client") on a row that is not billable
    yet, so there is nothing for the client to act on and the value would only invite a
    question the client cannot answer.
    """

    #: Money, present only when this row's settled route bills the client. D58.
    #:
    #: Named as a class attribute for the reason ``ServiceLogSerializer.MONEY_FIELDS``
    #: gives: the contract test asserts against the same tuple the code uses, so a test
    #: cannot keep passing after somebody widens the set.
    CLIENT_MONEY_FIELDS = ("amount", "amount_display")

    #: Commercial state the client does see. D65. ``pricing_failure_reason`` is absent
    #: from this tuple on purpose and is absent from ``Meta.fields`` entirely.
    CLIENT_COMMERCIAL_STATE_FIELDS = ("settled_billing_route", "route_deviation_reason")

    author_detail = UserLiteSerializer(source="author", read_only=True)
    hour_type_name = serializers.SerializerMethodField()
    billing_type_name = serializers.SerializerMethodField()
    equivalent_hours_display = serializers.SerializerMethodField()
    amount_display = serializers.SerializerMethodField()

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
            "settled_billing_route",
            "route_deviation_reason",
            "amount",
            "amount_display",
            "created_at",
        ]
        read_only_fields = fields

    def to_representation(self, instance):
        """Remove the money on a row whose settled route does not bill this client. D58.

        Per row and not per serializer, which is why this is ``to_representation`` and not
        the ``__init__`` field-popping that ``ServiceLogSerializer`` uses: one response can
        legitimately mix a pool-debited row with no amount and a billed row with one. A
        field popped at construction would apply the first row's answer to all of them.

        Absent, not zero. A zero would be a number the client may not have, and
        additionally a false one -- the same argument the aggregation layer makes in
        ``bucket()``.
        """
        data = super().to_representation(instance)

        if instance.settled_billing_route != ServiceBillingType.BillingRoute.BILL_AMOUNT:
            for field in self.CLIENT_MONEY_FIELDS:
                data.pop(field, None)

        return data

    def get_amount_display(self, obj):
        """``None`` rather than "R$ 0,00" when nothing was priced, matching
        ``ServiceLogSerializer.get_amount_display``: an unpriced row and a row priced at
        zero are different facts."""
        if obj.applied_hour_rate is None:
            return None

        return format_money(obj.amount)

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

    # The declared author, which Phase 7 made genuinely usable: a caller holding
    # ``can_delegate`` may name another technician, and ``created_by`` then records who
    # actually typed it (R8's two facts).
    #
    # Validated in the view rather than here, because the answer depends on the caller's
    # capabilities and on the target's workspace role -- neither of which a serializer field
    # can see. On the **edit** route a change of author is refused outright: reassignment
    # has its own route so that it writes no ledger row. See ``reassign_author``.
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
