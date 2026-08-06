# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Acceptance criterion 14: concurrent debits must not corrupt the balance.

**Isolated in its own module, and marked ``slow``.** Every other test in this phase runs
under ``--reuse-db``, which wraps each test in a transaction that is rolled back -- and
inside a single transaction ``select_for_update()`` never contends with anything, so the
lock this file exists to prove would appear to work even if it were deleted. These tests
need ``transaction=True``, which truncates tables instead of rolling back, and real
threads with real connections.

That makes them the slowest tests in the suite and the easiest to make flaky, which is
why they are here rather than mixed in with the fast ones.
"""

# Python imports
import threading
from datetime import date
from decimal import Decimal

# Third party imports
import pytest

# Django imports
from django.db import connections, transaction

# Module imports
from plane.db.models import (
    ServiceBillingType,
    ServiceContractPeriod,
    ServiceHourLedgerEntry,
    ServiceLedgerEntryType,
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
from plane.utils.service_pool import apply_debit, reconcile_period, resolve_period

pytestmark = [pytest.mark.unit, pytest.mark.slow]


def _segment(worked_on, minutes):
    return type(
        "Seg",
        (),
        {
            "worked_on": worked_on,
            "start_time": None,
            "end_time": None,
            "raw_duration_minutes": minutes,
            "suggested_hour_type": None,
            "reason": "",
        },
    )()


@pytest.mark.django_db(transaction=True)
def test_concurrent_debits_do_not_corrupt_the_balance(monkeypatch):
    """Eight threads debit 1h each from a 30h pool. The result must be exactly 8h.

    This is the test that ``select_for_update()`` and the ``F()`` expression exist for,
    and it fails loudly if either is removed:

    * without the lock, threads interleave between reading and writing;
    * without ``F()``, each thread writes back a value it read before the others
      committed, and the pool loses debits -- the classic lost update.

    The ledger is checked as well as the total, because they are two independent
    statements: eight rows must exist *and* the total must be eight. A bug that dropped a
    ledger row and the matching hour would keep them consistent with each other while
    being wrong, which is precisely what ``reconcile_period`` cannot catch on its own.
    """
    monkeypatch.setattr("celery.app.task.Task.apply_async", lambda *args, **kwargs: None)

    service_client = ServiceClientFactory(name="Concorrencia")
    workspace = service_client.workspace
    contract = ServiceContractFactory(
        service_client=service_client,
        code="CONC-1",
        monthly_hours=Decimal("30.0000"),
        starts_on=date(2026, 1, 1),
        ends_on=date(2026, 12, 31),
    )
    project = ProjectFactory(name="Concorrencia", workspace=workspace, service_client=service_client)
    project.is_time_tracking_enabled = True
    project.save()

    author = UserFactory()
    hour_type = ServiceHourTypeFactory(workspace=workspace, name="Comercial", multiplier=Decimal("1.00"))
    pool_route = ServiceBillingTypeFactory(
        workspace=workspace, name="Contrato", billing_route=ServiceBillingType.BillingRoute.DEBIT_POOL
    )

    assert hour_type.multiplier == Decimal("1.00")
    assert contract.monthly_hours == Decimal("30.0000")

    period = resolve_period(contract, date(2026, 1, 15))
    assert period.consumed_hours == Decimal("0.0000"), "positive control: the pool starts empty"

    worker_count = 8
    logs = []

    for _ in range(worker_count):
        issue = IssueFactory(project=project)
        rows = build_batch_rows(
            issue=issue,
            author=author,
            description="Simultaneo",
            billing_type=pool_route,
            hour_type=hour_type,
            segments=[_segment(date(2026, 1, 15), 60)],
        )
        create_service_log_batch(rows)
        logs.append(rows[0])

    # All threads wait on one barrier so they collide as hard as possible. Releasing
    # them one at a time would serialise the debits and the test would pass without a
    # lock -- which is the failure mode of most concurrency tests.
    barrier = threading.Barrier(worker_count)
    errors = []

    def debit(service_log):
        try:
            barrier.wait(timeout=30)
            with transaction.atomic():
                apply_debit(service_log, actor=author)
        except Exception as error:  # noqa: BLE001 - reported, not swallowed
            errors.append(error)
        finally:
            # Each thread gets its own connection and must close it, or the test
            # database cannot be truncated at teardown.
            connections.close_all()

    threads = [threading.Thread(target=debit, args=(log,)) for log in logs]

    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join(timeout=60)

    assert not errors, f"threads raised: {errors}"

    final = ServiceContractPeriod.objects.get(pk=period.pk)
    debit_rows = ServiceHourLedgerEntry.objects.filter(
        period_id=period.pk, entry_type=ServiceLedgerEntryType.DEBIT
    )

    assert debit_rows.count() == worker_count, "one ledger row per work log, none lost"
    assert final.consumed_hours == Decimal("8.0000"), "no debit was lost to a race"
    assert final.balance_hours == Decimal("22.0000")
    assert reconcile_period(final)["is_consistent"]


@pytest.mark.django_db(transaction=True)
def test_concurrent_materialisation_of_one_competency_creates_one_period(monkeypatch):
    """Two threads resolving the same month must not produce two pools.

    The partial unique index is what settles it: one ``create`` wins and the loser reads
    the winner's row. Two periods for one competency would mean two pools for one month,
    each with its own quota -- the client's hours silently doubled.
    """
    monkeypatch.setattr("celery.app.task.Task.apply_async", lambda *args, **kwargs: None)

    service_client = ServiceClientFactory(name="Materializa")
    contract = ServiceContractFactory(
        service_client=service_client,
        code="MAT-1",
        monthly_hours=Decimal("30.0000"),
        starts_on=date(2026, 1, 1),
        ends_on=date(2026, 12, 31),
    )

    assert not ServiceContractPeriod.objects.filter(contract=contract).exists()

    barrier = threading.Barrier(4)
    results = []
    errors = []

    def materialise():
        try:
            barrier.wait(timeout=30)
            results.append(resolve_period(contract, date(2026, 5, 10)))
        except Exception as error:  # noqa: BLE001
            errors.append(error)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=materialise) for _ in range(4)]

    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join(timeout=60)

    assert not errors, f"threads raised: {errors}"

    periods = ServiceContractPeriod.objects.filter(
        contract=contract, competence_year=2026, competence_month=5
    )

    assert periods.count() == 1
    assert len({str(period.pk) for period in results}) == 1, "every thread saw the same period"
    assert (
        ServiceHourLedgerEntry.objects.filter(
            period=periods.first(), entry_type=ServiceLedgerEntryType.GRANT
        ).count()
        == 1
    ), "the quota was granted exactly once"
    assert reconcile_period(periods.first())["is_consistent"]



@pytest.mark.django_db(transaction=True)
def test_a_debit_racing_a_close_cannot_land_in_the_closed_month(monkeypatch):
    """What the row lock protects that ``F()`` cannot. Acceptance criterion 14, and 13.

    ``apply_debit`` reads the period's ``status``, decides it is open, and then debits.
    Those are two steps, and an ``F()`` expression does nothing for the gap between them:
    without ``select_for_update()`` a concurrent ``close_period`` fits in the middle, and
    the debit lands in a month that has just been invoiced and whose ledger has already
    been settled to zero.

    The assertion is deliberately written as an **exclusive or over outcomes** rather than
    as a fixed expectation, because which thread wins the lock is genuinely
    non-deterministic and pinning it would make the test flaky for a reason that has
    nothing to do with correctness. What must hold either way is that the two results are
    *consistent with each other*:

    * the debit won -- the period closed with 1h consumed and its ledger sums to zero; or
    * the close won -- the debit was refused with ``PERIOD_IS_CLOSED`` and the period is
      closed with nothing consumed.

    The forbidden outcome, and the one this exists to catch, is a closed period carrying a
    debit that its own settlement never accounted for.
    """
    monkeypatch.setattr("celery.app.task.Task.apply_async", lambda *args, **kwargs: None)

    from plane.utils.service_pool import ServicePoolValidationError, close_period

    service_client = ServiceClientFactory(name="Fecha")
    workspace = service_client.workspace
    contract = ServiceContractFactory(
        service_client=service_client,
        code="FEC-1",
        monthly_hours=Decimal("30.0000"),
        starts_on=date(2026, 1, 1),
        ends_on=date(2026, 12, 31),
    )
    project = ProjectFactory(name="Fecha", workspace=workspace, service_client=service_client)
    project.is_time_tracking_enabled = True
    project.save()

    author = UserFactory()
    hour_type = ServiceHourTypeFactory(workspace=workspace, name="Comercial", multiplier=Decimal("1.00"))
    pool_route = ServiceBillingTypeFactory(
        workspace=workspace, name="Contrato", billing_route=ServiceBillingType.BillingRoute.DEBIT_POOL
    )

    period = resolve_period(contract, date(2026, 1, 15))
    assert period.status == "open", "positive control: it starts open"

    issue = IssueFactory(project=project)
    rows = build_batch_rows(
        issue=issue,
        author=author,
        description="Corrida",
        billing_type=pool_route,
        hour_type=hour_type,
        segments=[_segment(date(2026, 1, 15), 60)],
    )
    create_service_log_batch(rows)
    service_log = rows[0]

    barrier = threading.Barrier(2)
    outcomes = {}

    def debit():
        try:
            barrier.wait(timeout=30)
            with transaction.atomic():
                apply_debit(service_log, actor=author)
            outcomes["debit"] = "applied"
        except ServicePoolValidationError as error:
            outcomes["debit"] = error.code
        except Exception as error:  # noqa: BLE001
            outcomes["debit"] = f"unexpected: {error}"
        finally:
            connections.close_all()

    def close():
        try:
            barrier.wait(timeout=30)
            close_period(ServiceContractPeriod.objects.get(pk=period.pk), actor=author)
            outcomes["close"] = "closed"
        except ServicePoolValidationError as error:
            outcomes["close"] = error.code
        except Exception as error:  # noqa: BLE001
            outcomes["close"] = f"unexpected: {error}"
        finally:
            connections.close_all()

    threads = [threading.Thread(target=debit), threading.Thread(target=close)]

    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join(timeout=60)

    assert not any(
        str(value).startswith("unexpected") for value in outcomes.values()
    ), f"threads raised something unplanned: {outcomes}"

    final = ServiceContractPeriod.objects.get(pk=period.pk)
    debit_landed = ServiceHourLedgerEntry.objects.filter(
        service_log_id=service_log.pk, entry_type=ServiceLedgerEntryType.DEBIT
    ).exists()

    assert final.status == "closed", f"the close should have happened: {outcomes}"

    if debit_landed:
        assert final.consumed_hours == Decimal("1.0000"), (
            "the debit won the lock, so the close must have settled around it"
        )
    else:
        assert outcomes["debit"] == "PERIOD_IS_CLOSED", (
            f"if the debit did not land it must have been refused with a reason: {outcomes}"
        )
        assert final.consumed_hours == Decimal("0.0000")

    # The invariant that makes either outcome acceptable, and the forbidden third one
    # impossible: a closed period's ledger sums to exactly zero.
    assert reconcile_period(final)["is_consistent"], (
        f"a closed month must account for whatever it contains: {outcomes}"
    )
