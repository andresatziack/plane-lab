# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
from django.db import transaction

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions import ROLE, allow_permission
from plane.app.serializers import ServiceIssueRequesterSerializer
from plane.db.models import Issue, IssueSubscriber, ProjectMember, ServiceIssueRequester

from ..base import BaseAPIView

#: The named refusals. UPPER_SNAKE, like every other error in this domain.
NOT_A_CLIENT_USER_OF_THIS_PROJECT = "NOT_A_CLIENT_USER_OF_THIS_PROJECT"


class ServiceIssueRequesterEndpoint(BaseAPIView):
    """Who asked for this work item. Section 5, criterion 15. D62.

    A technician opens the ticket inside the Cliente's project -- which makes the Cliente
    and the contract right by construction -- and records the client's own user who asked
    for it. Two things follow, and both are load bearing:

    1. **That user can see the work item in their portal.** In the recommended portal
       configuration they could see it anyway, because the project grants its clients full
       visibility. With that flag off, this attribution is the *only* thing that makes the
       ticket visible to the person who asked for it -- see ``client_may_reach_issue``.
    2. **That user is notified**, through the native ``IssueSubscriber``. Reusing Plane's own
       subscription is the point: notification, digest and unsubscribe already work, and a
       parallel notification path for one attribution would be a second thing to maintain
       and a second thing to get wrong.

    ADMIN and MEMBER only. Recording who asked is a technician's action, and a client
    naming a *different* client user as the requester of a ticket would be one client user
    granting another visibility -- a decision that belongs with the operation, not with the
    client.
    """

    def _issue(self, slug, project_id, issue_id):
        return (
            Issue.objects.filter(workspace__slug=slug, project_id=project_id, pk=issue_id)
            .select_related("project")
            .first()
        )

    def _row(self, issue_id):
        return ServiceIssueRequester.objects.filter(issue_id=issue_id).select_related("requester").first()

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def get(self, request, slug, project_id, issue_id):
        row = self._row(issue_id)

        if row is None:
            # 200 with an explicit null rather than 404: "this ticket has no recorded
            # requester" is an answer, and the common one. A 404 would make the frontend
            # treat an ordinary state as an error.
            return Response({"requester": None}, status=status.HTTP_200_OK)

        return Response(ServiceIssueRequesterSerializer(row).data, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def post(self, request, slug, project_id, issue_id):
        """Record or replace the requester.

        Idempotent by ``update_or_create`` rather than refusing a second call: correcting a
        misrecorded requester is an ordinary operation, and forcing a DELETE first would
        leave a window in which the ticket has no requester at all.
        """
        issue = self._issue(slug, project_id, issue_id)

        if issue is None:
            return Response(
                {"error": "The required object does not exist."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = ServiceIssueRequesterSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        requester_id = serializer.validated_data["requester_id"]

        # The requester has to be a client user *of this project*. Without this, the
        # attribution would be a way to hand any workspace member visibility of a work item
        # in a project they are not in -- through a route whose whole purpose is granting
        # visibility. 400 and not 403 per D47: the actor is entitled to record a requester,
        # and it is the target the payload names that is ineligible.
        if not ProjectMember.objects.filter(
            project_id=project_id,
            member_id=requester_id,
            is_active=True,
            role=ROLE.GUEST.value,
        ).exists():
            return Response(
                {"error": NOT_A_CLIENT_USER_OF_THIS_PROJECT},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            row, _ = ServiceIssueRequester.objects.update_or_create(
                issue_id=issue_id,
                defaults={
                    "requester_id": requester_id,
                    "project_id": project_id,
                    "workspace_id": issue.workspace_id,
                },
            )

            # Native subscription, so notification is Plane's and not ours.
            IssueSubscriber.objects.get_or_create(
                issue_id=issue_id,
                subscriber_id=requester_id,
                defaults={"project_id": project_id, "workspace_id": issue.workspace_id},
            )

        return Response(
            ServiceIssueRequesterSerializer(self._row(issue_id)).data,
            status=status.HTTP_200_OK,
        )

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def delete(self, request, slug, project_id, issue_id):
        """Remove the attribution.

        **The subscription is deliberately left in place.** Unsubscribing is the requester's
        own decision and Plane already gives them the control; removing a correction to the
        attribution should not silently stop somebody's notifications. Losing the
        attribution does remove the visibility it granted, which is the part that has to
        happen.
        """
        row = self._row(issue_id)

        if row is None:
            return Response(status=status.HTTP_204_NO_CONTENT)

        row.delete()

        return Response(status=status.HTTP_204_NO_CONTENT)
