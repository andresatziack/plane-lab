# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Settlement and the monthly consolidation. Decisions D31, D33 and D36, section 5.

Acceptance criteria 9, 10, 11 and 13 live here, plus the two structural guarantees that
Phase 6 added in DDL: a work log is never both debited and billed, and an overage is never
billed twice.
"""

# Python imports
from datetime import date
from decimal import Decimal

# Third party imports
import pytest

# Django imports
from django.db import IntegrityError
from django.db.models import Sum

# Module imports
from plane.db.models import (
    ServiceBillingType,
    ServiceClientPrice,
    ServiceContract,
    ServiceContractPeriod,
    ServiceHourLedgerEntry,
    ServiceLedgerEntryType,
    ServiceLog,
    ServiceRouteDeviation,
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
from plane.tests.hour_display import hour_fields_missing_a_rendered_twin
from plane.utils.service_billing import (
    RevenueOrigin,
    consolidated_billing,
    resolve_settlement,
    settle_service_log,
)
from plane.utils.service_log import build_batch_rows, create_service_log_batch
from plane.utils.service_pool import close_period, resolve_period
from plane.utils.service_pricing import NO_PRICE_SHEET_FOR_CLIENT

# `unit` beside `django_db` on purpose: this file used to carry only the database marker, so
# `pytest -m "unit or contract"` skipped it silently -- which is how a change to the billing
# payload was reported green while three tests here were failing. The conventions document
# runs the suite with no marker filter; this makes the filtered run tell the truth too.
pytestmark = [pytest.mark.unit, pytest.mark.django_db]


@pytest.fixture
def billing_setup(db):
    """A client priced at R$ 200,00/h, with a 30h/month contract for 2026."""
    service_client = ServiceClientFactory(name="Marubeni")
    workspace = service_client.workspace
    project = ProjectFactory(workspace=workspace, service_client=service_client)
    project.is_time_tracking_enabled = True
    project.save()

    contract = ServiceContractFactory(
        service_client=service_client,
        code="SUP-001",
        monthly_hours=Decimal("30.0000"),
        starts_on=date(2026, 1, 1),
        ends_on=date(2026, 12, 31),
        overage_hour_rate=Decimal("250.00"),
    )
    ServiceClientPrice.objects.create(
        workspace=workspace,
        service_client=service_client,
        starts_on=date(2026, 1, 1),
        base_hour_rate=Decimal("200.00"),
    )

    return {
        "client": service_client,
        "workspace": workspace,
        "project": project,
        "contract": contract,
        "actor": UserFactory(),
        "catalog": {
            "normal": ServiceHourTypeFactory(
                workspace=workspace, name="Comercial", multiplier=Decimal("1.00")
            ),
            "pool": ServiceBillingTypeFactory(
                workspace=workspace,
                name="Contrato",
                billing_route=ServiceBillingType.BillingRoute.DEBIT_POOL,
            ),
            "avulso": ServiceBillingTypeFactory(
                workspace=workspace,
                name="Avulso",
                billing_route=ServiceBillingType.BillingRoute.BILL_AMOUNT,
            ),
            "warranty": ServiceBillingTypeFactory(
                workspace=workspace,
                name="Garantia",
                billing_route=ServiceBillingType.BillingRoute.NON_BILLABLE,
            ),
        },
    }


def settle(setup, *, minutes=60, billing="pool", worked_on=date(2026, 3, 10), project=None):
    """One work log, persisted and settled through the real pipeline."""
    issue = IssueFactory(project=project or setup["project"])
    segment = type(
        "Segment",
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

    rows = build_batch_rows(
        issue=issue,
        author=setup["actor"],
        description="Atendimento",
        billing_type=setup["catalog"][billing],
        hour_type=setup["catalog"]["normal"],
        segments=[segment],
    )
    create_service_log_batch(rows)
    settle_service_log(rows[0], actor=setup["actor"])

    return rows[0]


class TestAPoolDebitAndAChargeAreMutuallyExclusive:
    """The monetary counterpart of what D29 achieved for hours, in DDL."""

    def test_a_pool_route_debits_hours_and_carries_no_money(self, billing_setup):
        log = settle(billing_setup, billing="pool")

        assert log.settled_billing_route == ServiceBillingType.BillingRoute.DEBIT_POOL
        assert log.debited_period_id is not None
        assert log.amount == Decimal("0.00")
        assert log.applied_hour_rate is None

    def test_a_billed_route_carries_money_and_debits_no_pool(self, billing_setup):
        log = settle(billing_setup, billing="avulso")

        assert log.settled_billing_route == ServiceBillingType.BillingRoute.BILL_AMOUNT
        assert log.amount == Decimal("200.00")
        assert log.debited_period_id is None
        assert log.debited_allowance_id is None

    def test_the_database_refuses_a_row_that_is_both(self, billing_setup):
        """**Charging in reais and consuming contracted hours for the same work log is
        refused by the database, not by the order of an ``if``.**

        This is the sabotage the design named: an invoice line and a pool debit for one hour
        of work, which bills the client twice for it.
        """
        pooled = settle(billing_setup, billing="pool")

        assert pooled.debited_period_id is not None, "positive control"

        with pytest.raises(IntegrityError):
            ServiceLog.all_objects.filter(pk=pooled.pk).update(
                settled_billing_route=ServiceBillingType.BillingRoute.BILL_AMOUNT,
                applied_hour_rate=Decimal("200.00"),
                applied_rate_basis="base_multiplier",
                amount=Decimal("200.00"),
            )


class TestD33TheRouteDeviation:
    """A pool route with no usable contract is billed, and says why."""

    def test_a_client_with_no_contract_is_billed_as_avulso(self, billing_setup):
        fresh = ServiceClientFactory(name="Sem contrato", workspace=billing_setup["workspace"])
        ServiceClientPrice.objects.create(
            workspace=billing_setup["workspace"],
            service_client=fresh,
            starts_on=date(2026, 1, 1),
            base_hour_rate=Decimal("150.00"),
        )
        project = ProjectFactory(workspace=billing_setup["workspace"], service_client=fresh)
        project.is_time_tracking_enabled = True
        project.save()

        log = settle(billing_setup, billing="pool", project=project)

        assert log.applied_billing_route == ServiceBillingType.BillingRoute.DEBIT_POOL
        assert log.settled_billing_route == ServiceBillingType.BillingRoute.BILL_AMOUNT
        assert log.route_deviation_reason == ServiceRouteDeviation.NO_CONTRACT_FOR_CLIENT
        assert log.amount == Decimal("150.00")

    def test_a_suspended_contract_is_billed_and_says_which_pendency(self, billing_setup):
        ServiceContract.objects.filter(pk=billing_setup["contract"].pk).update(
            status=ServiceContract.Status.SUSPENDED
        )

        log = settle(billing_setup, billing="pool")

        assert log.settled_billing_route == ServiceBillingType.BillingRoute.BILL_AMOUNT
        assert log.route_deviation_reason == ServiceRouteDeviation.CONTRACT_SUSPENDED
        assert log.amount == Decimal("200.00")

    def test_a_contract_past_its_vigency_is_billed(self, billing_setup):
        ServiceClientPrice.objects.create(
            workspace=billing_setup["workspace"],
            service_client=billing_setup["client"],
            starts_on=date(2027, 1, 1),
            base_hour_rate=Decimal("220.00"),
        )

        log = settle(billing_setup, billing="pool", worked_on=date(2027, 3, 10))

        assert log.route_deviation_reason == ServiceRouteDeviation.CONTRACT_EXPIRED
        assert log.amount == Decimal("220.00"), "priced at the 2027 sheet"

    def test_an_ended_contract_is_a_distinct_reason_from_an_expired_one(self, billing_setup):
        """Both are avulso now, but only one is somebody forgetting to renew."""
        ServiceContract.objects.filter(pk=billing_setup["contract"].pk).update(
            status=ServiceContract.Status.ENDED
        )

        log = settle(billing_setup, billing="pool")

        assert log.route_deviation_reason == ServiceRouteDeviation.CONTRACT_ENDED

    def test_a_healthy_contract_does_not_deviate(self, billing_setup):
        """Positive control for all four above."""
        log = settle(billing_setup, billing="pool")

        assert log.route_deviation_reason is None
        assert log.settled_billing_route == log.applied_billing_route

    def test_a_non_billable_route_never_deviates(self, billing_setup):
        """A warranty repair during a suspended contract is still a warranty repair."""
        ServiceContract.objects.filter(pk=billing_setup["contract"].pk).update(
            status=ServiceContract.Status.SUSPENDED
        )

        log = settle(billing_setup, billing="warranty")

        assert log.settled_billing_route == ServiceBillingType.BillingRoute.NON_BILLABLE
        assert log.route_deviation_reason is None
        assert log.amount == Decimal("0.00")

    def test_ambiguous_contract_resolution_still_blocks(self, billing_setup):
        """D27 is untouched by D33: billing the client to avoid choosing a pool is not a
        repair, it is a different wrong answer."""
        from plane.utils.service_pool import ServicePoolValidationError

        ServiceContractFactory(
            service_client=billing_setup["client"],
            code="SUP-002",
            starts_on=date(2026, 1, 1),
            ends_on=date(2026, 12, 31),
        )

        issue = IssueFactory(project=billing_setup["project"])
        rows = build_batch_rows(
            issue=issue,
            author=billing_setup["actor"],
            description="x",
            billing_type=billing_setup["catalog"]["pool"],
            hour_type=billing_setup["catalog"]["normal"],
            segments=[
                type("S", (), {
                    "worked_on": date(2026, 3, 10), "start_time": None, "end_time": None,
                    "raw_duration_minutes": 60, "suggested_hour_type": None, "reason": "",
                })()
            ],
        )
        create_service_log_batch(rows)

        with pytest.raises(ServicePoolValidationError) as caught:
            settle_service_log(rows[0], actor=billing_setup["actor"])

        assert caught.value.code == "AMBIGUOUS_CONTRACT_RESOLUTION"


class TestD31NoPeriodOutsideTheVigency:
    """The 390h-against-a-360h-contract bug, and the assertion that catches it."""

    def test_work_before_the_contract_starts_debits_the_first_period(self, billing_setup):
        log = settle(billing_setup, billing="pool", worked_on=date(2025, 11, 20))

        period = ServiceContractPeriod.objects.get(pk=log.debited_period_id)

        assert period.competence_label == "2026-01"
        assert period.consumed_hours == Decimal("1.0000")

    def test_no_period_is_materialised_outside_the_vigency(self, billing_setup):
        """**The sabotage: materialising a period for a month the contract does not cover.**

        A 30h/month contract running twelve months sells 360h. A thirteenth period would grant
        a thirteenth quota -- hours the system invented.
        """
        settle(billing_setup, billing="pool", worked_on=date(2025, 11, 20))

        assert not ServiceContractPeriod.objects.filter(
            contract=billing_setup["contract"], competence_year=2025
        ).exists()

        granted = ServiceContractPeriod.objects.filter(
            contract=billing_setup["contract"]
        ).aggregate(total=Sum("contracted_hours"))["total"]

        assert granted == Decimal("30.0000"), "one period exists, and it grants one month"

    def test_the_read_path_agrees_with_the_write_path(self, billing_setup):
        """``resolve_period`` clamps inside itself, so a dashboard previews the period a debit
        would actually use rather than a different one."""
        previewed = resolve_period(
            billing_setup["contract"], date(2025, 11, 20), materialize=False
        )
        assert previewed is None, "nothing materialised yet"

        settle(billing_setup, billing="pool", worked_on=date(2025, 11, 20))

        previewed = resolve_period(
            billing_setup["contract"], date(2025, 11, 20), materialize=False
        )
        assert previewed is not None
        assert previewed.competence_label == "2026-01"


class TestOverageIsBilledOnce:
    """Acceptance criterion 9, promoted from an ``if`` to a database refusal."""

    def test_billing_the_overage_writes_hours_and_reais_on_one_row(self, billing_setup):
        settle(billing_setup, billing="pool", minutes=33 * 60, worked_on=date(2026, 1, 15))
        january = resolve_period(billing_setup["contract"], date(2026, 1, 1))

        close_period(january, settlement="billed", actor=billing_setup["actor"])

        billed = ServiceHourLedgerEntry.objects.get(
            period=january, entry_type=ServiceLedgerEntryType.OVERAGE_BILLED
        )

        assert billed.hours == Decimal("3.0000")
        assert billed.amount == Decimal("750.00")
        assert billed.applied_hour_rate == Decimal("250.00")

    def test_a_second_overage_billing_for_one_period_is_refused_by_the_database(
        self, billing_setup
    ):
        """**The sabotage, and it now fails with a ``UniqueViolation`` rather than an
        ``AssertionError`` about a Python guard.**

        The existing partial index does not cover this row -- it is conditioned on
        ``service_log__isnull=False`` and an ``OVERAGE_BILLED`` entry has no work log -- so
        duplicates were representable until Phase 6.
        """
        settle(billing_setup, billing="pool", minutes=33 * 60, worked_on=date(2026, 1, 15))
        january = resolve_period(billing_setup["contract"], date(2026, 1, 1))
        close_period(january, settlement="billed", actor=billing_setup["actor"])

        with pytest.raises(IntegrityError):
            ServiceHourLedgerEntry.objects.create(
                workspace_id=january.workspace_id,
                contract_id=january.contract_id,
                period=january,
                hours=Decimal("3.0000"),
                entry_type=ServiceLedgerEntryType.OVERAGE_BILLED,
                amount=Decimal("750.00"),
                applied_hour_rate=Decimal("250.00"),
            )

    def test_the_reconciliation_notices_a_tampered_amount(self, billing_setup):
        """The monetary half of D23's check: ``amount`` must equal hours times rate."""
        from plane.utils.service_pool import reconcile_period

        settle(billing_setup, billing="pool", minutes=33 * 60, worked_on=date(2026, 1, 15))
        january = resolve_period(billing_setup["contract"], date(2026, 1, 1))
        close_period(january, settlement="billed", actor=billing_setup["actor"])

        # `close_period` writes through its own locked re-read, so the instance held here is
        # stale. Reconciliation compares the ledger against the row it is given, so a stale
        # row would report a discrepancy that does not exist in the database.
        january.refresh_from_db()

        assert reconcile_period(january)["is_consistent"], "positive control"

        ServiceHourLedgerEntry.objects.filter(
            period=january, entry_type=ServiceLedgerEntryType.OVERAGE_BILLED
        ).update(amount=Decimal("1.00"))

        result = reconcile_period(january)

        assert result["is_consistent"] is False
        assert any(item["field"].startswith("overage_amount") for item in result["discrepancies"])

    def test_billing_without_a_resolvable_rate_is_refused(self, billing_setup):
        """Decision D. The admin can carry the deficit instead; billing R$ 0,00 would destroy
        a real debt in silence."""
        from plane.utils.service_pricing import (
            OVERAGE_RATE_NOT_CONFIGURED,
            ServicePricingValidationError,
        )

        ServiceContract.objects.filter(pk=billing_setup["contract"].pk).update(
            overage_hour_rate=None
        )
        ServiceClientPrice.objects.filter(service_client=billing_setup["client"]).delete()

        settle(billing_setup, billing="pool", minutes=33 * 60, worked_on=date(2026, 1, 15))
        january = resolve_period(billing_setup["contract"], date(2026, 1, 1))

        with pytest.raises(ServicePricingValidationError) as caught:
            close_period(january, settlement="billed", actor=billing_setup["actor"])

        assert caught.value.code == OVERAGE_RATE_NOT_CONFIGURED

        january.refresh_from_db()
        assert january.status == ServiceContractPeriod.Status.OPEN, "the close rolled back"

    def test_carrying_the_deficit_needs_no_rate(self, billing_setup):
        """Positive control for the refusal above: the alternative really is available."""
        ServiceContract.objects.filter(pk=billing_setup["contract"].pk).update(
            overage_hour_rate=None
        )
        ServiceClientPrice.objects.filter(service_client=billing_setup["client"]).delete()

        settle(billing_setup, billing="pool", minutes=33 * 60, worked_on=date(2026, 1, 15))
        january = resolve_period(billing_setup["contract"], date(2026, 1, 1))

        closed = close_period(january, settlement="carried", actor=billing_setup["actor"])

        assert closed.status == ServiceContractPeriod.Status.CLOSED
        assert closed.overage_settlement == "carried"


