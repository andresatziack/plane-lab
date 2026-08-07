# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.urls import path

from plane.app.views import (
    ServiceBillingConsolidationEndpoint,
    ServiceClientHourTypeRateViewSet,
    ServiceClientPriceViewSet,
    ServiceEffectiveRateTableEndpoint,
    ServiceLogExportEndpoint,
)

urlpatterns = [
    # Price sheets are nested under their client: a vigency has no meaning apart from whose
    # prices it is, and nesting keeps the workspace and the client in the path rather than in
    # a body that could name a different one.
    path(
        "workspaces/<str:slug>/service-clients/<uuid:service_client_id>/prices/",
        ServiceClientPriceViewSet.as_view({"get": "list", "post": "create"}),
        name="service-client-prices",
    ),
    path(
        "workspaces/<str:slug>/service-clients/<uuid:service_client_id>/prices/<uuid:pk>/",
        ServiceClientPriceViewSet.as_view({"patch": "partial_update", "delete": "destroy"}),
        name="service-client-price",
    ),
    # Overrides are nested under the sheet, mirroring the model: an override belongs to one
    # vigency, which is what makes each vigency a complete, self-contained price sheet.
    path(
        "workspaces/<str:slug>/service-client-prices/<uuid:price_id>/hour-type-rates/",
        ServiceClientHourTypeRateViewSet.as_view({"post": "create"}),
        name="service-client-hour-type-rates",
    ),
    path(
        "workspaces/<str:slug>/service-client-prices/<uuid:price_id>/hour-type-rates/<uuid:pk>/",
        ServiceClientHourTypeRateViewSet.as_view({"patch": "partial_update", "delete": "destroy"}),
        name="service-client-hour-type-rate",
    ),
    # The derived table. Its own route because the work log form reads it without needing the
    # vigency history behind it.
    path(
        "workspaces/<str:slug>/service-clients/<uuid:service_client_id>/effective-rates/",
        ServiceEffectiveRateTableEndpoint.as_view(),
        name="service-client-effective-rates",
    ),
    # Fills the `issue_worklogs` choice that has been on ExporterHistory with no handler.
    # POST queues, GET lists this type's history.
    path(
        "workspaces/<str:slug>/service-log-exports/",
        ServiceLogExportEndpoint.as_view(),
        name="service-log-exports",
    ),
    path(
        "workspaces/<str:slug>/service-billing-consolidation/",
        ServiceBillingConsolidationEndpoint.as_view(),
        name="service-billing-consolidation",
    ),
]
