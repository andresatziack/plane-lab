# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.urls import path

from plane.app.views import (
    ServiceAttentionReportEndpoint,
    ServiceBillingReportEndpoint,
    ServiceConsumptionReportEndpoint,
    ServiceOperationalReportEndpoint,
    ServiceReportLogsEndpoint,
)

urlpatterns = [
    # Every report is workspace scoped and takes its selection from query parameters, which
    # are a `ServiceLogFilterSet` -- the same descriptor the drill-down and the CSV export
    # consume. See D50.
    path(
        "workspaces/<str:slug>/service-reports/consumption/",
        ServiceConsumptionReportEndpoint.as_view(),
        name="service-report-consumption",
    ),
    path(
        "workspaces/<str:slug>/service-reports/operational/",
        ServiceOperationalReportEndpoint.as_view(),
        name="service-report-operational",
    ),
    path(
        "workspaces/<str:slug>/service-reports/attention/",
        ServiceAttentionReportEndpoint.as_view(),
        name="service-report-attention",
    ),
    # The only ADMIN-only report: every number in it is money.
    path(
        "workspaces/<str:slug>/service-reports/billing/",
        ServiceBillingReportEndpoint.as_view(),
        name="service-report-billing",
    ),
    # THE drill-down. One route, not one per chart: the list and the number come from the
    # same descriptor, which is what makes criteria 1 and 8 a single guarantee.
    path(
        "workspaces/<str:slug>/service-reports/logs/",
        ServiceReportLogsEndpoint.as_view(),
        name="service-report-logs",
    ),
]
