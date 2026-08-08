# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""The reference scenario of the master context, built by the domain layer.

Seeds a browsable demonstration of the whole work log series: the Clientes, their
projects and portal users, the contracts with their competency periods, the work logs
that consume them, the per-work-item allowance, and the permission grants.

**Every number here is computed, none is written.** Consumption, carried balances,
overage, equivalent hours and amounts in reais are produced by calling
``plane.utils.service_pool``, ``service_log``, ``service_billing``,
``service_allowance`` and ``service_pricing`` -- the same functions the API calls. The
seed chooses *inputs* (a duration, an hour type, a service date) and lets the domain
derive the rest. A seed that wrote ``consumed_hours = 4`` by hand would be a fixture
that agrees with itself and with nothing else, and the first regression in the debit
hierarchy would leave the demo looking perfectly healthy.

That is also why there is no ``ServiceLog.objects.create`` in this file, and no
assignment to ``consumed_hours``, ``credited_hours``, ``carried_hours`` or
``overage_hours``. The ledger row *is* the movement.

**Refuses to run without DEBUG.** Unlike ``seed_service_catalogs``, which repairs a
catalogue any production workspace legitimately needs, this command invents commercial
data -- clients that do not exist, contracts nobody signed, and users whose passwords
are printed to stdout. On a production instance that is contamination, not a fixture,
and the ledger it writes is indistinguishable from real movements. The guard is the
cheap half of the protection; the other half is that it is only ever pointed at a
throwaway workspace.

