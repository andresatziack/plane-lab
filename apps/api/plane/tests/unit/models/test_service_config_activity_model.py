# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Unit tests for the shape of the configuration audit trail.

The check constraint is tested here rather than in the migration script because
pytest.ini uses --nomigrations: the suite builds its schema from the models, so the
constraint is live, and the ORM knows the schema instead of a shell script having to guess
it. The migration's own concern -- back-filling rows that predate the column -- is checked
in /projects/sandbox/check-migration-0127.sh, which is the only place that can.
"""

import datetime
import uuid
from decimal import Decimal

import pytest
from django.db.utils import IntegrityError

from plane.db.models import (
    ServiceBillingType,
    ServiceClassificationWindow,
    ServiceConfigActivity,
    ServiceConfigVerb,
    ServiceDayScope,
    ServiceHoliday,
    ServiceHolidayScope,
    ServiceHourType,
)
from plane.tests.factories import UserFactory, WorkspaceFactory

pytestmark = pytest.mark.unit


@pytest.fixture
def workspace(db):
    return WorkspaceFactory()


@pytest.fixture
def actor(db):
    return UserFactory()


def _row(workspace, actor, **overrides):
    payload = {
        "workspace": workspace,
        "entity_name": "service_hour_type",
        "entity_identifier": uuid.uuid4(),
        "actor": actor,
        "verb": ServiceConfigVerb.UPDATED,
        "field_name": "multiplier",
        "old_value": "1.50",
        "new_value": "1.80",
    }
    payload.update(overrides)
    return ServiceConfigActivity.objects.create(**payload)


@pytest.mark.django_db
class TestVerbDefault:
    def test_the_default_verb_is_updated(self, workspace, actor):
        """Which is what makes the migration's back-fill a statement of fact.

        Every row written before the column existed came from ChangeTrackerMixin, which
        only fires on an edit -- so defaulting to `updated` describes history rather than
        guessing at it.
        """
        row = ServiceConfigActivity.objects.create(
            workspace=workspace,
            entity_name="service_hour_type",
            entity_identifier=uuid.uuid4(),
            field_name="multiplier",
            old_value="1.50",
            new_value="1.80",
            actor=actor,
        )
        assert row.verb == ServiceConfigVerb.UPDATED

    def test_verb_is_not_nullable(self, workspace, actor):
        """A nullable audit column would force every consumer to branch on null."""
        field = ServiceConfigActivity._meta.get_field("verb")
        assert field.null is False


@pytest.mark.django_db
class TestShapeConstraint:
    """The three coherent shapes, in DDL.

    An audit trail that can hold an incoherent row cannot be read with confidence, and
    confidence is the only thing it produces.
    """

    def test_a_well_formed_updated_row_is_accepted(self, workspace, actor):
        assert _row(workspace, actor).pk is not None

    def test_a_well_formed_created_row_is_accepted(self, workspace, actor):
        row = _row(
            workspace,
            actor,
            verb=ServiceConfigVerb.CREATED,
            field_name=None,
            old_value=None,
            new_value="Natal — 25/12/2026 (anual, national)",
        )
        assert row.pk is not None

    def test_a_well_formed_deleted_row_is_accepted(self, workspace, actor):
        row = _row(
            workspace,
            actor,
            verb=ServiceConfigVerb.DELETED,
            field_name=None,
            old_value="Natal — 25/12/2026 (anual, national)",
            new_value=None,
        )
        assert row.pk is not None

    @pytest.mark.parametrize(
        ("description", "overrides"),
        [
            ("updated with no field_name", {"field_name": None}),
            ("updated with no old_value", {"old_value": None}),
            ("updated with no new_value", {"new_value": None}),
        ],
    )
    def test_an_incoherent_updated_row_is_refused(self, workspace, actor, description, overrides):
        with pytest.raises(IntegrityError, match="svc_cfg_activity_shape_matches_verb"):
            _row(workspace, actor, **overrides)

    @pytest.mark.parametrize(
        ("description", "overrides"),
        [
            (
                "created carrying a field_name",
                {"field_name": "date", "old_value": None, "new_value": "Natal"},
            ),
            (
                "created carrying an old_value",
                {"field_name": None, "old_value": "algo", "new_value": "Natal"},
            ),
            (
                "created with no summary",
                {"field_name": None, "old_value": None, "new_value": None},
            ),
        ],
    )
    def test_an_incoherent_created_row_is_refused(self, workspace, actor, description, overrides):
        with pytest.raises(IntegrityError, match="svc_cfg_activity_shape_matches_verb"):
            _row(workspace, actor, verb=ServiceConfigVerb.CREATED, **overrides)

    @pytest.mark.parametrize(
        ("description", "overrides"),
        [
            (
                "deleted carrying a field_name",
                {"field_name": "date", "old_value": "Natal", "new_value": None},
            ),
            (
                "deleted carrying a new_value",
                {"field_name": None, "old_value": "Natal", "new_value": "algo"},
            ),
            (
                "deleted with no summary",
                {"field_name": None, "old_value": None, "new_value": None},
            ),
        ],
    )
    def test_an_incoherent_deleted_row_is_refused(self, workspace, actor, description, overrides):
        with pytest.raises(IntegrityError, match="svc_cfg_activity_shape_matches_verb"):
            _row(workspace, actor, verb=ServiceConfigVerb.DELETED, **overrides)


@pytest.mark.django_db
class TestConfigSummary:
    """One line per creation or deletion, carrying what changes the calculation."""

    def test_a_holiday_summary_names_its_recurrence(self, workspace):
        """The distinction the trail would be useless without.

        "Added Christmas 2027" moves one day's invoice. "Added Christmas every year" moves a
        day in every future invoice. Same name, same date, different acts.
        """
        once = ServiceHoliday(
            workspace=workspace,
            name="Natal",
            date=datetime.date(2027, 12, 25),
            is_recurring=False,
        )
        always = ServiceHoliday(
            workspace=workspace,
            name="Natal",
            date=datetime.date(2027, 12, 25),
            is_recurring=True,
        )

        assert "data específica" in once.config_summary()
        assert "anual" in always.config_summary()
        assert once.config_summary() != always.config_summary()

    def test_a_holiday_summary_names_the_date_and_scope(self, workspace):
        holiday = ServiceHoliday(
            workspace=workspace,
            name="Aniversário da cidade",
            date=datetime.date(2026, 3, 15),
            scope=ServiceHolidayScope.MUNICIPAL,
        )
        summary = holiday.config_summary()

        assert "Aniversário da cidade" in summary
        assert "15/03/2026" in summary
        assert "municipal" in summary

    def test_an_hour_type_summary_names_the_multiplier_and_priority(self, workspace):
        hour_type = ServiceHourType(
            workspace=workspace, name="Fora do expediente", multiplier=Decimal("1.50"), priority=20
        )
        summary = hour_type.config_summary()

        assert "Fora do expediente" in summary
        assert "1.50" in summary
        assert "20" in summary

    def test_a_billing_type_summary_names_the_route(self, workspace):
        billing_type = ServiceBillingType(
            workspace=workspace,
            name="Garantia",
            billing_route=ServiceBillingType.BillingRoute.NON_BILLABLE,
        )
        summary = billing_type.config_summary()

        assert "Garantia" in summary
        assert "non_billable" in summary

    def test_a_window_summary_names_its_hour_type_and_range(self, workspace):
        hour_type = ServiceHourType.objects.create(
            workspace=workspace, name="Fora do expediente", multiplier=Decimal("1.50")
        )
        window = ServiceClassificationWindow.objects.create(
            workspace=workspace,
            hour_type=hour_type,
            day_scope=ServiceDayScope.MONDAY,
            start_minute=18 * 60,
            end_minute=8 * 60,
        )

        summary = window.config_summary()

        assert "Fora do expediente" in summary
        assert "18:00" in summary
        assert "08:00" in summary

    def test_a_window_summary_survives_its_hour_type_being_soft_deleted(self, workspace):
        """An audit entry that cannot render is worse than none.

        The foreign key is DO_NOTHING, so it can outlive its target; the summary reads
        through all_objects for exactly this case.
        """
        hour_type = ServiceHourType.objects.create(
            workspace=workspace, name="Plantão", multiplier=Decimal("2.50")
        )
        window = ServiceClassificationWindow.objects.create(
            workspace=workspace,
            hour_type=hour_type,
            day_scope=ServiceDayScope.SUNDAY,
            start_minute=0,
            end_minute=1440,
        )
        hour_type.delete()

        window.refresh_from_db()
        assert "Plantão" in window.config_summary()


@pytest.mark.django_db
class TestStr:
    def test_str_does_not_assume_a_field_name(self, workspace, actor):
        """field_name is null for created and deleted, so __str__ cannot rely on it."""
        created = _row(
            workspace,
            actor,
            verb=ServiceConfigVerb.CREATED,
            field_name=None,
            old_value=None,
            new_value="Natal",
        )
        assert "created" in str(created)
        assert "service_hour_type" in str(created)
