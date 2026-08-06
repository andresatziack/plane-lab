# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Unit tests for the classification engine.

Section 4 of the phase brief says its table of scenarios is to be used "exatamente como
suíte de testes", so it is transcribed here verbatim, plus the exact boundaries it asks
for, the two rounding cases of section 6, and the three timezone cases of section 5b.

Also here, and deliberately: a characterization test for the daylight saving time
limitation. It pins what the engine does today on a transition day rather than asserting
what it should do, so that whoever fixes it later is told exactly what changed.
"""

import datetime
from decimal import Decimal

import pytest
from freezegun import freeze_time

from plane.db.models import (
    ServiceBillingType,
    ServiceClassificationWindow,
    ServiceDayScope,
    ServiceHoliday,
    ServiceHourType,
)
from plane.tests.factories import WorkspaceFactory
from plane.utils.service_calendar import (
    UNCLASSIFIED_REASON,
    classify,
    coverage_report,
    holiday_calendar,
)
from plane.utils.service_catalog_seed import seed_service_catalogs
from plane.utils.service_log_time import round_to_block, to_decimal_hours

pytestmark = pytest.mark.unit

# Anchor dates with known weekdays, so a reader does not have to work them out.
MONDAY = datetime.date(2026, 1, 5)
TUESDAY = datetime.date(2026, 1, 6)
WEDNESDAY = datetime.date(2026, 1, 7)
FRIDAY = datetime.date(2026, 1, 9)
SATURDAY = datetime.date(2026, 1, 10)
SUNDAY = datetime.date(2026, 1, 11)

COMMERCIAL = "Horário comercial"
AFTER_HOURS = "Fora do expediente"
HOLIDAYS_AND_SUNDAYS = "Domingos e feriados"


def _time(text):
    hour, minute = text.split(":")
    return datetime.time(hour=int(hour), minute=int(minute))


#: Windows the seed is expected to produce: 5 weekdays x (commercial + after hours),
#: plus Saturday, Sunday and holiday as whole days.
EXPECTED_SEEDED_WINDOW_COUNT = 13


@pytest.fixture
def workspace(db):
    """A workspace with the seeded catalogues and the seeded windows.

    Seeded rather than hand-built on purpose: these tests then verify that the *shipped*
    configuration produces rule R10, which is the claim that actually matters.

    THE ASSERTIONS IN THIS FIXTURE ARE NOT DECORATION. Every test below that expects
    "nothing was suggested" would also pass against a workspace with no windows at all --
    for entirely the wrong reason. Checking here that the seed really did create the
    configuration means a change to the seed fails loudly at setup, naming the seed,
    instead of quietly turning these tests green through the absent-configuration path.
    """
    workspace = WorkspaceFactory()
    seed_service_catalogs(
        ServiceHourType,
        ServiceBillingType,
        workspace.id,
        window_model=ServiceClassificationWindow,
    )

    window_count = ServiceClassificationWindow.objects.filter(workspace=workspace).count()
    assert window_count == EXPECTED_SEEDED_WINDOW_COUNT, (
        f"the seed produced {window_count} windows, expected {EXPECTED_SEEDED_WINDOW_COUNT}. "
        "These tests assert classification behaviour and would silently pass through the "
        "unconfigured path if the seed stopped creating windows."
    )
    assert ServiceHourType.objects.filter(workspace=workspace).count() == 3

    return workspace


def _classify(workspace, worked_on, start=None, end=None, raw_duration_minutes=60):
    return classify(
        workspace_id=workspace.id,
        worked_on=worked_on,
        raw_duration_minutes=raw_duration_minutes,
        start_time=_time(start) if start else None,
        end_time=_time(end) if end else None,
    )


def _summary(segments):
    """``[(hours, hour type name), ...]`` -- the shape section 4's table is written in."""
    return [
        (
            segment.raw_duration_minutes / 60,
            segment.suggested_hour_type.name if segment.suggested_hour_type else None,
        )
        for segment in segments
    ]


