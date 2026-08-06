# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Unit tests for the model-aware half of the work log domain layer.

Segmentation, batch persistence, the three totals, and the two guards inherited from
the client entity phase. The pure arithmetic is covered in
``test_service_log_time.py``; what is tested here is which rows exist and what they
carry.
"""

import uuid
from datetime import date, time, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.db.utils import IntegrityError

from plane.db.models import ServiceBillingType, ServiceLog
from plane.tests.factories import (
    IssueFactory,
    ProjectFactory,
    ServiceBillingTypeFactory,
    ServiceClientFactory,
    ServiceHourTypeFactory,
    ServiceLogFactory,
    UserFactory,
    WorkspaceFactory,
)
from plane.utils.service_log import (
    HOUR_TYPE_REQUIRED,
    ONLY_THE_AUTHOR_CAN_CHANGE_A_SERVICE_LOG,
    SERVICE_CLIENT_CANNOT_BE_CHANGED_WITH_SERVICE_LOGS,
    SERVICE_CLIENT_CANNOT_BE_UNSET_WITH_SERVICE_LOGS,
    TIME_TRACKING_DISABLED_FOR_PROJECT,
    WORKED_ON_CANNOT_BE_IN_THE_FUTURE,
    Segment,
    ServiceLogValidationError,
    annotate_service_log_totals,
    apply_minimum_block_guardrail,
    build_batch_rows,
    build_segments,
    compute_hour_quantities,
    create_service_log_batch,
    delete_service_log_batch,
    is_billable_route,
    issue_service_log_totals,
    project_has_service_logs,
    replace_service_log_batch,
    resolve_segment_hour_type,
    service_client_change_alert,
    validate_author_can_change,
    validate_service_client_change,
    validate_time_tracking_enabled,
    validate_worked_on,
    workspace_today,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def no_celery_dispatch():
    """Soft delete fires ``soft_delete_related_objects.delay``; no broker in tests."""
    with patch("celery.app.task.Task.apply_async", return_value=None):
        yield


@pytest.fixture
def workspace(db):
    return WorkspaceFactory()


@pytest.fixture
def project(workspace):
    return ProjectFactory(workspace=workspace, is_time_tracking_enabled=True)


@pytest.fixture
def issue(project):
    return IssueFactory(project=project)


@pytest.fixture
def author(db):
    return UserFactory()


@pytest.fixture
def commercial_hours(workspace):
    """Horário comercial, multiplier 1.00."""
    return ServiceHourTypeFactory(workspace=workspace, name="Horário comercial", multiplier=Decimal("1.00"))


@pytest.fixture
def after_hours(workspace):
    """Fora do expediente, multiplier 1.50."""
    return ServiceHourTypeFactory(workspace=workspace, name="Fora do expediente", multiplier=Decimal("1.50"))


@pytest.fixture
def contract_billing(workspace):
    return ServiceBillingTypeFactory(
        workspace=workspace,
        name="Contrato",
        billing_route=ServiceBillingType.BillingRoute.DEBIT_POOL,
    )


@pytest.fixture
def warranty_billing(workspace):
    """Garantia -- a NON_BILLABLE route. D20 removed the boolean this replaces."""
    return ServiceBillingTypeFactory(
        workspace=workspace,
        name="Garantia",
        billing_route=ServiceBillingType.BillingRoute.NON_BILLABLE,
    )


@pytest.fixture
def courtesy_billing(workspace):
    """Cortesia -- the *other* NON_BILLABLE route, and why the route is snapshotted."""
    return ServiceBillingTypeFactory(
        workspace=workspace,
        name="Cortesia",
        billing_route=ServiceBillingType.BillingRoute.NON_BILLABLE,
    )


class TestIsBillableRoute:
    """Rules R5 and R11(a). The single place ``NON_BILLABLE`` is interpreted."""

    def test_debit_pool_and_bill_amount_are_billable(self):
        assert is_billable_route(ServiceBillingType.BillingRoute.DEBIT_POOL) is True
        assert is_billable_route(ServiceBillingType.BillingRoute.BILL_AMOUNT) is True

    def test_non_billable_is_not(self):
        assert is_billable_route(ServiceBillingType.BillingRoute.NON_BILLABLE) is False

    def test_accepts_a_billing_type_instance(self, warranty_billing, contract_billing):
        assert is_billable_route(warranty_billing) is False
        assert is_billable_route(contract_billing) is True


class TestComputeHourQuantities:
    """Section 4, quantities 2 to 4, in the one place they are derived."""

    def test_billable_hour_at_multiplier_one(self):
        quantities = compute_hour_quantities(
            raw_duration_minutes=60,
            multiplier=Decimal("1.00"),
            billing_route=ServiceBillingType.BillingRoute.DEBIT_POOL,
        )
        assert quantities == {
            "logged_hours": Decimal("1.0000"),
            "equivalent_hours": Decimal("1.0000"),
            "debited_hours": Decimal("1.0000"),
        }

    def test_acceptance_criterion_7(self):
        """1h at multiplier 1.5 persists logged 1.0 and equivalent 1.5."""
        quantities = compute_hour_quantities(
            raw_duration_minutes=60,
            multiplier=Decimal("1.50"),
            billing_route=ServiceBillingType.BillingRoute.DEBIT_POOL,
        )
        assert quantities["logged_hours"] == Decimal("1.0000")
        assert quantities["equivalent_hours"] == Decimal("1.5000")

    def test_rounding_happens_before_the_multiplier(self):
        """68 raw minutes become 1.25 logged, then 1.875 equivalent at 1.5.

        Order matters: multiplying first and rounding after would give a different
        number and would be a rounding of hours outside R2.
        """
        quantities = compute_hour_quantities(
            raw_duration_minutes=68,
            multiplier=Decimal("1.50"),
            billing_route=ServiceBillingType.BillingRoute.DEBIT_POOL,
        )
        assert quantities["logged_hours"] == Decimal("1.2500")
        assert quantities["equivalent_hours"] == Decimal("1.8750")

    def test_acceptance_criterion_12_non_billable(self):
        """Equivalent hours are still calculated; only the debit is zero."""
        quantities = compute_hour_quantities(
            raw_duration_minutes=75,
            multiplier=Decimal("1.50"),
            billing_route=ServiceBillingType.BillingRoute.NON_BILLABLE,
        )
        assert quantities["logged_hours"] == Decimal("1.2500")
        assert quantities["equivalent_hours"] == Decimal("1.8750")
        assert quantities["debited_hours"] == Decimal("0.0000")


class TestBuildSegments:
    """The adapter between the work log phase and the classification engine.

    This class was originally written against the *absent* engine and asserted that
    nothing was ever suggested. The calendar and windows phase delivered the engine, so
    the assertions moved to what this function is actually responsible for now:

    * discarding the times in duration mode, so the engine cannot split an entry whose
      timing is unknown (R9 and D13);
    * passing the workspace through, so classification happens at all;
    * degrading to one unclassified segment rather than failing when there is no
      workspace to resolve against.

    The classification itself -- the twelve reference cases, the boundaries, the
    priorities -- is tested against the engine in `test_service_calendar_engine.py`,
    where it belongs.
    """

    def test_duration_mode_produces_one_segment(self, workspace):
        segments = build_segments(
            worked_on=date(2026, 1, 6),
            raw_duration_minutes=180,
            entry_mode=ServiceLog.EntryMode.DURATION,
            workspace_id=workspace.id,
        )
        assert len(segments) == 1
        assert segments[0].raw_duration_minutes == 180
        assert segments[0].start_time is None
        assert segments[0].end_time is None

    def test_duration_mode_discards_times_even_if_passed(self, workspace):
        """R9: duration mode does not know when the work happened, by definition.

        This is the adapter's own rule, and the reason it was not inlined into the
        engine. Letting the times through would allow a split that D13 forbids.
        """
        segments = build_segments(
            worked_on=date(2026, 1, 5),
            raw_duration_minutes=60,
            entry_mode=ServiceLog.EntryMode.DURATION,
            start_time=time(17, 0),
            end_time=time(18, 0),
            workspace_id=workspace.id,
        )
        assert len(segments) == 1
        assert segments[0].start_time is None
        assert segments[0].end_time is None

    def test_interval_mode_passes_the_times_to_the_engine(self, workspace):
        segments = build_segments(
            worked_on=date(2026, 1, 5),
            raw_duration_minutes=180,
            entry_mode=ServiceLog.EntryMode.INTERVAL,
            start_time=time(17, 0),
            end_time=time(20, 0),
            workspace_id=workspace.id,
        )
        assert segments[0].start_time == time(17, 0)
        assert segments[-1].end_time == time(20, 0)

    def test_an_unconfigured_workspace_yields_one_unclassified_segment(self, workspace):
        """The workspace fixture has hour types but no windows.

        Which is a real state -- the coverage ratchet allows it -- and must produce a
        usable entry rather than an error.

        The reason is asserted, not just the null suggestion. Without it this test would
        pass whether or not an engine existed at all, which is precisely the failure mode
        that made the previous version of this class worthless.
        """
        from plane.utils.service_calendar import UNCLASSIFIED_REASON

        segments = build_segments(
            worked_on=date(2026, 1, 6),
            raw_duration_minutes=60,
            entry_mode=ServiceLog.EntryMode.INTERVAL,
            start_time=time(9, 0),
            end_time=time(10, 0),
            workspace_id=workspace.id,
        )
        assert len(segments) == 1
        assert segments[0].suggested_hour_type is None
        assert segments[0].reason == UNCLASSIFIED_REASON

    def test_no_workspace_degrades_instead_of_failing(self):
        """Called without a workspace, which no view does, but which must not raise."""
        segments = build_segments(
            worked_on=date(2026, 1, 6),
            raw_duration_minutes=60,
            entry_mode=ServiceLog.EntryMode.DURATION,
        )
        assert len(segments) == 1
        assert segments[0].suggested_hour_type is None
        assert segments[0].reason == ""

    def test_a_configured_workspace_does_classify(self, workspace):
        """The other half of the rewrite: with windows present, suggestions appear.

        The old version of this class asserted that a suggestion was never produced. That
        was true of an absent engine and is now false, which is the whole point.
        """
        from plane.db.models import ServiceBillingType as BillingType
        from plane.db.models import ServiceClassificationWindow, ServiceHourType
        from plane.utils.service_catalog_seed import seed_service_catalogs

        seed_service_catalogs(
            ServiceHourType, BillingType, workspace.id, window_model=ServiceClassificationWindow
        )

        # 2026-01-05 is a Monday; 10:00 to 11:00 is business hours.
        segments = build_segments(
            worked_on=date(2026, 1, 5),
            raw_duration_minutes=60,
            entry_mode=ServiceLog.EntryMode.INTERVAL,
            start_time=time(10, 0),
            end_time=time(11, 0),
            workspace_id=workspace.id,
        )

        assert segments[0].suggested_hour_type is not None
        assert segments[0].suggested_hour_type.name == "Horário comercial"
        assert segments[0].reason



class TestApplyMinimumBlockGuardrail:
    """Section 6 of the calendar and windows brief, the mandatory guardrail."""

    def _segment(self, minutes, start, end, hour_type=None, reason=""):
        return Segment(
            worked_on=date(2026, 1, 5),
            start_time=start,
            end_time=end,
            raw_duration_minutes=minutes,
            suggested_hour_type=hour_type,
            reason=reason,
        )

    def test_a_single_segment_passes_through(self):
        segments = [self._segment(60, time(9, 0), time(10, 0))]
        assert apply_minimum_block_guardrail(segments) == segments

    def test_a_split_of_a_block_or_more_is_left_alone(self):
        """17:50-18:10 is 20 raw minutes, so it stays split. 2b criterion 10."""
        segments = [
            self._segment(10, time(17, 50), time(18, 0)),
            self._segment(10, time(18, 0), time(18, 10)),
        ]
        assert len(apply_minimum_block_guardrail(segments)) == 2

    def test_a_split_under_a_block_collapses_to_one(self, commercial_hours, after_hours):
        """17:57-18:03 is 6 raw minutes. 2b criterion 11.

        Without this it would be 3 + 3 minutes, each floored to 15, billing 30
        minutes for 6 minutes of work.
        """
        segments = [
            self._segment(3, time(17, 57), time(18, 0), commercial_hours, "Comercial"),
            self._segment(3, time(18, 0), time(18, 3), after_hours, "Fora do expediente"),
        ]
        collapsed = apply_minimum_block_guardrail(segments)

        assert len(collapsed) == 1
        assert collapsed[0].raw_duration_minutes == 6

    def test_collapse_keeps_the_full_span(self, commercial_hours, after_hours):
        segments = [
            self._segment(3, time(17, 57), time(18, 0), commercial_hours),
            self._segment(3, time(18, 0), time(18, 3), after_hours),
        ]
        collapsed = apply_minimum_block_guardrail(segments)

        assert collapsed[0].start_time == time(17, 57)
        assert collapsed[0].end_time == time(18, 3)

    def test_collapse_uses_the_predominant_bands_hour_type(self, commercial_hours, after_hours):
        """"a faixa predominante" -- the longest stretch wins."""
        segments = [
            self._segment(2, time(17, 58), time(18, 0), commercial_hours, "Comercial"),
            self._segment(5, time(18, 0), time(18, 5), after_hours, "Fora do expediente"),
        ]
        collapsed = apply_minimum_block_guardrail(segments)

        assert collapsed[0].suggested_hour_type == after_hours
        assert collapsed[0].reason == "Fora do expediente"

    def test_collapsed_entry_still_bills_one_block(self, commercial_hours):
        """The whole point: 6 minutes bills 0.25h, not 0.5h."""
        segments = [
            self._segment(3, time(17, 57), time(18, 0), commercial_hours),
            self._segment(3, time(18, 0), time(18, 3), commercial_hours),
        ]
        collapsed = apply_minimum_block_guardrail(segments)
        quantities = compute_hour_quantities(
            raw_duration_minutes=collapsed[0].raw_duration_minutes,
            multiplier=Decimal("1.00"),
            billing_route=ServiceBillingType.BillingRoute.DEBIT_POOL,
        )
        assert quantities["logged_hours"] == Decimal("0.2500")


class TestResolveSegmentHourType:
    """R10's override rule."""

    def _segment(self, suggested=None):
        return Segment(
            worked_on=date(2026, 1, 5),
            start_time=None,
            end_time=None,
            raw_duration_minutes=60,
            suggested_hour_type=suggested,
        )

    def test_the_suggestion_is_used_when_there_is_no_override(self, commercial_hours):
        resolved, is_overridden = resolve_segment_hour_type(self._segment(commercial_hours))
        assert resolved == commercial_hours
        assert is_overridden is False

    def test_an_override_wins_and_is_flagged(self, commercial_hours, after_hours):
        """"Sobrescrita manual sempre permitida, e registrada na auditoria"."""
        resolved, is_overridden = resolve_segment_hour_type(
            self._segment(commercial_hours), override=after_hours
        )
        assert resolved == after_hours
        assert is_overridden is True

    def test_choosing_the_same_value_is_not_an_override(self, commercial_hours):
        """R10 flags a divergence, and picking the suggestion is not one."""
        resolved, is_overridden = resolve_segment_hour_type(
            self._segment(commercial_hours), override=commercial_hours
        )
        assert resolved == commercial_hours
        assert is_overridden is False

    def test_a_choice_with_no_suggestion_is_not_an_override(self, commercial_hours):
        """Today's normal path: no engine, so the technician simply chose."""
        resolved, is_overridden = resolve_segment_hour_type(self._segment(None), override=commercial_hours)
        assert resolved == commercial_hours
        assert is_overridden is False

    def test_neither_suggestion_nor_choice_is_an_error(self):
        with pytest.raises(ServiceLogValidationError) as excinfo:
            resolve_segment_hour_type(self._segment(None))
        assert excinfo.value.code == HOUR_TYPE_REQUIRED


