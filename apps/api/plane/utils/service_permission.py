# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Who may create, edit, delete and reattribute a work log.

The authorisation half of the work log domain. ``plane.utils.service_log`` decides
*which rows exist* and ``plane.utils.service_log_time`` decides *what the numbers
are*; this module decides *who is allowed*. Nothing here knows that HTTP exists, as
section 6 of the master context requires -- the views translate the error codes into
status codes.

**Why the outermost guard is a read-time gate and not DDL.** Everywhere else in this
series, an invariant that decides money was pushed into a check constraint, on the
argument that a rule held only in Python is a rule the next ``queryset.update()``
bypasses. That move is unavailable here: the capabilities live in
``service_member_permissions`` and the role lives in ``workspace_members``, and a
PostgreSQL check constraint cannot read another table. The substitute is to make the
**resolution** authoritative rather than the storage -- ``resolve_capabilities`` reads
the live role on every call and returns nothing at all for a GUEST, so a stale,
hand-edited or badly back-filled grant row cannot escalate anybody. The stored row is
then kept honest by two further layers (refusing the grant, revoking on demotion), but
neither of those is what makes it safe.
"""

# Python imports
from dataclasses import dataclass

# Django imports
from django.db import transaction

# Module imports
from plane.db.models import (
    ServiceMemberPermission,
    ServicePeriodStatus,
    WorkspaceMember,
)
from plane.utils.service_catalog import (
    create_with_config_activity,
    save_with_config_activity,
)

# Error codes, in the UPPER_SNAKE style the rest of the domain uses. The frontend maps
# them to translated strings; the API never returns Portuguese.
#
# ``ONLY_THE_AUTHOR_CAN_CHANGE_A_SERVICE_LOG`` is deliberately NOT renamed even though
# its meaning widened -- it now means "you are not the author and you hold no grant".
# It is the code Phase 3 shipped, the frontend already translates it, and renaming a
# stable error code to reflect an internal refactor breaks a consumer for no gain.
from plane.utils.service_log import (  # noqa: F401  (re-exported for callers)
    ONLY_THE_AUTHOR_CAN_CHANGE_A_SERVICE_LOG,
    ServiceLogValidationError,
)

#: Delegation was attempted by somebody who was never granted it.
SERVICE_LOG_DELEGATION_NOT_PERMITTED = "SERVICE_LOG_DELEGATION_NOT_PERMITTED"

#: Reassignment was attempted by somebody who holds no ``can_reassign_author``, or who
#: holds it but may not edit *this* log. The two are one code on purpose: telling the
#: caller which half failed would let a member map out who authored what.
SERVICE_LOG_REASSIGNMENT_NOT_PERMITTED = "SERVICE_LOG_REASSIGNMENT_NOT_PERMITTED"

#: The declared author is not somebody who can perform work in this workspace.
SERVICE_LOG_AUTHOR_MUST_BE_A_TECHNICIAN = "SERVICE_LOG_AUTHOR_MUST_BE_A_TECHNICIAN"

#: A work log whose competency period is CLOSED may only be touched by a workspace
#: ADMIN. See ``validate_closed_period_access`` for why this trumps the grants.
CLOSED_PERIOD_IS_ADMIN_ONLY = "CLOSED_PERIOD_IS_ADMIN_ONLY"

#: A GUEST was offered one of the three capabilities.
GUEST_CANNOT_HOLD_SERVICE_LOG_PERMISSIONS = "GUEST_CANNOT_HOLD_SERVICE_LOG_PERMISSIONS"

#: An author change was sent to the edit route. It has its own route because it must not
#: touch the ledger -- an edit rebuilds the batch, reversing and reapplying every debit,
#: which lands on the same balance only by cancellation and cannot run at all in a closed
#: period. Refused rather than silently ignored: dropping the field would tell the caller
#: the reassignment succeeded.
SERVICE_LOG_AUTHOR_IS_REASSIGNED_SEPARATELY = "SERVICE_LOG_AUTHOR_IS_REASSIGNED_SEPARATELY"

#: The reassignment route was called without a target author.
SERVICE_LOG_AUTHOR_IS_REQUIRED = "SERVICE_LOG_AUTHOR_IS_REQUIRED"

#: The grant target is not an active member of the workspace at all.
NOT_AN_ACTIVE_WORKSPACE_MEMBER = "NOT_AN_ACTIVE_WORKSPACE_MEMBER"

#: The three grantable capabilities, in one place so the model, the serializer, the
#: endpoint and the tests cannot drift apart on spelling. Mirrors
#: ``ServiceMemberPermission.TRACKED_FIELDS`` and there is a test asserting they match --
#: a capability that is grantable but untracked would be an unaudited grant, which is
#: reason 2 for that model existing.
CAPABILITY_FIELDS = ("can_manage_others", "can_delegate", "can_reassign_author")

# Roles, by value, to avoid importing the DRF-flavoured ``ROLE`` enum from
# ``plane.app.permissions`` into the domain layer. The domain must not depend on the
# HTTP layer; these are the same three numbers ``plane/db/models/project.py`` defines.
ROLE_ADMIN = 20
ROLE_MEMBER = 15
ROLE_GUEST = 5


@dataclass(frozen=True)
class ServiceLogCapabilities:
    """What one user may do with work logs in one workspace.

    Frozen because it is a resolved answer, not a mutable permission object: it is
    computed from the live role plus the grant row at the moment of the request, and a
    caller that could mutate it would be re-deciding authorisation halfway through a
    view.

    ``is_admin`` is kept alongside the three effective flags even though an Admin has
    all three, because the closed-period rule is **not** expressible as a capability --
    it is the one rule the grants cannot unlock. See
    ``validate_closed_period_access``.

    ``is_member`` distinguishes "an active technician with no grants" from "not in this
    workspace at all". Both resolve to no capabilities, but only the first may author
    a work log, and collapsing them would make the delegation target check unable to
    tell a colleague from a stranger.
    """

    #: Who this answer is about. A field rather than an attribute set afterwards,
    #: because ``frozen=True`` refuses assignment to *any* attribute and not merely to
    #: declared fields -- so carrying it "outside" the dataclass is not an option.
    user_id: object = None

    is_admin: bool = False
    is_member: bool = False
    can_manage_others: bool = False
    can_delegate: bool = False
    can_reassign_author: bool = False

    def may_edit(self, service_log):
        """Whether this user may edit or delete ``service_log`` at all.

        The author always may -- it is their own record. Anybody else needs
        ``can_manage_others``, which an Admin holds implicitly.

        Deliberately compares ``author_id`` and not ``created_by``. Once delegation
        exists the two differ, and R8 makes the author the person whose work is
        described; the record belongs to them, not to whoever typed it. This is also
        why ``allow_permission(creator=True)`` is not reused here: it keys on
        ``created_by`` *and* hands over the whole view while ignoring the role, which
        the fork audit flagged as an existing security problem.
        """
        if self.can_manage_others:
            return True

        # Both sides stringified: ``author_id`` is a UUID from the database while
        # ``user_id`` may arrive as a UUID or its string form depending on the caller.
        return self.user_id is not None and str(service_log.author_id) == str(self.user_id)

    def may_reassign(self, service_log):
        """Whether this user may change ``service_log``'s author.

        **Scoped to what they may already edit** (decision D45): ``can_reassign_author``
        composes with edit rights rather than granting them. Reassigning somebody
        else's log is editing somebody else's record even though no balance moves, so
        without this conjunction the reassignment grant would silently include a slice
        of ``can_manage_others``.
        """
        return self.can_reassign_author and self.may_edit(service_log)


def resolve_capabilities(user, *, workspace_id=None, slug=None):
    """What ``user`` may do with work logs in one workspace. The single source of truth.

    **Reads the live role every time, and that is the security boundary.** A GUEST
    resolves to no capabilities even if a grant row says otherwise, which is what makes
    a stale or hand-written row harmless -- see the module docstring for why this could
    not be a check constraint. A user who is not an active member of the workspace
    resolves to nothing at all.

    A workspace ADMIN gets all three implicitly and needs no row. The resolution is
    ``is_admin OR flag``, never a back-filled grant: an Admin whose capabilities
    depended on a row would lose them to a bad back-fill, which is the failure mode of
    encoding an implication as data.

    Pass either ``workspace_id`` or ``slug``; the work log views have the slug in their
    kwargs and the domain callers usually have the id.
    """
    filters = {"member": user, "is_active": True}

    if workspace_id is not None:
        filters["workspace_id"] = workspace_id
    else:
        filters["workspace__slug"] = slug

    membership = WorkspaceMember.objects.filter(**filters).values("role", "workspace_id").first()

    if membership is None:
        return ServiceLogCapabilities(user_id=user.id)

    is_admin = membership["role"] == ROLE_ADMIN

    # A GUEST holds nothing, full stop -- not even through a grant row. A GUEST with
    # `can_delegate` is privilege escalation: a client's own user creating work logs
    # attributed to a technician. Returning early rather than filtering the row keeps
    # that fact in one visible place instead of spread across three `and` clauses.
    if membership["role"] <= ROLE_GUEST:
        return ServiceLogCapabilities(user_id=user.id, is_admin=False, is_member=False)

    granted = (
        ServiceMemberPermission.objects.filter(
            workspace_id=membership["workspace_id"], member=user
        )
        .values(*CAPABILITY_FIELDS)
        .first()
        or {}
    )

    return ServiceLogCapabilities(
        user_id=user.id,
        is_admin=is_admin,
        is_member=True,
        **{name: is_admin or bool(granted.get(name)) for name in CAPABILITY_FIELDS},
    )


# ---------------------------------------------------------------------------
# Validation used by the write paths
# ---------------------------------------------------------------------------


def validate_can_change(service_log, capabilities):
    """Refuse an edit or delete by somebody with no claim to this work log.

    Replaces Phase 3's ``validate_author_can_change``, which had no notion of a grant
    because no grant existed. The error code is unchanged so the frontend's translation
    and the Phase 3 tests keep working.
    """
    if not capabilities.may_edit(service_log):
        raise ServiceLogValidationError(ONLY_THE_AUTHOR_CAN_CHANGE_A_SERVICE_LOG)


def validate_can_delegate(author_id, capabilities):
    """Refuse a work log declared for somebody else without the delegation grant.

    Called with the *requested* author. Logging for oneself is never delegation, so an
    ``author_id`` equal to the caller -- or absent, which means the same thing -- passes
    without consulting any grant.
    """
    if author_id is None or str(author_id) == str(capabilities.user_id):
        return

    if not capabilities.can_delegate:
        raise ServiceLogValidationError(SERVICE_LOG_DELEGATION_NOT_PERMITTED)


def validate_delegated_author(author_id, *, workspace_id):
    """Refuse a declared author who cannot perform work in this workspace.

    Two rejections, and they are different mistakes: somebody outside the workspace
    entirely, and a GUEST. The GUEST case is the one worth spelling out -- a GUEST is a
    *client's* user (section 2b of the master context), so attributing technician work
    to one would put a client's name on the labour side of an invoice.

    Returns the ``WorkspaceMember`` row, so the caller does not query twice.
    """
    membership = WorkspaceMember.objects.filter(
        workspace_id=workspace_id, member_id=author_id, is_active=True
    ).first()

    if membership is None or membership.role <= ROLE_GUEST:
        raise ServiceLogValidationError(SERVICE_LOG_AUTHOR_MUST_BE_A_TECHNICIAN)

    return membership


def validate_can_reassign(service_log, capabilities):
    """Refuse an author change without the scoped reassignment grant.

    See ``ServiceLogCapabilities.may_reassign``: the grant composes with edit rights
    instead of implying them.
    """
    if not capabilities.may_reassign(service_log):
        raise ServiceLogValidationError(SERVICE_LOG_REASSIGNMENT_NOT_PERMITTED)


def validate_closed_period_access(service_logs, capabilities):
    """A work log in a CLOSED competency period is touchable by ADMIN only.

    **The grants do not unlock this, and that is deliberate** -- the one place where the
    three capabilities stop composing. What a closed period protects is not "no balance
    may move"; it is **the reproducibility of a document that has already been sent**.
    The monthly consolidation lists, per line, the date, the work item, the *technician*,
    the time, the hour type and the value, and R11 says the client sees the author of a
    work log. So the technician's name is on the attachment the client is holding.
    Changing it afterwards keeps the total and breaks the detail, and a client
    reconciling line by line finds a discrepancy with the document in their hand.

    That argument covers reassignment too, which moves no hours at all. It is the reason
    this check is not conditioned on whether the edit touches money: the lock is
    "closed means ADMIN, always", which is auditable and teachable, whereas "closed
    means ADMIN, except changing the author" invites the next exception and then the one
    after it. Financial locks erode by exception, never all at once.

    The cost of keeping it strict is low, which is what makes it sustainable: the grants
    exist so ordinary work needs no Admin, and a closed period is by definition not
    ordinary.

    KNOWN BOUNDARY, and it is decision **D42's existing debt rather than a new gap**.
    Passing this check lets an Admin *attempt* the write; it does not make the pool
    layer accept one. ``apply_debit`` and ``reverse_debit`` both raise
    ``PERIOD_IS_CLOSED`` for everybody, Admin included, and reopening a period is the
    mechanism D42 names as unbuilt -- it needs a reversing ledger entry type, a way back
    for four operations that currently refuse, and recursive cascading of the carried
    balance. So in practice an Admin edit succeeds here exactly when it moves no hours:
    an author reassignment, a non-billable row, or a log that never debited a period.
    An edit that would move hours in a closed period still fails, one layer down, with
    an explicit code. That is the honest boundary and it is tested as such.
    """
    if capabilities.is_admin:
        return

    for service_log in service_logs:
        period = service_log.debited_period

        if period is not None and period.status == ServicePeriodStatus.CLOSED:
            raise ServiceLogValidationError(CLOSED_PERIOD_IS_ADMIN_ONLY)


# ---------------------------------------------------------------------------
# Granting and revoking
# ---------------------------------------------------------------------------


def validate_grant_target(workspace_id, member_id):
    """Refuse granting capabilities to somebody who must not hold them.

    A GUEST is refused because both non-trivial capabilities are privilege escalation
    in a client's hands: ``can_delegate`` attributes technician work to whoever the
    GUEST names, and ``can_manage_others`` edits billing records. A non-member is
    refused because a grant to somebody outside the workspace is a row that means
    nothing and would start meaning something the day they are invited.

    Returns the ``WorkspaceMember``; raises ``ServiceLogValidationError`` otherwise.

    This is the *second* of the three GUEST layers. It keeps the stored data honest, but
    it is not what makes the system safe -- ``resolve_capabilities`` is, because it holds
    against rows this function never saw.
    """
    membership = WorkspaceMember.objects.filter(
        workspace_id=workspace_id, member_id=member_id, is_active=True
    ).first()

    if membership is None:
        raise ServiceLogValidationError(NOT_AN_ACTIVE_WORKSPACE_MEMBER)

    if membership.role <= ROLE_GUEST:
        raise ServiceLogValidationError(GUEST_CANNOT_HOLD_SERVICE_LOG_PERMISSIONS)

    return membership


@transaction.atomic
def set_member_capabilities(*, workspace_id, member_id, actor, **capabilities):
    """Grant or revoke capabilities for one member, auditing every transition.

    Only the keys present in ``capabilities`` are touched, so the endpoint can PATCH one
    flag without restating the other two.

    The row is **created on first grant and never deleted on revocation** -- revoking
    sets the flags to false. Deleting would detach the ``UPDATED`` audit entries from
    the entity they describe, and the entity identifier is what the audit endpoint
    filters by. A row with three false flags is the correct representation of "held
    something once, holds nothing now".

    Audited through ``save_with_config_activity`` / ``create_with_config_activity``, the
    same helpers the catalogues and the contract use, so this grant history is readable
    from the same endpoint as every other change that affects money. ``actor`` is
    required by those helpers: an audit row with no author is not an audit row.
    """
    validate_grant_target(workspace_id, member_id)

    requested = {name: bool(value) for name, value in capabilities.items() if name in CAPABILITY_FIELDS}

    permission = ServiceMemberPermission.objects.filter(
        workspace_id=workspace_id, member_id=member_id
    ).first()

    if permission is None:
        permission = ServiceMemberPermission(workspace_id=workspace_id, member_id=member_id, **requested)

        # A first grant of nothing is not an event. Creating a row of three falses would
        # put a CREATED entry in the trail describing an act that did not happen.
        if not any(requested.values()):
            return permission

        return create_with_config_activity(permission, actor=actor)

    for name, value in requested.items():
        setattr(permission, name, value)

    save_with_config_activity(permission, actor=actor)

    return permission


@transaction.atomic
def revoke_all_capabilities(*, workspace_id, member_id, actor):
    """Strip every capability from one member, auditing each one that was held.

    Called when a member is demoted to GUEST. **The demotion has already made the
    capabilities ineffective** -- ``resolve_capabilities`` gates on the live role -- so
    this is not what closes the escalation. What it does is stop the stored row from
    claiming something untrue, which matters because that row is what an admin screen
    displays and what the audit trail is read against. A row saying "may delegate" for
    somebody who cannot is a trap for the next person to read it.

    Returns the captured changes, empty when there was nothing to revoke -- so a
    demotion of somebody who never held a grant writes no audit noise.
    """
    permission = ServiceMemberPermission.objects.filter(
        workspace_id=workspace_id, member_id=member_id
    ).first()

    if permission is None:
        return {}

    for name in CAPABILITY_FIELDS:
        setattr(permission, name, False)

    return save_with_config_activity(permission, actor=actor)
