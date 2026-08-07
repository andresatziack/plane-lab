# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Unit tests for the work log authorisation domain. Phase 7.

The rules under test are the ones a permission bug would break silently, so each
absence asserts **the reason code** and sits next to a positive control in the same
fixture -- a suite where every capability happens to be absent is indistinguishable from
one where the resolution is broken.

The scoping matrix of decision D45 is the centre of this file. ``can_reassign_author``
composing with edit rights rather than granting them is the one rule whose failure mode
is a silent widening of authority: it would hand out a slice of ``can_manage_others`` to
anybody holding the reassignment grant, and nothing about the response would say so.
"""

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest

from plane.db.models import (
    ServiceConfigActivity,
    ServiceContractPeriod,
    ServiceMemberPermission,
    ServicePeriodStatus,
)
from plane.tests.factories import (
    IssueFactory,
    ProjectFactory,
    ServiceBillingTypeFactory,
    ServiceClientFactory,
    ServiceContractFactory,
    ServiceHourTypeFactory,
    ServiceLogFactory,
    UserFactory,
    WorkspaceFactory,
    WorkspaceMemberFactory,
)
from plane.utils.service_log import (
    ONLY_THE_AUTHOR_CAN_CHANGE_A_SERVICE_LOG,
    ServiceLogValidationError,
)
from plane.utils.service_permission import (
    CAPABILITY_FIELDS,
    CLOSED_PERIOD_IS_ADMIN_ONLY,
    GUEST_CANNOT_HOLD_SERVICE_LOG_PERMISSIONS,
    NOT_AN_ACTIVE_WORKSPACE_MEMBER,
    ROLE_ADMIN,
    ROLE_GUEST,
    ROLE_MEMBER,
    SERVICE_LOG_AUTHOR_MUST_BE_A_TECHNICIAN,
    SERVICE_LOG_DELEGATION_NOT_PERMITTED,
    SERVICE_LOG_REASSIGNMENT_NOT_PERMITTED,
    ServiceLogCapabilities,
    resolve_capabilities,
    revoke_all_capabilities,
    set_member_capabilities,
    validate_can_change,
    validate_can_delegate,
    validate_can_reassign,
    validate_closed_period_access,
    validate_delegated_author,
    validate_grant_target,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def no_celery_dispatch():
    """Soft delete fires ``soft_delete_related_objects.delay``; no broker in tests."""
    with patch("celery.app.task.Task.apply_async", return_value=None):
        yield


@pytest.fixture
def workspace(db):
    return WorkspaceFactory()


@pytest.fixture
def admin(db, workspace):
    """A workspace ADMIN, who holds all three capabilities implicitly."""
    member = WorkspaceMemberFactory(workspace=workspace, role=ROLE_ADMIN)
    return member.member


@pytest.fixture
def technician_a(db, workspace):
    member = WorkspaceMemberFactory(workspace=workspace, role=ROLE_MEMBER)
    return member.member


@pytest.fixture
def technician_b(db, workspace):
    member = WorkspaceMemberFactory(workspace=workspace, role=ROLE_MEMBER)
    return member.member


@pytest.fixture
def guest(db, workspace):
    member = WorkspaceMemberFactory(workspace=workspace, role=ROLE_GUEST)
    return member.member


@pytest.fixture
def outsider(db):
    """A real user who is not a member of the workspace under test."""
    return UserFactory()


@pytest.fixture
def project(db, workspace):
    return ProjectFactory(workspace=workspace, is_time_tracking_enabled=True)


@pytest.fixture
def issue(db, project):
    return IssueFactory(project=project, workspace=project.workspace)


@pytest.fixture
def catalogue(db, workspace):
    return {
        "hour_type": ServiceHourTypeFactory(workspace=workspace, multiplier=Decimal("1.00")),
        "billing_type": ServiceBillingTypeFactory(workspace=workspace),
    }


@pytest.fixture
def log_of_a(db, issue, technician_a, catalogue):
    return ServiceLogFactory(
        issue=issue,
        author=technician_a,
        hour_type=catalogue["hour_type"],
        billing_type=catalogue["billing_type"],
    )


def _grant(workspace, user, actor, **flags):
    return set_member_capabilities(
        workspace_id=workspace.pk, member_id=user.id, actor=actor, **flags
    )


class TestCapabilityResolution:
    """``resolve_capabilities`` is the security boundary; everything else trusts it."""

    def test_an_admin_holds_all_three_without_any_grant_row(self, workspace, admin):
        """The resolution is ``is_admin OR flag``, never a back-filled grant.

        Asserts the absence of the row *and* the presence of the capabilities, because
        an Admin whose authority depended on a row would lose it to a bad back-fill.
        """
        capabilities = resolve_capabilities(admin, workspace_id=workspace.pk)

        assert ServiceMemberPermission.objects.filter(workspace=workspace, member=admin).exists() is False
        assert capabilities.is_admin is True
        assert all(getattr(capabilities, name) for name in CAPABILITY_FIELDS)

    def test_a_plain_member_holds_none_of_them(self, workspace, technician_a):
        """The positive control for every refusal below: a MEMBER starts with nothing."""
        capabilities = resolve_capabilities(technician_a, workspace_id=workspace.pk)

        assert capabilities.is_member is True
        assert capabilities.is_admin is False
        assert not any(getattr(capabilities, name) for name in CAPABILITY_FIELDS)

    @pytest.mark.parametrize("capability", CAPABILITY_FIELDS)
    def test_a_granted_capability_resolves_and_the_others_do_not(
        self, workspace, admin, technician_a, capability
    ):
        """Granting one must grant exactly one -- the three are independent (D45).

        The "and the others do not" half is the point: a resolution that returned all
        three for any grant would pass a test that only checked the one asked for.
        """
        _grant(workspace, technician_a, admin, **{capability: True})

        capabilities = resolve_capabilities(technician_a, workspace_id=workspace.pk)

        assert getattr(capabilities, capability) is True
        assert [
            name for name in CAPABILITY_FIELDS if getattr(capabilities, name)
        ] == [capability]

    def test_a_guest_holds_nothing_even_with_a_row_saying_otherwise(
        self, workspace, technician_a, guest
    ):
        """The outermost of the three GUEST layers, and the only one that is load-bearing.

        The row is written **directly through the ORM**, bypassing
        ``set_member_capabilities`` and its refusal, precisely to prove the read-time gate
        stands on its own. A stale grant, a hand-edited row or a path nobody thought of
        must not escalate a GUEST -- and this cannot be a check constraint, because the
        role lives in another table.

        The positive control is in the same fixture: ``technician_a`` gets an identical
        row and does resolve the capabilities, so a resolution that returned nothing for
        everybody could not pass this.
        """
        for user in (guest, technician_a):
            ServiceMemberPermission.objects.create(
                workspace=workspace,
                member=user,
                can_manage_others=True,
                can_delegate=True,
                can_reassign_author=True,
            )

        guest_capabilities = resolve_capabilities(guest, workspace_id=workspace.pk)
        member_capabilities = resolve_capabilities(technician_a, workspace_id=workspace.pk)

        assert guest_capabilities.is_member is False
        assert not any(getattr(guest_capabilities, name) for name in CAPABILITY_FIELDS)
        # The positive control.
        assert all(getattr(member_capabilities, name) for name in CAPABILITY_FIELDS)

    def test_a_non_member_holds_nothing_and_is_not_a_member(self, workspace, outsider):
        capabilities = resolve_capabilities(outsider, workspace_id=workspace.pk)

        assert capabilities.is_member is False
        assert capabilities.is_admin is False
        assert not any(getattr(capabilities, name) for name in CAPABILITY_FIELDS)

    def test_a_deactivated_member_holds_nothing(self, workspace, admin, technician_a):
        """Deactivation is how this codebase removes somebody, so it must revoke reach.

        ``is_active = False`` is what ``WorkSpaceMemberViewSet.destroy`` does -- the row
        stays. A resolution keyed only on the row's existence would leave a removed
        employee able to edit their colleagues' billing records.
        """
        _grant(workspace, technician_a, admin, can_manage_others=True)

        membership = technician_a.member_workspace.get(workspace=workspace)
        membership.is_active = False
        membership.save()

        capabilities = resolve_capabilities(technician_a, workspace_id=workspace.pk)

        assert capabilities.is_member is False
        assert capabilities.can_manage_others is False

    def test_resolution_by_slug_matches_resolution_by_id(self, workspace, admin, technician_a):
        """Both call shapes exist; they must not be able to disagree."""
        _grant(workspace, technician_a, admin, can_delegate=True)

        by_id = resolve_capabilities(technician_a, workspace_id=workspace.pk)
        by_slug = resolve_capabilities(technician_a, slug=workspace.slug)

        assert by_id == by_slug

    def test_a_grant_in_another_workspace_does_not_leak(self, workspace, admin, technician_a):
        """Capabilities are workspace scoped, and the isolation test of D19 applies here too.

        The grant is real, so this is not vacuous -- it resolves in the workspace where it
        was made and not in the other one.
        """
        other = WorkspaceFactory(slug="other-workspace")
        WorkspaceMemberFactory(workspace=other, member=technician_a, role=ROLE_MEMBER)

        _grant(workspace, technician_a, admin, can_manage_others=True)

        assert resolve_capabilities(technician_a, workspace_id=workspace.pk).can_manage_others is True
        assert resolve_capabilities(technician_a, workspace_id=other.pk).can_manage_others is False

    def test_the_grantable_capabilities_are_all_audited(self):
        """Every grantable flag must be in ``TRACKED_FIELDS``.

        A capability that could be granted without being tracked would be an **unaudited
        grant of authority over a financial record**, which is reason 2 for this model
        existing at all (D45). Asserted as an equality rather than a subset so that adding
        a tracked field without making it grantable also fails, and the two lists cannot
        drift in either direction.
        """
        assert list(ServiceMemberPermission.TRACKED_FIELDS) == list(CAPABILITY_FIELDS)


class TestTheReassignmentScope:
    """Decision D45's matrix. The one rule whose failure widens authority silently."""

    def test_the_author_with_the_grant_reassigns_their_own(self, log_of_a, technician_a):
        capabilities = ServiceLogCapabilities(
            user_id=technician_a.id, is_member=True, can_reassign_author=True
        )

        validate_can_reassign(log_of_a, capabilities)

    def test_the_grant_alone_does_not_reach_somebody_elses_log(self, log_of_a, technician_b):
        """``can_reassign_author`` alone reassigns **only your own**.

        This is the silent-widening case. Without the conjunction in ``may_reassign``, the
        reassignment grant would let its holder edit the author of anybody's log -- which
        is editing somebody else's record, and nobody granted that.
        """
        capabilities = ServiceLogCapabilities(
            user_id=technician_b.id, is_member=True, can_reassign_author=True
        )

        with pytest.raises(ServiceLogValidationError) as excinfo:
            validate_can_reassign(log_of_a, capabilities)

        assert excinfo.value.code == SERVICE_LOG_REASSIGNMENT_NOT_PERMITTED

    def test_both_grants_together_reach_somebody_elses_log(self, log_of_a, technician_b):
        """The composition that *is* authorised, and the positive control for the test above."""
        capabilities = ServiceLogCapabilities(
            user_id=technician_b.id,
            is_member=True,
            can_manage_others=True,
            can_reassign_author=True,
        )

        validate_can_reassign(log_of_a, capabilities)

    def test_managing_others_alone_does_not_permit_changing_the_author(
        self, log_of_a, technician_b
    ):
        """The other direction of independence: editing is not reassigning.

        ``can_manage_others`` may change what was done; only ``can_reassign_author`` may
        change who did it. Asserted alongside a passing ``validate_can_change`` so this
        cannot be satisfied by a capability set that permits nothing at all.
        """
        capabilities = ServiceLogCapabilities(
            user_id=technician_b.id, is_member=True, can_manage_others=True
        )

        # The positive control: this member genuinely may edit the log.
        validate_can_change(log_of_a, capabilities)

        with pytest.raises(ServiceLogValidationError) as excinfo:
            validate_can_reassign(log_of_a, capabilities)

        assert excinfo.value.code == SERVICE_LOG_REASSIGNMENT_NOT_PERMITTED

    def test_the_author_without_the_grant_may_not_reassign(self, log_of_a, technician_a):
        """Owning the record is not authority to re-attribute it."""
        capabilities = ServiceLogCapabilities(user_id=technician_a.id, is_member=True)

        # Positive control: the author may still edit their own log.
        validate_can_change(log_of_a, capabilities)

        with pytest.raises(ServiceLogValidationError) as excinfo:
            validate_can_reassign(log_of_a, capabilities)

        assert excinfo.value.code == SERVICE_LOG_REASSIGNMENT_NOT_PERMITTED


