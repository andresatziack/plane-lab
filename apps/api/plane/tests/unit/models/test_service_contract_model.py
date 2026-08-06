# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Model invariants of the contract phase, and decision D4's two mandatory tests.

The check constraints are exercised directly rather than through the domain layer. The
domain layer refuses these shapes too, but a test that only went through it would still
pass if the DDL were dropped -- and DDL is the half that a future bulk ``update()`` or
data migration cannot bypass.
"""

# Python imports
from datetime import date
from decimal import Decimal

# Third party imports
import pytest

# Django imports
from django.db import IntegrityError, models, transaction

# Module imports
from plane.db.models import (
    ServiceAccrualCapMode,
    ServiceConfigActivity,
    ServiceConfigVerb,
    ServiceContract,
    ServiceContractPeriod,
    ServiceHourLedgerEntry,
    ServiceLedgerEntryType,
    ServicePeriodStatus,
)
from plane.tests.factories import (
    ServiceClientFactory,
    ServiceContractFactory,
    UserFactory,
)
from plane.utils.service_catalog import CONFIG_VALUE_UNSET, save_with_config_activity
from plane.utils.service_pool import resolve_period

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def no_celery_dispatch(monkeypatch):
    monkeypatch.setattr("celery.app.task.Task.apply_async", lambda *args, **kwargs: None)


@pytest.fixture
def actor(db):
    return UserFactory()


class TestAccrualCapCoherence:
    """Decision D5: the incoherent state must be unrepresentable, not merely refused."""

    def test_a_mode_of_none_with_a_value_is_refused_by_the_database(self, db):
        service_client = ServiceClientFactory(name="D5a")

        with pytest.raises(IntegrityError):
            with transaction.atomic():
                ServiceContract.objects.create(
                    workspace_id=service_client.workspace_id,
                    service_client=service_client,
                    code="D5-1",
                    name="X",
                    monthly_hours=Decimal("30.0000"),
                    starts_on=date(2026, 1, 1),
                    ends_on=date(2026, 12, 31),
                    accrual_cap_mode=ServiceAccrualCapMode.NONE,
                    accrual_cap_value=Decimal("60.0000"),
                )

    def test_a_mode_without_a_value_is_refused_by_the_database(self, db):
        service_client = ServiceClientFactory(name="D5b")

        with pytest.raises(IntegrityError):
            with transaction.atomic():
                ServiceContract.objects.create(
                    workspace_id=service_client.workspace_id,
                    service_client=service_client,
                    code="D5-2",
                    name="X",
                    monthly_hours=Decimal("30.0000"),
                    starts_on=date(2026, 1, 1),
                    ends_on=date(2026, 12, 31),
                    accrual_cap_mode=ServiceAccrualCapMode.MULTIPLE,
                    accrual_cap_value=None,
                )

    def test_a_coherent_pair_is_accepted(self, db):
        """The positive control. Without it, a constraint that refused *everything* would
        pass both tests above."""
        service_client = ServiceClientFactory(name="D5c")

        contract = ServiceContract.objects.create(
            workspace_id=service_client.workspace_id,
            service_client=service_client,
            code="D5-3",
            name="X",
            monthly_hours=Decimal("30.0000"),
            starts_on=date(2026, 1, 1),
            ends_on=date(2026, 12, 31),
            accrual_cap_mode=ServiceAccrualCapMode.MULTIPLE,
            accrual_cap_value=Decimal("2.0000"),
        )

        assert contract.accrual_cap_hours() == Decimal("60.0000")

    def test_an_absolute_cap_resolves_to_its_own_value(self, db):
        contract = ServiceContractFactory(
            code="D5-4",
            monthly_hours=Decimal("30.0000"),
            accrual_cap_mode=ServiceAccrualCapMode.ABSOLUTE,
            accrual_cap_value=Decimal("45.0000"),
        )

        assert contract.accrual_cap_hours() == Decimal("45.0000")

    def test_no_cap_resolves_to_none(self, db):
        assert ServiceContractFactory(code="D5-5").accrual_cap_hours() is None


class TestContractConstraints:
    def test_an_inverted_vigency_is_refused(self, db):
        service_client = ServiceClientFactory(name="Vig")

        with pytest.raises(IntegrityError):
            with transaction.atomic():
                ServiceContract.objects.create(
                    workspace_id=service_client.workspace_id,
                    service_client=service_client,
                    code="V-1",
                    name="X",
                    monthly_hours=Decimal("30.0000"),
                    starts_on=date(2026, 12, 31),
                    ends_on=date(2026, 1, 1),
                )

    def test_zero_monthly_hours_is_refused(self, db):
        service_client = ServiceClientFactory(name="Zero")

        with pytest.raises(IntegrityError):
            with transaction.atomic():
                ServiceContract.objects.create(
                    workspace_id=service_client.workspace_id,
                    service_client=service_client,
                    code="Z-1",
                    name="X",
                    monthly_hours=Decimal("0.0000"),
                    starts_on=date(2026, 1, 1),
                    ends_on=date(2026, 12, 31),
                )

    def test_the_default_is_unique_per_client_not_per_workspace(self, db):
        """The constraint that made inheriting ``ServiceCatalogBaseModel`` impossible.

        Two clients in ONE workspace may each have a default contract. That base's
        constraint is declared ``fields=["workspace"]``, so inheriting it would have
        refused the second one -- and no single-client fixture would ever have noticed.
        """
        first_client = ServiceClientFactory(name="Def1")
        second_client = ServiceClientFactory(name="Def2", workspace=first_client.workspace)

        a = ServiceContractFactory(service_client=first_client, code="DF-1")
        a.is_default = True
        a.save()

        b = ServiceContractFactory(service_client=second_client, code="DF-2")
        b.is_default = True
        b.save()

        assert a.workspace_id == b.workspace_id, "same workspace, and both are defaults"
        assert ServiceContract.objects.filter(
            workspace_id=a.workspace_id, is_default=True
        ).count() == 2

    def test_two_defaults_for_one_client_are_refused(self, db):
        """The other half: within one client, at most one."""
        service_client = ServiceClientFactory(name="Def3")
        first = ServiceContractFactory(service_client=service_client, code="DF-3")
        first.is_default = True
        first.save()

        second = ServiceContractFactory(service_client=service_client, code="DF-4")
        second.is_default = True

        with pytest.raises(IntegrityError):
            with transaction.atomic():
                second.save()

    def test_a_code_is_unique_per_client_but_reusable_across_clients(self, db):
        """Two clients may legitimately both call their agreement "001"."""
        first_client = ServiceClientFactory(name="Cod1")
        second_client = ServiceClientFactory(name="Cod2", workspace=first_client.workspace)

        ServiceContract.objects.create(
            workspace_id=first_client.workspace_id,
            service_client=first_client,
            code="001",
            name="X",
            monthly_hours=Decimal("30.0000"),
            starts_on=date(2026, 1, 1),
            ends_on=date(2026, 12, 31),
        )
        ServiceContract.objects.create(
            workspace_id=second_client.workspace_id,
            service_client=second_client,
            code="001",
            name="Y",
            monthly_hours=Decimal("10.0000"),
            starts_on=date(2026, 1, 1),
            ends_on=date(2026, 12, 31),
        )

        assert ServiceContract.objects.filter(code="001").count() == 2

        with pytest.raises(IntegrityError):
            with transaction.atomic():
                ServiceContract.objects.create(
                    workspace_id=first_client.workspace_id,
                    service_client=first_client,
                    code="001",
                    name="Z",
                    monthly_hours=Decimal("5.0000"),
                    starts_on=date(2026, 1, 1),
                    ends_on=date(2026, 12, 31),
                )


class TestLedgerConstraints:
    def test_a_positive_expiry_is_refused(self, db, actor):
        """A write-off that credited hours would reconcile perfectly and be wrong."""
        contract = ServiceContractFactory(code="LG-1")
        period = resolve_period(contract, date(2026, 1, 1), actor=actor)

        with pytest.raises(IntegrityError):
            with transaction.atomic():
                ServiceHourLedgerEntry.objects.create(
                    workspace_id=period.workspace_id,
                    contract_id=contract.pk,
                    period=period,
                    hours=Decimal("5.0000"),
                    entry_type=ServiceLedgerEntryType.EXPIRED_BY_CAP,
                )

    def test_a_positive_debit_is_refused(self, db, actor):
        contract = ServiceContractFactory(code="LG-2")
        period = resolve_period(contract, date(2026, 1, 1), actor=actor)

        with pytest.raises(IntegrityError):
            with transaction.atomic():
                ServiceHourLedgerEntry.objects.create(
                    workspace_id=period.workspace_id,
                    contract_id=contract.pk,
                    period=period,
                    hours=Decimal("5.0000"),
                    entry_type=ServiceLedgerEntryType.DEBIT,
                )

    def test_a_negative_grant_is_allowed_because_b1_needs_it(self, db, actor):
        """``GRANT`` is the one additive type that may be negative.

        Decision B1 makes ``contracted_hours`` editable while a period is open, and in an
        append-only journal a correction from 30h to 15h has to be a new row of -15. The
        alternative was a twelfth entry type, which would have turned
        ``sum(GRANT) == contracted_hours`` into a two-term sum for no gain.
        """
        contract = ServiceContractFactory(code="LG-3")
        period = resolve_period(contract, date(2026, 1, 1), actor=actor)

        entry = ServiceHourLedgerEntry.objects.create(
            workspace_id=period.workspace_id,
            contract_id=contract.pk,
            period=period,
            hours=Decimal("-15.0000"),
            entry_type=ServiceLedgerEntryType.GRANT,
            origin_period=period,
        )

        assert entry.hours == Decimal("-15.0000")

    def test_a_negative_carry_in_is_allowed_because_a_deficit_is_one(self, db, actor):
        contract = ServiceContractFactory(code="LG-4")
        period = resolve_period(contract, date(2026, 1, 1), actor=actor)

        entry = ServiceHourLedgerEntry.objects.create(
            workspace_id=period.workspace_id,
            contract_id=contract.pk,
            period=period,
            hours=Decimal("-3.0000"),
            entry_type=ServiceLedgerEntryType.CARRY_IN,
            origin_period=period,
        )

        assert entry.hours == Decimal("-3.0000")


class TestPeriodConstraints:
    def test_a_settlement_on_an_open_period_is_refused(self, db, actor):
        """A settlement without a close would be a decision nobody took."""
        contract = ServiceContractFactory(code="PC-1")
        period = resolve_period(contract, date(2026, 1, 1), actor=actor)

        with pytest.raises(IntegrityError):
            with transaction.atomic():
                ServiceContractPeriod.objects.filter(pk=period.pk).update(
                    status=ServicePeriodStatus.OPEN, overage_settlement="billed"
                )

    def test_a_duplicate_competency_is_refused(self, db, actor):
        """Two pools for one month would double the client's hours."""
        contract = ServiceContractFactory(code="PC-2")
        period = resolve_period(contract, date(2026, 1, 1), actor=actor)

        with pytest.raises(IntegrityError):
            with transaction.atomic():
                ServiceContractPeriod.objects.create(
                    workspace_id=period.workspace_id,
                    contract=contract,
                    competence_year=2026,
                    competence_month=1,
                    starts_on=date(2026, 1, 1),
                    ends_on=date(2026, 1, 31),
                    contracted_hours=Decimal("30.0000"),
                )

    def test_carried_hours_may_be_negative_but_contracted_hours_may_not(self, db, actor):
        """The asymmetry is deliberate: a carried deficit is legitimate, a negative quota
        is not."""
        contract = ServiceContractFactory(code="PC-3")
        period = resolve_period(contract, date(2026, 1, 1), actor=actor)

        ServiceContractPeriod.objects.filter(pk=period.pk).update(carried_hours=Decimal("-5.0000"))
        assert ServiceContractPeriod.objects.get(pk=period.pk).carried_hours == Decimal("-5.0000")

        with pytest.raises(IntegrityError):
            with transaction.atomic():
                ServiceContractPeriod.objects.filter(pk=period.pk).update(
                    contracted_hours=Decimal("-1.0000")
                )


