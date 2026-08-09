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
    Profile,
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


def test_four_clientes_each_with_a_project(seeded):
    """Marubeni, Terlogs, Vale and Bonfim.

    Vale belongs to no portal user: the leak canary. Bonfim is the fourth, added by Phase 10
    item 10, and it is the only one **without a contract** -- see
    ``test_bonfim_has_no_contract_and_that_is_the_point``.
    """
    assert set(
        ServiceClient.objects.filter(workspace=seeded).values_list("name", flat=True)
    ) == {"Marubeni", "Terlogs", "Vale", "Bonfim Alimentos"}

    for name in ("Marubeni", "Terlogs", "Vale", "Bonfim Alimentos"):
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


def test_every_seeded_profile_is_in_pt_br(seeded):
    """Phase 10, item 9. The scenario is Brazilian and so is the interface it hands over.

    Before this, the demo's Portuguese was set by hand in each profile after the seed ran --
    which meant a fresh box served English until somebody remembered, and Phase 10's own
    handoff had to say so.

    Asserted for **every** account and not just one, because the seed writes them through the
    same helper and a per-account regression would be invisible in a spot check.
    """
    for handle in ("admin", "tecnico", "tecnico2", "marcel", "adriano", "rafael"):
        assert _user(handle).profile.language == "pt-BR", handle


def test_the_seed_sets_the_language_rather_than_inheriting_the_model_default(seeded, settings):
    """**The failure the model default alone does not cover, and the reason item 9 has three
    parts instead of one.**

    ``_user`` uses ``get_or_create``, so on a re-seed the profile already exists and the
    column default never runs. An account first written before migration 0134 would keep
    ``en`` through every future seed.

    Simulated exactly: force a profile back to ``en``, re-run, and require the seed to have
    corrected it. A seed that relied on the default passes the test above and fails this one.
    """
    marcel = _user("marcel")
    Profile.objects.filter(user=marcel).update(language="en")

    # The negative control: it really is `en` before the re-seed, so the assertion after it
    # is about the seed and not about a write that never happened.
    assert Profile.objects.get(user=marcel).language == "en"

    settings.DEBUG = True
    call_command("seed_service_demo", "--workspace", SLUG, "--password", "demo-password-1234")

    assert Profile.objects.get(user=marcel).language == "pt-BR"


def test_a_deliberate_non_english_choice_is_still_overwritten_by_the_seed(seeded, settings):
    """Characterisation, not endorsement -- it pins a limitation so nobody reads the seed as
    gentler than it is.

    The **migration** only moves profiles still on ``en``, leaving a deliberate choice alone.
    The **seed** is blunter: it rewrites the language of every scenario account on every run,
    the same way it rewrites their passwords. That is correct for a throwaway demo box, whose
    whole purpose is to be reproducible, and it would be wrong on a real instance -- which is
    why the seed refuses to run without ``DEBUG``.
    """
    marcel = _user("marcel")
    Profile.objects.filter(user=marcel).update(language="ja")

    settings.DEBUG = True
    call_command("seed_service_demo", "--workspace", SLUG, "--password", "demo-password-1234")

    assert Profile.objects.get(user=marcel).language == "pt-BR"


