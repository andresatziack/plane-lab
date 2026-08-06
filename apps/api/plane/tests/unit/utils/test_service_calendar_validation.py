# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Unit tests for the configuration invariants of the calendar and windows phase.

Two invariants, section 2 of the phase brief: no overlap at the same priority, and a set
of windows that covers the whole week. Plus the holiday recurrence collision rules, which
the database cannot express.
"""

import datetime
from decimal import Decimal

import pytest

from plane.db.models import (
    ServiceBillingType,
    ServiceClassificationWindow,
    ServiceDayScope,
    ServiceHoliday,
    ServiceHolidayScope,
    ServiceHourType,
)
from plane.tests.factories import WorkspaceFactory
from plane.utils.service_calendar import (
    HOLIDAY_ALREADY_COVERED_BY_RECURRING,
    RECURRING_HOLIDAY_COLLIDES_WITH_SPECIFIC,
    WINDOWS_OVERLAP_AT_SAME_PRIORITY,
    WINDOWS_WOULD_NOT_COVER_THE_WEEK,
    coverage_report,
    describe_coverage_gaps,
    find_coverage_gaps,
    find_overlaps_within_day_scope_and_priority,
    hour_type_has_windows,
    is_window_set_complete,
    load_windows,
    validate_holiday,
    validate_window_set,
    window_ranges,
)
from plane.utils.service_catalog import (
    HOUR_TYPE_IN_USE_BY_CLASSIFICATION_WINDOWS,
    validate_catalog_delete,
)
from plane.utils.service_catalog_seed import seed_service_catalogs

pytestmark = pytest.mark.unit

WEEKDAYS = (
    ServiceDayScope.MONDAY,
    ServiceDayScope.TUESDAY,
    ServiceDayScope.WEDNESDAY,
    ServiceDayScope.THURSDAY,
    ServiceDayScope.FRIDAY,
)


@pytest.fixture
def workspace(db):
    """A seeded workspace: complete coverage, no overlaps."""
    workspace = WorkspaceFactory()
    seed_service_catalogs(
        ServiceHourType,
        ServiceBillingType,
        workspace.id,
        window_model=ServiceClassificationWindow,
    )
    return workspace


@pytest.fixture
def bare(db):
    """A workspace with catalogues but no windows at all."""
    workspace = WorkspaceFactory()
    seed_service_catalogs(ServiceHourType, ServiceBillingType, workspace.id)
    return workspace


def _hour_type(workspace, name):
    return ServiceHourType.objects.get(workspace=workspace, name=name)


def _window(workspace, hour_type, scope, start, end, **kwargs):
    return ServiceClassificationWindow.objects.create(
        workspace=workspace,
        hour_type=hour_type,
        day_scope=scope,
        start_minute=start,
        end_minute=end,
        **kwargs,
    )


class TestWindowRanges:
    """The split that lets coverage and overlap treat every range as ordinary."""

    def test_an_ordinary_window_is_one_range(self, workspace):
        window = ServiceClassificationWindow(start_minute=480, end_minute=1080)
        assert window_ranges(window) == ((480, 1080),)

    def test_a_midnight_crossing_window_is_two_ranges(self, workspace):
        window = ServiceClassificationWindow(start_minute=1080, end_minute=480)
        assert window_ranges(window) == ((1080, 1440), (0, 480))

    def test_a_whole_day_window_is_one_range(self, workspace):
        window = ServiceClassificationWindow(start_minute=0, end_minute=1440)
        assert window_ranges(window) == ((0, 1440),)


class TestOverlapIsScopedByDayScopeAndPriority:
    """The pair, not the priority alone.

    Checking priority alone would reject the shipped configuration: Monday 08:00-18:00 and
    Tuesday 08:00-18:00 are both priority 30 and never apply to the same instant.
    """

    def test_the_seeded_configuration_has_no_overlaps(self, workspace):
        assert find_overlaps_within_day_scope_and_priority(load_windows(workspace.id)) == []

    def test_same_range_on_different_days_at_the_same_priority_is_not_an_overlap(self, workspace):
        """The false positive a priority-only check would produce."""
        windows = load_windows(workspace.id)
        commercial = [
            window for window in windows if window.hour_type.name == "Horário comercial"
        ]

        # Five weekdays, one range each, all at priority 30.
        assert len(commercial) == 5
        assert len({window.hour_type.priority for window in commercial}) == 1
        assert find_overlaps_within_day_scope_and_priority(commercial) == []

    def test_overlapping_ranges_on_the_same_day_at_the_same_priority_is_an_overlap(self, bare):
        first = ServiceHourType.objects.create(
            workspace=bare, name="A", multiplier=Decimal("1.00"), priority=50
        )
        second = ServiceHourType.objects.create(
            workspace=bare, name="B", multiplier=Decimal("2.00"), priority=50
        )
        _window(bare, first, ServiceDayScope.MONDAY, 480, 1080)
        _window(bare, second, ServiceDayScope.MONDAY, 600, 720)

        collisions = find_overlaps_within_day_scope_and_priority(load_windows(bare.id))
        assert len(collisions) == 1

    def test_overlap_at_different_priorities_is_allowed_and_is_how_the_model_works(self, workspace):
        """A holiday window covers the whole day on top of the weekday window."""
        report = coverage_report(workspace.id)
        assert report["overlaps"] == []

        # holiday 00:00-24:00 and sunday 00:00-24:00 both exist and both cover an instant
        # on a Sunday that is also a holiday. Priority decides, not exclusion.
        holidays_type = _hour_type(workspace, "Domingos e feriados")
        scopes = set(
            ServiceClassificationWindow.objects.filter(
                workspace=workspace, hour_type=holidays_type
            ).values_list("day_scope", flat=True)
        )
        assert scopes == {ServiceDayScope.HOLIDAY, ServiceDayScope.SUNDAY}

    def test_touching_ranges_are_not_an_overlap(self, bare):
        """Half-open ranges: 08:00-18:00 and 18:00-24:00 share no minute."""
        hour_type = ServiceHourType.objects.create(
            workspace=bare, name="A", multiplier=Decimal("1.00"), priority=50
        )
        _window(bare, hour_type, ServiceDayScope.MONDAY, 480, 1080)
        _window(bare, hour_type, ServiceDayScope.MONDAY, 1080, 1440)

        assert find_overlaps_within_day_scope_and_priority(load_windows(bare.id)) == []

    def test_a_midnight_crossing_window_overlapping_an_early_window_is_detected(self, bare):
        hour_type = ServiceHourType.objects.create(
            workspace=bare, name="A", multiplier=Decimal("1.00"), priority=50
        )
        other = ServiceHourType.objects.create(
            workspace=bare, name="B", multiplier=Decimal("1.00"), priority=50
        )
        _window(bare, hour_type, ServiceDayScope.MONDAY, 1080, 480)  # 18:00 -> 08:00
        _window(bare, other, ServiceDayScope.MONDAY, 0, 120)  # 00:00 -> 02:00

        assert len(find_overlaps_within_day_scope_and_priority(load_windows(bare.id))) == 1


class TestCoverageGaps:
    """What the panel's health indicator renders."""

    def test_the_seeded_configuration_covers_the_week(self, workspace):
        assert find_coverage_gaps(load_windows(workspace.id)) == {}
        assert is_window_set_complete(workspace.id) is True

    def test_a_workspace_with_no_windows_is_entirely_uncovered(self, bare):
        gaps = find_coverage_gaps(load_windows(bare.id))

        # All seven weekdays plus the holiday scope, each missing the whole day.
        assert len(gaps) == 8
        assert all(scope_gaps == [(0, 1440)] for scope_gaps in gaps.values())
        assert is_window_set_complete(bare.id) is False

    def test_a_gap_names_the_scope_and_the_range(self, workspace):
        ServiceClassificationWindow.objects.filter(
            workspace=workspace,
            hour_type__name="Horário comercial",
            day_scope=ServiceDayScope.MONDAY,
        ).delete()

        gaps = find_coverage_gaps(load_windows(workspace.id))

        assert gaps == {ServiceDayScope.MONDAY: [(480, 1080)]}
        assert describe_coverage_gaps(gaps) == {ServiceDayScope.MONDAY: ["08:00–18:00"]}

    def test_the_report_is_descriptive_not_a_bare_boolean(self, workspace):
        ServiceClassificationWindow.objects.filter(
            workspace=workspace, day_scope=ServiceDayScope.SATURDAY
        ).delete()

        report = coverage_report(workspace.id)

        assert report["is_complete"] is False
        assert report["gaps"] == {ServiceDayScope.SATURDAY: ["00:00–24:00"]}
        assert report["window_count"] == 12

    def test_an_inactive_window_does_not_count_towards_coverage(self, workspace):
        ServiceClassificationWindow.objects.filter(
            workspace=workspace, day_scope=ServiceDayScope.SUNDAY
        ).update(is_active=False)

        assert coverage_report(workspace.id)["is_complete"] is False

    def test_a_deactivated_hour_type_takes_its_coverage_with_it(self, workspace):
        after_hours = _hour_type(workspace, "Fora do expediente")
        after_hours.is_active = False
        after_hours.save()

        gaps = find_coverage_gaps(load_windows(workspace.id))

        # Saturday goes entirely, and every weekday loses its night.
        assert ServiceDayScope.SATURDAY in gaps
        assert ServiceDayScope.MONDAY in gaps