class TestEditAndDeleteAuthority:
    def test_the_author_may_change_their_own(self, log_of_a, technician_a):
        validate_can_change(log_of_a, ServiceLogCapabilities(user_id=technician_a.id, is_member=True))

    def test_another_technician_may_not(self, log_of_a, technician_b):
        with pytest.raises(ServiceLogValidationError) as excinfo:
            validate_can_change(
                log_of_a, ServiceLogCapabilities(user_id=technician_b.id, is_member=True)
            )

        assert excinfo.value.code == ONLY_THE_AUTHOR_CAN_CHANGE_A_SERVICE_LOG

    def test_a_holder_of_manage_others_may(self, log_of_a, technician_b):
        validate_can_change(
            log_of_a,
            ServiceLogCapabilities(user_id=technician_b.id, is_member=True, can_manage_others=True),
        )

    def test_authority_keys_on_the_author_and_not_on_the_creator(
        self, issue, technician_a, technician_b, catalogue
    ):
        """R8: the record belongs to whoever did the work, not to whoever typed it.

        A delegated log authored by A and created by B is A's to edit. B, having only
        typed it, needs ``can_manage_others`` like anybody else -- which is also why
        ``allow_permission(creator=True)`` is not reused for this: it keys on
        ``created_by`` and would hand the log to B.
        """
        log = ServiceLogFactory(
            issue=issue,
            author=technician_a,
            created_by=technician_b,
            hour_type=catalogue["hour_type"],
            billing_type=catalogue["billing_type"],
        )

        validate_can_change(log, ServiceLogCapabilities(user_id=technician_a.id, is_member=True))

        with pytest.raises(ServiceLogValidationError) as excinfo:
            validate_can_change(log, ServiceLogCapabilities(user_id=technician_b.id, is_member=True))

        assert excinfo.value.code == ONLY_THE_AUTHOR_CAN_CHANGE_A_SERVICE_LOG