class TestBuildBatchRows:
    """What gets created, without touching the database."""

    def _rows(self, issue, author, billing_type, hour_type, segments, **kwargs):
        return build_batch_rows(
            issue=issue,
            author=author,
            description="Fixed the printer",
            billing_type=billing_type,
            hour_type=hour_type,
            segments=segments,
            **kwargs,
        )

    def test_one_segment_makes_one_row_with_the_snapshots(
        self, issue, author, contract_billing, after_hours
    ):
        segments = build_segments(
            worked_on=date(2026, 1, 5),
            raw_duration_minutes=60,
            entry_mode=ServiceLog.EntryMode.DURATION,
        )
        rows = self._rows(issue, author, contract_billing, after_hours, segments)

        assert len(rows) == 1
        row = rows[0]
        assert row.raw_duration_minutes == 60
        assert row.logged_hours == Decimal("1.0000")
        assert row.equivalent_hours == Decimal("1.5000")
        assert row.debited_hours == Decimal("1.5000")
        # R4: the multiplier and the route are copied, not referenced.
        assert row.applied_multiplier == Decimal("1.50")
        assert row.applied_billing_route == ServiceBillingType.BillingRoute.DEBIT_POOL

    def test_rounding_is_applied_per_segment(self, issue, author, contract_billing, commercial_hours):
        """D17 and section 6 of the calendar brief: each segment rounds independently."""
        segments = [
            Segment(date(2026, 1, 5), time(17, 50), time(18, 0), 10),
            Segment(date(2026, 1, 5), time(18, 0), time(18, 10), 10),
        ]
        rows = self._rows(issue, author, contract_billing, commercial_hours, segments)

        assert len(rows) == 2
        # 10 raw minutes each, each floored up to a full block.
        assert [row.logged_hours for row in rows] == [Decimal("0.2500"), Decimal("0.2500")]

    def test_all_rows_of_a_batch_share_one_batch_id_and_are_indexed_in_order(
        self, issue, author, contract_billing, commercial_hours
    ):
        segments = [
            Segment(date(2026, 1, 5), time(17, 0), time(18, 0), 60),
            Segment(date(2026, 1, 5), time(18, 0), time(20, 0), 120),
        ]
        rows = self._rows(issue, author, contract_billing, commercial_hours, segments)

        assert len({row.batch_id for row in rows}) == 1
        assert [row.segment_index for row in rows] == [0, 1]

    def test_a_single_entry_is_a_batch_of_one(self, issue, author, contract_billing, commercial_hours):
        """``batch_id`` is always set, so no query needs a null branch."""
        segments = build_segments(
            worked_on=date(2026, 1, 5),
            raw_duration_minutes=60,
            entry_mode=ServiceLog.EntryMode.DURATION,
        )
        rows = self._rows(issue, author, contract_billing, commercial_hours, segments)
        assert rows[0].batch_id is not None

    def test_non_billable_rows_keep_equivalent_and_zero_the_debit(
        self, issue, author, warranty_billing, after_hours
    ):
        segments = build_segments(
            worked_on=date(2026, 1, 5),
            raw_duration_minutes=75,
            entry_mode=ServiceLog.EntryMode.DURATION,
        )
        rows = self._rows(issue, author, warranty_billing, after_hours, segments)

        assert rows[0].equivalent_hours == Decimal("1.8750")
        assert rows[0].debited_hours == Decimal("0.0000")
        assert rows[0].applied_billing_route == ServiceBillingType.BillingRoute.NON_BILLABLE

    def test_workspace_is_denormalised_from_the_project(
        self, issue, author, contract_billing, commercial_hours
    ):
        segments = build_segments(
            worked_on=date(2026, 1, 5),
            raw_duration_minutes=60,
            entry_mode=ServiceLog.EntryMode.DURATION,
        )
        rows = self._rows(issue, author, contract_billing, commercial_hours, segments)
        rows[0].save()
        assert rows[0].workspace_id == issue.project.workspace_id


