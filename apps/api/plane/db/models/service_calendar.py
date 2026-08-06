# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q

# Module imports
from plane.utils.service_log_time import MINUTES_PER_DAY, minutes_to_clock

from ..mixins import ChangeTrackerMixin
from .service_catalog import ServiceConfigEntity
from .workspace import WorkspaceBaseModel


class ServiceHolidayScope(models.TextChoices):
    """Reach of a holiday.

    Recorded and displayed, with **no filtering logic anywhere** — decision D14. The
    calendar is single for the whole workspace, and this column exists so that a
    future phase can filter by locality without a migration. Adding behaviour here
    means revisiting D14 first.

    Declared at module level rather than nested so the check constraints and the
    engine can reference it while the model class is still being built.
    """

    NATIONAL = "national", "National"
    STATE = "state", "State"
    MUNICIPAL = "municipal", "Municipal"


class ServiceDayScope(models.TextChoices):
    """Which kind of day a classification window applies to.

    Seven weekdays plus ``holiday``. ``holiday`` is not a weekday and does not
    replace one: on a holiday the engine considers the holiday windows **and** the
    windows of that calendar weekday together, and resolves by priority. That is what
    makes "a holiday falling on a Wednesday is billed at 2.0 all day" a consequence of
    configuration rather than a special case in code, and what lets an admin create a
    night shift window that outranks the holiday if they ever want to.
    """

    MONDAY = "monday", "Monday"
    TUESDAY = "tuesday", "Tuesday"
    WEDNESDAY = "wednesday", "Wednesday"
    THURSDAY = "thursday", "Thursday"
    FRIDAY = "friday", "Friday"
    SATURDAY = "saturday", "Saturday"
    SUNDAY = "sunday", "Sunday"
    HOLIDAY = "holiday", "Holiday"


#: Indexed by ``datetime.date.weekday()`` (0 = Monday), so a date maps to its scope
#: without a lookup table in every caller.
WEEKDAY_SCOPES = (
    ServiceDayScope.MONDAY,
    ServiceDayScope.TUESDAY,
    ServiceDayScope.WEDNESDAY,
    ServiceDayScope.THURSDAY,
    ServiceDayScope.FRIDAY,
    ServiceDayScope.SATURDAY,
    ServiceDayScope.SUNDAY,
)

#: The seven weekday scopes plus the holiday scope, which together must cover the
#: whole week for the engine to be able to classify every instant.
COVERABLE_SCOPES = WEEKDAY_SCOPES + (ServiceDayScope.HOLIDAY,)

#: Short pt-BR labels, used only by ``__str__``. Debugging aid, not interface text --
#: the panel renders these through i18n like everything else.
DAY_SCOPE_SHORT_LABELS = {
    ServiceDayScope.MONDAY: "seg",
    ServiceDayScope.TUESDAY: "ter",
    ServiceDayScope.WEDNESDAY: "qua",
    ServiceDayScope.THURSDAY: "qui",
    ServiceDayScope.FRIDAY: "sex",
    ServiceDayScope.SATURDAY: "sáb",
    ServiceDayScope.SUNDAY: "dom",
    ServiceDayScope.HOLIDAY: "feriado",
}