class TestSeededConfigurationIsSound:
    """Before trusting any classification, the shipped configuration has to be valid."""

    def test_the_seed_covers_the_whole_week(self, workspace):
        report = coverage_report(workspace.id)
        assert report["is_complete"] is True, report["gaps"]
        assert report["gaps"] == {}

    def test_the_seed_has_no_overlap_at_the_same_day_scope_and_priority(self, workspace):
        assert coverage_report(workspace.id)["overlaps"] == []

    def test_the_seeded_priorities_order_holiday_over_weekday(self, workspace):
        priorities = dict(
            ServiceHourType.objects.filter(workspace=workspace).values_list("name", "priority")
        )
        assert priorities[HOLIDAYS_AND_SUNDAYS] < priorities[AFTER_HOURS] < priorities[COMMERCIAL]


class TestSection4ReferenceTable:
    """The twelve scenarios of section 4, in order."""

    def test_monday_14_to_16_is_one_commercial_segment(self, workspace):
        assert _summary(_classify(workspace, MONDAY, "14:00", "16:00")) == [(2.0, COMMERCIAL)]

    def test_monday_17_to_20_splits_into_commercial_then_after_hours(self, workspace):
        assert _summary(_classify(workspace, MONDAY, "17:00", "20:00")) == [
            (1.0, COMMERCIAL),
            (2.0, AFTER_HOURS),
        ]

    def test_monday_22_to_tuesday_01_crosses_midnight_without_splitting(self, workspace):
        """The case that makes "emit on change, not on midnight" load bearing."""
        assert _summary(_classify(workspace, MONDAY, "22:00", "01:00")) == [(3.0, AFTER_HOURS)]

    def test_friday_17_to_saturday_02_splits_once(self, workspace):
        assert _summary(_classify(workspace, FRIDAY, "17:00", "02:00")) == [
            (1.0, COMMERCIAL),
            (8.0, AFTER_HOURS),
        ]

    def test_saturday_22_to_sunday_02_splits_at_midnight(self, workspace):
        """Saturday is 1.5 and Sunday is 2.0, so midnight is a real boundary here."""
        assert _summary(_classify(workspace, SATURDAY, "22:00", "02:00")) == [
            (2.0, AFTER_HOURS),
            (2.0, HOLIDAYS_AND_SUNDAYS),
        ]

    def test_sunday_23_to_monday_02_splits_at_midnight(self, workspace):
        assert _summary(_classify(workspace, SUNDAY, "23:00", "02:00")) == [
            (1.0, HOLIDAYS_AND_SUNDAYS),
            (2.0, AFTER_HOURS),
        ]

    def test_a_holiday_10_to_12_is_one_holiday_segment(self, workspace):
        ServiceHoliday.objects.create(workspace=workspace, name="Natal", date=datetime.date(2026, 12, 25))
        christmas = datetime.date(2026, 12, 25)

        assert _summary(_classify(workspace, christmas, "10:00", "12:00")) == [
            (2.0, HOLIDAYS_AND_SUNDAYS)
        ]

    def test_a_holiday_on_a_weekday_beats_the_weekday_window(self, workspace):
        """Section 4's "Qua 10:00-12:00, sendo a Qua um feriado"."""
        ServiceHoliday.objects.create(workspace=workspace, name="Feriado local", date=WEDNESDAY)

        assert _summary(_classify(workspace, WEDNESDAY, "10:00", "12:00")) == [
            (2.0, HOLIDAYS_AND_SUNDAYS)
        ]

    def test_saturday_09_to_11_is_after_hours_not_sunday_rate(self, workspace):
        """1.5, not 2.0. The distinction no "working day" flag could express."""
        segments = _classify(workspace, SATURDAY, "09:00", "11:00")
        assert _summary(segments) == [(2.0, AFTER_HOURS)]
        assert segments[0].suggested_hour_type.multiplier == Decimal("1.50")

    def test_monday_07_to_09_splits_at_the_start_of_business_hours(self, workspace):
        assert _summary(_classify(workspace, MONDAY, "07:00", "09:00")) == [
            (1.0, AFTER_HOURS),
            (1.0, COMMERCIAL),
        ]

    def test_duration_mode_on_a_sunday_is_classified_by_date(self, workspace):
        segments = _classify(workspace, SUNDAY, raw_duration_minutes=180)
        assert _summary(segments) == [(3.0, HOLIDAYS_AND_SUNDAYS)]
        assert len(segments) == 1

    def test_duration_mode_on_a_tuesday_leaves_the_choice_to_the_technician(self, workspace):
        """R10: on a weekday there is no way to infer the hour, so nothing is suggested.

        Asserts the *reason* as well as the absence, and that is the point. An empty
        reason means "the rule says the technician chooses"; ``UNCLASSIFIED_REASON`` would
        mean "this workspace has no windows for Tuesdays". Both produce a null suggestion,
        so a test that only checked the null would pass against a broken configuration.
        """
        segments = _classify(workspace, TUESDAY, raw_duration_minutes=180)

        assert _summary(segments) == [(3.0, None)]
        assert len(segments) == 1
        assert segments[0].reason == ""

    def test_the_tuesday_case_is_a_rule_and_not_a_missing_configuration(self, workspace):
        """Positive control for the test above, in the same fixture.

        Proves the engine is live and classifying: the very same workspace does suggest an
        hour type for a Sunday. Without this, "nothing suggested on Tuesday" is compatible
        with the engine being switched off entirely.
        """
        assert _classify(workspace, SUNDAY, raw_duration_minutes=180)[0].suggested_hour_type
        assert _classify(workspace, TUESDAY, raw_duration_minutes=180)[0].suggested_hour_type is None


