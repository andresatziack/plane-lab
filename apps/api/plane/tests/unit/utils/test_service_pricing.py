# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Price sheets: which rate is in force, and what a work log is worth. Sections 1 to 3.

Acceptance criteria 4, 5, 6 and 12 live here. The pure arithmetic is in
``test_service_money.py``; this is the half that reads the database.

**Every absence test asserts the reason code, with a positive control in the same
fixture.** R$ 0,00 is true for five different reasons and they are five different bugs, so
a test that only checked the amount was zero would pass for the wrong one.
"""

# Python imports
from datetime import date
from decimal import Decimal

# Third party imports
import pytest

# Module imports
from plane.db.models import (
    ServiceBillingType,
    ServiceClientHourTypeRate,
    ServiceClientPrice,
    ServiceRateBasis,
)
from plane.tests.factories import (
    IssueFactory,
    ProjectFactory,
    ServiceBillingTypeFactory,
    ServiceClientFactory,
    ServiceHourTypeFactory,
    UserFactory,
)
from plane.utils.service_log import build_batch_rows, create_service_log_batch
from plane.utils.service_pricing import (
    INTERNAL_PROJECT_NO_CLIENT,
    NO_PRICE_SHEET_FOR_CLIENT,
    NO_PRICE_SHEET_IN_FORCE,
    OVERAGE_RATE_NOT_CONFIGURED,
    ServicePricingValidationError,
    client_has_any_price_sheet,
    effective_rate_table,
    resolve_log_price,
    resolve_overage_rate,
    resolve_price_sheet,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def workspace_setup(db):
    """A client with three hour types, priced at R$ 200,00/h from January 2026.

    Asserts the *specific* configuration rather than a count: "one price sheet" could be one
    for the wrong reasons, while "R$ 200,00 from 2026-01-01, multipliers 1.0/1.5/2.0" is
    precisely what makes the numbers below land where the criteria say they should.
    """
    service_client = ServiceClientFactory(name="Marubeni")
    workspace = service_client.workspace
    project = ProjectFactory(workspace=workspace, service_client=service_client)
    project.is_time_tracking_enabled = True
    project.save()

    catalog = {
        "normal": ServiceHourTypeFactory(
            workspace=workspace, name="Horario comercial", multiplier=Decimal("1.00")
        ),
        "after": ServiceHourTypeFactory(
            workspace=workspace, name="Fora do expediente", multiplier=Decimal("1.50")
        ),
        "sunday": ServiceHourTypeFactory(
            workspace=workspace, name="Domingos e feriados", multiplier=Decimal("2.00")
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
    }

    sheet = ServiceClientPrice.objects.create(
        workspace=workspace,
        service_client=service_client,
        starts_on=date(2026, 1, 1),
        base_hour_rate=Decimal("200.00"),
    )

    assert sheet.base_hour_rate == Decimal("200.00")
    assert catalog["after"].multiplier == Decimal("1.50")

    return {
        "client": service_client,
        "workspace": workspace,
        "project": project,
        "catalog": catalog,
        "sheet": sheet,
        "actor": UserFactory(),
    }


def price_one_log(setup, *, minutes=60, hour_type="normal", billing="avulso", worked_on=None):
    """One persisted work log, priced, through the real pipeline.

    Goes through ``build_batch_rows`` rather than a factory so the hour arithmetic and the R4
    snapshots are the ones production writes -- a hand-built row could carry numbers the
    multiplier never produced.
    """
    issue = IssueFactory(project=setup["project"])
    segment = type(
        "Segment",
        (),
        {
            "worked_on": worked_on or date(2026, 3, 10),
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
        hour_type=setup["catalog"][hour_type],
        segments=[segment],
    )
    create_service_log_batch(rows)

    return rows[0]


class TestVigencyResolution:
    """"The greatest ``starts_on`` not after the date." Section 2."""

    def test_the_sheet_in_force_is_the_latest_one_that_has_started(self, workspace_setup):
        client = workspace_setup["client"]
        ServiceClientPrice.objects.create(
            workspace=workspace_setup["workspace"],
            service_client=client,
            starts_on=date(2026, 7, 1),
            base_hour_rate=Decimal("220.00"),
        )

        assert resolve_price_sheet(client.pk, date(2026, 6, 30)).base_hour_rate == Decimal("200.00")
        assert resolve_price_sheet(client.pk, date(2026, 7, 1)).base_hour_rate == Decimal("220.00")
        assert resolve_price_sheet(client.pk, date(2027, 1, 1)).base_hour_rate == Decimal("220.00")

    def test_a_date_before_the_first_sheet_resolves_to_nothing(self, workspace_setup):
        """The one open case of the design, and it is an absence rather than an ambiguity."""
        assert resolve_price_sheet(workspace_setup["client"].pk, date(2025, 12, 31)) is None

    def test_there_is_no_gap_between_consecutive_vigencies(self, workspace_setup):
        """Why there is no ``ends_on``: a sheet holds until the next one starts.

        A closed interval pair would let a gap exist, and a gap is a date with no price. Here
        every date from the first sheet onwards resolves to exactly one sheet.
        """
        client = workspace_setup["client"]
        ServiceClientPrice.objects.create(
            workspace=workspace_setup["workspace"],
            service_client=client,
            starts_on=date(2026, 7, 1),
            base_hour_rate=Decimal("220.00"),
        )

        for day in (date(2026, 1, 1), date(2026, 3, 15), date(2026, 6, 30), date(2026, 12, 31)):
            assert resolve_price_sheet(client.pk, day) is not None, f"{day} has a price"

    def test_deleting_a_sheet_restores_the_previous_one(self, workspace_setup):
        """Resolution is a fresh ordered read, not a stored pointer, so this works for free."""
        client = workspace_setup["client"]
        readjustment = ServiceClientPrice.objects.create(
            workspace=workspace_setup["workspace"],
            service_client=client,
            starts_on=date(2026, 7, 1),
            base_hour_rate=Decimal("220.00"),
        )

        assert resolve_price_sheet(client.pk, date(2026, 8, 1)).base_hour_rate == Decimal("220.00")

        readjustment.delete()

        assert resolve_price_sheet(client.pk, date(2026, 8, 1)).base_hour_rate == Decimal("200.00")


class TestTheEffectiveTable:
    """Section 1: one number registered, the whole table derived."""

    def test_one_registered_number_produces_a_rate_for_every_hour_type(self, workspace_setup):
        """"Configurar um cliente novo deve exigir mudar um único número."""
        table = effective_rate_table(
            workspace_setup["client"].pk,
            date(2026, 3, 1),
            workspace_id=workspace_setup["workspace"].pk,
        )

        by_name = {row["hour_type_name"]: row for row in table}

        assert by_name["Horario comercial"]["rate"] == "200.00"
        assert by_name["Fora do expediente"]["rate"] == "300.00"
        assert by_name["Domingos e feriados"]["rate"] == "400.00"
        assert all(row["basis"] == ServiceRateBasis.BASE_MULTIPLIER for row in table)
        assert not any(row["is_overridden"] for row in table)

    def test_an_override_replaces_the_derived_rate_and_says_so(self, workspace_setup):
        """Criterion 5, on the display side."""
        ServiceClientHourTypeRate.objects.create(
            workspace=workspace_setup["workspace"],
            price=workspace_setup["sheet"],
            hour_type=workspace_setup["catalog"]["sunday"],
            absolute_rate=Decimal("350.00"),
        )

        table = effective_rate_table(
            workspace_setup["client"].pk,
            date(2026, 3, 1),
            workspace_id=workspace_setup["workspace"].pk,
        )
        by_name = {row["hour_type_name"]: row for row in table}

        assert by_name["Domingos e feriados"]["rate"] == "350.00"
        assert by_name["Domingos e feriados"]["is_overridden"] is True
        assert by_name["Domingos e feriados"]["basis"] == ServiceRateBasis.ABSOLUTE_OVERRIDE

        # Positive control: the other rows are untouched, so the override is an exception and
        # not a switch that changed the whole table.
        assert by_name["Fora do expediente"]["rate"] == "300.00"
        assert by_name["Fora do expediente"]["is_overridden"] is False

    def test_the_table_is_empty_when_no_sheet_is_in_force(self, workspace_setup):
        """Empty, not a table of zeros. Inventing R$ 0,00 next to real hour types would read
        as a finished calculation."""
        assert (
            effective_rate_table(
                workspace_setup["client"].pk,
                date(2025, 6, 1),
                workspace_id=workspace_setup["workspace"].pk,
            )
            == []
        )

    def test_an_override_belongs_to_one_vigency_only(self, workspace_setup):
        """**The reason the override is a child of the sheet rather than of the client.**

        A readjustment creates a new sheet. The old sheet's absolute override does *not* leak
        into it -- if it did, being an absolute value, it would silently stop being
        readjusted: the base moves and the Sunday rate stays at last year's number forever.
        """
        ServiceClientHourTypeRate.objects.create(
            workspace=workspace_setup["workspace"],
            price=workspace_setup["sheet"],
            hour_type=workspace_setup["catalog"]["sunday"],
            absolute_rate=Decimal("350.00"),
        )
        ServiceClientPrice.objects.create(
            workspace=workspace_setup["workspace"],
            service_client=workspace_setup["client"],
            starts_on=date(2027, 1, 1),
            base_hour_rate=Decimal("220.00"),
        )

        after = {
            row["hour_type_name"]: row
            for row in effective_rate_table(
                workspace_setup["client"].pk,
                date(2027, 3, 1),
                workspace_id=workspace_setup["workspace"].pk,
            )
        }

        assert after["Domingos e feriados"]["rate"] == "440.00", "220 * 2.0, readjusted"
        assert after["Domingos e feriados"]["is_overridden"] is False