class TestTheOveragePreviewSaysItIsIrreversible:
    """Requirement F1 of the design review: an irreversible action must be deliberate."""

    def test_the_preview_shows_the_value_and_the_rate_before_billing(self, billing_setup):
        from plane.utils.service_pool import overage_billing_preview

        settle(billing_setup, billing="pool", minutes=33 * 60, worked_on=date(2026, 1, 15))
        january = resolve_period(billing_setup["contract"], date(2026, 1, 1))

        preview = overage_billing_preview(january)

        assert preview["overage_hours"] == "3.0000"
        # The confirmation modal prints the hours it is about to bill irreversibly, so they
        # ship rendered as well (D68). An Admin reading "3.0000" and an Admin reading "3h" are
        # confirming the same charge, and only one of them is being told in the product's own
        # notation.
        assert preview["overage_hours_display"] == "3h"
        assert hour_fields_missing_a_rendered_twin(preview) == []
        assert preview["overage_hour_rate"] == "250.00"
        assert preview["amount"] == "750.00"
        assert preview["rate_source"] == "contract"
        assert preview["is_reversible"] is False, "stated, not left for the client to know"

    def test_the_preview_writes_nothing(self, billing_setup):
        from plane.utils.service_pool import overage_billing_preview

        settle(billing_setup, billing="pool", minutes=33 * 60, worked_on=date(2026, 1, 15))
        january = resolve_period(billing_setup["contract"], date(2026, 1, 1))

        overage_billing_preview(january)
        january.refresh_from_db()

        assert january.status == ServiceContractPeriod.Status.OPEN
        assert not ServiceHourLedgerEntry.objects.filter(
            period=january, entry_type=ServiceLedgerEntryType.OVERAGE_BILLED
        ).exists()


