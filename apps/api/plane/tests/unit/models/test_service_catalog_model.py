# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Model level tests for the work log catalogues.

These cover the invariants that hold the "exactly one default per catalogue"
acceptance criterion, the numeric scale fixed by section 4b of the master context,
and the change tracking that feeds the configuration audit trail.
"""

from decimal import Decimal
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from plane.db.models import ServiceBillingType, ServiceHourType
from plane.tests.factories import WorkspaceFactory

pytestmark = pytest.mark.unit

CATALOG_MODELS = [ServiceHourType, ServiceBillingType]

# The audit fields inherited from the abstract chain are null=True but not
# blank=True, so full_clean() reports them as required. Excluding them keeps these
# assertions about the field under test instead of about the base model.
INHERITED_NULLABLE_FIELDS = ["created_by", "updated_by", "project"]


@pytest.fixture(autouse=True)
def no_celery_dispatch():
    """Swallow background task dispatch.

    SoftDeleteModel.delete() enqueues soft_delete_related_objects and there is no
    broker under test. Patching apply_async covers delay() as well, since delay is a
    thin wrapper over it.
    """
    with patch("celery.app.task.Task.apply_async", return_value=None):
        yield


def _create(model, workspace, name, **kwargs):
    return model.objects.create(workspace=workspace, name=name, **kwargs)


@pytest.mark.django_db
@pytest.mark.parametrize("model", CATALOG_MODELS)
class TestCatalogDefaultInvariant:
    def test_first_option_becomes_default_even_when_not_asked(self, model):
        """The "at least one default" half of the invariant.

        A catalogue with options and no default would leave the work log form with
        nothing pre-selected, so the model overrides the caller here.
        """
        workspace = WorkspaceFactory()

        first = _create(model, workspace, "First", is_default=False)

        assert first.is_default is True

    def test_second_option_is_not_default_and_is_appended(self, model):
        workspace = WorkspaceFactory()

        first = _create(model, workspace, "First")
        second = _create(model, workspace, "Second")

        assert first.is_default is True
        assert second.is_default is False
        assert second.sequence == first.sequence + model.SEQUENCE_STEP

    def test_two_defaults_in_one_workspace_are_refused_by_the_database(self, model):
        """The "at most one default" half, enforced by the partial unique index.

        This is what makes two concurrent promotions safe: whatever the application
        layer does, only one can commit.
        """
        workspace = WorkspaceFactory()
        _create(model, workspace, "First")
        second = _create(model, workspace, "Second")

        second.is_default = True

        with pytest.raises(IntegrityError):
            with transaction.atomic():
                second.save()

    def test_each_workspace_has_its_own_default(self, model):
        workspace_a = WorkspaceFactory()
        workspace_b = WorkspaceFactory()

        first_a = _create(model, workspace_a, "Shared name")
        first_b = _create(model, workspace_b, "Shared name")

        assert first_a.is_default is True
        assert first_b.is_default is True

    def test_default_survives_after_the_other_options_are_deleted(self, model):
        workspace = WorkspaceFactory()
        default_option = _create(model, workspace, "First")
        other = _create(model, workspace, "Second")

        other.delete()
        default_option.refresh_from_db()

        assert default_option.is_default is True


@pytest.mark.django_db
@pytest.mark.parametrize("model", CATALOG_MODELS)
class TestCatalogUniqueness:
    def test_duplicate_name_in_the_same_workspace_is_refused(self, model):
        workspace = WorkspaceFactory()
        _create(model, workspace, "Horário comercial")

        with pytest.raises(IntegrityError):
            with transaction.atomic():
                _create(model, workspace, "Horário comercial")

    def test_the_same_name_in_another_workspace_is_allowed(self, model):
        _create(model, WorkspaceFactory(), "Horário comercial")
        other = _create(model, WorkspaceFactory(), "Horário comercial")

        assert other.pk is not None

    def test_the_name_is_reusable_after_a_soft_delete(self, model):
        """The double constraint pattern in action.

        unique_together includes deleted_at and the UniqueConstraint is scoped to live
        rows, so a soft deleted option does not squat on its name forever.
        """
        workspace = WorkspaceFactory()
        original = _create(model, workspace, "Fora do expediente")
        original.delete()

        recreated = _create(model, workspace, "Fora do expediente")

        assert recreated.pk != original.pk
        assert model.objects.filter(workspace=workspace).count() == 1
        assert model.all_objects.filter(workspace=workspace).count() == 2

    def test_soft_deleted_options_do_not_block_a_new_default(self, model):
        workspace = WorkspaceFactory()
        first = _create(model, workspace, "First")
        first.delete()

        second = _create(model, workspace, "Second")

        assert second.is_default is True


@pytest.mark.django_db
class TestHourTypeMultiplier:
    def test_multiplier_is_persisted_as_decimal(self):
        """Never float. Section 6 of the master context, and the whole reason the
        four decimal places of hour quantities are exact."""
        workspace = WorkspaceFactory()

        hour_type = ServiceHourType.objects.create(workspace=workspace, name="Fora", multiplier=Decimal("1.50"))
        hour_type.refresh_from_db()

        assert isinstance(hour_type.multiplier, Decimal)
        assert hour_type.multiplier == Decimal("1.50")

    def test_default_multiplier_is_one(self):
        workspace = WorkspaceFactory()

        hour_type = ServiceHourType.objects.create(workspace=workspace, name="Comercial")

        assert hour_type.multiplier == Decimal("1.00")

    def test_zero_is_refused(self):
        """Zero would be a third mechanism for "do not charge", competing with the
        non-billable billing route. There has to be exactly one."""
        workspace = WorkspaceFactory()
        hour_type = ServiceHourType(workspace=workspace, name="Gratis", multiplier=Decimal("0.00"))

        with pytest.raises(ValidationError) as raised:
            hour_type.full_clean(exclude=INHERITED_NULLABLE_FIELDS)

        # Asserted on the field, not just on the exception type: without this the test
        # passes on the inherited audit fields being blank and proves nothing.
        assert "multiplier" in raised.value.message_dict

    def test_a_negative_multiplier_is_refused(self):
        workspace = WorkspaceFactory()
        hour_type = ServiceHourType(workspace=workspace, name="Negativo", multiplier=Decimal("-1.00"))

        with pytest.raises(ValidationError) as raised:
            hour_type.full_clean(exclude=INHERITED_NULLABLE_FIELDS)

        assert "multiplier" in raised.value.message_dict

    def test_a_multiplier_below_one_is_allowed(self):
        """Travel time billed at half rate is normal practice, so the floor is 0.01
        rather than 1.00."""
        workspace = WorkspaceFactory()
        hour_type = ServiceHourType(workspace=workspace, name="Deslocamento", multiplier=Decimal("0.50"))

        hour_type.full_clean(exclude=INHERITED_NULLABLE_FIELDS)
        hour_type.save()

        assert hour_type.multiplier == Decimal("0.50")

    def test_the_worst_case_of_the_four_decimal_proof_is_representable(self):
        """0.25 * 1.01 = 0.2525 is the worst case quoted in section 4b. The
        multiplier side of it has to fit in two decimal places."""
        workspace = WorkspaceFactory()

        hour_type = ServiceHourType.objects.create(workspace=workspace, name="Ajuste", multiplier=Decimal("1.01"))
        hour_type.refresh_from_db()

        assert hour_type.multiplier * Decimal("0.25") == Decimal("0.2525")


@pytest.mark.django_db
class TestChangeTracking:
    def test_hour_type_tracks_the_fields_the_audit_trail_needs(self):
        workspace = WorkspaceFactory()
        ServiceHourType.objects.create(workspace=workspace, name="Comercial", multiplier=Decimal("1.50"))

        reloaded = ServiceHourType.objects.get(workspace=workspace, name="Comercial")
        reloaded.multiplier = Decimal("1.80")
        reloaded.is_active = False

        assert set(reloaded.changed_fields) == {"multiplier", "is_active"}
        assert reloaded.old_values["multiplier"] == Decimal("1.50")
        assert reloaded.old_values["is_active"] is True

    def test_billing_type_tracks_the_route(self):
        workspace = WorkspaceFactory()
        ServiceBillingType.objects.create(workspace=workspace, name="Contrato")

        reloaded = ServiceBillingType.objects.get(workspace=workspace, name="Contrato")
        reloaded.billing_route = ServiceBillingType.BillingRoute.BILL_AMOUNT

        assert reloaded.changed_fields == ["billing_route"]
        assert reloaded.old_values["billing_route"] == ServiceBillingType.BillingRoute.DEBIT_POOL

    def test_an_untracked_field_is_not_reported(self):
        """Only the financially meaningful fields are tracked. Renaming an option or
        nudging its display order is not an audit event."""
        workspace = WorkspaceFactory()
        ServiceHourType.objects.create(workspace=workspace, name="Comercial")

        reloaded = ServiceHourType.objects.get(workspace=workspace, name="Comercial")
        reloaded.name = "Comercial renomeado"
        reloaded.sequence = 99999

        assert reloaded.changed_fields == []

    def test_creating_a_row_reports_no_change(self):
        """Why the audit trail is updates-only: an instance is born holding its final
        values, so ChangeTrackerMixin structurally cannot emit a creation event."""
        workspace = WorkspaceFactory()

        hour_type = ServiceHourType(workspace=workspace, name="Novo", multiplier=Decimal("2.00"))

        assert hour_type.changed_fields == []


@pytest.mark.django_db
class TestCatalogOrdering:
    def test_options_come_back_in_display_order(self):
        workspace = WorkspaceFactory()
        first = ServiceHourType.objects.create(workspace=workspace, name="A")
        second = ServiceHourType.objects.create(workspace=workspace, name="B")
        third = ServiceHourType.objects.create(workspace=workspace, name="C")

        # Reordering writes a midpoint on the moved row only, which is the house
        # pattern for drag and drop.
        third.sequence = (first.sequence + second.sequence) / 2
        third.save()

        names = list(ServiceHourType.objects.filter(workspace=workspace).values_list("name", flat=True))

        assert names == ["A", "C", "B"]

    def test_sequence_does_not_double_as_priority(self):
        """Guard for the calendar and windows phase.

        `sequence` is display order and nothing else. If a later phase reuses it as
        the classification priority, dragging a row in the admin panel would silently
        reclassify hours and change invoices.
        """
        assert not hasattr(ServiceHourType, "priority")
        assert "priority" not in [field.name for field in ServiceHourType._meta.get_fields()]


@pytest.mark.django_db
class TestSoftDelete:
    def test_delete_is_soft(self):
        workspace = WorkspaceFactory()
        first = ServiceHourType.objects.create(workspace=workspace, name="A")
        second = ServiceHourType.objects.create(workspace=workspace, name="B")

        second.delete()

        assert ServiceHourType.objects.filter(pk=second.pk).first() is None
        assert ServiceHourType.all_objects.filter(pk=second.pk).first() is not None
        assert ServiceHourType.all_objects.get(pk=second.pk).deleted_at is not None
        assert ServiceHourType.objects.filter(pk=first.pk).exists()

    def test_deleted_at_is_not_set_on_a_live_row(self):
        workspace = WorkspaceFactory()

        hour_type = ServiceHourType.objects.create(workspace=workspace, name="A")

        assert hour_type.deleted_at is None
        assert hour_type.created_at <= timezone.now()
