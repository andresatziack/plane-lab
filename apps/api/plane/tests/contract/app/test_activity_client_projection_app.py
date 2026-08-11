# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""The activity feed does not hand the client what R11 hides. D61.

This phase creates the exposure, so this phase owns the fix: before the portal, no GUEST
was a member of a Cliente's project, so the activity readers -- which have admitted GUEST
all along -- had no client reading them.

The leak is arithmetic, not just a stray column. ``_service_log_batch_summary`` persists
the summed logged hours into ``IssueActivity.new_value`` -- since D68 as a clock duration
(``"3h 30min (...)"``) rather than as ``f"{total} h"``, which changes nothing here: an hour
a person can read is still the hour R11(c) hides. A client legitimately
sees ``equivalent_hours`` in the portal; given ``logged_hours`` from the feed, one division
returns the multiplier, which is what R11(c) exists to prevent.

**Three doors, one rule.** Every reader is tested, because two of them are admitted by
permission classes that apply no role filter at all on a safe method:

* ``IssueActivityEndpoint`` -- the work item feed
* ``WorkspaceUserActivityEndpoint`` -- another user's activity, actor id from the URL
* ``IssueActivityListAPIEndpoint`` -- the external token API

Every absence assertion has a positive control in the same fixture: a Member sees the row,
and the client still sees the non-work-log activity, so a feed that broke entirely would
fail here rather than pass.
"""

from uuid import uuid4

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from plane.db.models import Issue, IssueActivity, Project, ProjectMember, User, WorkspaceMember
from plane.utils.service_portal import CLIENT_HIDDEN_ACTIVITY_FIELDS

# `activity_type` is not optional in practice: the endpoint's default branch chains raw
# querysets and subscripts them, so every real caller passes it. Using the same value the
# web client uses keeps this test on the code path that actually runs.
FEED_URL = "/api/workspaces/{slug}/projects/{project_id}/issues/{issue_id}/history/?activity_type=issue-property"
USER_ACTIVITY_URL = "/api/workspaces/{slug}/user-activity/{user_id}/"

#: The number a client must never be handed, and the string it appears in -- written the way
#: ``_service_log_batch_summary`` writes it after D68, so this fixture keeps mirroring the row
#: the code really persists instead of a decimal notation no surface produces any more.
LOGGED_HOURS_DETAIL = "3h 30min (Fora do expediente)"


def _user(prefix, workspace, project, role):
    unique_id = uuid4().hex[:8]
    user = User.objects.create(
        email=f"{prefix}-{unique_id}@plane.so",
        username=f"{prefix}_{unique_id}",
        first_name=prefix.title(),
    )
    user.set_password("test-password")
    user.save()
    WorkspaceMember.objects.create(workspace=workspace, member=user, role=role)
    ProjectMember.objects.create(project=project, member=user, workspace=workspace, role=role)
    return user


@pytest.fixture
def project(db, workspace, create_user):
    project = Project.objects.create(
        name="Marubeni Support",
        identifier="MARU",
        workspace=workspace,
        created_by=create_user,
        guest_view_all_features=True,
    )
    ProjectMember.objects.create(project=project, member=create_user, workspace=workspace, role=20)
    return project


@pytest.fixture
def technician(db, workspace, project):
    return _user("tech", workspace, project, 15)


@pytest.fixture
def client_user(db, workspace, project):
    return _user("marcel", workspace, project, 5)


@pytest.fixture
def issue(db, project, workspace, technician):
    issue = Issue(name="Server is down", project=project, workspace=workspace, priority="none")
    issue.save(created_by_id=technician.id)
    return issue


@pytest.fixture
def activities(db, project, workspace, issue, technician):
    """One row per hidden field, plus one that must stay visible.

    Written directly rather than driven through the work log endpoints: the point here is
    what the *readers* return for a given stored row, and building them by hand makes the
    leaking value literal in the test instead of an emergent property of six other
    modules.
    """
    rows = [
        IssueActivity(
            issue=issue,
            project=project,
            workspace=workspace,
            actor=technician,
            field="service_log",
            verb="created",
            comment="created a work log",
            new_value=LOGGED_HOURS_DETAIL,
            epoch=1,
        ),
        IssueActivity(
            issue=issue,
            project=project,
            workspace=workspace,
            actor=technician,
            field="service_log_author",
            verb="updated",
            comment="reassigned the author of a work log",
            old_value="Joao",
            new_value="Maria",
            epoch=2,
        ),
        IssueActivity(
            issue=issue,
            project=project,
            workspace=workspace,
            actor=technician,
            field="service_log_delegation",
            verb="created",
            comment="logged work on behalf of another member",
            old_value="Joao",
            new_value="Maria",
            epoch=3,
        ),
        IssueActivity(
            issue=issue,
            project=project,
            workspace=workspace,
            actor=technician,
            field="service_log_hour_type_override",
            verb="updated",
            comment="overrode the suggested hour type on a work log",
            old_value="Horario comercial",
            new_value="Fora do expediente",
            epoch=4,
        ),
        # The positive control, and the reason a client is shown the feed at all.
        IssueActivity(
            issue=issue,
            project=project,
            workspace=workspace,
            actor=technician,
            field="state",
            verb="updated",
            comment="updated the state",
            old_value="Todo",
            new_value="Done",
            epoch=5,
        ),
    ]

    for row in rows:
        row.save()

    return rows


def _fields(payload):
    return {row.get("field") for row in payload if isinstance(row, dict)}


def _api(user):
    api = APIClient()
    api.force_authenticate(user=user)
    return api


@pytest.mark.contract
class TestTheWorkItemFeedHidesTheWorkLogRowsFromAClient:
    @pytest.mark.django_db
    def test_the_client_receives_none_of_the_hidden_fields(self, project, issue, client_user, activities):
        response = _api(client_user).get(
            FEED_URL.format(slug=project.workspace.slug, project_id=project.id, issue_id=issue.id)
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert _fields(response.data) & CLIENT_HIDDEN_ACTIVITY_FIELDS == set()

    @pytest.mark.django_db
    def test_the_logged_hours_string_appears_nowhere_in_the_clients_payload(
        self, project, issue, client_user, activities
    ):
        """Asserted against the rendered body, not the field list: a future change that
        moved the number into a different key would still be caught."""
        response = _api(client_user).get(
            FEED_URL.format(slug=project.workspace.slug, project_id=project.id, issue_id=issue.id)
        )

        # Status asserted before the absence: a 500 carries no work log hours either, and
        # would make this test pass for the wrong reason.
        assert response.status_code == status.HTTP_200_OK, response.data
        assert LOGGED_HOURS_DETAIL not in str(response.data)
        # And the quantity on its own, so a payload that dropped only the hour type name would
        # still fail. "3h 30min" is the rendering; the leak R11(c) forbids is the 3.5 hours it
        # states, which a client divides into `equivalent_hours` to recover the multiplier.
        assert "3h 30min" not in str(response.data)

    @pytest.mark.django_db
    def test_the_client_still_sees_the_rest_of_the_feed(self, project, issue, client_user, activities):
        """The positive control. Hiding the whole feed would also pass the test above."""
        response = _api(client_user).get(
            FEED_URL.format(slug=project.workspace.slug, project_id=project.id, issue_id=issue.id)
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert "state" in _fields(response.data)

    @pytest.mark.django_db
    def test_a_member_does_receive_the_work_log_rows(self, project, issue, technician, activities):
        """The other positive control: the rows exist, are readable, and carry the number.
        Without this, a feed that dropped them for everybody would look correct."""
        response = _api(technician).get(
            FEED_URL.format(slug=project.workspace.slug, project_id=project.id, issue_id=issue.id)
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert CLIENT_HIDDEN_ACTIVITY_FIELDS <= _fields(response.data)
        assert LOGGED_HOURS_DETAIL in str(response.data)


@pytest.mark.contract
class TestTheUserActivityReaderHidesThemToo:
    """The second door. `WorkspaceEntityPermission` admits any active member on a safe
    method, and the actor id is a URL segment -- so a client can name a technician."""

    @pytest.mark.django_db
    def test_a_client_asking_for_a_technicians_activity_gets_no_work_log_rows(
        self, project, client_user, technician, activities
    ):
        response = _api(client_user).get(
            USER_ACTIVITY_URL.format(slug=project.workspace.slug, user_id=technician.id)
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        body = str(response.data)
        assert LOGGED_HOURS_DETAIL not in body
        assert _fields(response.data.get("results", [])) & CLIENT_HIDDEN_ACTIVITY_FIELDS == set()

    @pytest.mark.django_db
    def test_a_member_asking_the_same_question_does_get_them(
        self, project, technician, activities, create_user
    ):
        response = _api(create_user).get(
            USER_ACTIVITY_URL.format(slug=project.workspace.slug, user_id=technician.id)
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert LOGGED_HOURS_DETAIL in str(response.data)


@pytest.mark.contract
class TestTheHiddenSetIsTheFourWorkLogFields:
    def test_the_set_is_named_and_complete(self):
        """Pinned so a phase that adds a fifth work log activity type has to come here and
        decide, rather than shipping it visible by default."""
        assert CLIENT_HIDDEN_ACTIVITY_FIELDS == {
            "service_log",
            "service_log_author",
            "service_log_delegation",
            "service_log_hour_type_override",
        }

    def test_the_core_exclusions_are_not_in_it(self):
        # Those four are excluded for everybody and are core Plane's business, not R11's.
        assert CLIENT_HIDDEN_ACTIVITY_FIELDS.isdisjoint({"comment", "vote", "reaction", "draft"})
