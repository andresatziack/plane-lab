# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""The two work log criteria that only the classification engine can close.

Criteria 8 and 9 of `03-worklog-core.md` were left **blocked** by that phase: it built
everything around the engine -- the batch, `batch_id`, per-segment rounding, the guardrail,
the preview that renders N segments, `suggested_hour_type`, `is_hour_type_overridden`,
`classification_reason` -- and had nothing to decide *where* to cut. The calendar and
windows phase supplies that, so the criteria are closed here, over HTTP, end to end.

* 8 -- a log on a holiday opens with "Domingos e feriados" already selected and the reason
  visible, **in both entry modes**
* 9 -- Monday 17:00 to 20:00 creates two grouped logs, 1h commercial and 2h after hours,
  with a preview before saving

Why a separate file: the fixtures in `test_service_log_app.py` build hour types by hand,
with no windows and no priority, which is what a workspace looked like before this phase.
Those tests keep that shape on purpose -- they assert the parser, the rounding and the
totals, none of which should depend on a calendar. Classification needs the opposite
fixture, and mixing the two would make it unclear which tests rely on which state.

**The fixture seeds the windows explicitly and then asserts the seed.** Reading them from
whatever the workspace happened to have is how a classification test passes without a
classifier: no windows means no suggestion, and "no suggestion" is exactly what a broken
engine also produces. If someone changes the seed, these tests have to fail because of
that, not go green by another route.
"""

import datetime
from decimal import Decimal
from unittest.mock import patch

import pytest
from rest_framework import status

from plane.db.models import (
    Issue,
    Project,
    ProjectMember,
    ServiceBillingType,
    ServiceClassificationWindow,
    ServiceClient,
    ServiceHoliday,
    ServiceHourType,
    ServiceLog,
    State,
)
from plane.db.models.service_calendar import ServiceDayScope
from plane.utils.service_catalog_seed import seed_service_catalogs

LIST_URL = "/api/workspaces/{slug}/projects/{project_id}/issues/{issue_id}/service-logs/"
PREVIEW_URL = "/api/workspaces/{slug}/projects/{project_id}/issues/{issue_id}/service-logs/preview/"

# 2026-01-05 is a Monday, and 2026-03-16 is a Monday chosen to carry a holiday: a holiday
# on a *business day* is what proves the holiday scope outranks the weekday window rather
# than merely coinciding with a Sunday.
A_MONDAY = "2026-01-05"
A_MONDAY_THAT_IS_A_HOLIDAY = datetime.date(2026, 3, 16)


@pytest.fixture(autouse=True)
def captured_activity():
    """Run the activity task inline instead of enqueuing it.

    The task swallows every exception by design, so without this an audit trail that
    recorded nothing would be indistinguishable from one that worked.
    """

    def run_inline(args=None, kwargs=None, **_ignored):
        from plane.bgtasks.issue_activities_task import issue_activity

        if kwargs is not None and "type" in kwargs:
            issue_activity(**kwargs)
        return None

    with patch("celery.app.task.Task.apply_async", side_effect=run_inline):
        yield


@pytest.fixture
def classifying_workspace(db, workspace):
    """A workspace with the shipped configuration, and proof of the parts used below.

    Asserts the **specific windows these tests depend on**, not a total count. A count of
    thirteen can be thirteen for the wrong reasons, whereas "Monday is commercial from 08:00
    to 18:00 and after hours from 18:00" is precisely the configuration that makes the
    17:00-to-20:00 split land where criterion 9 says it should. If the seed changes, these
    tests fail naming the window that went missing.
    """
    seed_service_catalogs(
        ServiceHourType,
        ServiceBillingType,
        workspace.id,
        window_model=ServiceClassificationWindow,
    )

    expected = {
        ("Horário comercial", ServiceDayScope.MONDAY, 8 * 60, 18 * 60),
        ("Fora do expediente", ServiceDayScope.MONDAY, 18 * 60, 8 * 60),
        ("Domingos e feriados", ServiceDayScope.HOLIDAY, 0, 24 * 60),
    }
    for name, day_scope, start_minute, end_minute in expected:
        assert ServiceClassificationWindow.objects.filter(
            workspace=workspace,
            hour_type__name=name,
            day_scope=day_scope,
            start_minute=start_minute,
            end_minute=end_minute,
        ).exists(), (
            f"the seed no longer has {name!r} on {day_scope} from {start_minute} to "
            f"{end_minute} minutes. Every assertion below about what the engine suggests "
            "rests on that window, so this must fail here rather than silently stop "
            "suggesting anything -- an absent window and a broken engine look the same."
        )

    return workspace


@pytest.fixture
def marubeni(db, classifying_workspace):
    return ServiceClient.objects.create(workspace=classifying_workspace, name="Marubeni")


@pytest.fixture
def project(db, classifying_workspace, marubeni):
    project = Project.objects.create(
        workspace=classifying_workspace,
        name="Marubeni Support",
        identifier="MCLS",
        service_client=marubeni,
        is_time_tracking_enabled=True,
    )
    State.objects.create(
        workspace=classifying_workspace, project=project, name="Todo", group="unstarted", default=True
    )
    return project


@pytest.fixture
def issue(db, project, create_user):
    ProjectMember.objects.create(project=project, member=create_user, role=20, is_active=True)
    return Issue.objects.create(workspace=project.workspace, project=project, name="Printer broken")


@pytest.fixture
def contract_billing(db, classifying_workspace):
    return ServiceBillingType.objects.get(workspace=classifying_workspace, name="Contrato")


@pytest.fixture
def christmas_in_march(db, classifying_workspace):
    """A holiday on a Monday. The name is deliberately not "Natal": nothing about the
    engine's decision depends on which holiday it is, and a test that reads "Natal" on a
    date in March invites the reader to look for a meaning that is not there."""
    return ServiceHoliday.objects.create(
        workspace=classifying_workspace,
        name="Aniversário da cidade",
        date=A_MONDAY_THAT_IS_A_HOLIDAY,
        is_recurring=True,
    )


def _urls(issue):
    keys = {"slug": issue.workspace.slug, "project_id": issue.project_id, "issue_id": issue.id}
    return {"list": LIST_URL.format(**keys), "preview": PREVIEW_URL.format(**keys)}


def _payload(billing_type, **overrides):
    """No `hour_type_id`, which is the point.

    Criterion 8 is about the type arriving *already chosen* by the engine. Sending one would
    make the test assert the technician's own choice coming back unchanged.
    """
    payload = {
        "worked_on": A_MONDAY,
        "description": "Replaced the fuser",
        "entry_mode": "duration",
        "duration": "3h",
        "billing_type_id": str(billing_type.id),
    }
    payload.update(overrides)
    return payload


@pytest.mark.contract
@pytest.mark.django_db
class TestCriterion8HolidayIsPreSelectedWithItsReason:
    """Inherited criterion 8: a holiday pre-selects its hour type, in both entry modes."""

    def test_duration_mode_on_a_holiday_pre_selects_the_holiday_type(
        self, session_client, issue, contract_billing, christmas_in_march
    ):
        response = session_client.post(
            _urls(issue)["preview"],
            _payload(contract_billing, worked_on=A_MONDAY_THAT_IS_A_HOLIDAY.isoformat()),
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        segments = response.data["segments"]
        assert len(segments) == 1, "Duration mode never splits (criterion 8 of the calendar phase)"
        assert segments[0]["hour_type_name"] == "Domingos e feriados"
        assert segments[0]["suggested_hour_type_name"] == "Domingos e feriados"
        assert segments[0]["is_hour_type_overridden"] is False

    def test_interval_mode_on_a_holiday_pre_selects_the_holiday_type(
        self, session_client, issue, contract_billing, christmas_in_march
    ):
        response = session_client.post(
            _urls(issue)["preview"],
            _payload(
                contract_billing,
                worked_on=A_MONDAY_THAT_IS_A_HOLIDAY.isoformat(),
                entry_mode="interval",
                start_time="10:00",
                end_time="12:00",
                duration=None,
            ),
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        segments = response.data["segments"]
        assert len(segments) == 1, "A holiday covers the whole day, so 10:00 to 12:00 is one segment"
        assert segments[0]["hour_type_name"] == "Domingos e feriados"
        assert segments[0]["logged_hours"] == "2.0000"

    def test_the_reason_names_the_holiday_in_both_modes(
        self, session_client, issue, contract_billing, christmas_in_march
    ):
        """Criterion 8 asks for the reason to be *visible*, which means it has to be in the
        payload. Section 7 of the calendar brief is explicit that silent classification in a
        financial calculation produces disputes with the client."""
        for extra in (
            {},
            {"entry_mode": "interval", "start_time": "10:00", "end_time": "12:00", "duration": None},
        ):
            response = session_client.post(
                _urls(issue)["preview"],
                _payload(
                    contract_billing, worked_on=A_MONDAY_THAT_IS_A_HOLIDAY.isoformat(), **extra
                ),
                format="json",
            )

            assert response.status_code == status.HTTP_200_OK, response.data
            reason = response.data["segments"][0]["classification_reason"]
            assert christmas_in_march.name in reason, (
                f"The reason {reason!r} does not name the holiday, so the technician cannot "
                "tell why the rate doubled."
            )

    def test_the_pre_selection_survives_being_saved(
        self, session_client, issue, contract_billing, christmas_in_march
    ):
        """The preview is computed by the same code as the save, and this is what proves it
        for classification: an entry created without an `hour_type_id` persists the engine's
        choice, its reason, and the 2.0 multiplier snapshot that follows from it."""
        response = session_client.post(
            _urls(issue)["list"],
            _payload(contract_billing, worked_on=A_MONDAY_THAT_IS_A_HOLIDAY.isoformat()),
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        log = ServiceLog.objects.get(issue=issue)
        holiday_type = ServiceHourType.objects.get(workspace=issue.workspace, name="Domingos e feriados")
        assert log.hour_type_id == holiday_type.id
        assert log.suggested_hour_type_id == holiday_type.id
        assert log.is_hour_type_overridden is False
        assert christmas_in_march.name in log.classification_reason
        assert log.applied_multiplier == Decimal("2.00")
        assert log.equivalent_hours == Decimal("6.0000")

    def test_the_technician_may_still_override_and_it_is_recorded(
        self, session_client, issue, contract_billing, christmas_in_march
    ):
        """R10: classification is a convenience, never a lock.

        This is the assertion the work log phase could not make. It registered the
        `overridden` activity generator and left it unable to fire, because with no engine
        there was no suggestion for a choice to diverge from.
        """
        commercial = ServiceHourType.objects.get(workspace=issue.workspace, name="Horário comercial")

        response = session_client.post(
            _urls(issue)["list"],
            _payload(
                contract_billing,
                worked_on=A_MONDAY_THAT_IS_A_HOLIDAY.isoformat(),
                hour_type_id=str(commercial.id),
            ),
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        log = ServiceLog.objects.get(issue=issue)
        assert log.hour_type_id == commercial.id, "The technician's choice wins"
        assert log.suggested_hour_type_id != commercial.id, "The suggestion is kept as it was"
        assert log.is_hour_type_overridden is True


@pytest.mark.contract
@pytest.mark.django_db
class TestCriterion9MondayEveningSplitsIntoTwoGroupedLogs:
    """Inherited criterion 9: Monday 17:00 to 20:00, 1h commercial plus 2h after hours."""

    def _interval(self, billing_type, start="17:00", end="20:00"):
        return _payload(
            billing_type,
            entry_mode="interval",
            start_time=start,
            end_time=end,
            duration=None,
        )

    def test_the_preview_shows_both_segments_before_saving(
        self, session_client, issue, contract_billing
    ):
        response = session_client.post(
            _urls(issue)["preview"], self._interval(contract_billing), format="json"
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        segments = response.data["segments"]
        assert len(segments) == 2

        assert segments[0]["hour_type_name"] == "Horário comercial"
        assert segments[0]["logged_hours"] == "1.0000"
        assert segments[0]["start_time"] == "17:00:00"
        assert segments[0]["end_time"] == "18:00:00"

        assert segments[1]["hour_type_name"] == "Fora do expediente"
        assert segments[1]["logged_hours"] == "2.0000"
        assert segments[1]["start_time"] == "18:00:00"
        assert segments[1]["end_time"] == "20:00:00"

        # The total is what the technician is being asked to confirm.
        assert response.data["totals"]["logged_hours"] == "3.0000"
        assert response.data["totals"]["equivalent_hours"] == "4.0000"
        assert response.data["was_rounded"] is False

    def test_the_preview_persists_nothing(self, session_client, issue, contract_billing):
        session_client.post(_urls(issue)["preview"], self._interval(contract_billing), format="json")
        assert ServiceLog.objects.filter(issue=issue).count() == 0

    def test_saving_creates_two_logs_grouped_by_one_batch_id(
        self, session_client, issue, contract_billing
    ):
        response = session_client.post(
            _urls(issue)["list"], self._interval(contract_billing), format="json"
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        logs = list(ServiceLog.objects.filter(issue=issue).order_by("segment_index"))
        assert len(logs) == 2
        assert logs[0].batch_id is not None
        assert logs[0].batch_id == logs[1].batch_id, "Grouped, which is what makes them one entry"
        assert [log.segment_index for log in logs] == [0, 1]

    def test_each_segment_carries_its_own_multiplier_and_reason(
        self, session_client, issue, contract_billing
    ):
        session_client.post(_urls(issue)["list"], self._interval(contract_billing), format="json")

        first, second = ServiceLog.objects.filter(issue=issue).order_by("segment_index")

        assert (first.logged_hours, first.applied_multiplier, first.equivalent_hours) == (
            Decimal("1.0000"),
            Decimal("1.00"),
            Decimal("1.0000"),
        )
        assert (second.logged_hours, second.applied_multiplier, second.equivalent_hours) == (
            Decimal("2.0000"),
            Decimal("1.50"),
            Decimal("3.0000"),
        )
        # Criterion 16 of the calendar phase, per segment rather than per entry.
        assert first.classification_reason and second.classification_reason
        assert first.classification_reason != second.classification_reason

    def test_the_preview_and_the_save_agree_field_by_field(
        self, session_client, issue, contract_billing
    ):
        """A preview computed by different code than the save is a preview that can lie.

        Criterion 9 requires the preview to be shown *before* saving, which is only worth
        anything if it matches. Compared field by field rather than by segment count.
        """
        preview = session_client.post(
            _urls(issue)["preview"], self._interval(contract_billing), format="json"
        )
        created = session_client.post(
            _urls(issue)["list"], self._interval(contract_billing), format="json"
        )

        interesting = ("hour_type_name", "logged_hours", "equivalent_hours", "start_time", "end_time")
        assert [
            {key: segment[key] for key in interesting} for segment in preview.data["segments"]
        ] == [{key: segment[key] for key in interesting} for segment in created.data["service_logs"]]

    def test_deleting_the_batch_removes_both_segments(
        self, session_client, issue, contract_billing
    ):
        """Criterion 11 of the work log phase, which could only ever be tested with a
        hand-made batch until now. This is the first time the engine decides the split."""
        created = session_client.post(
            _urls(issue)["list"], self._interval(contract_billing), format="json"
        )
        batch_id = created.data["service_logs"][0]["batch_id"]

        response = session_client.delete(f"{_urls(issue)['list']}batches/{batch_id}/")

        # 200 rather than 204: the endpoint returns the work item's recomputed totals, which
        # is what the list has to re-render after the deletion.
        assert response.status_code == status.HTTP_200_OK, response.data
        assert ServiceLog.objects.filter(issue=issue).count() == 0

    def test_an_evening_that_does_not_cross_a_border_stays_one_log(
        self, session_client, issue, contract_billing
    ):
        """The positive control for the split.

        Without it, a test suite where every interval produced two segments -- because the
        engine cut on something other than the 18:00 border -- would look identical to this
        one passing.
        """
        response = session_client.post(
            _urls(issue)["list"], self._interval(contract_billing, "19:00", "21:00"), format="json"
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        logs = ServiceLog.objects.filter(issue=issue)
        assert logs.count() == 1
        assert logs.first().hour_type.name == "Fora do expediente"