class TestTheConsolidation:
    """Section 5 and decision D36: four origins, and internal work outside revenue."""

    def test_an_avulso_client_lands_in_standalone(self, billing_setup):
        settle(billing_setup, billing="avulso", worked_on=date(2026, 3, 10))

        result = consolidated_billing(billing_setup["workspace"].id, 2026, 3)
        origins = result["clients"][0]["origins"]

        assert origins[RevenueOrigin.OUT_OF_SCOPE_LOG]["amount"] == "200.00"
        assert result["total_amount"] == "200.00"

    def test_every_hour_in_the_consolidation_ships_a_rendered_twin(self, billing_setup):
        """D68, asserted structurally on the payload rather than field by field.

        This report is reached through ``as unknown as`` casts in ``billing-tab.tsx``, so
        TypeScript cannot see a missing key here. The walker is the substitute: any hour added
        to an origin bucket, a pendency list or the internal-work block without its rendered
        twin fails here instead of printing "1.2500h" at a client, which is the exact defect
        D68 was written for.
        """
        internal = ProjectFactory(workspace=billing_setup["workspace"], service_client=None)
        internal.is_time_tracking_enabled = True
        internal.save()

        settle(billing_setup, billing="avulso", minutes=75, worked_on=date(2026, 3, 10))
        settle(billing_setup, billing="avulso", worked_on=date(2026, 3, 11), project=internal)

        result = consolidated_billing(billing_setup["workspace"].id, 2026, 3)

        assert hour_fields_missing_a_rendered_twin(result) == []
        assert result["internal_work"]["hours_display"] == "1h"
        assert (
            result["clients"][0]["origins"][RevenueOrigin.OUT_OF_SCOPE_LOG]["hours_display"]
            == "1h 15min"
        )

    def test_a_deviated_log_is_standalone_with_its_reason_beside_it(self, billing_setup):
        """D33's reason reaches the consolidation, because "avulso by design" and "the
        contract expired" need different people to do different things."""
        ServiceContract.objects.filter(pk=billing_setup["contract"].pk).update(
            status=ServiceContract.Status.SUSPENDED
        )
        settle(billing_setup, billing="pool", worked_on=date(2026, 3, 10))

        entry = consolidated_billing(billing_setup["workspace"].id, 2026, 3)["clients"][0]

        assert entry["origins"][RevenueOrigin.STANDALONE_LOG]["amount"] == "200.00"
        # Still an EXACT dict, deliberately: `billing-tab.tsx` reaches this payload through
        # `as unknown as` casts, so TypeScript cannot see a renamed or missing key and this
        # assertion is the only structural guard the shape has. `hours_display` is D68's
        # rendered twin -- the screen used to concatenate a literal "h" onto "1.0000".
        assert entry["commercial_pendencies"] == [
            {
                "reason": ServiceRouteDeviation.CONTRACT_SUSPENDED,
                "hours": "1.0000",
                "hours_display": "1h",
                "amount": "200.00",
                "amount_display": "R$ 200,00",
                "entries": 1,
            }
        ]

    def test_internal_work_is_excluded_from_revenue_not_listed_as_a_pendency(
        self, billing_setup
    ):
        """**Decision E.** A company that does real internal work must not face a panel full
        of "failures" nobody can act on -- an operator who learns to ignore the list misses
        the one entry that mattered."""
        internal = ProjectFactory(workspace=billing_setup["workspace"], service_client=None)
        internal.is_time_tracking_enabled = True
        internal.save()

        settle(billing_setup, billing="avulso", worked_on=date(2026, 3, 10), project=internal)

        result = consolidated_billing(billing_setup["workspace"].id, 2026, 3)

        assert result["total_amount"] == "0.00"
        assert result["internal_work"] == {
            "hours": "1.0000",
            "hours_display": "1h",
            "entries": 1,
        }
        assert result["clients"] == [], "internal work creates no client entry at all"

    def test_a_registration_pendency_carries_hours_but_no_amount(self, billing_setup):
        """It is real money that cannot be invoiced yet, so it must not inflate the total."""
        fresh = ServiceClientFactory(name="Sem preco", workspace=billing_setup["workspace"])
        project = ProjectFactory(workspace=billing_setup["workspace"], service_client=fresh)
        project.is_time_tracking_enabled = True
        project.save()

        settle(billing_setup, billing="avulso", worked_on=date(2026, 3, 10), project=project)

        result = consolidated_billing(billing_setup["workspace"].id, 2026, 3)
        entry = next(c for c in result["clients"] if c["service_client_name"] == "Sem preco")

        assert entry["total_amount"] == "0.00"
        assert entry["registration_pendencies"] == [
            {
                "reason": NO_PRICE_SHEET_FOR_CLIENT,
                "hours": "1.0000",
                "hours_display": "1h",
                "entries": 1,
            }
        ]
        assert "amount" not in entry["registration_pendencies"][0], (
            "no amount exists yet: printing R$ 0,00 would make a missing price sheet look "
            "like a finished calculation"
        )
        assert result["total_amount"] == "0.00"

    def test_a_contract_overage_lands_in_the_periods_competency_not_the_closing_month(
        self, billing_setup
    ):
        """R7 per origin. Closing in April must not put the overage in April's invoice."""
        settle(billing_setup, billing="pool", minutes=33 * 60, worked_on=date(2026, 1, 15))
        january = resolve_period(billing_setup["contract"], date(2026, 1, 1))
        close_period(january, settlement="billed", actor=billing_setup["actor"])

        january_result = consolidated_billing(billing_setup["workspace"].id, 2026, 1)
        assert january_result["clients"][0]["origins"][RevenueOrigin.CONTRACT_OVERAGE][
            "amount"
        ] == "750.00"

        # And nowhere else.
        february_result = consolidated_billing(billing_setup["workspace"].id, 2026, 2)
        assert february_result["total_amount"] == "0.00"

    def test_the_total_is_a_sum_of_persisted_amounts(self, billing_setup):
        """Acceptance criterion 13. Three logs of 0.25h at a rate that produces a half-cent
        tie: the total is the sum of the rounded lines, which is what the invoice says."""
        ServiceClientPrice.objects.filter(service_client=billing_setup["client"]).update(
            base_hour_rate=Decimal("180.50")
        )

        for _ in range(3):
            settle(billing_setup, billing="avulso", minutes=15, worked_on=date(2026, 3, 10))

        result = consolidated_billing(billing_setup["workspace"].id, 2026, 3)

        assert result["total_amount"] == "135.39", "3 x R$ 45,13, not R$ 135,38"
        assert result["total_amount_display"] == "R$ 135,39"

    def test_a_pool_debit_contributes_nothing_to_revenue(self, billing_setup):
        """Positive control: contracted hours are already paid for."""
        settle(billing_setup, billing="pool", worked_on=date(2026, 3, 10))

        result = consolidated_billing(billing_setup["workspace"].id, 2026, 3)

        assert result["total_amount"] == "0.00"
        assert result["clients"] == []

    def test_a_deleted_log_leaves_the_total(self, billing_setup):
        """No monetary reversal exists, and none is needed: the amount lives on the row, so
        soft deleting the row removes it from every total by removing it from ``objects``."""
        log = settle(billing_setup, billing="avulso", worked_on=date(2026, 3, 10))

        assert consolidated_billing(billing_setup["workspace"].id, 2026, 3)["total_amount"] == "200.00"

        log.delete()

        assert consolidated_billing(billing_setup["workspace"].id, 2026, 3)["total_amount"] == "0.00"

    def test_filtering_by_client_narrows_the_report(self, billing_setup):
        other = ServiceClientFactory(name="Outro", workspace=billing_setup["workspace"])
        ServiceClientPrice.objects.create(
            workspace=billing_setup["workspace"],
            service_client=other,
            starts_on=date(2026, 1, 1),
            base_hour_rate=Decimal("100.00"),
        )
        project = ProjectFactory(workspace=billing_setup["workspace"], service_client=other)
        project.is_time_tracking_enabled = True
        project.save()

        settle(billing_setup, billing="avulso", worked_on=date(2026, 3, 10))
        settle(billing_setup, billing="avulso", worked_on=date(2026, 3, 10), project=project)

        everything = consolidated_billing(billing_setup["workspace"].id, 2026, 3)
        assert everything["total_amount"] == "300.00"

        narrowed = consolidated_billing(
            billing_setup["workspace"].id, 2026, 3, service_client_id=str(other.pk)
        )
        assert narrowed["total_amount"] == "100.00"