class TestPricingAWorkLog:
    """Criteria 1 to 5, through the real pipeline and onto the row."""

    def test_a_commercial_hour_is_priced_from_the_base_rate(self, workspace_setup):
        log = price_one_log(workspace_setup)
        price = resolve_log_price(log, service_client_id=workspace_setup["client"].pk)

        assert price["amount"] == Decimal("200.00")
        assert price["applied_hour_rate"] == Decimal("200.00")
        assert price["applied_rate_basis"] == ServiceRateBasis.BASE_MULTIPLIER
        assert price["pricing_failure_reason"] is None

    def test_an_after_hours_entry_is_priced_through_its_equivalent_hours(self, workspace_setup):
        """Criterion 2. The multiplier enters via the hours, exactly once."""
        log = price_one_log(workspace_setup, hour_type="after")

        assert log.logged_hours == Decimal("1.0000")
        assert log.equivalent_hours == Decimal("1.5000")

        price = resolve_log_price(log, service_client_id=workspace_setup["client"].pk)
        assert price["amount"] == Decimal("300.00")

    def test_an_override_prices_from_logged_hours_not_equivalent_hours(self, workspace_setup):
        """**Criterion 5, and the double-charge it hides.**

        One hour on Sundays (multiplier 2.0) with an absolute override of R$ 350,00. The row
        carries logged 1.0 and equivalent 2.0. The answer is R$ 350,00; using equivalent
        hours would give R$ 700,00.
        """
        ServiceClientHourTypeRate.objects.create(
            workspace=workspace_setup["workspace"],
            price=workspace_setup["sheet"],
            hour_type=workspace_setup["catalog"]["sunday"],
            absolute_rate=Decimal("350.00"),
        )

        log = price_one_log(workspace_setup, hour_type="sunday")
        assert (log.logged_hours, log.equivalent_hours) == (Decimal("1.0000"), Decimal("2.0000"))

        price = resolve_log_price(log, service_client_id=workspace_setup["client"].pk)

        assert price["amount"] == Decimal("350.00"), "not 700.00 -- the override embeds the 2.0"
        assert price["applied_rate_basis"] == ServiceRateBasis.ABSOLUTE_OVERRIDE
        assert price["applied_hour_rate"] == Decimal("350.00")

    def test_an_hour_type_without_an_override_still_uses_the_base(self, workspace_setup):
        """Positive control for the test above: the override is per hour type, not per sheet."""
        ServiceClientHourTypeRate.objects.create(
            workspace=workspace_setup["workspace"],
            price=workspace_setup["sheet"],
            hour_type=workspace_setup["catalog"]["sunday"],
            absolute_rate=Decimal("350.00"),
        )

        log = price_one_log(workspace_setup, hour_type="after")
        price = resolve_log_price(log, service_client_id=workspace_setup["client"].pk)

        assert price["amount"] == Decimal("300.00")
        assert price["applied_rate_basis"] == ServiceRateBasis.BASE_MULTIPLIER


