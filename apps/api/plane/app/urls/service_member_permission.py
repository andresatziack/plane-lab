# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.urls import path

from plane.app.views import (
    ServiceMemberPermissionEndpoint,
    ServiceMemberPermissionMeEndpoint,
)

# Workspace level, not project level: the capabilities supplement the workspace role, and a
# coordinator who logs on a technician's behalf does so wherever that technician works.
# Per-project grants would multiply the rows with nothing asking for it.
urlpatterns = [
    path(
        "workspaces/<str:slug>/service-member-permissions/",
        ServiceMemberPermissionEndpoint.as_view(),
        name="service-member-permissions",
    ),
    # Before the collection route's `<uuid:member_id>` sibling, because "me" would otherwise
    # never be reached if it were ever loosened to `<str:>`. Cheap ordering, and it removes
    # a class of bug rather than relying on the converter staying strict.
    path(
        "workspaces/<str:slug>/service-member-permissions/me/",
        ServiceMemberPermissionMeEndpoint.as_view(),
        name="service-member-permissions-me",
    ),
    # Addressed by member, not by row id: on a first grant there is no row yet. See the
    # endpoint docstring.
    path(
        "workspaces/<str:slug>/service-member-permissions/<uuid:member_id>/",
        ServiceMemberPermissionEndpoint.as_view(),
        name="service-member-permission",
    ),
]
