# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
from django.db.models import Count, Q

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions import ROLE, allow_permission
from plane.app.serializers import (
    ProjectLiteWithServiceClientSerializer,
    ServiceClientLiteSerializer,
    ServiceClientProjectAssignSerializer,
    ServiceClientSerializer,
)
from plane.db.models import Project, ServiceClient, Workspace
from plane.utils.service_log import validate_service_client_change

from ..base import BaseAPIView, BaseViewSet


class ServiceClientViewSet(BaseViewSet):
    """Workspace level CRUD for client companies.

    Reads are open to admins and members: technicians need client names to
    display, filter and group work items. Writes are restricted to workspace
    admins.
    """

    serializer_class = ServiceClientSerializer
    model = ServiceClient
    search_fields = ["name", "trade_name", "tax_id"]

    def get_queryset(self):
        return (
            ServiceClient.objects.filter(workspace__slug=self.kwargs.get("slug"))
            .annotate(
                # Count live projects only. Soft deleted projects still reference
                # the client in the database, but they must not be shown to the
                # admin as if they were active links.
                project_count=Count("projects", filter=Q(projects__deleted_at__isnull=True), distinct=True)
            )
            .select_related("workspace")
        )

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def list(self, request, slug):
        service_clients = self.get_queryset()

        # The admin panel needs inactive clients too; the work item filter
        # dropdown does not. Opt in explicitly rather than guessing from the
        # caller's role.
        if request.GET.get("only_active", "false").lower() == "true":
            service_clients = service_clients.filter(is_active=True)

        return Response(
            ServiceClientSerializer(service_clients, many=True).data,
            status=status.HTTP_200_OK,
        )

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def retrieve(self, request, slug, pk):
        service_client = self.get_queryset().filter(pk=pk).first()

        if not service_client:
            return Response(
                {"error": "The required object does not exist."},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(ServiceClientSerializer(service_client).data, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def create(self, request, slug):
        workspace = Workspace.objects.get(slug=slug)

        serializer = ServiceClientSerializer(data=request.data, context={"workspace_id": workspace.id})

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        serializer.save(workspace_id=workspace.id)

        # Re-read through get_queryset so the response carries project_count.
        service_client = self.get_queryset().filter(pk=serializer.data["id"]).first()
        return Response(
            ServiceClientSerializer(service_client).data,
            status=status.HTTP_201_CREATED,
        )

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def partial_update(self, request, slug, pk):
        workspace = Workspace.objects.get(slug=slug)
        service_client = ServiceClient.objects.filter(workspace__slug=slug, pk=pk).first()

        if not service_client:
            return Response(
                {"error": "The required object does not exist."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = ServiceClientSerializer(
            service_client,
            data=request.data,
            partial=True,
            context={"workspace_id": workspace.id},
        )

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        serializer.save()

        service_client = self.get_queryset().filter(pk=pk).first()
        return Response(ServiceClientSerializer(service_client).data, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def destroy(self, request, slug, pk):
        service_client = ServiceClient.objects.filter(workspace__slug=slug, pk=pk).first()

        if not service_client:
            return Response(
                {"error": "The required object does not exist."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # A client that any project still points at cannot be deleted, only
        # deactivated. all_objects is deliberate: a soft deleted project still
        # holds the foreign key, and the periodic hard_delete task would later
        # fail with an IntegrityError against the DO_NOTHING constraint if the
        # client were removed while such a row survived.
        linked_project_count = Project.all_objects.filter(service_client_id=pk).count()

        if linked_project_count:
            return Response(
                {
                    "error": "This client has linked projects and cannot be deleted. Deactivate it instead.",
                    "linked_project_count": linked_project_count,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        service_client.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def projects(self, request, slug, pk):
        """List the projects linked to this client."""
        projects = Project.objects.filter(workspace__slug=slug, service_client_id=pk).select_related("service_client")
        return Response(
            ProjectLiteWithServiceClientSerializer(projects, many=True).data,
            status=status.HTTP_200_OK,
        )

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def assign_projects(self, request, slug, pk):
        """Bulk assign projects to this client.

        Exists so that the projects that predate this feature can be attributed
        without editing them one at a time.

        The two feature flags are applied only when the caller sends them. The
        master context requires that turning on guest_view_all_features be
        suggested to the admin rather than done silently, so the decision stays
        with the UI and this endpoint never infers it.
        """
        service_client = ServiceClient.objects.filter(workspace__slug=slug, pk=pk).first()

        if not service_client:
            return Response(
                {"error": "The required object does not exist."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = ServiceClientProjectAssignSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        project_ids = serializer.validated_data["project_ids"]

        # Only projects of this workspace, so a crafted payload cannot reach
        # across workspaces.
        projects = Project.objects.filter(workspace__slug=slug, id__in=project_ids)
        found_ids = {str(project_id) for project_id in projects.values_list("id", flat=True)}
        missing = [str(project_id) for project_id in project_ids if str(project_id) not in found_ids]

        if missing:
            return Response(
                {"error": "Some projects do not belong to this workspace.", "project_ids": missing},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Phase 1's acceptance criterion 11, on the write path the phase brief did not
        # know about. This action assigns through a queryset `update()`, which skips
        # serializers, `save()` and signals -- so the guard in
        # `ProjectSerializer.validate_service_client` cannot see it, and a client
        # reassignment here would silently reattribute already-invoiced hours.
        #
        # Reported per project rather than as one opaque error: an admin bulk assigning
        # twenty legacy projects needs to know which of them refused and why.
        blocked = []

        for project in projects:
            error_code = validate_service_client_change(project, service_client)

            if error_code:
                blocked.append({"project_id": str(project.id), "error": error_code})

        if blocked:
            return Response(
                {
                    "error": "Some projects already have work logs under another client.",
                    "projects": blocked,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        update_fields = {"service_client_id": service_client.id}

        if "guest_view_all_features" in serializer.validated_data:
            update_fields["guest_view_all_features"] = serializer.validated_data["guest_view_all_features"]

        if "is_time_tracking_enabled" in serializer.validated_data:
            update_fields["is_time_tracking_enabled"] = serializer.validated_data["is_time_tracking_enabled"]

        updated = projects.update(**update_fields)

        return Response(
            {
                "updated_project_count": updated,
                "projects": ProjectLiteWithServiceClientSerializer(
                    Project.objects.filter(workspace__slug=slug, id__in=project_ids).select_related("service_client"),
                    many=True,
                ).data,
            },
            status=status.HTTP_200_OK,
        )


class UserServiceClientEndpoint(BaseAPIView):
    """The clients the requesting user belongs to, derived from project membership.

    There is deliberately no user-to-client association table. The link is
    derived from the user's membership in the projects of those clients, using
    Plane's native access mechanism, so there is exactly one source of truth
    about access. See docs/worklog/DECISOES.md, D19.
    """

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def get(self, request, slug):
        service_clients = (
            ServiceClient.objects.filter(
                workspace__slug=slug,
                projects__project_projectmember__member=request.user,
                projects__project_projectmember__is_active=True,
                projects__archived_at__isnull=True,
                projects__deleted_at__isnull=True,
            )
            # distinct() is required: projects is a reverse relation, so a client
            # with two projects the user belongs to would otherwise repeat.
            .distinct()
        )

        # The lite serializer is used on purpose. project_count would be counted
        # over the membership-filtered join and would therefore mean "projects of
        # this client that I can see" rather than "projects of this client",
        # which is a misleading number to publish. Billing configuration has no
        # business in this response either.
        return Response(
            ServiceClientLiteSerializer(service_clients, many=True).data,
            status=status.HTTP_200_OK,
        )