class TestSettlementResolutionOrder:
    """R6's hierarchy, asserted as an order rather than as four independent outcomes."""

    def test_an_allowance_pays_before_the_contract_is_even_consulted(self, billing_setup):
        from plane.utils.service_allowance import credit_allowance

        issue = IssueFactory(project=billing_setup["project"])
        credit_allowance(issue, Decimal("10.0000"), actor=billing_setup["actor"])

        rows = build_batch_rows(
            issue=issue,
            author=billing_setup["actor"],
            description="x",
            billing_type=billing_setup["catalog"]["pool"],
            hour_type=billing_setup["catalog"]["normal"],
            segments=[
                type("S", (), {
                    "worked_on": date(2026, 3, 10), "start_time": None, "end_time": None,
                    "raw_duration_minutes": 60, "suggested_hour_type": None, "reason": "",
                })()
            ],
        )
        create_service_log_batch(rows)

        settlement = resolve_settlement(rows[0])

        assert settlement["allowance"] is not None
        assert settlement["contract"] is None, "the contract was not consulted"

    def test_an_allowance_wins_even_when_the_contract_has_expired(self, billing_setup):
        """The allowance check comes **before** D33, so a client whose contract lapsed still
        debits their project allowance rather than being billed twice for the same sale."""
        from plane.utils.service_allowance import credit_allowance

        ServiceContract.objects.filter(pk=billing_setup["contract"].pk).update(
            status=ServiceContract.Status.ENDED
        )

        issue = IssueFactory(project=billing_setup["project"])
        credit_allowance(issue, Decimal("10.0000"), actor=billing_setup["actor"])

        rows = build_batch_rows(
            issue=issue,
            author=billing_setup["actor"],
            description="x",
            billing_type=billing_setup["catalog"]["pool"],
            hour_type=billing_setup["catalog"]["normal"],
            segments=[
                type("S", (), {
                    "worked_on": date(2026, 3, 10), "start_time": None, "end_time": None,
                    "raw_duration_minutes": 60, "suggested_hour_type": None, "reason": "",
                })()
            ],
        )
        create_service_log_batch(rows)
        settle_service_log(rows[0], actor=billing_setup["actor"])

        assert rows[0].debited_allowance_id is not None
        assert rows[0].amount == Decimal("0.00")
        assert rows[0].route_deviation_reason is None