class TestVigencyIsSelectedByTheServiceDate:
    """Acceptance criteria 6 and 12, which are the reason this table exists at all."""

    def test_a_retroactive_entry_prices_at_the_rate_of_its_service_date(self, workspace_setup):
        """**Criterion 12.** March work typed after an April readjustment is March's price.

        Selecting by the typing date instead would put an April price on a March invoice, and
        make two logs for the same March day priced a month apart disagree -- the March
        invoice would stop being reproducible.
        """
        ServiceClientPrice.objects.create(
            workspace=workspace_setup["workspace"],
            service_client=workspace_setup["client"],
            starts_on=date(2026, 4, 1),
            base_hour_rate=Decimal("300.00"),
        )

        march = price_one_log(workspace_setup, worked_on=date(2026, 3, 10))
        may = price_one_log(workspace_setup, worked_on=date(2026, 5, 10))

        assert resolve_log_price(
            march, service_client_id=workspace_setup["client"].pk
        )["amount"] == Decimal("200.00"), "March's rate, not April's"
        assert resolve_log_price(
            may, service_client_id=workspace_setup["client"].pk
        )["amount"] == Decimal("300.00")

    def test_a_readjustment_does_not_move_an_already_priced_log(self, workspace_setup):
        """**Criterion 6.** The R4 snapshot, asserted on the persisted row.

        The log is settled first, then the base rate is readjusted. The stored amount does not
        move, because nothing re-reads the sheet.
        """
        from plane.utils.service_billing import settle_service_log

        log = price_one_log(workspace_setup, worked_on=date(2026, 3, 10))
        settle_service_log(log, actor=workspace_setup["actor"])

        assert log.amount == Decimal("200.00")

        ServiceClientPrice.objects.filter(pk=workspace_setup["sheet"].pk).update(
            base_hour_rate=Decimal("999.00")
        )
        log.refresh_from_db()

        assert log.amount == Decimal("200.00"), "the snapshot held"
        assert log.applied_hour_rate == Decimal("200.00")