class TestDelegation:
    def test_logging_for_yourself_is_not_delegation(self, technician_a):
        """No grant is consulted, so the common case never needs one."""
        capabilities = ServiceLogCapabilities(user_id=technician_a.id, is_member=True)

        validate_can_delegate(technician_a.id, capabilities)
        validate_can_delegate(None, capabilities)

    def test_naming_somebody_else_without_the_grant_is_refused(self, technician_a, technician_b):
        capabilities = ServiceLogCapabilities(user_id=technician_a.id, is_member=True)

        with pytest.raises(ServiceLogValidationError) as excinfo:
            validate_can_delegate(technician_b.id, capabilities)

        assert excinfo.value.code == SERVICE_LOG_DELEGATION_NOT_PERMITTED

    def test_naming_somebody_else_with_the_grant_is_allowed(self, technician_a, technician_b):
        capabilities = ServiceLogCapabilities(
            user_id=technician_a.id, is_member=True, can_delegate=True
        )

        validate_can_delegate(technician_b.id, capabilities)

    def test_the_declared_author_must_be_a_technician_of_this_workspace(
        self, workspace, technician_b
    ):
        """The positive control for the two refusals below."""
        membership = validate_delegated_author(technician_b.id, workspace_id=workspace.pk)

        assert membership.member_id == technician_b.id

    def test_delegating_to_a_guest_is_refused(self, workspace, guest):
        """A GUEST is a *client's* user, so this would credit client staff with labour.

        Separate from the caller's own grant on purpose: holding ``can_delegate`` must not
        imply being able to name anybody at all as the author.
        """
        with pytest.raises(ServiceLogValidationError) as excinfo:
            validate_delegated_author(guest.id, workspace_id=workspace.pk)

        assert excinfo.value.code == SERVICE_LOG_AUTHOR_MUST_BE_A_TECHNICIAN

    def test_delegating_to_a_non_member_is_refused(self, workspace, outsider):
        with pytest.raises(ServiceLogValidationError) as excinfo:
            validate_delegated_author(outsider.id, workspace_id=workspace.pk)

        assert excinfo.value.code == SERVICE_LOG_AUTHOR_MUST_BE_A_TECHNICIAN

    def test_delegating_to_a_deactivated_member_is_refused(self, workspace, technician_b):
        membership = technician_b.member_workspace.get(workspace=workspace)
        membership.is_active = False
        membership.save()

        with pytest.raises(ServiceLogValidationError) as excinfo:
            validate_delegated_author(technician_b.id, workspace_id=workspace.pk)

        assert excinfo.value.code == SERVICE_LOG_AUTHOR_MUST_BE_A_TECHNICIAN


