# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.urls import path

from plane.app.views import (
    IssueServicePoolEndpoint,
    ServiceContractAlertPanelEndpoint,
    ServiceContractPeriodViewSet,
    ServiceContractViewSet,
)

urlpatterns = [
    path(
        "workspaces/<str:slug>/service-contracts/",
        ServiceContractViewSet.as_view({"get": "list", "post": "create"}),
        name="service-contracts",
    ),
    path(
        "workspaces/<str:slug>/service-contracts/<uuid:pk>/",
        ServiceContractViewSet.as_view(
            {"get": "retrieve", "patch": "partial_update", "delete": "destroy"},
        ),
        name="service-contract",
    ),
    path(
        "workspaces/<str:slug>/service-contracts/<uuid:pk>/periods/",
        ServiceContractViewSet.as_view({"get": "periods"}),
        name="service-contract-periods",
    ),
    path(
        "workspaces/<str:slug>/service-contracts/<uuid:pk>/renew/",
        ServiceContractViewSet.as_view({"post": "renew"}),
        name="service-contract-renew",
    ),
    path(
        "workspaces/<str:slug>/service-contracts/<uuid:pk>/successor/",
        ServiceContractViewSet.as_view({"post": "create_successor"}),
        name="service-contract-successor",
    ),
    # A period is never created or deleted through the API. Materialisation belongs to
    # the domain layer, because creating one has to grant its monthly quota in the same
    # transaction, and deleting one would orphan the ledger rows that reference it.
    path(
        "workspaces/<str:slug>/service-contract-periods/<uuid:pk>/",
        ServiceContractPeriodViewSet.as_view({"get": "retrieve"}),
        name="service-contract-period",
    ),
    path(
        "workspaces/<str:slug>/service-contract-periods/<uuid:pk>/close/",
        ServiceContractPeriodViewSet.as_view({"post": "close"}),
        name="service-contract-period-close",
    ),
    # Read before the irreversible write. GET, because it changes nothing -- see the
    # method's docstring for why the confirmation needs it.
    path(
        "workspaces/<str:slug>/service-contract-periods/<uuid:pk>/overage-preview/",
        ServiceContractPeriodViewSet.as_view({"get": "overage_preview"}),
        name="service-contract-period-overage-preview",
    ),
    path(
        "workspaces/<str:slug>/service-contract-periods/<uuid:pk>/contracted-hours/",
        ServiceContractPeriodViewSet.as_view({"post": "set_contracted_hours"}),
        name="service-contract-period-contracted-hours",
    ),
    path(
        "workspaces/<str:slug>/service-contract-periods/<uuid:pk>/dismiss-alert/",
        ServiceContractPeriodViewSet.as_view({"post": "dismiss_alert"}),
        name="service-contract-period-dismiss-alert",
    ),
    path(
        "workspaces/<str:slug>/service-contract-alerts/",
        ServiceContractAlertPanelEndpoint.as_view(),
        name="service-contract-alerts",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/issues/<uuid:issue_id>/service-pool/",
        IssueServicePoolEndpoint.as_view(),
        name="issue-service-pool",
    ),
]
