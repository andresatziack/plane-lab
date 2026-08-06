# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""The clients-needing-attention panel. Section 9, criteria 7, 18 and 19.

Every "this alert does not fire" assertion below names the alert it expects to be absent
**and asserts that a different, expected alert is present in the same call**. An empty
alert list is the single most likely false green in this module: a panel that returned
``[]`` unconditionally would satisfy every negative assertion written the lazy way.
"""

# Python imports
from datetime import date
from decimal import Decimal

# Third party imports
import pytest

# Module imports
from plane.db.models import (
    ServiceBillingType,
    ServiceContract,
    ServiceContractAlertDismissal,
    ServiceContractPeriod,
    ServiceHourLedgerEntry,
    ServiceLedgerEntryType,
)
from plane.tests.factories import (
    ProjectFactory,
    ServiceBillingTypeFactory,
    ServiceClientFactory,
    ServiceContractFactory,
    ServiceHourTypeFactory,
    UserFactory,
)
from plane.utils.service_pool import close_period, resolve_period
from plane.utils.service_pool_alerts import (
    ACCRUED_BALANCE_ABOVE_THRESHOLD,
    ALERT_REARM_BAND_HOURS,
    HIGH_CONSUMPTION_BEFORE_MIDMONTH,
    HOURS_DISCARDED_BY_CAP,
    LOW_CONSUMPTION_AT_MONTH_END,
    NEGATIVE_BALANCE,
    NO_SERVICE_LOGS_IN_MONTH,
    contracts_without_default,
    dismiss_alert,
    evaluate_period,
    is_alert_dismissed,
    visible_alerts_for_period,
    workspace_alert_panel,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def no_celery_dispatch(monkeypatch):
    monkeypatch.setattr("celery.app.task.Task.apply_async", lambda *args, **kwargs: None)


@pytest.fixture
def actor(db):
    return UserFactory()


@pytest.fixture
def pool(db, actor):
    """A 30h/month contract with the default thresholds, and January materialised.

    Asserts the thresholds explicitly: 80% high and 30% low are what make the day-10
    example of criterion 18 land where it should, and a fixture that silently carried
    other values would make the arithmetic below meaningless.
    """
    service_client = ServiceClientFactory(name="Alertas")
    contract = ServiceContractFactory(
        service_client=service_client,
        code="ALE-1",
        monthly_hours=Decimal("30.0000"),
        starts_on=date(2026, 1, 1),
        ends_on=date(2026, 12, 31),
    )
    project = ProjectFactory(
        name="Alertas", workspace=service_client.workspace, service_client=service_client
    )

    assert contract.high_consumption_threshold_pct == Decimal("80.00")
    assert contract.low_consumption_threshold_pct == Decimal("30.00")

    period = resolve_period(contract, date(2026, 1, 1), actor=actor)

    return {"contract": contract, "period": period, "project": project, "client": service_client}


def consume(period, hours):
    """Move a period's consumption and its ledger together.

    Both, always: writing only the column would leave the period unreconciled and a test
    asserting on alerts would be measuring a state the system can never actually be in.
    """
    ServiceContractPeriod.objects.filter(pk=period.pk).update(consumed_hours=Decimal(hours))
    ServiceHourLedgerEntry.objects.create(
        workspace_id=period.workspace_id,
        contract_id=period.contract_id,
        period=period,
        hours=-Decimal(hours),
        entry_type=ServiceLedgerEntryType.DEBIT,
    )
    return ServiceContractPeriod.objects.get(pk=period.pk)


def codes(alerts):
    return {alert["code"] for alert in alerts}


class TestHighConsumption:
    def test_ninety_percent_on_the_tenth_raises_the_high_consumption_alert(self, pool):
        """Criterion 18, with the example's own numbers: 27h of 30h on the 10th."""
        period = consume(pool["period"], "27.0000")

        alerts = evaluate_period(period, reference_date=date(2026, 1, 10), service_log_count=3)

        assert HIGH_CONSUMPTION_BEFORE_MIDMONTH in codes(alerts)
        entry = next(a for a in alerts if a["code"] == HIGH_CONSUMPTION_BEFORE_MIDMONTH)
        assert entry["consumed_pct"] == "90.00"
        assert entry["day_of_month"] == 10
        assert entry["severity"] == "high_consumption"

    def test_the_same_consumption_late_in_the_month_does_not_raise_it_but_raises_another(
        self, pool
    ):
        """Absence WITH its reason and WITH a positive control in the same call.

        90% consumption on the 25th is not the criterion-18 alert -- the whole point of
        that alert is that it is *early*, while there is still time to act. The
        projection alert is what fires instead, which proves the engine ran.
        """
        period = consume(pool["period"], "27.0000")

        alerts = evaluate_period(period, reference_date=date(2026, 1, 25), service_log_count=3)

        assert HIGH_CONSUMPTION_BEFORE_MIDMONTH not in codes(alerts)
        assert alerts, "the engine ran and produced something"
        assert "PROJECTED_OVERRUN" in codes(alerts), "positive control in the same call"

    def test_a_negative_balance_raises_its_own_alert(self, pool):
        """Criterion 7's alert half."""
        period = consume(pool["period"], "33.0000")

        alerts = evaluate_period(period, reference_date=date(2026, 1, 20), service_log_count=5)

        assert NEGATIVE_BALANCE in codes(alerts)
        entry = next(a for a in alerts if a["code"] == NEGATIVE_BALANCE)
        assert entry["balance_hours"] == "-3.0000"

    def test_a_healthy_pool_early_in_the_month_raises_no_consumption_alert(self, pool):
        """The positive control for the whole module, inverted: a pool that is fine must
        produce neither extreme, and this is what proves the alerts above were not simply
        always-on."""
        period = consume(pool["period"], "9.0000")

        alerts = evaluate_period(period, reference_date=date(2026, 1, 10), service_log_count=4)

        assert HIGH_CONSUMPTION_BEFORE_MIDMONTH not in codes(alerts)
        assert NEGATIVE_BALANCE not in codes(alerts)
        assert LOW_CONSUMPTION_AT_MONTH_END not in codes(alerts)
        assert NO_SERVICE_LOGS_IN_MONTH not in codes(alerts)


