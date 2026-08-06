# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Unit tests for the service client work item filter.

The client of a work item is derived from its project, never stored on the work
item, so this filter must always emit a lookup that traverses `project`. A test
that asserted only "some filter was produced" would pass even if the lookup
pointed at a column on the work item, which is exactly the mistake this design
forbids.
"""

from uuid import UUID

import pytest

from plane.utils.grouper import issue_on_results
from plane.utils.issue_filters import issue_filters
from plane.utils.order_queryset import ISSUE_GROUP_BY_ALLOWLIST

CLIENT_A = "11111111-1111-1111-1111-111111111111"
CLIENT_B = "22222222-2222-2222-2222-222222222222"

# The GET path coerces ids to UUID objects via filter_valid_uuids, matching how
# the sibling project filter behaves.
CLIENT_A_UUID = UUID(CLIENT_A)
CLIENT_B_UUID = UUID(CLIENT_B)

GROUP_BY_KEY = "project__service_client_id"


@pytest.mark.unit
class TestServiceClientFilter:
    def test_get_single_client(self):
        filters = issue_filters({"service_client": CLIENT_A}, "GET")
        assert filters == {"project__service_client_id__in": [CLIENT_A_UUID]}

    def test_get_multiple_clients(self):
        filters = issue_filters({"service_client": f"{CLIENT_A},{CLIENT_B}"}, "GET")
        assert filters == {"project__service_client_id__in": [CLIENT_A_UUID, CLIENT_B_UUID]}

    def test_none_selects_internal_work(self):
        """"None" must mean "projects with no client", not "no filter"."""
        filters = issue_filters({"service_client": "None"}, "GET")
        assert filters == {"project__service_client_id__isnull": True}

    def test_none_combined_with_ids(self):
        filters = issue_filters({"service_client": f"None,{CLIENT_A}"}, "GET")
        assert filters == {
            "project__service_client_id__isnull": True,
            "project__service_client_id__in": [CLIENT_A_UUID],
        }

    def test_post_receives_a_list(self):
        """Saved views send filters as a POST body, where the value is already a list."""
        filters = issue_filters({"service_client": [CLIENT_A]}, "POST")
        assert filters == {"project__service_client_id__in": [CLIENT_A]}

    @pytest.mark.parametrize("value", ["not-a-uuid", "", "null", "1234"])
    def test_invalid_values_produce_no_filter(self, value):
        """A malformed id must be dropped, never passed to the ORM."""
        assert issue_filters({"service_client": value}, "GET") == {}

    def test_prefix_is_honoured(self):
        """Intake reuses this dict against a queryset rooted on IntakeIssue."""
        filters = issue_filters({"service_client": CLIENT_A}, "GET", prefix="issue__")
        assert filters == {"issue__project__service_client_id__in": [CLIENT_A_UUID]}

    def test_absent_key_produces_no_filter(self):
        assert issue_filters({}, "GET") == {}

    def test_lookup_traverses_the_project(self):
        """Guards the core design decision: the client is derived, never a column
        on the work item. If this ever emits `service_client_id__in`, a client
        field has been added to the work item and D19 has been broken."""
        filters = issue_filters({"service_client": CLIENT_A}, "GET")
        for lookup in filters:
            assert lookup.startswith("project__service_client_id")


@pytest.mark.unit
class TestServiceClientGroupBy:
    def test_group_by_field_is_allowlisted(self):
        """Without this, BasePaginator.paginate rejects the request with 400."""
        assert GROUP_BY_KEY in ISSUE_GROUP_BY_ALLOWLIST

    def test_group_field_is_projected_by_issue_on_results(self):
        """Regression guard for a silent failure mode.

        BasePaginator.paginate applies on_results (issue_on_results) *before*
        process_results, and the grouped paginators then read
        result[group_by_field_name] from those dicts. If the column is not in the
        .values() projection nothing raises: every work item lands in the "None"
        bucket instead. Assert the projection includes it.
        """
        captured = {}

        class QuerySetSpy:
            def values(self, *fields):
                captured["fields"] = fields
                return []

        issue_on_results(issues=QuerySetSpy(), group_by=GROUP_BY_KEY, sub_group_by=None)

        assert GROUP_BY_KEY in captured["fields"]
