# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
from django.utils import timezone
from django.apps import apps
from django.conf import settings
from django.db import models
from django.db.models.fields.related import OneToOneRel


# Third party imports
from celery import shared_task


def _is_service_log_relation(relation):
    """Whether this reverse relation points at ``ServiceLog``.

    Compared by label rather than by importing the model, so this module keeps no import
    of ``plane.db.models`` at load time -- ``plane.db.mixins`` imports this module, and a
    model import here would close the cycle.
    """
    related_model = relation.related_model

    return (
        related_model is not None
        and related_model._meta.app_label == "db"
        and related_model._meta.model_name == "servicelog"
    )


def _soft_delete_service_logs(instance, related_name):
    """Soft delete work logs through their own ``delete()``, so the pool is reversed.

    One row at a time and through the model's ``delete()``, which is the entire point:
    the reversal hangs there, and both the queryset ``delete()`` and the catch-all's
    ``save()`` bypass it.

    Failures are contained per row. A work log in a closed period raises, and the right
    response is to skip that one and keep going rather than abandon the rest of the
    cascade -- the same containment the catch-all already applies per relation.
    """
    from plane.utils.service_pool import ServicePoolValidationError

    for related_obj in getattr(instance, related_name)(manager="objects").all():
        if related_obj.deleted_at:
            continue

        try:
            related_obj.delete()
        except ServicePoolValidationError as error:
            # An invoiced month must not be rewritten by a cascade. Left intact, and
            # loudly, because a silently skipped work log is indistinguishable from one
            # that was deleted correctly.
            print(f"Skipped soft delete of service log {related_obj.pk}: {error.code}")
            continue


@shared_task
def soft_delete_related_objects(app_label, model_name, instance_pk, using=None):
    """
    Soft delete related objects for a given model instance
    """
    # Get the model class using app registry
    model_class = apps.get_model(app_label, model_name)

    # Get the instance using all_objects to ensure we can get even if it's already soft deleted
    try:
        instance = model_class.all_objects.get(pk=instance_pk)
    except model_class.DoesNotExist:
        return

    # Get all related fields that are reverse relationships
    all_related = [
        f for f in instance._meta.get_fields() if (f.one_to_many or f.one_to_one) and f.auto_created and not f.concrete
    ]

    # Handle each related field
    for relation in all_related:
        related_name = relation.get_accessor_name()

        # Skip if the relation doesn't exist
        if not hasattr(instance, related_name):
            continue

        # Get the on_delete behavior name
        on_delete_name = relation.on_delete.__name__ if hasattr(relation.on_delete, "__name__") else ""

        if on_delete_name == "DO_NOTHING":
            continue

        elif on_delete_name == "SET_NULL":
            # Handle SET_NULL relationships
            if isinstance(relation, OneToOneRel):
                # For OneToOne relationships
                related_obj = getattr(instance, related_name, None)
                if related_obj and isinstance(related_obj, models.Model):
                    setattr(related_obj, relation.remote_field.name, None)
                    related_obj.save(update_fields=[relation.remote_field.name])
            else:
                # For other relationships
                related_queryset = getattr(instance, related_name).all()
                related_queryset.update(**{relation.remote_field.name: None})

        elif _is_service_log_relation(relation):
            # EXPLICIT BRANCH FOR ServiceLog, AHEAD OF THE CATCH-ALL BELOW.
            #
            # The bug this fixes is real and was verified before the branch was
            # written. The catch-all below assigns `deleted_at` and calls `.save()`
            # directly, which never runs `SoftDeleteModel.delete()` -- and therefore
            # never runs `ServiceLog.delete()`, where the hour pool reversal lives.
            # `ServiceLog.issue` is CASCADE, so deleting a work item took this path and
            # left the pool debited for hours belonging to a work item that no longer
            # existed. The ordinary paths were already safe: `delete_service_log_batch`
            # calls `row.delete()` per row, with a comment saying it does so precisely
            # in anticipation of this phase's debit.
            #
            # SCOPED TO ServiceLog ON PURPOSE. Making the catch-all call `.delete()` on
            # every model with a `deleted_at` column would start executing the custom
            # `delete()` of dozens of core models, which is far too large a behaviour
            # change to smuggle in here.
            #
            # THE REVERSAL IS ASYNCHRONOUS, and that is a property of where this runs,
            # not a choice. This whole function is a Celery task, so the pool does not
            # come back inside the transaction that deleted the work item. If the task
            # fails, the balance stays wrong until reconciliation runs --
            # `reconcile_period` and the `reconcile_service_periods --repair` command
            # exist for exactly that window, which is also why the fix is not "just"
            # this branch.
            #
            # A work log whose period is already CLOSED is deliberately left alone. Its
            # month has been invoiced, and handing hours back into it would change a
            # total the client was already billed for; refusing is the same choice the
            # client delete guard makes. It stays in `all_objects` either way.
            _soft_delete_service_logs(instance, related_name)

        else:
            # Handle CASCADE and other delete behaviors
            try:
                if relation.one_to_one:
                    # Handle OneToOne relationships
                    related_obj = getattr(instance, related_name, None)
                    if related_obj:
                        if hasattr(related_obj, "deleted_at"):
                            if not related_obj.deleted_at:
                                related_obj.deleted_at = timezone.now()
                                related_obj.save()
                                # Recursively handle related objects
                                soft_delete_related_objects(
                                    related_obj._meta.app_label,
                                    related_obj._meta.model_name,
                                    related_obj.pk,
                                    using,
                                )
                else:
                    # Handle other relationships
                    related_queryset = getattr(instance, related_name)(manager="objects").all()

                    for related_obj in related_queryset:
                        if hasattr(related_obj, "deleted_at"):
                            if not related_obj.deleted_at:
                                related_obj.deleted_at = timezone.now()
                                related_obj.save()
                                # Recursively handle related objects
                                soft_delete_related_objects(
                                    related_obj._meta.app_label,
                                    related_obj._meta.model_name,
                                    related_obj.pk,
                                    using,
                                )
            except Exception as e:
                # Log the error or handle as needed
                print(f"Error handling relation {related_name}: {str(e)}")
                continue

    # Finally, soft delete the instance itself if it hasn't been deleted yet
    if hasattr(instance, "deleted_at") and not instance.deleted_at:
        instance.deleted_at = timezone.now()
        instance.save()