class TestCreateAndDeleteBatch:
    """Persistence, and acceptance criterion 11."""

    def _create(self, issue, author, billing_type, hour_type, segments):
        # Entry mode is derived from the segments here: a segment that carries times
        # came from an interval, and the entry_mode check constraint requires the two
        # to agree. The API derives it from the request instead.
        entry_mode = (
            ServiceLog.EntryMode.INTERVAL if segments[0].start_time else ServiceLog.EntryMode.DURATION
        )
        return create_service_log_batch(
            build_batch_rows(
                issue=issue,
                author=author,
                description="Worked",
                billing_type=billing_type,
                hour_type=hour_type,
                segments=segments,
                entry_mode=entry_mode,
            )
        )

    def test_creates_every_segment(self, issue, author, contract_billing, commercial_hours):
        segments = [
            Segment(date(2026, 1, 5), time(17, 0), time(18, 0), 60),
            Segment(date(2026, 1, 5), time(18, 0), time(20, 0), 120),
        ]
        rows = self._create(issue, author, contract_billing, commercial_hours, segments)

        assert ServiceLog.objects.filter(issue=issue).count() == 2
        assert all(row.pk is not None for row in rows)

    def test_deleting_a_batch_removes_all_its_segments(
        self, issue, author, contract_billing, commercial_hours
    ):
        """Acceptance criterion 11, and section 3's transactional requirement."""
        segments = [
            Segment(date(2026, 1, 5), time(17, 0), time(18, 0), 60),
            Segment(date(2026, 1, 5), time(18, 0), time(20, 0), 120),
        ]
        rows = self._create(issue, author, contract_billing, commercial_hours, segments)

        removed = delete_service_log_batch(rows[0].batch_id)

        assert removed == 2
        assert ServiceLog.objects.filter(issue=issue).count() == 0

    def test_delete_is_soft_so_the_rows_survive_for_billing(
        self, issue, author, contract_billing, commercial_hours
    ):
        segments = build_segments(
            worked_on=date(2026, 1, 5), raw_duration_minutes=60, entry_mode=ServiceLog.EntryMode.DURATION
        )
        rows = self._create(issue, author, contract_billing, commercial_hours, segments)

        delete_service_log_batch(rows[0].batch_id)

        assert ServiceLog.objects.filter(issue=issue).count() == 0
        assert ServiceLog.all_objects.filter(issue=issue).count() == 1

    def test_deleting_one_batch_leaves_another_alone(
        self, issue, author, contract_billing, commercial_hours
    ):
        first = self._create(
            issue,
            author,
            contract_billing,
            commercial_hours,
            build_segments(
                worked_on=date(2026, 1, 5),
                raw_duration_minutes=60,
                entry_mode=ServiceLog.EntryMode.DURATION,
            ),
        )
        self._create(
            issue,
            author,
            contract_billing,
            commercial_hours,
            build_segments(
                worked_on=date(2026, 1, 6),
                raw_duration_minutes=30,
                entry_mode=ServiceLog.EntryMode.DURATION,
            ),
        )

        delete_service_log_batch(first[0].batch_id)

        assert ServiceLog.objects.filter(issue=issue).count() == 1

    def test_replacing_a_batch_keeps_its_identity_and_swaps_the_segments(
        self, issue, author, contract_billing, commercial_hours
    ):
        """An edit can change the segment count, so replace rather than update."""
        original = self._create(
            issue,
            author,
            contract_billing,
            commercial_hours,
            build_segments(
                worked_on=date(2026, 1, 5),
                raw_duration_minutes=60,
                entry_mode=ServiceLog.EntryMode.DURATION,
            ),
        )
        batch_id = original[0].batch_id

        replacement = build_batch_rows(
            issue=issue,
            author=author,
            description="Worked longer",
            billing_type=contract_billing,
            hour_type=commercial_hours,
            segments=[
                Segment(date(2026, 1, 5), time(17, 0), time(18, 0), 60),
                Segment(date(2026, 1, 5), time(18, 0), time(20, 0), 120),
            ],
            entry_mode=ServiceLog.EntryMode.INTERVAL,
            batch_id=batch_id,
        )
        replace_service_log_batch(batch_id=batch_id, rows=replacement)

        live = ServiceLog.objects.filter(batch_id=batch_id)
        assert live.count() == 2
        assert {row.segment_index for row in live} == {0, 1}


