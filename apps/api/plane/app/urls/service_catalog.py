# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.urls import path

from plane.app.views import (
    ServiceBillingTypeViewSet,
    ServiceConfigActivityEndpoint,
    ServiceHourTypeViewSet,
)

urlpatterns = [
    path(
        "workspaces/<str:slug>/service-hour-types/",
        ServiceHourTypeViewSet.as_view({"get": "list", "post": "create"}),
        name="service-hour-types",
    ),
    path(
        "workspaces/<str:slug>/service-hour-types/<uuid:pk>/",
        ServiceHourTypeViewSet.as_view(
            {"get": "retrieve", "patch": "partial_update", "delete": "destroy"},
        ),
        name="service-hour-type",
    ),
    path(
        "workspaces/<str:slug>/service-hour-types/<uuid:pk>/mark-default/",
        ServiceHourTypeViewSet.as_view({"post": "mark_default"}),
        name="service-hour-type-mark-default",
    ),
    path(
        "workspaces/<str:slug>/service-billing-types/",
        ServiceBillingTypeViewSet.as_view({"get": "list", "post": "create"}),
        name="service-billing-types",
    ),
    path(
        "workspaces/<str:slug>/service-billing-types/<uuid:pk>/",
        ServiceBillingTypeViewSet.as_view(
            {"get": "retrieve", "patch": "partial_update", "delete": "destroy"},
        ),
        name="service-billing-type",
    ),
    path(
        "workspaces/<str:slug>/service-billing-types/<uuid:pk>/mark-default/",
        ServiceBillingTypeViewSet.as_view({"post": "mark_default"}),
        name="service-billing-type-mark-default",
    ),
    # Read only, on purpose. No POST, PATCH or DELETE is routed for the audit trail.
    path(
        "workspaces/<str:slug>/service-config-activities/",
        ServiceConfigActivityEndpoint.as_view(),
        name="service-config-activities",
    ),
]