# @shared_task
def restore_related_objects(app_label, model_name, instance_pk, using=None):
    pass


def _detach_hour_ledger_from_purged_service_logs(cutoff):
    """Unlink hour ledger entries from work logs this purge is about to destroy.

    WITHOUT THIS, THE WHOLE NIGHTLY PURGE STOPS. The purge hard deletes workspaces,
    projects and work items, and `ServiceLog.issue` is CASCADE, so their work logs are
    hard deleted with them. `ServiceHourLedgerEntry.service_log` is DO_NOTHING, which
    means Django emits the database constraint and then does nothing about it -- so
    Postgres refuses the delete with an IntegrityError, and because these are single
    queryset `.delete()` calls the failure takes the entire task down, not just one row.
    This is the same hazard the client delete guard documents
    (`app/views/service_client/base.py`), arriving through a door nobody opens by hand.

    Detaching is safe here in a way it would never be anywhere else, and the distinction
    is worth being precise about:

      * the work log is about to cease to exist, so there is nothing left to debit twice
        and the idempotency the foreign key normally guarantees has nothing to protect;
      * `reconcile_period` sums `hours` grouped by `entry_type`, and neither of those is
        touched -- so every balance still reconciles, before and after;
      * the hours themselves stay, which is what acceptance criterion 20 actually asks
        for. It requires that balance never disappear without a record, not that the
        record keep pointing at a row the retention policy deleted.

    **`SET_NULL` on the foreign key would NOT be the same fix, and would be a bug.**
    `soft_delete_related_objects` has a branch that nulls SET_NULL columns on every
    ordinary soft delete -- and a work log is soft deleted on every ordinary *edit*,
    because `replace_service_log_batch` deletes and recreates. That would clear the link
    exactly when `reverse_debit` needs it, and the next delete would reverse a second
    time and credit hours that never existed. The narrow, once-a-day detach is the
    difference between the two.

    Imported inside the function, like the rest of this task's model access.
    """
    from django.db.models import Q

    from plane.db.models import ServiceHourLedgerEntry

    return ServiceHourLedgerEntry.all_objects.filter(
        Q(service_log__deleted_at__lt=cutoff)
        | Q(service_log__issue__deleted_at__lt=cutoff)
        | Q(service_log__project__deleted_at__lt=cutoff)
        | Q(service_log__workspace__deleted_at__lt=cutoff)
    ).update(service_log=None)