class TestExactBoundaries:
    """The two limits section 4 asks for explicitly.

    Both follow from ``contains_minute`` being half-open: the start border is inside the
    window and the end border is not.
    """

    def test_work_ending_exactly_at_18_00_is_entirely_commercial(self, workspace):
        assert _summary(_classify(workspace, MONDAY, "17:00", "18:00")) == [(1.0, COMMERCIAL)]

    def test_work_starting_exactly_at_08_00_is_entirely_commercial(self, workspace):
        assert _summary(_classify(workspace, MONDAY, "08:00", "09:00")) == [(1.0, COMMERCIAL)]

    def test_work_ending_exactly_at_08_00_is_entirely_after_hours(self, workspace):
        assert _summary(_classify(workspace, MONDAY, "07:00", "08:00")) == [(1.0, AFTER_HOURS)]

    def test_work_starting_exactly_at_18_00_is_entirely_after_hours(self, workspace):
        assert _summary(_classify(workspace, MONDAY, "18:00", "19:00")) == [(1.0, AFTER_HOURS)]

    def test_a_full_business_day_is_one_segment(self, workspace):
        assert _summary(_classify(workspace, MONDAY, "08:00", "18:00")) == [(10.0, COMMERCIAL)]


class TestNoSplitOnMidnightWithoutAChange:
    """Section 4's third case, generalised."""

    @pytest.mark.parametrize(
        ("start", "end", "expected_hours"),
        [
            ("22:00", "01:00", 3.0),
            ("23:00", "02:00", 3.0),
            ("19:00", "07:00", 12.0),
            ("18:00", "08:00", 14.0),  # the whole after-hours window, end to end
        ],
    )
    def test_a_weekday_night_stays_one_segment(self, workspace, start, end, expected_hours):
        segments = _classify(workspace, MONDAY, start, end)
        assert _summary(segments) == [(expected_hours, AFTER_HOURS)]

    def test_the_segment_keeps_the_date_it_started_on(self, workspace):
        segments = _classify(workspace, MONDAY, "22:00", "01:00")
        assert segments[0].worked_on == MONDAY
        assert segments[0].start_time == datetime.time(22, 0)
        assert segments[0].end_time == datetime.time(1, 0)