class TestCoverageRatchet:
    """"A complete set may never become incomplete", and nothing stronger.

    Requiring completeness unconditionally would lock an admin out of the only screen
    that could repair a workspace whose configuration was assembled by hand.
    """

    def test_a_change_that_keeps_the_set_complete_is_allowed(self, workspace):
        was_complete = is_window_set_complete(workspace.id)

        # Split Monday's business hours in two, which changes nothing about coverage.
        commercial = _hour_type(workspace, "Horário comercial")
        ServiceClassificationWindow.objects.filter(
            workspace=workspace, hour_type=commercial, day_scope=ServiceDayScope.MONDAY
        ).delete()
        _window(workspace, commercial, ServiceDayScope.MONDAY, 480, 720)
        _window(workspace, commercial, ServiceDayScope.MONDAY, 720, 1080)

        assert validate_window_set(workspace.id, was_complete_before=was_complete) is None

    def test_a_change_that_breaks_a_complete_set_is_refused(self, workspace):
        was_complete = is_window_set_complete(workspace.id)
        assert was_complete is True

        ServiceClassificationWindow.objects.filter(
            workspace=workspace, day_scope=ServiceDayScope.SUNDAY
        ).delete()

        assert (
            validate_window_set(workspace.id, was_complete_before=was_complete)
            == WINDOWS_WOULD_NOT_COVER_THE_WEEK
        )

    def test_deleting_a_window_is_the_case_a_per_row_check_would_miss(self, workspace):
        """No row is invalid; the *set* is. Which is why validation is set-level."""
        was_complete = is_window_set_complete(workspace.id)
        window = ServiceClassificationWindow.objects.filter(
            workspace=workspace, day_scope=ServiceDayScope.SATURDAY
        ).first()

        window.delete()

        assert (
            validate_window_set(workspace.id, was_complete_before=was_complete)
            == WINDOWS_WOULD_NOT_COVER_THE_WEEK
        )

    def test_building_the_first_window_of_an_incomplete_workspace_is_allowed(self, bare):
        """The ratchet's whole reason for existing.

        An unconditional coverage rule would refuse this and leave the admin unable to
        ever create a first window.
        """
        was_complete = is_window_set_complete(bare.id)
        assert was_complete is False

        _window(bare, _hour_type(bare, "Horário comercial"), ServiceDayScope.MONDAY, 480, 1080)

        assert validate_window_set(bare.id, was_complete_before=was_complete) is None

    def test_an_incomplete_workspace_can_be_repaired_step_by_step(self, bare):
        """Every intermediate state is allowed, so the admin can work towards complete."""
        commercial = _hour_type(bare, "Horário comercial")

        for scope in WEEKDAYS:
            was_complete = is_window_set_complete(bare.id)
            _window(bare, commercial, scope, 0, 1440)
            assert validate_window_set(bare.id, was_complete_before=was_complete) is None

        assert is_window_set_complete(bare.id) is False  # weekend still missing

    def test_overlap_is_refused_even_while_incomplete(self, bare):
        """No transitional excuse: two windows that cannot be decided between are a bug."""
        first = ServiceHourType.objects.create(
            workspace=bare, name="A", multiplier=Decimal("1.00"), priority=50
        )
        second = ServiceHourType.objects.create(
            workspace=bare, name="B", multiplier=Decimal("1.00"), priority=50
        )
        was_complete = is_window_set_complete(bare.id)

        _window(bare, first, ServiceDayScope.MONDAY, 480, 1080)
        _window(bare, second, ServiceDayScope.MONDAY, 600, 700)

        assert (
            validate_window_set(bare.id, was_complete_before=was_complete)
            == WINDOWS_OVERLAP_AT_SAME_PRIORITY
        )

    def test_overlap_is_reported_before_coverage(self, workspace):
        """An overlap is the more specific fault, so it is the more useful message."""
        commercial = _hour_type(workspace, "Horário comercial")
        duplicate = ServiceHourType.objects.create(
            workspace=workspace,
            name="Comercial duplicado",
            multiplier=Decimal("1.00"),
            priority=commercial.priority,
        )
        was_complete = is_window_set_complete(workspace.id)

        _window(workspace, duplicate, ServiceDayScope.MONDAY, 600, 700)
        ServiceClassificationWindow.objects.filter(
            workspace=workspace, day_scope=ServiceDayScope.SUNDAY
        ).delete()

        assert (
            validate_window_set(workspace.id, was_complete_before=was_complete)
            == WINDOWS_OVERLAP_AT_SAME_PRIORITY
        )


