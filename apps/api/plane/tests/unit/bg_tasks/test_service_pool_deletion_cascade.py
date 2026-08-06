# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""The deletion cascade, and the two bugs it would otherwise leave behind.

Both of these were verified against the real code before being fixed, and both are the
kind that only show up as money:

1. ``soft_delete_related_objects``' catch-all assigns ``deleted_at`` and calls
   ``.save()``, which never runs ``SoftDeleteModel.delete()`` and therefore never runs
   ``ServiceLog.delete()``, where the reversal lives. ``ServiceLog.issue`` is CASCADE, so
   deleting a work item left the pool debited for hours belonging to a work item that no
   longer existed.
2. The nightly ``hard_delete`` task hard-cascades to work logs, and the ledger's
   ``service_log`` foreign key is DO_NOTHING -- which in Postgres means NO ACTION, so the
   delete is refused with an ``IntegrityError`` that takes the **whole purge** down, not
   just one row.
"""

# Python imports
from datetime import date, timedelta
from decimal import Decimal

# Third party imports
import pytest

# Django imports
from django.conf import settings
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

# Module imports
from plane.bgtasks.deletion_task import (
    _detach_hour_ledger_from_purged_service_logs,
    soft_delete_related_objects,
)
from plane.db.models import (
    ServiceBillingType,
    ServiceContractPeriod,
    ServiceHourLedgerEntry,
    ServiceLedgerEntryType,
    ServiceLog,
)
from plane.tests.factories import (
    IssueFactory,
    ProjectFactory,
    ServiceBillingTypeFactory,
    ServiceClientFactory,
    ServiceContractFactory,
    ServiceHourTypeFactory,
    UserFactory,
)
from plane.utils.service_log import build_batch_rows, create_service_log_batch
from plane.utils.service_pool import apply_debit, close_period, reconcile_period, resolve_period

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def no_celery_dispatch(monkeypatch):
    """The cascade is dispatched with ``.delay``; there is no broker in tests.

    Patched to a no-op rather than run inline, because these tests call
    ``soft_delete_related_objects`` **directly**. Running it inline as well would execute
    the cascade twice and make an idempotency bug look like correct behaviour -- which is
    the opposite of what this file is for.
    """
    monkeypatch.setattr("celery.app.task.Task.apply_async", lambda *args, **kwargs: None)


@pytest.fixture
def debited(db):
    """A work item with one 3h work log that has debited a live January pool."""
    service_client = ServiceClientFactory(name="Cascata")
    workspace = service_client.workspace
    contract = ServiceContractFactory(
        service_client=service_client,
        code="CAS-1",
        monthly_hours=Decimal("30.0000"),
        starts_on=date(2026, 1, 1),
        ends_on=date(2026, 12, 31),
    )
    project = ProjectFactory(name="Cascata", workspace=workspace, service_client=service_client)
    project.is_time_tracking_enabled = True
    project.save()

    author = UserFactory()
    hour_type = ServiceHourTypeFactory(workspace=workspace, name="Comercial", multiplier=Decimal("1.00"))
    pool_route = ServiceBillingTypeFactory(
        workspace=workspace, name="Contrato", billing_route=ServiceBillingType.BillingRoute.DEBIT_POOL
    )

    issue = IssueFactory(project=project)
    rows = build_batch_rows(
        issue=issue,
        author=author,
        description="Atendimento",
        billing_type=pool_route,
        hour_type=hour_type,
        segments=[
            type(
                "Seg",
                (),
                {
                    "worked_on": date(2026, 1, 15),
                    "start_time": None,
                    "end_time": None,
                    "raw_duration_minutes": 180,
                    "suggested_hour_type": None,
                    "reason": "",
                },
            )()
        ],
    )
    create_service_log_batch(rows)
    apply_debit(rows[0], actor=author)

    period = resolve_period(contract, date(2026, 1, 15))
    assert period.consumed_hours == Decimal("3.0000"), "positive control: the debit landed"

    return {
        "issue": issue,
        "log": rows[0],
        "period": period,
        "contract": contract,
        "author": author,
        "project": project,
    }


class TestWorkItemCascade:
    def test_deleting_a_work_item_reverses_its_pool_debit(self, debited):
        """The bug fixed by the explicit ``ServiceLog`` branch.

        Before the branch, this deletion went through the catch-all, which soft deletes by
        assigning ``deleted_at`` and calling ``.save()`` -- bypassing
        ``ServiceLog.delete()`` and therefore the reversal. The pool stayed 3h down for a
        work item that no longer existed.
        """
        issue = debited["issue"]
        period = debited["period"]

        issue.delete()
        soft_delete_related_objects("db", "issue", issue.pk)

        period.refresh_from_db()

        assert ServiceLog.objects.filter(pk=debited["log"].pk).count() == 0, "the log was deleted"
        assert period.consumed_hours == Decimal("0.0000"), "and its hours came back"
        assert period.balance_hours == Decimal("30.0000")
        assert ServiceHourLedgerEntry.objects.filter(
            service_log_id=debited["log"].pk, entry_type=ServiceLedgerEntryType.REVERSAL
        ).exists()
        assert reconcile_period(period)["is_consistent"]

    def test_the_cascade_is_idempotent_because_celery_retries(self, debited):
        """Celery retries tasks. Without the unique index covering ``REVERSAL``, a retry
        credits hours that never existed -- so this runs the cascade three times."""
        issue = debited["issue"]
        period = debited["period"]

        issue.delete()
        for _ in range(3):
            soft_delete_related_objects("db", "issue", issue.pk)

        period.refresh_from_db()

        assert period.consumed_hours == Decimal("0.0000")
        assert (
            ServiceHourLedgerEntry.objects.filter(
                service_log_id=debited["log"].pk, entry_type=ServiceLedgerEntryType.REVERSAL
            ).count()
            == 1
        )

    def test_a_work_log_in_a_closed_period_survives_the_cascade(self, debited):
        """An invoiced month is never rewritten by a cascade.

        The work log is deliberately left intact instead of being deleted without its
        reversal. Refusing is the same choice the client delete guard makes: the
        alternative is a balance that no longer matches an invoice the client has already
        received.
        """
        issue = debited["issue"]
        period = debited["period"]
        close_period(period, actor=debited["author"])

        issue.delete()
        soft_delete_related_objects("db", "issue", issue.pk)

        closed = ServiceContractPeriod.objects.get(pk=period.pk)

        assert ServiceLog.objects.filter(pk=debited["log"].pk).exists(), "left intact on purpose"
        assert closed.consumed_hours == Decimal("3.0000"), "the invoiced total did not move"
        assert not ServiceHourLedgerEntry.objects.filter(
            service_log_id=debited["log"].pk, entry_type=ServiceLedgerEntryType.REVERSAL
        ).exists()
        assert reconcile_period(closed)["is_consistent"]

    def test_the_catch_all_still_handles_other_cascading_models(self, debited):
        """The branch is scoped to ``ServiceLog`` and changes nothing else.

        Making the catch-all call ``.delete()`` on every model with a ``deleted_at``
        column would start executing the custom ``delete()`` of dozens of core models.
        This asserts an ordinary CASCADE child of the same work item is still soft deleted
        by the untouched catch-all.
        """
        from plane.db.models import IssueLink

        issue = debited["issue"]
        link = IssueLink.objects.create(
            issue=issue,
            project=debited["project"],
            workspace=debited["project"].workspace,
            url="https://example.com",
            title="ref",
        )

        issue.delete()
        soft_delete_related_objects("db", "issue", issue.pk)

        assert IssueLink.objects.filter(pk=link.pk).count() == 0
        assert IssueLink.all_objects.filter(pk=link.pk).exists(), "soft deleted, not destroyed"


class TestHardDeleteDetach:
    def test_hard_deleting_a_ledger_backed_work_log_would_raise_without_the_detach(self, debited):
        """CHARACTERISATION of the hazard, proving it is real rather than theorised.

        The ledger's ``service_log`` foreign key is DO_NOTHING, which Django renders as a
        database constraint it then does nothing about -- so Postgres refuses. Because the
        nightly purge deletes through a single queryset ``.delete()``, this
        ``IntegrityError`` would take the entire task down for the whole instance, not
        just skip one row.
        """
        # `all_objects` is a plain Manager, so its queryset `delete()` is a genuine hard
        # delete -- exactly what the purge issues.
        #
        # `check_constraints()` is required and is not a test artefact: Django creates
        # foreign keys as DEFERRABLE INITIALLY DEFERRED, so the violation fires at COMMIT
        # rather than at the statement. In production that means the nightly purge dies
        # when its transaction commits, having appeared to work; here, where the test
        # transaction is rolled back and never commits, the check has to be asked for
        # explicitly or the failure would be invisible.
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                ServiceLog.all_objects.filter(pk=debited["log"].pk).delete()
                connection.check_constraints()

    def test_the_detach_unlinks_entries_of_work_logs_about_to_be_purged(self, debited):
        """And then the hard delete succeeds.

        Detaching is safe **here and nowhere else**: the work log is about to cease to
        exist, so there is nothing left to debit twice and idempotency has nothing to
        protect. The ``hours`` and ``entry_type`` stay, which is what criterion 20
        actually asks for -- and what keeps the period reconciling.
        """
        cutoff = timezone.now() - timedelta(days=settings.HARD_DELETE_AFTER_DAYS)
        period = debited["period"]

        # Age the work item past the retention window, the way the purge finds it.
        debited["issue"].delete()
        from plane.db.models import Issue

        Issue.all_objects.filter(pk=debited["issue"].pk).update(
            deleted_at=cutoff - timedelta(days=1)
        )

        detached = _detach_hour_ledger_from_purged_service_logs(cutoff)

        assert detached >= 1

        entry = ServiceHourLedgerEntry.objects.get(
            period=period, entry_type=ServiceLedgerEntryType.DEBIT
        )
        assert entry.service_log_id is None, "unlinked"
        assert entry.hours == Decimal("-3.0000"), "the hours survive"
        assert entry.entry_type == ServiceLedgerEntryType.DEBIT

        # The purge can now do its job, and the deferred check passes -- which is the
        # whole difference between a working nightly task and a broken one.
        with transaction.atomic():
            ServiceLog.all_objects.filter(pk=debited["log"].pk).delete()
            connection.check_constraints()

        assert not ServiceLog.all_objects.filter(pk=debited["log"].pk).exists()

    def test_reconciliation_is_unaffected_by_the_detach(self, debited):
        """The property that makes the detach acceptable at all: ``reconcile_period`` sums
        ``hours`` grouped by ``entry_type``, and neither is touched."""
        cutoff = timezone.now() - timedelta(days=settings.HARD_DELETE_AFTER_DAYS)
        period = debited["period"]

        assert reconcile_period(period)["is_consistent"], "positive control before the detach"

        ServiceLog.all_objects.filter(pk=debited["log"].pk).update(
            deleted_at=cutoff - timedelta(days=1)
        )
        _detach_hour_ledger_from_purged_service_logs(cutoff)

        assert reconcile_period(ServiceContractPeriod.objects.get(pk=period.pk))["is_consistent"]

    def test_a_live_work_log_is_never_detached(self, debited):
        """Absence with its reason: the cutoff is what selects rows, and a live work log
        is not past it. The positive control is the test above, where ageing the row does
        detach it -- without that pairing, a detach that did nothing at all would pass
        this test.
        """
        cutoff = timezone.now() - timedelta(days=settings.HARD_DELETE_AFTER_DAYS)

        detached = _detach_hour_ledger_from_purged_service_logs(cutoff)

        entry = ServiceHourLedgerEntry.objects.get(
            period=debited["period"], entry_type=ServiceLedgerEntryType.DEBIT
        )

        assert detached == 0
        assert entry.service_log_id == debited["log"].pk, "still linked"
