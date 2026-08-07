# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.urls import path

from plane.app.views import (
    IssueServiceAllowanceEndpoint,
    ServiceIssueAllowanceAlertPanelEndpoint,
    ServiceIssueAllowanceCloseEndpoint,
    ServiceIssueAllowanceDismissAlertEndpoint,
    ServiceIssueAllowanceOverageRateEndpoint,
)

urlpatterns = [
    # The allowance is addressed through its work item, not by its own id, because that is
    # how it is created: there is exactly one per work item, so the work item *is* the
    # identifier. Creating one through a collection route would invite a second.
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/issues/<uuid:issue_id>/service-allowance/",
        IssueServiceAllowanceEndpoint.as_view(),
        name="issue-service-allowance",
    ),
    # Closing is addressed by allowance id rather than by work item: it is an operation on
    # a specific allowance, and an admin closing one from the alert panel has the id and
    # not necessarily the project.
    path(
        "workspaces/<str:slug>/service-issue-allowances/<uuid:pk>/close/",
        ServiceIssueAllowanceCloseEndpoint.as_view(),
        name="service-issue-allowance-close",
    ),
    # The rate an hour past this allowance costs. Its own route: a renegotiation changes the
    # rate without crediting hours.
    path(
        "workspaces/<str:slug>/service-issue-allowances/<uuid:pk>/overage-rate/",
        ServiceIssueAllowanceOverageRateEndpoint.as_view(),
        name="service-issue-allowance-overage-rate",
    ),
    # Dismissing an allowance alert. D53 -- the route Phase 5 could not offer, because the
    # dismissal table had a mandatory foreign key to a contract period.
    path(
        "workspaces/<str:slug>/service-issue-allowances/<uuid:pk>/dismiss-alert/",
        ServiceIssueAllowanceDismissAlertEndpoint.as_view(),
        name="service-issue-allowance-dismiss-alert",
    ),
    path(
        "workspaces/<str:slug>/service-issue-allowance-alerts/",
        ServiceIssueAllowanceAlertPanelEndpoint.as_view(),
        name="service-issue-allowance-alerts",
    ),
]
