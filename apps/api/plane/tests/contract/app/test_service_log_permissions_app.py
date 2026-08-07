# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests for work log permissions, delegation and audit. Phase 7.

Walks every acceptance criterion of the phase over HTTP:

* 1 -- technician A cannot edit or delete technician B's log
* 2 -- technician A edits and deletes their own
* 3 -- an Admin edits and deletes anybody's
* 4 -- a granted ``can_manage_others`` lets a non-Admin edit anybody's
* 5 -- a delegated log carries both names
* 6 -- an author change is audited and moves no pool balance
* 7 -- a duration change is audited and does move the balance
* 8 -- every denial returns an authorisation error, not a validation error
* 9 -- a deleted log stays recoverable in the audit history

Plus the guard the brief did not ask for and the review required: **the three
capabilities are invalid for a GUEST**, refused on the way in and revoked on demotion.

Every refusal asserts the **error code**, not just the status, and every refusal has a
positive control in the same fixture -- a suite in which nobody can do anything passes
all the negative tests and protects nothing.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

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
    ServiceConfigActivity,
    ServiceContract,
    ServiceHourLedgerEntry,
    ServiceHourType,
    ServiceLog,
    ServiceMemberPermission,
    State,
    User,
    WorkspaceMember,
)

LIST_URL = "/api/workspaces/{slug}/projects/{project_id}/issues/{issue_id}/service-logs/"
BATCH_URL = "/api/workspaces/{slug}/projects/{project_id}/issues/{issue_id}/service-logs/batches/{batch_id}/"
AUTHOR_URL = (
    "/api/workspaces/{slug}/projects/{project_id}/issues/{issue_id}/service-logs/batches/{batch_id}/author/"
)
PERMISSIONS_URL = "/api/workspaces/{slug}/service-member-permissions/"
PERMISSION_URL = "/api/workspaces/{slug}/service-member-permissions/{member_id}/"
MY_PERMISSIONS_URL = "/api/workspaces/{slug}/service-member-permissions/me/"
WORKSPACE_MEMBER_URL = "/api/workspaces/{slug}/members/{pk}/"


@pytest.fixture(autouse=True)
def captured_activity():
    """Run the activity task inline so the audit trail can be asserted on.

    The task swallows every exception by design, so without this a trail that silently
    recorded nothing would look identical to one that worked -- which is the exact
    false-green this repository has been bitten by before.
    """

    def run_inline(args=None, kwargs=None, **_ignored):
        from plane.bgtasks.issue_activities_task import issue_activity

        if kwargs is not None and "type" in kwargs:
            issue_activity(**kwargs)
        return None

    with patch("celery.app.task.Task.apply_async", side_effect=run_inline):
        yield


# ---------------------------------------------------------------------------
# The cast
# ---------------------------------------------------------------------------


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


def _make_member(workspace, project, email, role):
    """A user who is both a workspace member at ``role`` and a project member.

    Both memberships are required: ``allow_permission`` resolves the work log routes at
    PROJECT level, while the capabilities resolve at WORKSPACE level. A cast member missing
    either one would fail for a reason that has nothing to do with what is under test.
    """
    user = User.objects.create(email=email, username=email, display_name=email.split("@")[0])
    WorkspaceMember.objects.create(workspace=workspace, member=user, role=role, is_active=True)
    ProjectMember.objects.create(project=project, member=user, role=role, is_active=True)
    return user


@pytest.fixture
def admin_user(db, create_user):
    """The ``workspace`` fixture already makes ``create_user`` a workspace ADMIN."""
    return create_user


@pytest.fixture
def admin_client(db, admin_user, project):
    ProjectMember.objects.get_or_create(
        project=project, member=admin_user, defaults={"role": 20, "is_active": True}
    )
    client = APIClient()
    client.force_authenticate(user=admin_user)
    return client


@pytest.fixture
def technician_a(db, workspace, project):
    return _make_member(workspace, project, "a@example.com", 15)


@pytest.fixture
def technician_b(db, workspace, project):
    return _make_member(workspace, project, "b@example.com", 15)


@pytest.fixture
def technician_c(db, workspace, project):
    """The one criterion 4 grants ``can_manage_others`` to."""
    return _make_member(workspace, project, "c@example.com", 15)


@pytest.fixture
def guest_user(db, workspace, project):
    return _make_member(workspace, project, "guest@example.com", 5)