def test_a_brand_new_profile_gets_pt_br_without_the_seed(db):
    """The second of item 9's three parts, and the one the seed cannot deliver: everybody the
    seed does not create.

    An invited GUEST, a technician added next month, an account made through the instance
    admin -- none of them goes through ``seed_service_demo``, and all of them go through the
    column default. Asserted against a plain ``Profile.objects.create``, which is what
    ``plane/authentication/adapter/base.py`` does on first login.
    """
    user = User.objects.create(email="newcomer@planedev.satziack.com", username="newcomer")
    profile = Profile.objects.create(user=user)

    assert profile.language == "pt-BR"


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

    Scoped to Marubeni's project since Phase 10 item 10: Bonfim now has a Garantia log and
    three Avulso ones, so the unscoped ``get()`` this test used to do raises
    ``MultipleObjectsReturned``. Scoping keeps the assertions about the rows they were always
    about; Bonfim's own equivalents are asserted in the ad hoc section below.
    """
    warranty = ServiceLog.objects.get(billing_type__name="Garantia", project__identifier="MRB")
    courtesy = ServiceLog.objects.get(billing_type__name="Cortesia", project__identifier="MRB")

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
    ad_hoc = ServiceLog.objects.get(billing_type__name="Avulso", project__identifier="MRB")
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



# ---------------------------------------------------------------------------
# Bonfim: the Cliente without a contract. Phase 10, item 10
# ---------------------------------------------------------------------------


BONFIM = "Bonfim Alimentos"


def _bonfim_logs():
    return ServiceLog.objects.filter(project__identifier="BNF")


def test_bonfim_has_no_contract_and_that_is_the_point(seeded):
    """**The assertion item 10 exists for**, and the one a future change is most likely to
    break by being helpful.

    Every other Cliente holds a contract, which is why the ad hoc half of the product had no
    example: ``shape == "standalone"``, ``revenue_series``, and pricing from a sheet rather
    than debiting from a pool were all reachable only from code.

    Stated as an absence **with the positive control beside it** -- the other three Clientes
    do have contracts -- because "no contract found" is also what a seed that failed to
    create any Cliente produces.
    """
    bonfim = ServiceClient.objects.get(workspace=seeded, name=BONFIM)

    assert not ServiceContract.objects.filter(service_client=bonfim).exists()

    # The positive control: the mechanism works, and Bonfim's emptiness is a choice.
    for name in ("Marubeni", "Terlogs", "Vale"):
        other = ServiceClient.objects.get(workspace=seeded, name=name)
        assert ServiceContract.objects.filter(service_client=other).exists(), name


def test_bonfim_has_no_contract_period_either(seeded):
    """A period belongs to a contract, so a Cliente without one must own no period.

    Separate from the test above because they fail for different reasons: a contract could be
    removed and leave orphan periods, and a period is what would make the portal render a
    statement table for a Cliente that has no statement.
    """
    assert not ServiceContractPeriod.objects.filter(
        contract__service_client__name=BONFIM
    ).exists()


def test_bonfim_is_configured_like_any_client_project(seeded):
    """The difference from the other three is the missing contract, **not** a missing setting.

    A project with `guest_view_all_features` off or time tracking off would produce the same
    empty portal for an entirely different reason, and debugging that on a demo box is the
    hour item 10 is meant to save.
    """
    project = Project.objects.get(workspace=seeded, identifier="BNF")

    assert project.service_client.name == BONFIM
    assert project.guest_view_all_features is True
    assert project.is_time_tracking_enabled is True


def test_bonfim_is_ad_hoc_by_default(seeded):
    """``default_billing_type`` says what a work log form pre-selects here.

    It takes no part in settlement -- ``resolve_default_billing_type`` is only consulted by
    the form -- so this is about the demo being self-describing rather than about a number.
    """
    bonfim = ServiceClient.objects.get(workspace=seeded, name=BONFIM)

    assert bonfim.default_billing_type is not None
    assert bonfim.default_billing_type.name == "Avulso"
    assert (
        bonfim.default_billing_type.billing_route == ServiceBillingType.BillingRoute.BILL_AMOUNT
    )


def test_the_multiplier_is_a_price_here_and_the_three_rates_are_exact(seeded):
    """**The demonstration item 10 was asked for**, and the master context's own argument for
    modelling hours in three quantities: "1h trabalhada fora do expediente custa R$ 300,00 em
    vez de R$ 200,00 para o cliente avulso".

    One configured base rate (R$ 240,00) and one configured multiplier per hour type produce
    three prices for the same hour:

    ======================  =====  ========  ==========  =========  ==========
    hour type               mult.  logged    equivalent  rate       amount
    ======================  =====  ========  ==========  =========  ==========
    Horário comercial       1.00   2.0000    2.0000      R$ 240,00  R$ 480,00
    Fora do expediente      1.50   1.0000    1.5000      R$ 240,00  R$ 360,00
    Domingos e feriados     2.00   2.0000    4.0000      R$ 240,00  R$ 960,00
    ======================  =====  ========  ==========  =========  ==========

    Note the after-hours row: **1h of work billed as R$ 360,00**, which is 1,5 x 240. That is
    the sentence the master context wrote -- "1h fora do expediente custa R$ 300,00 em vez de
    R$ 200,00 para o cliente avulso" -- in data, for the first time in the series.

    **And note that ``applied_hour_rate`` is R$ 240,00 on all three rows.** I expected it to
    be the base times the multiplier and it is not; the multiplier has *already entered
    through the hours*, and ``ServiceRateBasis.BASE_MULTIPLIER`` says which formula produced
    the amount. Asserting the rate on every row rather than only the amount is what pins
    that: multiplying a rate that already embedded the multiplier by equivalent hours would
    double-charge, and every amount here would still look like a plausible price.
    """
    by_hour_type = {
        log.hour_type.name: log
        for log in _bonfim_logs().exclude(billing_type__name="Garantia").select_related("hour_type")
    }

    business = by_hour_type["Horário comercial"]
    assert business.applied_multiplier == Decimal("1.00")
    assert business.logged_hours == Decimal("2.0000")
    assert business.equivalent_hours == Decimal("2.0000")
    assert business.amount == Decimal("480.00")

    after_hours = by_hour_type["Fora do expediente"]
    assert after_hours.applied_multiplier == Decimal("1.50")
    assert after_hours.logged_hours == Decimal("1.0000")
    assert after_hours.equivalent_hours == Decimal("1.5000")
    assert after_hours.amount == Decimal("360.00")

    sunday = by_hour_type["Domingos e feriados"]
    assert sunday.applied_multiplier == Decimal("2.00")
    assert sunday.logged_hours == Decimal("2.0000")
    assert sunday.equivalent_hours == Decimal("4.0000")
    assert sunday.amount == Decimal("960.00")

    # The snapshotted sheet rate (R4), identical on every row, and the formula that used it.
    # This is the assertion that would catch a rate pre-multiplied by the hour type.
    for label, log in (("business", business), ("after_hours", after_hours), ("sunday", sunday)):
        assert log.applied_hour_rate == Decimal("240.00"), label
        assert log.applied_rate_basis == ServiceLog.RateBasis.BASE_MULTIPLIER, label
        assert log.amount == log.equivalent_hours * log.applied_hour_rate, label


def test_no_bonfim_log_debits_a_pool(seeded):
    """R6's third branch: no allowance, no contract, therefore a value in reais.

    ``debited_period`` and ``debited_allowance`` are what distinguish "billed" from
    "debited" -- not ``debited_hours``, which follows the *route* and is non-zero on a billed
    row (see the note in ``test_the_two_non_billable_routes_stay_two_rows``).
    """
    logs = list(_bonfim_logs())

    # The positive control: there are rows to make this claim about.
    assert len(logs) == 4, [log.description for log in logs]

    for log in logs:
        assert log.debited_period_id is None, log.description
        assert log.debited_allowance_id is None, log.description
        assert not ServiceHourLedgerEntry.objects.filter(
            service_log=log, entry_type=ServiceLedgerEntryType.DEBIT
        ).exists(), log.description


def test_the_billable_bonfim_logs_are_priced_and_the_warranty_one_is_not(seeded):
    """R5 on the ad hoc path, which is a different demonstration from the contracted one.

    On a Cliente with a contract, a reader looking at a zero-value warranty row can wonder
    whether the pool absorbed it. Here there is no pool, so ``R$ 0,00`` can only mean the
    route refused to charge -- and the three priced rows next to it are the control that the
    pricing lookup is working at all.
    """
    warranty = _bonfim_logs().get(billing_type__name="Garantia")

    assert warranty.applied_billing_route == ServiceBillingType.BillingRoute.NON_BILLABLE
    assert warranty.logged_hours == Decimal("1.5000")
    assert warranty.equivalent_hours == Decimal("1.5000")
    assert warranty.debited_hours == Decimal("0.0000")
    assert warranty.amount == Decimal("0.00")

    billed = _bonfim_logs().exclude(billing_type__name="Garantia")
    assert billed.count() == 3
    for log in billed:
        assert log.settled_billing_route == ServiceBillingType.BillingRoute.BILL_AMOUNT
        assert log.amount > Decimal("0.00"), log.description


def test_bonfim_revenue_is_the_sum_of_the_persisted_amounts(seeded):
    """R$ 1.800,00 across two competencies, and it is a **sum of persisted values**.

    480 + 360 + 960 = 1800. Section 4b: money is rounded once per work log, at derivation,
    and every total is the sum of what was stored -- never hours times a rate recomputed at
    report time, which is how the lines of an invoice stop adding up to its total.
    """
    total = _bonfim_logs().aggregate(total=Sum("amount"))["total"]

    assert total == Decimal("1800.00")


def test_bonfim_spans_two_competencies(seeded):
    """So the ticket table of item 7 has more than one chip, and its competency filter has
    something to tell apart.

    The Avulso path groups by ``worked_on`` -- the service date, R7 -- because there is no
    period whose competency could disagree with it (D48).
    """
    months = {log.worked_on.strftime("%Y-%m") for log in _bonfim_logs()}

    assert months == {"2026-07", "2026-08"}


def test_patricia_reaches_bonfim_and_nothing_else(seeded):
    """The Avulso portal user, scoped the way every portal user is: by project membership,
    with the Cliente derived and stored nowhere (master context 2b).

    The second assertion is the one that matters -- she must not reach a contracted Cliente,
    or the "no contract" dashboard would not be hers.
    """
    projects = {str(pid) for pid in client_project_ids(_user("patricia"), slug=SLUG)}
    bonfim_project = Project.objects.get(workspace=seeded, identifier="BNF")

    assert projects == {str(bonfim_project.id)}

    for identifier in ("MRB", "TLG", "VALE"):
        other = Project.objects.get(workspace=seeded, identifier=identifier)
        assert str(other.id) not in projects, identifier


def test_vale_is_still_the_cliente_no_portal_user_reaches(seeded):
    """Item 10 added a fourth Cliente and a fifth portal user, and the leak canary has to
    survive that. A new GUEST wired to the wrong project would quietly retire the one test
    that proves isolation is real rather than incidental."""
    vale = Project.objects.get(workspace=seeded, identifier="VALE")

    reachable = set()
    for handle in ("marcel", "adriano", "rafael", "patricia"):
        reachable |= {str(pid) for pid in client_project_ids(_user(handle), slug=SLUG)}

    # The positive control: these users do reach projects, so the absence below is meaningful.
    assert reachable
    assert str(vale.id) not in reachable



def test_bonfim_gives_the_consumption_dashboard_a_standalone_shape_with_revenue(seeded):
    """**Item 10's actual purpose, asserted through the code the dashboard calls.**

    The point was never "a fourth Cliente exists"; it was that the ad hoc dashboard had no
    example. So this exercises the report layer itself -- ``headline_totals`` and
    ``revenue_series`` with an Admin viewer -- rather than re-reading the work log rows the
    tests above already cover.

    Three things a contracted Cliente cannot demonstrate:

    * the dashboard resolves to ``standalone`` because no contract covers the window, which
      is the branch ``ServiceConsumptionReportEndpoint`` picks from the data rather than from
      the caller;
    * ``revenue_series`` is non-empty and carries money, which for a contracted Cliente is
      near-zero because the hours went to a pool instead;
    * the headline total in reais equals the sum of the persisted amounts, which is section
      4b's rule that a total is never recomputed from hours.

    The **negative control is Marubeni**, in the same fixture: it holds a contract, so asking
    the same question about it must produce the contract shape. Without that control this test
    would pass on a report layer that answered "standalone" for everybody.
    """
    from plane.utils.service_reports import ReportViewer, headline_totals, revenue_series
    from plane.utils.service_reports_filters import ServiceLogFilterSet

    admin_viewer = ReportViewer.admin()
    bonfim = ServiceClient.objects.get(workspace=seeded, name=BONFIM)
    marubeni = ServiceClient.objects.get(workspace=seeded, name="Marubeni")

    bonfim_filters = ServiceLogFilterSet(service_client_ids=(bonfim.id,))

    totals = headline_totals(seeded.id, bonfim_filters, admin_viewer)
    assert totals["amount"] == "1800.00"
    assert "R$" in totals["amount_display"]

    # 4 work logs across 3 tickets -- one ticket carries the business-hours and the
    # after-hours entry, which is what puts the two prices on one screen. Both counts are
    # asserted because they are different questions, and `issues` is the number item 7's
    # ticket table must agree with: its `total_count` is this same
    # `Count("issue_id", distinct=True)` over the same descriptor.
    assert totals["entries"] == 4
    assert totals["issues"] == 3

    series = revenue_series(seeded.id, bonfim_filters, admin_viewer)
    assert series, "the ad hoc Cliente produced no revenue series at all"

    # Each point carries `origins` plus `total_amount`; the amount does not live at the top
    # level of a point. Two competencies, and they must sum to the persisted total.
    assert {point["competence"] for point in series} == {"2026-07", "2026-08"}
    billed = sum(Decimal(point["total_amount"]) for point in series)
    assert billed == Decimal("1800.00")

    # **The origin that had no example before item 10.** D36 splits revenue four ways, and
    # `standalone_log` -- billed work for a Cliente holding no contract for that competency --
    # was unreachable in the seeded scenario, because every Cliente had a contract. Marubeni's
    # ad hoc log classifies as `out_of_scope_log` instead, which is a different bucket and a
    # different conversation.
    standalone = sum(
        Decimal(point["origins"]["standalone_log"]["amount"]) for point in series
    )
    assert standalone == Decimal("1800.00")

    out_of_scope = sum(
        Decimal(point["origins"]["out_of_scope_log"]["amount"]) for point in series
    )
    assert out_of_scope == Decimal("0.00"), (
        "Bonfim holds no contract, so none of its revenue can be out-of-scope work"
    )

    # The shape the endpoint would answer, resolved the way it resolves it: from whether a
    # contract exists for the Cliente, not from anything the caller said.
    assert not ServiceContract.objects.filter(service_client=bonfim).exists()

    # The negative control. Same question, contracted Cliente, opposite answer -- so a report
    # layer that called everything "standalone" fails here.
    assert ServiceContract.objects.filter(service_client=marubeni).exists()
