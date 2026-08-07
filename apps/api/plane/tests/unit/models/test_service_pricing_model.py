# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""What the database refuses to store. Phase 6's DDL.

These tests write through ``objects.create`` and queryset ``update()`` on purpose --
bypassing every serializer, validator and domain function -- because that is exactly the
path a future bulk update or data migration would take. A rule held only in Python is a rule
the next ``.update()`` walks past.
"""

# Python imports
from datetime import date
from decimal import Decimal

# Third party imports
import pytest

# Django imports
from django.db import IntegrityError, transaction

# Module imports
from plane.db.models import (
    ServiceBillingType,
    ServiceClientHourTypeRate,
    ServiceClientPrice,
    ServiceIssueAllowance,
    ServiceLog,
    ServiceRateBasis,
    ServiceRouteDeviation,
)
from plane.tests.factories import (
    ServiceClientFactory,
    ServiceContractFactory,
    ServiceHourTypeFactory,
    ServiceIssueAllowanceFactory,
    ServiceLogFactory,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def sheet(db):
    service_client = ServiceClientFactory(name="Marubeni")

    return ServiceClientPrice.objects.create(
        workspace=service_client.workspace,
        service_client=service_client,
        starts_on=date(2026, 1, 1),
        base_hour_rate=Decimal("200.00"),
    )


def refuses(callable_):
    """Assert the database refuses, in its own transaction so the test can continue."""
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            callable_()


class TestThePriceSheet:
    def test_a_vigency_must_start_on_the_first_of_a_month(self, sheet):
        """**Not cosmetic.** A work log is priced by ``worked_on`` and a contract overage by
        the period's start; a sheet taking effect on the 28th would make those two readings
        disagree inside one invoice, and would reach backwards over three already-worked weeks
        in the supplier's favour. Day 1 makes the two coincide by construction.
        """
        refuses(
            lambda: ServiceClientPrice.objects.create(
                workspace=sheet.workspace,
                service_client=sheet.service_client,
                starts_on=date(2026, 5, 15),
                base_hour_rate=Decimal("300.00"),
            )
        )

        # Positive control: the same insert on the 1st is accepted.
        assert ServiceClientPrice.objects.create(
            workspace=sheet.workspace,
            service_client=sheet.service_client,
            starts_on=date(2026, 5, 1),
            base_hour_rate=Decimal("300.00"),
        ).pk

    def test_a_base_rate_of_zero_is_refused(self, sheet):
        """Decision D20: exactly one mechanism for "do not charge", and it is the
        ``NON_BILLABLE`` route. A rate of zero would be a second one, and a report filtering
        on the route would silently miss half the free hours."""
        refuses(
            lambda: ServiceClientPrice.objects.create(
                workspace=sheet.workspace,
                service_client=sheet.service_client,
                starts_on=date(2026, 6, 1),
                base_hour_rate=Decimal("0.00"),
            )
        )

    def test_a_negative_base_rate_is_refused(self, sheet):
        refuses(
            lambda: ServiceClientPrice.objects.create(
                workspace=sheet.workspace,
                service_client=sheet.service_client,
                starts_on=date(2026, 6, 1),
                base_hour_rate=Decimal("-1.00"),
            )
        )

    def test_two_sheets_cannot_start_on_the_same_day(self, sheet):
        """What makes an overlapping vigency unrepresentable, and therefore what lets
        resolution be one ordered read with no tie to break."""
        refuses(
            lambda: ServiceClientPrice.objects.create(
                workspace=sheet.workspace,
                service_client=sheet.service_client,
                starts_on=date(2026, 1, 1),
                base_hour_rate=Decimal("999.00"),
            )
        )

    def test_a_soft_deleted_sheet_frees_its_start_date(self, sheet):
        """The house soft-delete pattern: the partial index only covers live rows, so a
        mistaken sheet can be removed and the date registered again."""
        sheet.delete()

        assert ServiceClientPrice.objects.create(
            workspace=sheet.workspace,
            service_client=sheet.service_client,
            starts_on=date(2026, 1, 1),
            base_hour_rate=Decimal("210.00"),
        ).pk


class TestTheAbsoluteOverride:
    def test_one_override_per_hour_type_per_sheet(self, sheet):
        hour_type = ServiceHourTypeFactory(workspace=sheet.workspace, name="Domingos")
        ServiceClientHourTypeRate.objects.create(
            workspace=sheet.workspace, price=sheet, hour_type=hour_type,
            absolute_rate=Decimal("350.00"),
        )

        refuses(
            lambda: ServiceClientHourTypeRate.objects.create(
                workspace=sheet.workspace, price=sheet, hour_type=hour_type,
                absolute_rate=Decimal("400.00"),
            )
        )

    def test_an_absolute_rate_of_zero_is_refused(self, sheet):
        hour_type = ServiceHourTypeFactory(workspace=sheet.workspace, name="Domingos")

        refuses(
            lambda: ServiceClientHourTypeRate.objects.create(
                workspace=sheet.workspace, price=sheet, hour_type=hour_type,
                absolute_rate=Decimal("0.00"),
            )
        )


class TestTheWorkLogsMonetaryColumns:
    def test_an_amount_without_a_rate_is_refused(self):
        """All three of rate, basis and amount travel together or none of them exist. A value
        with no rate cannot be audited back to a decision."""
        log = ServiceLogFactory()

        refuses(lambda: ServiceLog.all_objects.filter(pk=log.pk).update(amount=Decimal("200.00")))

    def test_a_rate_without_an_amount_is_refused(self):
        log = ServiceLogFactory()

        refuses(
            lambda: ServiceLog.all_objects.filter(pk=log.pk).update(
                applied_hour_rate=Decimal("200.00"), applied_rate_basis=ServiceRateBasis.BASE_MULTIPLIER
            )
        )

    def test_a_non_billable_row_cannot_carry_money(self):
        """R5 and criterion 4, in DDL: the zero comes out of the route."""
        log = ServiceLogFactory(
            applied_billing_route=ServiceBillingType.BillingRoute.NON_BILLABLE,
            debited_hours=Decimal("0.0000"),
        )

        refuses(
            lambda: ServiceLog.all_objects.filter(pk=log.pk).update(
                applied_hour_rate=Decimal("200.00"),
                applied_rate_basis=ServiceRateBasis.BASE_MULTIPLIER,
                amount=Decimal("200.00"),
            )
        )

    def test_a_non_billable_row_cannot_deviate(self):
        """A warranty repair during a suspended contract is still a warranty repair."""
        log = ServiceLogFactory(
            applied_billing_route=ServiceBillingType.BillingRoute.NON_BILLABLE,
            debited_hours=Decimal("0.0000"),
        )

        refuses(
            lambda: ServiceLog.all_objects.filter(pk=log.pk).update(
                settled_billing_route=ServiceBillingType.BillingRoute.BILL_AMOUNT,
                route_deviation_reason=ServiceRouteDeviation.CONTRACT_SUSPENDED,
            )
        )

    def test_a_deviation_without_a_reason_is_refused(self):
        """The biconditional. "Deviated with no reason" and "a reason with no deviation" are
        both unrepresentable, not merely unlikely."""
        log = ServiceLogFactory()

        refuses(
            lambda: ServiceLog.all_objects.filter(pk=log.pk).update(
                settled_billing_route=ServiceBillingType.BillingRoute.BILL_AMOUNT
            )
        )

    def test_a_reason_without_a_deviation_is_refused(self):
        log = ServiceLogFactory()

        refuses(
            lambda: ServiceLog.all_objects.filter(pk=log.pk).update(
                route_deviation_reason=ServiceRouteDeviation.CONTRACT_EXPIRED
            )
        )

    def test_the_only_legal_deviation_is_pool_to_billed(self):
        """D33 has one shape. A deviation from any other route is refused."""
        log = ServiceLogFactory(
            applied_billing_route=ServiceBillingType.BillingRoute.BILL_AMOUNT
        )

        refuses(
            lambda: ServiceLog.all_objects.filter(pk=log.pk).update(
                settled_billing_route=ServiceBillingType.BillingRoute.DEBIT_POOL,
                route_deviation_reason=ServiceRouteDeviation.CONTRACT_EXPIRED,
            )
        )

    def test_a_coherent_deviation_is_accepted(self):
        """Positive control for the four refusals above."""
        log = ServiceLogFactory()

        assert ServiceLog.all_objects.filter(pk=log.pk).update(
            settled_billing_route=ServiceBillingType.BillingRoute.BILL_AMOUNT,
            route_deviation_reason=ServiceRouteDeviation.CONTRACT_EXPIRED,
            applied_hour_rate=Decimal("200.00"),
            applied_rate_basis=ServiceRateBasis.BASE_MULTIPLIER,
            amount=Decimal("200.00"),
        )

    def test_a_pricing_failure_cannot_sit_on_a_priced_row(self):
        """A failure code only exists where there was money to make and none was made."""
        log = ServiceLogFactory()
        ServiceLog.all_objects.filter(pk=log.pk).update(
            settled_billing_route=ServiceBillingType.BillingRoute.BILL_AMOUNT,
            route_deviation_reason=ServiceRouteDeviation.CONTRACT_EXPIRED,
            applied_hour_rate=Decimal("200.00"),
            applied_rate_basis=ServiceRateBasis.BASE_MULTIPLIER,
            amount=Decimal("200.00"),
        )

        refuses(
            lambda: ServiceLog.all_objects.filter(pk=log.pk).update(
                pricing_failure_reason="no_price_sheet_for_client"
            )
        )


class TestTheOverageRates:
    def test_a_contracts_overage_rate_of_zero_is_refused(self):
        """Phase 4 left this column unguarded because nothing read it. Phase 6 reads it, so a
        zero would be a second mechanism for free and a negative would credit the client for
        exceeding their pool."""
        contract = ServiceContractFactory()

        refuses(
            lambda: type(contract).objects.filter(pk=contract.pk).update(
                overage_hour_rate=Decimal("0.00")
            )
        )
        refuses(
            lambda: type(contract).objects.filter(pk=contract.pk).update(
                overage_hour_rate=Decimal("-5.00")
            )
        )

    def test_null_stays_legal_because_it_means_fall_back(self, sheet):
        """Positive control: null is not "free", it is "use the client's base rate"."""
        contract = ServiceContractFactory(overage_hour_rate=Decimal("250.00"))

        assert type(contract).objects.filter(pk=contract.pk).update(overage_hour_rate=None)

    def test_an_allowances_overage_rate_of_zero_is_refused(self):
        allowance = ServiceIssueAllowanceFactory()

        refuses(
            lambda: ServiceIssueAllowance.objects.filter(pk=allowance.pk).update(
                overage_hour_rate=Decimal("0.00")
            )
        )

    def test_an_allowance_rate_is_tracked_for_the_audit_trail(self):
        """Decision B: the rate is configuration that decides money, so it is audited -- unlike
        the hour accumulators, whose audit is the ledger itself. The mixin arrived for this one
        field, and the exclusions are the point."""
        assert ServiceIssueAllowance.TRACKED_FIELDS == ["overage_hour_rate"]
        assert "credited_hours" not in ServiceIssueAllowance.TRACKED_FIELDS
        assert "consumed_hours" not in ServiceIssueAllowance.TRACKED_FIELDS