Idempotent, at the granularity of the work item: re-running adds whatever is missing and
never duplicates a work log, because the logs of a work item are only written in the run
that created it. Passwords are the one exception, and they are reset on every run so the
operator always leaves with credentials that work.
"""

# Python imports
from datetime import date
from decimal import Decimal

# Django imports
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils.crypto import get_random_string

# Third party imports
from crum import get_current_user, set_current_user

# Module imports
from plane.db.models import (
    DEFAULT_STATES,
    Issue,
    Profile,
    Project,
    ProjectMember,
    ServiceBillingType,
    ServiceClassificationWindow,
    ServiceClient,
    ServiceClientPrice,
    ServiceContract,
    ServiceHourType,
    ServiceIssueAllowance,
    ServiceIssueRequester,
    ServiceLogEntryMode,
    ServiceLogSource,
    ServiceOverageSettlement,
    ServicePeriodStatus,
    State,
    User,
    Workspace,
    WorkspaceMember,
)
from plane.utils.service_allowance import allowance_credits, credit_allowance
from plane.utils.service_billing import settle_service_log_batch
from plane.utils.service_catalog import create_with_config_activity
from plane.utils.service_catalog_seed import seed_service_catalogs
from plane.utils.service_log import build_batch_rows, build_segments, create_service_log_batch
from plane.utils.service_permission import ROLE_ADMIN, ROLE_GUEST, ROLE_MEMBER, set_member_capabilities
from plane.utils.service_pool import close_period, resolve_period

# ---------------------------------------------------------------------------
# The scenario, as data
# ---------------------------------------------------------------------------

# Catalogue names as `service_catalog_seed` writes them. Looked up by name rather than
# by route or multiplier because that is the seed's own contract with the workspace, and
# a name that drifts should fail loudly here rather than silently pick another row.
HOUR_TYPE_BUSINESS = "Horário comercial"
HOUR_TYPE_AFTER_HOURS = "Fora do expediente"  # multiplier 1.50 -- criterion 5
HOUR_TYPE_SUNDAY = "Domingos e feriados"

BILLING_CONTRACT = "Contrato"
BILLING_AD_HOC = "Avulso"
BILLING_WARRANTY = "Garantia"  # non_billable -- criterion 4
BILLING_COURTESY = "Cortesia"  # non_billable -- criterion 4

# Two Clientes with a shareholding link that deliberately affects no billing (D18), plus
# a third belonging to neither portal user: the leak canary the isolation tests use.
CLIENT_MARUBENI = "Marubeni"
CLIENT_TERLOGS = "Terlogs"
CLIENT_VALE = "Vale"

# Service dates. All weekdays, so duration mode leaves the hour type to the technician
# exactly as R10 prescribes -- which is why every log below names its hour type.
_JUN = "2026-06"
_JUL = "2026-07"
_AUG = "2026-08"


class Command(BaseCommand):
    help = (
        "Seed the reference demonstration scenario of the work log series into one "
        "workspace: Clientes, projects, portal users, contracts with closed and open "
        "competency periods, work logs, an allowance and the permission grants. "
        "Requires DEBUG. Idempotent."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--workspace",
            type=str,
            default="worklog-demo",
            help="Workspace slug. Created when it does not exist. Default: worklog-demo",
        )
        parser.add_argument(
            "--email-domain",
            type=str,
            default="planedev.satziack.com",
            help="Domain of the scenario users' e-mail addresses.",
        )
        parser.add_argument(
            "--password",
            type=str,
            default=None,
            help=(
                "Password for every scenario user. A distinct random one is generated "
                "per user when omitted, and printed at the end."
            ),
        )

    # ------------------------------------------------------------------ entry point

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError(
                "seed_service_demo refuses to run with DEBUG off. It invents Clientes, "
                "contracts and ledger movements that are indistinguishable from real "
                "ones, and prints user passwords to stdout. Point it at a throwaway "
                "instance, or set DEBUG=1 there if that is what this is."
            )

        slug = options["workspace"]
        domain = options["email_domain"]
        fixed_password = options["password"]

        self.credentials = []
        self.password_of = {}

        previous_user = get_current_user()

        try:
            admin = self._user(
                "admin",
                "Ana",
                "Prado",
                domain,
                fixed_password,
                label="Admin do workspace e da instância",
            )

            # Every `BaseModel.save()` downstream reads crum for `created_by`, and R8
            # cares who typed a row. Without this the whole scenario is authored by
            # nobody, and the audit trail the series spent nine phases building would
            # open on a demo full of blanks.
            set_current_user(admin)

            workspace = self._workspace(slug, admin)
            self._instance(admin)
            self._catalogs(workspace)
            self._people(workspace, admin, domain, fixed_password)
            self._clients_and_projects(workspace)
            self._memberships(workspace)
            self._grants(workspace, admin)
            self._prices(workspace, admin)
            self._contracts(workspace, admin)
            self._marubeni_scenario(workspace, admin)
            self._terlogs_scenario(workspace, admin)
            self._vale_scenario(workspace, admin)
            self._allowance_scenario(workspace, admin)
            self._requesters(workspace)
        finally:
            set_current_user(previous_user)

        self._report(workspace)

    # ------------------------------------------------------------------ workspace

    def _workspace(self, slug, admin):
        workspace, created = Workspace.objects.get_or_create(
            slug=slug, defaults={"name": "Worklog Demo", "owner": admin}
        )

        WorkspaceMember.objects.get_or_create(
            workspace=workspace,
            member=admin,
            defaults={"role": ROLE_ADMIN, "is_active": True},
        )

        Profile.objects.filter(user=admin).update(last_workspace_id=workspace.id)

        self.stdout.write(f"workspace {slug}: {'created' if created else 'already there'}")

        return workspace

    def _instance(self, admin):
        """Make the instance itself usable: an instance admin, and setup marked done.

        Without this the seed produces a perfectly correct database behind a front door
        nobody can open. The web app gates on ``Instance.is_setup_done`` and shows "Set up
        your instance and create your first workspace" instead of a login form -- so a
        freshly seeded box looks broken, and the credentials handed over do not work.

        Normally that flag is flipped by the god-mode first-run flow, which also creates
        the first ``InstanceAdmin``. Both are done here because a demo box is seeded by
        this command and by nothing else, and "run this, then go through a wizard" is a
        step that gets forgotten.

        Skipped silently when no ``Instance`` row exists yet: ``register_instance`` runs on
        the first API boot, so this is only ever the case when the seed is pointed at a
        database whose API has never started -- the test suite, for one.
        """
        from plane.license.models import Instance, InstanceAdmin

        instance = Instance.objects.first()

        if instance is None:
            self.stdout.write("instância: nenhuma registrada ainda, nada a marcar")
            return

        _, created = InstanceAdmin.objects.get_or_create(
            instance=instance, user=admin, defaults={"role": ROLE_ADMIN}
        )

        if not instance.is_setup_done:
            instance.is_setup_done = True
            instance.save(update_fields=["is_setup_done", "updated_at"])

        self.stdout.write(
            f"instância: admin {'criado' if created else 'já existia'}, setup marcado como concluído"
        )

    def _catalogs(self, workspace):
        """Hour types, billing types and the R10 windows, through the existing seeder."""
        created = seed_service_catalogs(
            ServiceHourType,
            ServiceBillingType,
            workspace.id,
            window_model=ServiceClassificationWindow,
        )
        self.stdout.write(
            f"catálogos: +{created['hour_types_created']} tipo(s) de hora, "
            f"+{created['billing_types_created']} tipo(s) de atendimento, "
            f"+{created.get('windows_created', 0)} janela(s)"
        )

        self.hour_types = {
            hour_type.name: hour_type
            for hour_type in ServiceHourType.objects.filter(
                workspace=workspace, deleted_at__isnull=True
            )
        }
        self.billing_types = {
            billing_type.name: billing_type
            for billing_type in ServiceBillingType.objects.filter(
                workspace=workspace, deleted_at__isnull=True
            )
        }

        for name in (HOUR_TYPE_BUSINESS, HOUR_TYPE_AFTER_HOURS, HOUR_TYPE_SUNDAY):
            if name not in self.hour_types:
                raise CommandError(f"Hour type '{name}' is missing from the catalogue.")

        for name in (BILLING_CONTRACT, BILLING_AD_HOC, BILLING_WARRANTY, BILLING_COURTESY):
            if name not in self.billing_types:
                raise CommandError(f"Billing type '{name}' is missing from the catalogue.")

        # The demonstration of criterion 5 rests on this number, so it is asserted rather
        # than assumed: an admin who retuned the multiplier to 1.8 would otherwise leave
        # the scenario quietly telling a different story than the script handed over.
        after_hours = self.hour_types[HOUR_TYPE_AFTER_HOURS]
        if after_hours.multiplier != Decimal("1.50"):
            raise CommandError(
                f"'{HOUR_TYPE_AFTER_HOURS}' has multiplier {after_hours.multiplier}, and the "
                "scenario is built around 1.50. Reset it, or seed a fresh workspace."
            )

    # ------------------------------------------------------------------ people

    def _user(self, handle, first_name, last_name, domain, fixed_password, *, label):
        """A scenario user who can log in with a password, with onboarding out of the way.

        ``is_password_autoset`` off and ``is_onboarded`` on are what make the handover
        credentials usable: with the defaults, the first login lands on "set a password"
        and then on the onboarding wizard, and neither can be completed on an instance
        with no SMTP.
        """
        email = f"{handle}@{domain}"
        password = fixed_password or self._password()

        user, created = User.objects.get_or_create(
            email=email,
            defaults={
                "username": email,
                "first_name": first_name,
                "last_name": last_name,
                "display_name": f"{first_name} {last_name}",
            },
        )

        # Reset on every run, deliberately. A re-seed whose printed credentials do not
        # open the door is worse than no re-seed.
        user.set_password(password)
        user.is_password_autoset = False
        user.is_email_verified = True
        user.is_active = True
        user.save()

        profile, _ = Profile.objects.get_or_create(user=user)
        Profile.objects.filter(pk=profile.pk).update(is_onboarded=True, is_tour_completed=True)

        self.password_of[handle] = password
        self.credentials.append((label, f"{first_name} {last_name}", email, password))

        self.stdout.write(f"usuário {email}: {'created' if created else 'password reset'}")

        return user

    def _password(self):
        """A password strong enough for a public host, typeable on a phone.

        Ambiguous glyphs are out on purpose -- these get read off a chat window and typed
        into a mobile keyboard, and an l/1 or O/0 confusion turns a demo into a support
        call.
        """
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
        return f"{get_random_string(20, alphabet)}-{get_random_string(4, '23456789')}"

    def _people(self, workspace, admin, domain, fixed_password):
        self.admin = admin

        # MEMBER. Records the work, and holds the grant the seed makes through the domain.
        self.technician = self._user(
            "tecnico",
            "Bruno",
            "Tavares",
            domain,
            fixed_password,
            label="Técnico MEMBER, autor dos apontamentos, já com can_delegate",
        )

        # A second MEMBER, ungranted on purpose: criterion 7 is the *act* of granting and
        # then demoting, and it needs a technician nobody minds breaking.
        self.spare_technician = self._user(
            "tecnico2",
            "Camila",
            "Duarte",
            domain,
            fixed_password,
            label="Técnico MEMBER sem concessão — alvo do critério 7",
        )

        # GUEST of two Clientes. The whole point of the portal: he switches Cliente with
        # the native project selector and never picks a company anywhere.
        self.marcel = self._user(
            "marcel",
            "Marcel",
            "Bianchi",
            domain,
            fixed_password,
            label="GUEST cliente — Marubeni E Terlogs",
        )

        # GUEST of one. The isolation canary from the client's side.
        self.adriano = self._user(
            "adriano",
            "Adriano",
            "Correia",
            domain,
            fixed_password,
            label="GUEST cliente — só Terlogs",
        )

        # The second Marubeni GUEST, on the project whose `guest_view_all_features` is
        # off. He reaches exactly one work item, and only because he is its requester.
        self.requester_guest = self._user(
            "rafael",
            "Rafael",
            "Nunes",
            domain,
            fixed_password,
            label="2º GUEST da Marubeni — vê só o chamado em que é Solicitante",
        )

    # ------------------------------------------------------------------ clients

    def _clients_and_projects(self, workspace):
        self.clients = {}

        for name in (CLIENT_MARUBENI, CLIENT_TERLOGS, CLIENT_VALE):
            client, _ = ServiceClient.objects.get_or_create(
                workspace=workspace,
                name=name,
                deleted_at__isnull=True,
                defaults={
                    "trade_name": name,
                    "is_active": True,
                    "contact_name": "Contato de TI",
                },
            )
            self.clients[name] = client

        marubeni = self.clients[CLIENT_MARUBENI]
        terlogs = self.clients[CLIENT_TERLOGS]
        vale = self.clients[CLIENT_VALE]

        # The shareholding link. Carries no billing behaviour -- that is D18, and having
        # it in the demo is what lets someone check that it carries none.
        if terlogs.parent_id != marubeni.id:
            terlogs.parent = marubeni
            terlogs.save(update_fields=["parent", "updated_at"])

        self.projects = {
            # Marubeni's main project: clients see everything in it.
            "marubeni": self._project(workspace, "Marubeni — Suporte", "MRB", marubeni, guest_view_all=True),
            # Marubeni's second project, with client-wide visibility OFF. This is what
            # makes the requester's second effect demonstrable: Rafael is a member here
            # and still sees nothing except the one work item he was recorded on.
            "marubeni_projetos": self._project(
                workspace, "Marubeni — Projetos", "MRBP", marubeni, guest_view_all=False
            ),
            "terlogs": self._project(workspace, "Terlogs — Suporte", "TLG", terlogs, guest_view_all=True),
            "vale": self._project(workspace, "Vale — Suporte", "VALE", vale, guest_view_all=True),
            # No Cliente, no time tracking, no client visibility: the raw material for
            # criterion 8's bulk assignment, where the modal offers to set both flags.
            "infra": self._project(workspace, "Infra Interna", "INFRA", None, guest_view_all=False),
            "backoffice": self._project(workspace, "Backoffice", "BKO", None, guest_view_all=False),
        }

    def _project(self, workspace, name, identifier, service_client, *, guest_view_all):
        """A project with the default states, as ``ProjectViewSet.create`` builds one.

        The states matter: a project created without them has no board and no work item
        form, so a demo that skipped this would be a set of Clientes nobody can click into.
        """
        project, created = Project.objects.get_or_create(
            workspace=workspace,
            identifier=identifier,
            defaults={
                "name": name,
                "service_client": service_client,
                # Both flags follow the Cliente. A client project without them is the
                # misconfiguration criterion 8's modal exists to prevent.
                "guest_view_all_features": guest_view_all,
                "is_time_tracking_enabled": service_client is not None,
                "created_by": self.admin,
            },
        )

        if created:
            State.objects.bulk_create(
                [
                    State(
                        name=state["name"],
                        color=state["color"],
                        project=project,
                        sequence=state["sequence"],
                        workspace=workspace,
                        group=state["group"],
                        default=state.get("default", False),
                        created_by=self.admin,
                    )
                    for state in DEFAULT_STATES
                ]
            )

        ProjectMember.objects.get_or_create(
            project=project,
            member=self.admin,
            defaults={"workspace": workspace, "role": ROLE_ADMIN, "is_active": True},
        )

        self.stdout.write(f"project {identifier}: {'created' if created else 'already there'}")

        return project

    def _memberships(self, workspace):
        """Who reaches which project, and at which role.

        The Cliente a portal user belongs to is *derived* from this table and stored
        nowhere (master context 2b). Marcel is a GUEST of two Clientes' projects; that is
        the entire mechanism behind criterion 1.
        """
        client_projects = [
            self.projects["marubeni"],
            self.projects["marubeni_projetos"],
            self.projects["terlogs"],
            self.projects["vale"],
        ]

        for technician in (self.technician, self.spare_technician):
            self._member(workspace, technician, ROLE_MEMBER, client_projects)

        self._member(workspace, self.marcel, ROLE_GUEST, [self.projects["marubeni"], self.projects["terlogs"]])
        self._member(workspace, self.adriano, ROLE_GUEST, [self.projects["terlogs"]])
        self._member(workspace, self.requester_guest, ROLE_GUEST, [self.projects["marubeni_projetos"]])

    def _member(self, workspace, user, role, projects):
        membership, created = WorkspaceMember.objects.get_or_create(
            workspace=workspace, member=user, defaults={"role": role, "is_active": True}
        )

        # Repair rather than skip: a re-run after criterion 7 demoted somebody to GUEST
        # has to put the role back, or the second walkthrough starts from a different
        # scenario than the first.
        if not created and (membership.role != role or not membership.is_active):
            membership.role = role
            membership.is_active = True
            membership.save(update_fields=["role", "is_active", "updated_at"])

        for project in projects:
            ProjectMember.objects.get_or_create(
                project=project,
                member=user,
                defaults={"workspace": workspace, "role": role, "is_active": True},
            )

        Profile.objects.filter(user=user).update(last_workspace_id=workspace.id)

    def _grants(self, workspace, admin):
        """``can_delegate`` on the primary technician, through the domain function.

        Not a row written by hand: ``set_member_capabilities`` is what refuses a GUEST and
        what writes the audit entry, and a grant that skipped it would be a grant the
        permissions screen shows and the audit trail has never heard of.
        """
        set_member_capabilities(
            workspace_id=workspace.id,
            member_id=self.technician.id,
            actor=admin,
            can_delegate=True,
        )
        self.stdout.write(f"concessão: can_delegate para {self.technician.email}")

    # ------------------------------------------------------------------ pricing

    def _prices(self, workspace, admin):
        """A dated price sheet per contracted Cliente.

        Without one, every ad hoc log and every billed overage would carry
        ``NO_PRICE_SHEET_IN_FORCE`` and a value of zero -- which is correct behaviour and a
        useless demo. The rate of each hour type is derived from the base by the
        multiplier, never registered twice (D16).
        """
        rates = {CLIENT_MARUBENI: Decimal("180.00"), CLIENT_TERLOGS: Decimal("200.00")}

        for client_name, base_rate in rates.items():
            client = self.clients[client_name]
            exists = ServiceClientPrice.objects.filter(
                service_client=client, starts_on=date(2026, 1, 1), deleted_at__isnull=True
            ).exists()

            if exists:
                continue

            create_with_config_activity(
                ServiceClientPrice(
                    workspace=workspace,
                    service_client=client,
                    starts_on=date(2026, 1, 1),
                    base_hour_rate=base_rate,
                    notes="Tabela vigente do exercício.",
                ),
                actor=admin,
            )
            self.stdout.write(f"preço {client_name}: base R$ {base_rate}/h desde 2026-01-01")

    # ------------------------------------------------------------------ contracts

    def _contracts(self, workspace, admin):
        self.contracts = {
            # Accumulation path. Never expires and has no ceiling, so the carried parcels
            # stay visible instead of being trimmed away by a cap the demo never explains.
            CLIENT_MARUBENI: self._contract(
                workspace,
                admin,
                client_name=CLIENT_MARUBENI,
                code="SUP-MRB-001",
                name="Suporte Marubeni",
                monthly_hours=Decimal("10.0000"),
                starts_on=date(2026, 6, 1),
                ends_on=date(2027, 5, 31),
                overage_policy=ServiceContract.OveragePolicy.CARRY_DEFICIT,
                overage_hour_rate=Decimal("180.00"),
            ),
            # Overage path: a month consumed past its quota, settled by billing, which is
            # what makes the next month open whole instead of mortgaged (B3).
            CLIENT_TERLOGS: self._contract(
                workspace,
                admin,
                client_name=CLIENT_TERLOGS,
                code="SUP-TLG-001",
                name="Suporte Terlogs",
                monthly_hours=Decimal("30.0000"),
                starts_on=date(2026, 6, 1),
                ends_on=date(2029, 5, 31),
                overage_policy=ServiceContract.OveragePolicy.BILL_AMOUNT,
                overage_hour_rate=Decimal("250.00"),
            ),
            CLIENT_VALE: self._contract(
                workspace,
                admin,
                client_name=CLIENT_VALE,
                code="SUP-VALE-001",
                name="Suporte Vale",
                monthly_hours=Decimal("20.0000"),
                starts_on=date(2026, 6, 1),
                ends_on=date(2027, 5, 31),
                overage_policy=ServiceContract.OveragePolicy.CARRY_DEFICIT,
                overage_hour_rate=Decimal("220.00"),
            ),
        }

    def _contract(self, workspace, admin, *, client_name, code, name, **fields):
        client = self.clients[client_name]
        existing = ServiceContract.objects.filter(
            service_client=client, code=code, deleted_at__isnull=True
        ).first()

        if existing is not None:
            return existing

        contract = create_with_config_activity(
            ServiceContract(
                workspace=workspace,
                service_client=client,
                code=code,
                name=name,
                status=ServiceContract.Status.ACTIVE,
                is_default=True,
                # Never expires, no ceiling. Section 3 of the contracts brief makes this
                # the explicit default, and it keeps the accumulation legible.
                carryover_months=None,
                accrual_cap_mode=ServiceContract.AccrualCapMode.NONE,
                accrual_cap_value=None,
                **fields,
            ),
            actor=admin,
        )
        self.stdout.write(f"contrato {code}: {fields['monthly_hours']}h/mês, {fields['starts_on']} a {fields['ends_on']}")

        return contract

    # ------------------------------------------------------------------ work logs

    def _issue(self, project, name, *, description=""):
        """A work item, and whether this run created it.

        The flag is the idempotency seam for everything expensive: work logs, credits and
        closes are only performed for a work item this run brought into existence, so a
        second run cannot double the consumption of a period.
        """
        existing = Issue.objects.filter(project=project, name=name, deleted_at__isnull=True).first()

        if existing is not None:
            return existing, False

        issue = Issue(
            name=name,
            project=project,
            workspace=project.workspace,
            description_html=f"<p>{description or name}</p>",
        )
        issue.save(created_by_id=self.technician.id)

        return issue, True

    def _log(
        self,
        issue,
        *,
        minutes,
        worked_on,
        hour_type_name=HOUR_TYPE_BUSINESS,
        billing_type_name=BILLING_CONTRACT,
        description="Atendimento registrado no cenário de demonstração.",
        author=None,
    ):
        """One work log, through the pipeline the API uses. Nothing else.

        ``build_segments`` → ``build_batch_rows`` → ``create_service_log_batch`` →
        ``settle_service_log_batch``. The last call is where the hours actually leave a
        pool or become a value in reais, and it is the reason this seed can be trusted:
        the demo's balances were produced by the code under demonstration.

        The hour type is always named. In duration mode on a weekday the engine returns no
        suggestion by design (R10), so leaving it out would raise ``HOUR_TYPE_REQUIRED``.
        """
        project = issue.project

        segments = build_segments(
            worked_on=worked_on,
            raw_duration_minutes=minutes,
            entry_mode=ServiceLogEntryMode.DURATION,
            workspace_id=project.workspace_id,
            service_client=project.service_client,
        )

        rows = build_batch_rows(
            issue=issue,
            author=author or self.technician,
            description=description,
            billing_type=self.billing_types[billing_type_name],
            hour_type=self.hour_types[hour_type_name],
            segments=segments,
            entry_mode=ServiceLogEntryMode.DURATION,
            source=ServiceLogSource.MANUAL,
        )

        create_service_log_batch(rows)
        settle_service_log_batch(rows, actor=author or self.technician)

        return rows

    def _close(self, contract, competence, *, settlement=None, actor=None):
        """Close a competency month, unless it is already closed.

        Closing is what turns a surplus into carried parcels and a deficit into either a
        carry or a billed overage -- so the accumulation and the overage in this demo are
        both side effects of the real settlement routine, computed at the moment of the
        close from whatever the logs actually consumed.
        """
        year, month = competence
        period = resolve_period(contract, date(year, month, 1), actor=actor)

        if period.status == ServicePeriodStatus.CLOSED:
            return period

        closed = close_period(period, settlement=settlement, actor=actor)
        closed.refresh_from_db()
        self.stdout.write(
            f"  {contract.code} {closed.competence_label}: fechado "
            f"(contratado {closed.contracted_hours}h, transportado {closed.carried_hours}h, "
            f"consumido {closed.consumed_hours}h, excedente {closed.overage_hours}h)"
        )

        return closed

    def _marubeni_scenario(self, workspace, admin):
        """Marubeni: three periods, accumulation, and the two demonstrations R11 needs.

        June and July are closed under budget, so August opens carrying parcels from both
        -- which is the "parcela de competência anterior" a client should be able to see
        the origin of. August then holds the 1.5x log (criterion 5), one non-billable log
        of each kind (criterion 4), and an ad hoc log whose value in reais must never
        reach Marcel (criterion 2).
        """
        contract = self.contracts[CLIENT_MARUBENI]
        project = self.projects["marubeni"]

        self.stdout.write("cenário Marubeni:")

        # ---- June: 4h of a 10h quota -> surplus of 6h carried out on close
        issue, created = self._issue(project, "Lentidão no ERP após atualização")
        if created:
            self._log(issue, minutes=150, worked_on=date(2026, 6, 10))  # 2.5h
            self._log(issue, minutes=90, worked_on=date(2026, 6, 17))  # 1.5h
        self._close(contract, (2026, 6), actor=admin)

        # ---- July: 3h of a 16h granted quota -> 13h carried into August
        issue, created = self._issue(project, "Integração com a transportadora falhando")
        if created:
            self._log(issue, minutes=180, worked_on=date(2026, 7, 8))  # 3h
        self._close(contract, (2026, 7), actor=admin)

        # ---- August: open, and where the interesting rows live
        resolve_period(contract, date(2026, 8, 1), actor=admin)

        issue, created = self._issue(project, "Impressora fiscal não emite NF")
        self.marubeni_requester_issue = issue
        if created:
            # Criterion 5. One hour of work, at multiplier 1.50: the technician's panel
            # must read 1h and the client's 1,5h, from these very columns.
            self._log(
                issue,
                minutes=60,
                worked_on=date(2026, 8, 4),
                hour_type_name=HOUR_TYPE_AFTER_HOURS,
                description="Chamado noturno: 1h apontada, 1,5h equivalente.",
            )
            # An ordinary business-hours entry on the same work item, so the 1.5x row has
            # something to be compared against. Without it the multiplier is a claim; next
            # to a 2h/2h row it is visible.
            self._log(
                issue,
                minutes=120,
                worked_on=date(2026, 8, 5),
                description="Continuação em horário comercial: 2h apontadas, 2h equivalentes.",
            )

        issue, created = self._issue(project, "Retrabalho da carga de notas")
        if created:
            # Criterion 4, first half. Garantia: rework already paid for.
            self._log(
                issue,
                minutes=120,
                worked_on=date(2026, 8, 5),
                billing_type_name=BILLING_WARRANTY,
                description="Retrabalho de atendimento já cobrado. Não debita, não fatura.",
            )

        issue, created = self._issue(project, "Treinamento rápido do time fiscal")
        if created:
            # Criterion 4, second half. Cortesia: a discount that was chosen. Same route
            # as Garantia, different row -- which is exactly why a report must not add
            # them into one "não faturável" total.
            self._log(
                issue,
                minutes=60,
                worked_on=date(2026, 8, 6),
                billing_type_name=BILLING_COURTESY,
                description="Cortesia comercial concedida. Não debita, não fatura.",
            )

        issue, created = self._issue(project, "Servidor de arquivos novo — fora do contrato")
        if created:
            # Ad hoc: priced in reais rather than debited. Criterion 2 is that Marcel
            # never sees this number.
            self._log(
                issue,
                minutes=120,
                worked_on=date(2026, 8, 7),
                billing_type_name=BILLING_AD_HOC,
                description="Escopo fora do contrato, faturado à parte.",
            )

    def _terlogs_scenario(self, workspace, admin):
        """Terlogs: a month consumed past its quota, settled as billed overage.

        34h against a 30h quota. Closing with ``BILLED`` records 4h of overage and its
        value at the contract's overage rate, and leaves July opening with its full 30h --
        the difference between paying for the excess and mortgaging the rest of the
        contract.
        """
        contract = self.contracts[CLIENT_TERLOGS]
        project = self.projects["terlogs"]

        self.stdout.write("cenário Terlogs:")

        # ---- June: 34h of a 30h quota
        issue, created = self._issue(project, "Migração do WMS do centro de distribuição")
        if created:
            # 8 + 8 + 6 + 6 + 4 + 2 = 34h against a 30h quota. Every duration is already a
            # multiple of R2's 15 minute block, so the rounding is a no-op and the overage
            # the close computes is exactly 4h -- which is what makes the figure in the
            # handover script safe to state.
            for day, minutes in ((9, 480), (10, 480), (11, 360), (16, 360), (17, 240), (18, 120)):
                self._log(issue, minutes=minutes, worked_on=date(2026, 6, day))
        self._close(contract, (2026, 6), settlement=ServiceOverageSettlement.BILLED, actor=admin)

        # ---- July: 12h of 30h -> 18h carried into August
        issue, created = self._issue(project, "Ajustes de rota no roteirizador")
        if created:
            self._log(issue, minutes=420, worked_on=date(2026, 7, 14))  # 7h
            self._log(issue, minutes=300, worked_on=date(2026, 7, 21))  # 5h
        self._close(contract, (2026, 7), actor=admin)

        # ---- August: open, with a Sunday call at 2.0 so the two multipliers coexist
        resolve_period(contract, date(2026, 8, 1), actor=admin)

        issue, created = self._issue(project, "Coletores fora do ar na expedição")
        if created:
            self._log(issue, minutes=180, worked_on=date(2026, 8, 5))
            self._log(
                issue,
                minutes=120,
                worked_on=date(2026, 8, 2),  # a Sunday
                hour_type_name=HOUR_TYPE_SUNDAY,
                description="Plantão de domingo.",
            )

    def _vale_scenario(self, workspace, admin):
        """Vale: the leak canary, with enough substance to be recognisable if it leaks."""
        contract = self.contracts[CLIENT_VALE]
        project = self.projects["vale"]

        resolve_period(contract, date(2026, 8, 1), actor=admin)

        issue, created = self._issue(project, "Inventário de ativos da planta")
        if created:
            self._log(issue, minutes=480, worked_on=date(2026, 8, 6))

    def _allowance_scenario(self, workspace, admin):
        """A per-work-item allowance with two credits, and a log that debits it.

        Two credits rather than one 50h credit: the history is the point. Each ``CREDIT``
        row carries its own author and date, which is what "histórico de crédito" means
        here -- not a pair of columns the second credit would overwrite.

        The log against this work item debits the **allowance**, not the contract, which is
        R6's hierarchy doing its job. That is worth seeing next to the pool balance.
        """
        project = self.projects["marubeni_projetos"]

        issue, created = self._issue(
            project,
            "Migração do ERP para a nuvem — bolsa de horas",
            description="Projeto com bolsa de horas contratada à parte do pool mensal.",
        )
        self.allowance_issue = issue

        # Keyed on the credit history rather than on the work item, because the two credits
        # are the thing that must not double. `allowance_credits` returns a list, so this
        # is a length test and not an `.exists()`.
        existing = ServiceIssueAllowance.objects.filter(issue=issue).first()
        already_credited = existing is not None and len(allowance_credits(existing)) > 0

        if not already_credited:
            credit_allowance(
                issue,
                Decimal("40.0000"),
                actor=admin,
                reference="PROP-2026-041",
                notes="Bolsa aprovada para a migração.",
                credit_notes="Crédito inicial da proposta PROP-2026-041.",
            )
            credit_allowance(
                issue,
                Decimal("10.0000"),
                actor=admin,
                credit_notes="Complemento aprovado após revisão de escopo.",
            )

        if created:
            self._log(
                issue,
                minutes=240,
                worked_on=date(2026, 8, 6),
                description="Levantamento de integrações. Debita a bolsa, não o pool.",
            )

        allowance = ServiceIssueAllowance.objects.filter(issue=issue).first()
        if allowance is not None:
            allowance.refresh_from_db()
            self.stdout.write(
                f"bolsa {issue.name[:32]}…: creditado {allowance.credited_hours}h, "
                f"consumido {allowance.consumed_hours}h, saldo {allowance.balance_hours}h"
            )

    def _requesters(self, workspace):
        """Who asked for the ticket. Two of them, for two different reasons.

        Marcel on a project that already shows him everything: the field is there for the
        attribution and the notification. Rafael on the project whose client-wide
        visibility is **off**: without this row he reaches nothing, and with it he reaches
        exactly one work item. That second case is the one that proves the feature does
        something.
        """
        pairs = (
            (self.marubeni_requester_issue, self.marcel),
            (self.allowance_issue, self.requester_guest),
        )

        for issue, requester in pairs:
            ServiceIssueRequester.objects.get_or_create(
                issue=issue,
                defaults={
                    "requester": requester,
                    "project": issue.project,
                    "workspace": workspace,
                    "created_by": self.technician,
                },
            )
            self.stdout.write(f"solicitante de '{issue.name[:38]}': {requester.email}")

    # ------------------------------------------------------------------ handover

    def _report(self, workspace):
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Cenário semeado no workspace '{workspace.slug}'."))
        self.stdout.write("")
        self.stdout.write("Credenciais (guarde: as senhas não são recuperáveis depois):")
        self.stdout.write("")

        for label, name, email, password in self.credentials:
            self.stdout.write(f"  {name} — {label}")
            self.stdout.write(f"    {email}")
            self.stdout.write(f"    {password}")
            self.stdout.write("")