class TestPerSegmentDates:
    """Section 5 and rule R7: each segment carries its own date."""

    def test_a_split_across_midnight_dates_each_segment_correctly(self, workspace):
        segments = _classify(workspace, SATURDAY, "22:00", "02:00")

        assert segments[0].worked_on == SATURDAY
        assert segments[1].worked_on == SUNDAY

    def test_a_split_across_a_month_boundary_lands_in_two_competencies(self, workspace):
        """Section 5's own example: 31/01 23:00 to 01/02 01:00.

        31/01/2026 is a Saturday and 01/02/2026 a Sunday, so this splits on rate as well
        as on date -- which is exactly why using the start date for both would misbill.
        """
        segments = _classify(workspace, datetime.date(2026, 1, 31), "23:00", "01:00")

        assert [segment.worked_on for segment in segments] == [
            datetime.date(2026, 1, 31),
            datetime.date(2026, 2, 1),
        ]
        assert [segment.worked_on.month for segment in segments] == [1, 2]


class TestSection6Rounding:
    """The two rounding cases of section 6.

    Rounding itself belongs to the work log phase, so what is checked here is that the
    engine hands it segments that produce the documented result.
    """

    def test_17_50_to_18_10_becomes_two_quarter_hours(self, workspace):
        """Section 6's example, and criterion 10: 20 raw minutes bill 30."""
        segments = _classify(workspace, MONDAY, "17:50", "18:10")

        assert [segment.raw_duration_minutes for segment in segments] == [10, 10]
        billed = [round_to_block(segment.raw_duration_minutes) for segment in segments]
        assert billed == [15, 15]
        assert sum(to_decimal_hours(minutes) for minutes in billed) == Decimal("0.5000")

    def test_17_57_to_18_03_is_split_by_the_engine_and_collapsed_by_the_guardrail(self, workspace):
        """Criterion 11. The engine splits; the guardrail in the work log phase collapses.

        The division of labour is the point: the engine reports what the timeline says
        (two bands were crossed), and the guardrail decides that six minutes must not
        become half an hour.
        """
        from plane.utils.service_log import apply_minimum_block_guardrail

        segments = _classify(workspace, MONDAY, "17:57", "18:03")
        assert len(segments) == 2

        collapsed = apply_minimum_block_guardrail(segments)
        assert len(collapsed) == 1
        assert collapsed[0].raw_duration_minutes == 6
        assert to_decimal_hours(round_to_block(collapsed[0].raw_duration_minutes)) == Decimal("0.2500")


