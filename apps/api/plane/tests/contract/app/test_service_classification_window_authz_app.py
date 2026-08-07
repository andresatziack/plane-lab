# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""``GET /service-classification-windows/<pk>/`` was readable by anybody with a login.

``ServiceClassificationWindowViewSet`` routes ``get: retrieve`` but defined no ``retrieve``,
so the inherited ``ModelViewSet`` one ran under ``BaseViewSet.permission_classes =
[IsAuthenticated]`` -- no ``allow_permission``, therefore no role check and no membership
check. ``get_queryset`` filters on the slug taken from the URL and nothing else.

**The consequence is cross-tenant, not a role leak.** On an instance hosting two unrelated
companies, an authenticated user of either -- or of neither -- could read the other's window
configuration given a slug and an id. Which hours fall in which window is the operation's
pricing structure read from another angle.

Deliberately self-contained: two workspaces built here, and no fixture from any feature
branch. This file and the decorator it proves are meant to be reviewable, findable and
backportable on their own.
"""

from uuid import uuid4

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from plane.db.models import (
    ServiceClassificationWindow,
    ServiceHourType,
    User,
    Workspace,
    WorkspaceMember,
)

URL = "/api/workspaces/{slug}/service-classification-windows/{pk}/"


def _user(prefix):
    unique_id = uuid4().hex[:8]
    user = User.objects.create(
        email=f"{prefix}-{unique_id}@plane.so",
        username=f"{prefix}_{unique_id}",
        first_name=prefix.title(),
    )
    user.set_password("test-password")
    user.save()
    return user


def _api(user):
    api = APIClient()
    api.force_authenticate(user=user)
    return api


@pytest.fixture
def other_workspace(db):
    """A second, unrelated company on the same instance. The tenancy boundary under test."""
    owner = _user("other-owner")
    other = Workspace.objects.create(name="Other Company", owner=owner, slug="other-company")
    WorkspaceMember.objects.create(workspace=other, member=owner, role=20)
    return other


@pytest.fixture
def window(db, workspace):
    """An after-hours window in the first workspace."""
    hour_type = ServiceHourType.objects.create(name="Fora do expediente", workspace=workspace)

    return ServiceClassificationWindow.objects.create(
        workspace=workspace,
        hour_type=hour_type,
        day_scope="monday",
        start_minute=18 * 60,
        end_minute=24 * 60,
    )


@pytest.mark.contract
class TestOnlyMembersOfTheWorkspaceCanReadAClassificationWindow:
    @pytest.mark.django_db
    def test_a_member_of_another_workspace_is_refused(self, workspace, other_workspace, window):
        """The cross-tenant case, and the reason this is being fixed on its own.

        This user is a legitimate ADMIN of their own company and has no relationship to the
        workspace being read. ``IsAuthenticated`` alone was letting them through.
        """
        intruder = _user("intruder")
        WorkspaceMember.objects.create(workspace=other_workspace, member=intruder, role=20)

        response = _api(intruder).get(URL.format(slug=workspace.slug, pk=window.id))

        assert response.status_code == status.HTTP_403_FORBIDDEN, response.data
        assert str(window.id) not in str(response.data)

    @pytest.mark.django_db
    def test_a_user_who_belongs_to_no_workspace_at_all_is_refused(self, workspace, window):
        """The weakest possible caller: authenticated and nothing else. Anyone who can sign up
        was in this position."""
        stranger = _user("stranger")

        response = _api(stranger).get(URL.format(slug=workspace.slug, pk=window.id))

        assert response.status_code == status.HTTP_403_FORBIDDEN, response.data
        assert str(window.id) not in str(response.data)

    @pytest.mark.django_db
    def test_a_guest_of_the_workspace_is_refused(self, workspace, window):
        """The role case. The class docstring already promised "GUEST to neither", and every
        written action honoured it -- this one was only routed."""
        guest = _user("guest")
        WorkspaceMember.objects.create(workspace=workspace, member=guest, role=5)

        response = _api(guest).get(URL.format(slug=workspace.slug, pk=window.id))

        assert response.status_code == status.HTTP_403_FORBIDDEN, response.data

    @pytest.mark.django_db
    def test_a_member_of_the_workspace_still_reads_it(self, workspace, window):
        """The positive control. Reads are open to admins and members, and must stay open --
        otherwise this fix is an outage rather than a fix."""
        member = _user("member")
        WorkspaceMember.objects.create(workspace=workspace, member=member, role=15)

        response = _api(member).get(URL.format(slug=workspace.slug, pk=window.id))

        assert response.status_code == status.HTTP_200_OK, response.data
        assert str(response.data["id"]) == str(window.id)

    @pytest.mark.django_db
    def test_an_absent_window_is_a_404_for_a_member_and_a_403_for_an_outsider(
        self, workspace, other_workspace
    ):
        """The distinction that made the bug visible: an outsider must be refused *before* the
        lookup, so they cannot use the difference between 404 and 200 to probe which ids exist
        in somebody else's workspace."""
        member = _user("member")
        WorkspaceMember.objects.create(workspace=workspace, member=member, role=15)
        intruder = _user("intruder")
        WorkspaceMember.objects.create(workspace=other_workspace, member=intruder, role=20)

        missing = URL.format(slug=workspace.slug, pk=uuid4())

        assert _api(member).get(missing).status_code == status.HTTP_404_NOT_FOUND
        assert _api(intruder).get(missing).status_code == status.HTTP_403_FORBIDDEN
