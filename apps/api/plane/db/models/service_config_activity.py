# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
from django.db import models

# Module imports
from .workspace import WorkspaceBaseModel


class ServiceConfigActivity(WorkspaceBaseModel):
    """Append-only trail of changes to configuration that affects money.

    The question this exists to answer is "why did the March invoice use 1.5 and
    the April one 1.8?". ``updated_by`` / ``updated_at`` on the catalogue row
    cannot answer it, because they lose the previous value -- and the previous
    value is the entire point of a financial audit trail.

    Scoped to *all* configuration that feeds a calculation, not just this phase's
    two catalogues: the contract phase (contracted hours, accrual cap, overage
    hour rate) and the pricing phase (client base hour rate, absolute overrides)
    have the identical need, and restricting this model now would only cause a
    second audit table to appear later.

    Not a reuse of ``IssueActivity``: that model is a ``ProjectBaseModel`` keyed to
    a work item, and these events belong to workspace level configuration. What is
    reused is its *shape* -- field / old_value / new_value / actor -- which is what
    section 6 of the master context asks for.

    SECURITY: this table holds the history of multipliers and, from the pricing
    phase on, of prices. That is commercial intelligence. It must never be
    reachable by a GUEST, directly or through any listing endpoint. The read
    endpoint is workspace ADMIN only, and there is a regression test asserting 403
    for both GUEST and MEMBER.
    """

    # Which kind of entity changed. See ``ServiceConfigEntity`` for the constants.
    entity_name = models.CharField(max_length=255)

    # The changed row's primary key, as a bare UUID rather than a ForeignKey.
    #
    # Two reasons, and the second is what makes append-only actually hold:
    #
    # 1. One table serves several entity types, so a single FK is impossible. This
    #    is the established house pattern -- DeployBoard, Notification, PageLog,
    #    UserFavorite and RecentVisit all pair entity_name/entity_type with
    #    entity_identifier. django.contrib.contenttypes is not used anywhere in
    #    this repository for this and is not introduced here.
    #
    # 2. A ForeignKey would expose this trail to `soft_delete_related_objects`,
    #    which walks reverse relations on delete and, in its catch-all branch,
    #    soft deletes the referring rows. Soft deleting an hour type would then
    #    erase that hour type's audit history -- exactly the record being kept. A
    #    bare UUID has no reverse relation to walk.
    entity_identifier = models.UUIDField()

    field_name = models.CharField(max_length=255)

    # Values are text because they cross Decimal, bool and enum. Serialisation is
    # centralised in plane.utils.service_catalog so that every phase writes them
    # the same way and the history stays comparable: Decimal quantised to the
    # field's scale ("1.50", never "1.5"), booleans lowercase, enums as stored.
    #
    # old_value is nullable for the genuine case of a field that was NULL and
    # gained a value -- the pricing phase's optional override going from unset to
    # 350.00. It does not mean "creation": this table records changes to tracked
    # fields only. Creation and deletion are already answered by created_by /
    # created_at / deleted_at on the entity itself, and ChangeTrackerMixin
    # structurally cannot emit a creation event, since a new instance is born
    # holding its final values.
    old_value = models.TextField(null=True, blank=True)
    new_value = models.TextField(null=True, blank=True)

    # SET_NULL, never CASCADE: deleting a user must not delete audit rows, which
    # would defeat append-only. Nullable at the database level for that reason
    # only -- the service layer refuses to write a row without an actor, because
    # crum's get_current_user() returns None inside management commands, data
    # migrations and Celery tasks, and BaseModel.save() then blanks created_by
    # without raising.
    actor = models.ForeignKey(
        "db.User",
        on_delete=models.SET_NULL,
        null=True,
        related_name="service_config_activities",
    )

    class Meta:
        verbose_name = "Service Config Activity"
        verbose_name_plural = "Service Config Activities"
        db_table = "service_config_activities"
        ordering = ("-created_at",)
        indexes = [
            # Drill-down: "every change to this specific option, newest first".
            models.Index(
                fields=["workspace", "entity_name", "entity_identifier", "created_at"],
                name="svc_cfg_activity_entity_idx",
            ),
            # The unfiltered listing and date-range queries. A separate index is
            # required rather than a nicety: with entity_name and entity_identifier
            # sitting between workspace and created_at, the composite index above
            # cannot supply the ordering when no entity filter is given, and
            # PostgreSQL has no index skip scan. Two genuinely different read
            # shapes, and the write cost is irrelevant on an append-only table
            # that grows only when an admin edits configuration.
            models.Index(fields=["workspace", "-created_at"], name="svc_cfg_activity_recent_idx"),
        ]

    def __str__(self):
        return f"{self.entity_name} {self.field_name} <{self.entity_identifier}>"
