# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""What a client's own user may change on a work item. Criteria 6, 19 and 20.

Criterion 19 is the one that keeps the inclusion of GUEST from being a privilege
escalation, so it is written first and it is written against the path that is actually
open: ``allow_permission(..., creator=True, model=Issue)`` releases
``IssueViewSet.partial_update`` to whoever created the row before the role list is read.
A client who opened a ticket therefore reaches the view regardless of role, and without
the allowlist would be changing the responsible party on it.

Every refusal here asserts the persisted value is unchanged rather than only the status
code. A 204 that silently dropped the forbidden field and a 204 that applied it look
identical from the outside, and the second one is the bug.

Fixture note: ``BaseModel.save`` overwrites ``created_by`` from the request context, so
an issue whose author matters must be built with ``issue.save(created_by_id=...)``. The
creator bypass keys on exactly that column, so an issue created the obvious way would
test the wrong path.
"""

from uuid import uuid4

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from plane.db.models import Issue, Project, ProjectMember, State, User, WorkspaceMember
from plane.db.models.state import StateGroup
from plane.utils.service_portal import STATE_GROUP_NOT_PERMITTED_FOR_CLIENT

DETAIL_URL = "/api/workspaces/{slug}/projects/{project_id}/issues/{issue_id}/"


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
        # The portal premise: a client's users see everything in their own project.
        guest_view_all_features=True,
    )
    ProjectMember.objects.create(project=project, member=create_user, workspace=workspace, role=20)
    return project


@pytest.fixture
def technician(db, workspace, project):
    return _user("tech", workspace, project, 15)


@pytest.fixture
def client_user(db, workspace, project):
    """Marcel: the Cliente's own user, a GUEST of the Cliente's project."""
    return _user("marcel", workspace, project, 5)


@pytest.fixture
def client_api(client_user):
    api = APIClient()
    api.force_authenticate(user=client_user)
    return api


def _state(project, workspace, group, name=None):
    return State.objects.create(
        name=name or f"{group}-{uuid4().hex[:6]}",
        project=project,
        workspace=workspace,
        group=group,
    )


@pytest.fixture
def states(project, workspace):
    return {
        group.value: _state(project, workspace, group.value)
        for group in StateGroup
        if group is not StateGroup.TRIAGE
    }


@pytest.fixture
def triage_state(project, workspace):
    # `State.objects` excludes triage, so it has to be built through the base manager.
    state = State(
        name="Triage",
        project=project,
        workspace=workspace,
        group=StateGroup.TRIAGE.value,
        is_triage=True,
    )
    state.save()
    return state


def _make_issue(project, workspace, author, state=None):
    issue = Issue(
        name="Server is down",
        project=project,
        workspace=workspace,
        state=state,
        priority="none",
    )
    issue.save(created_by_id=author.id)
    return issue


@pytest.fixture
def own_issue(project, workspace, client_user, states):
    """Opened by the client themself: the creator-bypass path."""
    return _make_issue(project, workspace, client_user, states[StateGroup.UNSTARTED.value])


@pytest.fixture
def foreign_issue(project, workspace, technician, states):
    """Opened by a technician: the role-list path."""
    return _make_issue(project, workspace, technician, states[StateGroup.UNSTARTED.value])


def _url(project, issue):
    return DETAIL_URL.format(slug=project.workspace.slug, project_id=project.id, issue_id=issue.id)


@pytest.mark.contract
class TestCriterion19TheClientCannotReachAnyOtherField:
    """The allowlist. Not one criterion among twenty-one: the one that stops the
    inclusion of GUEST from being an escalation."""

    @pytest.mark.django_db
    def test_the_client_cannot_change_the_responsible_party_on_their_own_issue(
        self, client_api, project, own_issue, technician
    ):
        response = client_api.patch(
            _url(project, own_issue),
            {"assignee_ids": [str(technician.id)]},
            format="json",
        )

        own_issue.refresh_from_db()
        assert response.status_code == status.HTTP_204_NO_CONTENT, response.data
        assert list(own_issue.assignees.all()) == []

    @pytest.mark.django_db
    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("name", "renamed by the client"),
            ("description_html", "<p>rewritten</p>"),
            ("start_date", "2026-01-01"),
            ("target_date", "2026-12-31"),
            ("estimate_point", None),
        ],
    )
    def test_no_other_field_is_persisted(self, client_api, project, own_issue, field, value):
        """The five the brief names, each asserted against the stored row."""
        before = getattr(own_issue, field)

        response = client_api.patch(_url(project, own_issue), {field: value}, format="json")

        own_issue.refresh_from_db()
        assert response.status_code == status.HTTP_204_NO_CONTENT, response.data
        assert getattr(own_issue, field) == before

    @pytest.mark.django_db
    def test_the_client_cannot_set_a_parent(self, client_api, project, own_issue, foreign_issue):
        response = client_api.patch(
            _url(project, own_issue),
            {"parent": str(foreign_issue.id)},
            format="json",
        )

        own_issue.refresh_from_db()
        assert response.status_code == status.HTTP_204_NO_CONTENT, response.data
        assert own_issue.parent_id is None

    @pytest.mark.django_db
    def test_a_forbidden_field_alongside_an_allowed_one_does_not_ride_along(
        self, client_api, project, own_issue, technician
    ):
        """The payload an attack would actually send."""
        response = client_api.patch(
            _url(project, own_issue),
            {"priority": "urgent", "name": "hijacked", "assignee_ids": [str(technician.id)]},
            format="json",
        )

        own_issue.refresh_from_db()
        assert response.status_code == status.HTTP_204_NO_CONTENT, response.data
        assert own_issue.priority == "urgent"
        assert own_issue.name == "Server is down"
        assert list(own_issue.assignees.all()) == []

    @pytest.mark.django_db
    def test_the_activity_trail_records_the_filtered_payload(
        self, client_api, project, own_issue, technician, monkeypatch
    ):
        """What was applied, not what was attempted -- otherwise the trail asserts a
        change to the responsible party that never happened."""
        captured = {}

        def _capture(*args, **kwargs):
            captured.update(kwargs)

        monkeypatch.setattr("plane.app.views.issue.base.issue_activity.delay", _capture)

        client_api.patch(
            _url(project, own_issue),
            {"priority": "high", "assignee_ids": [str(technician.id)]},
            format="json",
        )

        assert "assignee_ids" not in captured["requested_data"]
        assert "priority" in captured["requested_data"]


@pytest.mark.contract
class TestCriterion6TheClientClosesAndReopens:
    @pytest.mark.django_db
    @pytest.mark.parametrize(
        "group",
        [
            StateGroup.COMPLETED.value,
            StateGroup.CANCELLED.value,
            StateGroup.UNSTARTED.value,
            StateGroup.STARTED.value,
        ],
    )
    def test_the_client_may_move_the_issue_into_each_permitted_group(
        self, client_api, project, own_issue, states, group
    ):
        response = client_api.patch(
            _url(project, own_issue),
            {"state_id": str(states[group].id)},
            format="json",
        )

        own_issue.refresh_from_db()
        assert response.status_code == status.HTTP_204_NO_CONTENT, response.data
        assert own_issue.state_id == states[group].id

    @pytest.mark.django_db
    def test_the_client_may_change_the_priority(self, client_api, project, own_issue):
        response = client_api.patch(_url(project, own_issue), {"priority": "urgent"}, format="json")

        own_issue.refresh_from_db()
        assert response.status_code == status.HTTP_204_NO_CONTENT, response.data
        assert own_issue.priority == "urgent"

    @pytest.mark.django_db
    def test_the_client_may_close_an_issue_a_technician_opened(
        self, client_api, project, foreign_issue, states
    ):
        """The role-list path rather than the creator bypass: this is what adding GUEST
        to `allowed_roles` is for."""
        response = client_api.patch(
            _url(project, foreign_issue),
            {"state_id": str(states[StateGroup.COMPLETED.value].id)},
            format="json",
        )

        foreign_issue.refresh_from_db()
        assert response.status_code == status.HTTP_204_NO_CONTENT, response.data
        assert foreign_issue.state_id == states[StateGroup.COMPLETED.value].id


@pytest.mark.contract
class TestCriterion20TheStateGroupIsValidatedNotJustTheField:
    @pytest.mark.django_db
    def test_triage_is_refused_with_a_named_code(self, client_api, project, own_issue, triage_state):
        before = own_issue.state_id

        response = client_api.patch(
            _url(project, own_issue),
            {"state_id": str(triage_state.id)},
            format="json",
        )

        own_issue.refresh_from_db()
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.data
        assert response.data["error"] == STATE_GROUP_NOT_PERMITTED_FOR_CLIENT
        assert own_issue.state_id == before

    @pytest.mark.django_db
    def test_backlog_is_refused_with_a_named_code(self, client_api, project, own_issue, states):
        before = own_issue.state_id

        response = client_api.patch(
            _url(project, own_issue),
            {"state_id": str(states[StateGroup.BACKLOG.value].id)},
            format="json",
        )

        own_issue.refresh_from_db()
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.data
        assert response.data["error"] == STATE_GROUP_NOT_PERMITTED_FOR_CLIENT
        assert own_issue.state_id == before

    @pytest.mark.django_db
    def test_a_technician_may_still_use_backlog(self, project, own_issue, states, technician):
        """The positive control that proves the refusal is about the client and not about
        the state."""
        api = APIClient()
        api.force_authenticate(user=technician)

        response = api.patch(
            _url(project, own_issue),
            {"state_id": str(states[StateGroup.BACKLOG.value].id)},
            format="json",
        )

        own_issue.refresh_from_db()
        assert response.status_code == status.HTTP_204_NO_CONTENT, response.data
        assert own_issue.state_id == states[StateGroup.BACKLOG.value].id

    @pytest.mark.django_db
    def test_a_state_from_another_project_is_refused(
        self, client_api, project, own_issue, workspace, create_user
    ):
        """Already true in core, asserted here so it stays true: the serializer scopes the
        state lookup to the project in context."""
        other = Project.objects.create(
            name="Terlogs Support", identifier="TERL", workspace=workspace, created_by=create_user
        )
        foreign_state = _state(other, workspace, StateGroup.COMPLETED.value)
        before = own_issue.state_id

        response = client_api.patch(
            _url(project, own_issue),
            {"state_id": str(foreign_state.id)},
            format="json",
        )

        own_issue.refresh_from_db()
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.data
        assert own_issue.state_id == before


@pytest.mark.contract
class TestTheClientCannotEditWhatTheReadEndpointHides:
    @pytest.mark.django_db
    def test_a_client_cannot_touch_a_foreign_issue_when_full_visibility_is_off(
        self, client_api, project, foreign_issue, states
    ):
        """`retrieve` already refuses to show this issue; the write path had no
        equivalent check until this phase."""
        project.guest_view_all_features = False
        project.save(update_fields=["guest_view_all_features"])
        before = foreign_issue.priority

        response = client_api.patch(_url(project, foreign_issue), {"priority": "urgent"}, format="json")

        foreign_issue.refresh_from_db()
        assert response.status_code == status.HTTP_403_FORBIDDEN, response.data
        assert foreign_issue.priority == before

    @pytest.mark.django_db
    def test_the_client_may_still_touch_their_own_issue_when_visibility_is_off(
        self, client_api, project, own_issue
    ):
        """The positive control: the restriction is about reach, not about the client."""
        project.guest_view_all_features = False
        project.save(update_fields=["guest_view_all_features"])

        response = client_api.patch(_url(project, own_issue), {"priority": "urgent"}, format="json")

        own_issue.refresh_from_db()
        assert response.status_code == status.HTTP_204_NO_CONTENT, response.data
        assert own_issue.priority == "urgent"


@pytest.mark.contract
class TestATechnicianIsNotNarrowed:
    @pytest.mark.django_db
    def test_a_member_still_changes_every_field(self, project, own_issue, technician, states):
        """The allowlist must apply to the client and to nobody else. Without this, a
        passing suite could mean the endpoint was narrowed for everyone."""
        api = APIClient()
        api.force_authenticate(user=technician)

        response = api.patch(
            _url(project, own_issue),
            {
                "name": "Renamed by the technician",
                "assignee_ids": [str(technician.id)],
                "target_date": "2026-12-31",
            },
            format="json",
        )

        own_issue.refresh_from_db()
        assert response.status_code == status.HTTP_204_NO_CONTENT, response.data
        assert own_issue.name == "Renamed by the technician"
        assert [member.id for member in own_issue.assignees.all()] == [technician.id]
        assert str(own_issue.target_date) == "2026-12-31"
