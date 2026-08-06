# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests for the work log endpoints.

Walks the acceptance criteria of this phase that are reachable over HTTP:

* 1 to 5 -- the parser and rounding through the API
* 6 -- the two entry modes agreeing
* 7 -- the multiplier
* 11 -- deleting a split entry removes every segment
* 12 -- a non-billable entry sums into logged but not into debited
* 13 -- a 30h entry saves, with a warning
* 14 -- a future date is rejected
* 15 -- a later multiplier change does not move an existing log
* 16 -- an edit recalculates the totals and records the change

Criteria 8 and 9 depend on the classification engine of the calendar and windows phase.
They are closed in `test_service_log_classification_app.py`, which is a separate file
because it needs the opposite fixture: the hour types below are built by hand, with no
windows and no priority, and they stay that way deliberately. The parser, the rounding
and the totals must not depend on a calendar, and these tests are what says so.

Criterion 10 -- duration mode on a weekday leaves the choice to the technician -- passes
here and passes there. It used to pass as a side effect of there being no engine at all;
it now passes because R10 requires a weekday in duration mode to carry no suggestion,
which is a rule rather than an accident.
"""

from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from plane.db.models import (
    Issue,
    IssueActivity,
    Project,
    ProjectMember,
    ServiceBillingType,
    ServiceClient,
    ServiceHourType,
    ServiceLog,
    State,
    User,
    WorkspaceMember,
)

LIST_URL = "/api/workspaces/{slug}/projects/{project_id}/issues/{issue_id}/service-logs/"
TOTALS_URL = "/api/workspaces/{slug}/projects/{project_id}/issues/{issue_id}/service-logs/totals/"
PREVIEW_URL = "/api/workspaces/{slug}/projects/{project_id}/issues/{issue_id}/service-logs/preview/"
BATCH_URL = (
    "/api/workspaces/{slug}/projects/{project_id}/issues/{issue_id}/service-logs/batches/{batch_id}/"
)


@pytest.fixture(autouse=True)
def captured_activity():
    """Run the activity task inline so the audit trail can be asserted on.

    ``apply_async`` is patched to call the task body synchronously instead of enqueuing
    it. The task is written to be fired and forgotten -- it swallows every exception --
    so without this an audit trail that silently recorded nothing would look identical
    to one that worked.
    """

    def run_inline(args=None, kwargs=None, **_ignored):
        from plane.bgtasks.issue_activities_task import issue_activity

        if kwargs is not None and "type" in kwargs:
            issue_activity(**kwargs)
        return None

    with patch("celery.app.task.Task.apply_async", side_effect=run_inline):
        yield


@pytest.fixture
def marubeni(db, workspace):
    return ServiceClient.objects.create(workspace=workspace, name="Marubeni")


@pytest.fixture
def project(db, workspace, marubeni):
    project = Project.objects.create(
        workspace=workspace,
        name="Marubeni Support",
        identifier="MSUP",
        service_client=marubeni,
        is_time_tracking_enabled=True,
    )
    State.objects.create(workspace=workspace, project=project, name="Todo", group="unstarted", default=True)
    return project


@pytest.fixture
def project_member(db, project, create_user):
    return ProjectMember.objects.create(project=project, member=create_user, role=20, is_active=True)


@pytest.fixture
def issue(db, project, project_member):
    return Issue.objects.create(workspace=project.workspace, project=project, name="Printer broken")


@pytest.fixture
def commercial_hours(db, workspace):
    """Created first, so the model makes it the catalogue default."""
    return ServiceHourType.objects.create(
        workspace=workspace, name="Horário comercial", multiplier=Decimal("1.00")
    )


@pytest.fixture
def after_hours(db, workspace, commercial_hours):
    return ServiceHourType.objects.create(
        workspace=workspace, name="Fora do expediente", multiplier=Decimal("1.50")
    )


@pytest.fixture
def contract_billing(db, workspace):
    return ServiceBillingType.objects.create(
        workspace=workspace, name="Contrato", billing_route=ServiceBillingType.BillingRoute.DEBIT_POOL
    )


@pytest.fixture
def warranty_billing(db, workspace, contract_billing):
    return ServiceBillingType.objects.create(
        workspace=workspace, name="Garantia", billing_route=ServiceBillingType.BillingRoute.NON_BILLABLE
    )


def _urls(issue):
    return {
        "list": LIST_URL.format(
            slug=issue.workspace.slug, project_id=issue.project_id, issue_id=issue.id
        ),
        "totals": TOTALS_URL.format(
            slug=issue.workspace.slug, project_id=issue.project_id, issue_id=issue.id
        ),
        "preview": PREVIEW_URL.format(
            slug=issue.workspace.slug, project_id=issue.project_id, issue_id=issue.id
        ),
    }


def _batch_url(issue, batch_id):
    return BATCH_URL.format(
        slug=issue.workspace.slug, project_id=issue.project_id, issue_id=issue.id, batch_id=batch_id
    )


def _payload(hour_type, billing_type, **overrides):
    payload = {
        "worked_on": "2026-01-05",
        "description": "Replaced the fuser",
        "entry_mode": "duration",
        "duration": "1h",
        "hour_type_id": str(hour_type.id),
        "billing_type_id": str(billing_type.id),
    }
    payload.update(overrides)
    return payload


@pytest.mark.contract
@pytest.mark.django_db
class TestParserAndRoundingThroughTheApi:
    """Acceptance criteria 1 to 5."""

    @pytest.mark.parametrize(
        ("duration", "logged_hours"),
        [
            ("1h 15min", "1.2500"),  # criterion 1
            ("1h 08min", "1.2500"),  # criterion 2
            ("1h 07min", "1.0000"),  # criterion 3
            ("3min", "0.2500"),  # criterion 4, the 15 minute floor
            ("1,5h", "1.5000"),
            ("90min", "1.5000"),
            ("2 horas", "2.0000"),
        ],
    )
    def test_duration_text_persists_the_documented_decimal(
        self, session_client, issue, commercial_hours, contract_billing, duration, logged_hours
    ):
        response = session_client.post(
            _urls(issue)["list"],
            _payload(commercial_hours, contract_billing, duration=duration),
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert response.data["service_logs"][0]["logged_hours"] == logged_hours

    def test_the_preview_reports_that_rounding_happened(
        self, session_client, issue, commercial_hours, contract_billing
    ):
        """Criterion 2's other half: the UI has to be told."""
        response = session_client.post(
            _urls(issue)["preview"],
            _payload(commercial_hours, contract_billing, duration="1h 08min"),
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["was_rounded"] is True
        assert response.data["raw_duration_minutes"] == 68
        assert response.data["totals"]["logged_hours"] == "1.2500"

    def test_an_exact_duration_is_not_reported_as_rounded(
        self, session_client, issue, commercial_hours, contract_billing
    ):
        response = session_client.post(
            _urls(issue)["preview"],
            _payload(commercial_hours, contract_billing, duration="1h 15min"),
            format="json",
        )
        assert response.data["was_rounded"] is False

    @pytest.mark.parametrize("duration", ["banana", "", "90", "1:30", "0min"])
    def test_invalid_duration_is_refused(
        self, session_client, issue, commercial_hours, contract_billing, duration
    ):
        """Criterion 5. "90" is refused because it is ambiguous, per R1."""
        response = session_client.post(
            _urls(issue)["list"],
            _payload(commercial_hours, contract_billing, duration=duration),
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert ServiceLog.objects.filter(issue=issue).count() == 0

    def test_the_preview_never_persists_anything(
        self, session_client, issue, commercial_hours, contract_billing
    ):
        session_client.post(
            _urls(issue)["preview"], _payload(commercial_hours, contract_billing), format="json"
        )
        assert ServiceLog.all_objects.filter(issue=issue).count() == 0


@pytest.mark.contract
@pytest.mark.django_db
class TestEntryModes:
    """Rule R9 and acceptance criterion 6."""

    def test_an_interval_matches_the_equivalent_duration_text(
        self, session_client, issue, commercial_hours, contract_billing
    ):
        """Criterion 6: 14:00 to 15:30 equals "1h 30min"."""
        interval = session_client.post(
            _urls(issue)["list"],
            _payload(
                commercial_hours,
                contract_billing,
                entry_mode="interval",
                start_time="14:00",
                end_time="15:30",
                duration=None,
            ),
            format="json",
        )
        assert interval.status_code == status.HTTP_201_CREATED, interval.data

        duration = session_client.post(
            _urls(issue)["list"],
            _payload(commercial_hours, contract_billing, duration="1h 30min"),
            format="json",
        )
        assert duration.status_code == status.HTTP_201_CREATED, duration.data

        assert (
            interval.data["service_logs"][0]["logged_hours"]
            == duration.data["service_logs"][0]["logged_hours"]
            == "1.5000"
        )

    def test_an_interval_across_midnight_is_accepted(
        self, session_client, issue, commercial_hours, contract_billing
    ):
        response = session_client.post(
            _urls(issue)["list"],
            _payload(
                commercial_hours,
                contract_billing,
                entry_mode="interval",
                start_time="22:00",
                end_time="01:00",
            ),
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert response.data["service_logs"][0]["logged_hours"] == "3.0000"

    def test_an_interval_missing_its_end_is_refused(
        self, session_client, issue, commercial_hours, contract_billing
    ):
        response = session_client.post(
            _urls(issue)["list"],
            _payload(commercial_hours, contract_billing, entry_mode="interval", start_time="14:00"),
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "INTERVAL_REQUIRES_START_AND_END_TIME" in str(response.data)

    def test_identical_endpoints_are_refused(
        self, session_client, issue, commercial_hours, contract_billing
    ):
        """Ambiguous between zero and 24 hours, so it fails rather than guessing."""
        response = session_client.post(
            _urls(issue)["list"],
            _payload(
                commercial_hours,
                contract_billing,
                entry_mode="interval",
                start_time="14:00",
                end_time="14:00",
            ),
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "INTERVAL_ENDPOINTS_MUST_DIFFER" in str(response.data)

    def test_duration_mode_stores_no_times(
        self, session_client, issue, commercial_hours, contract_billing
    ):
        response = session_client.post(
            _urls(issue)["list"],
            _payload(commercial_hours, contract_billing, start_time="14:00", end_time="15:00"),
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert response.data["service_logs"][0]["start_time"] is None
        assert response.data["service_logs"][0]["end_time"] is None


@pytest.mark.contract
@pytest.mark.django_db
class TestMultiplierAndBillingRoute:
    """Acceptance criteria 7, 12 and 15."""

    def test_criterion_7_multiplier_applies_to_equivalent_only(
        self, session_client, issue, after_hours, contract_billing
    ):
        response = session_client.post(
            _urls(issue)["list"], _payload(after_hours, contract_billing, duration="1h"), format="json"
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        row = response.data["service_logs"][0]
        assert row["logged_hours"] == "1.0000"
        assert row["equivalent_hours"] == "1.5000"
        assert row["applied_multiplier"] == "1.50"

    def test_criterion_12_non_billable_debits_nothing(
        self, session_client, issue, after_hours, warranty_billing
    ):
        response = session_client.post(
            _urls(issue)["list"],
            _payload(after_hours, warranty_billing, duration="1h 15min"),
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        row = response.data["service_logs"][0]
        assert row["logged_hours"] == "1.2500"
        assert row["equivalent_hours"] == "1.8750"
        assert row["debited_hours"] == "0.0000"
        assert row["is_billable"] is False
        # The badge the list shows, from the snapshot rather than the catalogue.
        assert row["billing_type_name"] == "Garantia"

    def test_criterion_12_totals_separate_logged_from_debited(
        self, session_client, issue, commercial_hours, contract_billing, warranty_billing
    ):
        session_client.post(
            _urls(issue)["list"], _payload(commercial_hours, contract_billing, duration="1h"), format="json"
        )
        response = session_client.post(
            _urls(issue)["list"], _payload(commercial_hours, warranty_billing, duration="2h"), format="json"
        )

        totals = response.data["totals"]
        assert totals["logged_hours"] == "3.0000"
        assert totals["equivalent_hours"] == "3.0000"
        assert totals["debited_hours"] == "1.0000"

    def test_criterion_15_a_later_multiplier_change_does_not_move_the_log(
        self, session_client, issue, after_hours, contract_billing
    ):
        response = session_client.post(
            _urls(issue)["list"], _payload(after_hours, contract_billing, duration="1h"), format="json"
        )
        log_id = response.data["service_logs"][0]["id"]

        after_hours.multiplier = Decimal("1.80")
        after_hours.save()

        listed = session_client.get(_urls(issue)["list"])
        row = next(entry for entry in listed.data["service_logs"] if entry["id"] == log_id)

        assert row["applied_multiplier"] == "1.50"
        assert row["equivalent_hours"] == "1.5000"

    def test_an_inactive_hour_type_cannot_be_chosen_for_a_new_log(
        self, session_client, issue, after_hours, contract_billing
    ):
        """Phase 2, criterion 3: retired options leave the form but stay readable."""
        after_hours.is_active = False
        after_hours.save()

        response = session_client.post(
            _urls(issue)["list"], _payload(after_hours, contract_billing), format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] == "HOUR_TYPE_NOT_FOUND"

    def test_an_hour_type_from_another_workspace_is_refused(
        self, session_client, issue, contract_billing, create_user
    ):
        """Same isolation concern as the client link: the catalogue is per workspace."""
        from plane.db.models import Workspace

        other = Workspace.objects.create(name="Other", owner=create_user, slug="other-ws")
        foreign = ServiceHourType.objects.create(
            workspace=other, name="Foreign", multiplier=Decimal("9.00")
        )

        response = session_client.post(
            _urls(issue)["list"], _payload(foreign, contract_billing), format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] == "HOUR_TYPE_NOT_FOUND"


@pytest.mark.contract
@pytest.mark.django_db
class TestValidationRules:
    """Acceptance criteria 13 and 14, plus the feature toggle."""

    def test_criterion_14_a_future_date_is_rejected(
        self, session_client, issue, commercial_hours, contract_billing
    ):
        from django.utils import timezone as django_timezone

        tomorrow = (django_timezone.now() + django_timezone.timedelta(days=2)).date()

        response = session_client.post(
            _urls(issue)["list"],
            _payload(commercial_hours, contract_billing, worked_on=str(tomorrow)),
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "WORKED_ON_CANNOT_BE_IN_THE_FUTURE" in str(response.data)

    def test_a_backdated_entry_is_accepted(
        self, session_client, issue, commercial_hours, contract_billing
    ):
        """R7 exists so backdating works -- it debits the retroactive month."""
        response = session_client.post(
            _urls(issue)["list"],
            _payload(commercial_hours, contract_billing, worked_on="2024-03-11"),
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED, response.data

    def test_criterion_13_a_thirty_hour_entry_saves_with_a_warning(
        self, session_client, issue, commercial_hours, contract_billing
    ):
        """Section 5: never block. The warning exists to catch a typo."""
        response = session_client.post(
            _urls(issue)["list"], _payload(commercial_hours, contract_billing, duration="30h"), format="json"
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert response.data["service_logs"][0]["logged_hours"] == "30.0000"
        assert response.data["warning"]["code"] == "SERVICE_LOG_EXCEEDS_24_HOURS"

    def test_a_normal_entry_carries_no_warning(
        self, session_client, issue, commercial_hours, contract_billing
    ):
        response = session_client.post(
            _urls(issue)["list"], _payload(commercial_hours, contract_billing, duration="8h"), format="json"
        )
        assert response.data["warning"] is None

    def test_writes_are_refused_when_the_project_toggle_is_off(
        self, session_client, issue, project, commercial_hours, contract_billing
    ):
        project.is_time_tracking_enabled = False
        project.save()

        response = session_client.post(
            _urls(issue)["list"], _payload(commercial_hours, contract_billing), format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] == "TIME_TRACKING_DISABLED_FOR_PROJECT"

    def test_reads_still_work_when_the_toggle_is_turned_off(
        self, session_client, issue, project, commercial_hours, contract_billing
    ):
        """Switching the flag off must not hide work that may already be invoiced."""
        session_client.post(
            _urls(issue)["list"], _payload(commercial_hours, contract_billing), format="json"
        )

        project.is_time_tracking_enabled = False
        project.save()

        response = session_client.get(_urls(issue)["list"])
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["service_logs"]) == 1

    def test_an_empty_description_is_refused(
        self, session_client, issue, commercial_hours, contract_billing
    ):
        """Section 1 marks the description required -- it is what the client reads."""
        response = session_client.post(
            _urls(issue)["list"],
            _payload(commercial_hours, contract_billing, description="   "),
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_delegating_to_another_author_is_refused_until_phase_seven(
        self, session_client, issue, commercial_hours, contract_billing
    ):
        other = User.objects.create(email="other@plane.so", username="other")

        response = session_client.post(
            _urls(issue)["list"],
            _payload(commercial_hours, contract_billing, author_id=str(other.id)),
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] == "SERVICE_LOG_DELEGATION_NOT_AVAILABLE"


@pytest.mark.contract
@pytest.mark.django_db
class TestBatchEditingAndDeletion:
    """Acceptance criteria 11 and 16."""

    def _create(self, session_client, issue, hour_type, billing_type, **overrides):
        response = session_client.post(
            _urls(issue)["list"], _payload(hour_type, billing_type, **overrides), format="json"
        )
        assert response.status_code == status.HTTP_201_CREATED, response.data
        return response.data["batch_id"]

    def test_criterion_11_deleting_an_entry_removes_every_segment(
        self, session_client, issue, commercial_hours, contract_billing
    ):
        batch_id = self._create(session_client, issue, commercial_hours, contract_billing)

        response = session_client.delete(_batch_url(issue, batch_id))

        assert response.status_code == status.HTTP_200_OK, response.data
        assert ServiceLog.objects.filter(batch_id=batch_id).count() == 0
        assert response.data["totals"]["logged_hours"] == "0.0000"

    def test_delete_is_soft(self, session_client, issue, commercial_hours, contract_billing):
        batch_id = self._create(session_client, issue, commercial_hours, contract_billing)
        session_client.delete(_batch_url(issue, batch_id))
        assert ServiceLog.all_objects.filter(batch_id=batch_id).count() == 1

    def test_criterion_16_an_edit_recalculates_the_totals(
        self, session_client, issue, commercial_hours, after_hours, contract_billing
    ):
        batch_id = self._create(
            session_client, issue, commercial_hours, contract_billing, duration="1h"
        )

        response = session_client.patch(
            _batch_url(issue, batch_id),
            _payload(after_hours, contract_billing, duration="2h", description="Took longer"),
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["totals"]["logged_hours"] == "2.0000"
        assert response.data["totals"]["equivalent_hours"] == "3.0000"
        assert response.data["service_logs"][0]["description"] == "Took longer"

    def test_an_edit_does_not_duplicate_the_entry(
        self, session_client, issue, commercial_hours, contract_billing
    ):
        batch_id = self._create(session_client, issue, commercial_hours, contract_billing)

        session_client.patch(
            _batch_url(issue, batch_id),
            _payload(commercial_hours, contract_billing, duration="2h"),
            format="json",
        )

        assert ServiceLog.objects.filter(issue=issue).count() == 1

    def test_only_the_author_may_edit(
        self, session_client, issue, project, workspace, commercial_hours, contract_billing
    ):
        """Section 6: "por ora, apenas o autor". Real permissions arrive in Phase 7."""
        batch_id = self._create(session_client, issue, commercial_hours, contract_billing)

        intruder = User.objects.create(email="intruder@plane.so", username="intruder")
        WorkspaceMember.objects.create(workspace=workspace, member=intruder, role=20)
        ProjectMember.objects.create(project=project, member=intruder, role=20, is_active=True)
        intruder_client = APIClient()
        intruder_client.force_authenticate(user=intruder)

        response = intruder_client.patch(
            _batch_url(issue, batch_id),
            _payload(commercial_hours, contract_billing, duration="9h"),
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.data["error"] == "ONLY_THE_AUTHOR_CAN_CHANGE_A_SERVICE_LOG"

    def test_only_the_author_may_delete(
        self, session_client, issue, project, workspace, commercial_hours, contract_billing
    ):
        batch_id = self._create(session_client, issue, commercial_hours, contract_billing)

        intruder = User.objects.create(email="intruder2@plane.so", username="intruder2")
        WorkspaceMember.objects.create(workspace=workspace, member=intruder, role=20)
        ProjectMember.objects.create(project=project, member=intruder, role=20, is_active=True)
        intruder_client = APIClient()
        intruder_client.force_authenticate(user=intruder)

        response = intruder_client.delete(_batch_url(issue, batch_id))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert ServiceLog.objects.filter(batch_id=batch_id).count() == 1

    def test_an_unknown_batch_is_a_404(self, session_client, issue):
        response = session_client.delete(_batch_url(issue, uuid4()))
        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.contract
@pytest.mark.django_db
class TestListingAndTotals:
    """Section 6 and section 7."""

    def test_ordering_is_newest_service_date_first(
        self, session_client, issue, commercial_hours, contract_billing
    ):
        for worked_on in ("2026-01-05", "2026-01-20", "2026-01-12"):
            session_client.post(
                _urls(issue)["list"],
                _payload(commercial_hours, contract_billing, worked_on=worked_on),
                format="json",
            )

        response = session_client.get(_urls(issue)["list"])
        dates = [row["worked_on"] for row in response.data["service_logs"]]

        assert dates == ["2026-01-20", "2026-01-12", "2026-01-05"]

    def test_the_three_totals_are_reported_separately(
        self, session_client, issue, after_hours, contract_billing, warranty_billing
    ):
        """Confusing these three is the error section 6 warns about."""
        session_client.post(
            _urls(issue)["list"], _payload(after_hours, contract_billing, duration="1h"), format="json"
        )
        session_client.post(
            _urls(issue)["list"], _payload(after_hours, warranty_billing, duration="1h"), format="json"
        )

        response = session_client.get(_urls(issue)["totals"])

        assert response.data["logged_hours"] == "2.0000"
        assert response.data["equivalent_hours"] == "3.0000"
        assert response.data["debited_hours"] == "1.5000"

    def test_totals_carry_a_pt_br_rendering(
        self, session_client, issue, after_hours, contract_billing
    ):
        session_client.post(
            _urls(issue)["list"],
            _payload(after_hours, contract_billing, duration="1h 15min"),
            format="json",
        )

        response = session_client.get(_urls(issue)["totals"])

        # R11's dual reading: 1.25 logged becomes 1,875h for the client.
        assert response.data["logged_hours_display"] == "1,25h"
        assert response.data["equivalent_hours_display"] == "1,875h"

    def test_the_row_carries_both_readings_of_r11(
        self, session_client, issue, after_hours, contract_billing
    ):
        """Section 6: the technician must see what they logged and what the client sees.

        Without this the technician cannot notice they picked the wrong hour type, and
        the error only shows up on the invoice.
        """
        response = session_client.post(
            _urls(issue)["list"],
            _payload(after_hours, contract_billing, duration="1h 15min"),
            format="json",
        )

        row = response.data["service_logs"][0]
        assert row["logged_hours_display"] == "1h 15min"
        assert row["equivalent_hours_display"] == "1,875h"
        assert row["hour_type_name"] == "Fora do expediente"

    def test_logs_of_another_work_item_do_not_leak_in(
        self, session_client, issue, project, commercial_hours, contract_billing
    ):
        other = Issue.objects.create(workspace=project.workspace, project=project, name="Other")
        session_client.post(
            _urls(issue)["list"], _payload(commercial_hours, contract_billing), format="json"
        )

        response = session_client.get(_urls(other)["list"])
        assert response.data["service_logs"] == []
        assert response.data["totals"]["logged_hours"] == "0.0000"


@pytest.mark.contract
@pytest.mark.django_db
class TestGuestIsExcluded:
    """Section 6: "não expor apontamento a papel GUEST em nenhum endpoint"."""

    @pytest.fixture
    def guest_client(self, db, workspace, project):
        guest = User.objects.create(email="guest@plane.so", username="guest")
        WorkspaceMember.objects.create(workspace=workspace, member=guest, role=5)
        ProjectMember.objects.create(project=project, member=guest, role=5, is_active=True)
        client = APIClient()
        client.force_authenticate(user=guest)
        return client

    def test_a_guest_cannot_list(self, guest_client, issue):
        """Even reading leaks: the payload carries the raw duration and the multiplier."""
        assert guest_client.get(_urls(issue)["list"]).status_code == status.HTTP_403_FORBIDDEN

    def test_a_guest_cannot_read_totals(self, guest_client, issue):
        assert guest_client.get(_urls(issue)["totals"]).status_code == status.HTTP_403_FORBIDDEN

    def test_a_guest_cannot_create(self, guest_client, issue, commercial_hours, contract_billing):
        response = guest_client.post(
            _urls(issue)["list"], _payload(commercial_hours, contract_billing), format="json"
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_a_guest_cannot_preview(self, guest_client, issue, commercial_hours, contract_billing):
        response = guest_client.post(
            _urls(issue)["preview"], _payload(commercial_hours, contract_billing), format="json"
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.contract
@pytest.mark.django_db
class TestAuditTrail:
    """Rule R8, through IssueActivity rather than ServiceConfigActivity."""

    def _activities(self, issue, field="service_log"):
        return IssueActivity.objects.filter(issue=issue, field=field).order_by("created_at")

    def test_creating_a_log_records_an_activity_on_the_work_item(
        self, session_client, issue, after_hours, contract_billing
    ):
        session_client.post(
            _urls(issue)["list"], _payload(after_hours, contract_billing, duration="1h"), format="json"
        )

        activities = self._activities(issue)
        assert activities.count() == 1
        activity = activities.first()
        assert activity.verb == "created"
        assert activity.comment == "created a work log"
        assert "Fora do expediente" in activity.new_value

    def test_criterion_16_an_edit_is_recorded_with_both_sides(
        self, session_client, issue, commercial_hours, contract_billing
    ):
        created = session_client.post(
            _urls(issue)["list"], _payload(commercial_hours, contract_billing, duration="1h"), format="json"
        )
        session_client.patch(
            _batch_url(issue, created.data["batch_id"]),
            _payload(commercial_hours, contract_billing, duration="2h"),
            format="json",
        )

        updated = self._activities(issue).filter(verb="updated").first()
        assert updated is not None
        assert "1.0000" in updated.old_value
        assert "2.0000" in updated.new_value

    def test_an_edit_that_changes_nothing_records_nothing(
        self, session_client, issue, commercial_hours, contract_billing
    ):
        """A no-op PATCH must not pad the feed."""
        created = session_client.post(
            _urls(issue)["list"], _payload(commercial_hours, contract_billing, duration="1h"), format="json"
        )
        session_client.patch(
            _batch_url(issue, created.data["batch_id"]),
            _payload(commercial_hours, contract_billing, duration="1h"),
            format="json",
        )

        assert self._activities(issue).filter(verb="updated").count() == 0

    def test_a_deletion_is_recorded_with_the_hours_that_were_removed(
        self, session_client, issue, commercial_hours, contract_billing
    ):
        created = session_client.post(
            _urls(issue)["list"], _payload(commercial_hours, contract_billing, duration="2h"), format="json"
        )
        session_client.delete(_batch_url(issue, created.data["batch_id"]))

        deleted = self._activities(issue).filter(verb="deleted").first()
        assert deleted is not None
        assert "2.0000" in deleted.old_value

    def test_no_override_activity_is_recorded_when_the_choice_matches_the_suggestion(
        self, session_client, issue, commercial_hours, contract_billing
    ):
        """R10 records an override only when it *diverges* from the suggestion.

        This project has no classification windows, so nothing is suggested and there is
        nothing to diverge from. The override trail with the engine configured is tested
        in `test_service_calendar_app.py`.
        """
        session_client.post(
            _urls(issue)["list"], _payload(commercial_hours, contract_billing), format="json"
        )

        assert self._activities(issue, field="service_log_hour_type_override").count() == 0
        assert ServiceLog.objects.get(issue=issue).is_hour_type_overridden is False

    def test_the_config_audit_trail_is_not_used_for_work_logs(
        self, session_client, issue, commercial_hours, contract_billing
    ):
        """Section 6 of the master context keeps the two trails separate."""
        from plane.db.models import ServiceConfigActivity

        before = ServiceConfigActivity.objects.count()
        session_client.post(
            _urls(issue)["list"], _payload(commercial_hours, contract_billing), format="json"
        )
        assert ServiceConfigActivity.objects.count() == before