class TestHolidays:
    """Section 1, and the recurrence rules."""

    def test_a_recurring_holiday_applies_in_every_year(self, workspace):
        """Criterion 1: 25/12 registered once, observed always."""
        ServiceHoliday.objects.create(
            workspace=workspace, name="Natal", date=datetime.date(2026, 12, 25), is_recurring=True
        )

        for year in (2026, 2027, 2030):
            segments = _classify(workspace, datetime.date(year, 12, 25), "10:00", "12:00")
            assert _summary(segments) == [(2.0, HOLIDAYS_AND_SUNDAYS)], year

    def test_a_non_recurring_holiday_does_not_apply_the_following_year(self, workspace):
        """Criterion 2: Carnival moves, so it is registered year by year."""
        ServiceHoliday.objects.create(
            workspace=workspace, name="Carnaval", date=datetime.date(2026, 2, 17), is_recurring=False
        )

        assert _summary(_classify(workspace, datetime.date(2026, 2, 17), "10:00", "12:00")) == [
            (2.0, HOLIDAYS_AND_SUNDAYS)
        ]
        # 17/02/2027 is a Wednesday, so it falls back to business hours.
        assert _summary(_classify(workspace, datetime.date(2027, 2, 17), "10:00", "12:00")) == [
            (2.0, COMMERCIAL)
        ]

    def test_an_inactive_holiday_is_ignored(self, workspace):
        ServiceHoliday.objects.create(
            workspace=workspace, name="Revogado", date=WEDNESDAY, is_active=False
        )

        assert _summary(_classify(workspace, WEDNESDAY, "10:00", "12:00")) == [(2.0, COMMERCIAL)]

    def test_the_reason_names_the_holiday(self, workspace):
        """Section 7: the UI has to be able to say why."""
        ServiceHoliday.objects.create(workspace=workspace, name="Natal", date=WEDNESDAY)

        segments = _classify(workspace, WEDNESDAY, "10:00", "12:00")
        assert segments[0].reason == "Feriado: Natal"

    def test_the_reason_names_the_window_when_no_holiday_decided_it(self, workspace):
        segments = _classify(workspace, MONDAY, "19:00", "20:00")
        assert segments[0].reason == f"{AFTER_HOURS}: 18:00–08:00"

    def test_a_holiday_starting_at_midnight_splits_the_night(self, workspace):
        """Monday night into a Tuesday that is a holiday."""
        ServiceHoliday.objects.create(workspace=workspace, name="Feriado", date=TUESDAY)

        segments = _classify(workspace, MONDAY, "22:00", "02:00")

        assert _summary(segments) == [(2.0, AFTER_HOURS), (2.0, HOLIDAYS_AND_SUNDAYS)]

    def test_the_annual_calendar_expands_recurrence(self, workspace):
        ServiceHoliday.objects.create(
            workspace=workspace, name="Natal", date=datetime.date(2020, 12, 25), is_recurring=True
        )
        ServiceHoliday.objects.create(
            workspace=workspace, name="Carnaval", date=datetime.date(2026, 2, 17)
        )

        calendar = holiday_calendar(workspace.id, 2026)
        observed = [(entry["date"], entry["name"]) for entry in calendar]

        assert (datetime.date(2026, 12, 25), "Natal") in observed
        assert (datetime.date(2026, 2, 17), "Carnaval") in observed

    def test_the_annual_calendar_excludes_other_years_specific_holidays(self, workspace):
        ServiceHoliday.objects.create(
            workspace=workspace, name="Carnaval 2027", date=datetime.date(2027, 2, 9)
        )

        assert holiday_calendar(workspace.id, 2026) == []

    def test_a_recurring_29_february_is_skipped_in_a_non_leap_year(self, workspace):
        ServiceHoliday.objects.create(
            workspace=workspace, name="Bissexto", date=datetime.date(2024, 2, 29), is_recurring=True
        )

        assert holiday_calendar(workspace.id, 2026) == []
        assert len(holiday_calendar(workspace.id, 2028)) == 1


class TestCustomHourTypeWithoutADeploy:
    """Criterion 13: a new hour type with its own window and priority just works."""

    def test_a_night_shift_type_at_priority_15_beats_after_hours(self, workspace):
        night = ServiceHourType.objects.create(
            workspace=workspace,
            name="Plantão de madrugada",
            multiplier=Decimal("2.50"),
            priority=15,
        )
        for scope in (
            ServiceDayScope.MONDAY,
            ServiceDayScope.TUESDAY,
            ServiceDayScope.WEDNESDAY,
            ServiceDayScope.THURSDAY,
            ServiceDayScope.FRIDAY,
        ):
            ServiceClassificationWindow.objects.create(
                workspace=workspace, hour_type=night, day_scope=scope, start_minute=0, end_minute=6 * 60
            )

        # 00:00-06:00 on a Tuesday now belongs to the new type, not to after hours.
        assert _summary(_classify(workspace, TUESDAY, "01:00", "03:00")) == [
            (2.0, "Plantão de madrugada")
        ]

    def test_priority_15_still_loses_to_a_holiday(self, workspace):
        """The ordering the phase brief's own example implies: 10 < 15 < 20."""
        night = ServiceHourType.objects.create(
            workspace=workspace,
            name="Plantão de madrugada",
            multiplier=Decimal("2.50"),
            priority=15,
        )
        ServiceClassificationWindow.objects.create(
            workspace=workspace,
            hour_type=night,
            day_scope=ServiceDayScope.TUESDAY,
            start_minute=0,
            end_minute=6 * 60,
        )
        ServiceHoliday.objects.create(workspace=workspace, name="Feriado", date=TUESDAY)

        assert _summary(_classify(workspace, TUESDAY, "01:00", "03:00")) == [
            (2.0, HOLIDAYS_AND_SUNDAYS)
        ]

    def test_an_hour_type_with_no_window_is_never_suggested(self, workspace):
        """Section 2: "Tipo de Hora sem nenhuma janela nunca é sugerido"."""
        ServiceHourType.objects.create(
            workspace=workspace, name="Sem janela", multiplier=Decimal("9.00"), priority=1
        )

        # Priority 1 would beat everything if it were ever a candidate.
        assert _summary(_classify(workspace, MONDAY, "10:00", "11:00")) == [(1.0, COMMERCIAL)]

    def test_a_deactivated_hour_type_stops_classifying(self, workspace):
        after_hours = ServiceHourType.objects.get(workspace=workspace, name=AFTER_HOURS)
        after_hours.is_active = False
        after_hours.save()

        # Its windows drop out with it, leaving the night uncovered rather than
        # misclassified -- and the engine says so instead of raising.
        segments = _classify(workspace, MONDAY, "19:00", "20:00")
        assert segments[0].suggested_hour_type is None
        assert segments[0].reason == UNCLASSIFIED_REASON


