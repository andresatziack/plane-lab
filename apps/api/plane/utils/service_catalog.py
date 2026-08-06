# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Domain logic for the work log catalogues.

Pure functions over models, with no dependency on views, serializers or DRF, as
section 6 of the master context requires. Everything financial in this phase lives
here so it can be unit tested without an HTTP layer.
"""

# Python imports
from decimal import ROUND_HALF_UP, Decimal

# Django imports
from django.db import models, transaction

# Module imports
from plane.db.models import ServiceBillingType, ServiceConfigActivity, ServiceConfigVerb

# Error codes, in the UPPER_SNAKE style the client entity established. The
# frontend maps them to translated strings; the API never returns Portuguese.
DEFAULT_CANNOT_BE_UNSET = "DEFAULT_CANNOT_BE_UNSET"
DEFAULT_CANNOT_BE_DEACTIVATED = "DEFAULT_CANNOT_BE_DEACTIVATED"
DEFAULT_CANNOT_BE_DELETED = "DEFAULT_CANNOT_BE_DELETED"
HOUR_TYPE_IN_USE_BY_SERVICE_LOGS = "HOUR_TYPE_IN_USE_BY_SERVICE_LOGS"
BILLING_TYPE_IN_USE_BY_SERVICE_LOGS = "BILLING_TYPE_IN_USE_BY_SERVICE_LOGS"
HOUR_TYPE_IN_USE_BY_CLASSIFICATION_WINDOWS = "HOUR_TYPE_IN_USE_BY_CLASSIFICATION_WINDOWS"

#: How an unset nullable field is rendered in the audit trail. Decision D4.
#:
#: ``svc_cfg_activity_shape_matches_verb`` requires ``old_value`` and ``new_value`` to
#: be non-null on an ``updated`` row, and until the contract phase every tracked field
#: was non-nullable, so the question never arose. The contract has three that are
#: exactly what the trail exists to record -- accrual cap, carryover validity and the
#: overage hour rate -- and a transition from NULL to 60.00 would have violated the
#: constraint at the database level.
#:
#: Of the three exits the constraint's own comment names, this is the sentinel. The two
#: refused:
#:
#: * **dropping the fields from TRACKED_FIELDS** -- the docstring of
#:   ``ServiceConfigActivity`` names "accrual cap, overage hour rate" as this table's
#:   reason to exist, so excluding them would answer the constraint by deleting the
#:   requirement;
#: * **relaxing the constraint** -- that weakens a DDL guarantee over *every* row,
#:   including the catalogues', to solve a problem belonging to three columns.
#:
#: ``"UNSET"`` and not ``""``, which is ambiguous with a legitimately empty string, nor
#: ``"None"``, which is a Python repr leaking into data. It follows the UPPER_SNAKE of
#: the error codes and cannot collide with anything ``serialize_config_value``
#: produces: quantised decimals, ISO dates, lowercase booleans and lowercase enum
#: values. That non-collision depends on no tracked field being free text, which is a
#: premise a comment cannot enforce -- so there is a test that fails if one ever is.
CONFIG_VALUE_UNSET = "UNSET"


# ---------------------------------------------------------------------------
# Default resolution
# ---------------------------------------------------------------------------


def resolve_default_billing_type(workspace_id, service_client=None):
    """The billing type a new work log form should pre-select.

    Two levels only, deliberately: the catalogue default, overridden by the
    client's own default when it has one. The third level of the precedence chain
    -- the choice the technician actually submits -- is not modelled here, because
    that value arrives in a request payload and belongs to whoever handles it. See
    DECISOES.md, D7, and section 3 of the phase brief.

    Returns ``None`` only when the workspace has no active default at all, which
    means the catalogue was never seeded.

    Consumed by the work log form in the next phase. It is implemented now because
    the precedence is a decision of *this* phase: leaving it undefined would let a
    later session re-derive the order and get it backwards.
    """
    catalogue_default = ServiceBillingType.objects.filter(
        workspace_id=workspace_id, is_default=True, is_active=True
    ).first()

    if service_client is None or not service_client.default_billing_type_id:
        return catalogue_default

    # Queried by id rather than through ``service_client.default_billing_type``.
    # The foreign key is DO_NOTHING, so it can still point at a soft deleted row,
    # and Django's forward descriptor resolves it through the soft-delete filtered
    # base manager -- which raises DoesNotExist instead of returning None. Filtering
    # explicitly also covers the two cases that must fall back rather than fail:
    # an override that was deactivated (pre-selecting a hidden option would be a
    # dead end in the form) and, defensively, one belonging to another workspace.
    client_default = ServiceBillingType.objects.filter(
        id=service_client.default_billing_type_id,
        workspace_id=workspace_id,
        is_active=True,
    ).first()

    return client_default or catalogue_default


# ---------------------------------------------------------------------------
# Catalogue invariants
# ---------------------------------------------------------------------------


def validate_catalog_update(instance, is_default=None, is_active=None):
    """Refuse the two edits that would leave a catalogue without a default.

    Returns an error code, or ``None`` when the edit is allowed. Pass the *incoming*
    values; ``None`` means "not being changed".

    Deactivating the default matters as much as unsetting it: an inactive default
    would still be pre-selected by the form while being hidden from the dropdown.
    """
    if not instance.is_default:
        return None

    if is_default is False:
        return DEFAULT_CANNOT_BE_UNSET

    if is_active is False:
        return DEFAULT_CANNOT_BE_DEACTIVATED

    return None


def validate_catalog_delete(instance):
    """Refuse deletions that would break an invariant.

    Returns an error code, or ``None`` when the delete is allowed.

    Deleting the default would leave the catalogue with zero defaults, so it is
    refused here -- the same reasoning the State model uses for its own default.

    EXTENSION POINT, and the only place these checks should be added:
      * the work log phase adds "in use by work logs" -- DONE, see below;
      * the calendar and windows phase adds "has classification windows" -- DONE;
      * the pricing phase adds "referenced by a client price override".
    All three are reasons to refuse a delete on an hour type, and keeping them in
    one function is why the guard was not inlined into the viewset.
    """
    if instance.is_default:
        return DEFAULT_CANNOT_BE_DELETED

    in_use = _in_use_by_service_logs(instance)

    if in_use:
        return in_use

    return _in_use_by_classification_windows(instance)


def _in_use_by_classification_windows(instance):
    """Refuse deleting an hour type that still has classification windows.

    Deleting it would tear a hole in the week's coverage without any single window being
    invalid -- the same failure the coverage ratchet exists to prevent, arriving through
    a different door. The admin has to remove or move the windows first, which forces the
    replacement coverage to be thought about.

    Only hour types have windows; billing types are not classified, so this returns
    ``None`` for them.

    Imported inside the function for the same reason as the work log check: keeping the
    dependency out of module scope stops these sibling utility modules from importing each
    other at load time.
    """
    from plane.db.models import ServiceHourType
    from plane.utils.service_calendar import hour_type_has_windows

    if not isinstance(instance, ServiceHourType):
        return None

    return HOUR_TYPE_IN_USE_BY_CLASSIFICATION_WINDOWS if hour_type_has_windows(instance) else None


def _in_use_by_service_logs(instance):
    """Refuse deleting a catalogue option that a work log points at.

    Phase 2's acceptance criterion 4 -- "Tentar excluir um Tipo de Hora em uso retorna
    erro explicativo" -- which that phase could not implement because no work log
    model existed. The work log foreign keys are ``DO_NOTHING``, so without this guard
    the delete would either succeed and leave a work log unable to render its own
    label, or fail deep in the database with an ``IntegrityError`` and a 500.

    Counted with ``all_objects``, matching how the client entity counts its
    references. A soft deleted work log still counts: it can be restored, its hours
    may already be on an invoice, and the database foreign key does not care that the
    row is flagged deleted -- a hard delete of the option would still fail.

    An hour type is in use as a *suggestion* too. A row whose ``suggested_hour_type``
    points here would break the same way, and the audit answer to "what did the engine
    propose" would be lost.

    Imported inside the function, not at module scope: ``plane.utils.service_log``
    imports this module's sibling for the billing route check, and a top level import
    here would make the two modules import each other at load time.
    """
    from plane.db.models import ServiceHourType, ServiceLog

    if isinstance(instance, ServiceHourType):
        in_use = ServiceLog.all_objects.filter(
            models.Q(hour_type_id=instance.pk) | models.Q(suggested_hour_type_id=instance.pk)
        ).exists()
        return HOUR_TYPE_IN_USE_BY_SERVICE_LOGS if in_use else None

    if isinstance(instance, ServiceBillingType):
        in_use = ServiceLog.all_objects.filter(billing_type_id=instance.pk).exists()
        return BILLING_TYPE_IN_USE_BY_SERVICE_LOGS if in_use else None

    return None


def set_catalog_default(instance, actor=None):
    """Promote one option to be its catalogue's default, atomically.

    Unsets the incumbent first: the partial unique index on (workspace) where
    is_default is true is checked per statement, so setting before unsetting would
    raise. Two concurrent promotions still cannot both succeed -- one hits the
    constraint -- which is the point of having it.
    """
    model = type(instance)

    with transaction.atomic():
        model.objects.filter(workspace_id=instance.workspace_id, is_default=True).exclude(pk=instance.pk).update(
            is_default=False
        )

        if not instance.is_default:
            instance.is_default = True
            save_with_config_activity(instance, actor=actor)

    return instance


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------


def serialize_config_value(instance, field_name, value):
    """Render a field value as the text stored in the audit trail.

    One convention for every phase, so the history stays comparable:

    * ``Decimal`` quantised to the field's own scale -- "1.50", never "1.5", so
      that a string comparison between two entries is meaningful;
    * booleans lowercase, rather than Python's "True"/"False";
    * everything else by its stored value, which for a TextChoices field is the
      database value ("debit_pool") and not the human label;
    * an unset nullable field as ``CONFIG_VALUE_UNSET``, never as SQL NULL.

    That last rule is decision D4, and it is why this function no longer returns
    ``None``. A null would violate ``svc_cfg_activity_shape_matches_verb`` on an
    ``updated`` row, so the first contract to have its accrual cap filled in would have
    crashed on a database constraint. Handled here, once, in the shared helper, so that
    the pricing phase's optional absolute override inherits the answer instead of
    rediscovering the problem.
    """
    if value is None:
        return CONFIG_VALUE_UNSET

    field = instance._meta.get_field(field_name)

    if isinstance(field, models.DecimalField):
        scale = Decimal(1).scaleb(-field.decimal_places)
        return str(Decimal(value).quantize(scale, rounding=ROUND_HALF_UP))

    if isinstance(field, models.BooleanField):
        return "true" if value else "false"

    return str(value)


def capture_tracked_changes(instance):
    """Snapshot the pending changes to an instance's TRACKED_FIELDS.

    MUST be called before ``save()``. ``ChangeTrackerMixin.save()`` resets the
    tracked values afterwards, and while it leaves the changed field *names* behind
    in ``_changes_on_save``, the old *values* are gone -- and the old value is the
    whole reason this trail exists.

    Returns ``{field_name: (old, new)}``, empty when nothing tracked changed.
    """
    return {
        field_name: (instance.old_values.get(field_name), getattr(instance, field_name))
        for field_name in instance.changed_fields
    }


def record_config_activity(instance, changes, actor):
    """Write the audit rows for a set of captured changes.

    Synchronous and inside the caller's transaction, deliberately not following the
    ``issue_activity.delay(...)`` pattern: configuration changes are rare enough
    that latency is irrelevant, and a task that fails would lose the record of
    precisely the event the trail was created to explain.

    ``actor`` is required. It is passed in rather than read from crum because
    ``get_current_user()`` returns None inside management commands, data migrations
    and Celery tasks, and ``BaseModel.save()`` then blanks ``created_by`` without
    raising -- an audit row with no author is not an audit row.
    """
    if not changes:
        return []

    if actor is None:
        raise ValueError("An actor is required to record a service config activity.")

    return [
        ServiceConfigActivity.objects.create(
            workspace_id=instance.workspace_id,
            entity_name=instance.CONFIG_ENTITY_NAME,
            entity_identifier=instance.pk,
            verb=ServiceConfigVerb.UPDATED,
            field_name=field_name,
            old_value=serialize_config_value(instance, field_name, old_value),
            new_value=serialize_config_value(instance, field_name, new_value),
            actor=actor,
        )
        for field_name, (old_value, new_value) in changes.items()
    ]


def record_config_creation(instance, actor):
    """Write the audit row for a configuration row that has just been created.

    **ONE row, not one per field.** A creation is a single act, and a row per column would
    bury it: registering one holiday would produce five entries that a reader has to
    reassemble. The whole row is described by ``instance.config_summary()``, which each
    audited model implements with the values that change the calculation.

    Synchronous and inside the caller's transaction, like the update trail, and for the
    same reason: a task that failed would lose the record of exactly the event the trail
    exists to explain.

    ``actor`` is required. An audit row with no author is not an audit row.

    EXTENSION POINT: the contract and pricing phases must call this on create. Their
    "when was this contract signed, and by whom" is the same question as this one.
    """
    if actor is None:
        raise ValueError("An actor is required to record a service config creation.")

    return ServiceConfigActivity.objects.create(
        workspace_id=instance.workspace_id,
        entity_name=instance.CONFIG_ENTITY_NAME,
        entity_identifier=instance.pk,
        verb=ServiceConfigVerb.CREATED,
        # Null for created and deleted: no single field is involved, and the check
        # constraint on the model enforces that.
        field_name=None,
        old_value=None,
        new_value=instance.config_summary(),
        actor=actor,
    )


def record_config_deletion(instance, actor):
    """Write the audit row for a configuration row that is about to be deleted.

    MUST be called **before** the delete, while the row can still describe itself.

    The summary goes in ``old_value``: the reader needs to know what disappeared, and
    telling them to go and find it in ``all_objects`` defeats the point of having one
    place to look.

    EXTENSION POINT: the contract and pricing phases must call this on delete.
    """
    if actor is None:
        raise ValueError("An actor is required to record a service config deletion.")

    return ServiceConfigActivity.objects.create(
        workspace_id=instance.workspace_id,
        entity_name=instance.CONFIG_ENTITY_NAME,
        entity_identifier=instance.pk,
        verb=ServiceConfigVerb.DELETED,
        field_name=None,
        old_value=instance.config_summary(),
        new_value=None,
        actor=actor,
    )


@transaction.atomic
def create_with_config_activity(instance, actor=None):
    """Save a new audited configuration row and record its creation, in one transaction.

    The creation counterpart of ``save_with_config_activity``. Atomic because a
    configuration row that exists with no audit entry is the state this trail was built to
    make impossible.
    """
    instance.save()

    if actor is not None:
        record_config_creation(instance, actor)

    return instance


@transaction.atomic
def delete_with_config_activity(instance, actor=None):
    """Record the deletion, then soft delete the row, in one transaction.

    In that order, deliberately: ``config_summary()`` reads the instance, and for a window
    it reads through to its hour type. Recording first keeps that resolution simple and
    keeps the audit row correct even if the delete cascades further than expected.
    """
    if actor is not None:
        record_config_deletion(instance, actor)

    instance.delete()

    return instance


def save_with_config_activity(instance, actor=None):
    """Save an audited catalogue row and record what changed, in one transaction.

    Returns the captured changes so a caller can react to them, for example to warn
    that a multiplier change applies only to future work logs.

    ``actor`` may be None only when nothing tracked has changed -- creating a row,
    or editing a field outside TRACKED_FIELDS. That keeps the seed and the
    management command, which have no request user, from needing a fake one.
    """
    with transaction.atomic():
        changes = capture_tracked_changes(instance)
        instance.save()

        if changes:
            record_config_activity(instance, changes, actor)

    return changes