class TestEveryAbsenceStatesItsReason:
    """R$ 0,00 is true for five reasons, and they are five different bugs.

    Each test below asserts the **code**, and each has a positive control that shows the same
    fixture produces a real price when the configuration is there.
    """

    def test_a_project_with_no_client_is_internal_work_not_a_failure(self, workspace_setup):
        """Section 1 of Phase 1. Nobody must act on this, so it must not read as a pendency."""
        internal = ProjectFactory(workspace=workspace_setup["workspace"], service_client=None)
        internal.is_time_tracking_enabled = True
        internal.save()

        setup = {**workspace_setup, "project": internal}
        log = price_one_log(setup)

        price = resolve_log_price(log, service_client_id=None)

        assert price["pricing_failure_reason"] == INTERNAL_PROJECT_NO_CLIENT
        assert price["amount"] == Decimal("0.00")
        assert price["applied_hour_rate"] is None

    def test_a_client_with_no_sheet_at_all_says_so(self, workspace_setup):
        """"Nobody ever registered a price." Distinct from the test below."""
        fresh = ServiceClientFactory(name="Sem preco", workspace=workspace_setup["workspace"])
        project = ProjectFactory(workspace=workspace_setup["workspace"], service_client=fresh)
        project.is_time_tracking_enabled = True
        project.save()

        assert client_has_any_price_sheet(fresh.pk) is False

        log = price_one_log({**workspace_setup, "project": project})
        price = resolve_log_price(log, service_client_id=fresh.pk)

        assert price["pricing_failure_reason"] == NO_PRICE_SHEET_FOR_CLIENT
        assert price["amount"] == Decimal("0.00")

    def test_a_sheet_registered_too_late_says_something_different(self, workspace_setup):
        """"Somebody registered it with the wrong start date." The **opposite** mistake.

        This is the case the design review added: ``worked_on`` before the first vigency. It
        does not block -- the work was already performed (D27, D9) -- and it does not collapse
        into the code above, because one needs a price registered and the other needs a start
        date corrected.
        """
        log = price_one_log(workspace_setup, worked_on=date(2025, 11, 20))

        assert client_has_any_price_sheet(workspace_setup["client"].pk) is True

        price = resolve_log_price(log, service_client_id=workspace_setup["client"].pk)

        assert price["pricing_failure_reason"] == NO_PRICE_SHEET_IN_FORCE
        assert price["amount"] == Decimal("0.00")
        assert price["applied_hour_rate"] is None

    def test_the_positive_control_for_all_three(self, workspace_setup):
        """Same fixture, a date inside the vigency, a real price. Without this the three tests
        above would pass on a ``resolve_log_price`` that always returned zero."""
        log = price_one_log(workspace_setup, worked_on=date(2026, 3, 10))
        price = resolve_log_price(log, service_client_id=workspace_setup["client"].pk)

        assert price["pricing_failure_reason"] is None
        assert price["amount"] == Decimal("200.00")

    def test_a_non_billable_route_never_reaches_pricing(self, workspace_setup):
        """Criterion 4, and R5. The zero comes out of the route, not out of a price lookup.

        Asserted through the settlement rather than through ``resolve_log_price``, because the
        route gate is upstream of pricing -- which is the point: a warranty repair is not a
        priced thing that happens to cost nothing.
        """
        from plane.utils.service_billing import settle_service_log

        log = price_one_log(workspace_setup, billing="warranty")
        settle_service_log(log, actor=workspace_setup["actor"])

        assert log.amount == Decimal("0.00")
        assert log.applied_hour_rate is None
        assert log.pricing_failure_reason is None, "nothing failed -- it is simply not billable"
        assert log.settled_billing_route == ServiceBillingType.BillingRoute.NON_BILLABLE