class TestModelConstraints:
    """The three check constraints, exercised through Django.

    These cannot be tested from raw SQL because the foreign keys trip first, and they
    are the reason the constraints exist at all: a rule held only in Python is a rule
    the next queryset ``update()`` bypasses.
    """

    def test_a_non_billable_row_cannot_debit_anything(self, issue, author, warranty_billing, after_hours):
        with pytest.raises(IntegrityError, match="service_log_debited_hours_follows_billing_route"):
            ServiceLogFactory(
                issue=issue,
                author=author,
                billing_type=warranty_billing,
                hour_type=after_hours,
                applied_billing_route=ServiceBillingType.BillingRoute.NON_BILLABLE,
                equivalent_hours=Decimal("1.5000"),
                debited_hours=Decimal("1.5000"),
            )

    def test_a_billable_row_must_debit_its_equivalent_hours(
        self, issue, author, contract_billing, after_hours
    ):
        """The other direction, which a one-sided constraint would have allowed."""
        with pytest.raises(IntegrityError, match="service_log_debited_hours_follows_billing_route"):
            ServiceLogFactory(
                issue=issue,
                author=author,
                billing_type=contract_billing,
                hour_type=after_hours,
                applied_billing_route=ServiceBillingType.BillingRoute.DEBIT_POOL,
                equivalent_hours=Decimal("1.5000"),
                debited_hours=Decimal("0.0000"),
            )

    def test_an_interval_entry_needs_both_times(self, issue, author, contract_billing, commercial_hours):
        with pytest.raises(IntegrityError, match="service_log_entry_mode_matches_times"):
            ServiceLogFactory(
                issue=issue,
                author=author,
                billing_type=contract_billing,
                hour_type=commercial_hours,
                entry_mode=ServiceLog.EntryMode.INTERVAL,
                start_time=time(14, 0),
                end_time=None,
            )

    def test_a_duration_entry_cannot_carry_times(self, issue, author, contract_billing, commercial_hours):
        with pytest.raises(IntegrityError, match="service_log_entry_mode_matches_times"):
            ServiceLogFactory(
                issue=issue,
                author=author,
                billing_type=contract_billing,
                hour_type=commercial_hours,
                entry_mode=ServiceLog.EntryMode.DURATION,
                start_time=time(14, 0),
                end_time=time(15, 0),
            )

    def test_raw_duration_must_be_positive(self, issue, author, contract_billing, commercial_hours):
        with pytest.raises(IntegrityError, match="service_log_raw_duration_is_positive"):
            ServiceLogFactory(
                issue=issue,
                author=author,
                billing_type=contract_billing,
                hour_type=commercial_hours,
                raw_duration_minutes=0,
            )

    def test_two_live_segments_cannot_share_an_index_in_one_batch(
        self, issue, author, contract_billing, commercial_hours
    ):
        batch_id = uuid.uuid4()
        ServiceLogFactory(
            issue=issue, author=author, billing_type=contract_billing, hour_type=commercial_hours,
            batch_id=batch_id, segment_index=0,
        )
        with pytest.raises(IntegrityError):
            ServiceLogFactory(
                issue=issue, author=author, billing_type=contract_billing, hour_type=commercial_hours,
                batch_id=batch_id, segment_index=0,
            )