class TestTheEngineIsTotal:
    """An uncovered instant must never raise, and never block recording work.

    R10 says classification is a convenience and not a lock, and the coverage ratchet
    deliberately allows a workspace to sit incomplete -- so this state is reachable. If
    it raised, an admin halfway through editing windows would stop every technician in
    the workspace from recording work already performed.
    """

    def test_a_workspace_with_no_configuration_at_all_still_classifies(self, db):
        bare = WorkspaceFactory()

        segments = classify(
            workspace_id=bare.id,
            worked_on=MONDAY,
            raw_duration_minutes=60,
            start_time=datetime.time(10, 0),
            end_time=datetime.time(11, 0),
        )

        assert len(segments) == 1
        assert segments[0].suggested_hour_type is None
        assert segments[0].reason == UNCLASSIFIED_REASON
        assert segments[0].raw_duration_minutes == 60

    def test_a_workspace_with_no_configuration_still_classifies_duration_mode(self, db):
        """And says it is a gap, which is what distinguishes it from a normal weekday.

        Duration mode on a configured Tuesday also yields no suggestion. The reason is the
        only thing that tells the two apart, so asserting it is what stops this test and
        the Tuesday one from being the same test written twice.
        """
        bare = WorkspaceFactory()

        segments = classify(workspace_id=bare.id, worked_on=MONDAY, raw_duration_minutes=180)

        assert _summary(segments) == [(3.0, None)]
        assert segments[0].reason == UNCLASSIFIED_REASON

    def test_a_gap_in_the_middle_of_the_day_is_reported_not_raised(self, workspace):
        """Delete the commercial window and log during business hours."""
        ServiceClassificationWindow.objects.filter(
            workspace=workspace, hour_type__name=COMMERCIAL, day_scope=ServiceDayScope.MONDAY
        ).delete()

        segments = _classify(workspace, MONDAY, "10:00", "11:00")

        assert segments[0].suggested_hour_type is None
        assert segments[0].reason == UNCLASSIFIED_REASON

    def test_a_partially_uncovered_interval_splits_into_covered_and_not(self, workspace):
        ServiceClassificationWindow.objects.filter(
            workspace=workspace, hour_type__name=COMMERCIAL, day_scope=ServiceDayScope.MONDAY
        ).delete()

        # 07:00-09:00 spans after hours (covered) then the hole left above. The covered
        # half is the control: it proves the engine is resolving windows rather than
        # having given up on the whole interval.
        segments = _classify(workspace, MONDAY, "07:00", "09:00")

        assert _summary(segments) == [(1.0, AFTER_HOURS), (1.0, None)]
        assert segments[0].reason == f"{AFTER_HOURS}: 18:00–08:00"
        assert segments[1].reason == UNCLASSIFIED_REASON

    def test_the_total_duration_is_preserved_even_when_unclassified(self, workspace):
        """Whatever the classification, no time may be lost or invented."""
        ServiceClassificationWindow.objects.filter(workspace=workspace).delete()

        segments = _classify(workspace, MONDAY, "09:00", "17:30")

        assert sum(segment.raw_duration_minutes for segment in segments) == 510