class TestAuditSentinel:
    """Decision D4's two mandatory tests."""

    def test_filling_in_a_nullable_tracked_field_satisfies_the_constraint(self, db, actor):
        """MANDATORY TEST 1. The transition NULL -> 60.00 must write a legal audit row.

        Before the sentinel this raised at the database level:
        ``svc_cfg_activity_shape_matches_verb`` requires ``old_value`` and ``new_value``
        to be non-null on an ``updated`` row, and ``serialize_config_value`` rendered
        ``None`` as ``None``. This is the bill the calendar phase left for this one, and
        the test is what proves it was paid.
        """
        contract = ServiceContractFactory(
            code="SEN-1",
            accrual_cap_mode=ServiceAccrualCapMode.NONE,
            accrual_cap_value=None,
        )
        assert contract.accrual_cap_value is None, "positive control: it really starts unset"

        fresh = ServiceContract.objects.get(pk=contract.pk)
        fresh.accrual_cap_mode = ServiceAccrualCapMode.ABSOLUTE
        fresh.accrual_cap_value = Decimal("60.0000")
        save_with_config_activity(fresh, actor=actor)

        row = ServiceConfigActivity.objects.get(
            entity_identifier=contract.pk, field_name="accrual_cap_value"
        )

        assert row.verb == ServiceConfigVerb.UPDATED
        assert row.old_value == CONFIG_VALUE_UNSET
        assert row.new_value == "60.0000"
        assert row.old_value is not None, "a NULL here would violate the check constraint"

    def test_clearing_a_nullable_tracked_field_also_satisfies_the_constraint(self, db, actor):
        """The other direction: 60.00 -> NULL."""
        contract = ServiceContractFactory(
            code="SEN-2",
            accrual_cap_mode=ServiceAccrualCapMode.ABSOLUTE,
            accrual_cap_value=Decimal("60.0000"),
        )

        fresh = ServiceContract.objects.get(pk=contract.pk)
        fresh.accrual_cap_mode = ServiceAccrualCapMode.NONE
        fresh.accrual_cap_value = None
        save_with_config_activity(fresh, actor=actor)

        row = ServiceConfigActivity.objects.get(
            entity_identifier=contract.pk, field_name="accrual_cap_value"
        )

        assert row.old_value == "60.0000"
        assert row.new_value == CONFIG_VALUE_UNSET

    def test_no_tracked_field_of_an_audited_model_is_free_text(self):
        """MANDATORY TEST 2, and it guards a PREMISE, not a behaviour.

        The sentinel is only unambiguous because no tracked field can legitimately hold
        the literal string "UNSET". Today that is true of every audited model: the
        tracked fields are decimals, dates, integers, booleans and ``CharField``s **with
        choices**. It is a premise of the design, and until now it was a comment.
        Comments rot; a test fails. This is the lesson of the false green applied to a
        design assumption rather than to a fixture.

        Scoped to models that declare ``CONFIG_ENTITY_NAME`` -- the audited configuration
        models. ``Issue`` and ``IssueComment`` also use ``ChangeTrackerMixin`` but write
        to ``IssueActivity``, and ``IssueComment`` tracks ``comment_html``, which *is*
        free text. Including them would fail this test for entirely the wrong reason.
        """
        from django.apps import apps

        audited = [
            model
            for model in apps.get_models()
            if getattr(model, "CONFIG_ENTITY_NAME", None) and getattr(model, "TRACKED_FIELDS", None)
        ]

        assert audited, "positive control: the scan found the audited models at all"
        assert len(audited) >= 5, (
            "expected at least the two catalogues, holiday, window and contract; "
            f"found {[m.__name__ for m in audited]}"
        )

        offenders = []

        for model in audited:
            for field_name in model.TRACKED_FIELDS:
                field = model._meta.get_field(field_name)

                is_text = isinstance(field, (models.CharField, models.TextField))
                has_choices = bool(getattr(field, "choices", None))

                if is_text and not has_choices:
                    offenders.append(f"{model.__name__}.{field_name}")

        assert not offenders, (
            "These tracked fields are free text, so the CONFIG_VALUE_UNSET sentinel can "
            f"collide with a legitimate value: {offenders}. Either give the field "
            "choices, drop it from TRACKED_FIELDS, or replace the sentinel mechanism."
        )

    def test_the_sentinel_cannot_collide_with_a_serialised_value(self, db):
        """The other half of the premise: nothing the serialiser produces looks like the
        sentinel. Decimals are quantised, booleans are lowercase, enums are their
        database values."""
        from plane.utils.service_catalog import serialize_config_value

        contract = ServiceContractFactory(code="SEN-3")

        assert serialize_config_value(contract, "monthly_hours", Decimal("30")) == "30.0000"
        assert serialize_config_value(contract, "status", "active") == "active"
        assert serialize_config_value(contract, "starts_on", date(2026, 1, 1)) == "2026-01-01"
        assert CONFIG_VALUE_UNSET == CONFIG_VALUE_UNSET.upper(), "UPPER_SNAKE, like the error codes"

    def test_creating_a_contract_writes_one_creation_row(self, db, actor):
        """Decision D22: the contract phase must use all three verbs, and a creation is
        ONE row rather than one per column."""
        from plane.utils.service_catalog import create_with_config_activity

        service_client = ServiceClientFactory(name="Verbo")
        contract = ServiceContract(
            workspace_id=service_client.workspace_id,
            service_client=service_client,
            code="VB-1",
            name="Suporte",
            monthly_hours=Decimal("30.0000"),
            starts_on=date(2026, 1, 1),
            ends_on=date(2026, 12, 31),
        )

        create_with_config_activity(contract, actor=actor)

        rows = ServiceConfigActivity.objects.filter(entity_identifier=contract.pk)

        assert rows.count() == 1
        assert rows.first().verb == ServiceConfigVerb.CREATED
        assert rows.first().field_name is None
        assert "30.0000" in rows.first().new_value
        assert "VB-1" in rows.first().new_value

    def test_deleting_a_contract_writes_one_deletion_row(self, db, actor):
        from plane.utils.service_catalog import delete_with_config_activity

        contract = ServiceContractFactory(code="VB-2")

        delete_with_config_activity(contract, actor=actor)

        row = ServiceConfigActivity.objects.get(
            entity_identifier=contract.pk, verb=ServiceConfigVerb.DELETED
        )

        assert row.new_value is None
        assert "VB-2" in row.old_value