class TestHistoricalSnapshots:
    """Rule R4, and Phase 2's acceptance criteria 3 and 5."""

    def test_changing_a_multiplier_does_not_move_an_existing_log(
        self, issue, author, contract_billing, after_hours
    ):
        """Phase 2, criterion 5. Acceptance criterion 15 of this phase."""
        rows = create_service_log_batch(
            build_batch_rows(
                issue=issue,
                author=author,
                description="Worked",
                billing_type=contract_billing,
                hour_type=after_hours,
                segments=build_segments(
                    worked_on=date(2026, 1, 5),
                    raw_duration_minutes=60,
                    entry_mode=ServiceLog.EntryMode.DURATION,
                ),
            )
        )
        assert rows[0].equivalent_hours == Decimal("1.5000")

        after_hours.multiplier = Decimal("1.80")
        after_hours.save()

        rows[0].refresh_from_db()
        assert rows[0].applied_multiplier == Decimal("1.50")
        assert rows[0].equivalent_hours == Decimal("1.5000")

    def test_a_deactivated_hour_type_still_resolves_its_name(
        self, issue, author, contract_billing, after_hours
    ):
        """Phase 2, criterion 3. This is what the DO_NOTHING foreign key buys."""
        log = ServiceLogFactory(
            issue=issue, author=author, billing_type=contract_billing, hour_type=after_hours
        )

        after_hours.is_active = False
        after_hours.save()

        log.refresh_from_db()
        assert log.hour_type.name == "Fora do expediente"

    def test_a_soft_deleted_hour_type_still_resolves_its_name(
        self, issue, author, contract_billing, after_hours
    ):
        """The stronger case: DO_NOTHING means the row survives its catalogue option.

        ``ServiceLog.objects`` cannot follow the forward descriptor to a soft deleted
        option -- the default manager filters it out -- so the serializer has to read
        through ``all_objects``, exactly as ``resolve_default_billing_type`` does.
        """
        log = ServiceLogFactory(
            issue=issue, author=author, billing_type=contract_billing, hour_type=after_hours
        )
        hour_type_id = after_hours.pk
        after_hours.delete()

        log.refresh_from_db()
        assert log.hour_type_id == hour_type_id
        from plane.db.models import ServiceHourType

        assert ServiceHourType.all_objects.get(pk=hour_type_id).name == "Fora do expediente"

    def test_two_non_billable_types_stay_distinguishable_after_a_catalogue_edit(
        self, issue, author, warranty_billing, courtesy_billing, commercial_hours
    ):
        """Why D20 requires snapshotting the route, not just the multiplier.

        Garantia and Cortesia share one route. Reports group by billing type, so both
        the foreign key and the snapshotted route have to survive independently --
        otherwise a historical report cannot tell a quality problem from a discount.
        """
        warranty_log = ServiceLogFactory(
            issue=issue, author=author, billing_type=warranty_billing, hour_type=commercial_hours,
            applied_billing_route=ServiceBillingType.BillingRoute.NON_BILLABLE,
            equivalent_hours=Decimal("1.0000"), debited_hours=Decimal("0.0000"),
        )
        courtesy_log = ServiceLogFactory(
            issue=issue, author=author, billing_type=courtesy_billing, hour_type=commercial_hours,
            applied_billing_route=ServiceBillingType.BillingRoute.NON_BILLABLE,
            equivalent_hours=Decimal("1.0000"), debited_hours=Decimal("0.0000"),
            batch_id=uuid.uuid4(),
        )

        # An admin later re-routes Cortesia to be billable.
        courtesy_billing.billing_route = ServiceBillingType.BillingRoute.BILL_AMOUNT
        courtesy_billing.save()

        warranty_log.refresh_from_db()
        courtesy_log.refresh_from_db()

        assert warranty_log.billing_type_id != courtesy_log.billing_type_id
        # Both logs still record that they were not charged at the time.
        assert courtesy_log.applied_billing_route == ServiceBillingType.BillingRoute.NON_BILLABLE
        assert courtesy_log.debited_hours == Decimal("0.0000")