class TestHolidayRecurrenceIsCheckedBothWays:
    """A one-way check just moves the duplicate to the other door."""

    def test_a_specific_holiday_on_a_day_a_recurring_one_covers_is_refused(self, workspace):
        ServiceHoliday.objects.create(
            workspace=workspace, name="Natal", date=datetime.date(2020, 12, 25), is_recurring=True
        )

        assert (
            validate_holiday(
                workspace.id,
                date=datetime.date(2027, 12, 25),
                scope=ServiceHolidayScope.NATIONAL,
                is_recurring=False,
            )
            == HOLIDAY_ALREADY_COVERED_BY_RECURRING
        )

    def test_a_recurring_holiday_on_a_day_a_specific_one_occupies_is_refused(self, workspace):
        """The inverse direction, which a one-way check would let through."""
        ServiceHoliday.objects.create(
            workspace=workspace, name="Natal 2027", date=datetime.date(2027, 12, 25)
        )

        assert (
            validate_holiday(
                workspace.id,
                date=datetime.date(2020, 12, 25),
                scope=ServiceHolidayScope.NATIONAL,
                is_recurring=True,
            )
            == RECURRING_HOLIDAY_COLLIDES_WITH_SPECIFIC
        )

    def test_two_recurring_holidays_on_the_same_day_are_refused(self, workspace):
        ServiceHoliday.objects.create(
            workspace=workspace, name="Natal", date=datetime.date(2020, 12, 25), is_recurring=True
        )

        assert (
            validate_holiday(
                workspace.id,
                date=datetime.date(2024, 12, 25),
                scope=ServiceHolidayScope.NATIONAL,
                is_recurring=True,
            )
            == RECURRING_HOLIDAY_COLLIDES_WITH_SPECIFIC
        )

    def test_a_different_scope_may_share_a_day(self, workspace):
        """Which is the point of recording scope at all (D14)."""
        ServiceHoliday.objects.create(
            workspace=workspace,
            name="Natal",
            date=datetime.date(2020, 12, 25),
            is_recurring=True,
            scope=ServiceHolidayScope.NATIONAL,
        )

        assert (
            validate_holiday(
                workspace.id,
                date=datetime.date(2027, 12, 25),
                scope=ServiceHolidayScope.MUNICIPAL,
                is_recurring=False,
            )
            is None
        )

    def test_an_unrelated_day_is_allowed(self, workspace):
        ServiceHoliday.objects.create(
            workspace=workspace, name="Natal", date=datetime.date(2020, 12, 25), is_recurring=True
        )

        assert (
            validate_holiday(
                workspace.id,
                date=datetime.date(2027, 9, 7),
                scope=ServiceHolidayScope.NATIONAL,
                is_recurring=False,
            )
            is None
        )

    def test_editing_a_holiday_does_not_collide_with_itself(self, workspace):
        holiday = ServiceHoliday.objects.create(
            workspace=workspace, name="Natal", date=datetime.date(2026, 12, 25), is_recurring=True
        )

        assert (
            validate_holiday(
                workspace.id,
                date=datetime.date(2026, 12, 25),
                scope=ServiceHolidayScope.NATIONAL,
                is_recurring=True,
                instance=holiday,
            )
            is None
        )

    def test_an_inactive_holiday_does_not_block(self, workspace):
        ServiceHoliday.objects.create(
            workspace=workspace,
            name="Revogado",
            date=datetime.date(2020, 12, 25),
            is_recurring=True,
            is_active=False,
        )

        assert (
            validate_holiday(
                workspace.id,
                date=datetime.date(2027, 12, 25),
                scope=ServiceHolidayScope.NATIONAL,
                is_recurring=False,
            )
            is None
        )