class ServiceHoliday(ChangeTrackerMixin, WorkspaceBaseModel):
    """A day the classification engine treats as a holiday.

    Single calendar per workspace (D14). Holidays are a commercial parameter, like the
    windows: they are the same for every technician because they are what the contracts
    with clients say.

    Changing the calendar never reclassifies a work log that already exists — R4, and
    section 1 of the phase brief. That is held by the snapshots on ``ServiceLog``, not
    by anything here, which is why this model can be edited freely.

    Inherited from ``WorkspaceBaseModel``: ``workspace`` (required) plus a nullable
    ``project`` FK that this model does not use and that serializers exclude.
    """

    # `date` and `is_active` change what future work costs, so they are audited the
    # same way a multiplier is. `name` is not tracked: renaming "Natal" to "Natal
    # (feriado nacional)" is not a financial event.
    #
    # Creation and deletion are audited too, through the `created` and `deleted` verbs
    # on ServiceConfigActivity rather than through this mixin -- which structurally
    # cannot emit them. For this entity those are the *important* events: registering a
    # holiday on 15/03 doubles that day's invoice. See `config_summary` below and the
    # class docstring on ServiceConfigActivity.
    TRACKED_FIELDS = ["date", "is_recurring", "is_active"]
    CONFIG_ENTITY_NAME = ServiceConfigEntity.HOLIDAY

    name = models.CharField(max_length=255)

    # The canonical occurrence. For a recurring holiday the year is only a record of
    # when it was first registered; the engine matches on month and day.
    date = models.DateField()

    # Christmas repeats on the same date every year; Carnival moves and has to be
    # registered year by year. Section 1 of the phase brief.
    is_recurring = models.BooleanField(default=False)

    scope = models.CharField(
        max_length=20,
        choices=ServiceHolidayScope.choices,
        default=ServiceHolidayScope.NATIONAL,
    )

    is_active = models.BooleanField(default=True)

    # Import provenance, following the convention used across plane.db. Set by the CSV
    # bulk import so a botched import can be identified and undone.
    external_source = models.CharField(max_length=255, null=True, blank=True)
    external_id = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        verbose_name = "Service Holiday"
        verbose_name_plural = "Service Holidays"
        db_table = "service_holidays"
        ordering = ("date", "name")

        # Uniqueness under soft delete follows the house pattern: unique_together
        # including deleted_at, plus a conditional UniqueConstraint for live rows.
        #
        # This catches two entries on the same stored date and scope. It CANNOT catch a
        # recurring 25/12/2026 colliding with a specific 25/12/2027, because those are
        # different `date` values that nonetheless resolve to the same day. That
        # collision depends on the year and is therefore checked in the domain layer,
        # in both directions -- see `validate_holiday` in plane.utils.service_calendar.
        unique_together = [["workspace", "date", "scope", "deleted_at"]]
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "date", "scope"],
                condition=Q(deleted_at__isnull=True),
                name="service_holiday_unique_date_scope_when_deleted_at_null",
            ),
        ]

        indexes = [
            models.Index(fields=["workspace", "is_active"], name="svc_holiday_ws_active_idx"),
            models.Index(fields=["workspace", "date"], name="svc_holiday_ws_date_idx"),
        ]

    def __str__(self):
        recurring = " (anual)" if self.is_recurring else ""
        return f"{self.name} {self.date:%d/%m/%Y}{recurring}"

    def config_summary(self):
        """One line for a creation or deletion audit entry.

        ``is_recurring`` is in here and is not decoration. Without it the trail cannot
        distinguish "added Christmas 2027", which moves one day's invoice, from "added
        Christmas every year", which moves a day in every future invoice. Those are
        different acts and the reader has to be able to tell them apart.

        The scope is included for the same kind of reason: it is what a later phase will
        filter by, so a trail written without it could not be reinterpreted later.
        """
        recurrence = "anual" if self.is_recurring else "data específica"
        return f"{self.name} — {self.date:%d/%m/%Y} ({recurrence}, {self.scope})"

    def matches(self, target):
        """Whether this holiday falls on ``target``, honouring annual recurrence.

        The one place recurrence is interpreted. A recurring entry matches the same
        month and day in any year; a non-recurring one matches only its own date.

        29 February on a recurring holiday simply does not match in a non-leap year,
        which is the correct reading: the day does not exist, so there is no holiday to
        observe. No civil holiday falls on the 29th, so this is a formality.
        """
        if not self.is_active:
            return False

        if self.is_recurring:
            return (self.date.month, self.date.day) == (target.month, target.day)

        return self.date == target


