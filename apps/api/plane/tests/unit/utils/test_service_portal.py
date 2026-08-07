# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""The client's write allowlist, proved at the domain layer. D59, D60.

Every absence assertion here runs against the module's own constants rather than a
restated list, for the reason the constants' docstrings give: a test that restates the
allowlist keeps passing after somebody widens it.

The HTTP half of these guarantees lives in
``plane/tests/contract/app/test_issue_client_portal_app.py``. Both halves exist on
purpose -- this one pins the rule, that one pins that the view actually calls it.
"""

import pytest

from plane.db.models.state import StateGroup
from plane.utils.service_log import ServiceLogValidationError
from plane.utils.service_portal import (
    CLIENT_ALLOWED_STATE_GROUPS,
    CLIENT_WRITABLE_ISSUE_FIELDS,
    STATE_GROUP_NOT_PERMITTED_FOR_CLIENT,
    filter_issue_payload_for_client,
    scope_filterset_to_client,
    validate_client_state_transition,
)
from plane.utils.service_reports_filters import ServiceLogFilterSet

pytestmark = pytest.mark.unit


class _Issue:
    """The two attributes the filter reads, without a database."""

    def __init__(self, priority="none", state_id="11111111-1111-1111-1111-111111111111"):
        self.priority = priority
        self.state_id = state_id


class TestD59TheAllowlistIsTwoFieldsAndNothingElse:
    def test_the_allowlist_is_exactly_priority_and_state(self):
        assert CLIENT_WRITABLE_ISSUE_FIELDS == {"priority", "state_id"}

    def test_state_is_not_a_writable_key_under_its_model_name(self):
        # `IssueCreateSerializer` declares `state_id` with `source="state"`, so a payload
        # key of `state` writes nothing. Allowing it here would look like it worked.
        assert "state" not in CLIENT_WRITABLE_ISSUE_FIELDS

    def test_the_filtered_payload_carries_only_allowlisted_keys(self):
        filtered = filter_issue_payload_for_client({"priority": "urgent"}, _Issue())

        assert set(filtered) == CLIENT_WRITABLE_ISSUE_FIELDS

    @pytest.mark.parametrize(
        "field",
        [
            "name",
            "description_html",
            "assignee_ids",
            "label_ids",
            "start_date",
            "target_date",
            "estimate_point",
            "parent",
            "type_id",
            "project",
            "created_by",
            "module_ids",
            "cycle_id",
        ],
    )
    def test_every_field_the_client_must_not_reach_is_dropped(self, field):
        """Criterion 19. The five the brief names, plus the ones a payload could carry.

        Parametrized rather than looped so a failure names the field that leaked.
        """
        filtered = filter_issue_payload_for_client({field: "anything at all"}, _Issue())

        assert field not in filtered

    def test_the_client_does_get_the_two_fields_through(self):
        """The positive control. An allowlist that drops everything is not an allowlist."""
        filtered = filter_issue_payload_for_client(
            {"priority": "urgent", "state_id": "22222222-2222-2222-2222-222222222222"},
            _Issue(),
        )

        assert filtered["priority"] == "urgent"
        assert filtered["state_id"] == "22222222-2222-2222-2222-222222222222"

    def test_an_untouched_field_defaults_to_the_current_value(self):
        issue = _Issue(priority="high", state_id="33333333-3333-3333-3333-333333333333")

        filtered = filter_issue_payload_for_client({"priority": "low"}, issue)

        assert filtered["priority"] == "low"
        assert filtered["state_id"] == "33333333-3333-3333-3333-333333333333"

    def test_a_forbidden_field_does_not_displace_an_allowed_one(self):
        """The payload shape that would matter in an attack: both at once."""
        issue = _Issue(priority="none")

        filtered = filter_issue_payload_for_client(
            {"priority": "urgent", "assignee_ids": ["some-user-id"], "name": "hijacked"},
            issue,
        )

        assert filtered["priority"] == "urgent"
        assert "assignee_ids" not in filtered
        assert "name" not in filtered

    def test_an_issue_with_no_state_yields_none_rather_than_the_string_none(self):
        filtered = filter_issue_payload_for_client({}, _Issue(state_id=None))

        assert filtered["state_id"] is None


class TestD60TheStateGroupsAreFourAndTriageAndBacklogAreRefused:
    def test_the_permitted_groups_are_the_two_closing_and_the_two_reopening_ones(self):
        assert CLIENT_ALLOWED_STATE_GROUPS == {
            StateGroup.COMPLETED.value,
            StateGroup.UNSTARTED.value,
            StateGroup.STARTED.value,
            StateGroup.CANCELLED.value,
        }

    def test_triage_is_refused(self):
        assert StateGroup.TRIAGE.value not in CLIENT_ALLOWED_STATE_GROUPS

    def test_backlog_is_refused(self):
        """Not an omission. The client has closing and priority already; backlog is
        internal planning, and a work item a client parked there is neither closed nor
        queued."""
        assert StateGroup.BACKLOG.value not in CLIENT_ALLOWED_STATE_GROUPS

    def test_the_two_refused_groups_are_the_only_ones_refused(self):
        """Pins the complement, so adding a seventh group to `StateGroup` fails here
        rather than silently becoming client-writable or silently becoming refused."""
        all_groups = {group.value for group in StateGroup}

        assert all_groups - CLIENT_ALLOWED_STATE_GROUPS == {
            StateGroup.TRIAGE.value,
            StateGroup.BACKLOG.value,
        }

    def test_an_absent_state_is_not_an_error(self):
        # This is how "the client did not touch the state" arrives after filtering.
        validate_client_state_transition(None, project_id="p")


class TestD63AnEmptyScopeIsRefusedRatherThanSilentlyWidened:
    def test_scoping_to_no_projects_raises_instead_of_selecting_everything(self):
        """The mine: `narrow(project_ids=())` produces a query with no project filter,
        turning an empty scope into total access."""
        with pytest.raises(ValueError, match="empty project set"):
            scope_filterset_to_client(ServiceLogFilterSet(), ())

    def test_scoping_to_projects_narrows_to_exactly_those(self):
        project_id = "44444444-4444-4444-4444-444444444444"

        scoped = scope_filterset_to_client(ServiceLogFilterSet(), (project_id,))

        assert scoped.project_ids == (project_id,)

    def test_the_scope_replaces_anything_the_caller_asked_for(self):
        """`narrow()` is `dataclasses.replace`, so the scope must be applied last. This
        test is what fails if a future caller reverses the order."""
        mine = "44444444-4444-4444-4444-444444444444"
        someone_elses = "55555555-5555-5555-5555-555555555555"

        requested = ServiceLogFilterSet(project_ids=(someone_elses,))
        scoped = scope_filterset_to_client(requested, (mine,))

        assert scoped.project_ids == (mine,)
        assert someone_elses not in scoped.project_ids


class TestTheReasonCodeIsNamedAndDistinct:
    def test_the_code_is_upper_snake(self):
        assert STATE_GROUP_NOT_PERMITTED_FOR_CLIENT == "STATE_GROUP_NOT_PERMITTED_FOR_CLIENT"

    def test_the_error_carries_the_code(self):
        error = ServiceLogValidationError(STATE_GROUP_NOT_PERMITTED_FOR_CLIENT)

        assert error.code == STATE_GROUP_NOT_PERMITTED_FOR_CLIENT



class TestD62TheTwoFormsOfTheVisibilityRuleAgree:
    """``client_may_reach_issue`` answers about an instance in memory,
    ``client_visible_issues_q`` filters rows in the database. They are separate mechanisms,
    so the risk worth guarding against is that they stop agreeing about the same facts."""

    def test_the_queryset_form_covers_both_narrowing_clauses(self):
        """Structural, without a database: the Q must mention the creator and the requester
        and nothing else. The project-wide clause belongs to the caller's ``if``."""
        from plane.utils.service_portal import client_visible_issues_q

        class _User:
            id = "11111111-1111-1111-1111-111111111111"
            pk = id

        rendered = str(client_visible_issues_q(_User()))

        assert "created_by" in rendered
        assert "service_requester__requester" in rendered
        # An OR, not an AND: either clause alone must be enough to see the work item.
        assert "OR" in rendered

    def test_the_queryset_form_does_not_test_the_project_flag(self):
        """If it did, callers would apply the flag twice -- once in the ``if`` and once in
        the filter -- and the second one would silently win."""
        from plane.utils.service_portal import client_visible_issues_q

        class _User:
            id = "11111111-1111-1111-1111-111111111111"
            pk = id

        assert "guest_view_all_features" not in str(client_visible_issues_q(_User()))