class TestOverageRateResolution:
    """Section 6, and the one place in the phase that refuses instead of recording."""

    def test_the_contract_rate_wins_over_the_client_base_rate(self, workspace_setup):
        from plane.tests.factories import ServiceContractFactory

        contract = ServiceContractFactory(
            service_client=workspace_setup["client"],
            starts_on=date(2026, 1, 1),
            ends_on=date(2026, 12, 31),
            overage_hour_rate=Decimal("250.00"),
        )

        assert resolve_overage_rate(
            service_client_id=workspace_setup["client"].pk,
            on_date=date(2026, 3, 1),
            contract=contract,
        ) == Decimal("250.00")

    def test_without_a_contract_rate_it_falls_back_to_the_client_base(self, workspace_setup):
        """Positive control for the precedence above."""
        from plane.tests.factories import ServiceContractFactory

        contract = ServiceContractFactory(
            service_client=workspace_setup["client"],
            starts_on=date(2026, 1, 1),
            ends_on=date(2026, 12, 31),
            overage_hour_rate=None,
        )

        assert resolve_overage_rate(
            service_client_id=workspace_setup["client"].pk,
            on_date=date(2026, 3, 1),
            contract=contract,
        ) == Decimal("200.00")

    def test_with_no_rate_anywhere_it_refuses_rather_than_billing_zero(self, workspace_setup):
        """**Decision D: the one blocking path, and why it does not contradict D27.**

        D27, D9 and D21 protect *work already executed by a technician*. Billing an overage is
        a deliberate administrative act with a legitimate alternative available -- carry the
        deficit instead. Billing R$ 0,00 would silently zero a real debt, which is worse than
        either.
        """
        from plane.tests.factories import ServiceContractFactory

        fresh = ServiceClientFactory(name="Sem preco algum", workspace=workspace_setup["workspace"])
        contract = ServiceContractFactory(
            service_client=fresh,
            starts_on=date(2026, 1, 1),
            ends_on=date(2026, 12, 31),
            overage_hour_rate=None,
        )

        with pytest.raises(ServicePricingValidationError) as caught:
            resolve_overage_rate(
                service_client_id=fresh.pk, on_date=date(2026, 3, 1), contract=contract
            )

        assert caught.value.code == OVERAGE_RATE_NOT_CONFIGURED
        assert caught.value.detail["service_client_id"] == str(fresh.pk)