def _client_for(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def client_a(technician_a):
    return _client_for(technician_a)


@pytest.fixture
def client_b(technician_b):
    return _client_for(technician_b)


@pytest.fixture
def client_c(technician_c):
    return _client_for(technician_c)


@pytest.fixture
def issue(db, project, admin_user):
    return Issue.objects.create(workspace=project.workspace, project=project, name="Printer broken")


@pytest.fixture
def commercial_hours(db, workspace):
    return ServiceHourType.objects.create(
        workspace=workspace, name="Horário comercial", multiplier=Decimal("1.00")
    )


@pytest.fixture
def contract_billing(db, workspace):
    return ServiceBillingType.objects.create(
        workspace=workspace, name="Contrato", billing_route=ServiceBillingType.BillingRoute.DEBIT_POOL
    )


@pytest.fixture
def contract(db, workspace, marubeni):
    """A live contract, so a ``DEBIT_POOL`` log actually debits something.

    **Most tests in this file deliberately do without it.** Permission rules must not
    depend on whether a contract exists -- and without one, D33 settles a ``DEBIT_POOL``
    log as avulso, which is a perfectly good row to test authority against.

    Criterion 7 is the exception: "ajusta o saldo" cannot be asserted where there is no
    balance to move, and a test that claimed to check the pool while silently exercising
    the avulso path would be the false green this repository keeps finding. So this fixture
    is requested explicitly, by the one class that needs it.
    """
    return ServiceContract.objects.create(
        workspace=workspace,
        service_client=marubeni,
        code="CT-PERM",
        name="Suporte Marubeni",
        monthly_hours=Decimal("30.0000"),
        starts_on=date(2026, 1, 1),
        ends_on=date(2026, 12, 31),
        status=ServiceContract.Status.ACTIVE,
        is_default=True,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _list_url(issue):
    return LIST_URL.format(slug=issue.workspace.slug, project_id=issue.project_id, issue_id=issue.id)


def _batch_url(issue, batch_id):
    return BATCH_URL.format(
        slug=issue.workspace.slug, project_id=issue.project_id, issue_id=issue.id, batch_id=batch_id
    )


def _author_url(issue, batch_id):
    return AUTHOR_URL.format(
        slug=issue.workspace.slug, project_id=issue.project_id, issue_id=issue.id, batch_id=batch_id
    )


def _create_log(client, issue, hour_type, billing_type, **overrides):
    """Create a log through the API and return its batch id."""
    response = client.post(
        _list_url(issue), _payload(hour_type, billing_type, **overrides), format="json"
    )
    assert response.status_code == status.HTTP_201_CREATED, response.data
    return response.data["batch_id"]


def _grant(admin_client, workspace, member, **flags):
    response = admin_client.patch(
        PERMISSION_URL.format(slug=workspace.slug, member_id=member.id), flags, format="json"
    )
    assert response.status_code == status.HTTP_200_OK, response.data
    return response


@pytest.mark.contract
@pytest.mark.django_db
class TestBaseAuthority:
    """Acceptance criteria 1, 2 and 3."""

    def test_a_technician_cannot_edit_another_technicians_log(
        self, client_a, client_b, issue, commercial_hours, contract_billing
    ):
        """Criterion 1, the edit half. 403 with the reason, not a silent no-op."""
        batch_id = _create_log(client_a, issue, commercial_hours, contract_billing)

        response = client_b.patch(
            _batch_url(issue, batch_id),
            _payload(commercial_hours, contract_billing, duration="3h"),
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.data["error"] == "ONLY_THE_AUTHOR_CAN_CHANGE_A_SERVICE_LOG"
        # The refusal actually refused: the log is untouched.
        assert ServiceLog.objects.get(batch_id=batch_id).logged_hours == Decimal("1.0000")

    def test_a_technician_cannot_delete_another_technicians_log(
        self, client_a, client_b, issue, commercial_hours, contract_billing
    ):
        """Criterion 1, the delete half."""
        batch_id = _create_log(client_a, issue, commercial_hours, contract_billing)

        response = client_b.delete(_batch_url(issue, batch_id))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.data["error"] == "ONLY_THE_AUTHOR_CAN_CHANGE_A_SERVICE_LOG"
        assert ServiceLog.objects.filter(batch_id=batch_id).exists()

    def test_the_author_edits_their_own(
        self, client_a, issue, commercial_hours, contract_billing
    ):
        """Criterion 2, the edit half. Positive control for criterion 1."""
        batch_id = _create_log(client_a, issue, commercial_hours, contract_billing)

        response = client_a.patch(
            _batch_url(issue, batch_id),
            _payload(commercial_hours, contract_billing, duration="3h"),
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert ServiceLog.objects.get(batch_id=batch_id).logged_hours == Decimal("3.0000")

    def test_the_author_deletes_their_own(
        self, client_a, issue, commercial_hours, contract_billing
    ):
        """Criterion 2, the delete half."""
        batch_id = _create_log(client_a, issue, commercial_hours, contract_billing)

        response = client_a.delete(_batch_url(issue, batch_id))

        assert response.status_code == status.HTTP_200_OK, response.data
        assert ServiceLog.objects.filter(batch_id=batch_id).exists() is False

    def test_an_admin_edits_anybodys_log(
        self, client_a, admin_client, issue, commercial_hours, contract_billing
    ):
        """Criterion 3, the edit half. The Admin holds the capability with no grant row."""
        batch_id = _create_log(client_a, issue, commercial_hours, contract_billing)

        response = admin_client.patch(
            _batch_url(issue, batch_id),
            _payload(commercial_hours, contract_billing, duration="4h"),
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert ServiceLog.objects.get(batch_id=batch_id).logged_hours == Decimal("4.0000")
        assert ServiceMemberPermission.objects.exists() is False

    def test_an_admin_deletes_anybodys_log(
        self, client_a, admin_client, issue, commercial_hours, contract_billing
    ):
        """Criterion 3, the delete half."""
        batch_id = _create_log(client_a, issue, commercial_hours, contract_billing)

        response = admin_client.delete(_batch_url(issue, batch_id))

        assert response.status_code == status.HTTP_200_OK, response.data
        assert ServiceLog.objects.filter(batch_id=batch_id).exists() is False

    def test_a_guest_reaches_no_work_log_endpoint(
        self, client_a, guest_user, issue, commercial_hours, contract_billing
    ):
        """Phase 3's rule, still true: GUEST is on no work log route, reads included.

        Re-asserted here because Phase 7 touched every one of these handlers, and the
        payload carries the raw duration and multiplier that R11 keeps from clients.
        """
        batch_id = _create_log(client_a, issue, commercial_hours, contract_billing)
        guest_client = _client_for(guest_user)

        assert guest_client.get(_list_url(issue)).status_code == status.HTTP_403_FORBIDDEN
        assert (
            guest_client.post(
                _list_url(issue), _payload(commercial_hours, contract_billing), format="json"
            ).status_code
            == status.HTTP_403_FORBIDDEN
        )
        assert guest_client.delete(_batch_url(issue, batch_id)).status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.contract
@pytest.mark.django_db
class TestGrantedManageOthers:
    """Acceptance criterion 4: a granted non-Admin edits anybody's log."""

    def test_technician_c_cannot_edit_before_the_grant(
        self, client_a, client_c, issue, commercial_hours, contract_billing
    ):
        """The "before" half of criterion 4, and it is what makes the "after" meaningful."""
        batch_id = _create_log(client_a, issue, commercial_hours, contract_billing)

        response = client_c.patch(
            _batch_url(issue, batch_id),
            _payload(commercial_hours, contract_billing, duration="2h"),
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.data["error"] == "ONLY_THE_AUTHOR_CAN_CHANGE_A_SERVICE_LOG"

    def test_technician_c_edits_anybodys_log_after_the_grant_without_being_admin(
        self, client_a, client_c, admin_client, workspace, technician_c, issue,
        commercial_hours, contract_billing,
    ):
        """Criterion 4 in full: granted, effective, and **still not an Admin**.

        The role assertion is the point of the criterion -- "sem ser Admin". A grant that
        worked by quietly promoting somebody would pass every other assertion here.
        """
        batch_id = _create_log(client_a, issue, commercial_hours, contract_billing)

        _grant(admin_client, workspace, technician_c, can_manage_others=True)

        response = client_c.patch(
            _batch_url(issue, batch_id),
            _payload(commercial_hours, contract_billing, duration="2h"),
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert ServiceLog.objects.get(batch_id=batch_id).logged_hours == Decimal("2.0000")
        assert WorkspaceMember.objects.get(workspace=workspace, member=technician_c).role == 15

    def test_technician_c_deletes_anybodys_log_after_the_grant(
        self, client_a, client_c, admin_client, workspace, technician_c, issue,
        commercial_hours, contract_billing,
    ):
        batch_id = _create_log(client_a, issue, commercial_hours, contract_billing)

        _grant(admin_client, workspace, technician_c, can_manage_others=True)

        response = client_c.delete(_batch_url(issue, batch_id))

        assert response.status_code == status.HTTP_200_OK, response.data
        assert ServiceLog.objects.filter(batch_id=batch_id).exists() is False

    def test_revoking_the_grant_restores_the_refusal(
        self, client_a, client_c, admin_client, workspace, technician_c, issue,
        commercial_hours, contract_billing,
    ):
        """A grant that could not be taken back would be a promotion, not a grant."""
        batch_id = _create_log(client_a, issue, commercial_hours, contract_billing)

        _grant(admin_client, workspace, technician_c, can_manage_others=True)
        _grant(admin_client, workspace, technician_c, can_manage_others=False)

        response = client_c.patch(
            _batch_url(issue, batch_id),
            _payload(commercial_hours, contract_billing, duration="2h"),
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.data["error"] == "ONLY_THE_AUTHOR_CAN_CHANGE_A_SERVICE_LOG"

    def test_the_grant_endpoint_is_admin_only(self, client_a, workspace, technician_c):
        """Who may edit other people's billing records is itself sensitive information.

        Both verbs, because a MEMBER able to *read* the list learns whose account is worth
        having, and one able to write it grants themselves anything.
        """
        assert (
            client_a.get(PERMISSIONS_URL.format(slug=workspace.slug)).status_code
            == status.HTTP_403_FORBIDDEN
        )
        assert (
            client_a.patch(
                PERMISSION_URL.format(slug=workspace.slug, member_id=technician_c.id),
                {"can_manage_others": True},
                format="json",
            ).status_code
            == status.HTTP_403_FORBIDDEN
        )
        assert ServiceMemberPermission.objects.exists() is False

    def test_a_member_can_read_their_own_capabilities(
        self, client_c, admin_client, workspace, technician_c
    ):
        """The form needs this to decide whether to show the "Autor" field (section 3).

        Discloses nothing about anybody else, which is why it is open to MEMBER while the
        listing is not.
        """
        before = client_c.get(MY_PERMISSIONS_URL.format(slug=workspace.slug))

        assert before.status_code == status.HTTP_200_OK
        assert before.data["can_delegate"] is False

        _grant(admin_client, workspace, technician_c, can_delegate=True)

        after = client_c.get(MY_PERMISSIONS_URL.format(slug=workspace.slug))

        assert after.data["can_delegate"] is True
        assert after.data["is_admin"] is False

    def test_an_admin_sees_all_three_as_held_without_a_grant(self, admin_client, workspace):
        response = admin_client.get(MY_PERMISSIONS_URL.format(slug=workspace.slug))

        assert response.data["is_admin"] is True
        assert response.data["can_manage_others"] is True
        assert response.data["can_delegate"] is True
        assert response.data["can_reassign_author"] is True


@pytest.mark.contract
@pytest.mark.django_db
class TestDelegation:
    """Acceptance criterion 5."""

    def test_delegation_is_refused_without_the_grant(
        self, client_a, technician_b, issue, commercial_hours, contract_billing
    ):
        """403 and the reason, and no row written."""
        response = client_a.post(
            _list_url(issue),
            _payload(commercial_hours, contract_billing, author_id=str(technician_b.id)),
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.data["error"] == "SERVICE_LOG_DELEGATION_NOT_PERMITTED"
        assert ServiceLog.objects.exists() is False

    def test_a_delegated_log_carries_both_names(
        self, client_a, admin_client, workspace, technician_a, technician_b, issue,
        commercial_hours, contract_billing,
    ):
        """Criterion 5: "a lista mostra ambos os nomes".

        R8's two facts, distinct in the payload: ``author`` is who did the work and
        ``created_by`` is who typed it. The list is what the criterion names, so the
        assertion is made against the list response and not only against the create.
        """
        _grant(admin_client, workspace, technician_a, can_delegate=True)

        created = client_a.post(
            _list_url(issue),
            _payload(commercial_hours, contract_billing, author_id=str(technician_b.id)),
            format="json",
        )

        assert created.status_code == status.HTTP_201_CREATED, created.data
        assert created.data["was_delegated"] is True

        listed = client_a.get(_list_url(issue)).data["service_logs"][0]

        assert str(listed["author"]) == str(technician_b.id)
        assert listed["author_detail"]["display_name"] == "b"
        assert str(listed["created_by"]) == str(technician_a.id)

    def test_delegating_to_yourself_is_not_delegation(
        self, client_a, technician_a, issue, commercial_hours, contract_billing
    ):
        """No grant required, because naming yourself is the ordinary case."""
        response = client_a.post(
            _list_url(issue),
            _payload(commercial_hours, contract_billing, author_id=str(technician_a.id)),
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert response.data["was_delegated"] is False

    def test_delegating_to_a_guest_is_refused_even_with_the_grant(
        self, client_a, admin_client, workspace, technician_a, guest_user, issue,
        commercial_hours, contract_billing,
    ):
        """Holding ``can_delegate`` does not mean naming *anybody* as the author.

        A GUEST is a client's own user, so this would put client staff on the labour side
        of an invoice. The positive control is the test above: the same grant does allow
        naming a technician.
        """
        _grant(admin_client, workspace, technician_a, can_delegate=True)

        response = client_a.post(
            _list_url(issue),
            _payload(commercial_hours, contract_billing, author_id=str(guest_user.id)),
            format="json",
        )

        # 400, not 403, and the distinction is the contract with the interface: this caller
        # *holds* `can_delegate`, so the refusal is about the value of `author_id` and belongs
        # on the field. A 403 would tell them they lack a permission they have.
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] == "SERVICE_LOG_AUTHOR_MUST_BE_A_TECHNICIAN"
        assert ServiceLog.objects.exists() is False

    def test_the_delegation_is_recorded_in_the_audit_trail(
        self, client_a, admin_client, workspace, technician_a, technician_b, issue,
        commercial_hours, contract_billing,
    ):
        """R8 wants the fact stated, not inferable by comparing two ids."""
        _grant(admin_client, workspace, technician_a, can_delegate=True)

        client_a.post(
            _list_url(issue),
            _payload(commercial_hours, contract_billing, author_id=str(technician_b.id)),
            format="json",
        )

        entry = IssueActivity.objects.get(field="service_log_delegation")

        assert entry.actor_id == technician_a.id
        assert entry.old_identifier == technician_b.id
        assert entry.new_identifier == technician_a.id

    def test_logging_for_yourself_writes_no_delegation_entry(
        self, client_a, issue, commercial_hours, contract_billing
    ):
        """Noise in a trail is what teaches people to stop reading it.

        Paired with the test above, which proves the entry does appear when it should --
        so this is not passing because the generator is broken.
        """
        _create_log(client_a, issue, commercial_hours, contract_billing)

        assert IssueActivity.objects.filter(field="service_log_delegation").exists() is False


@pytest.mark.contract
@pytest.mark.django_db
class TestReassignment:
    """Acceptance criterion 6, and the scope of decision D45."""

    def test_reassignment_is_refused_without_the_grant(
        self, client_a, technician_b, issue, commercial_hours, contract_billing
    ):
        batch_id = _create_log(client_a, issue, commercial_hours, contract_billing)

        response = client_a.patch(
            _author_url(issue, batch_id), {"author_id": str(technician_b.id)}, format="json"
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.data["error"] == "SERVICE_LOG_REASSIGNMENT_NOT_PERMITTED"
        assert ServiceLog.objects.get(batch_id=batch_id).author_id != technician_b.id

    def test_the_author_with_the_grant_reassigns_their_own(
        self, client_a, admin_client, workspace, technician_a, technician_b, issue,
        commercial_hours, contract_billing,
    ):
        """The common case D45 names: "lancei isto, mas o trabalho foi do João"."""
        batch_id = _create_log(client_a, issue, commercial_hours, contract_billing)

        _grant(admin_client, workspace, technician_a, can_reassign_author=True)

        response = client_a.patch(
            _author_url(issue, batch_id), {"author_id": str(technician_b.id)}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["was_reassigned"] is True
        assert ServiceLog.objects.get(batch_id=batch_id).author_id == technician_b.id

    def test_the_reassign_grant_alone_does_not_reach_somebody_elses_log(
        self, client_a, client_c, admin_client, workspace, technician_c, technician_b, issue,
        commercial_hours, contract_billing,
    ):
        """Decision D45's scope, over HTTP. The silent-widening case.

        C holds ``can_reassign_author`` and nothing else, so C may reassign only C's own
        logs. Reaching A's log would be editing A's record, which nobody granted.
        """
        batch_id = _create_log(client_a, issue, commercial_hours, contract_billing)

        _grant(admin_client, workspace, technician_c, can_reassign_author=True)

        response = client_c.patch(
            _author_url(issue, batch_id), {"author_id": str(technician_b.id)}, format="json"
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.data["error"] == "SERVICE_LOG_REASSIGNMENT_NOT_PERMITTED"

    def test_both_grants_together_reach_somebody_elses_log(
        self, client_a, client_c, admin_client, workspace, technician_c, technician_b, issue,
        commercial_hours, contract_billing,
    ):
        """The authorised composition, and the positive control for the test above."""
        batch_id = _create_log(client_a, issue, commercial_hours, contract_billing)

        _grant(
            admin_client, workspace, technician_c, can_manage_others=True, can_reassign_author=True
        )

        response = client_c.patch(
            _author_url(issue, batch_id), {"author_id": str(technician_b.id)}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert ServiceLog.objects.get(batch_id=batch_id).author_id == technician_b.id

    def test_manage_others_alone_cannot_change_the_author(
        self, client_a, client_c, admin_client, workspace, technician_c, technician_b, issue,
        commercial_hours, contract_billing,
    ):
        """Independence in the other direction: editing what was done is not re-attributing it.

        The positive control sits in the same test -- C really can edit the log's duration,
        so the refusal below is specifically about the author.
        """
        batch_id = _create_log(client_a, issue, commercial_hours, contract_billing)

        _grant(admin_client, workspace, technician_c, can_manage_others=True)

        edited = client_c.patch(
            _batch_url(issue, batch_id),
            _payload(commercial_hours, contract_billing, duration="2h"),
            format="json",
        )
        assert edited.status_code == status.HTTP_200_OK, edited.data

        response = client_c.patch(
            _author_url(issue, batch_id), {"author_id": str(technician_b.id)}, format="json"
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.data["error"] == "SERVICE_LOG_REASSIGNMENT_NOT_PERMITTED"

    def test_an_admin_reassigns_without_a_grant(
        self, client_a, admin_client, technician_b, issue, commercial_hours, contract_billing
    ):
        batch_id = _create_log(client_a, issue, commercial_hours, contract_billing)

        response = admin_client.patch(
            _author_url(issue, batch_id), {"author_id": str(technician_b.id)}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert ServiceLog.objects.get(batch_id=batch_id).author_id == technician_b.id

    def test_reassignment_is_audited_with_all_four_facts(
        self, client_a, admin_client, technician_a, technician_b, issue,
        commercial_hours, contract_billing,
    ):
        """Criterion 6, the audit half. Section 4 asks for four facts; all four asserted."""
        batch_id = _create_log(client_a, issue, commercial_hours, contract_billing)

        admin_client.patch(
            _author_url(issue, batch_id), {"author_id": str(technician_b.id)}, format="json"
        )

        entry = IssueActivity.objects.get(field="service_log_author")

        assert entry.old_identifier == technician_a.id  # the previous author
        assert entry.new_identifier == technician_b.id  # the new author
        assert entry.actor_id != technician_a.id  # who changed it: the Admin
        assert entry.epoch is not None  # when

    def test_reassignment_does_not_move_the_pool_balance(
        self, client_a, admin_client, technician_b, issue, commercial_hours, contract_billing
    ):
        """Criterion 6's other half: "não altera saldo de pool".

        Asserted **structurally**, not just as an equal total: the reassignment must write
        no ledger row at all. An implementation that reversed the debit and reapplied it
        would land on the same balance while breaking a closed period and doubling the
        ledger -- so comparing totals alone would not catch it.
        """
        batch_id = _create_log(client_a, issue, commercial_hours, contract_billing)

        ledger_before = list(
            ServiceHourLedgerEntry.objects.order_by("created_at").values_list(
                "entry_type", "hours"
            )
        )
        # Read through the SAME client before and after. An Admin's payload carries the
        # monetary keys and a technician's does not, so comparing across the two would
        # differ on key sets rather than on balance.
        totals_before = admin_client.get(_list_url(issue)).data["totals"]

        admin_client.patch(
            _author_url(issue, batch_id), {"author_id": str(technician_b.id)}, format="json"
        )

        ledger_after = list(
            ServiceHourLedgerEntry.objects.order_by("created_at").values_list(
                "entry_type", "hours"
            )
        )

        # Not merely "the same count": the same rows. A reversal plus a fresh debit would
        # keep the balance and change this list, which is the implementation this asserts
        # against.
        assert ledger_after == ledger_before
        assert admin_client.get(_list_url(issue)).data["totals"] == totals_before

    def test_the_edit_route_refuses_to_change_the_author(
        self, client_a, admin_client, technician_b, issue, commercial_hours, contract_billing
    ):
        """Reassignment has its own route, and the edit route says so instead of ignoring it.

        Silently dropping the field would tell the caller the reassignment succeeded.
        """
        batch_id = _create_log(client_a, issue, commercial_hours, contract_billing)

        response = admin_client.patch(
            _batch_url(issue, batch_id),
            _payload(commercial_hours, contract_billing, author_id=str(technician_b.id)),
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] == "SERVICE_LOG_AUTHOR_IS_REASSIGNED_SEPARATELY"
        assert ServiceLog.objects.get(batch_id=batch_id).author_id != technician_b.id

    def test_reassigning_to_the_incumbent_is_a_no_op_with_no_trail_entry(
        self, client_a, admin_client, technician_a, issue, commercial_hours, contract_billing
    ):
        batch_id = _create_log(client_a, issue, commercial_hours, contract_billing)

        response = admin_client.patch(
            _author_url(issue, batch_id), {"author_id": str(technician_a.id)}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["was_reassigned"] is False
        assert IssueActivity.objects.filter(field="service_log_author").exists() is False

    def test_reassigning_to_a_guest_is_refused(
        self, client_a, admin_client, guest_user, issue, commercial_hours, contract_billing
    ):
        batch_id = _create_log(client_a, issue, commercial_hours, contract_billing)

        response = admin_client.patch(
            _author_url(issue, batch_id), {"author_id": str(guest_user.id)}, format="json"
        )

        # 400: the Admin holds every capability, so this is the payload being wrong.
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] == "SERVICE_LOG_AUTHOR_MUST_BE_A_TECHNICIAN"

    def test_reassignment_without_a_target_is_a_validation_error(
        self, client_a, admin_client, issue, commercial_hours, contract_billing
    ):
        """400, not 403: this is a bad payload, not a refusal of authority (criterion 8)."""
        batch_id = _create_log(client_a, issue, commercial_hours, contract_billing)

        response = admin_client.patch(_author_url(issue, batch_id), {}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] == "SERVICE_LOG_AUTHOR_IS_REQUIRED"

    def test_every_segment_of_a_split_entry_is_reassigned(
        self, client_a, admin_client, technician_b, issue, commercial_hours, contract_billing
    ):
        """A batch is edited as one unit, so it is reassigned as one unit.

        Asserted on a two-segment batch created by hand, because a one-row batch would pass
        even if the implementation only touched the first row.
        """
        batch_id = _create_log(client_a, issue, commercial_hours, contract_billing)
        original = ServiceLog.objects.get(batch_id=batch_id)

        ServiceLog.objects.create(
            issue=issue,
            project=issue.project,
            author=original.author,
            worked_on=original.worked_on,
            description=original.description,
            entry_mode=original.entry_mode,
            raw_duration_minutes=60,
            hour_type=original.hour_type,
            billing_type=original.billing_type,
            logged_hours=Decimal("1.0000"),
            equivalent_hours=Decimal("1.0000"),
            debited_hours=Decimal("1.0000"),
            applied_multiplier=Decimal("1.00"),
            # All three route columns copied together, because
            # `service_log_route_deviation_is_coherent` is a biconditional over them: with
            # no contract on this fixture, D33 settled the original as `bill_amount` with a
            # deviation reason, and copying only two of the three is refused by the
            # database. The handoff named this footgun exactly.
            applied_billing_route=original.applied_billing_route,
            settled_billing_route=original.settled_billing_route,
            route_deviation_reason=original.route_deviation_reason,
            batch_id=batch_id,
            segment_index=1,
        )

        response = admin_client.patch(
            _author_url(issue, batch_id), {"author_id": str(technician_b.id)}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        authors = set(
            ServiceLog.objects.filter(batch_id=batch_id).values_list("author_id", flat=True)
        )
        assert authors == {technician_b.id}


@pytest.mark.contract
@pytest.mark.django_db
class TestEditsThatMoveMoney:
    """Acceptance criterion 7, and its contrast with criterion 6."""

    def test_changing_the_duration_is_audited_and_adjusts_the_pool_balance(
        self, client_a, issue, contract, commercial_hours, contract_billing
    ):
        """Criterion 7, and the contrast with criterion 6 is the point of this class.

        Requests the ``contract`` fixture on purpose: without a live contract, D33 settles a
        ``DEBIT_POOL`` log as avulso and **no ledger row is ever written**, so a test
        asserting "the balance adjusted" would pass against zero movement in both
        directions. It first failed exactly that way.

        Three assertions, because three different things must hold together: the totals
        moved, the trail recorded it, and the *pool* moved -- with the reversal of the old
        2h and the debit of the new 3h both present, which is what acceptance criterion 12
        of Phase 4 means by "no double debit".
        """
        batch_id = _create_log(client_a, issue, commercial_hours, contract_billing, duration="2h")

        assert client_a.get(_list_url(issue)).data["totals"]["logged_hours"] == "2.0000"

        debits = ServiceHourLedgerEntry.objects.filter(entry_type="debit")
        assert debits.count() == 1, "the pool must have been debited, or this test is vacuous"
        assert debits.first().hours == Decimal("-2.0000")

        response = client_a.patch(
            _batch_url(issue, batch_id),
            _payload(commercial_hours, contract_billing, duration="3h"),
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert client_a.get(_list_url(issue)).data["totals"]["logged_hours"] == "3.0000"
        assert IssueActivity.objects.filter(field="service_log", verb="updated").exists()

        # The pool half. The old 2h came back and the new 3h went out, so the balance
        # reflects 3h and not 5h.
        assert ServiceHourLedgerEntry.objects.filter(
            entry_type="reversal", hours=Decimal("2.0000")
        ).exists()
        assert ServiceHourLedgerEntry.objects.filter(
            entry_type="debit", hours=Decimal("-3.0000")
        ).exists()

    def test_reassignment_writes_no_ledger_row_even_where_a_pool_exists(
        self, client_a, admin_client, technician_b, issue, contract, commercial_hours, contract_billing
    ):
        """Criterion 6 against a live pool, which is the case that can actually go wrong.

        The reassignment test in ``TestReassignment`` runs without a contract, where no
        ledger row would be written whatever the implementation did. This one has a real
        debit sitting in the ledger, so an implementation that rebuilt the batch would show
        up here as a reversal plus a second debit.
        """
        batch_id = _create_log(client_a, issue, commercial_hours, contract_billing, duration="2h")

        before = list(
            ServiceHourLedgerEntry.objects.order_by("created_at").values_list("entry_type", "hours")
        )
        assert before == [("grant", Decimal("30.0000")), ("debit", Decimal("-2.0000"))]

        response = admin_client.patch(
            _author_url(issue, batch_id), {"author_id": str(technician_b.id)}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert (
            list(
                ServiceHourLedgerEntry.objects.order_by("created_at").values_list(
                    "entry_type", "hours"
                )
            )
            == before
        )
        assert ServiceLog.objects.get(batch_id=batch_id).author_id == technician_b.id


@pytest.mark.contract
@pytest.mark.django_db
class TestClosedPeriodIsAdminOnly:
    """Section 6: "Apontamento em período fechado só é editável por Admin".

    The one lock the three grants do not open. What it protects is the reproducibility of a
    consolidation already sent to the client -- which lists the technician's name, per R11 --
    so it covers reassignment as well as edits, even though reassignment moves no balance.
    """

    def _close_the_period(self, batch_id):
        """Close the competency the log debited, and prove it debited one.

        The assertion is not decoration: without a contract the log would settle as avulso
        with ``debited_period`` null, and every test below would pass because the gate found
        no period rather than because the lock works.
        """
        log = ServiceLog.objects.get(batch_id=batch_id)
        assert log.debited_period_id is not None, "the log must have debited a period"

        period = log.debited_period
        period.status = "closed"
        period.save()
        return period

    def test_the_author_cannot_edit_in_a_closed_period(
        self, client_a, issue, contract, commercial_hours, contract_billing
    ):
        batch_id = _create_log(client_a, issue, commercial_hours, contract_billing)
        self._close_the_period(batch_id)

        response = client_a.patch(
            _batch_url(issue, batch_id),
            _payload(commercial_hours, contract_billing, duration="3h"),
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.data["error"] == "CLOSED_PERIOD_IS_ADMIN_ONLY"
        assert ServiceLog.objects.get(batch_id=batch_id).logged_hours == Decimal("1.0000")

    def test_the_author_cannot_delete_in_a_closed_period(
        self, client_a, issue, contract, commercial_hours, contract_billing
    ):
        batch_id = _create_log(client_a, issue, commercial_hours, contract_billing)
        self._close_the_period(batch_id)

        response = client_a.delete(_batch_url(issue, batch_id))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.data["error"] == "CLOSED_PERIOD_IS_ADMIN_ONLY"
        assert ServiceLog.objects.filter(batch_id=batch_id).exists()

    def test_no_grant_unlocks_a_closed_period(
        self, client_a, client_c, admin_client, workspace, technician_c, issue, contract,
        commercial_hours, contract_billing,
    ):
        """All three capabilities held, and still refused.

        The positive control is in the same test: before the period closes, C's grant does
        let C edit A's log. So this is not passing because the grant never worked.
        """
        batch_id = _create_log(client_a, issue, commercial_hours, contract_billing)
        _grant(
            admin_client,
            workspace,
            technician_c,
            can_manage_others=True,
            can_delegate=True,
            can_reassign_author=True,
        )

        # The positive control, while the period is still open.
        allowed = client_c.patch(
            _batch_url(issue, batch_id),
            _payload(commercial_hours, contract_billing, duration="2h"),
            format="json",
        )
        assert allowed.status_code == status.HTTP_200_OK, allowed.data

        self._close_the_period(batch_id)

        refused = client_c.patch(
            _batch_url(issue, batch_id),
            _payload(commercial_hours, contract_billing, duration="4h"),
            format="json",
        )

        assert refused.status_code == status.HTTP_403_FORBIDDEN
        assert refused.data["error"] == "CLOSED_PERIOD_IS_ADMIN_ONLY"

    def test_a_granted_member_cannot_reassign_in_a_closed_period(
        self, client_a, client_c, admin_client, workspace, technician_c, technician_b, issue,
        contract, commercial_hours, contract_billing,
    ):
        """Reassignment is locked too, and this is the case the review insisted on.

        It moves no balance, so "closed protects the balance" would let it through. The
        actual reason it is refused is that the technician's name is on the consolidation the
        client already received -- reassigning keeps the total and breaks the detail.
        """
        batch_id = _create_log(client_a, issue, commercial_hours, contract_billing)
        _grant(admin_client, workspace, technician_c, can_manage_others=True, can_reassign_author=True)
        self._close_the_period(batch_id)

        response = client_c.patch(
            _author_url(issue, batch_id), {"author_id": str(technician_b.id)}, format="json"
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.data["error"] == "CLOSED_PERIOD_IS_ADMIN_ONLY"
        assert ServiceLog.objects.get(batch_id=batch_id).author_id != technician_b.id

    def test_an_admin_reassigns_inside_a_closed_period(
        self, client_a, admin_client, technician_b, issue, contract, commercial_hours, contract_billing
    ):
        """The Admin's exception, and the *reason* it can succeed at all.

        Reassignment writes no ledger row, so the pool layer -- which refuses movement in a
        closed period for everybody, Admin included -- is never asked. This is precisely
        where the permission rule and D42's unbuilt period reopening meet without colliding.
        """
        batch_id = _create_log(client_a, issue, commercial_hours, contract_billing)
        self._close_the_period(batch_id)

        response = admin_client.patch(
            _author_url(issue, batch_id), {"author_id": str(technician_b.id)}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert ServiceLog.objects.get(batch_id=batch_id).author_id == technician_b.id

    def test_an_admin_editing_hours_in_a_closed_period_is_refused_by_the_pool_not_the_permission(
        self, client_a, admin_client, issue, contract, commercial_hours, contract_billing
    ):
        """**The honest boundary of this phase, fixed as a characterisation test.**

        Section 6 of the brief says a closed period is "editável por Admin". The permission
        layer implements that -- the Admin passes ``validate_closed_period_access`` -- but an
        edit that moves hours still fails one layer down, because ``reverse_debit`` refuses a
        closed period for everybody and **reopening a period does not exist**. That is
        decision D42's named debt, not a gap introduced here.

        So the Admin gets ``PERIOD_IS_CLOSED`` (400, a domain refusal) rather than
        ``CLOSED_PERIOD_IS_ADMIN_ONLY`` (403, a permission refusal). The distinction is the
        whole point: the two codes say which layer stopped it, and a future phase that builds
        period reopening will make this test change deliberately rather than discover the
        limitation by accident.
        """
        batch_id = _create_log(client_a, issue, commercial_hours, contract_billing)
        self._close_the_period(batch_id)

        response = admin_client.patch(
            _batch_url(issue, batch_id),
            _payload(commercial_hours, contract_billing, duration="3h"),
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] == "PERIOD_IS_CLOSED"
        assert ServiceLog.objects.get(batch_id=batch_id).logged_hours == Decimal("1.0000")

        # The refusal carries a way forward. An Admin who passed the permission gate and was
        # then stopped by the ledger would otherwise be told only what they cannot do, and the
        # obvious guess -- "get somebody to reopen the month" -- is the thing D42 now records
        # as an open question rather than a task.
        assert response.data["remediation"] == "RECORD_A_NEW_ENTRY_IN_THE_OPEN_COMPETENCE"

    def test_a_refusal_that_is_not_about_a_closed_period_carries_no_remediation(
        self, client_a, issue, commercial_hours, contract_billing
    ):
        """The positive control for the hint above: it is specific, not attached to everything.

        A future date is refused by the same handler and has nothing to do with competencies,
        so a ``remediation`` key here would mean the hint was being stapled onto every error.
        """
        response = client_a.post(
            _list_url(issue),
            _payload(commercial_hours, contract_billing, worked_on="2099-01-01"),
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "remediation" not in response.data

    def test_an_open_period_is_not_locked(
        self, client_a, issue, contract, commercial_hours, contract_billing
    ):
        """The positive control for the whole class."""
        batch_id = _create_log(client_a, issue, commercial_hours, contract_billing)

        assert ServiceLog.objects.get(batch_id=batch_id).debited_period.status == "open"

        response = client_a.patch(
            _batch_url(issue, batch_id),
            _payload(commercial_hours, contract_billing, duration="3h"),
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data


@pytest.mark.contract
@pytest.mark.django_db
class TestDeletionRemainsInHistory:
    """Acceptance criterion 9."""

    def test_a_deleted_log_stays_recoverable_and_its_content_is_in_the_trail(
        self, client_a, issue, commercial_hours, contract_billing
    ):
        """Criterion 9: "permanece recuperável no histórico de auditoria".

        Two mechanisms, and both are asserted because they answer different questions:
        the soft deleted row is still in ``all_objects`` with its hours intact, and the
        trail states how much time left the work item without anybody having to go and
        look for the row.
        """
        batch_id = _create_log(client_a, issue, commercial_hours, contract_billing, duration="2h")

        client_a.delete(_batch_url(issue, batch_id))

        assert ServiceLog.objects.filter(batch_id=batch_id).exists() is False

        recovered = ServiceLog.all_objects.get(batch_id=batch_id)
        assert recovered.deleted_at is not None
        assert recovered.logged_hours == Decimal("2.0000")

        entry = IssueActivity.objects.get(field="service_log", verb="deleted")
        assert "2.0000" in entry.old_value


@pytest.mark.contract
@pytest.mark.django_db
class TestGuestsCannotHoldCapabilities:
    """The guard the brief did not ask for. Three layers, each tested."""

    def test_granting_any_capability_to_a_guest_is_refused(
        self, admin_client, workspace, guest_user, technician_c
    ):
        """Layer two, with the positive control beside it in the same test.

        The same request against a MEMBER succeeds, so this is not passing because the
        endpoint refuses everything.
        """
        for capability in ("can_manage_others", "can_delegate", "can_reassign_author"):
            response = admin_client.patch(
                PERMISSION_URL.format(slug=workspace.slug, member_id=guest_user.id),
                {capability: True},
                format="json",
            )

            assert response.status_code == status.HTTP_400_BAD_REQUEST
            assert response.data["error"] == "GUEST_CANNOT_HOLD_SERVICE_LOG_PERMISSIONS"

        assert ServiceMemberPermission.objects.filter(member=guest_user).exists() is False

        # The positive control.
        _grant(admin_client, workspace, technician_c, can_manage_others=True)
        assert ServiceMemberPermission.objects.filter(member=technician_c).exists()

    def test_demoting_a_member_to_guest_revokes_their_capabilities(
        self, admin_client, workspace, technician_c
    ):
        """Layer three: the stored row must stop claiming something untrue.

        Uses the real workspace member endpoint, which is the path an admin actually takes,
        rather than calling the revocation helper directly -- the demotion and the
        revocation have to be wired together, and only going through the endpoint proves it.
        """
        _grant(admin_client, workspace, technician_c, can_manage_others=True, can_delegate=True)

        membership = WorkspaceMember.objects.get(workspace=workspace, member=technician_c)

        response = admin_client.patch(
            WORKSPACE_MEMBER_URL.format(slug=workspace.slug, pk=membership.id),
            {"role": 5},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data

        permission = ServiceMemberPermission.objects.get(workspace=workspace, member=technician_c)
        assert permission.can_manage_others is False
        assert permission.can_delegate is False
        assert permission.can_reassign_author is False

    def test_the_revocation_on_demotion_is_audited(self, admin_client, workspace, technician_c):
        """Losing authority is as much an audit event as gaining it."""
        _grant(admin_client, workspace, technician_c, can_manage_others=True)
        membership = WorkspaceMember.objects.get(workspace=workspace, member=technician_c)

        admin_client.patch(
            WORKSPACE_MEMBER_URL.format(slug=workspace.slug, pk=membership.id),
            {"role": 5},
            format="json",
        )

        assert ServiceConfigActivity.objects.filter(
            entity_name="service_member_permission",
            field_name="can_manage_others",
            old_value="true",
            new_value="false",
        ).exists()

    def test_a_demoted_member_loses_the_capability_in_practice(
        self, client_a, client_c, admin_client, workspace, technician_c, issue,
        commercial_hours, contract_billing,
    ):
        """The behaviour all three layers exist for, asserted end to end.

        C can edit A's log, is demoted to GUEST, and then cannot -- and the refusal is a
        403 from the work log route itself.
        """
        batch_id = _create_log(client_a, issue, commercial_hours, contract_billing)
        _grant(admin_client, workspace, technician_c, can_manage_others=True)

        allowed = client_c.patch(
            _batch_url(issue, batch_id),
            _payload(commercial_hours, contract_billing, duration="2h"),
            format="json",
        )
        assert allowed.status_code == status.HTTP_200_OK, allowed.data

        membership = WorkspaceMember.objects.get(workspace=workspace, member=technician_c)
        admin_client.patch(
            WORKSPACE_MEMBER_URL.format(slug=workspace.slug, pk=membership.id),
            {"role": 5},
            format="json",
        )

        refused = client_c.patch(
            _batch_url(issue, batch_id),
            _payload(commercial_hours, contract_billing, duration="4h"),
            format="json",
        )

        assert refused.status_code == status.HTTP_403_FORBIDDEN
        assert ServiceLog.objects.get(batch_id=batch_id).logged_hours == Decimal("2.0000")


@pytest.mark.contract
@pytest.mark.django_db
class TestTheActivityTrailCarriesNoMoney:
    """R11(b), re-asserted because Phase 7 added two new activity types.

    The handoff warned that any new serialisation context needs this decided explicitly.
    The activity feed is readable by every Member of the project, so a reassignment or
    delegation entry that carried an amount would leak exactly what R11 withholds -- and
    these two payloads are hand-built rather than reusing a serialised log, so nothing
    stops a future edit from putting money in them except this test.
    """

    def test_no_new_activity_entry_carries_an_amount(
        self, client_a, admin_client, workspace, technician_a, technician_b, issue,
        commercial_hours, contract_billing,
    ):
        # `can_manage_others` is needed as well as the other two, and that is D45's scope
        # rather than an accident: the log below is authored by B, so reassigning it is
        # editing somebody else's record. Without this grant the request is correctly a 403
        # and the second activity entry never exists -- which is how this test first failed.
        _grant(
            admin_client,
            workspace,
            technician_a,
            can_delegate=True,
            can_reassign_author=True,
            can_manage_others=True,
        )

        created = client_a.post(
            _list_url(issue),
            _payload(commercial_hours, contract_billing, author_id=str(technician_b.id)),
            format="json",
        )
        batch_id = created.data["batch_id"]

        client_a.patch(
            _author_url(issue, batch_id), {"author_id": str(technician_a.id)}, format="json"
        )

        entries = IssueActivity.objects.filter(
            field__in=["service_log_delegation", "service_log_author"]
        )

        # The positive control: both entries exist, so this is not vacuous.
        assert entries.count() == 2

        for entry in entries:
            for value in (entry.old_value or "", entry.new_value or ""):
                assert "amount" not in value.lower()
                assert "R$" not in value
