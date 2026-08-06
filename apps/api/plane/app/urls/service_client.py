# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.urls import path

from plane.app.views import ServiceClientViewSet, UserServiceClientEndpoint

urlpatterns = [
    path(
        "workspaces/<str:slug>/service-clients/",
        ServiceClientViewSet.as_view({"get": "list", "post": "create"}),
        name="service-clients",
    ),
    # Declared before the <uuid:pk> route is irrelevant here because the path
    # converters do not overlap, but keeping the static segment adjacent to the
    # collection route keeps the file readable.
    path(
        "workspaces/<str:slug>/service-clients/me/",
        UserServiceClientEndpoint.as_view(),
        name="user-service-clients",
    ),
    path(
        "workspaces/<str:slug>/service-clients/<uuid:pk>/",
        ServiceClientViewSet.as_view(
            {"get": "retrieve", "patch": "partial_update", "delete": "destroy"},
        ),
        name="service-client",
    ),
    path(
        "workspaces/<str:slug>/service-clients/<uuid:pk>/projects/",
        ServiceClientViewSet.as_view({"get": "projects"}),
        name="service-client-projects",
    ),
    path(
        "workspaces/<str:slug>/service-clients/<uuid:pk>/assign-projects/",
        ServiceClientViewSet.as_view({"post": "assign_projects"}),
        name="service-client-assign-projects",
    ),
]
