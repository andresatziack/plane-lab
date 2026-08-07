# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
from django.conf import settings
from django.db import models
from django.db.models import Q

# Module imports
from ..mixins import ChangeTrackerMixin
from .service_catalog import ServiceConfigEntity
from .workspace import WorkspaceBaseModel


class ServiceMemberPermission(ChangeTrackerMixin, WorkspaceBaseModel):
    """The elevated work log capabilities granted to one workspace member.

    Section 2 of the phase brief requires three capabilities to be grantable "a
    usuários específicos, sem precisar torná-los Admin do workspace". This fork has
    exactly three roles -- ``ADMIN = 20``, ``MEMBER = 15``, ``GUEST = 5`` -- and no
    custom roles, no permission schemes and no granular permissions, so there is
    nothing existing to hang them on. This model is that mechanism.

    **Its own model rather than three booleans on ``WorkspaceMember`` (decision D45),
    and both reasons are about cost that arrives later:**

    1. ``WorkspaceMember`` is a **core Plane model**, and
       ``plane/app/views/workspace/member.py`` is precisely the kind of file upstream
       edits often. Three columns there would be a permanent merge surface on the
       member-management path. Section 6 of the master context asks to prefer
       extension to modification of the core for this reason, and seven phases have
       been delivered without touching a core model.
    2. **Granting these is configuration that decides money, and it has to be
       audited.** Granting ``can_manage_others`` gives somebody the power to alter
       another technician's billing record; "who granted this, and when?" is a
       first-order forensic question the moment a work log turns up wrongly edited.
       A ``Service*`` model inherits ``ChangeTrackerMixin`` and writes to
       ``ServiceConfigActivity``, which section 6 of the master context defines as the
       trail for exactly that. ``WorkspaceMember`` is not a ``ServiceConfigEntity``
       and making it one would mean editing the core again.

    The user-facing shape is deliberately unchanged: **there is no new permissions
    screen.** The controls live on the existing workspace members screen and reach
    this model through its own endpoint. One place for the operator, no invasion of
    the core.

    **The three capabilities are independent, not hierarchical**, because they are
    three different axes: managing somebody else's log moves money (hours, hour type,
    service date), reassigning an author moves none, and delegating creates a record
    for a declared author. Free composition is correct.

    **But ``can_reassign_author`` is scoped to the logs the member may already edit**,
    and that scoping is what keeps the independence honest. Reassigning *somebody
    else's* work log is editing somebody else's record, even though the changed field
    moves no balance -- so without the scope, ``can_reassign_author`` would silently
    grant a slice of ``can_manage_others``. The resulting matrix:

    ===================================================  =============================
    author + ``can_reassign_author``                     reassigns their own
    ``can_manage_others`` + ``can_reassign_author``      reassigns anybody's
    ``can_manage_others`` alone                          edits anybody's, author fixed
    ``can_reassign_author`` alone                        reassigns only their own
    ===================================================  =============================

    The last row is the common case and the reason the capability exists on its own:
    "I logged this, but João did the work".

    **A workspace ADMIN holds all three implicitly and needs no row here.** The
    resolution is ``is_admin OR flag``, never a back-filled grant -- see
    ``plane.utils.service_permission.resolve_capabilities``. An Admin whose
    capabilities depended on a row would lose them to a bad back-fill, which is the
    failure mode of encoding an implication as data.

    **A GUEST can never hold any of them, and that is enforced in three places rather
    than one.** A GUEST with ``can_delegate`` is privilege escalation -- a client's own
    user creating work logs attributed to a technician -- and a GUEST with
    ``can_manage_others`` edits billing records. The layers, outermost first:

    * ``resolve_capabilities`` reads the **live role** and returns nothing for a GUEST
      or a non-member. This is the layer that actually guarantees safety, because it
      holds even if a row is stale, back-filled by hand, or written by a path nobody
      thought of;
    * the write path refuses to grant to a GUEST (``GUEST_CANNOT_HOLD_SERVICE_LOG_PERMISSIONS``);
    * demotion to GUEST **revokes** the row, so the stored state never lies about what
      somebody holds.

    A check constraint cannot express this: the role lives in ``workspace_members`` and
    PostgreSQL check constraints cannot read another table. That is exactly why the
    outermost layer is a read-time gate rather than DDL -- see the module docstring of
    ``plane.utils.service_permission``.

    Inherited from ``WorkspaceBaseModel``: ``workspace`` (required) and ``project``
    (nullable, unused here). The capabilities are **workspace scoped**, not project
    scoped, matching the role they supplement -- a coordinator who logs on a
    technician's behalf does so wherever that technician works, and per-project grants
    would multiply the rows without any request asking for it.
    """

    #: All three, because all three are grants of authority over a financial record.
    #: Every transition is written to ``ServiceConfigActivity`` with the actor who made
    #: it, which is the audit the class docstring gives as reason 2 for this model
    #: existing.
    TRACKED_FIELDS = ["can_manage_others", "can_delegate", "can_reassign_author"]
    CONFIG_ENTITY_NAME = ServiceConfigEntity.MEMBER_PERMISSION

    # CASCADE, matching ``WorkspaceMember.member``: a grant to a user who no longer
    # exists is meaningless, so it belongs in the bin alongside them. Unlike
    # ``ServiceLog.author`` this is not a billing record -- nothing on an invoice
    # depends on it -- so the DO_NOTHING reasoning that protects the author column does
    # not apply. ``User`` is not a ``SoftDeleteModel``, so the
    # ``soft_delete_related_objects`` catch-all that makes CASCADE hazardous for
    # catalogue links never runs for it.
    member = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="service_permissions",
    )

    # Edit and delete work logs authored by anyone. The capability that moves money:
    # changing duration, hour type, billing type or service date reverses and reapplies
    # the pool debit.
    can_manage_others = models.BooleanField(default=False)

    # Create a work log whose author is somebody else. Rule R8's two facts -- who did
    # the work and who typed it in -- become genuinely different people here, and
    # ``created_by`` is what records the second.
    can_delegate = models.BooleanField(default=False)

    # Change the author of an existing work log. **Scoped to the logs the member may
    # already edit** -- see the matrix in the class docstring. Moves no balance: who
    # performed the work does not change what it cost.
    can_reassign_author = models.BooleanField(default=False)

    def config_summary(self):
        """One line for the ``CREATED`` and ``DELETED`` rows of the audit trail.

        Lists the capabilities that are actually held, so a reader of the trail sees
        the grant rather than having to diff three booleans. "nenhuma" is a real state:
        a row whose three flags were all revoked is kept rather than deleted, precisely
        so the ``UPDATED`` entries that revoked them stay attached to something.
        """
        held = [name for name in self.TRACKED_FIELDS if getattr(self, name)]

        return f"Permissoes de apontamento: {', '.join(held) if held else 'nenhuma'}"

    class Meta:
        verbose_name = "Service Member Permission"
        verbose_name_plural = "Service Member Permissions"
        db_table = "service_member_permissions"
        ordering = ("-created_at",)

        # Uniqueness under soft delete follows the house pattern: unique_together
        # including deleted_at, plus a conditional UniqueConstraint for live rows.
        unique_together = [["workspace", "member", "deleted_at"]]

        constraints = [
            # ONE ROW PER MEMBER PER WORKSPACE. Two rows would be two answers to "may
            # this person delegate", and the resolution would depend on ordering --
            # which is the ambiguity D27 says must never be representable in the first
            # place. Same partial-index shape as ``WorkspaceMember``'s own uniqueness.
            models.UniqueConstraint(
                fields=["workspace", "member"],
                condition=Q(deleted_at__isnull=True),
                name="svc_member_perm_unique_when_deleted_at_null",
            ),
        ]

        indexes = [
            # The only read shape: "what may this member do in this workspace", asked
            # once per work log write. Name kept to 29 characters because Django's
            # models.E034 caps index names at 30.
            models.Index(fields=["workspace", "member"], name="svc_member_perm_ws_member_idx"),
        ]

    def __str__(self):
        return f"{self.member_id} <{self.workspace_id}> {self.config_summary()}"
