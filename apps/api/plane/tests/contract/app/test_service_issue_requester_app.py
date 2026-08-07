# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""A technician opens a ticket on the client's behalf. Section 5, criterion 15. D62.

Criterion 15 has two halves and the second one is the one that would silently not work:
the recorded requester must *see* the work item in their portal. In the recommended portal
configuration they would see it anyway, because the project grants its clients full
visibility -- so every visibility test here turns that flag **off** first. With the flag on,
a broken attribution would pass.

That is the trap this file exists for: an administrator records a requester, the API returns
200, and the requester sees nothing. A feature that reports success and does nothing is
worse than an absent one.
"""

from uuid import uuid4

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from plane.app.views.service_issue_requester.base import NOT_A_CLIENT_USER_OF_THIS_PROJECT
from plane.db.models import (
    Issue,
    IssueSubscriber,
    Project,
    ProjectMember,
    ServiceIssueRequester,
    User,
    WorkspaceMember,
)

REQUESTER_URL = "/api/workspaces/{slug}/projects/{project_id}/issues/{issue_id}/service-requester/"
DETAIL_URL = "/api/workspaces/{slug}/projects/{project_id}/issues/{issue_id}/"
LIST_URL = "/api/workspaces/{slug}/projects/{project_id}/issues/list/?issues={issue_ids}"


def _user(prefix, workspace, role, projects=()):
    unique_id = uuid4().hex[:8]
    user = User.objects.create(
        email=f"{prefix}-{unique_id}@plane.so",
        username=f"{prefix}_{unique_id}",
        first_name=prefix.title(),
    )
    user.set_password("test-password")
    user.save()
    WorkspaceMember.objects.create(workspace=workspace, member=user, role=role)

    for project in projects:
        ProjectMember.objects.create(project=project, member=user, workspace=workspace, role=role)

    return user


@pytest.fixture
def project(db, workspace, create_user):
    """Full client visibility **off**, so the attribution is the only way in."""
    project = Project.objects.create(
        name="Marubeni Support",
        identifier="MARU",
        workspace=workspace,
        created_by=create_user,
        guest_view_all_features=False,
    )
    ProjectMember.objects.create(project=project, member=create_user, workspace=workspace, role=20)
    return project


@pytest.fixture
def other_project(db, workspace, create_user):
    project = Project.objects.create(
        name="Terlogs Support",
        identifier="TERL",
        workspace=workspace,
        created_by=create_user,
        guest_view_all_features=False,
    )
    ProjectMember.objects.create(project=project, member=create_user, workspace=workspace, role=20)
    return project


@pytest.fixture
def technician(db, workspace, project):
    return _user("tech", workspace, 15, [project])


@pytest.fixture
def marcel(db, workspace, project):
    """The client user who asked for the ticket."""
    return _user("marcel", workspace, 5, [project])


@pytest.fixture
def other_client_user(db, workspace, project):
    """A second client user of the same project, who did not ask for anything."""
    return _user("bruno", workspace, 5, [project])


@pytest.fixture
def outsider_client_user(db, workspace, other_project):
    """A client user of a different project entirely."""
    return _user("adriano", workspace, 5, [other_project])


@pytest.fixture
def issue(db, project, workspace, technician):
    """Opened by the technician, so `created_by` gives the client no way in."""
    issue = Issue(name="Server is down", project=project, workspace=workspace)
    issue.save(created_by_id=technician.id)
    return issue


def _api(user):
    api = APIClient()
    api.force_authenticate(user=user)
    return api


def _url(project, issue):
    return REQUESTER_URL.format(slug=project.workspace.slug, project_id=project.id, issue_id=issue.id)


def _record(user, project, issue, requester):
    return _api(user).post(_url(project, issue), {"requester_id": str(requester.id)}, format="json")


@pytest.mark.contract
class TestCriterion15TheRequesterSeesTheWorkItem:
    @pytest.mark.django_db
    def test_a_technician_records_the_requester(self, project, issue, technician, marcel):
        response = _record(technician, project, issue, marcel)

        assert response.status_code == status.HTTP_200_OK, response.data
        assert str(response.data["requester_detail"]["id"]) == str(marcel.id)
        assert ServiceIssueRequester.objects.filter(issue=issue, requester=marcel).exists()

    @pytest.mark.django_db
    def test_the_requester_can_then_retrieve_the_work_item(self, project, issue, technician, marcel):
        """The half that would silently not work. Visibility is off and the client did not
        create the ticket, so the attribution is the only thing that lets them in."""
        before = _api(marcel).get(
            DETAIL_URL.format(slug=project.workspace.slug, project_id=project.id, issue_id=issue.id)
        )
        assert before.status_code == status.HTTP_403_FORBIDDEN, before.data

        _record(technician, project, issue, marcel)

        after = _api(marcel).get(
            DETAIL_URL.format(slug=project.workspace.slug, project_id=project.id, issue_id=issue.id)
        )
        assert after.status_code == status.HTTP_200_OK, after.data

    @pytest.mark.django_db
    def test_the_requester_sees_it_in_the_list_endpoint_too(self, project, issue, technician, marcel):
        """Three guest-scope sites in core, so the attribution has to hold at each. This is
        the one that would be missed by testing only the detail route."""
        url = LIST_URL.format(
            slug=project.workspace.slug, project_id=project.id, issue_ids=str(issue.id)
        )

        before = _api(marcel).get(url)
        assert before.json() == [] or len(before.json()) == 0

        _record(technician, project, issue, marcel)

        after = _api(marcel).get(url)
        assert [row["id"] for row in after.json()] == [str(issue.id)]

    @pytest.mark.django_db
    def test_another_client_user_of_the_same_project_still_cannot_see_it(
        self, project, issue, technician, marcel, other_client_user
    ):
        """The attribution grants visibility to one person, not to the project's clients at
        large -- otherwise it would be `guest_view_all_features` by another name."""
        _record(technician, project, issue, marcel)

        response = _api(other_client_user).get(
            DETAIL_URL.format(slug=project.workspace.slug, project_id=project.id, issue_id=issue.id)
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN, response.data

    @pytest.mark.django_db
    def test_removing_the_attribution_removes_the_visibility(self, project, issue, technician, marcel):
        _record(technician, project, issue, marcel)
        _api(technician).delete(_url(project, issue))

        response = _api(marcel).get(
            DETAIL_URL.format(slug=project.workspace.slug, project_id=project.id, issue_id=issue.id)
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN, response.data

    @pytest.mark.django_db
    def test_the_requester_gains_no_extra_write_power(self, project, issue, technician, marcel):
        """Visibility is not authority. The allowlist still applies: the requester may set
        the priority and nothing else."""
        _record(technician, project, issue, marcel)

        response = _api(marcel).patch(
            DETAIL_URL.format(slug=project.workspace.slug, project_id=project.id, issue_id=issue.id),
            {"priority": "urgent", "name": "hijacked"},
            format="json",
        )

        issue.refresh_from_db()
        assert response.status_code == status.HTTP_204_NO_CONTENT, response.data
        assert issue.priority == "urgent"
        assert issue.name == "Server is down"


@pytest.mark.contract
class TestTheRequesterIsNotified:
    @pytest.mark.django_db
    def test_recording_the_requester_subscribes_them(self, project, issue, technician, marcel):
        """Through the native IssueSubscriber, so notification, digest and unsubscribe are
        Plane's and not a second mechanism of ours."""
        _record(technician, project, issue, marcel)

        assert IssueSubscriber.objects.filter(issue=issue, subscriber=marcel).exists()

    @pytest.mark.django_db
    def test_recording_twice_does_not_duplicate_the_subscription(
        self, project, issue, technician, marcel
    ):
        _record(technician, project, issue, marcel)
        _record(technician, project, issue, marcel)

        assert IssueSubscriber.objects.filter(issue=issue, subscriber=marcel).count() == 1

    @pytest.mark.django_db
    def test_removing_the_attribution_leaves_the_subscription(self, project, issue, technician, marcel):
        """Deliberate. Unsubscribing is the requester's own decision and Plane already gives
        them that control; a correction to the attribution should not silently stop
        somebody's notifications."""
        _record(technician, project, issue, marcel)
        _api(technician).delete(_url(project, issue))

        assert IssueSubscriber.objects.filter(issue=issue, subscriber=marcel).exists()


