# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""The demonstration seed, checked against the domain rather than against itself.

**What is worth testing about a seed is that its numbers were computed.** A seed can only
fail in two interesting ways: it writes figures by hand that the domain would never
produce, or it runs twice and doubles them. Both are invisible to a test that merely
asserts the rows exist, so nothing here counts rows without also checking the arithmetic
that should have produced them.

So each assertion below names the domain rule it is standing in for -- R2's blocks, the
1.5 multiplier of R11/criterion 5, R5's two non-billable routes, R6's hierarchy, the
carry-out and carry-in of a close, the overage of a month past its quota -- and the
ledger is checked alongside every balance, because a balance without its journal row is
the one state the whole series was built to make impossible.

``DEBUG`` is forced on, because the command refuses to run without it. That refusal has a
test of its own, and it is the first one: it is the only thing standing between this
command and a production ledger.
"""

# Python imports
from datetime import date
from decimal import Decimal

# Third party imports
import pytest

# Django imports
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.models import Sum
from django.utils import timezone

# Module imports
from plane.db.models import (
    Project,
    ProjectMember,
    ServiceBillingType,
    ServiceClient,
    ServiceContract,
    ServiceContractPeriod,
    ServiceHourLedgerEntry,
    ServiceIssueAllowance,
    ServiceIssueRequester,
    ServiceLedgerEntryType,
    ServiceLog,
    ServiceMemberPermission,
    ServiceOverageSettlement,
    ServicePeriodStatus,
    User,
    Workspace,
    WorkspaceMember,
)
from plane.utils.service_permission import ROLE_GUEST, ROLE_MEMBER, resolve_capabilities
from plane.utils.service_portal import client_may_reach_issue, client_project_ids

pytestmark = pytest.mark.unit

SLUG = "worklog-demo-test"


@pytest.fixture(autouse=True)
def no_celery_dispatch(monkeypatch):
    """Soft delete and work item creation fire ``.delay``, and there is no broker here.

    Patched at ``Task.apply_async`` rather than per task, matching ``test_service_pool.py``,
    so a ``.delay`` added anywhere in the write path does not silently start needing a
    broker.
    """
    monkeypatch.setattr("celery.app.task.Task.apply_async", lambda *args, **kwargs: None)


@pytest.fixture
def seeded(db, settings):
    """The scenario, seeded once, with a fixed password so assertions can log in."""
    settings.DEBUG = True
    call_command("seed_service_demo", "--workspace", SLUG, "--password", "demo-password-1234")

    return Workspace.objects.get(slug=SLUG)


def _user(handle):
    return User.objects.get(email=f"{handle}@planedev.satziack.com")


def _contract(code):
    return ServiceContract.objects.get(code=code)


def _period(code, year, month):
    return ServiceContractPeriod.objects.get(
        contract__code=code, competence_year=year, competence_month=month
    )


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------


def test_the_command_refuses_to_run_with_debug_off(db, settings):
    """The only thing between this seed and a production ledger.

    It writes Clientes, contracts and ledger movements that are indistinguishable from
    real ones, so "somebody ran it against prod" has to be impossible by construction and
    not merely unlikely. Asserting the workspace was *not* created matters as much as the
    raise: a command that failed halfway would have left the contamination behind.
    """
    settings.DEBUG = False

    with pytest.raises(CommandError, match="refuses to run with DEBUG off"):
        call_command("seed_service_demo", "--workspace", SLUG)

    assert not Workspace.objects.filter(slug=SLUG).exists()


# ---------------------------------------------------------------------------
# The cast: Clientes, projects, roles
# ---------------------------------------------------------------------------


def test_three_clientes_each_with_a_project(seeded):
    """Marubeni, Terlogs and Vale, and Vale belongs to no portal user: the leak canary."""
    assert set(
        ServiceClient.objects.filter(workspace=seeded).values_list("name", flat=True)
    ) == {"Marubeni", "Terlogs", "Vale"}

    for name in ("Marubeni", "Terlogs", "Vale"):
        assert Project.objects.filter(workspace=seeded, service_client__name=name).exists()

    # D18: the shareholding link exists and carries no billing behaviour. Both halves are
    # asserted, because the link being *present* is what makes the second half meaningful.
    terlogs = ServiceClient.objects.get(workspace=seeded, name="Terlogs")
    assert terlogs.parent.name == "Marubeni"
    assert _contract("SUP-TLG-001").service_client_id == terlogs.id


def test_marcel_reaches_two_clientes_and_adriano_one(seeded):
    """Criteria 1 and 3, at the level the whole portal derives from.

    The Cliente of a portal user is not stored anywhere -- it is read back from project
    membership (master context 2b). So the scenario is only right if these sets are, and
    ``client_project_ids`` is the function every portal screen asks.
    """
    marcel_projects = set(client_project_ids(_user("marcel"), slug=SLUG))
    adriano_projects = set(client_project_ids(_user("adriano"), slug=SLUG))

    marubeni = Project.objects.get(workspace=seeded, identifier="MRB")
    terlogs = Project.objects.get(workspace=seeded, identifier="TLG")
    vale = Project.objects.get(workspace=seeded, identifier="VALE")

    assert {str(marubeni.id), str(terlogs.id)} == {str(pid) for pid in marcel_projects}

    # Criterion 3, from the data side: Marubeni is not merely hidden from Adriano, it is
    # absent from the set the API scopes him to -- so typing the URL has nothing to hit.
    assert {str(terlogs.id)} == {str(pid) for pid in adriano_projects}
    assert str(marubeni.id) not in {str(pid) for pid in adriano_projects}

    # And nobody's client reaches Vale.
    assert str(vale.id) not in {str(pid) for pid in marcel_projects | adriano_projects}


def test_the_client_projects_are_configured_for_clients(seeded):
    """``guest_view_all_features`` on, time tracking on -- except where the demo needs it off.

    Without the flag a GUEST sees only work items they created themselves, so Marcel would
    miss every ticket a technician opened. The one project with it *off* is deliberate and
    is the subject of the requester test below.
    """
    for identifier in ("MRB", "TLG", "VALE"):
        project = Project.objects.get(workspace=seeded, identifier=identifier)
        assert project.guest_view_all_features is True
        assert project.is_time_tracking_enabled is True

    assert Project.objects.get(workspace=seeded, identifier="MRBP").guest_view_all_features is False

    # Criterion 8's raw material: projects with no Cliente and both flags off, waiting to
    # be assigned in bulk.
    unassigned = Project.objects.filter(workspace=seeded, service_client__isnull=True)
    assert unassigned.count() == 2
    assert not unassigned.filter(guest_view_all_features=True).exists()
    assert not unassigned.filter(is_time_tracking_enabled=True).exists()


def test_the_roles_are_the_three_core_integers(seeded):
    """Two MEMBER technicians and three GUEST client users. No fourth role invented."""
    roles = dict(
        WorkspaceMember.objects.filter(workspace=seeded).values_list("member__email", "role")
    )

    assert roles[f"tecnico@planedev.satziack.com"] == ROLE_MEMBER
    assert roles[f"tecnico2@planedev.satziack.com"] == ROLE_MEMBER

    for handle in ("marcel", "adriano", "rafael"):
        assert roles[f"{handle}@planedev.satziack.com"] == ROLE_GUEST


def test_the_instance_is_marked_set_up_with_an_admin(db, settings):
    """The front door, which a correct database is useless without.

    The web app gates on ``Instance.is_setup_done`` and renders "Set up your instance"
    instead of a login form while it is false. A seed that produced the whole scenario and
    left this flag alone would hand over credentials that cannot be typed anywhere -- which
    is exactly what happened the first time this was deployed, and is why the assertion
    exists.

    An ``Instance`` row is created here on purpose: the suite never boots the API, so
    ``register_instance`` has not run and the seed's own skip path would otherwise be the
    only thing tested.
    """
    from plane.license.models import Instance, InstanceAdmin

    Instance.objects.create(
        instance_name="Test instance",
        instance_id="test-instance",
        current_version="1.0.0",
        latest_version="1.0.0",
        last_checked_at=timezone.now(),
    )

    settings.DEBUG = True
    call_command("seed_service_demo", "--workspace", SLUG, "--password", "demo-password-1234")

    instance = Instance.objects.first()
    assert instance.is_setup_done is True
    assert InstanceAdmin.objects.filter(instance=instance, user=_user("admin"), role=20).exists()


def test_the_seed_survives_a_database_with_no_instance_row(db, settings):
    """The skip path. Registration happens on API boot, which a seeded database may predate."""
    settings.DEBUG = True
    call_command("seed_service_demo", "--workspace", SLUG, "--password", "demo-password-1234")

    assert Workspace.objects.filter(slug=SLUG).exists()


def test_the_scenario_users_can_actually_log_in(seeded):
    """Onboarding out of the way, password usable, e-mail verified.

    On an instance with no SMTP -- which is what a throwaway demo box is -- a user left
    with ``is_password_autoset`` lands on a password reset that can never arrive, and one
    left un-onboarded lands in the wizard. Either makes the handed-over credentials
    useless, so this is part of the seed's job and not of the deployment's.
    """
    for handle in ("admin", "tecnico", "tecnico2", "marcel", "adriano", "rafael"):
        user = _user(handle)
        assert user.check_password("demo-password-1234")
        assert user.is_password_autoset is False
        assert user.is_email_verified is True
        assert user.profile.is_onboarded is True
        assert user.profile.last_workspace_id == seeded.id


# ---------------------------------------------------------------------------
# Criterion 7's target, and the grant that is already in place
# ---------------------------------------------------------------------------


def test_can_delegate_is_granted_through_the_domain_and_resolves(seeded):
    """The grant exists *and* resolves -- which are two different facts.

    ``resolve_capabilities`` gates on the live role, so a stored row is not a capability.
    Checking only the row would pass for a member who had since been demoted, which is
    precisely the trap criterion 7 asks the operator to walk into on purpose.
    """
    technician = _user("tecnico")

    assert ServiceMemberPermission.objects.get(
        workspace=seeded, member=technician
    ).can_delegate is True

    assert resolve_capabilities(technician, workspace_id=seeded.id).can_delegate is True

    # The spare technician is the one criterion 7 is performed on, so he must start clean:
    # a MEMBER with no grant to revoke and nothing on his row yet.
    spare = _user("tecnico2")
    assert not ServiceMemberPermission.objects.filter(workspace=seeded, member=spare).exists()
    assert resolve_capabilities(spare, workspace_id=seeded.id).can_delegate is False


# ---------------------------------------------------------------------------
# The hours: computed, not written
# ---------------------------------------------------------------------------


def test_one_hour_at_multiplier_one_and_a_half(seeded):
    """Criterion 5, at the source of both numbers the two panels display.

    The technician's 1h and the client's 1,5h are not two roundings of one figure: they
    are ``logged_hours`` and ``equivalent_hours``, computed once by
    ``compute_hour_quantities`` from the multiplier snapshotted on the row (R4). If the
    seed had written them by hand this test would be checking the seed against the seed.
    """
    log = ServiceLog.objects.get(
        issue__name="Impressora fiscal não emite NF", hour_type__multiplier=Decimal("1.50")
    )

    assert log.raw_duration_minutes == 60
    assert log.logged_hours == Decimal("1.0000")
    assert log.equivalent_hours == Decimal("1.5000")
    assert log.debited_hours == Decimal("1.5000")
    assert log.applied_multiplier == Decimal("1.50")

    # The debit is what actually left the pool, and it is the equivalent hours that left
    # it -- 1.5, not 1.0. A pool debited by logged hours would be the client being
    # undercharged for a night call, silently.
    debit = ServiceHourLedgerEntry.objects.get(
        service_log=log, entry_type=ServiceLedgerEntryType.DEBIT
    )
    assert debit.hours == Decimal("-1.5000")
    assert debit.period_id == log.debited_period_id


def test_the_two_non_billable_routes_stay_two_rows(seeded):
    """Criterion 4, and R5.

    Garantia and Cortesia share the ``non_billable`` route and are *separate billing
    types*, which is the only reason a report can discriminate them without an extra
    column. This asserts both halves: same route, different row, and neither debits.

    The positive control is in the same fixture -- the ad hoc log below has a value in
    reais -- because "amount is zero" is also what a broken pricing lookup produces.
    """
    warranty = ServiceLog.objects.get(billing_type__name="Garantia")
    courtesy = ServiceLog.objects.get(billing_type__name="Cortesia")

    assert warranty.billing_type_id != courtesy.billing_type_id
    assert (
        warranty.applied_billing_route
        == courtesy.applied_billing_route
        == ServiceBillingType.BillingRoute.NON_BILLABLE
    )

    for log in (warranty, courtesy):
        # R5: hours are recorded and worked, and nothing is charged for them.
        assert log.logged_hours > Decimal("0")
        assert log.debited_hours == Decimal("0.0000")
        assert log.amount == Decimal("0.00")
        assert log.debited_period_id is None
        assert not ServiceHourLedgerEntry.objects.filter(
            service_log=log, entry_type=ServiceLedgerEntryType.DEBIT
        ).exists()

    # The positive control: an ad hoc log priced from the Cliente's sheet. 2h at
    # multiplier 1.0 on Marubeni's R$ 180,00 base. Computed by `resolve_log_price`, which
    # is why it is safe to assert the exact figure.
    #
    # Note ``debited_hours`` is 2.0 here and **not** zero, which looks wrong at first and
    # is not: the column follows the *route*, and only NON_BILLABLE zeroes it
    # (`service_log_debited_hours_follows_billing_route`). What tells "billed in reais"
    # apart from "debited a pool" is ``debited_period``, so that is what is asserted.
    ad_hoc = ServiceLog.objects.get(billing_type__name="Avulso")
    assert ad_hoc.applied_hour_rate == Decimal("180.00")
    assert ad_hoc.amount == Decimal("360.00")
    assert ad_hoc.debited_hours == Decimal("2.0000")
    assert ad_hoc.debited_period_id is None
    assert ad_hoc.debited_allowance_id is None


def test_marubeni_accumulates_and_carries_a_previous_competence(seeded):
    """Two closed months under budget, and August opening with both parcels.

    The arithmetic is the contract's, not the seed's: 10h granted less 4h consumed leaves
    6h carried into July; July grants 10 + 6 and 3h are consumed, so 13h reach August.
    Every step is asserted against its ledger row, because "the balance moved" and "the
    balance moved *and was journalled*" are the two states criterion 20 distinguishes.
    """
    june = _period("SUP-MRB-001", 2026, 6)
    july = _period("SUP-MRB-001", 2026, 7)
    august = _period("SUP-MRB-001", 2026, 8)

    assert june.status == ServicePeriodStatus.CLOSED
    assert june.contracted_hours == Decimal("10.0000")
    assert june.consumed_hours == Decimal("4.0000")  # 2.5h + 1.5h, at multiplier 1.0

    assert july.status == ServicePeriodStatus.CLOSED
    assert july.carried_hours == Decimal("6.0000")
    assert july.granted_hours == Decimal("16.0000")
    assert july.consumed_hours == Decimal("3.0000")

    # The parcel of a previous competence, still identifiable as such: August's carry-in
    # names July as its origin, and the FIFO parcels behind it reach back to June.
    assert august.carried_hours == Decimal("13.0000")
    assert august.status == ServicePeriodStatus.OPEN

    carry_in = ServiceHourLedgerEntry.objects.filter(
        period=august, entry_type=ServiceLedgerEntryType.CARRY_IN
    )
    assert carry_in.aggregate(total=Sum("hours"))["total"] == Decimal("13.0000")
    assert set(carry_in.values_list("origin_period__competence_month", flat=True)) == {6, 7}

    # A closed period's journal sums to zero: everything granted was either consumed or
    # carried out, and nothing evaporated.
    for period in (june, july):
        assert ServiceHourLedgerEntry.objects.filter(period=period).aggregate(
            total=Sum("hours")
        )["total"] == Decimal("0.0000")


def test_terlogs_overruns_its_quota_and_the_overage_is_billed(seeded):
    """A month consumed past the contract, settled the way that keeps the next one whole.

    34h against a 30h quota. Closing as ``BILLED`` records 4h of overage at the contract's
    R$ 250,00 rate, and -- decision B3 -- moves the deficit *out* of the pool, so July
    opens with its full 30h rather than mortgaged. Asserting ``carried_hours == 0`` on
    July is what tells billing apart from relabelling.
    """
    june = _period("SUP-TLG-001", 2026, 6)
    july = _period("SUP-TLG-001", 2026, 7)
    august = _period("SUP-TLG-001", 2026, 8)

    assert june.contracted_hours == Decimal("30.0000")
    assert june.consumed_hours == Decimal("34.0000")
    assert june.status == ServicePeriodStatus.CLOSED
    assert june.overage_hours == Decimal("4.0000")
    assert june.overage_settlement == ServiceOverageSettlement.BILLED

    billed = ServiceHourLedgerEntry.objects.get(
        period=june, entry_type=ServiceLedgerEntryType.OVERAGE_BILLED
    )
    assert billed.hours == Decimal("4.0000")

    assert july.carried_hours == Decimal("0.0000")
    assert july.granted_hours == Decimal("30.0000")

    # July closes under budget, so the accumulation resumes: 30 - 12 = 18h into August.
    assert july.consumed_hours == Decimal("12.0000")
    assert august.carried_hours == Decimal("18.0000")

    # And the Sunday call in August is at 2.0, so the demo carries two multipliers rather
    # than one: 2h logged, 4h equivalent.
    sunday = ServiceLog.objects.get(
        project__identifier="TLG", worked_on=date(2026, 8, 2)
    )
    assert sunday.applied_multiplier == Decimal("2.00")
    assert sunday.logged_hours == Decimal("2.0000")
    assert sunday.equivalent_hours == Decimal("4.0000")


def test_the_allowance_has_two_credits_and_takes_the_debit_before_the_pool(seeded):
    """The bolsa, its credit history, and R6's hierarchy in one place.

    Two ``CREDIT`` rows rather than one of 50h, because the history *is* the ledger: each
    row carries its own author and date, and a single column pair would have lost the
    first credit the moment the second arrived.

    The debit is the sharper half. R6 puts an allowance ahead of the contract, so this 4h
    log must leave the allowance and must **not** touch Marubeni's period -- and the
    ``debited_allowance``/``debited_period`` pair is mutually exclusive in the schema, so
    checking both is checking that the right one won.
    """
    allowance = ServiceIssueAllowance.objects.get(
        issue__name="Migração do ERP para a nuvem — bolsa de horas"
    )

    credits = ServiceHourLedgerEntry.objects.filter(
        allowance=allowance, entry_type=ServiceLedgerEntryType.CREDIT
    ).order_by("created_at")

    assert credits.count() == 2
    assert [entry.hours for entry in credits] == [Decimal("40.0000"), Decimal("10.0000")]
    assert allowance.credited_hours == Decimal("50.0000")
    assert allowance.reference == "PROP-2026-041"

    # Every credit has an author. "Autoria e data do crédito", recorded per credit.
    assert all(entry.actor_id is not None for entry in credits)

    log = ServiceLog.objects.get(issue=allowance.issue)
    assert log.debited_allowance_id == allowance.id
    assert log.debited_period_id is None
    assert allowance.consumed_hours == Decimal("4.0000")
    assert allowance.balance_hours == Decimal("46.0000")

    # The allowance's work item lives in a Marubeni project, so the proof that the pool was
    # spared is that the period's consumption does not include these 4 hours.
    assert _period("SUP-MRB-001", 2026, 8).consumed_hours == Decimal("3.5000")


# ---------------------------------------------------------------------------
# The requester
# ---------------------------------------------------------------------------


def test_the_requester_is_what_lets_the_second_guest_see_anything(seeded):
    """Criterion 6's data, and the clause that makes the field do something.

    Rafael is a GUEST of the one project whose ``guest_view_all_features`` is off. He did
    not create the work item, so the only thing that can let him reach it is the requester
    row -- and the negative control is in the same test: a work item in the same project
    that he cannot reach.

    Without this pairing, "the requester field exists" would pass for a feature that
    records an attribution and grants nothing, which is the failure mode
    ``client_may_reach_issue`` was written to prevent.
    """
    rafael = _user("rafael")
    marcel = _user("marcel")

    attributed = ServiceIssueRequester.objects.get(requester=rafael).issue
    assert attributed.project.guest_view_all_features is False
    assert attributed.created_by_id != rafael.id
    assert client_may_reach_issue(rafael, attributed) is True

    # Negative control, same project, same role, no requester row.
    from plane.db.models import Issue

    other = Issue(
        name="Chamado que o Rafael não deve alcançar",
        project=attributed.project,
        workspace=attributed.project.workspace,
    )
    other.save(created_by_id=_user("tecnico").id)
    assert client_may_reach_issue(rafael, other) is False

    # And Marcel's own attribution, on a project that already shows him everything: the
    # row is there for the attribution and the notification, not for the visibility.
    marcel_issue = ServiceIssueRequester.objects.get(requester=marcel).issue
    assert marcel_issue.project.guest_view_all_features is True


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_running_it_twice_changes_no_number(seeded, settings):
    """The property that makes a seed safe to re-run against a live demo.

    Re-running must not double a period's consumption, add a third credit or open a second
    contract. Consumption is the one that would be silently plausible -- 8h instead of 4h
    looks like a busy month, not like a bug -- so the balances are compared before and
    after rather than merely recounted.
    """
    settings.DEBUG = True

    before = {
        "logs": ServiceLog.objects.count(),
        "clients": ServiceClient.objects.count(),
        "contracts": ServiceContract.objects.count(),
        "projects": Project.objects.filter(workspace=seeded).count(),
        "members": ProjectMember.objects.filter(workspace=seeded).count(),
        "ledger": ServiceHourLedgerEntry.objects.count(),
        "june_consumed": _period("SUP-MRB-001", 2026, 6).consumed_hours,
        "august_carried": _period("SUP-MRB-001", 2026, 8).carried_hours,
        "credited": ServiceIssueAllowance.objects.get(
            issue__name="Migração do ERP para a nuvem — bolsa de horas"
        ).credited_hours,
        "requesters": ServiceIssueRequester.objects.count(),
    }

    call_command("seed_service_demo", "--workspace", SLUG, "--password", "demo-password-1234")

    after = {
        "logs": ServiceLog.objects.count(),
        "clients": ServiceClient.objects.count(),
        "contracts": ServiceContract.objects.count(),
        "projects": Project.objects.filter(workspace=seeded).count(),
        "members": ProjectMember.objects.filter(workspace=seeded).count(),
        "ledger": ServiceHourLedgerEntry.objects.count(),
        "june_consumed": _period("SUP-MRB-001", 2026, 6).consumed_hours,
        "august_carried": _period("SUP-MRB-001", 2026, 8).carried_hours,
        "credited": ServiceIssueAllowance.objects.get(
            issue__name="Migração do ERP para a nuvem — bolsa de horas"
        ).credited_hours,
        "requesters": ServiceIssueRequester.objects.count(),
    }

    assert before == after


def test_a_re_run_restores_a_role_that_criterion_seven_demoted(seeded, settings):
    """Criterion 7 ends with a technician demoted to GUEST. The seed has to undo that.

    Otherwise the second person to walk the script starts from a different scenario than
    the first, and the step that is supposed to demonstrate the demotion finds a GUEST
    already sitting there.
    """
    settings.DEBUG = True
    spare = _user("tecnico2")

    membership = WorkspaceMember.objects.get(workspace=seeded, member=spare)
    membership.role = ROLE_GUEST
    membership.save(update_fields=["role"])

    call_command("seed_service_demo", "--workspace", SLUG, "--password", "demo-password-1234")

    membership.refresh_from_db()
    assert membership.role == ROLE_MEMBER
