# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests for the service client endpoints and the derived client link.

Covers the acceptance criteria of the client foundation phase:

* CRUD restricted to workspace admins (1)
* Linking a project to a client (2)
* Two projects of one client resolving to the same client (3)
* Filtering and grouping work items by client (5)
* Users resolving to their clients through project membership only (6, 7)
* Deleting a client with linked projects being refused (12)
* Clients never crossing a workspace boundary (14)
"""

from unittest.mock import patch
from uuid import uuid4

import pytest
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APIClient

from plane.db.models import (
    Issue,
    Project,
    ProjectMember,
    ServiceClient,
    State,
    User,
    Workspace,
    WorkspaceMember,
)

LIST_URL = "/api/workspaces/{slug}/service-clients/"
DETAIL_URL = "/api/workspaces/{slug}/service-clients/{pk}/"
ME_URL = "/api/workspaces/{slug}/service-clients/me/"
PROJECTS_URL = "/api/workspaces/{slug}/service-clients/{pk}/projects/"
ASSIGN_URL = "/api/workspaces/{slug}/service-clients/{pk}/assign-projects/"
PROJECT_DETAIL_URL = "/api/workspaces/{slug}/projects/{project_id}/"
ISSUES_URL = "/api/workspaces/{slug}/projects/{project_id}/issues/"


def _make_user(role_label):
    unique_id = uuid4().hex[:8]
    user = User.objects.create(
        email=f"{role_label}-{unique_id}@plane.so",
        username=f"{role_label}_{unique_id}",
        first_name=role_label.title(),
        last_name="User",
    )
    user.set_password("test-password")
    user.save()
    return user


def _authenticated_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture(autouse=True)
def no_celery_dispatch():
    """Swallow background task dispatch.

    These endpoints enqueue activity and recent-visit tasks, and there is no
    broker under test. Patching apply_async covers delay() as well, since delay
    is a thin wrapper over it.
    """
    with patch("celery.app.task.Task.apply_async", return_value=None):
        yield


@pytest.fixture
def member_user(db, workspace):
    """An active workspace MEMBER (role=15)."""
    user = _make_user("member")
    WorkspaceMember.objects.create(workspace=workspace, member=user, role=15)
    return user


@pytest.fixture
def guest_user(db, workspace):
    """An active workspace GUEST (role=5)."""
    user = _make_user("guest")
    WorkspaceMember.objects.create(workspace=workspace, member=user, role=5)
    return user


@pytest.fixture
def member_client(member_user):
    return _authenticated_client(member_user)


@pytest.fixture
def guest_client(guest_user):
    return _authenticated_client(guest_user)


@pytest.fixture
def marubeni(db, workspace):
    return ServiceClient.objects.create(workspace=workspace, name="Marubeni", tax_id="11222333000181")


@pytest.fixture
def terlogs(db, workspace, marubeni):
    """A client owned by Marubeni. The parent link carries no behaviour."""
    return ServiceClient.objects.create(workspace=workspace, name="Terlogs", parent=marubeni)


def _make_project(workspace, name, identifier, service_client=None):
    project = Project.objects.create(
        workspace=workspace,
        name=name,
        identifier=identifier,
        service_client=service_client,
        guest_view_all_features=bool(service_client),
    )
    State.objects.create(workspace=workspace, project=project, name="Todo", group="unstarted", default=True)
    return project


@pytest.mark.contract
@pytest.mark.django_db
class TestServiceClientCrudPermissions:
    """Criterion 1: only workspace admins may manage clients."""

    def test_admin_creates_a_client(self, session_client, workspace):
        response = session_client.post(
            LIST_URL.format(slug=workspace.slug),
            {"name": "Marubeni", "tax_id": "11.222.333/0001-81", "default_billing_mode": "contract"},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED, response.data
        # The CNPJ is normalized on the way in; the mask is presentation only.
        assert response.data["tax_id"] == "11222333000181"
        assert response.data["project_count"] == 0

    def test_admin_rejects_an_invalid_cnpj(self, session_client, workspace):
        response = session_client.post(
            LIST_URL.format(slug=workspace.slug),
            {"name": "Bad CNPJ", "tax_id": "11.222.333/0001-82"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "tax_id" in response.data

    def test_admin_may_omit_the_cnpj(self, session_client, workspace):
        response = session_client.post(
            LIST_URL.format(slug=workspace.slug), {"name": "Foreign Co"}, format="json"
        )
        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert response.data["tax_id"] is None

    def test_admin_updates_and_deactivates(self, session_client, workspace, marubeni):
        response = session_client.patch(
            DETAIL_URL.format(slug=workspace.slug, pk=marubeni.id),
            {"is_active": False, "trade_name": "Marubeni Brasil"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["is_active"] is False
        assert response.data["trade_name"] == "Marubeni Brasil"

    def test_member_can_read_but_not_write(self, member_client, workspace, marubeni):
        """Technicians need client names to display, filter and group work items,
        so reads are open to members while writes are not."""
        list_response = member_client.get(LIST_URL.format(slug=workspace.slug))
        assert list_response.status_code == status.HTTP_200_OK
        assert len(list_response.data) == 1

        create_response = member_client.post(
            LIST_URL.format(slug=workspace.slug), {"name": "Sneaky"}, format="json"
        )
        assert create_response.status_code == status.HTTP_403_FORBIDDEN

        patch_response = member_client.patch(
            DETAIL_URL.format(slug=workspace.slug, pk=marubeni.id), {"name": "Renamed"}, format="json"
        )
        assert patch_response.status_code == status.HTTP_403_FORBIDDEN

        delete_response = member_client.delete(DETAIL_URL.format(slug=workspace.slug, pk=marubeni.id))
        assert delete_response.status_code == status.HTTP_403_FORBIDDEN

    def test_guest_cannot_read_the_client_list(self, guest_client, workspace, marubeni):
        """The client portal is a later phase; a guest has no business reading the
        workspace's client roster, which would disclose every company served."""
        response = guest_client.get(LIST_URL.format(slug=workspace.slug))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_only_active_filter(self, session_client, workspace, marubeni):
        ServiceClient.objects.create(workspace=workspace, name="Dormant", is_active=False)

        everything = session_client.get(LIST_URL.format(slug=workspace.slug))
        assert len(everything.data) == 2

        active_only = session_client.get(LIST_URL.format(slug=workspace.slug), {"only_active": "true"})
        assert len(active_only.data) == 1
        assert active_only.data[0]["name"] == "Marubeni"