@pytest.mark.contract
class TestTheAttributionCannotBeUsedToGrantArbitraryVisibility:
    @pytest.mark.django_db
    def test_a_client_user_of_another_project_is_refused(
        self, project, issue, technician, outsider_client_user
    ):
        """Otherwise this route -- whose entire purpose is granting visibility -- would hand
        any workspace member sight of a work item in a project they are not in."""
        response = _record(technician, project, issue, outsider_client_user)

        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.data
        assert response.data["error"] == NOT_A_CLIENT_USER_OF_THIS_PROJECT
        assert not ServiceIssueRequester.objects.filter(issue=issue).exists()

    @pytest.mark.django_db
    def test_a_technician_cannot_be_recorded_as_the_requester(
        self, project, issue, technician, marcel
    ):
        """The requester is the client's user. A technician named here would be a member
        gaining nothing and an attribution that lies about who asked."""
        response = _record(technician, project, issue, technician)

        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.data
        assert response.data["error"] == NOT_A_CLIENT_USER_OF_THIS_PROJECT

    @pytest.mark.django_db
    def test_a_client_cannot_record_a_requester(self, project, issue, marcel, other_client_user):
        """Recording who asked is a technician's action. A client naming another client user
        would be one client granting another visibility."""
        response = _record(marcel, project, issue, other_client_user)

        assert response.status_code == status.HTTP_403_FORBIDDEN, response.data

    @pytest.mark.django_db
    def test_an_unknown_issue_is_a_404(self, project, technician, marcel):
        response = _api(technician).post(
            REQUESTER_URL.format(
                slug=project.workspace.slug, project_id=project.id, issue_id=uuid4()
            ),
            {"requester_id": str(marcel.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND, response.data


@pytest.mark.contract
class TestReadingAndReplacingTheAttribution:
    @pytest.mark.django_db
    def test_an_unrecorded_requester_reads_as_an_explicit_null(self, project, issue, technician):
        """200 and not 404: "this ticket has no recorded requester" is an answer, and the
        common one."""
        response = _api(technician).get(_url(project, issue))

        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["requester"] is None

    @pytest.mark.django_db
    def test_the_requester_can_be_corrected(
        self, project, issue, technician, marcel, other_client_user
    ):
        """Idempotent rather than refusing a second call: correcting a misrecorded requester
        is ordinary, and forcing a DELETE first would leave a window with no requester."""
        _record(technician, project, issue, marcel)
        response = _record(technician, project, issue, other_client_user)

        assert response.status_code == status.HTTP_200_OK, response.data
        assert ServiceIssueRequester.objects.filter(issue=issue).count() == 1
        assert ServiceIssueRequester.objects.get(issue=issue).requester_id == other_client_user.id

    @pytest.mark.django_db
    def test_correcting_it_moves_the_visibility(
        self, project, issue, technician, marcel, other_client_user
    ):
        _record(technician, project, issue, marcel)
        _record(technician, project, issue, other_client_user)

        url = DETAIL_URL.format(slug=project.workspace.slug, project_id=project.id, issue_id=issue.id)

        assert _api(marcel).get(url).status_code == status.HTTP_403_FORBIDDEN
        assert _api(other_client_user).get(url).status_code == status.HTTP_200_OK

    @pytest.mark.django_db
    def test_deleting_an_absent_attribution_is_not_an_error(self, project, issue, technician):
        response = _api(technician).delete(_url(project, issue))

        assert response.status_code == status.HTTP_204_NO_CONTENT

    @pytest.mark.django_db
    def test_the_attribution_does_not_change_who_opened_the_ticket(
        self, project, issue, technician, marcel
    ):
        """R8's separation one level down: `created_by` on the work item stays the technician
        who opened it, and `created_by` on the attribution is who recorded it."""
        _record(technician, project, issue, marcel)

        issue.refresh_from_db()
        row = ServiceIssueRequester.objects.get(issue=issue)

        assert issue.created_by_id == technician.id
        assert row.requester_id == marcel.id
        assert row.created_by_id == technician.id


@pytest.mark.contract
class TestFullVisibilityStillWorksWithoutAnAttribution:
    @pytest.mark.django_db
    def test_the_recommended_configuration_needs_no_requester(
        self, project, issue, marcel, technician
    ):
        """The positive control for the fixture choice: with the flag on, a client sees the
        ticket with no attribution at all -- which is why every test above turns it off."""
        project.guest_view_all_features = True
        project.save(update_fields=["guest_view_all_features"])

        response = _api(marcel).get(
            DETAIL_URL.format(slug=project.workspace.slug, project_id=project.id, issue_id=issue.id)
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert not ServiceIssueRequester.objects.filter(issue=issue).exists()
