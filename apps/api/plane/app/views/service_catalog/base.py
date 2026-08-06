# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
import re
from datetime import datetime, time

# Django imports
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions import ROLE, allow_permission
from plane.app.serializers import (
    ServiceBillingTypeSerializer,
    ServiceConfigActivitySerializer,
    ServiceHourTypeSerializer,
)
from plane.db.models import (
    ServiceBillingType,
    ServiceConfigActivity,
    ServiceHourType,
    Workspace,
)
from plane.utils.service_catalog import set_catalog_default, validate_catalog_delete

from ..base import BaseAPIView, BaseViewSet

# A bare calendar date means "the whole day"; anything else is a precise instant.
DATE_ONLY_PATTERN = re.compile(r"^\d{4}-\d{1,2}-\d{1,2}$")


class ServiceCatalogViewSet(BaseViewSet):
    """Shared workspace level CRUD for the work log catalogues.

    Reads are open to admins and members: a technician needs these options to fill in
    a work log. Writes are restricted to workspace admins, and GUEST is excluded from
    everything -- the hour type payload carries the multiplier, which rule R11 keeps
    away from clients.

    Subclasses only set ``model`` and ``serializer_class``.
    """

    search_fields = ["name", "description"]

    def get_queryset(self):
        return self.model.objects.filter(workspace__slug=self.kwargs.get("slug")).select_related("workspace")

    def _serializer_context(self, workspace_id):
        # `actor` reaches the serializer's update() so the audit row has an author.
        # crum cannot be relied on for this -- see plane.utils.service_catalog.
        return {"workspace_id": workspace_id, "actor": self.request.user}

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def list(self, request, slug):
        catalog_options = self.get_queryset()

        # The admin panel needs inactive options so they can be reactivated; the work
        # log form must not offer them. Opt in explicitly rather than guessing from
        # the caller's role.
        if request.GET.get("only_active", "false").lower() == "true":
            catalog_options = catalog_options.filter(is_active=True)

        return Response(
            self.serializer_class(catalog_options, many=True).data,
            status=status.HTTP_200_OK,
        )

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def retrieve(self, request, slug, pk):
        catalog_option = self.get_queryset().filter(pk=pk).first()

        if not catalog_option:
            return Response(
                {"error": "The required object does not exist."},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(self.serializer_class(catalog_option).data, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def create(self, request, slug):
        workspace = Workspace.objects.get(slug=slug)

        serializer = self.serializer_class(data=request.data, context=self._serializer_context(workspace.id))

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        serializer.save(workspace_id=workspace.id)

        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def partial_update(self, request, slug, pk):
        workspace = Workspace.objects.get(slug=slug)
        catalog_option = self.get_queryset().filter(pk=pk).first()

        if not catalog_option:
            return Response(
                {"error": "The required object does not exist."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = self.serializer_class(
            catalog_option,
            data=request.data,
            partial=True,
            context=self._serializer_context(workspace.id),
        )

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        serializer.save()

        return Response(serializer.data, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def destroy(self, request, slug, pk):
        catalog_option = self.get_queryset().filter(pk=pk).first()

        if not catalog_option:
            return Response(
                {"error": "The required object does not exist."},
                status=status.HTTP_404_NOT_FOUND,
            )

        error_code = validate_catalog_delete(catalog_option)

        if error_code:
            return Response({"error": error_code}, status=status.HTTP_400_BAD_REQUEST)

        catalog_option.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def mark_default(self, request, slug, pk):
        """Promote this option to be the catalogue default.

        A dedicated action rather than a PATCH of ``is_default``, following the State
        model's ``mark-default``: promoting is a swap of two rows, and the partial
        unique index would reject a naive PATCH that set a second default.
        """
        catalog_option = self.get_queryset().filter(pk=pk).first()

        if not catalog_option:
            return Response(
                {"error": "The required object does not exist."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if not catalog_option.is_active:
            # An inactive default would be pre-selected by the form while being
            # hidden from the dropdown.
            return Response(
                {"error": "INACTIVE_OPTION_CANNOT_BE_DEFAULT"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        set_catalog_default(catalog_option, actor=request.user)

        return Response(self.serializer_class(catalog_option).data, status=status.HTTP_200_OK)


class ServiceHourTypeViewSet(ServiceCatalogViewSet):
    """When work happened, and the multiplier that applies to it."""

    model = ServiceHourType
    serializer_class = ServiceHourTypeSerializer


class ServiceBillingTypeViewSet(ServiceCatalogViewSet):
    """Where logged time goes: a contracted pool, an invoice, or nowhere."""

    model = ServiceBillingType
    serializer_class = ServiceBillingTypeSerializer


class ServiceConfigActivityEndpoint(BaseAPIView):
    """Read-only configuration audit trail.

    SECURITY: this holds the history of multipliers and, once the pricing phase
    lands, of client prices. It is commercial intelligence, so it is workspace ADMIN
    only -- not MEMBER, who may read the catalogues but has no business reading the
    price history, and emphatically not GUEST. There are regression tests asserting
    403 for both. This endpoint is also listed in the client portal phase's criterion
    about new endpoints not leaking client data.

    There is deliberately no POST, PATCH or DELETE anywhere for this model. A trail
    that can be rewritten is not a trail.
    """

    @staticmethod
    def _parse_boundary(raw_value, is_upper_bound):
        """Turn a query parameter into an aware datetime, or None if unparseable.

        Accepts a full timestamp or a bare date. A bare date is expanded to cover the
        whole day, so ``?created_at__lte=2026-03-31`` includes the changes made on the
        31st -- taken literally it would mean midnight and silently exclude them.

        Made timezone aware in the currently active timezone. This is a filter over a
        technical ``created_at`` timestamp for a screen, not a business date, so it is
        explicitly NOT the timezone strategy that the calendar and windows phase has
        to define for classification. Leaving it naive makes the comparison against an
        aware column ambiguous, which is what Django warns about.
        """
        # Dispatched on the shape of the string rather than by trying parse_datetime
        # first and falling back. Under Django 5 on Python 3.11, parse_datetime()
        # delegates to datetime.fromisoformat(), which happily accepts a bare
        # "2026-08-06" and returns midnight -- so the date-only branch would never be
        # reached and an upper bound would silently exclude its own day.
        if DATE_ONLY_PATTERN.match(raw_value):
            parsed_date = parse_date(raw_value)

            if parsed_date is None:
                return None

            parsed = datetime.combine(parsed_date, time.max if is_upper_bound else time.min)
        else:
            parsed = parse_datetime(raw_value)

            if parsed is None:
                return None

        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed, timezone.get_current_timezone())

        return parsed

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def get(self, request, slug):
        activities = ServiceConfigActivity.objects.filter(workspace__slug=slug).select_related("actor")

        entity_name = request.GET.get("entity_name")
        if entity_name:
            activities = activities.filter(entity_name=entity_name)

        entity_identifier = request.GET.get("entity_identifier")
        if entity_identifier:
            activities = activities.filter(entity_identifier=entity_identifier)

        # Parsed rather than handed to the ORM raw, so a malformed value is a 400 with
        # a reason instead of a 500 from the database driver.
        for parameter, is_upper_bound in (("created_at__gte", False), ("created_at__lte", True)):
            raw_value = request.GET.get(parameter)

            if not raw_value:
                continue

            parsed = self._parse_boundary(raw_value, is_upper_bound=is_upper_bound)

            if parsed is None:
                return Response(
                    {"error": "INVALID_DATE", "parameter": parameter},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            activities = activities.filter(**{parameter: parsed})

        return self.paginate(
            request=request,
            order_by="-created_at",
            queryset=activities,
            on_results=lambda rows: ServiceConfigActivitySerializer(rows, many=True).data,
        )