class TestLowConsumption:
    def test_a_month_with_no_work_logs_raises_the_churn_alert(self, pool):
        """Criterion 19."""
        alerts = evaluate_period(
            pool["period"], reference_date=date(2026, 1, 20), service_log_count=0
        )

        assert NO_SERVICE_LOGS_IN_MONTH in codes(alerts)
        entry = next(a for a in alerts if a["code"] == NO_SERVICE_LOGS_IN_MONTH)
        assert entry["severity"] == "low_consumption"
        assert entry["competence"] == "2026-01"
        assert entry["allowance_hours_in_period"] == "0.0000", (
            "no allowance work either, so the alert really does mean what it says"
        )

    def test_the_churn_alert_still_fires_for_a_client_served_only_through_allowances(
        self, pool, actor
    ):
        """**Characterisation test, and an accepted limitation with its wording mitigated.**

        Work paid by a work item allowance has ``debited_period`` null, so it does not count
        toward this alert. The alert therefore fires for a client who is being actively
        served -- and that is the *correct* reading, because this alert is about consumption
        **of the contract**, and a client whose work all goes to project allowances genuinely
        is not consuming the support they pay for. That is a real renewal signal.

        What would be wrong is the sentence a reader takes from it. "Cliente sem
        atendimento" about a client with 40h of project work gets somebody to make an
        embarrassing phone call. So the alert carries the allowance hours, and the frontend
        renders "sem apontamentos no contrato (Nh em bolsas de projeto)".

        Fixed here so that a future phase changing the logic -- Phase 9 owns the dashboards
        -- does it deliberately rather than by accident.
        """
        from plane.tests.factories import IssueFactory
        from plane.utils.service_allowance import credit_allowance
        from plane.utils.service_log import build_batch_rows, create_service_log_batch
        from plane.utils.service_pool import apply_debit

        issue = IssueFactory(project=pool["project"])
        credit_allowance(issue, Decimal("40.0000"), actor=actor)

        workspace = pool["client"].workspace
        hour_type = ServiceHourTypeFactory(workspace=workspace, multiplier=Decimal("1.00"))
        pool_route = ServiceBillingTypeFactory(
            workspace=workspace, billing_route=ServiceBillingType.BillingRoute.DEBIT_POOL
        )

        rows = build_batch_rows(
            issue=issue,
            author=actor,
            description="Projeto",
            billing_type=pool_route,
            hour_type=hour_type,
            segments=[
                type(
                    "Seg",
                    (),
                    {
                        "worked_on": date(2026, 1, 12),
                        "start_time": None,
                        "end_time": None,
                        "raw_duration_minutes": 8 * 60,
                        "suggested_hour_type": None,
                        "reason": "",
                    },
                )()
            ],
        )
        create_service_log_batch(rows)
        for row in rows:
            apply_debit(row, actor=actor)

        assert rows[0].debited_allowance_id is not None, "the allowance paid, not the contract"

        alerts = evaluate_period(pool["period"], reference_date=date(2026, 1, 20))

        assert NO_SERVICE_LOGS_IN_MONTH in codes(alerts), "the contract really saw no work"

        entry = next(a for a in alerts if a["code"] == NO_SERVICE_LOGS_IN_MONTH)
        assert entry["allowance_hours_in_period"] == "8.0000", (
            "and the alert says so, so nobody calls this client to ask why they went quiet"
        )

    def test_low_consumption_near_the_month_end_raises_the_alert(self, pool):
        """Section 9. 6h of 30h is 20%, under the 30% threshold, on the 28th."""
        period = consume(pool["period"], "6.0000")

        alerts = evaluate_period(period, reference_date=date(2026, 1, 28), service_log_count=2)

        assert LOW_CONSUMPTION_AT_MONTH_END in codes(alerts)
        entry = next(a for a in alerts if a["code"] == LOW_CONSUMPTION_AT_MONTH_END)
        assert entry["consumed_pct"] == "20.00"
        assert entry["threshold_pct"] == "30.00"

    def test_the_same_low_consumption_early_in_the_month_does_not_raise_it(self, pool):
        """Absence with its reason: 20% on the 5th is normal, not a churn signal. The
        positive control is that the engine still reports the month has no logs."""
        period = consume(pool["period"], "6.0000")

        alerts = evaluate_period(period, reference_date=date(2026, 1, 5), service_log_count=0)

        assert LOW_CONSUMPTION_AT_MONTH_END not in codes(alerts)
        assert NO_SERVICE_LOGS_IN_MONTH in codes(alerts), "positive control in the same call"

    def test_a_carried_balance_beyond_two_months_raises_the_churn_alert(self, pool, actor):
        """Section 9's "saldo acumulado acima de N meses de horas contratadas"."""
        period = pool["period"]
        ServiceContractPeriod.objects.filter(pk=period.pk).update(carried_hours=Decimal("70.0000"))
        ServiceHourLedgerEntry.objects.create(
            workspace_id=period.workspace_id,
            contract_id=period.contract_id,
            period=period,
            hours=Decimal("70.0000"),
            entry_type=ServiceLedgerEntryType.CARRY_IN,
            origin_period=period,
        )

        alerts = evaluate_period(
            ServiceContractPeriod.objects.get(pk=period.pk),
            reference_date=date(2026, 1, 10),
            service_log_count=1,
        )

        assert ACCRUED_BALANCE_ABOVE_THRESHOLD in codes(alerts)
        entry = next(a for a in alerts if a["code"] == ACCRUED_BALANCE_ABOVE_THRESHOLD)
        assert entry["threshold_hours"] == "60.0000"

    def test_hours_discarded_by_the_cap_raise_the_sharpest_churn_alert(self, db, actor):
        """The clearest form of the signal: the client paid for hours the contract would
        not even let them keep."""
        service_client = ServiceClientFactory(name="TetoAlerta")
        contract = ServiceContractFactory(
            service_client=service_client,
            code="TAL-1",
            monthly_hours=Decimal("30.0000"),
            starts_on=date(2026, 1, 1),
            ends_on=date(2026, 12, 31),
            accrual_cap_mode=ServiceContract.AccrualCapMode.ABSOLUTE,
            accrual_cap_value=Decimal("10.0000"),
        )

        close_period(resolve_period(contract, date(2026, 1, 1), actor=actor), actor=actor)
        february = resolve_period(contract, date(2026, 2, 1), actor=actor)
        close_period(february, actor=actor)

        february.refresh_from_db()
        assert february.discarded_by_cap_hours > 0, "positive control: the cap did discard"

        alerts = evaluate_period(
            february, reference_date=date(2026, 2, 20), service_log_count=0
        )

        assert HOURS_DISCARDED_BY_CAP in codes(alerts)