class TestDurationIsAlwaysConserved:
    """A property that has to hold for every split: the parts sum to the whole."""

    @pytest.mark.parametrize(
        ("worked_on", "start", "end", "expected_minutes"),
        [
            (MONDAY, "17:00", "20:00", 180),
            (MONDAY, "22:00", "01:00", 180),
            (FRIDAY, "17:00", "02:00", 540),
            (SATURDAY, "22:00", "02:00", 240),
            (SUNDAY, "23:00", "02:00", 180),
            (MONDAY, "07:00", "09:00", 120),
            (MONDAY, "17:50", "18:10", 20),
            (MONDAY, "08:00", "18:00", 600),
        ],
    )
    def test_segments_sum_to_the_interval(self, workspace, worked_on, start, end, expected_minutes):
        segments = _classify(workspace, worked_on, start, end)
        assert sum(segment.raw_duration_minutes for segment in segments) == expected_minutes

    @pytest.mark.parametrize(
        ("worked_on", "start", "end"),
        [
            (MONDAY, "17:00", "20:00"),
            (SATURDAY, "22:00", "02:00"),
            (MONDAY, "07:00", "09:00"),
        ],
    )
    def test_segments_are_contiguous_and_ordered(self, workspace, worked_on, start, end):
        segments = _classify(workspace, worked_on, start, end)

        for previous, current in zip(segments, segments[1:]):
            assert previous.end_time == current.start_time


class TestTimezoneStrategy:
    """Section 5b. The three cases the brief asks to be covered.

    All three pass because classification is local wall clock throughout: the date and
    the borders are stored local, and the windows are local, so there is no conversion to
    get wrong. Classifying in UTC instead would bill a Sunday 23:00 in São Paulo as
    Monday 02:00 business-adjacent time -- 1.0 instead of 2.0.
    """

    @pytest.fixture
    def sao_paulo(self, db):
        workspace = WorkspaceFactory(timezone="America/Sao_Paulo")
        seed_service_catalogs(
            ServiceHourType,
            ServiceBillingType,
            workspace.id,
            window_model=ServiceClassificationWindow,
        )
        return workspace

    def test_sunday_23_00_is_sunday_rate_not_monday(self, sao_paulo):
        segments = _classify(sao_paulo, SUNDAY, "23:00", "23:59")
        assert segments[0].suggested_hour_type.name == HOLIDAYS_AND_SUNDAYS

    def test_saturday_22_to_sunday_02_splits_on_the_local_midnight(self, sao_paulo):
        assert _summary(_classify(sao_paulo, SATURDAY, "22:00", "02:00")) == [
            (2.0, AFTER_HOURS),
            (2.0, HOLIDAYS_AND_SUNDAYS),
        ]

    def test_a_whole_holiday_is_the_local_day_not_a_utc_day(self, sao_paulo):
        ServiceHoliday.objects.create(workspace=sao_paulo, name="Natal", date=WEDNESDAY)

        # Both ends of the local day, which in UTC would fall on the neighbouring dates.
        for start, end in (("00:00", "01:00"), ("23:00", "23:59")):
            segments = _classify(sao_paulo, WEDNESDAY, start, end)
            assert segments[0].suggested_hour_type.name == HOLIDAYS_AND_SUNDAYS

    @freeze_time("2026-06-15 03:00:00")
    def test_classification_does_not_depend_on_the_request_timezone(self, sao_paulo):
        """The engine takes the workspace, and reads no active timezone.

        ``TimezoneMixin`` activates the requesting user's zone per request. If the engine
        consulted it, the same work would classify differently depending on who opened the
        screen, and an invoice would depend on it too.
        """
        from django.utils import timezone as django_timezone

        django_timezone.activate("Pacific/Kiritimati")
        try:
            kiritimati_view = _summary(_classify(sao_paulo, SUNDAY, "23:00", "23:59"))
        finally:
            django_timezone.deactivate()

        assert kiritimati_view == _summary(_classify(sao_paulo, SUNDAY, "23:00", "23:59"))


