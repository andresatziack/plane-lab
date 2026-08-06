# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Initial contents of the work log catalogues.

Deliberately free of any model import. The data migration that back-fills existing
workspaces needs these values, and a migration must operate on the historical
models it gets from ``apps.get_model`` rather than on the live ones. Every function
here therefore takes the model classes as arguments, which lets the same code serve
the migration, the management command and the workspace creation hook.

The names are Portuguese on purpose. These rows are business data for a Brazilian
operation: an admin reads and edits them, and a work log has to keep displaying the
name it was recorded against. They are not interface strings and are not localised.
"""

# Python imports
from decimal import Decimal

# Display order. Matches ServiceCatalogBaseModel.SEQUENCE_STEP so that an option
# added by hand afterwards lands after the seeded ones.
SEQUENCE_STEP = 15000

# Multipliers come straight from rule R10 of the master context. Saturday shares
# "Fora do expediente" with weekday nights, and Sunday sits with holidays at 2.0 --
# which is why classification cannot be derived from "working day / non working
# day" and needs the window model the next phase introduces.
HOUR_TYPE_SEED = (
    {
        "name": "Horário comercial",
        "description": "Segunda a sexta, das 08:00 às 18:00.",
        "multiplier": "1.00",
        "color": "#46A758",
        "priority": 30,
        "is_default": True,
    },
    {
        "name": "Fora do expediente",
        "description": "Segunda a sexta das 18:00 às 08:00, e sábado o dia inteiro.",
        "multiplier": "1.50",
        "color": "#F59E0B",
        "priority": 20,
        "is_default": False,
    },
    {
        "name": "Domingos e feriados",
        "description": "Domingo e feriado, o dia inteiro.",
        "multiplier": "2.00",
        "color": "#E5484D",
        "priority": 10,
        "is_default": False,
    },
)

# Minutes since midnight, matching how ServiceClassificationWindow stores its borders.
# 1440 is 24:00.
_DAY_START = 0
_DAY_END = 1440
_BUSINESS_START = 8 * 60  # 08:00
_BUSINESS_END = 18 * 60  # 18:00

WEEKDAY_SCOPES_SEED = ("monday", "tuesday", "wednesday", "thursday", "friday")

# The classification windows that express rule R10, keyed by hour type name.
#
# Read together with the priorities above, these produce exactly R10's table:
#
#   * a holiday beats the weekday window covering the same instant, because 10 < 30
#   * Saturday is 1.5 and Sunday is 2.0, which no "working day / non working day"
#     derivation could express -- it is why this model exists
#   * "Fora do expediente" on a weekday is ONE continuous window crossing midnight,
#     (1080, 480), not two. That is what stops a night shift being split at 00:00
#
# Together they cover 100% of the week, which the coverage check depends on: a seeded
# workspace starts complete, and the ratchet then refuses to let it become incomplete.
CLASSIFICATION_WINDOW_SEED = {
    "Domingos e feriados": (
        {"day_scope": "holiday", "start_minute": _DAY_START, "end_minute": _DAY_END},
        {"day_scope": "sunday", "start_minute": _DAY_START, "end_minute": _DAY_END},
    ),
    "Fora do expediente": (
        {"day_scope": "saturday", "start_minute": _DAY_START, "end_minute": _DAY_END},
        # 18:00 to 08:00 of the following day, as one range per weekday.
        *tuple(
            {"day_scope": scope, "start_minute": _BUSINESS_END, "end_minute": _BUSINESS_START}
            for scope in WEEKDAY_SCOPES_SEED
        ),
    ),
    "Horário comercial": tuple(
        {"day_scope": scope, "start_minute": _BUSINESS_START, "end_minute": _BUSINESS_END}
        for scope in WEEKDAY_SCOPES_SEED
    ),
}

# Garantia and Cortesia share the non_billable route but stay separate rows: both
# mean "not charged", yet garantia is rework already paid for (a delivery quality
# signal) and cortesia is a discount that was chosen (a commercial decision).
# Reports group by billing type, so the distinction survives without an extra
# column. The descriptions exist so whoever fills the form knows which to pick.
BILLING_TYPE_SEED = (
    {
        "name": "Contrato",
        "description": "Debita do pool de horas contratado do cliente.",
        "billing_route": "debit_pool",
        "is_default": True,
    },
    {
        "name": "Avulso",
        "description": "Gera valor em R$, faturado no mês seguinte.",
        "billing_route": "bill_amount",
        "is_default": False,
    },
    {
        "name": "Garantia",
        "description": "Retrabalho de atendimento já cobrado. Não debita e não fatura.",
        "billing_route": "non_billable",
        "is_default": False,
    },
    {
        "name": "Cortesia",
        "description": "Desconto comercial concedido. Não debita e não fatura.",
        "billing_route": "non_billable",
        "is_default": False,
    },
)

# Maps the billing mode enum the client entity carried in the previous phase onto
# the seeded billing type that replaces it. Used by the data migration, in both
# directions.
LEGACY_BILLING_MODE_TO_TYPE_NAME = {"contract": "Contrato", "ad_hoc": "Avulso"}


def _existing_fields(model, field_names):
    """Filter ``field_names`` down to the ones ``model`` actually has.

    NOT defensive programming for its own sake. This module is imported by data
    migrations, which receive **historical** models from ``apps.get_model`` — the model
    as it existed at that point in the chain, not the current one. Migration 0124 seeds
    the catalogues at a state where ``ServiceHourType`` has no ``priority`` column,
    because 0126 adds it. Passing ``priority`` there raises
    ``TypeError: got unexpected keyword arguments``, and it would only ever surface on a
    fresh database — never in the test suite, which builds its schema from the current
    models and skips migrations entirely (``--nomigrations`` in pytest.ini).

    So every field a later phase adds to a seed row has to be filtered like this, or
    the migration that predates it stops applying.
    """
    available = {field.name for field in model._meta.get_fields()}
    return tuple(name for name in field_names if name in available)


def _seed_one(model, workspace_id, rows, extra_fields):
    """Create the missing rows of one catalogue for one workspace.

    ``get_or_create`` and not ``update_or_create``: re-running this must never
    overwrite an admin's edits. A workspace whose multiplier was tuned to 1.8 keeps
    1.8. Only genuinely absent rows are added.

    Returns the number of rows created.
    """
    extra_fields = _existing_fields(model, extra_fields)

    # A workspace that already has a default must not receive a second one: the
    # partial unique constraint would raise. This matters for the repair command
    # rather than for the data migration -- the migration runs against brand new
    # tables, but the command can be pointed at a workspace whose catalogue was
    # partially built by hand, and refusing to seed at all would be worse than
    # seeding the missing rows without touching whichever default already exists.
    catalogue_has_default = model.objects.filter(
        workspace_id=workspace_id, is_default=True, deleted_at__isnull=True
    ).exists()

    created_count = 0

    for index, row in enumerate(rows):
        should_be_default = row["is_default"] and not catalogue_has_default

        defaults = {
            "description": row["description"],
            "sequence": SEQUENCE_STEP * (index + 1),
            "is_active": True,
            "is_default": should_be_default,
        }
        defaults.update({field: row[field] for field in extra_fields})

        _, created = model.objects.get_or_create(
            workspace_id=workspace_id,
            name=row["name"],
            deleted_at__isnull=True,
            defaults=defaults,
        )

        if created:
            created_count += 1
            if should_be_default:
                catalogue_has_default = True

    return created_count


def seed_hour_types(model, workspace_id):
    """Seed the hour type catalogue of a single workspace. Idempotent."""
    rows = tuple({**row, "multiplier": Decimal(row["multiplier"])} for row in HOUR_TYPE_SEED)
    return _seed_one(model, workspace_id, rows, extra_fields=("multiplier", "color", "priority"))


def seed_classification_windows(window_model, hour_type_model, workspace_id):
    """Give a workspace the windows that express rule R10. Idempotent.

    Matches hour types **by name**, and skips silently when a name is absent. An admin
    who renamed "Fora do expediente" loses the seeding of its windows rather than
    causing a failure -- the same tolerance the priority back-fill needs, and for the
    same reason: this runs inside a data migration, and a migration that refuses to
    apply because someone renamed a row is worse than a configuration to fix by hand.

    Returns the number of windows created.
    """
    hour_types_by_name = {
        hour_type.name: hour_type
        for hour_type in hour_type_model.objects.filter(workspace_id=workspace_id, deleted_at__isnull=True)
    }

    created_count = 0

    for hour_type_name, windows in CLASSIFICATION_WINDOW_SEED.items():
        hour_type = hour_types_by_name.get(hour_type_name)

        if hour_type is None:
            continue

        for window in windows:
            _, created = window_model.objects.get_or_create(
                workspace_id=workspace_id,
                hour_type_id=hour_type.id,
                day_scope=window["day_scope"],
                start_minute=window["start_minute"],
                end_minute=window["end_minute"],
                deleted_at__isnull=True,
                defaults={"is_active": True},
            )

            if created:
                created_count += 1

    return created_count


def seed_billing_types(model, workspace_id):
    """Seed the billing type catalogue of a single workspace. Idempotent."""
    return _seed_one(model, workspace_id, BILLING_TYPE_SEED, extra_fields=("billing_route",))


def seed_service_catalogs(hour_type_model, billing_type_model, workspace_id, window_model=None):
    """Seed the catalogues, and the classification windows, of a single workspace.

    Idempotent.

    Callers pass the model classes so that a data migration can hand over its
    historical models. Note that historical models carry no custom ``save()``, which
    is why ``sequence`` and ``is_default`` are always set explicitly here instead of
    being left to the model to derive.

    ``window_model`` is optional, and that is what keeps migration 0124 working: at that
    point in the chain the window table does not exist yet, so 0124 calls this without
    it and seeds only the two catalogues. Live callers and 0126 pass it and get the
    windows too.
    """
    created = {
        "hour_types_created": seed_hour_types(hour_type_model, workspace_id),
        "billing_types_created": seed_billing_types(billing_type_model, workspace_id),
    }

    if window_model is not None:
        created["windows_created"] = seed_classification_windows(
            window_model, hour_type_model, workspace_id
        )

    return created