@shared_task
def hard_delete():
    from plane.db.models import (
        Workspace,
        Project,
        Cycle,
        Module,
        Issue,
        Page,
        IssueView,
        Label,
        State,
        IssueActivity,
        IssueComment,
        IssueLink,
        IssueReaction,
        UserFavorite,
        ModuleIssue,
        CycleIssue,
        Estimate,
        EstimatePoint,
    )

    days = settings.HARD_DELETE_AFTER_DAYS

    _detach_hour_ledger_from_purged_service_logs(timezone.now() - timezone.timedelta(days=days))

    # check delete workspace
    _ = Workspace.all_objects.filter(deleted_at__lt=timezone.now() - timezone.timedelta(days=days)).delete()

    # check delete project
    _ = Project.all_objects.filter(deleted_at__lt=timezone.now() - timezone.timedelta(days=days)).delete()

    # check delete cycle
    _ = Cycle.all_objects.filter(deleted_at__lt=timezone.now() - timezone.timedelta(days=days)).delete()

    # check delete module
    _ = Module.all_objects.filter(deleted_at__lt=timezone.now() - timezone.timedelta(days=days)).delete()

    # check delete issue
    _ = Issue.all_objects.filter(deleted_at__lt=timezone.now() - timezone.timedelta(days=days)).delete()

    # check delete page
    _ = Page.all_objects.filter(deleted_at__lt=timezone.now() - timezone.timedelta(days=days)).delete()

    # check delete view
    _ = IssueView.all_objects.filter(deleted_at__lt=timezone.now() - timezone.timedelta(days=days)).delete()

    # check delete label
    _ = Label.all_objects.filter(deleted_at__lt=timezone.now() - timezone.timedelta(days=days)).delete()

    # check delete state
    _ = State.all_objects.filter(deleted_at__lt=timezone.now() - timezone.timedelta(days=days)).delete()

    _ = IssueActivity.all_objects.filter(deleted_at__lt=timezone.now() - timezone.timedelta(days=days)).delete()

    _ = IssueComment.all_objects.filter(deleted_at__lt=timezone.now() - timezone.timedelta(days=days)).delete()

    _ = IssueLink.all_objects.filter(deleted_at__lt=timezone.now() - timezone.timedelta(days=days)).delete()

    _ = IssueReaction.all_objects.filter(deleted_at__lt=timezone.now() - timezone.timedelta(days=days)).delete()

    _ = UserFavorite.all_objects.filter(deleted_at__lt=timezone.now() - timezone.timedelta(days=days)).delete()

    _ = ModuleIssue.all_objects.filter(deleted_at__lt=timezone.now() - timezone.timedelta(days=days)).delete()

    _ = CycleIssue.all_objects.filter(deleted_at__lt=timezone.now() - timezone.timedelta(days=days)).delete()

    _ = Estimate.all_objects.filter(deleted_at__lt=timezone.now() - timezone.timedelta(days=days)).delete()

    _ = EstimatePoint.all_objects.filter(deleted_at__lt=timezone.now() - timezone.timedelta(days=days)).delete()

    # at last, check for every thing which ever is left and delete it
    # Get all Django models
    all_models = apps.get_models()

    # Iterate through all models
    for model in all_models:
        # Check if the model has a 'deleted_at' field
        if hasattr(model, "deleted_at"):
            # Get all instances where 'deleted_at' is greater than 30 days ago
            _ = model.all_objects.filter(deleted_at__lt=timezone.now() - timezone.timedelta(days=days)).delete()

    return
