# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.urls import path

from plane.app.views import (
    ServiceClassificationWindowViewSet,
    ServiceHolidayViewSet,
)

# Explicit path() entries, one file per resource, following the house convention -- there
# is no DRF router in this codebase.
#
# Workspace level, like the catalogues: holidays and windows are a commercial parameter of
# the workspace and are the same for every technician, because they are what the contracts
# with clients say (D14 and D16).
urlpatterns = [
    path(
        "workspaces/<str:slug>/service-holidays/",
        ServiceHolidayViewSet.as_view({"get": "list", "post": "create"}),
        name="service-holidays",
    ),
    # Before the <uuid:pk> route, so "calendar" is never parsed as an id.
    path(
        "workspaces/<str:slug>/service-holidays/calendar/",
        ServiceHolidayViewSet.as_view({"get": "calendar"}),
        name="service-holiday-calendar",
    ),
    path(
        "workspaces/<str:slug>/service-holidays/bulk-import/",
        ServiceHolidayViewSet.as_view({"post": "bulk_import"}),
        name="service-holiday-bulk-import",
    ),
    path(
        "workspaces/<str:slug>/service-holidays/<uuid:pk>/",
        ServiceHolidayViewSet.as_view(
            {"get": "retrieve", "patch": "partial_update", "delete": "destroy"},
        ),
        name="service-holiday",
    ),
    path(
        "workspaces/<str:slug>/service-classification-windows/",
        ServiceClassificationWindowViewSet.as_view({"get": "list", "post": "create"}),
        name="service-classification-windows",
    ),
    # The health indicator's source. Read only: coverage is derived, never set.
    path(
        "workspaces/<str:slug>/service-classification-windows/coverage/",
        ServiceClassificationWindowViewSet.as_view({"get": "coverage"}),
        name="service-classification-window-coverage",
    ),
    path(
        "workspaces/<str:slug>/service-classification-windows/<uuid:pk>/",
        ServiceClassificationWindowViewSet.as_view(
            {"get": "retrieve", "patch": "partial_update", "delete": "destroy"},
        ),
        name="service-classification-window",
    ),
]
