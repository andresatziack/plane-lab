# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Helpers that enforce D68: every hour a screen receives ships a rendered twin.

Not a test module -- pytest collects ``test_*.py`` only -- but the two functions here are
what the guard tests and the payload tests assert with, so the rule lives in one place
instead of in a reviewer's memory. Four rounds of the same defect (a decimal reaching a
screen) were found by users rather than by the suite; these are the pieces that make the
fifth omission a red test.

- :func:`hour_fields_missing_a_rendered_twin` walks a payload and reports every hour key
  that has no ``_display`` sibling.
- :func:`modules_rendering_hours_as_decimals` scans the product tree for ``format_hours``,
  which is the export-only renderer, and :func:`scanned_modules` says which files that is.
"""

# Python imports
import ast
from pathlib import Path

#: The decimal renderer ("1,25h"). Never ``format_hours_human``, which is a different name.
DECIMAL_HOUR_RENDERER = "format_hours"

#: The two files that may legitimately name ``format_hours``.
#:
#: ``service_log_time.py`` defines it; the export schema is the one deliberate caller,
#: because a CSV row is read by a spreadsheet and an invoicing system, where a summable
#: decimal is the useful form. Everything else renders for a screen and must use
#: ``format_hours_human``. Widening this set is a decision, not a refactor.
DECIMAL_HOUR_RENDERER_ALLOWED_IN = frozenset(
    {
        "plane/utils/service_log_time.py",
        "plane/utils/exporters/schemas/service_log.py",
    }
)

#: Where a payload destined for a screen is built: the whole product tree.
#:
#: This used to be ``("plane/app", "plane/utils")`` -- the API boundary and the domain layer --
#: which is narrower than the rule it defends. ``plane/bgtasks/issue_activities_task.py`` built
#: the work log activity row's description, an hour quantity destined for the activity feed, and
#: sat outside the scan; ``plane/space`` (the public deploy) and ``plane/api`` (the external
#: token API) were outside it too. A guard whose reach is smaller than the rule teaches the next
#: reader the wrong boundary, so the scan now walks everything under ``plane`` that is not
#: excluded below.
SCREEN_PAYLOAD_ROOTS = ("plane",)

#: Directory names the scan does not walk, and why each one is not a hole in the rule.
#:
#: - ``tests``: the suite names ``format_hours`` on purpose -- this module does, and
#:   ``test_service_log_time.py`` is the decimal renderer's own test. A guard that fired on its
#:   own tests would be turned off within a week.
#: - ``migrations``: frozen history. A migration renders nothing to a screen, and rewriting one
#:   to satisfy a display rule is worse than the rule being unenforced there.
SCAN_EXCLUDED_DIRECTORIES = frozenset({"tests", "migrations", "__pycache__"})


def hour_fields_missing_a_rendered_twin(payload, *, path="payload", skip_keys=()):
    """Return every hour key in ``payload`` that ships no ``<key>_display`` sibling.

    An hour key is ``hours`` itself or anything ending in ``_hours``: ``logged_hours``,
    ``balance_hours``, ``overage_hours``, ``average_equivalent_hours_per_issue``. Walks
    nested dicts and lists, because the offenders so far were nested -- the consolidation's
    pendency lists and the alert panel's entries.

    ``skip_keys`` names dict keys whose values are not walked. It exists for one shape:
    an alert's ``alerts`` list carries the numbers that *justified* the code (a projection,
    a threshold, a discarded remainder) behind ``TServiceAlert``'s ``[detail: string]:
    unknown`` index signature. Those are diagnostics that no screen reads, and a caller that
    starts rendering one is adding a display, at which point D68 applies to it. Every use of
    ``skip_keys`` should say which fields it is excusing and why.

    Returns a list of dotted paths, so a failure names the field instead of only saying a
    dictionary did not match.
    """
    missing = []

    if isinstance(payload, dict):
        for key, value in payload.items():
            if isinstance(key, str) and key in skip_keys:
                continue
            if isinstance(key, str) and (key == "hours" or key.endswith("_hours")):
                if f"{key}_display" not in payload:
                    missing.append(f"{path}.{key}")
            missing.extend(
                hour_fields_missing_a_rendered_twin(
                    value, path=f"{path}.{key}", skip_keys=skip_keys
                )
            )
    elif isinstance(payload, (list, tuple)):
        for index, item in enumerate(payload):
            missing.extend(
                hour_fields_missing_a_rendered_twin(
                    item, path=f"{path}[{index}]", skip_keys=skip_keys
                )
            )

    return sorted(missing)


def _binds_the_decimal_renderer(source):
    """True when the module imports or calls ``format_hours``.

    Parsed rather than grepped: half a dozen docstrings in this codebase discuss
    ``format_hours`` by name, and a rule that fires on prose gets deleted by the next person
    who trips over it. The AST sees names, not comments.
    """
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if any(alias.name == DECIMAL_HOUR_RENDERER for alias in node.names):
                return True
        elif isinstance(node, ast.Name) and node.id == DECIMAL_HOUR_RENDERER:
            return True
        elif isinstance(node, ast.Attribute) and node.attr == DECIMAL_HOUR_RENDERER:
            return True

    return False


def scanned_modules():
    """Return the repo-relative paths the decimal-renderer scan reads.

    Separate from :func:`modules_rendering_hours_as_decimals` so a test can assert what the
    guard's reach *is* -- the previous version's reach was two directories, and nothing said so.
    """
    api_root = Path(__file__).resolve().parents[2]
    modules = []

    for root in SCREEN_PAYLOAD_ROOTS:
        for source in sorted((api_root / root).rglob("*.py")):
            relative = source.relative_to(api_root)
            if SCAN_EXCLUDED_DIRECTORIES.intersection(relative.parts):
                continue
            modules.append(relative.as_posix())

    return modules


def modules_rendering_hours_as_decimals():
    """Return the repo-relative paths under the API that use ``format_hours``.

    Excludes :data:`DECIMAL_HOUR_RENDERER_ALLOWED_IN`, so a non-empty answer is a module
    that renders a decimal hour for a screen.

    **What this cannot see.** The scan matches the *name* ``format_hours``, so a decimal hour
    assembled by hand walks straight past it: ``f"{total_logged} h"`` renders ``"1.5000 h"`` and
    binds nothing. That exact line existed in ``plane/bgtasks/issue_activities_task.py`` and is
    now ``format_hours_human``, but the blind spot is structural, not fixed. A rule that fired on
    every f-string containing an hour-ish variable would be a rule of guesses; the honest cover
    for the inline case is :func:`hour_fields_missing_a_rendered_twin` on the payload plus a
    reviewer reading the diff.
    """
    api_root = Path(__file__).resolve().parents[2]
    offenders = []

    for relative in scanned_modules():
        if relative in DECIMAL_HOUR_RENDERER_ALLOWED_IN:
            continue
        if _binds_the_decimal_renderer((api_root / relative).read_text(encoding="utf-8")):
            offenders.append(relative)

    return offenders