class TestClosedPeriodIsAdminOnly:
    """The one lock the three grants do not open.

    What a closed period protects is the reproducibility of a consolidation already sent
    to the client -- which carries the technician's name, per R11 -- so it is broader than
    "no balance may move" and therefore covers reassignment too.
    """

    @pytest.fixture
    def closed_period_log(self, db, workspace, issue, technician_a, catalogue):
        service_client = ServiceClientFactory(workspace=workspace)
        contract = ServiceContractFactory(workspace=workspace, service_client=service_client)
        period = ServiceContractPeriod.objects.create(
            workspace=workspace,
            contract=contract,
            competence_year=2026,
            competence_month=1,
            starts_on=date(2026, 1, 1),
            ends_on=date(2026, 1, 31),
            contracted_hours=Decimal("30.0000"),
            status=ServicePeriodStatus.CLOSED,
        )
        return ServiceLogFactory(
            issue=issue,
            author=technician_a,
            hour_type=catalogue["hour_type"],
            billing_type=catalogue["billing_type"],
            debited_period=period,
        )

    @pytest.fixture
    def open_period_log(self, db, workspace, issue, technician_a, catalogue):
        service_client = ServiceClientFactory(workspace=workspace, name="Open Co")
        contract = ServiceContractFactory(workspace=workspace, service_client=service_client)
        period = ServiceContractPeriod.objects.create(
            workspace=workspace,
            contract=contract,
            competence_year=2026,
            competence_month=2,
            starts_on=date(2026, 2, 1),
            ends_on=date(2026, 2, 28),
            contracted_hours=Decimal("30.0000"),
            status=ServicePeriodStatus.OPEN,
        )
        return ServiceLogFactory(
            issue=issue,
            author=technician_a,
            hour_type=catalogue["hour_type"],
            billing_type=catalogue["billing_type"],
            debited_period=period,
        )

    def test_an_admin_passes(self, closed_period_log, admin, workspace):
        capabilities = resolve_capabilities(admin, workspace_id=workspace.pk)

        validate_closed_period_access([closed_period_log], capabilities)

    def test_the_author_is_refused(self, closed_period_log, technician_a, workspace):
        capabilities = resolve_capabilities(technician_a, workspace_id=workspace.pk)

        with pytest.raises(ServiceLogValidationError) as excinfo:
            validate_closed_period_access([closed_period_log], capabilities)

        assert excinfo.value.code == CLOSED_PERIOD_IS_ADMIN_ONLY

    @pytest.mark.parametrize("capability", CAPABILITY_FIELDS)
    def test_no_grant_unlocks_it(
        self, closed_period_log, workspace, admin, technician_b, capability
    ):
        """Every one of the three, individually, still refused.

        Parametrised rather than written once because the failure this guards against is
        somebody adding a capability check to the closed-period path later; whichever flag
        they reached for, this fails.
        """
        _grant(workspace, technician_b, admin, **{capability: True})
        capabilities = resolve_capabilities(technician_b, workspace_id=workspace.pk)

        with pytest.raises(ServiceLogValidationError) as excinfo:
            validate_closed_period_access([closed_period_log], capabilities)

        assert excinfo.value.code == CLOSED_PERIOD_IS_ADMIN_ONLY

    def test_all_three_grants_together_still_do_not_unlock_it(
        self, closed_period_log, workspace, admin, technician_b
    ):
        _grant(
            workspace,
            technician_b,
            admin,
            can_manage_others=True,
            can_delegate=True,
            can_reassign_author=True,
        )
        capabilities = resolve_capabilities(technician_b, workspace_id=workspace.pk)

        with pytest.raises(ServiceLogValidationError) as excinfo:
            validate_closed_period_access([closed_period_log], capabilities)

        assert excinfo.value.code == CLOSED_PERIOD_IS_ADMIN_ONLY

    def test_an_open_period_is_not_locked(self, open_period_log, technician_a, workspace):
        """The positive control. Without it, a gate that refused everything would pass above."""
        capabilities = resolve_capabilities(technician_a, workspace_id=workspace.pk)

        validate_closed_period_access([open_period_log], capabilities)

    def test_a_log_with_no_period_is_not_locked(self, log_of_a, technician_a, workspace):
        """No pool was debited, so there is no competency to be closed.

        The second positive control, and it matters: ``debited_period`` is null for a
        non-billable row, an avulso row and a row with no contract configured (D27). A gate
        that treated null as closed would lock most of the system out of editing.
        """
        capabilities = resolve_capabilities(technician_a, workspace_id=workspace.pk)

        assert log_of_a.debited_period_id is None
        validate_closed_period_access([log_of_a], capabilities)

    def test_any_segment_in_a_closed_period_locks_the_whole_batch(
        self, closed_period_log, open_period_log, technician_a, workspace
    ):
        """A split entry is edited as one unit, so one locked segment locks the entry.

        The alternative -- checking only the first row -- would let an edit through when the
        batch straddles a month boundary and only the later segment is still open, which is
        exactly the shape R7 produces on 31/01 23:00 to 01/02 01:00.
        """
        capabilities = resolve_capabilities(technician_a, workspace_id=workspace.pk)

        with pytest.raises(ServiceLogValidationError) as excinfo:
            validate_closed_period_access([open_period_log, closed_period_log], capabilities)

        assert excinfo.value.code == CLOSED_PERIOD_IS_ADMIN_ONLY


