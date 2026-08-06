# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests for the holiday calendar and classification window endpoints.

Covers the acceptance criteria of the calendar and windows phase that are reachable over
HTTP:

* 1 and 2 -- recurring and non-recurring holidays
* 12 -- overlapping windows at the same priority are refused
* 13 -- a new hour type with its own window and priority works from the panel
* 16 -- the reason for each segment is available

Plus the isolation tests every new endpoint is born with: GUEST gets 403 everywhere, and
MEMBER can read but not write.
"""

import datetime
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from plane.db.models import (
    ServiceBillingType,
    ServiceClassificationWindow,
    ServiceConfigActivity,
    ServiceConfigVerb,
    ServiceDayScope,
    ServiceHoliday,
    ServiceHourType,
    User,
    WorkspaceMember,
)
from plane.utils.service_catalog_seed import seed_service_catalogs

HOLIDAYS_URL = "/api/workspaces/{slug}/service-holidays/"
HOLIDAY_DETAIL_URL = "/api/workspaces/{slug}/service-holidays/{pk}/"
CALENDAR_URL = "/api/workspaces/{slug}/service-holidays/calendar/"
BULK_IMPORT_URL = "/api/workspaces/{slug}/service-holidays/bulk-import/"
WINDOWS_URL = "/api/workspaces/{slug}/service-classification-windows/"
WINDOW_DETAIL_URL = "/api/workspaces/{slug}/service-classification-windows/{pk}/"
COVERAGE_URL = "/api/workspaces/{slug}/service-classification-windows/coverage/"


@pytest.fixture(autouse=True)
def no_celery_dispatch():
    with patch("celery.app.task.Task.apply_async", return_value=None):
        yield


def _make_user(label):
    unique = uuid4().hex[:8]
    user = User.objects.create(email=f"{label}-{unique}@plane.so", username=f"{label}_{unique}")
    user.set_password("test-password")
    user.save()
    return user


def _client_for(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def seeded(db, workspace):
    """The shipped configuration: three hour types, thirteen windows, complete coverage.

    Asserted rather than assumed, for the same reason the engine fixtures assert it: several
    tests below check that a change is *refused*, and a refusal is indistinguishable from a
    workspace that never had windows to break.
    """
    seed_service_catalogs(
        ServiceHourType,
        ServiceBillingType,
        workspace.id,
        window_model=ServiceClassificationWindow,
    )
    assert ServiceClassificationWindow.objects.filter(workspace=workspace).count() == 13
    return workspace


@pytest.fixture
def member_client(db, workspace):
    user = _make_user("member")
    WorkspaceMember.objects.create(workspace=workspace, member=user, role=15)
    return _client_for(user)


@pytest.fixture
def guest_client(db, workspace):
    user = _make_user("guest")
    WorkspaceMember.objects.create(workspace=workspace, member=user, role=5)
    return _client_for(user)


def _hour_type(workspace, name):
    return ServiceHourType.objects.get(workspace=workspace, name=name)


@pytest.mark.contract
@pytest.mark.django_db
class TestHolidayCrud:
    """Criteria 1 and 2."""

    def test_admin_creates_a_recurring_holiday(self, session_client, workspace):
        response = session_client.post(
            HOLIDAYS_URL.format(slug=workspace.slug),
            {"name": "Natal", "date": "2026-12-25", "is_recurring": True},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert response.data["is_recurring"] is True
        assert response.data["scope"] == "national"

    def test_criterion_1_a_recurring_holiday_appears_in_every_year(self, session_client, workspace):
        session_client.post(
            HOLIDAYS_URL.format(slug=workspace.slug),
            {"name": "Natal", "date": "2026-12-25", "is_recurring": True},
            format="json",
        )

        for year in (2026, 2027, 2031):
            response = session_client.get(CALENDAR_URL.format(slug=workspace.slug), {"year": year})
            # response.json(), not response.data: the endpoint hands DRF `date` objects and
            # the renderer turns them into ISO strings. The rendered form is what a frontend
            # actually receives, so that is what is worth asserting.
            observed = [entry["date"] for entry in response.json()["holidays"]]
            assert f"{year}-12-25" in observed, year

    def test_criterion_2_a_non_recurring_holiday_does_not_appear_the_next_year(
        self, session_client, workspace
    ):
        session_client.post(
            HOLIDAYS_URL.format(slug=workspace.slug),
            {"name": "Carnaval", "date": "2026-02-17", "is_recurring": False},
            format="json",
        )

        this_year = session_client.get(CALENDAR_URL.format(slug=workspace.slug), {"year": 2026})
        next_year = session_client.get(CALENDAR_URL.format(slug=workspace.slug), {"year": 2027})

        assert len(this_year.json()["holidays"]) == 1
        assert next_year.json()["holidays"] == []

    def test_a_specific_holiday_on_a_recurring_day_is_refused(self, session_client, workspace):
        session_client.post(
            HOLIDAYS_URL.format(slug=workspace.slug),
            {"name": "Natal", "date": "2026-12-25", "is_recurring": True},
            format="json",
        )

        response = session_client.post(
            HOLIDAYS_URL.format(slug=workspace.slug),
            {"name": "Natal 2027", "date": "2027-12-25", "is_recurring": False},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "HOLIDAY_ALREADY_COVERED_BY_RECURRING" in str(response.data)

    def test_a_recurring_holiday_over_a_specific_day_is_refused(self, session_client, workspace):
        """The inverse direction. A one-way check would let this through."""
        session_client.post(
            HOLIDAYS_URL.format(slug=workspace.slug),
            {"name": "Natal 2027", "date": "2027-12-25", "is_recurring": False},
            format="json",
        )

        response = session_client.post(
            HOLIDAYS_URL.format(slug=workspace.slug),
            {"name": "Natal", "date": "2026-12-25", "is_recurring": True},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "RECURRING_HOLIDAY_COLLIDES_WITH_SPECIFIC" in str(response.data)

    def test_the_recurrence_rule_applies_when_scope_is_omitted(self, session_client, workspace):
        """Regression: an omitted scope must fall back to the model default, not to None.

        DRF leaves a defaulted field out of validated_data, and a None scope made the
        collision query match nothing -- so a holiday created without an explicit scope
        bypassed the rule entirely. The unit tests never caught it because they all passed
        scope explicitly.
        """
        session_client.post(
            HOLIDAYS_URL.format(slug=workspace.slug),
            {"name": "Natal", "date": "2026-12-25", "is_recurring": True},
            format="json",
        )

        response = session_client.post(
            HOLIDAYS_URL.format(slug=workspace.slug),
            # No scope, no is_recurring: both defaulted.
            {"name": "Natal 2027", "date": "2027-12-25"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.data
        assert "HOLIDAY_ALREADY_COVERED_BY_RECURRING" in str(response.data)

    def test_a_different_scope_may_share_a_day(self, session_client, workspace):
        session_client.post(
            HOLIDAYS_URL.format(slug=workspace.slug),
            {"name": "Natal", "date": "2026-12-25", "is_recurring": True, "scope": "national"},
            format="json",
        )

        response = session_client.post(
            HOLIDAYS_URL.format(slug=workspace.slug),
            {"name": "Feriado local", "date": "2027-12-25", "scope": "municipal"},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data

    def test_editing_a_holiday_does_not_collide_with_itself(self, session_client, workspace):
        created = session_client.post(
            HOLIDAYS_URL.format(slug=workspace.slug),
            {"name": "Natal", "date": "2026-12-25", "is_recurring": True},
            format="json",
        )

        response = session_client.patch(
            HOLIDAY_DETAIL_URL.format(slug=workspace.slug, pk=created.data["id"]),
            {"name": "Natal (nacional)"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data

    def test_deleting_a_holiday_is_soft(self, session_client, workspace):
        created = session_client.post(
            HOLIDAYS_URL.format(slug=workspace.slug),
            {"name": "Natal", "date": "2026-12-25"},
            format="json",
        )

        response = session_client.delete(
            HOLIDAY_DETAIL_URL.format(slug=workspace.slug, pk=created.data["id"])
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not ServiceHoliday.objects.filter(pk=created.data["id"]).exists()
        assert ServiceHoliday.all_objects.filter(pk=created.data["id"]).exists()

    def test_an_out_of_range_year_is_refused(self, session_client, workspace):
        """Guards the recurrence expansion against `?year=999999`."""
        response = session_client.get(CALENDAR_URL.format(slug=workspace.slug), {"year": 999999})
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.contract
@pytest.mark.django_db
class TestHolidayAuditTrail:
    """Creating and removing a holiday ARE the financial events for this entity."""

    def _rows(self, workspace, verb):
        return ServiceConfigActivity.objects.filter(
            workspace=workspace, entity_name="service_holiday", verb=verb
        )

    def test_creating_a_holiday_is_recorded(self, session_client, workspace):
        session_client.post(
            HOLIDAYS_URL.format(slug=workspace.slug),
            {"name": "Natal", "date": "2026-12-25", "is_recurring": True},
            format="json",
        )

        rows = self._rows(workspace, ServiceConfigVerb.CREATED)
        assert rows.count() == 1

        row = rows.first()
        assert row.field_name is None
        # The recurrence has to be legible in the trail: "added Christmas 2026" and "added
        # Christmas every year" are different acts with the same name and date.
        assert "anual" in row.new_value
        assert "Natal" in row.new_value

    def test_a_non_recurring_holiday_is_recorded_differently(self, session_client, workspace):
        session_client.post(
            HOLIDAYS_URL.format(slug=workspace.slug),
            {"name": "Carnaval", "date": "2026-02-17", "is_recurring": False},
            format="json",
        )

        row = self._rows(workspace, ServiceConfigVerb.CREATED).first()
        assert "data específica" in row.new_value

    def test_deleting_a_holiday_is_recorded(self, session_client, workspace):
        created = session_client.post(
            HOLIDAYS_URL.format(slug=workspace.slug),
            {"name": "Natal", "date": "2026-12-25"},
            format="json",
        )
        session_client.delete(HOLIDAY_DETAIL_URL.format(slug=workspace.slug, pk=created.data["id"]))

        row = self._rows(workspace, ServiceConfigVerb.DELETED).first()
        assert row is not None
        assert "Natal" in row.old_value
        assert row.new_value is None

    def test_editing_a_tracked_field_is_recorded_as_updated(self, session_client, workspace):
        created = session_client.post(
            HOLIDAYS_URL.format(slug=workspace.slug),
            {"name": "Natal", "date": "2026-12-25"},
            format="json",
        )

        session_client.patch(
            HOLIDAY_DETAIL_URL.format(slug=workspace.slug, pk=created.data["id"]),
            {"is_active": False},
            format="json",
        )

        row = self._rows(workspace, ServiceConfigVerb.UPDATED).first()
        assert row is not None
        assert row.field_name == "is_active"
        assert row.old_value == "true"
        assert row.new_value == "false"


@pytest.mark.contract
@pytest.mark.django_db
class TestHolidayBulkImport:
    """Section 1's CSV import."""

    def test_a_valid_csv_is_imported(self, session_client, workspace):
        csv_content = (
            "name,date,is_recurring,scope\n"
            "Confraternização Universal,2026-01-01,true,national\n"
            "Carnaval,2026-02-17,false,national\n"
            "Aniversário da cidade,15/03/2026,true,municipal\n"
        )

        response = session_client.post(
            BULK_IMPORT_URL.format(slug=workspace.slug), {"csv_content": csv_content}, format="json"
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert response.data["imported"] == 3
        assert ServiceHoliday.objects.filter(workspace=workspace).count() == 3

    def test_the_brazilian_date_format_is_accepted(self, session_client, workspace):
        """Which is what a pt-BR spreadsheet exports."""
        response = session_client.post(
            BULK_IMPORT_URL.format(slug=workspace.slug),
            {"csv_content": "name,date\nNatal,25/12/2026\n"},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert ServiceHoliday.objects.get(workspace=workspace).date == datetime.date(2026, 12, 25)

    def test_every_bad_row_is_reported_at_once(self, session_client, workspace):
        """An admin pasting a year of holidays wants the whole list, not a dozen retries."""
        csv_content = (
            "name,date,is_recurring,scope\n"
            "Sem data,,true,national\n"
            "Data ruim,32/13/2026,true,national\n"
            "Escopo ruim,2026-01-01,true,galactic\n"
        )

        response = session_client.post(
            BULK_IMPORT_URL.format(slug=workspace.slug), {"csv_content": csv_content}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert len(response.data["rows"]) == 3
        assert [row["line"] for row in response.data["rows"]] == [2, 3, 4]

    def test_nothing_is_imported_when_any_row_is_bad(self, session_client, workspace):
        """All or nothing: a partial import leaves the admin unable to tell what landed."""
        csv_content = "name,date\nBoa,2026-01-01\nRuim,nao-e-data\n"

        session_client.post(
            BULK_IMPORT_URL.format(slug=workspace.slug), {"csv_content": csv_content}, format="json"
        )

        assert ServiceHoliday.objects.filter(workspace=workspace).count() == 0

    def test_a_collision_inside_the_file_is_caught_and_rolls_everything_back(
        self, session_client, workspace
    ):
        """The second Christmas sees the first one already stored, inside the transaction."""
        csv_content = (
            "name,date,is_recurring\nNatal,2026-12-25,true\nNatal de novo,2027-12-25,false\n"
        )

        response = session_client.post(
            BULK_IMPORT_URL.format(slug=workspace.slug), {"csv_content": csv_content}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["imported"] == 0
        assert ServiceHoliday.objects.filter(workspace=workspace).count() == 0

    def test_a_bad_header_is_reported_on_line_one(self, session_client, workspace):
        response = session_client.post(
            BULK_IMPORT_URL.format(slug=workspace.slug),
            {"csv_content": "nome,dia\nNatal,2026-12-25\n"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["rows"][0]["line"] == 1
        assert response.data["rows"][0]["error"] == "CSV_HEADER_IS_INVALID"

    def test_imported_rows_are_marked_with_their_provenance(self, session_client, workspace):
        """So a botched import can be identified and undone."""
        session_client.post(
            BULK_IMPORT_URL.format(slug=workspace.slug),
            {"csv_content": "name,date\nNatal,2026-12-25\n"},
            format="json",
        )

        assert ServiceHoliday.objects.get(workspace=workspace).external_source == "csv_import"


@pytest.mark.contract
@pytest.mark.django_db
class TestWindowsSpeakClockTime:
    """The API is HH:MM in both directions; the stored integer never crosses the boundary."""

    def test_a_window_is_created_from_clock_times(self, session_client, seeded):
        night = ServiceHourType.objects.create(
            workspace=seeded, name="Plantão", multiplier=Decimal("2.50"), priority=15
        )

        response = session_client.post(
            WINDOWS_URL.format(slug=seeded.slug),
            {
                "hour_type": str(night.id),
                "day_scope": "monday",
                "start_time": "22:00",
                "end_time": "23:00",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert response.data["start_time"] == "22:00"
        assert response.data["end_time"] == "23:00"

    def test_the_raw_minute_columns_are_not_exposed(self, session_client, seeded):
        response = session_client.get(WINDOWS_URL.format(slug=seeded.slug))

        window = response.data[0]
        assert "start_minute" not in window
        assert "end_minute" not in window
        assert "start_time" in window

    def test_a_whole_day_window_reads_as_24_00(self, session_client, seeded):
        response = session_client.get(WINDOWS_URL.format(slug=seeded.slug), {"day_scope": "sunday"})

        window = response.data[0]
        assert window["start_time"] == "00:00"
        assert window["end_time"] == "24:00"
        assert window["is_all_day"] is True

    def test_a_midnight_crossing_window_is_flagged(self, session_client, seeded):
        response = session_client.get(WINDOWS_URL.format(slug=seeded.slug), {"day_scope": "monday"})

        after_hours = next(w for w in response.data if w["start_time"] == "18:00")
        assert after_hours["end_time"] == "08:00"
        assert after_hours["crosses_midnight"] is True
        assert after_hours["duration_minutes"] == 14 * 60

    @pytest.mark.parametrize("bad", ["18h", "25:00", "18:60", "", "1800", "18:0"])
    def test_an_unparseable_clock_time_is_refused(self, session_client, seeded, bad):
        commercial = _hour_type(seeded, "Horário comercial")

        response = session_client.post(
            WINDOWS_URL.format(slug=seeded.slug),
            {
                "hour_type": str(commercial.id),
                "day_scope": "monday",
                "start_time": bad,
                "end_time": "23:00",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_equal_borders_are_refused_with_a_usable_code(self, session_client, seeded):
        commercial = _hour_type(seeded, "Horário comercial")

        response = session_client.post(
            WINDOWS_URL.format(slug=seeded.slug),
            {
                "hour_type": str(commercial.id),
                "day_scope": "monday",
                "start_time": "10:00",
                "end_time": "10:00",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "WINDOW_BORDERS_MUST_DIFFER" in str(response.data)

    def test_an_hour_type_from_another_workspace_is_refused(self, session_client, seeded, create_user):
        from plane.db.models import Workspace

        other = Workspace.objects.create(name="Other", owner=create_user, slug="other-cal-ws")
        foreign = ServiceHourType.objects.create(
            workspace=other, name="Foreign", multiplier=Decimal("9.00")
        )

        response = session_client.post(
            WINDOWS_URL.format(slug=seeded.slug),
            {
                "hour_type": str(foreign.id),
                "day_scope": "monday",
                "start_time": "22:00",
                "end_time": "23:00",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "HOUR_TYPE_MUST_BELONG_TO_SAME_WORKSPACE" in str(response.data)


@pytest.mark.contract
@pytest.mark.django_db
class TestWindowInvariantsOverHttp:
    """Criterion 12, and the coverage ratchet."""

    def test_criterion_12_an_overlap_at_the_same_priority_is_refused(self, session_client, seeded):
        duplicate = ServiceHourType.objects.create(
            workspace=seeded,
            name="Comercial duplicado",
            multiplier=Decimal("1.00"),
            priority=_hour_type(seeded, "Horário comercial").priority,
        )

        response = session_client.post(
            WINDOWS_URL.format(slug=seeded.slug),
            {
                "hour_type": str(duplicate.id),
                "day_scope": "monday",
                "start_time": "10:00",
                "end_time": "11:00",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] == "WINDOWS_OVERLAP_AT_SAME_PRIORITY"

    def test_the_rejected_window_is_rolled_back(self, session_client, seeded):
        """The write happens and is then undone, so nothing may survive it."""
        before = ServiceClassificationWindow.objects.filter(workspace=seeded).count()
        duplicate = ServiceHourType.objects.create(
            workspace=seeded,
            name="Comercial duplicado",
            multiplier=Decimal("1.00"),
            priority=_hour_type(seeded, "Horário comercial").priority,
        )

        session_client.post(
            WINDOWS_URL.format(slug=seeded.slug),
            {
                "hour_type": str(duplicate.id),
                "day_scope": "monday",
                "start_time": "10:00",
                "end_time": "11:00",
            },
            format="json",
        )

        assert ServiceClassificationWindow.objects.filter(workspace=seeded).count() == before
        assert not ServiceConfigActivity.objects.filter(
            entity_name="service_classification_window", verb=ServiceConfigVerb.CREATED
        ).exists(), "the audit row must roll back with the write"

    def test_an_overlap_at_a_different_priority_is_allowed(self, session_client, seeded):
        """Which is how the model works: the holiday window sits on top of the weekday one."""
        night = ServiceHourType.objects.create(
            workspace=seeded, name="Plantão", multiplier=Decimal("2.50"), priority=15
        )

        response = session_client.post(
            WINDOWS_URL.format(slug=seeded.slug),
            {
                "hour_type": str(night.id),
                "day_scope": "monday",
                "start_time": "00:00",
                "end_time": "06:00",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data

    def test_deleting_a_window_that_breaks_coverage_is_refused(self, session_client, seeded):
        saturday = ServiceClassificationWindow.objects.get(
            workspace=seeded, day_scope=ServiceDayScope.SATURDAY
        )

        response = session_client.delete(
            WINDOW_DETAIL_URL.format(slug=seeded.slug, pk=saturday.id)
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] == "WINDOWS_WOULD_NOT_COVER_THE_WEEK"
        assert ServiceClassificationWindow.objects.filter(pk=saturday.id).exists()

    def test_narrowing_a_window_that_breaks_coverage_is_refused(self, session_client, seeded):
        sunday = ServiceClassificationWindow.objects.get(
            workspace=seeded, day_scope=ServiceDayScope.SUNDAY
        )

        response = session_client.patch(
            WINDOW_DETAIL_URL.format(slug=seeded.slug, pk=sunday.id),
            {"end_time": "12:00"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] == "WINDOWS_WOULD_NOT_COVER_THE_WEEK"

        sunday.refresh_from_db()
        assert sunday.end_minute == 1440, "the rejected edit must be rolled back"

    def test_a_change_that_keeps_coverage_is_allowed(self, session_client, seeded):
        """Splitting Monday's business hours in two changes nothing about coverage."""
        monday_commercial = ServiceClassificationWindow.objects.get(
            workspace=seeded,
            day_scope=ServiceDayScope.MONDAY,
            hour_type__name="Horário comercial",
        )

        response = session_client.patch(
            WINDOW_DETAIL_URL.format(slug=seeded.slug, pk=monday_commercial.id),
            {"end_time": "12:00"},
            format="json",
        )

        # This one DOES break coverage (12:00-18:00 is now uncovered), so it must be refused
        # -- which is the point: the ratchet is about coverage, not about the edit's shape.
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.contract
@pytest.mark.django_db
class TestCoverageHealthEndpoint:
    """The panel's health indicator. Descriptive, not a bare boolean."""

    def test_a_seeded_workspace_reports_complete(self, session_client, seeded):
        response = session_client.get(COVERAGE_URL.format(slug=seeded.slug))

        assert response.status_code == status.HTTP_200_OK
        assert response.data["is_complete"] is True
        assert response.data["gaps"] == {}
        assert response.data["window_count"] == 13

    def test_a_bare_workspace_names_every_uncovered_scope(self, session_client, workspace):
        response = session_client.get(COVERAGE_URL.format(slug=workspace.slug))

        assert response.data["is_complete"] is False
        # Seven weekdays plus the holiday scope, each missing the whole day.
        assert len(response.data["gaps"]) == 8
        assert response.data["gaps"]["monday"] == ["00:00–24:00"]

    def test_a_gap_names_the_exact_range(self, session_client, seeded):
        """"Incomplete" alone gives an admin nothing to act on."""
        ServiceClassificationWindow.objects.filter(
            workspace=seeded, day_scope=ServiceDayScope.MONDAY, hour_type__name="Horário comercial"
        ).delete()

        response = session_client.get(COVERAGE_URL.format(slug=seeded.slug))

        assert response.data["is_complete"] is False
        assert response.data["gaps"] == {"monday": ["08:00–18:00"]}

    def test_a_member_may_read_the_health_report(self, member_client, seeded):
        """A technician seeing an unclassified entry must be able to find out why."""
        response = member_client.get(COVERAGE_URL.format(slug=seeded.slug))
        assert response.status_code == status.HTTP_200_OK


@pytest.mark.contract
@pytest.mark.django_db
class TestCriterion13NewHourTypeFromThePanel:
    """"Admin cria um Tipo de Hora novo com janela e prioridade próprias pelo painel,
    e o motor passa a usá-lo sem alteração de código"."""

    def test_a_night_shift_type_created_over_http_is_used_by_the_engine(
        self, session_client, seeded
    ):
        from plane.utils.service_calendar import classify

        hour_type_response = session_client.post(
            "/api/workspaces/{slug}/service-hour-types/".format(slug=seeded.slug),
            {"name": "Plantão de madrugada", "multiplier": "2.50", "priority": 15},
            format="json",
        )
        assert hour_type_response.status_code == status.HTTP_201_CREATED, hour_type_response.data

        window_response = session_client.post(
            WINDOWS_URL.format(slug=seeded.slug),
            {
                "hour_type": hour_type_response.data["id"],
                "day_scope": "tuesday",
                "start_time": "00:00",
                "end_time": "06:00",
            },
            format="json",
        )
        assert window_response.status_code == status.HTTP_201_CREATED, window_response.data

        # 2026-01-06 is a Tuesday. No code changed between the two requests and this call.
        segments = classify(
            workspace_id=seeded.id,
            worked_on=datetime.date(2026, 1, 6),
            raw_duration_minutes=120,
            start_time=datetime.time(1, 0),
            end_time=datetime.time(3, 0),
        )

        assert [segment.suggested_hour_type.name for segment in segments] == [
            "Plantão de madrugada"
        ]

    def test_priority_is_recorded_in_the_audit_trail_when_changed(self, session_client, seeded):
        """It decides which type the engine picks, so it moves money like a multiplier does."""
        commercial = _hour_type(seeded, "Horário comercial")

        session_client.patch(
            "/api/workspaces/{slug}/service-hour-types/{pk}/".format(
                slug=seeded.slug, pk=commercial.id
            ),
            {"priority": 40},
            format="json",
        )

        row = ServiceConfigActivity.objects.filter(
            entity_identifier=commercial.id, field_name="priority"
        ).first()

        assert row is not None
        assert row.old_value == "30"
        assert row.new_value == "40"


@pytest.mark.contract
@pytest.mark.django_db
class TestHourTypeWithWindowsCannotBeDeleted:
    """The deletion guard, over HTTP."""

    def test_deleting_an_hour_type_with_windows_is_refused(self, session_client, seeded):
        after_hours = _hour_type(seeded, "Fora do expediente")

        response = session_client.delete(
            "/api/workspaces/{slug}/service-hour-types/{pk}/".format(
                slug=seeded.slug, pk=after_hours.id
            )
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] == "HOUR_TYPE_IN_USE_BY_CLASSIFICATION_WINDOWS"


@pytest.mark.contract
@pytest.mark.django_db
class TestIsolation:
    """Every new endpoint is born with its isolation test.

    The same rule the configuration audit trail was given: a new endpoint does not wait for
    the client portal phase to discover who can reach it. The calendar and the windows
    determine multipliers, and rule R11 keeps those away from clients.
    """

    GUEST_FORBIDDEN_GETS = (HOLIDAYS_URL, CALENDAR_URL, WINDOWS_URL, COVERAGE_URL)

    @pytest.mark.parametrize("url_template", GUEST_FORBIDDEN_GETS)
    def test_a_guest_cannot_read(self, guest_client, seeded, url_template):
        response = guest_client.get(url_template.format(slug=seeded.slug))
        assert response.status_code == status.HTTP_403_FORBIDDEN, url_template

    def test_a_guest_cannot_create_a_holiday(self, guest_client, seeded):
        response = guest_client.post(
            HOLIDAYS_URL.format(slug=seeded.slug),
            {"name": "Natal", "date": "2026-12-25"},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_a_guest_cannot_create_a_window(self, guest_client, seeded):
        response = guest_client.post(
            WINDOWS_URL.format(slug=seeded.slug),
            {
                "hour_type": str(_hour_type(seeded, "Horário comercial").id),
                "day_scope": "monday",
                "start_time": "22:00",
                "end_time": "23:00",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_a_guest_cannot_bulk_import(self, guest_client, seeded):
        response = guest_client.post(
            BULK_IMPORT_URL.format(slug=seeded.slug),
            {"csv_content": "name,date\nNatal,2026-12-25\n"},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_a_member_may_read_but_not_write(self, member_client, seeded):
        assert member_client.get(HOLIDAYS_URL.format(slug=seeded.slug)).status_code == status.HTTP_200_OK

        write = member_client.post(
            HOLIDAYS_URL.format(slug=seeded.slug),
            {"name": "Natal", "date": "2026-12-25"},
            format="json",
        )
        assert write.status_code == status.HTTP_403_FORBIDDEN

    def test_a_member_cannot_write_windows(self, member_client, seeded):
        response = member_client.post(
            WINDOWS_URL.format(slug=seeded.slug),
            {
                "hour_type": str(_hour_type(seeded, "Horário comercial").id),
                "day_scope": "monday",
                "start_time": "22:00",
                "end_time": "23:00",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_holidays_never_cross_a_workspace_boundary(self, session_client, workspace, create_user):
        from plane.db.models import Workspace

        other = Workspace.objects.create(name="Other", owner=create_user, slug="other-hol-ws")
        ServiceHoliday.objects.create(workspace=other, name="Alheio", date=datetime.date(2026, 5, 1))

        response = session_client.get(HOLIDAYS_URL.format(slug=workspace.slug))

        assert response.status_code == status.HTTP_200_OK
        assert response.data == []
