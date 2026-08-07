# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
from django.conf import settings
from django.db import models

# Module imports
from .project import ProjectBaseModel


class ServiceIssueRequester(ProjectBaseModel):
    """The client's own user on whose behalf a technician opened a work item.

    Section 5 of the phase brief: the technician opens the ticket inside the Cliente's
    project -- so the Cliente and the contract are already right by construction -- and
    records *who asked*, "para que aquele usuário veja o chamado no portal dele e seja
    notificado". Two effects, and this model is what makes both possible.

    **Its own model rather than a column on ``Issue``, decision D62.** ``Issue`` is the most
    heavily edited model in core Plane, and a nullable foreign key on it would be a
    permanent merge surface on the single hottest table in the schema for the sake of one
    optional attribution. The same reasoning D45 applied to ``WorkspaceMember``: eight
    phases have been delivered without adding a column to a core model, and this one does
    not need to be the exception.

    **Not a ``ServiceConfigEntity`` and no ``ChangeTrackerMixin``**, unlike every other
    ``Service*`` model that carries a decision. Recording who asked for a ticket decides no
    money -- it changes no rate, no route, no multiplier and no balance -- so
    ``ServiceConfigActivity`` is the wrong trail for it (master context, section 6: that
    trail is for configuration that affects money). The inherited audit columns answer
    "who recorded this and when", which is the whole forensic question here.

    **One requester per work item**, hence ``OneToOneField``. A ticket is opened on behalf
    of one person; several interested parties are subscribers, which is a native Plane
    concept and already works. Modelling this as many-to-many would blur the two and leave
    "who asked for this" without an answer.

    The requester is *not* the author of anything and *not* the creator of the work item.
    ``created_by`` on this row is the technician who recorded the attribution;
    ``Issue.created_by`` stays the technician who opened the ticket. R8's separation of
    "who did it" from "who typed it" is the same distinction one level down, and collapsing
    them here would misreport who opened the ticket.
    """

    issue = models.OneToOneField(
        "db.Issue",
        on_delete=models.CASCADE,
        related_name="service_requester",
    )

    # DO_NOTHING, matching `ServiceLog.author` and for the same reason: every other option
    # routes through `soft_delete_related_objects`, whose catch-all branch would soft delete
    # this row -- and with it the attribution -- the moment the user was deactivated. A
    # client's employee leaving the company must not erase who asked for a ticket that is
    # already invoiced.
    requester = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.DO_NOTHING,
        related_name="service_requested_issues",
    )

    class Meta:
        db_table = "service_issue_requesters"
        verbose_name = "Service Issue Requester"
        verbose_name_plural = "Service Issue Requesters"
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.issue_id} <- {self.requester_id}"