@pytest.mark.contract
@pytest.mark.django_db
class TestServiceClientWorkspaceIsolation:
    """Criterion 14: a client of one workspace is never visible or selectable in another."""

    @pytest.fixture
    def other_workspace(self, db, create_user):
        other = Workspace.objects.create(name="Other", owner=create_user, slug="other-workspace")
        WorkspaceMember.objects.create(workspace=other, member=create_user, role=20)
        return other

    def test_client_is_not_listed_in_another_workspace(self, session_client, workspace, marubeni, other_workspace):
        response = session_client.get(LIST_URL.format(slug=other_workspace.slug))
        assert response.status_code == status.HTTP_200_OK
        assert response.data == []

    def test_client_cannot_be_retrieved_through_another_workspace(
        self, session_client, workspace, marubeni, other_workspace
    ):
        response = session_client.get(DETAIL_URL.format(slug=other_workspace.slug, pk=marubeni.id))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    @override_settings(APP_BASE_URL="http://localhost:3000")
    def test_project_cannot_be_linked_to_a_client_of_another_workspace(
        self, session_client, workspace, other_workspace
    ):
        """The project serializer uses fields = "__all__", so without an explicit
        check the default related field queryset would span every workspace."""
        alien = ServiceClient.objects.create(workspace=other_workspace, name="Alien")
        project = _make_project(workspace, "Internal", "INT")

        response = session_client.patch(
            PROJECT_DETAIL_URL.format(slug=workspace.slug, project_id=project.id),
            {"service_client": str(alien.id)},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "service_client" in response.data

        project.refresh_from_db()
        assert project.service_client_id is None

    def test_parent_must_belong_to_the_same_workspace(self, session_client, workspace, other_workspace):
        alien = ServiceClient.objects.create(workspace=other_workspace, name="Alien Parent")
        response = session_client.post(
            LIST_URL.format(slug=workspace.slug),
            {"name": "Child", "parent": str(alien.id)},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "parent" in response.data


@pytest.mark.contract
@pytest.mark.django_db
class TestServiceClientDeletion:
    """Criterion 12: a client with linked projects can only be deactivated."""

    def test_delete_is_refused_when_projects_are_linked(self, session_client, workspace, marubeni):
        _make_project(workspace, "Marubeni Support", "MSUP", marubeni)

        response = session_client.delete(DETAIL_URL.format(slug=workspace.slug, pk=marubeni.id))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["linked_project_count"] == 1
        assert ServiceClient.objects.filter(id=marubeni.id).exists()

    def test_delete_is_refused_even_for_a_soft_deleted_project(self, session_client, workspace, marubeni):
        """A soft deleted project still holds the foreign key. Allowing the delete
        here would leave the periodic hard delete task to fail later against the
        database constraint."""
        project = _make_project(workspace, "Marubeni Support", "MSUP2", marubeni)
        Project.objects.filter(id=project.id).update(deleted_at="2026-01-01T00:00:00Z")

        response = session_client.delete(DETAIL_URL.format(slug=workspace.slug, pk=marubeni.id))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert ServiceClient.objects.filter(id=marubeni.id).exists()

    def test_delete_succeeds_without_linked_projects(self, session_client, workspace, marubeni):
        response = session_client.delete(DETAIL_URL.format(slug=workspace.slug, pk=marubeni.id))
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not ServiceClient.objects.filter(id=marubeni.id).exists()


@pytest.mark.contract
@pytest.mark.django_db
class TestProjectServiceClientLink:
    """Criterion 2 and 3."""

    @override_settings(APP_BASE_URL="http://localhost:3000")
    def test_admin_links_a_project_to_a_client(self, session_client, workspace, marubeni):
        project = _make_project(workspace, "Marubeni Support", "MSUP")

        response = session_client.patch(
            PROJECT_DETAIL_URL.format(slug=workspace.slug, project_id=project.id),
            {"service_client": str(marubeni.id), "guest_view_all_features": True},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.data

        project.refresh_from_db()
        assert project.service_client_id == marubeni.id
        assert project.guest_view_all_features is True

    @override_settings(APP_BASE_URL="http://localhost:3000")
    def test_linking_does_not_enable_guest_visibility_on_its_own(self, session_client, workspace, marubeni):
        """The master context requires the flag to be suggested, not imposed
        silently, so the backend must not infer it."""
        project = _make_project(workspace, "Quiet", "QUI")
        assert project.guest_view_all_features is False

        session_client.patch(
            PROJECT_DETAIL_URL.format(slug=workspace.slug, project_id=project.id),
            {"service_client": str(marubeni.id)},
            format="json",
        )

        project.refresh_from_db()
        assert project.service_client_id == marubeni.id
        assert project.guest_view_all_features is False

    @override_settings(APP_BASE_URL="http://localhost:3000")
    def test_two_projects_of_one_client_resolve_to_the_same_client(self, session_client, workspace, marubeni):
        support = _make_project(workspace, "Marubeni Support", "MSUP", marubeni)
        erp = _make_project(workspace, "Marubeni ERP", "MERP", marubeni)

        response = session_client.get(PROJECTS_URL.format(slug=workspace.slug, pk=marubeni.id))
        assert response.status_code == status.HTTP_200_OK
        assert {str(row["id"]) for row in response.data} == {str(support.id), str(erp.id)}

        for project in (support, erp):
            assert project.service_client_id == marubeni.id

    def test_bulk_assign_projects(self, session_client, workspace, marubeni):
        """Existing projects are not migrated, so they must be attributable in bulk."""
        first = _make_project(workspace, "Legacy One", "LEG1")
        second = _make_project(workspace, "Legacy Two", "LEG2")

        response = session_client.post(
            ASSIGN_URL.format(slug=workspace.slug, pk=marubeni.id),
            {"project_ids": [str(first.id), str(second.id)], "guest_view_all_features": True},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["updated_project_count"] == 2

        for project in (first, second):
            project.refresh_from_db()
            assert project.service_client_id == marubeni.id
            assert project.guest_view_all_features is True

    def test_bulk_assign_rejects_projects_of_another_workspace(self, session_client, workspace, marubeni, create_user):
        other = Workspace.objects.create(name="Other", owner=create_user, slug="other-ws-bulk")
        WorkspaceMember.objects.create(workspace=other, member=create_user, role=20)
        alien_project = _make_project(other, "Alien", "ALN")

        response = session_client.post(
            ASSIGN_URL.format(slug=workspace.slug, pk=marubeni.id),
            {"project_ids": [str(alien_project.id)]},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        alien_project.refresh_from_db()
        assert alien_project.service_client_id is None


@pytest.mark.contract
@pytest.mark.django_db
class TestDerivedClientOnWorkItems:
    """Criteria 4, 5 and 9: the client is derived from the project."""

    def test_work_item_exposes_the_derived_client(self, session_client, workspace, marubeni, create_user):
        project = _make_project(workspace, "Marubeni Support", "MSUP", marubeni)
        ProjectMember.objects.create(workspace=workspace, project=project, member=create_user, role=20)
        issue = Issue.objects.create(workspace=workspace, project=project, name="Ticket")

        response = session_client.get(
            f"{ISSUES_URL.format(slug=workspace.slug, project_id=project.id)}{issue.id}/"
        )
        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["service_client_id"] == str(marubeni.id)

    @override_settings(APP_BASE_URL="http://localhost:3000")
    def test_work_item_client_cannot_be_written(self, session_client, workspace, marubeni, terlogs, create_user):
        """Criterion 4: there is no editable client field on a work item. Sending
        one must be ignored rather than moving the item to another client."""
        project = _make_project(workspace, "Marubeni Support", "MSUP", marubeni)
        ProjectMember.objects.create(workspace=workspace, project=project, member=create_user, role=20)
        issue = Issue.objects.create(workspace=workspace, project=project, name="Ticket")

        response = session_client.patch(
            f"{ISSUES_URL.format(slug=workspace.slug, project_id=project.id)}{issue.id}/",
            {"service_client_id": str(terlogs.id), "name": "Renamed ticket"},
            format="json",
        )
        # The issue update endpoint answers 204 for some payload shapes; either way
        # the point of this test is what happened to the client, not the status.
        assert response.status_code in (
            status.HTTP_200_OK,
            status.HTTP_201_CREATED,
            status.HTTP_204_NO_CONTENT,
        ), response.data

        issue.refresh_from_db()
        assert issue.name == "Renamed ticket"
        # The client still follows the project, not the payload.
        assert issue.project.service_client_id == marubeni.id

    def test_filter_work_items_by_client(self, session_client, workspace, marubeni, terlogs, create_user):
        project = _make_project(workspace, "Marubeni Support", "MSUP", marubeni)
        ProjectMember.objects.create(workspace=workspace, project=project, member=create_user, role=20)
        Issue.objects.create(workspace=workspace, project=project, name="First")
        Issue.objects.create(workspace=workspace, project=project, name="Second")

        url = ISSUES_URL.format(slug=workspace.slug, project_id=project.id)

        matching = session_client.get(url, {"service_client": str(marubeni.id)})
        assert matching.status_code == status.HTTP_200_OK, matching.data
        assert len(matching.data["results"]) == 2

        other = session_client.get(url, {"service_client": str(terlogs.id)})
        assert len(other.data["results"]) == 0

    def test_filter_internal_work(self, session_client, workspace, create_user):
        project = _make_project(workspace, "Internal", "INT")
        ProjectMember.objects.create(workspace=workspace, project=project, member=create_user, role=20)
        Issue.objects.create(workspace=workspace, project=project, name="Internal task")

        response = session_client.get(
            ISSUES_URL.format(slug=workspace.slug, project_id=project.id), {"service_client": "None"}
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1

    def test_group_work_items_by_client(self, session_client, workspace, marubeni, create_user):
        project = _make_project(workspace, "Marubeni Support", "MSUP", marubeni)
        ProjectMember.objects.create(workspace=workspace, project=project, member=create_user, role=20)
        Issue.objects.create(workspace=workspace, project=project, name="First")
        Issue.objects.create(workspace=workspace, project=project, name="Second")

        response = session_client.get(
            ISSUES_URL.format(slug=workspace.slug, project_id=project.id),
            {"group_by": "project__service_client_id"},
        )
        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["grouped_by"] == "project__service_client_id"

        group = response.data["results"][str(marubeni.id)]
        assert group["total_results"] == 2
        assert len(group["results"]) == 2

    def test_group_by_an_arbitrary_field_is_still_rejected(self, session_client, workspace, create_user):
        """The group_by allowlist is a security control; adding a member to it
        must not have opened the door to arbitrary field names."""
        project = _make_project(workspace, "Internal", "INT")
        ProjectMember.objects.create(workspace=workspace, project=project, member=create_user, role=20)

        response = session_client.get(
            ISSUES_URL.format(slug=workspace.slug, project_id=project.id),
            {"group_by": "created_by__password"},
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.contract
@pytest.mark.django_db
class TestClientsOfCurrentUser:
    """Criteria 6, 7 and 8: the user to client link is derived from project membership."""

    def test_user_in_two_clients_resolves_to_both(self, workspace, marubeni, terlogs):
        """Marcel answers for two companies. No association table is involved."""
        marcel = _make_user("marcel")
        WorkspaceMember.objects.create(workspace=workspace, member=marcel, role=5)

        marubeni_project = _make_project(workspace, "Marubeni", "MAR", marubeni)
        terlogs_project = _make_project(workspace, "Terlogs", "TER", terlogs)
        for project in (marubeni_project, terlogs_project):
            ProjectMember.objects.create(workspace=workspace, project=project, member=marcel, role=5)

        response = _authenticated_client(marcel).get(ME_URL.format(slug=workspace.slug))
        assert response.status_code == status.HTTP_200_OK, response.data
        assert {row["name"] for row in response.data} == {"Marubeni", "Terlogs"}

    def test_user_in_one_client_resolves_to_only_that_client(self, workspace, marubeni, terlogs):
        adriano = _make_user("adriano")
        WorkspaceMember.objects.create(workspace=workspace, member=adriano, role=5)

        _make_project(workspace, "Marubeni", "MAR", marubeni)
        terlogs_project = _make_project(workspace, "Terlogs", "TER", terlogs)
        ProjectMember.objects.create(workspace=workspace, project=terlogs_project, member=adriano, role=5)

        response = _authenticated_client(adriano).get(ME_URL.format(slug=workspace.slug))
        assert response.status_code == status.HTTP_200_OK
        assert [row["name"] for row in response.data] == ["Terlogs"]

    def test_two_projects_of_one_client_do_not_duplicate_it(self, workspace, marubeni):
        """The reverse relation would repeat the client once per project without
        the distinct() in the queryset."""
        user = _make_user("tech")
        WorkspaceMember.objects.create(workspace=workspace, member=user, role=15)

        support = _make_project(workspace, "Marubeni Support", "MSUP", marubeni)
        erp = _make_project(workspace, "Marubeni ERP", "MERP", marubeni)
        for project in (support, erp):
            ProjectMember.objects.create(workspace=workspace, project=project, member=user, role=15)

        response = _authenticated_client(user).get(ME_URL.format(slug=workspace.slug))
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1

    def test_user_without_projects_resolves_to_no_clients(self, workspace, marubeni):
        loner = _make_user("loner")
        WorkspaceMember.objects.create(workspace=workspace, member=loner, role=15)
        _make_project(workspace, "Marubeni", "MAR", marubeni)

        response = _authenticated_client(loner).get(ME_URL.format(slug=workspace.slug))
        assert response.status_code == status.HTTP_200_OK
        assert response.data == []

    def test_response_does_not_leak_billing_configuration(self, workspace, marubeni):
        """This endpoint is the foundation the client portal will build on, so it
        must not publish billing fields."""
        user = _make_user("portal")
        WorkspaceMember.objects.create(workspace=workspace, member=user, role=5)
        project = _make_project(workspace, "Marubeni", "MAR", marubeni)
        ProjectMember.objects.create(workspace=workspace, project=project, member=user, role=5)

        response = _authenticated_client(user).get(ME_URL.format(slug=workspace.slug))
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        payload = response.data[0]
        for forbidden in ("default_billing_mode", "tax_id", "notes", "parent"):
            assert forbidden not in payload
