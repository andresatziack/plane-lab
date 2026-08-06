# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests for the criteria this phase inherited from Phases 1 and 2.

Those criteria could not be verified in their own phases because they all depend on
work logs existing. Covered here:

* Phase 1, criterion 11 -- clearing a project's client with work logs is refused, on
  **both** write paths: the project PATCH and the bulk assign action. The phase brief
  claimed the PATCH was the only one; it is not.
* Phase 2, criterion 4 -- deleting a catalogue option that work logs point at returns
  an explanatory code.
* Phase 2, criterion 3 -- a deactivated option keeps rendering its historical name.

Phase 1's criterion 10 has no test here because it has nothing to call: no backend
path changes an existing ``Issue.project_id``. The detection helper is unit tested in
``unit/utils/test_service_log.py``.
"""

from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import pytest
from rest_framework import status

from plane.db.models import (
    Issue,
    Project,
    ServiceBillingType,
    ServiceClient,
    ServiceHourType,
    ServiceLog,
    State,
)

PROJECT_DETAIL_URL = "/api/workspaces/{slug}/projects/{project_id}/"
ASSIGN_URL = "/api/workspaces/{slug}/service-clients/{pk}/assign-projects/"
HOUR_TYPE_DETAIL_URL = "/api/workspaces/{slug}/service-hour-types/{pk}/"
BILLING_TYPE_DETAIL_URL = "/api/workspaces/{slug}/service-billing-types/{pk}/"


@pytest.fixture(autouse=True)
def no_celery_dispatch():
    with patch("celery.app.task.Task.apply_async", return_value=None):
        yield


@pytest.fixture
def marubeni(db, workspace):
    return ServiceClient.objects.create(workspace=workspace, name="Marubeni")


@pytest.fixture
def terlogs(db, workspace):
    return ServiceClient.objects.create(workspace=workspace, name="Terlogs")


@pytest.fixture
def default_hour_type(db, workspace):
    """Horário comercial, and the catalogue default.

    Created first on purpose. ``ServiceCatalogBaseModel.save()`` forces the first live
    option of a catalogue to be the default, and the default cannot be deactivated or
    deleted -- so without a default standing in front of it, every test below would hit
    ``DEFAULT_CANNOT_BE_DELETED`` instead of the in-use check it is actually about.
    """
    return ServiceHourType.objects.create(
        workspace=workspace, name="Horário comercial", multiplier=Decimal("1.00")
    )


@pytest.fixture
def hour_type(db, workspace, default_hour_type):
    """A non-default option, which is what a deletion guard has to be tested against."""
    return ServiceHourType.objects.create(
        workspace=workspace, name="Fora do expediente", multiplier=Decimal("1.50")
    )


@pytest.fixture
def default_billing_type(db, workspace):
    """Contrato, and the catalogue default. Same reasoning as ``default_hour_type``."""
    return ServiceBillingType.objects.create(
        workspace=workspace,
        name="Contrato",
        billing_route=ServiceBillingType.BillingRoute.DEBIT_POOL,
    )


@pytest.fixture
def billing_type(db, workspace, default_billing_type):
    return ServiceBillingType.objects.create(
        workspace=workspace,
        name="Avulso",
        billing_route=ServiceBillingType.BillingRoute.BILL_AMOUNT,
    )


def _make_project(workspace, name, identifier, service_client=None):
    project = Project.objects.create(
        workspace=workspace,
        name=name,
        identifier=identifier,
        service_client=service_client,
        is_time_tracking_enabled=True,
    )
    State.objects.create(workspace=workspace, project=project, name="Todo", group="unstarted", default=True)
    return project


def _log_time(project, author, hour_type, billing_type, **overrides):
    """One persisted work log on a new work item of this project."""
    issue = Issue.objects.create(workspace=project.workspace, project=project, name="Printer broken")

    defaults = {
        "workspace": project.workspace,
        "project": project,
        "issue": issue,
        "author": author,
        "worked_on": "2026-01-05",
        "description": "Replaced the fuser",
        "entry_mode": ServiceLog.EntryMode.DURATION,
        "source": ServiceLog.Source.MANUAL,
        "raw_duration_minutes": 60,
        "logged_hours": Decimal("1.0000"),
        "equivalent_hours": Decimal("1.5000"),
        "debited_hours": Decimal("1.5000"),
        "applied_multiplier": hour_type.multiplier,
        "applied_billing_route": billing_type.billing_route,
        "hour_type": hour_type,
        "billing_type": billing_type,
        "batch_id": uuid4(),
        "segment_index": 0,
    }
    defaults.update(overrides)

    return ServiceLog.objects.create(**defaults)


@pytest.mark.contract
@pytest.mark.django_db
class TestClientLinkCannotBeBrokenByProjectPatch:
    """Phase 1, criterion 11 -- the project PATCH path."""

    def test_clearing_the_client_is_refused_when_the_project_has_work_logs(
        self, session_client, workspace, marubeni, create_user, hour_type, billing_type
    ):
        project = _make_project(workspace, "Marubeni Support", "MSUP", marubeni)
        _log_time(project, create_user, hour_type, billing_type)

        response = session_client.patch(
            PROJECT_DETAIL_URL.format(slug=workspace.slug, project_id=project.id),
            {"service_client": None},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.data
        assert "SERVICE_CLIENT_CANNOT_BE_UNSET_WITH_SERVICE_LOGS" in str(response.data)

        project.refresh_from_db()
        assert project.service_client_id == marubeni.id

    def test_moving_to_another_client_is_refused_when_the_project_has_work_logs(
        self, session_client, workspace, marubeni, terlogs, create_user, hour_type, billing_type
    ):
        """Not in the criterion's wording, but it breaks the same invariant.

        Every existing log was recorded against Marubeni's contract; reassigning the
        project would reattribute already-billed hours to Terlogs.
        """
        project = _make_project(workspace, "Marubeni Support", "MSUP", marubeni)
        _log_time(project, create_user, hour_type, billing_type)

        response = session_client.patch(
            PROJECT_DETAIL_URL.format(slug=workspace.slug, project_id=project.id),
            {"service_client": str(terlogs.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.data
        assert "SERVICE_CLIENT_CANNOT_BE_CHANGED_WITH_SERVICE_LOGS" in str(response.data)

    def test_clearing_the_client_is_allowed_without_work_logs(
        self, session_client, workspace, marubeni
    ):
        """The guard must not make an ordinary correction impossible."""
        project = _make_project(workspace, "Marubeni Support", "MSUP", marubeni)

        response = session_client.patch(
            PROJECT_DETAIL_URL.format(slug=workspace.slug, project_id=project.id),
            {"service_client": None},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        project.refresh_from_db()
        assert project.service_client_id is None

    def test_attaching_a_client_to_a_project_that_already_has_work_logs_is_allowed(
        self, session_client, workspace, marubeni, create_user, hour_type, billing_type
    ):
        """Adopting previously unattributed work takes nothing away from anyone."""
        project = _make_project(workspace, "Legacy", "LEG", None)
        _log_time(project, create_user, hour_type, billing_type)

        response = session_client.patch(
            PROJECT_DETAIL_URL.format(slug=workspace.slug, project_id=project.id),
            {"service_client": str(marubeni.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        project.refresh_from_db()
        assert project.service_client_id == marubeni.id

    def test_an_unrelated_patch_is_unaffected(
        self, session_client, workspace, marubeni, create_user, hour_type, billing_type
    ):
        """DRF skips the field validator when the key is absent, and must keep doing so."""
        project = _make_project(workspace, "Marubeni Support", "MSUP", marubeni)
        _log_time(project, create_user, hour_type, billing_type)

        response = session_client.patch(
            PROJECT_DETAIL_URL.format(slug=workspace.slug, project_id=project.id),
            {"description": "Support desk for Marubeni"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data


@pytest.mark.contract
@pytest.mark.django_db
class TestClientLinkCannotBeBrokenByBulkAssign:
    """Phase 1, criterion 11 -- the write path the phase brief did not know about.

    ``assign-projects`` writes ``service_client_id`` through a queryset ``update()``,
    which never reaches a serializer. Without its own guard, this endpoint would
    reassign a project with work logs while the PATCH refused to.
    """

    def test_reassigning_a_project_with_work_logs_is_refused(
        self, session_client, workspace, marubeni, terlogs, create_user, hour_type, billing_type
    ):
        project = _make_project(workspace, "Marubeni Support", "MSUP", marubeni)
        _log_time(project, create_user, hour_type, billing_type)

        response = session_client.post(
            ASSIGN_URL.format(slug=workspace.slug, pk=terlogs.id),
            {"project_ids": [str(project.id)]},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.data
        assert response.data["projects"][0]["error"] == "SERVICE_CLIENT_CANNOT_BE_CHANGED_WITH_SERVICE_LOGS"

        project.refresh_from_db()
        assert project.service_client_id == marubeni.id

    def test_assigning_an_unattributed_project_still_works(
        self, session_client, workspace, marubeni, create_user, hour_type, billing_type
    ):
        """The endpoint exists to attribute legacy projects, and must keep doing it."""
        project = _make_project(workspace, "Legacy", "LEG", None)
        _log_time(project, create_user, hour_type, billing_type)

        response = session_client.post(
            ASSIGN_URL.format(slug=workspace.slug, pk=marubeni.id),
            {"project_ids": [str(project.id)]},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        project.refresh_from_db()
        assert project.service_client_id == marubeni.id

    def test_one_blocked_project_blocks_the_whole_batch(
        self, session_client, workspace, marubeni, terlogs, create_user, hour_type, billing_type
    ):
        """All or nothing, and the response names which project refused.

        A partial success would leave the admin unable to tell what happened, and the
        underlying write is a single queryset update anyway.
        """
        blocked = _make_project(workspace, "Marubeni Support", "MSUP", marubeni)
        _log_time(blocked, create_user, hour_type, billing_type)
        allowed = _make_project(workspace, "Legacy", "LEG", None)

        response = session_client.post(
            ASSIGN_URL.format(slug=workspace.slug, pk=terlogs.id),
            {"project_ids": [str(blocked.id), str(allowed.id)]},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert [entry["project_id"] for entry in response.data["projects"]] == [str(blocked.id)]

        allowed.refresh_from_db()
        assert allowed.service_client_id is None


@pytest.mark.contract
@pytest.mark.django_db
class TestCatalogOptionInUse:
    """Phase 2, criterion 4 -- "excluir um Tipo de Hora em uso retorna erro explicativo"."""

    def test_deleting_an_hour_type_in_use_is_refused(
        self, session_client, workspace, marubeni, create_user, hour_type, billing_type
    ):
        project = _make_project(workspace, "Marubeni Support", "MSUP", marubeni)
        _log_time(project, create_user, hour_type, billing_type)

        response = session_client.delete(
            HOUR_TYPE_DETAIL_URL.format(slug=workspace.slug, pk=hour_type.id)
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.data
        assert response.data["error"] == "HOUR_TYPE_IN_USE_BY_SERVICE_LOGS"
        assert ServiceHourType.objects.filter(pk=hour_type.id).exists()

    def test_deleting_a_billing_type_in_use_is_refused(
        self, session_client, workspace, marubeni, create_user, hour_type, billing_type
    ):
        project = _make_project(workspace, "Marubeni Support", "MSUP", marubeni)
        _log_time(project, create_user, hour_type, billing_type)

        response = session_client.delete(
            BILLING_TYPE_DETAIL_URL.format(slug=workspace.slug, pk=billing_type.id)
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.data
        assert response.data["error"] == "BILLING_TYPE_IN_USE_BY_SERVICE_LOGS"

    def test_an_unused_hour_type_can_still_be_deleted(self, session_client, workspace, hour_type):
        """The guard must not turn into "catalogue options can never be removed"."""
        unused = ServiceHourType.objects.create(
            workspace=workspace, name="Plantão", multiplier=Decimal("2.50")
        )

        response = session_client.delete(
            HOUR_TYPE_DETAIL_URL.format(slug=workspace.slug, pk=unused.id)
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT, getattr(response, "data", None)

    def test_a_soft_deleted_work_log_still_blocks_the_delete(
        self, session_client, workspace, marubeni, create_user, hour_type, billing_type
    ):
        """Counted with ``all_objects``, matching how the client entity counts.

        The database foreign key does not care that the log is flagged deleted, so a
        hard delete of the option would still fail -- and the log may be restored.
        """
        project = _make_project(workspace, "Marubeni Support", "MSUP", marubeni)
        log = _log_time(project, create_user, hour_type, billing_type)
        log.delete()

        response = session_client.delete(
            HOUR_TYPE_DETAIL_URL.format(slug=workspace.slug, pk=hour_type.id)
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] == "HOUR_TYPE_IN_USE_BY_SERVICE_LOGS"

    def test_an_hour_type_used_only_as_a_suggestion_blocks_the_delete(
        self, session_client, workspace, marubeni, create_user, hour_type, billing_type, default_hour_type
    ):
        """A row whose ``suggested_hour_type`` points here would break identically.

        The technician overrode "Fora do expediente" with "Horário comercial", so the
        log's ``hour_type`` is the latter and only its ``suggested_hour_type`` refers to
        the option being deleted.
        """
        project = _make_project(workspace, "Marubeni Support", "MSUP", marubeni)
        _log_time(
            project,
            create_user,
            default_hour_type,
            billing_type,
            applied_multiplier=default_hour_type.multiplier,
            equivalent_hours=Decimal("1.0000"),
            debited_hours=Decimal("1.0000"),
            suggested_hour_type=hour_type,
            is_hour_type_overridden=True,
        )

        response = session_client.delete(
            HOUR_TYPE_DETAIL_URL.format(slug=workspace.slug, pk=hour_type.id)
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] == "HOUR_TYPE_IN_USE_BY_SERVICE_LOGS"


@pytest.mark.contract
@pytest.mark.django_db
class TestDeactivatedOptionKeepsItsHistoricalName:
    """Phase 2, criterion 3 -- and the reason the foreign keys are DO_NOTHING."""

    def test_deactivating_an_hour_type_hides_it_from_new_logs_but_not_from_old_ones(
        self, session_client, workspace, marubeni, create_user, hour_type, billing_type
    ):
        project = _make_project(workspace, "Marubeni Support", "MSUP", marubeni)
        log = _log_time(project, create_user, hour_type, billing_type)

        response = session_client.patch(
            HOUR_TYPE_DETAIL_URL.format(slug=workspace.slug, pk=hour_type.id),
            {"is_active": False},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.data

        # Gone from the form's list...
        listing = session_client.get(
            "/api/workspaces/{slug}/service-hour-types/?only_active=true".format(slug=workspace.slug)
        )
        assert all(option["id"] != str(hour_type.id) for option in listing.data)

        # ...but the existing log still renders its label and its snapshot.
        log.refresh_from_db()
        assert log.hour_type.name == "Fora do expediente"
        assert log.applied_multiplier == Decimal("1.50")
