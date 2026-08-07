# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
import datetime

# Django imports
from django.db import transaction

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions import ROLE, allow_permission
from plane.app.serializers import (
    ServiceClassificationWindowSerializer,
    ServiceHolidayBulkImportSerializer,
    ServiceHolidaySerializer,
)
from plane.db.models import ServiceClassificationWindow, ServiceHoliday, Workspace
from plane.utils.service_calendar import (
    coverage_report,
    holiday_calendar,
    is_window_set_complete,
    parse_holiday_csv,
    validate_holiday,
    validate_window_set,
)
from plane.utils.service_catalog import (
    create_with_config_activity,
    delete_with_config_activity,
    save_with_config_activity,
)

from ..base import BaseViewSet

#: How far the annual calendar will look. Guards against `?year=999999` turning the
#: recurrence expansion into a denial of service.
MIN_CALENDAR_YEAR = 1970
MAX_CALENDAR_YEAR = 2200


class ServiceHolidayViewSet(BaseViewSet):
    """Workspace level CRUD for the holiday calendar.

    Reads are open to admins and members: the work log form shows *why* an entry was
    classified as a holiday, and a technician has to be able to see that reason. Writes are
    restricted to workspace admins, and GUEST is excluded from everything -- the calendar
    determines multipliers, which rule R11 keeps away from clients.
    """

    serializer_class = ServiceHolidaySerializer
    model = ServiceHoliday
    search_fields = ["name"]

    def get_queryset(self):
        return ServiceHoliday.objects.filter(workspace__slug=self.kwargs.get("slug")).select_related(
            "workspace"
        )

    def _serializer_context(self, workspace_id):
        # `actor` reaches the audit helpers so the trail has an author. crum cannot be
        # relied on -- see plane.utils.service_catalog.
        return {"workspace_id": workspace_id, "actor": self.request.user}

    def _not_found(self):
        return Response({"error": "The required object does not exist."}, status=status.HTTP_404_NOT_FOUND)

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def list(self, request, slug):
        holidays = self.get_queryset()

        if request.GET.get("only_active", "false").lower() == "true":
            holidays = holidays.filter(is_active=True)

        if request.GET.get("year"):
            # Filters by *stored* year, which is not the same as "observed in that year" --
            # a recurring holiday is stored once. Use the calendar action for that.
            try:
                holidays = holidays.filter(date__year=int(request.GET["year"]))
            except ValueError:
                return Response({"error": "YEAR_IS_INVALID"}, status=status.HTTP_400_BAD_REQUEST)

        return Response(self.serializer_class(holidays, many=True).data, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def retrieve(self, request, slug, pk):
        holiday = self.get_queryset().filter(pk=pk).first()

        if not holiday:
            return self._not_found()

        return Response(self.serializer_class(holiday).data, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def calendar(self, request, slug):
        """Every holiday *observed* in a year, with recurrence expanded to real dates.

        Section 1 asks for an annual view so an admin can eyeball the year instead of
        trusting a list of rows. Recurrence makes the stored rows a poor answer to "what
        does next year look like", which is exactly what this returns.
        """
        workspace = Workspace.objects.filter(slug=slug).first()

        if not workspace:
            return self._not_found()

        try:
            year = int(request.GET.get("year") or datetime.date.today().year)
        except ValueError:
            return Response({"error": "YEAR_IS_INVALID"}, status=status.HTTP_400_BAD_REQUEST)

        if not MIN_CALENDAR_YEAR <= year <= MAX_CALENDAR_YEAR:
            return Response({"error": "YEAR_IS_OUT_OF_RANGE"}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {"year": year, "holidays": holiday_calendar(workspace.id, year)},
            status=status.HTTP_200_OK,
        )

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def create(self, request, slug):
        workspace = Workspace.objects.get(slug=slug)

        serializer = self.serializer_class(
            data=request.data, context=self._serializer_context(workspace.id)
        )

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        holiday = ServiceHoliday(**serializer.validated_data, workspace_id=workspace.id)
        # Registering a holiday moves every work log that day onto a different multiplier,
        # so the creation itself is the financial event and goes in the audit trail.
        create_with_config_activity(holiday, actor=request.user)

        return Response(self.serializer_class(holiday).data, status=status.HTTP_201_CREATED)

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def partial_update(self, request, slug, pk):
        workspace = Workspace.objects.get(slug=slug)
        holiday = self.get_queryset().filter(pk=pk).first()

        if not holiday:
            return self._not_found()

        serializer = self.serializer_class(
            holiday,
            data=request.data,
            partial=True,
            context=self._serializer_context(workspace.id),
        )

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        for attribute, value in serializer.validated_data.items():
            setattr(holiday, attribute, value)

        save_with_config_activity(holiday, actor=request.user)

        return Response(self.serializer_class(holiday).data, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def destroy(self, request, slug, pk):
        holiday = self.get_queryset().filter(pk=pk).first()

        if not holiday:
            return self._not_found()

        # Removing a holiday moves that day back to its weekday rate, which is as much a
        # financial event as adding it was.
        delete_with_config_activity(holiday, actor=request.user)

        return Response(status=status.HTTP_204_NO_CONTENT)

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def bulk_import(self, request, slug):
        """Import holidays from CSV. Section 1 of the phase brief.

        **All or nothing.** A partially applied import leaves the admin unable to tell what
        landed, and re-running it would then collide with the rows that did. Parsing reports
        every bad row at once so the fix is one edit rather than a dozen round trips.
        """
        workspace = Workspace.objects.get(slug=slug)

        serializer = ServiceHolidayBulkImportSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        rows, errors = parse_holiday_csv(serializer.validated_data["csv_content"])

        if errors:
            return Response(
                {"error": "CSV_HAS_INVALID_ROWS", "rows": errors, "imported": 0},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not rows:
            return Response(
                {"error": "CSV_HAS_NO_ROWS", "imported": 0}, status=status.HTTP_400_BAD_REQUEST
            )

        created = []

        try:
            with transaction.atomic():
                for row in rows:
                    # The same recurrence rule the single-row endpoint applies. Checked per
                    # row inside the transaction so a collision *within the file* is caught
                    # too: the second Christmas sees the first one already stored.
                    error_code = validate_holiday(
                        workspace.id,
                        date=row["date"],
                        scope=row["scope"],
                        is_recurring=row["is_recurring"],
                    )

                    if error_code:
                        raise _BulkImportRejected(row["line"], error_code, row["name"])

                    holiday = ServiceHoliday(
                        workspace_id=workspace.id,
                        name=row["name"],
                        date=row["date"],
                        is_recurring=row["is_recurring"],
                        scope=row["scope"],
                        external_source="csv_import",
                    )
                    create_with_config_activity(holiday, actor=request.user)
                    created.append(holiday)
        except _BulkImportRejected as rejection:
            return Response(
                {
                    "error": "CSV_HAS_INVALID_ROWS",
                    "rows": [
                        {"line": rejection.line, "error": rejection.code, "value": rejection.value}
                    ],
                    "imported": 0,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "imported": len(created),
                "holidays": self.serializer_class(created, many=True).data,
            },
            status=status.HTTP_201_CREATED,
        )


class _BulkImportRejected(Exception):
    """Carries a per-row rejection out of the atomic block so the transaction rolls back."""

    def __init__(self, line, code, value):
        self.line = line
        self.code = code
        self.value = value
        super().__init__(code)


class ServiceClassificationWindowViewSet(BaseViewSet):
    """Workspace level CRUD for the classification windows.

    Reads are open to admins and members, writes to workspace admins, GUEST to neither --
    the same reasoning as the holiday calendar and the hour type catalogue.

    Every write is wrapped so the set-level invariants are checked afterwards and the write
    is rolled back when they fail. See ``_apply`` below.
    """

    serializer_class = ServiceClassificationWindowSerializer
    model = ServiceClassificationWindow

    def get_queryset(self):
        return ServiceClassificationWindow.objects.filter(
            workspace__slug=self.kwargs.get("slug")
        ).select_related("workspace")

    def _serializer_context(self, workspace_id):
        return {"workspace_id": workspace_id, "actor": self.request.user}

    def _not_found(self):
        return Response({"error": "The required object does not exist."}, status=status.HTTP_404_NOT_FOUND)

    def _apply(self, workspace_id, mutate):
        """Run a window write, then validate the whole set, rolling back on failure.

        The ratchet needs the *before* state, and it has to be read before ``mutate`` runs
        and inside the same transaction, or a concurrent write could make a complete set look
        incomplete and let a breaking change through.

        Returns ``(result, error_code)``.
        """
        try:
            with transaction.atomic():
                was_complete = is_window_set_complete(workspace_id)

                result = mutate()

                error_code = validate_window_set(workspace_id, was_complete_before=was_complete)

                if error_code:
                    # Raising is what rolls the write back; the code is carried out.
                    raise _WindowSetRejected(error_code)
        except _WindowSetRejected as rejection:
            return None, rejection.code

        return result, None

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def list(self, request, slug):
        windows = self.get_queryset()

        if request.GET.get("only_active", "false").lower() == "true":
            windows = windows.filter(is_active=True)

        if request.GET.get("hour_type_id"):
            windows = windows.filter(hour_type_id=request.GET["hour_type_id"])

        if request.GET.get("day_scope"):
            windows = windows.filter(day_scope=request.GET["day_scope"])

        return Response(self.serializer_class(windows, many=True).data, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def retrieve(self, request, slug, pk):
        """Read one window. **This action was reachable by anybody with a login.**

        ``get: retrieve`` is routed for this viewset but no ``retrieve`` was ever defined, so
        the inherited ``ModelViewSet`` one ran -- and it carries no ``allow_permission``, which
        left only ``BaseViewSet.permission_classes = [IsAuthenticated]``. No role check and no
        membership check. ``get_queryset`` filters on the slug taken from the URL and nothing
        else, so any authenticated user on the instance could read any workspace's window given
        its id. On an instance hosting two unrelated companies, one could read the other's
        window configuration.

        The class docstring above already said "reads are open to admins and members, GUEST to
        neither". That was true of every action that was *written* and false of the one that was
        only *routed* -- which is why an endpoint list maintained by hand would have asserted
        the docstring and agreed with it.

        The payload is worth guarding: it carries the hour type, and which hours fall in which
        window is the operation's pricing structure read from another angle. Whoever sees the
        windows knows which hours cost more, and when.

        Identical in shape to ``ServiceHolidayViewSet.retrieve``, which has had the decorator
        since it was written.
        """
        window = self.get_queryset().filter(pk=pk).first()

        if not window:
            return self._not_found()

        return Response(self.serializer_class(window).data, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def coverage(self, request, slug):
        """The health of the workspace's classification configuration.

        Feeds the panel's health indicator. Descriptive rather than a bare boolean on
        purpose: the coverage ratchet deliberately allows a workspace to sit incomplete, and
        an incomplete set that is invisible would only ever surface as a blank hour type in
        the work log form, where nobody would connect the two.

        Open to members as well as admins, because a technician who sees an unclassified
        entry needs to be able to find out why without waiting for an admin.
        """
        workspace = Workspace.objects.filter(slug=slug).first()

        if not workspace:
            return self._not_found()

        return Response(coverage_report(workspace.id), status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def create(self, request, slug):
        workspace = Workspace.objects.get(slug=slug)

        serializer = self.serializer_class(
            data=request.data, context=self._serializer_context(workspace.id)
        )

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        def mutate():
            window = ServiceClassificationWindow(
                **serializer.validated_data, workspace_id=workspace.id
            )
            create_with_config_activity(window, actor=request.user)
            return window

        window, error_code = self._apply(workspace.id, mutate)

        if error_code:
            return Response({"error": error_code}, status=status.HTTP_400_BAD_REQUEST)

        return Response(self.serializer_class(window).data, status=status.HTTP_201_CREATED)

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def partial_update(self, request, slug, pk):
        workspace = Workspace.objects.get(slug=slug)
        window = self.get_queryset().filter(pk=pk).first()

        if not window:
            return self._not_found()

        serializer = self.serializer_class(
            window,
            data=request.data,
            partial=True,
            context=self._serializer_context(workspace.id),
        )

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        def mutate():
            for attribute, value in serializer.validated_data.items():
                setattr(window, attribute, value)
            save_with_config_activity(window, actor=request.user)
            return window

        updated, error_code = self._apply(workspace.id, mutate)

        if error_code:
            return Response({"error": error_code}, status=status.HTTP_400_BAD_REQUEST)

        return Response(self.serializer_class(updated).data, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def destroy(self, request, slug, pk):
        workspace = Workspace.objects.get(slug=slug)
        window = self.get_queryset().filter(pk=pk).first()

        if not window:
            return self._not_found()

        def mutate():
            delete_with_config_activity(window, actor=request.user)
            return window

        # Deletion is the case a per-row check could never catch: no row is invalid, the
        # *set* is. Which is why validation is set-level and runs here too.
        _, error_code = self._apply(workspace.id, mutate)

        if error_code:
            return Response({"error": error_code}, status=status.HTTP_400_BAD_REQUEST)

        return Response(status=status.HTTP_204_NO_CONTENT)


class _WindowSetRejected(Exception):
    """Carries the invariant failure out of the atomic block so the write rolls back."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)
