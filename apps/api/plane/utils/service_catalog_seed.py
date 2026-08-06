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
        "is_default": True,
    },
    {
        "name": "Fora do expediente",
        "description": "Segunda a sexta das 18:00 às 08:00, e sábado o dia inteiro.",
        "multiplier": "1.50",
        "color": "#F59E0B",
        "is_default": False,
    },
    {
        "name": "Domingos e feriados",
        "description": "Domingo e feriado, o dia inteiro.",
        "multiplier": "2.00",
        "color": "#E5484D",
        "is_default": False,
    },
)

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


def _seed_one(model, workspace_id, rows, extra_fields):
    """Create the missing rows of one catalogue for one workspace.

    ``get_or_create`` and not ``update_or_create``: re-running this must never
    overwrite an admin's edits. A workspace whose multiplier was tuned to 1.8 keeps
    1.8. Only genuinely absent rows are added.

    Returns the number of rows created.
    """
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
    return _seed_one(model, workspace_id, rows, extra_fields=("multiplier", "color"))


def seed_billing_types(model, workspace_id):
    """Seed the billing type catalogue of a single workspace. Idempotent."""
    return _seed_one(model, workspace_id, BILLING_TYPE_SEED, extra_fields=("billing_route",))


def seed_service_catalogs(hour_type_model, billing_type_model, workspace_id):
    """Seed both catalogues of a single workspace. Idempotent.

    Callers pass the model classes so that a data migration can hand over its
    historical models. Note that historical models carry no custom ``save()``, which
    is why ``sequence`` and ``is_default`` are always set explicitly here instead of
    being left to the model to derive.
    """
    return {
        "hour_types_created": seed_hour_types(hour_type_model, workspace_id),
        "billing_types_created": seed_billing_types(billing_type_model, workspace_id),
    }