class TestIssueServiceLogTotals:
    """Section 6's three totals."""

    def test_no_logs_gives_three_zeros(self, issue):
        totals = issue_service_log_totals(issue.pk)
        assert totals == {
            "logged_hours": Decimal("0.0000"),
            "equivalent_hours": Decimal("0.0000"),
            "debited_hours": Decimal("0.0000"),
        }

    def test_sums_the_persisted_columns(self, issue, author, contract_billing, commercial_hours, after_hours):
        ServiceLogFactory(
            issue=issue, author=author, billing_type=contract_billing, hour_type=commercial_hours,
            raw_duration_minutes=60, logged_hours=Decimal("1.0000"),
            equivalent_hours=Decimal("1.0000"), debited_hours=Decimal("1.0000"),
            applied_multiplier=Decimal("1.00"),
        )
        ServiceLogFactory(
            issue=issue, author=author, billing_type=contract_billing, hour_type=after_hours,
            raw_duration_minutes=60, logged_hours=Decimal("1.0000"),
            equivalent_hours=Decimal("1.5000"), debited_hours=Decimal("1.5000"),
            applied_multiplier=Decimal("1.50"), batch_id=uuid.uuid4(),
        )

        totals = issue_service_log_totals(issue.pk)
        assert totals["logged_hours"] == Decimal("2.0000")
        assert totals["equivalent_hours"] == Decimal("2.5000")
        assert totals["debited_hours"] == Decimal("2.5000")

    def test_non_billable_counts_in_logged_but_not_in_debited(
        self, issue, author, contract_billing, warranty_billing, commercial_hours
    ):
        """Acceptance criterion 12's aggregate half."""
        ServiceLogFactory(
            issue=issue, author=author, billing_type=contract_billing, hour_type=commercial_hours,
            logged_hours=Decimal("1.0000"), equivalent_hours=Decimal("1.0000"),
            debited_hours=Decimal("1.0000"),
        )
        ServiceLogFactory(
            issue=issue, author=author, billing_type=warranty_billing, hour_type=commercial_hours,
            applied_billing_route=ServiceBillingType.BillingRoute.NON_BILLABLE,
            logged_hours=Decimal("2.0000"), equivalent_hours=Decimal("2.0000"),
            debited_hours=Decimal("0.0000"), batch_id=uuid.uuid4(),
        )

        totals = issue_service_log_totals(issue.pk)
        assert totals["logged_hours"] == Decimal("3.0000")
        assert totals["equivalent_hours"] == Decimal("3.0000")
        assert totals["debited_hours"] == Decimal("1.0000")

    def test_soft_deleted_logs_are_excluded(self, issue, author, contract_billing, commercial_hours):
        log = ServiceLogFactory(
            issue=issue, author=author, billing_type=contract_billing, hour_type=commercial_hours
        )
        log.delete()
        assert issue_service_log_totals(issue.pk)["logged_hours"] == Decimal("0.0000")

    def test_totals_do_not_leak_between_work_items(
        self, project, author, contract_billing, commercial_hours
    ):
        first = IssueFactory(project=project)
        second = IssueFactory(project=project)
        ServiceLogFactory(
            issue=first, author=author, billing_type=contract_billing, hour_type=commercial_hours
        )

        assert issue_service_log_totals(first.pk)["logged_hours"] == Decimal("1.0000")
        assert issue_service_log_totals(second.pk)["logged_hours"] == Decimal("0.0000")


