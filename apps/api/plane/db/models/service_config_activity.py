# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
from django.db import models
from django.db.models import Q

# Module imports
from .workspace import WorkspaceBaseModel


class ServiceConfigVerb(models.TextChoices):
    """What happened to the configuration row.

    Declared at module level, not nested, because the check constraint in ``Meta``
    references these values and a class nested in the model is not yet in scope while
    ``Meta`` is being evaluated. Reachable as ``ServiceConfigActivity.Verb``.
    """

    CREATED = "created", "Created"
    UPDATED = "updated", "Updated"
    DELETED = "deleted", "Deleted"


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

    **Three verbs, and creation and deletion are not optional extras.** When this table
    was introduced it recorded only field edits, on the reasoning that creation and
    deletion were already answered by ``created_by`` and ``deleted_at`` on the entity
    itself. The calendar phase overturned that, because for its two entities the
    relationship inverts completely:

    * For a catalogue option, the financial event is EDITING the multiplier. The option
      existing is not itself a price.
    * For a holiday or a classification window, CREATING and DELETING **are** the
      financial events. Registering a holiday on 15/03 moves every work log that day from
      1.0 to 2.0 and doubles the invoice. Recording only edits would record precisely
      what matters least in those two tables.

    And the entity's own columns are not a substitute in practice. Someone reconstructing
    a disputed invoice has to read ONE place; if the holiday's creation exists only on the
    holiday row, and its deletion only in ``all_objects``, the audit endpoint does not
    show it and the endpoint stops serving its purpose.

    EXTENSION POINT -- the contract and pricing phases must use **all three verbs**.
    "When was this 30h/month contract created, and by whom?" and "when was this hour rate
    registered?" are first-order questions there, exactly as they are here. Each audited
    model supplies its own one-line description through ``config_summary()``; see
    ``plane.utils.service_catalog`` for the helpers that write the rows.

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

    Verb = ServiceConfigVerb

    # What happened. NOT nullable, and defaulted to `updated`.
    #
    # `updated` as the default is not a guess for existing rows: before this column
    # existed, every row in this table came from ChangeTrackerMixin, which only fires on
    # a change to an already-saved instance. So "updated" is the correct value for all of
    # them, and the back-fill in migration 0127 is a statement of fact rather than an
    # assumption. A nullable audit column would have been worse than useless -- it would
    # force every consumer to branch on null, and the null would encode "probably an
    # edit", which is the opposite of what an audit trail is for.
    verb = models.CharField(
        max_length=20,
        choices=ServiceConfigVerb.choices,
        default=ServiceConfigVerb.UPDATED,
    )

    # Nullable, because creation and deletion do not concern a single field.
    #
    # See the check constraint in Meta for the three shapes this table accepts.
    field_name = models.CharField(max_length=255, null=True, blank=True)

    # Values are text because they cross Decimal, bool and enum. Serialisation is
    # centralised in plane.utils.service_catalog so that every phase writes them
    # the same way and the history stays comparable: Decimal quantised to the
    # field's scale ("1.50", never "1.5"), booleans lowercase, enums as stored.
    #
    # For `updated`, the two sides of one field's change. For `created`, new_value holds
    # a human-readable summary of the row that appeared and old_value is null. For
    # `deleted`, the reverse.
    #
    # Both stay nullable at the database level because two of the three verbs legitimately
    # leave one of them empty; which one is enforced by the check constraint below rather
    # than by the column.
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
        constraints = [
            # The three shapes this table accepts, in DDL. An audit trail that can hold an
            # incoherent row -- a creation carrying a field name, an update with no old
            # value -- cannot be read with confidence, and confidence is the only thing it
            # produces.
            #
            # KNOWN CONSEQUENCE, and the reason it is written here rather than discovered
            # later: requiring old_value and new_value on `updated` is safe for every
            # field tracked today, because all of them are non-nullable model fields and
            # therefore always serialise to a string. A future phase that tracks a
            # NULLABLE field -- the pricing phase's optional absolute override is the
            # obvious candidate -- would serialise its "unset" side to None and violate
            # this constraint at write time. That phase must either exclude the field from
            # TRACKED_FIELDS, serialise unset to a sentinel, or relax this constraint
            # deliberately. It is a visible trade, not an oversight.
            models.CheckConstraint(
                condition=(
                    Q(
                        verb=ServiceConfigVerb.UPDATED,
                        field_name__isnull=False,
                        old_value__isnull=False,
                        new_value__isnull=False,
                    )
                    | Q(
                        verb=ServiceConfigVerb.CREATED,
                        field_name__isnull=True,
                        old_value__isnull=True,
                        new_value__isnull=False,
                    )
                    | Q(
                        verb=ServiceConfigVerb.DELETED,
                        field_name__isnull=True,
                        old_value__isnull=False,
                        new_value__isnull=True,
                    )
                ),
                name="svc_cfg_activity_shape_matches_verb",
            ),
        ]
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
        # field_name is null for created and deleted, so it cannot be assumed here.
        subject = self.field_name or self.entity_name
        return f"{self.entity_name} {self.verb} {subject} <{self.entity_identifier}>"