class TestDismissal:
    def test_dismissing_an_alert_hides_it(self, pool, actor):
        """Section 9: alerts must be dismissible "para não virar ruído"."""
        period = consume(pool["period"], "33.0000")
        assert NEGATIVE_BALANCE in codes(
            visible_alerts_for_period(period, reference_date=date(2026, 1, 20), service_log_count=5)
        ), "positive control before dismissal"

        dismiss_alert(period, NEGATIVE_BALANCE, actor=actor)

        visible = visible_alerts_for_period(
            period, reference_date=date(2026, 1, 20), service_log_count=5
        )

        assert NEGATIVE_BALANCE not in codes(visible)

    def test_the_balance_at_dismissal_is_recorded(self, pool, actor):
        """Decision B2 depends on this column existing: without it there is nothing to
        compare against and the dismissal could only ever be permanent."""
        period = consume(pool["period"], "33.0000")

        dismiss_alert(period, NEGATIVE_BALANCE, actor=actor)

        dismissal = ServiceContractAlertDismissal.objects.get(
            period=period, alert_code=NEGATIVE_BALANCE
        )

        assert dismissal.balance_at_dismissal == Decimal("-3.0000")
        assert dismissal.dismissed_by_id == actor.pk

    def test_the_alert_returns_when_the_balance_worsens_past_the_band(self, pool, actor):
        """Decision B2, and the hole it closes.

        Dismissing at -3h must not silence the same alert at -50h. An alert that fires
        once, on the cheapest day, is no barrier at all. The band is asserted from the
        constant rather than hard-coded, so changing the constant changes the test with
        it instead of breaking it mysteriously.
        """
        period = consume(pool["period"], "33.0000")
        dismiss_alert(period, NEGATIVE_BALANCE, actor=actor)

        # Just inside the band: still quiet.
        period = consume(pool["period"], Decimal("33.0000") + ALERT_REARM_BAND_HOURS - Decimal("1"))
        assert NEGATIVE_BALANCE not in codes(
            visible_alerts_for_period(period, reference_date=date(2026, 1, 20), service_log_count=5)
        )

        # Past the band: it comes back.
        period = consume(pool["period"], "80.0000")
        assert NEGATIVE_BALANCE in codes(
            visible_alerts_for_period(period, reference_date=date(2026, 1, 20), service_log_count=5)
        )

    def test_re_dismissing_a_rearmed_alert_updates_the_recorded_balance(self, pool, actor):
        """Rather than failing on the unique index. The admin is acknowledging the worse
        number, and refusing would leave them unable to quiet an alert they just saw."""
        period = consume(pool["period"], "33.0000")
        dismiss_alert(period, NEGATIVE_BALANCE, actor=actor)

        period = consume(pool["period"], "80.0000")
        _dismissal, created = dismiss_alert(period, NEGATIVE_BALANCE, actor=actor)

        stored = ServiceContractAlertDismissal.objects.get(
            period=period, alert_code=NEGATIVE_BALANCE
        )

        assert created is False
        assert stored.balance_at_dismissal == Decimal("-50.0000")
        assert (
            ServiceContractAlertDismissal.objects.filter(
                period=period, alert_code=NEGATIVE_BALANCE
            ).count()
            == 1
        )

    def test_an_improving_balance_keeps_the_alert_dismissed(self, pool, actor):
        """CHARACTERISATION of the accepted limitation.

        Re-arming is one-directional: it fires again when things get worse, not when they
        get better. And there is still **no ceiling on the deficit itself** -- nothing
        here stops a period reaching -200h, only guarantees somebody was told repeatedly
        on the way down. A hard ceiling would contradict D4 and section 4, which forbid
        blocking work already performed. Fixed by this test so a later phase changes it
        deliberately.
        """
        period = consume(pool["period"], "40.0000")
        dismiss_alert(period, NEGATIVE_BALANCE, actor=actor)

        period = consume(pool["period"], "31.0000")

        assert NEGATIVE_BALANCE not in codes(
            visible_alerts_for_period(period, reference_date=date(2026, 1, 20), service_log_count=5)
        )

    def test_is_alert_dismissed_is_false_without_a_dismissal(self, pool):
        assert is_alert_dismissed(None, Decimal("-100.0000")) is False