class TestGrantingAndRevoking:
    def test_granting_to_a_guest_is_refused(self, workspace, guest):
        """The second GUEST layer: the stored data never claims something untrue."""
        with pytest.raises(ServiceLogValidationError) as excinfo:
            validate_grant_target(workspace.pk, guest.id)

        assert excinfo.value.code == GUEST_CANNOT_HOLD_SERVICE_LOG_PERMISSIONS

    def test_granting_to_a_non_member_is_refused(self, workspace, outsider):
        with pytest.raises(ServiceLogValidationError) as excinfo:
            validate_grant_target(workspace.pk, outsider.id)

        assert excinfo.value.code == NOT_AN_ACTIVE_WORKSPACE_MEMBER

    def test_granting_to_a_member_is_allowed(self, workspace, technician_a):
        """Positive control for the two refusals above."""
        assert validate_grant_target(workspace.pk, technician_a.id).member_id == technician_a.id

    def test_set_member_capabilities_refuses_a_guest_and_writes_no_row(
        self, workspace, admin, guest
    ):
        """The refusal has to happen before the write, not after."""
        with pytest.raises(ServiceLogValidationError) as excinfo:
            _grant(workspace, guest, admin, can_delegate=True)

        assert excinfo.value.code == GUEST_CANNOT_HOLD_SERVICE_LOG_PERMISSIONS
        assert ServiceMemberPermission.objects.filter(workspace=workspace, member=guest).exists() is False

    def test_a_grant_is_audited_with_the_actor(self, workspace, admin, technician_a):
        """Reason 2 for this model existing: "quem deu essa permissão, e quando?".

        The ``CREATED`` verb, because the row came into being, and the summary names the
        capability -- so the trail is readable without joining back to the row's current
        state, which is the whole point of an append-only trail.
        """
        _grant(workspace, technician_a, admin, can_manage_others=True)

        entry = ServiceConfigActivity.objects.get(
            entity_name="service_member_permission", workspace=workspace
        )

        assert entry.verb == "created"
        assert entry.actor_id == admin.id
        assert "can_manage_others" in entry.new_value

    def test_a_later_change_is_audited_field_by_field_with_both_sides(
        self, workspace, admin, technician_a
    ):
        """An ``UPDATED`` row carries old and new, which is what an audit trail is for."""
        _grant(workspace, technician_a, admin, can_manage_others=True)
        _grant(workspace, technician_a, admin, can_delegate=True)

        entry = ServiceConfigActivity.objects.get(
            entity_name="service_member_permission", field_name="can_delegate"
        )

        assert entry.verb == "updated"
        assert entry.old_value == "false"
        assert entry.new_value == "true"
        assert entry.actor_id == admin.id

    def test_a_partial_patch_leaves_the_other_capabilities_alone(
        self, workspace, admin, technician_a
    ):
        """The three are independent, so a form that omits two must not revoke them."""
        _grant(workspace, technician_a, admin, can_manage_others=True, can_delegate=True)
        _grant(workspace, technician_a, admin, can_reassign_author=True)

        capabilities = resolve_capabilities(technician_a, workspace_id=workspace.pk)

        assert all(getattr(capabilities, name) for name in CAPABILITY_FIELDS)

    def test_a_first_grant_of_nothing_writes_no_row_and_no_audit_entry(
        self, workspace, admin, technician_a
    ):
        """Granting false to somebody who holds nothing is not an event.

        A ``CREATED`` entry here would describe an act that did not happen, and the members
        screen would start listing somebody with no elevated access.
        """
        _grant(workspace, technician_a, admin, can_manage_others=False)

        assert ServiceMemberPermission.objects.filter(workspace=workspace, member=technician_a).exists() is False
        assert ServiceConfigActivity.objects.filter(entity_name="service_member_permission").exists() is False

    def test_revoking_clears_every_capability_and_audits_each_one_held(
        self, workspace, admin, technician_a
    ):
        _grant(workspace, technician_a, admin, can_manage_others=True, can_delegate=True)

        changes = revoke_all_capabilities(
            workspace_id=workspace.pk, member_id=technician_a.id, actor=admin
        )

        capabilities = resolve_capabilities(technician_a, workspace_id=workspace.pk)

        assert not any(getattr(capabilities, name) for name in CAPABILITY_FIELDS)
        assert set(changes) == {"can_manage_others", "can_delegate"}
        assert (
            ServiceConfigActivity.objects.filter(
                entity_name="service_member_permission", verb="updated", new_value="false"
            ).count()
            == 2
        )

    def test_revoking_keeps_the_row_so_the_trail_stays_attached(
        self, workspace, admin, technician_a
    ):
        """Revocation sets the flags; it does not delete.

        The audit entries are keyed on ``entity_identifier``, so deleting the row would
        leave the history of the grant pointing at nothing.
        """
        _grant(workspace, technician_a, admin, can_delegate=True)
        revoke_all_capabilities(workspace_id=workspace.pk, member_id=technician_a.id, actor=admin)

        assert ServiceMemberPermission.objects.filter(workspace=workspace, member=technician_a).exists()

    def test_revoking_somebody_who_held_nothing_writes_no_audit_noise(
        self, workspace, admin, technician_a
    ):
        """Demotion is common; most people hold no grant. The trail must stay readable."""
        changes = revoke_all_capabilities(
            workspace_id=workspace.pk, member_id=technician_a.id, actor=admin
        )

        assert changes == {}
        assert ServiceConfigActivity.objects.filter(entity_name="service_member_permission").exists() is False

    def test_one_row_per_member_per_workspace(self, workspace, admin, technician_a):
        """Two rows would be two answers to the same question, resolved by ordering."""
        _grant(workspace, technician_a, admin, can_delegate=True)
        _grant(workspace, technician_a, admin, can_manage_others=True)

        assert (
            ServiceMemberPermission.objects.filter(workspace=workspace, member=technician_a).count() == 1
        )