class TestAnnotateServiceLogTotals:
    """Section 7's aggregation."""

    def test_annotates_zeros_for_a_work_item_with_no_logs(self, issue):
        from plane.db.models import Issue

        annotated = annotate_service_log_totals(Issue.objects.filter(pk=issue.pk)).first()
        assert annotated.service_log_logged_hours == Decimal("0.0000")
        assert annotated.service_log_debited_hours == Decimal("0.0000")

    def test_annotates_the_three_totals(self, issue, author, contract_billing, after_hours):
        from plane.db.models import Issue

        ServiceLogFactory(
            issue=issue, author=author, billing_type=contract_billing, hour_type=after_hours,
            logged_hours=Decimal("1.0000"), equivalent_hours=Decimal("1.5000"),
            debited_hours=Decimal("1.5000"), applied_multiplier=Decimal("1.50"),
        )

        annotated = annotate_service_log_totals(Issue.objects.filter(pk=issue.pk)).first()
        assert annotated.service_log_logged_hours == Decimal("1.0000")
        assert annotated.service_log_equivalent_hours == Decimal("1.5000")
        assert annotated.service_log_debited_hours == Decimal("1.5000")

    def test_does_not_multiply_other_annotations(self, issue, author, contract_billing, commercial_hours):
        """A join would inflate a sibling Count by the number of work logs.

        This is why the totals use correlated subqueries. Plane's issue list already
        carries several annotations, and a join here would quietly corrupt them.
        """
        from django.db.models import Count

        from plane.db.models import Issue

        for _ in range(3):
            ServiceLogFactory(
                issue=issue, author=author, billing_type=contract_billing,
                hour_type=commercial_hours, batch_id=uuid.uuid4(),
            )

        annotated = annotate_service_log_totals(
            Issue.objects.filter(pk=issue.pk).annotate(link_count=Count("issue_link"))
        ).first()

        assert annotated.link_count == 0
        assert annotated.service_log_logged_hours == Decimal("3.0000")

    def test_totals_are_aggregable_by_project_and_period(
        self, project, author, contract_billing, commercial_hours
    ):
        """Section 7: sums by project and period without walking log by log."""
        from django.db.models import Sum

        issue = IssueFactory(project=project)
        ServiceLogFactory(
            issue=issue, author=author, billing_type=contract_billing, hour_type=commercial_hours,
            worked_on=date(2026, 1, 5),
        )
        ServiceLogFactory(
            issue=issue, author=author, billing_type=contract_billing, hour_type=commercial_hours,
            worked_on=date(2026, 2, 5), batch_id=uuid.uuid4(),
        )

        january = ServiceLog.objects.filter(
            project=project, worked_on__year=2026, worked_on__month=1
        ).aggregate(total=Sum("debited_hours"))

        assert january["total"] == Decimal("1.0000")


class TestValidation:
    """The guards that need the database."""

    def test_today_is_allowed(self, workspace):
        validate_worked_on(workspace_today(workspace), workspace)

    def test_a_past_date_is_allowed(self, workspace):
        """R7 exists so backdating works: a retroactive entry debits the old month."""
        validate_worked_on(workspace_today(workspace) - timedelta(days=90), workspace)

    def test_a_future_date_is_rejected(self, workspace):
        """Acceptance criterion 14."""
        with pytest.raises(ServiceLogValidationError) as excinfo:
            validate_worked_on(workspace_today(workspace) + timedelta(days=1), workspace)
        assert excinfo.value.code == WORKED_ON_CANNOT_BE_IN_THE_FUTURE

    def test_today_is_resolved_in_the_workspace_timezone(self, db):
        """Not the requesting user's, and not UTC.

        A workspace in São Paulo must not reject an entry dated "today" just because
        UTC has already rolled over.
        """
        sao_paulo = WorkspaceFactory(timezone="America/Sao_Paulo")
        kiritimati = WorkspaceFactory(timezone="Pacific/Kiritimati")

        assert workspace_today(kiritimati) >= workspace_today(sao_paulo)

    def test_an_unparseable_timezone_falls_back_to_utc(self, db):
        """A bad configuration value must not make logging impossible."""
        workspace = WorkspaceFactory()
        workspace.timezone = "Not/AZone"
        assert workspace_today(workspace) is not None

    def test_writes_are_refused_when_the_project_toggle_is_off(self, workspace):
        project = ProjectFactory(workspace=workspace, is_time_tracking_enabled=False)
        with pytest.raises(ServiceLogValidationError) as excinfo:
            validate_time_tracking_enabled(project)
        assert excinfo.value.code == TIME_TRACKING_DISABLED_FOR_PROJECT

    def test_writes_are_allowed_when_the_toggle_is_on(self, project):
        validate_time_tracking_enabled(project)

    def test_the_author_may_change_their_own_log(self, issue, author, contract_billing, commercial_hours):
        log = ServiceLogFactory(
            issue=issue, author=author, billing_type=contract_billing, hour_type=commercial_hours
        )
        validate_author_can_change(log, author)

    def test_another_user_may_not(self, issue, author, contract_billing, commercial_hours):
        """Section 6: "por ora, apenas o autor". Real permissions arrive in Phase 7."""
        log = ServiceLogFactory(
            issue=issue, author=author, billing_type=contract_billing, hour_type=commercial_hours
        )
        with pytest.raises(ServiceLogValidationError) as excinfo:
            validate_author_can_change(log, UserFactory())
        assert excinfo.value.code == ONLY_THE_AUTHOR_CAN_CHANGE_A_SERVICE_LOG