class ServiceClassificationWindow(ChangeTrackerMixin, WorkspaceBaseModel):
    """A stretch of a kind of day that maps to one hour type.

    The engine resolves an instant by collecting every window that contains it and
    taking the one whose hour type has the lowest ``priority``.

    **Borders are whole minutes since midnight, not ``TimeField``.** Three reasons,
    and the first is decisive:

    1. A window has to be able to end at 24:00 to say "the whole day", and
       ``datetime.time`` cannot represent that hour. Encoding it as ``00:00`` would
       collide with a window that genuinely starts at midnight, and an extra
       ``is_all_day`` flag would add a branch to every comparison plus a second way to
       express the same thing.
    2. Everything else in this feature is already whole minutes -- the raw duration and
       the 15 minute block -- so one unit throughout removes a class of conversion bug.
    3. Midnight crossing becomes ordinary arithmetic instead of a special case.

    The cost is that ``1080`` does not read as ``18:00``. That is paid back in two
    places, deliberately: the serializer speaks ``HH:MM`` in both directions and never
    exposes the integer, and ``__str__`` renders the window readably for anyone
    debugging in a shell.
    """

    # `hour_type` is deliberately NOT tracked. `ChangeTrackerMixin` reads tracked
    # fields with `getattr` in `__init__`, and this foreign key is DO_NOTHING, so it can
    # point at a soft deleted hour type -- following the forward descriptor would raise
    # DoesNotExist while merely loading the row. Moving a window to another hour type is
    # in practice a delete plus a create anyway.
    TRACKED_FIELDS = ["day_scope", "start_minute", "end_minute", "is_active"]
    CONFIG_ENTITY_NAME = ServiceConfigEntity.CLASSIFICATION_WINDOW

    # DO_NOTHING, for the reason spelled out on ServiceLog and repeated by section 2 of
    # the phase brief: `soft_delete_related_objects` dispatches on the on_delete name,
    # and every option except DO_NOTHING and SET_NULL falls into a catch-all that soft
    # deletes the related rows. With CASCADE, soft deleting an hour type would silently
    # delete its windows and tear a hole in the week's coverage.
    #
    # `related_name="windows"` is the name the phase brief reserved for this.
    hour_type = models.ForeignKey(
        "db.ServiceHourType",
        on_delete=models.DO_NOTHING,
        related_name="windows",
    )

    day_scope = models.CharField(max_length=20, choices=ServiceDayScope.choices)

    # 0 to 1439. A window may start at any minute of the day but not at its end.
    start_minute = models.IntegerField(
        validators=[MinValueValidator(0), MaxValueValidator(MINUTES_PER_DAY - 1)]
    )

    # 1 to 1440. 1440 is 24:00, the end of the day.
    #
    # `end_minute <= start_minute` means the window crosses midnight, which is how
    # R10's continuous 18:00 to 08:00 range is expressed -- (1080, 480).
    end_minute = models.IntegerField(validators=[MinValueValidator(1), MaxValueValidator(MINUTES_PER_DAY)])

    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Service Classification Window"
        verbose_name_plural = "Service Classification Windows"
        db_table = "service_classification_windows"
        ordering = ("day_scope", "start_minute")

        unique_together = [
            ["workspace", "hour_type", "day_scope", "start_minute", "end_minute", "deleted_at"]
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "hour_type", "day_scope", "start_minute", "end_minute"],
                condition=Q(deleted_at__isnull=True),
                name="svc_window_unique_range_when_deleted_at_null",
            ),
            models.CheckConstraint(
                condition=Q(start_minute__gte=0, start_minute__lt=MINUTES_PER_DAY),
                name="svc_window_start_minute_within_day",
            ),
            models.CheckConstraint(
                condition=Q(end_minute__gt=0, end_minute__lte=MINUTES_PER_DAY),
                name="svc_window_end_minute_within_day",
            ),
            # A window whose borders are equal has no duration and would classify
            # nothing, while looking like it covered something. The whole day is
            # (0, 1440), never (0, 0).
            models.CheckConstraint(
                condition=~Q(start_minute=models.F("end_minute")),
                name="svc_window_borders_must_differ",
            ),
        ]

        indexes = [
            # How the engine reads them: every active window of a workspace, filtered
            # by the day scopes in play.
            models.Index(fields=["workspace", "day_scope", "is_active"], name="svc_window_ws_scope_idx"),
        ]

    def __str__(self):
        """A readable window, e.g. ``seg 18:00–08:00`` or ``dom 00:00–24:00``.

        Exists because the borders are stored as integers, and anyone inspecting this
        model in a shell, in a traceback or in a data migration would otherwise be
        reading 1080 and 480 and having to convert in their head.
        """
        scope = DAY_SCOPE_SHORT_LABELS.get(self.day_scope, self.day_scope)
        return f"{scope} {self.start_clock}–{self.end_clock}"

    def config_summary(self):
        """One line for a creation or deletion audit entry.

        Names the hour type as well as the range, because a window without its hour type
        says nothing about price. Read through ``all_objects`` so a window whose hour type
        was soft deleted can still describe itself -- an audit entry that cannot render is
        worse than none.
        """
        hour_type_name = "?"

        if self.hour_type_id:
            from .service_catalog import ServiceHourType

            hour_type = ServiceHourType.all_objects.filter(pk=self.hour_type_id).first()
            if hour_type:
                hour_type_name = hour_type.name

        return f"{hour_type_name} — {self}"

    @property
    def start_clock(self):
        """``HH:MM`` of the opening border."""
        return minutes_to_clock(self.start_minute)

    @property
    def end_clock(self):
        """``HH:MM`` of the closing border. ``24:00`` when the window ends the day."""
        return minutes_to_clock(self.end_minute)

    @property
    def crosses_midnight(self):
        """True when the window runs past 24:00 into the next day."""
        return self.end_minute <= self.start_minute

    @property
    def is_all_day(self):
        """True for the whole day, which is exactly ``(0, 1440)``."""
        return self.start_minute == 0 and self.end_minute == MINUTES_PER_DAY

    @property
    def duration_minutes(self):
        """How long the window lasts, in minutes, midnight crossing included."""
        return (self.end_minute - self.start_minute) % MINUTES_PER_DAY or MINUTES_PER_DAY

    def contains_minute(self, minute):
        """Whether a minute of the day falls inside this window.

        Half-open on purpose: the start border is inside and the end border is not. It
        is what makes the boundary cases of the phase brief come out right -- work
        ending exactly at 18:00 is entirely business hours, and work starting exactly at
        08:00 is too, because 18:00 belongs to the window that starts there.
        """
        if self.crosses_midnight:
            return minute >= self.start_minute or minute < self.end_minute

        return self.start_minute <= minute < self.end_minute