class TestHourTypeDeletionGuard:
    """The check the catalogue phase reserved a place for."""

    def test_an_hour_type_with_windows_cannot_be_deleted(self, workspace):
        after_hours = _hour_type(workspace, "Fora do expediente")

        assert hour_type_has_windows(after_hours) is True
        assert validate_catalog_delete(after_hours) == HOUR_TYPE_IN_USE_BY_CLASSIFICATION_WINDOWS

    def test_an_hour_type_without_windows_can_be_deleted(self, workspace):
        spare = ServiceHourType.objects.create(
            workspace=workspace, name="Sem janela", multiplier=Decimal("1.00")
        )

        assert hour_type_has_windows(spare) is False
        assert validate_catalog_delete(spare) is None

    def test_a_soft_deleted_window_still_blocks(self, workspace):
        """The database foreign key does not care that the window is flagged deleted."""
        after_hours = _hour_type(workspace, "Fora do expediente")
        ServiceClassificationWindow.objects.filter(workspace=workspace, hour_type=after_hours).delete()

        assert hour_type_has_windows(after_hours) is True

    def test_a_billing_type_is_unaffected(self, workspace):
        """Billing types are not classified, so they have no windows to check."""
        billing_type = ServiceBillingType.objects.get(workspace=workspace, name="Avulso")
        assert validate_catalog_delete(billing_type) is None