class TestServiceClientGuards:
    """The two criteria inherited from the client entity phase."""

    def test_project_has_service_logs_counts_soft_deleted_rows(
        self, project, issue, author, contract_billing, commercial_hours
    ):
        assert project_has_service_logs(project.pk) is False

        log = ServiceLogFactory(
            issue=issue, author=author, billing_type=contract_billing, hour_type=commercial_hours
        )
        assert project_has_service_logs(project.pk) is True

        log.delete()
        assert project_has_service_logs(project.pk) is True

    def test_unsetting_a_client_with_logs_is_rejected(
        self, workspace, author, contract_billing, commercial_hours
    ):
        """Phase 1, criterion 11."""
        client = ServiceClientFactory(workspace=workspace)
        project = ProjectFactory(workspace=workspace, service_client=client, is_time_tracking_enabled=True)
        issue = IssueFactory(project=project)
        ServiceLogFactory(
            issue=issue, author=author, billing_type=contract_billing, hour_type=commercial_hours
        )

        assert validate_service_client_change(project, None) == SERVICE_CLIENT_CANNOT_BE_UNSET_WITH_SERVICE_LOGS

    def test_changing_to_another_client_with_logs_is_rejected(
        self, workspace, author, contract_billing, commercial_hours
    ):
        """Not in the criterion's wording, but it breaks the same thing."""
        origin = ServiceClientFactory(workspace=workspace, name="Marubeni")
        destination = ServiceClientFactory(workspace=workspace, name="Terlogs")
        project = ProjectFactory(workspace=workspace, service_client=origin, is_time_tracking_enabled=True)
        issue = IssueFactory(project=project)
        ServiceLogFactory(
            issue=issue, author=author, billing_type=contract_billing, hour_type=commercial_hours
        )

        assert (
            validate_service_client_change(project, destination)
            == SERVICE_CLIENT_CANNOT_BE_CHANGED_WITH_SERVICE_LOGS
        )

    def test_unsetting_a_client_without_logs_is_allowed(self, workspace):
        client = ServiceClientFactory(workspace=workspace)
        project = ProjectFactory(workspace=workspace, service_client=client)
        assert validate_service_client_change(project, None) is None

    def test_attaching_a_client_to_a_project_with_logs_is_allowed(
        self, project, issue, author, contract_billing, commercial_hours, workspace
    ):
        """Adopting logs that had no client takes nothing away."""
        ServiceLogFactory(
            issue=issue, author=author, billing_type=contract_billing, hour_type=commercial_hours
        )
        assert project.service_client_id is None
        assert validate_service_client_change(project, ServiceClientFactory(workspace=workspace)) is None

    def test_setting_the_same_client_again_is_allowed(self, workspace, author, contract_billing, commercial_hours):
        client = ServiceClientFactory(workspace=workspace)
        project = ProjectFactory(workspace=workspace, service_client=client, is_time_tracking_enabled=True)
        issue = IssueFactory(project=project)
        ServiceLogFactory(
            issue=issue, author=author, billing_type=contract_billing, hour_type=commercial_hours
        )
        assert validate_service_client_change(project, client) is None


class TestServiceClientChangeAlert:
    """Phase 1's criterion 10 -- detection only; no move path exists to call it."""

    def test_alerts_when_the_destination_belongs_to_another_client(
        self, workspace, author, contract_billing, commercial_hours
    ):
        marubeni = ServiceClientFactory(workspace=workspace, name="Marubeni")
        terlogs = ServiceClientFactory(workspace=workspace, name="Terlogs")
        origin = ProjectFactory(workspace=workspace, name="Marubeni", service_client=marubeni)
        destination = ProjectFactory(workspace=workspace, name="Terlogs", service_client=terlogs)
        issue = IssueFactory(project=origin)
        ServiceLogFactory(
            issue=issue, author=author, billing_type=contract_billing, hour_type=commercial_hours
        )

        alert = service_client_change_alert(issue, destination)

        assert alert is not None
        assert alert["code"] == "SERVICE_CLIENT_WILL_CHANGE"
        assert alert["origin_service_client_id"] == str(marubeni.pk)
        assert alert["destination_service_client_id"] == str(terlogs.pk)
        assert alert["service_log_count"] == 1

    def test_no_alert_when_both_projects_share_a_client(
        self, workspace, author, contract_billing, commercial_hours
    ):
        """The Marubeni case: one client, two projects, no billing change."""
        client = ServiceClientFactory(workspace=workspace)
        origin = ProjectFactory(workspace=workspace, name="Support", service_client=client)
        destination = ProjectFactory(workspace=workspace, name="Infra", service_client=client)
        issue = IssueFactory(project=origin)
        ServiceLogFactory(
            issue=issue, author=author, billing_type=contract_billing, hour_type=commercial_hours
        )

        assert service_client_change_alert(issue, destination) is None

    def test_no_alert_when_the_work_item_has_no_logs(self, workspace):
        marubeni = ServiceClientFactory(workspace=workspace, name="Marubeni")
        terlogs = ServiceClientFactory(workspace=workspace, name="Terlogs")
        origin = ProjectFactory(workspace=workspace, name="Marubeni", service_client=marubeni)
        destination = ProjectFactory(workspace=workspace, name="Terlogs", service_client=terlogs)

        assert service_client_change_alert(IssueFactory(project=origin), destination) is None