class TestDaylightSavingTimeIsAKnownLimitation:
    """CHARACTERIZATION TESTS. These pin what happens today, not what should happen.

    Classification is wall-clock arithmetic, so on a day when the clock shifts, the
    engine measures wall clock and not elapsed time.

    This is **configuration-dependent, not geographically impossible**: Brazil abolished
    DST in 2019, but ``Workspace.timezone`` accepts any zone, so an installation in one
    that still observes it is exposed.

    What breaks, concretely: on a spring-forward day the seeded 18:00->08:00 window spans
    13 elapsed hours while being measured as 14 wall-clock hours; on a fall-back day it
    spans 15 and is measured as 14. A work log crossing the transition therefore carries a
    segment duration wrong by one hour, which flows into ``equivalent_hours`` and into the
    invoice. A small, rare, real financial error.

    Fixing it would require storing instants rather than local dates and times, which
    would break the "whole day" semantics a holiday depends on. If someone does fix it,
    these tests fail and say exactly what changed -- which is the point of writing them.
    """

    @pytest.fixture
    def lisbon(self, db):
        """A workspace in a zone that does observe DST.

        Europe/Lisbon springs forward at 01:00 local on the last Sunday of March: 2026-03-29.
        """
        workspace = WorkspaceFactory(timezone="Europe/Lisbon")
        seed_service_catalogs(
            ServiceHourType,
            ServiceBillingType,
            workspace.id,
            window_model=ServiceClassificationWindow,
        )
        return workspace

    def test_the_transition_day_is_measured_in_wall_clock_hours(self, lisbon):
        """KNOWN LIMITATION. 2026-03-28 is a Saturday; the clock shifts early on the 29th.

        A shift that runs 23:00 Saturday to 03:00 Sunday spans 3 elapsed hours in Lisbon,
        because 01:00 becomes 02:00. The engine reports 4 wall-clock hours.
        """
        segments = _classify(lisbon, datetime.date(2026, 3, 28), "23:00", "03:00")
        total = sum(segment.raw_duration_minutes for segment in segments)

        # Wall clock says 4 hours. Elapsed time in Europe/Lisbon is 3.
        assert total == 240, "if this is now 180, DST handling was implemented"

    def test_the_after_hours_window_is_still_measured_as_fourteen_hours(self, lisbon):
        """KNOWN LIMITATION. The 18:00->08:00 window on the night of a spring forward.

        Elapsed time is 13 hours; the engine measures the 14 hours between the borders.
        """
        segments = _classify(lisbon, datetime.date(2026, 3, 28), "18:00", "08:00")
        total = sum(segment.raw_duration_minutes for segment in segments)

        assert total == 14 * 60, "if this is now 780, DST handling was implemented"

    def test_the_classification_itself_is_unaffected(self, lisbon):
        """The band assignment stays right; only the measured duration is wrong.

        Worth pinning separately: a future fix should not have to change which hour type
        applies, only how long the segment is.
        """
        segments = _classify(lisbon, datetime.date(2026, 3, 28), "23:00", "03:00")

        assert [segment.suggested_hour_type.name for segment in segments] == [
            AFTER_HOURS,
            HOLIDAYS_AND_SUNDAYS,
        ]

    def test_a_workspace_without_dst_is_exact(self, workspace):
        """The control. The default and every Brazilian zone are unaffected."""
        segments = _classify(workspace, datetime.date(2026, 3, 28), "23:00", "03:00")
        assert sum(segment.raw_duration_minutes for segment in segments) == 240
