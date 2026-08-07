# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.urls import path

from plane.app.views import ServiceLogClientEndpoint, ServiceLogViewSet

# Explicit path() entries, one file per resource, following the house convention --
# there is no DRF router in this codebase.
#
# Routed under the work item, because a work log has no meaning apart from the work
# item it describes and the project it is billed to. The project therefore comes from
# the URL and never from the payload, exactly as every other issue sub-resource does.
#
# Note what is NOT routed: a per-row PATCH or DELETE. The unit of editing is the batch,
# because one submission can produce several segments (R10) and section 3 of the phase
# brief requires them to be edited and deleted together, transactionally. A single
# entry is a batch of one, so nothing is harder for the common case.
urlpatterns = [
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/issues/<uuid:issue_id>/service-logs/",
        ServiceLogViewSet.as_view({"get": "list", "post": "create"}),
        name="service-logs",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/issues/<uuid:issue_id>/service-logs/totals/",
        ServiceLogViewSet.as_view({"get": "totals"}),
        name="service-log-totals",
    ),
    # The client portal's read of the same rows, through `ServiceLogClientSerializer`.
    # A separate endpoint and not a role branch on `list`, so that the viewset above keeps
    # its "GUEST is on no endpoint here" guarantee unconditionally. Declared before the
    # `batches/<uuid:batch_id>/` route for no routing reason -- `client` is not a UUID and
    # cannot collide -- but kept next to the read routes it belongs with.
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/issues/<uuid:issue_id>/service-logs/client/",
        ServiceLogClientEndpoint.as_view(),
        name="service-log-client",
    ),
    # Validates, classifies and computes without persisting, so the form can show what
    # will be created before it is created.
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/issues/<uuid:issue_id>/service-logs/preview/",
        ServiceLogViewSet.as_view({"post": "preview"}),
        name="service-log-preview",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/issues/<uuid:issue_id>/"
        "service-logs/batches/<uuid:batch_id>/",
        ServiceLogViewSet.as_view({"patch": "update_batch", "delete": "destroy_batch"}),
        name="service-log-batch",
    ),
    # Reassignment is its own route rather than a field on the PATCH above, and the reason
    # is structural rather than aesthetic: an edit rebuilds the batch, reversing and
    # reapplying every pool debit, so it lands on the same balance only by cancellation and
    # is refused outright inside a closed period. This route sets `author` in place and
    # writes no ledger row, which is what makes acceptance criterion 6 -- "alterar o autor
    # não altera saldo de pool" -- true by construction. See `reassign_author`.
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/issues/<uuid:issue_id>/"
        "service-logs/batches/<uuid:batch_id>/author/",
        ServiceLogViewSet.as_view({"patch": "reassign_author"}),
        name="service-log-batch-author",
    ),
]
