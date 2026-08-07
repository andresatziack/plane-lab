# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.urls import path

from plane.app.views import ServiceIssueRequesterEndpoint

# One route, three verbs. Routed under the work item because the attribution has no meaning
# apart from it, and the project therefore comes from the URL and never from the payload --
# the same rule every other issue sub-resource in this fork follows.
urlpatterns = [
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/issues/<uuid:issue_id>/service-requester/",
        ServiceIssueRequesterEndpoint.as_view(),
        name="service-issue-requester",
    ),
]
