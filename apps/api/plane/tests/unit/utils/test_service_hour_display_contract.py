# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""The guard on D68: one renderer for hours on screen, and a twin for every hour field.

Written because this rule kept being broken. ``/preview`` shipped no ``_display`` twins at
all, the alert panel printed ``-2.0000``, the billing report concatenated a literal "h"
onto ``1.2500`` and the work log form printed ``1.2500`` -- four rounds of the same defect,
each one found by a user reading a screen rather than by the suite. Until this file the
rule lived in code comments and in whether a reviewer happened to remember it.

Two guards, both cheap and both structural:

1. no module that builds a payload for a screen may name ``format_hours``, the decimal
   renderer. The export schema is the single deliberate exception and is named as such;
2. any payload asserted through :func:`hour_fields_missing_a_rendered_twin` fails when an
   hour field arrives without its rendered sibling. The payload tests that use it live
   beside the fixtures they need (``test_service_billing.py``, ``test_service_pool.py``,
   ``test_service_pool_alerts.py``, ``test_service_allowance.py``,
   ``test_service_reports.py``); the self-tests below are what keep the walker itself
   honest, because a guard that cannot fail guards nothing.
"""

# Third party imports
import pytest

# Module imports
from plane.tests.hour_display import (
    DECIMAL_HOUR_RENDERER_ALLOWED_IN,
    _binds_the_decimal_renderer,
    hour_fields_missing_a_rendered_twin,
    modules_rendering_hours_as_decimals,
)

pytestmark = pytest.mark.unit


class TestOnlyTheExporterRendersADecimalHour:
    """D68: ``format_hours_human`` is the renderer for screens, ``format_hours`` for files."""

    def test_no_serializer_view_or_domain_module_names_format_hours(self):
        """The whole point of the rule, asserted as a fact about the tree.

        A new serializer, view, portal projection, allowance, pool, billing or reports
        module that reaches for ``format_hours`` fails here instead of shipping "32,25h" to
        a phone and waiting for the user to report it.
        """
        offenders = modules_rendering_hours_as_decimals()

        assert offenders == [], (
            "these modules build payloads for screens and must render hours with "
            f"format_hours_human, not format_hours: {offenders}"
        )

    def test_the_exception_is_the_export_schema_and_it_is_still_taken(self):
        """The exception must stay visible AND stay used.

        If the export ever moves to the human renderer, this test fails and the allow-list
        shrinks with it -- rather than leaving a permanent hole nobody remembers opening.
        """
        assert DECIMAL_HOUR_RENDERER_ALLOWED_IN == {
            "plane/utils/service_log_time.py",
            "plane/utils/exporters/schemas/service_log.py",
        }

        from plane.utils.exporters.schemas import service_log as export_schema

        # The binding itself is the assertion: an AttributeError here means the export
        # stopped using the decimal renderer and the allow-list should shrink with it.
        assert export_schema.format_hours.__name__ == "format_hours"

    def test_the_scan_reads_code_and_not_prose(self):
        """A guard that cannot fail guards nothing, and one that fires on a docstring gets
        deleted by the first person it annoys. Both halves asserted."""
        assert _binds_the_decimal_renderer("from plane.utils.service_log_time import format_hours")
        assert _binds_the_decimal_renderer("value = format_hours(hours)")
        assert _binds_the_decimal_renderer("value = service_log_time.format_hours(hours)")

        assert not _binds_the_decimal_renderer('"""Rendered with format_hours elsewhere."""')
        assert not _binds_the_decimal_renderer("# format_hours is the export renderer")
        assert not _binds_the_decimal_renderer(
            "from plane.utils.service_log_time import format_hours_human"
        )


class TestEveryHourFieldShipsARenderedTwin:
    """The walker the payload tests assert with. Self-tested, so it can actually fail."""

    def test_a_payload_with_both_readings_passes(self):
        assert (
            hour_fields_missing_a_rendered_twin(
                {"logged_hours": "1.2500", "logged_hours_display": "1h 15min"}
            )
            == []
        )

    def test_a_bare_hour_field_is_reported_with_its_path(self):
        assert hour_fields_missing_a_rendered_twin({"balance_hours": "-2.0000"}) == [
            "payload.balance_hours"
        ]

    def test_a_nested_list_entry_is_reached(self):
        """The three offenders so far were nested: pendency lists and alert entries."""
        payload = {
            "clients": [
                {
                    "registration_pendencies": [{"hours": "1.0000", "entries": 1}],
                }
            ]
        }

        assert hour_fields_missing_a_rendered_twin(payload) == [
            "payload.clients[0].registration_pendencies[0].hours"
        ]

    def test_the_bare_key_hours_counts_too(self):
        """``hours`` and ``*_hours`` are both hour fields; the consolidation uses the first."""
        assert hour_fields_missing_a_rendered_twin({"hours": "1.0000"}) == ["payload.hours"]
        assert (
            hour_fields_missing_a_rendered_twin({"hours": "1.0000", "hours_display": "1h"}) == []
        )

    def test_skip_keys_excuses_a_subtree_and_nothing_else(self):
        """The one documented exception: an alert's justification detail, which no screen
        reads and which `TServiceAlert` types as `unknown`. It must not excuse the level
        above it."""
        payload = {
            "balance_hours": "-3.0000",
            "alerts": [{"code": "PERIOD_NEGATIVE_BALANCE", "granted_hours": "30.0000"}],
        }

        assert hour_fields_missing_a_rendered_twin(payload, skip_keys={"alerts"}) == [
            "payload.balance_hours"
        ]

    def test_a_field_that_is_not_an_hour_is_left_alone(self):
        """``amount`` has its own twin rule and its own tests; this walker is about hours."""
        assert hour_fields_missing_a_rendered_twin({"amount": "200.00", "entries": 1}) == []