class TestPanel:
    def test_the_panel_groups_by_contract_and_counts_both_extremes(self, db, actor):
        """Section 9. A client can be overrunning on support and idle on infrastructure,
        and merging them would hide exactly the case that needs the call."""
        service_client = ServiceClientFactory(name="Painel")
        workspace = service_client.workspace

        overrun = ServiceContractFactory(
            service_client=service_client,
            code="PAN-SUP",
            name="Suporte",
            monthly_hours=Decimal("30.0000"),
            starts_on=date(2026, 1, 1),
            ends_on=date(2026, 12, 31),
        )
        idle_client = ServiceClientFactory(name="PainelIdle", workspace=workspace)
        idle = ServiceContractFactory(
            service_client=idle_client,
            code="PAN-INF",
            name="Infra",
            monthly_hours=Decimal("20.0000"),
            starts_on=date(2026, 1, 1),
            ends_on=date(2026, 12, 31),
        )

        overrun_period = resolve_period(overrun, date(2026, 1, 1), actor=actor)
        consume(overrun_period, "33.0000")
        resolve_period(idle, date(2026, 1, 1), actor=actor)

        entries = workspace_alert_panel(workspace.id, reference_date=date(2026, 1, 20))
        by_code = {entry["contract_code"]: entry for entry in entries}

        assert "PAN-SUP" in by_code
        assert by_code["PAN-SUP"]["has_high_consumption"] is True
        assert by_code["PAN-SUP"]["balance_hours"] == "-3.0000"
        assert "PAN-INF" in by_code
        assert by_code["PAN-INF"]["has_low_consumption"] is True
        assert NO_SERVICE_LOGS_IN_MONTH in codes(by_code["PAN-INF"]["alerts"])

    def test_a_client_with_several_contracts_and_no_default_is_flagged(self, db):
        """A configuration fault surfaced before it blocks a technician's first work log
        with AMBIGUOUS_CONTRACT_RESOLUTION."""
        service_client = ServiceClientFactory(name="SemDefault")

        for code in ("SD-1", "SD-2"):
            ServiceContractFactory(
                service_client=service_client,
                code=code,
                starts_on=date(2026, 1, 1),
                ends_on=date(2026, 12, 31),
            )

        faulty = contracts_without_default(service_client.workspace_id)

        assert str(service_client.pk) in faulty

        # POSITIVE CONTROL: marking one as the default clears the fault.
        first = ServiceContractFactory(service_client=service_client, code="SD-1")
        first.is_default = True
        first.save()

        assert str(service_client.pk) not in contracts_without_default(service_client.workspace_id)

    def test_a_single_contract_client_is_never_flagged(self, db):
        """Absence with its reason: one contract resolves without needing the flag, so
        the absence of a default is not a fault. The positive control is the two-contract
        client in the test above."""
        service_client = ServiceClientFactory(name="UmContrato")
        ServiceContractFactory(
            service_client=service_client,
            code="UC-1",
            starts_on=date(2026, 1, 1),
            ends_on=date(2026, 12, 31),
        )

        assert str(service_client.pk) not in contracts_without_default(service_client.workspace_id)



