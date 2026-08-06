# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Unit tests for the work log catalogue domain layer.

The precedence chain of decision D7, the invariant guards, and the audit trail are
all here rather than behind HTTP, because they are the financial core of this phase.
"""

from decimal import Decimal
from unittest.mock import patch

import pytest

from plane.db.models import ServiceBillingType, ServiceConfigActivity, ServiceHourType
from plane.tests.factories import (
    ServiceBillingTypeFactory,
    ServiceClientFactory,
    ServiceHourTypeFactory,
    UserFactory,
    WorkspaceFactory,
)
from plane.utils.service_catalog import (
    DEFAULT_CANNOT_BE_DEACTIVATED,
    DEFAULT_CANNOT_BE_DELETED,
    DEFAULT_CANNOT_BE_UNSET,
    capture_tracked_changes,
    record_config_activity,
    resolve_default_billing_type,
    save_with_config_activity,
    serialize_config_value,
    set_catalog_default,
    validate_catalog_delete,
    validate_catalog_update,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def no_celery_dispatch():
    with patch("celery.app.task.Task.apply_async", return_value=None):
        yield


@pytest.fixture
def workspace(db):
    return WorkspaceFactory()


@pytest.fixture
def seeded_billing_types(workspace):
    """A minimal catalogue: Contrato is the default, Avulso is not."""
    contract = ServiceBillingTypeFactory(
        workspace=workspace,
        name="Contrato",
        billing_route=ServiceBillingType.BillingRoute.DEBIT_POOL,
    )
    ad_hoc = ServiceBillingTypeFactory(
        workspace=workspace,
        name="Avulso",
        billing_route=ServiceBillingType.BillingRoute.BILL_AMOUNT,
    )
    contract.refresh_from_db()
    return contract, ad_hoc


class TestResolveDefaultBillingType:
    """Decision D7 and section 3 of the phase brief.

    Two levels: the catalogue default, overridden by the client's own. The third
    level -- what the technician submits on the work log -- is applied by the caller
    and deliberately not modelled here.
    """

    def test_falls_back_to_the_catalogue_default_without_a_client(self, workspace, seeded_billing_types):
        contract, _ = seeded_billing_types

        assert resolve_default_billing_type(workspace.id) == contract

    def test_a_client_without_an_override_gets_the_catalogue_default(self, workspace, seeded_billing_types):
        contract, _ = seeded_billing_types
        service_client = ServiceClientFactory(workspace=workspace, name="Marubeni")

        assert resolve_default_billing_type(workspace.id, service_client) == contract

    def test_the_client_override_wins(self, workspace, seeded_billing_types):
        """The use case from the brief: a client whose standard route is not the
        global default."""
        _, ad_hoc = seeded_billing_types
        service_client = ServiceClientFactory(workspace=workspace, name="Terlogs", default_billing_type=ad_hoc)

        assert resolve_default_billing_type(workspace.id, service_client) == ad_hoc

    def test_a_deactivated_override_falls_back(self, workspace, seeded_billing_types):
        """Pre-selecting an option that the dropdown hides would be a dead end in the
        form, so an override that has been deactivated is ignored."""
        contract, ad_hoc = seeded_billing_types
        service_client = ServiceClientFactory(workspace=workspace, name="Terlogs", default_billing_type=ad_hoc)

        ad_hoc.is_active = False
        ad_hoc.save()

        assert resolve_default_billing_type(workspace.id, service_client) == contract

    def test_a_soft_deleted_override_falls_back_instead_of_raising(self, workspace, seeded_billing_types):
        """The DO_NOTHING foreign key can still point at a soft deleted row, and
        Django's forward descriptor would raise DoesNotExist. The resolver queries by
        id precisely so this returns the catalogue default instead of a 500."""
        contract, ad_hoc = seeded_billing_types
        service_client = ServiceClientFactory(workspace=workspace, name="Terlogs", default_billing_type=ad_hoc)

        ad_hoc.delete()

        assert resolve_default_billing_type(workspace.id, service_client) == contract

    def test_an_override_from_another_workspace_falls_back(self, workspace, seeded_billing_types):
        contract, _ = seeded_billing_types
        other_workspace_type = ServiceBillingTypeFactory(workspace=WorkspaceFactory(), name="Contrato")
        service_client = ServiceClientFactory(
            workspace=workspace, name="Terlogs", default_billing_type=other_workspace_type
        )

        assert resolve_default_billing_type(workspace.id, service_client) == contract

    def test_returns_none_when_the_catalogue_was_never_seeded(self, workspace):
        assert resolve_default_billing_type(workspace.id) is None

    def test_returns_none_when_no_default_is_active(self, workspace):
        """Cannot happen through the API, which refuses to deactivate the default.
        Asserted so the resolver is honest if it happens through the shell."""
        option = ServiceBillingTypeFactory(workspace=workspace, name="Contrato")
        ServiceBillingType.objects.filter(pk=option.pk).update(is_active=False)

        assert resolve_default_billing_type(workspace.id) is None


class TestCatalogGuards:
    def test_unsetting_the_default_is_refused(self, workspace):
        option = ServiceHourTypeFactory(workspace=workspace, name="Comercial")
        option.refresh_from_db()

        assert validate_catalog_update(option, is_default=False) == DEFAULT_CANNOT_BE_UNSET

    def test_deactivating_the_default_is_refused(self, workspace):
        """An inactive default would still be pre-selected by the form while being
        hidden from the dropdown."""
        option = ServiceHourTypeFactory(workspace=workspace, name="Comercial")
        option.refresh_from_db()

        assert validate_catalog_update(option, is_active=False) == DEFAULT_CANNOT_BE_DEACTIVATED

    def test_deactivating_a_non_default_is_allowed(self, workspace):
        ServiceHourTypeFactory(workspace=workspace, name="Comercial")
        other = ServiceHourTypeFactory(workspace=workspace, name="Fora")

        assert validate_catalog_update(other, is_active=False) is None

    def test_an_unrelated_edit_of_the_default_is_allowed(self, workspace):
        """Changing the multiplier of the default is legitimate: rule R4 protects the
        history through the snapshot on each work log."""
        option = ServiceHourTypeFactory(workspace=workspace, name="Comercial")
        option.refresh_from_db()

        assert validate_catalog_update(option) is None
        assert validate_catalog_update(option, is_active=True, is_default=True) is None

    def test_deleting_the_default_is_refused(self, workspace):
        option = ServiceHourTypeFactory(workspace=workspace, name="Comercial")
        option.refresh_from_db()

        assert validate_catalog_delete(option) == DEFAULT_CANNOT_BE_DELETED

    def test_deleting_a_non_default_is_allowed(self, workspace):
        ServiceHourTypeFactory(workspace=workspace, name="Comercial")
        other = ServiceHourTypeFactory(workspace=workspace, name="Fora")

        assert validate_catalog_delete(other) is None


class TestSetCatalogDefault:
    def test_promotion_swaps_the_incumbent(self, workspace):
        first = ServiceHourTypeFactory(workspace=workspace, name="Comercial")
        second = ServiceHourTypeFactory(workspace=workspace, name="Fora")
        first.refresh_from_db()

        set_catalog_default(second, actor=UserFactory())

        first.refresh_from_db()
        second.refresh_from_db()

        assert first.is_default is False
        assert second.is_default is True

    def test_exactly_one_default_remains(self, workspace):
        options = [ServiceHourTypeFactory(workspace=workspace, name=f"Tipo {index}") for index in range(4)]

        for option in options:
            set_catalog_default(option, actor=UserFactory())
            assert ServiceHourType.objects.filter(workspace=workspace, is_default=True).count() == 1

    def test_promoting_the_current_default_is_a_no_op(self, workspace):
        option = ServiceHourTypeFactory(workspace=workspace, name="Comercial")
        option.refresh_from_db()

        set_catalog_default(option, actor=UserFactory())

        option.refresh_from_db()
        assert option.is_default is True
        assert ServiceHourType.objects.filter(workspace=workspace, is_default=True).count() == 1


class TestSerializeConfigValue:
    def test_decimal_is_quantised_to_the_field_scale(self, workspace):
        """"1.50", never "1.5". Two entries have to be comparable as text, and the
        scale is what makes that true."""
        option = ServiceHourTypeFactory(workspace=workspace, name="Fora")

        assert serialize_config_value(option, "multiplier", Decimal("1.5")) == "1.50"
        assert serialize_config_value(option, "multiplier", Decimal("2")) == "2.00"
        assert serialize_config_value(option, "multiplier", Decimal("1.05")) == "1.05"

    def test_booleans_are_lowercase(self, workspace):
        option = ServiceHourTypeFactory(workspace=workspace, name="Fora")

        assert serialize_config_value(option, "is_active", True) == "true"
        assert serialize_config_value(option, "is_active", False) == "false"

    def test_an_enum_is_stored_by_its_database_value(self, workspace):
        """Not the human label: the label is presentation and could be translated or
        reworded, which would corrupt the comparability of the history."""
        option = ServiceBillingTypeFactory(workspace=workspace, name="Contrato")

        serialized = serialize_config_value(option, "billing_route", ServiceBillingType.BillingRoute.NON_BILLABLE)

        assert serialized == "non_billable"

    def test_none_stays_none(self, workspace):
        option = ServiceHourTypeFactory(workspace=workspace, name="Fora")

        assert serialize_config_value(option, "multiplier", None) is None


class TestConfigActivityTrail:
    def test_a_multiplier_change_is_recorded_with_both_values(self, workspace):
        """The question the trail exists to answer: why did March use 1.5 and April
        1.8?"""
        actor = UserFactory()
        ServiceHourTypeFactory(workspace=workspace, name="Fora", multiplier=Decimal("1.50"))

        option = ServiceHourType.objects.get(workspace=workspace, name="Fora")
        option.multiplier = Decimal("1.80")
        save_with_config_activity(option, actor=actor)

        activity = ServiceConfigActivity.objects.get(entity_identifier=option.pk, field_name="multiplier")

        assert activity.old_value == "1.50"
        assert activity.new_value == "1.80"
        assert activity.entity_name == "service_hour_type"
        assert activity.actor_id == actor.id
        assert activity.workspace_id == workspace.id

    def test_a_billing_route_change_is_recorded(self, workspace):
        """As financially serious as the multiplier: it changes the *kind* of charge,
        not its magnitude."""
        actor = UserFactory()
        ServiceBillingTypeFactory(workspace=workspace, name="Contrato")

        option = ServiceBillingType.objects.get(workspace=workspace, name="Contrato")
        option.billing_route = ServiceBillingType.BillingRoute.BILL_AMOUNT
        save_with_config_activity(option, actor=actor)

        activity = ServiceConfigActivity.objects.get(entity_identifier=option.pk, field_name="billing_route")

        assert activity.old_value == "debit_pool"
        assert activity.new_value == "bill_amount"
        assert activity.entity_name == "service_billing_type"

    def test_deactivation_is_recorded(self, workspace):
        """"Why did this type stop appearing in the form?" is a real support
        question."""
        actor = UserFactory()
        ServiceHourTypeFactory(workspace=workspace, name="Comercial")
        ServiceHourTypeFactory(workspace=workspace, name="Fora")

        option = ServiceHourType.objects.get(workspace=workspace, name="Fora")
        option.is_active = False
        save_with_config_activity(option, actor=actor)

        activity = ServiceConfigActivity.objects.get(entity_identifier=option.pk, field_name="is_active")

        assert activity.old_value == "true"
        assert activity.new_value == "false"

    def test_two_fields_changed_at_once_produce_two_rows(self, workspace):
        actor = UserFactory()
        ServiceHourTypeFactory(workspace=workspace, name="Comercial")
        ServiceHourTypeFactory(workspace=workspace, name="Fora", multiplier=Decimal("1.50"))

        option = ServiceHourType.objects.get(workspace=workspace, name="Fora")
        option.multiplier = Decimal("1.75")
        option.is_active = False
        save_with_config_activity(option, actor=actor)

        fields = set(
            ServiceConfigActivity.objects.filter(entity_identifier=option.pk).values_list("field_name", flat=True)
        )

        assert fields == {"multiplier", "is_active"}

    def test_an_untracked_change_records_nothing(self, workspace):
        actor = UserFactory()
        ServiceHourTypeFactory(workspace=workspace, name="Comercial")

        option = ServiceHourType.objects.get(workspace=workspace, name="Comercial")
        option.name = "Comercial renomeado"
        option.sequence = 42
        save_with_config_activity(option, actor=actor)

        assert ServiceConfigActivity.objects.filter(entity_identifier=option.pk).count() == 0

    def test_creating_a_row_records_nothing(self, workspace):
        """Creation is already answered by created_by and created_at on the entity,
        and ChangeTrackerMixin cannot emit a creation event anyway."""
        option = ServiceHourType(workspace=workspace, name="Novo", multiplier=Decimal("2.00"))
        save_with_config_activity(option, actor=UserFactory())

        assert ServiceConfigActivity.objects.filter(entity_identifier=option.pk).count() == 0

    def test_recording_without_an_actor_raises(self, workspace):
        """An audit row with no author is not an audit row. crum returns None inside
        management commands and tasks, so this has to fail loudly rather than write a
        useless row."""
        ServiceHourTypeFactory(workspace=workspace, name="Fora", multiplier=Decimal("1.50"))

        option = ServiceHourType.objects.get(workspace=workspace, name="Fora")
        option.multiplier = Decimal("1.80")
        changes = capture_tracked_changes(option)

        with pytest.raises(ValueError):
            record_config_activity(option, changes, actor=None)

    def test_capture_must_happen_before_save(self, workspace):
        """Documents the constraint that shapes the helper: ChangeTrackerMixin resets
        its tracked values inside save(), so capturing afterwards yields nothing."""
        ServiceHourTypeFactory(workspace=workspace, name="Fora", multiplier=Decimal("1.50"))

        option = ServiceHourType.objects.get(workspace=workspace, name="Fora")
        option.multiplier = Decimal("1.80")

        before_save = capture_tracked_changes(option)
        option.save()
        after_save = capture_tracked_changes(option)

        assert before_save == {"multiplier": (Decimal("1.50"), Decimal("1.80"))}
        assert after_save == {}

    def test_the_trail_survives_a_soft_delete_of_the_audited_row(self, workspace):
        """Why entity_identifier is a bare UUID and not a foreign key: an FK would be
        walked by soft_delete_related_objects, whose catch-all branch would soft
        delete the trail along with the row it describes."""
        actor = UserFactory()
        ServiceHourTypeFactory(workspace=workspace, name="Comercial")
        ServiceHourTypeFactory(workspace=workspace, name="Fora", multiplier=Decimal("1.50"))

        option = ServiceHourType.objects.get(workspace=workspace, name="Fora")
        option.multiplier = Decimal("1.80")
        save_with_config_activity(option, actor=actor)
        option_pk = option.pk

        option.delete()

        assert ServiceConfigActivity.objects.filter(entity_identifier=option_pk).count() == 1



class TestRecordConfigCreationAndDeletion:
    """The `created` and `deleted` halves of the trail.

    Added by the calendar phase. For a holiday or a classification window, creating and
    removing the row ARE the financial events -- registering a holiday on 15/03 moves every
    work log that day from 1.0 to 2.0 -- so recording only field edits would record exactly
    what matters least in those two tables.
    """

    def test_creation_writes_exactly_one_row(self, workspace, create_user):
        from plane.db.models import ServiceConfigVerb
        from plane.utils.service_catalog import record_config_creation

        hour_type = ServiceHourTypeFactory(workspace=workspace, name="Plantão", multiplier=Decimal("2.50"))

        record_config_creation(hour_type, actor=create_user)

        rows = ServiceConfigActivity.objects.filter(entity_identifier=hour_type.pk)
        assert rows.count() == 1, "one row per creation, not one per field"

        row = rows.first()
        assert row.verb == ServiceConfigVerb.CREATED
        assert row.field_name is None
        assert row.old_value is None
        assert "Plantão" in row.new_value
        assert row.actor_id == create_user.id

    def test_deletion_writes_exactly_one_row_with_the_summary_in_old_value(
        self, workspace, create_user
    ):
        from plane.db.models import ServiceConfigVerb
        from plane.utils.service_catalog import record_config_deletion

        hour_type = ServiceHourTypeFactory(workspace=workspace, name="Plantão", multiplier=Decimal("2.50"))

        record_config_deletion(hour_type, actor=create_user)

        row = ServiceConfigActivity.objects.get(entity_identifier=hour_type.pk)
        assert row.verb == ServiceConfigVerb.DELETED
        assert row.field_name is None
        assert "Plantão" in row.old_value
        assert row.new_value is None

    def test_the_summary_carries_what_changes_the_calculation(self, workspace, create_user):
        """Not just the name: a reader has to see the multiplier that was introduced."""
        from plane.utils.service_catalog import record_config_creation

        hour_type = ServiceHourTypeFactory(
            workspace=workspace, name="Plantão", multiplier=Decimal("2.50")
        )

        record_config_creation(hour_type, actor=create_user)

        row = ServiceConfigActivity.objects.get(entity_identifier=hour_type.pk)
        assert "2.50" in row.new_value

    @pytest.mark.parametrize("recorder", ["record_config_creation", "record_config_deletion"])
    def test_an_actor_is_required(self, workspace, recorder):
        """An audit row with no author is not an audit row."""
        import plane.utils.service_catalog as service_catalog

        hour_type = ServiceHourTypeFactory(workspace=workspace, name="Plantão")

        with pytest.raises(ValueError):
            getattr(service_catalog, recorder)(hour_type, actor=None)

    def test_create_with_config_activity_saves_and_records_together(self, workspace, create_user):
        from plane.db.models import ServiceHourType
        from plane.utils.service_catalog import create_with_config_activity

        hour_type = ServiceHourType(workspace=workspace, name="Plantão", multiplier=Decimal("2.50"))
        create_with_config_activity(hour_type, actor=create_user)

        assert ServiceHourType.objects.filter(pk=hour_type.pk).exists()
        assert ServiceConfigActivity.objects.filter(entity_identifier=hour_type.pk).count() == 1

    def test_delete_with_config_activity_records_before_deleting(self, workspace, create_user):
        """Order matters: the summary reads the instance, so it has to still be readable."""
        from plane.db.models import ServiceHourType
        from plane.utils.service_catalog import delete_with_config_activity

        hour_type = ServiceHourTypeFactory(
            workspace=workspace, name="Plantão", multiplier=Decimal("2.50")
        )
        # Not the catalogue default, which cannot be deleted.
        ServiceHourType.objects.filter(pk=hour_type.pk).update(is_default=False)
        hour_type.refresh_from_db()

        delete_with_config_activity(hour_type, actor=create_user)

        assert not ServiceHourType.objects.filter(pk=hour_type.pk).exists()
        assert ServiceHourType.all_objects.filter(pk=hour_type.pk).exists()

        row = ServiceConfigActivity.objects.get(entity_identifier=hour_type.pk)
        assert "Plantão" in row.old_value

    def test_the_audit_row_survives_the_entity_being_soft_deleted(self, workspace, create_user):
        """entity_identifier is a bare UUID precisely so the trail is not walked on delete."""
        from plane.db.models import ServiceHourType
        from plane.utils.service_catalog import delete_with_config_activity

        hour_type = ServiceHourTypeFactory(workspace=workspace, name="Plantão")
        ServiceHourType.objects.filter(pk=hour_type.pk).update(is_default=False)
        hour_type.refresh_from_db()

        delete_with_config_activity(hour_type, actor=create_user)

        assert ServiceConfigActivity.objects.filter(entity_identifier=hour_type.pk).exists()