# ---------------------------------------------------------------------------
# Work item allowances -- section 3 of the allowance phase
# ---------------------------------------------------------------------------


class TestAllowanceAlerts:
    """"A bolsa fica com saldo negativo e o alerta aparece" -- the second half of that
    sentence, which is the half a test can be written for."""

    def test_a_negative_allowance_raises_the_alert(self, db, actor):
        from plane.tests.factories import ServiceIssueAllowanceFactory
        from plane.utils.service_pool_alerts import ALLOWANCE_NEGATIVE_BALANCE, evaluate_allowance

        allowance = ServiceIssueAllowanceFactory(
            credited_hours=Decimal("10.0000"), consumed_hours=Decimal("25.0000")
        )

        alerts = evaluate_allowance(allowance)

        assert codes(alerts) == {ALLOWANCE_NEGATIVE_BALANCE}
        entry = alerts[0]
        assert entry["severity"] == "high_consumption"
        assert entry["balance_hours"] == "-15.0000"

    def test_high_consumption_raises_while_the_balance_is_still_positive(self, db):
        """The warning that arrives while there is still time to negotiate an aditivo de
        escopo, rather than after the hours are gone."""
        from plane.tests.factories import ServiceIssueAllowanceFactory
        from plane.utils.service_pool_alerts import ALLOWANCE_HIGH_CONSUMPTION, evaluate_allowance

        allowance = ServiceIssueAllowanceFactory(
            credited_hours=Decimal("40.0000"), consumed_hours=Decimal("34.0000")
        )

        alerts = evaluate_allowance(allowance)

        assert codes(alerts) == {ALLOWANCE_HIGH_CONSUMPTION}
        assert alerts[0]["consumed_pct"] == "85.00"
        assert alerts[0]["threshold_pct"] == "80.00"

    def test_a_healthy_allowance_raises_nothing(self, db):
        """The positive control the two above need. Without it, an ``evaluate_allowance``
        that returned everything unconditionally would satisfy both of them."""
        from plane.tests.factories import ServiceIssueAllowanceFactory
        from plane.utils.service_pool_alerts import evaluate_allowance

        allowance = ServiceIssueAllowanceFactory(
            credited_hours=Decimal("40.0000"), consumed_hours=Decimal("10.0000")
        )

        assert evaluate_allowance(allowance) == []

    def test_a_negative_allowance_does_not_also_report_high_consumption(self, db):
        """One fact, one alert. Reporting both would let a panel counting alerts double
        the same overrun."""
        from plane.tests.factories import ServiceIssueAllowanceFactory
        from plane.utils.service_pool_alerts import ALLOWANCE_HIGH_CONSUMPTION, evaluate_allowance

        allowance = ServiceIssueAllowanceFactory(
            credited_hours=Decimal("10.0000"), consumed_hours=Decimal("25.0000")
        )

        assert ALLOWANCE_HIGH_CONSUMPTION not in codes(evaluate_allowance(allowance))

    def test_an_empty_allowance_raises_nothing_rather_than_dividing_by_zero(self, db):
        """Zero of zero has no percentage. An allowance created and not yet credited is a
        real state -- the API creates and credits in one transaction, but a conversion from
        a contract could land here."""
        from plane.tests.factories import ServiceIssueAllowanceFactory
        from plane.utils.service_pool_alerts import evaluate_allowance

        allowance = ServiceIssueAllowanceFactory()

        assert evaluate_allowance(allowance) == []

    def test_the_workspace_panel_lists_only_the_overrun_allowances(self, db, actor):
        """And it names the work item, because "an allowance is overrun" is not actionable
        without knowing which project."""
        from plane.tests.factories import IssueFactory, ServiceIssueAllowanceFactory
        from plane.utils.service_pool_alerts import workspace_allowance_alerts

        overrun = ServiceIssueAllowanceFactory(
            credited_hours=Decimal("10.0000"), consumed_hours=Decimal("25.0000")
        )
        healthy = ServiceIssueAllowanceFactory(
            issue=IssueFactory(project=overrun.project),
            credited_hours=Decimal("40.0000"),
            consumed_hours=Decimal("1.0000"),
        )

        entries = workspace_allowance_alerts(overrun.workspace_id)

        assert [entry["allowance_id"] for entry in entries] == [str(overrun.pk)]
        assert entries[0]["issue_name"] == overrun.issue.name
        assert str(healthy.pk) not in [entry["allowance_id"] for entry in entries]

    def test_a_closed_allowance_is_not_alerted_on(self, db, actor):
        """It has been settled: the deficit was billed or the surplus written off, so there
        is nothing left for anyone to act on."""
        from plane.tests.factories import ServiceIssueAllowanceFactory
        from plane.utils.service_allowance import close_allowance
        from plane.utils.service_pool_alerts import workspace_allowance_alerts

        allowance = ServiceIssueAllowanceFactory(
            credited_hours=Decimal("10.0000"), consumed_hours=Decimal("25.0000")
        )

        assert len(workspace_allowance_alerts(allowance.workspace_id)) == 1, "positive control"

        close_allowance(allowance, actor=actor)

        assert workspace_allowance_alerts(allowance.workspace_id) == []
