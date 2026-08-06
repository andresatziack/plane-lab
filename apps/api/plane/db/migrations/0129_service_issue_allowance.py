# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Phase 5: the work item hour allowance.

Entirely additive, and therefore reversible: reversing drops the new table, the two new
columns and the four new constraints, and restores the ledger's original sign constraint.
There is **no back-fill** -- ``debited_allowance`` is born null everywhere, and every
existing ledger row already carries both ``contract`` and ``period``.

Two of the constraints added here are declared over **pre-existing columns**, so unlike
the rest of this migration they can fail on real data:

* ``service_ledger_entry_has_exactly_one_target`` requires every existing ledger row to
  have both ``contract`` and ``period`` set, which was mandatory until this migration
  made them nullable;
* ``service_log_non_billable_debits_no_origin`` requires no ``NON_BILLABLE`` work log to
  name a debited origin. Nothing should ever have written one -- ``apply_debit`` returns
  before touching ``debited_period`` when the route is not ``DEBIT_POOL`` -- but "should"
  is the word that makes this worth checking.

``_refuse_on_rows_that_break_the_new_invariants`` runs **before** both, and its whole
purpose is the error message. A raw Postgres ``IntegrityError`` naming a constraint tells
whoever is running the deploy nothing about which rows are wrong; this names them. A
migration that fails loudly on bad data is better than one that carries it forward
silently, and if it does fail the finding is a bug to fix, not a constraint to relax.
"""

# Python imports
import uuid
from decimal import Decimal

# Django imports
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.models import Q

#: How many offending primary keys to name before truncating. Enough to see the shape of
#: the problem; not so many that the traceback becomes unreadable.
_MAX_REPORTED_IDS = 50


def _refuse_on_rows_that_break_the_new_invariants(apps, schema_editor):
    """Refuse to add the two constraints over old columns if any row already breaks them.

    Reads through ``_base_manager`` on purpose. Both models are soft-delete models whose
    default manager hides ``deleted_at`` rows, but a check constraint is enforced against
    **every** row in the table including soft deleted ones -- so a check that used the
    filtered manager would pass here and then fail in the DDL, which is the exact
    failure mode this function exists to prevent.
    """
    ledger = apps.get_model("db", "ServiceHourLedgerEntry")
    service_log = apps.get_model("db", "ServiceLog")

    problems = []

    # `allowance` is null on every row at this point -- the column was created moments
    # ago -- so the only way to violate the XOR is a missing contract or period.
    untargeted = list(
        ledger._base_manager.filter(Q(period__isnull=True) | Q(contract__isnull=True))
        .values_list("id", flat=True)[:_MAX_REPORTED_IDS]
    )

    if untargeted:
        problems.append(
            "service_hour_ledger_entries sem contrato ou sem periodo, o que viola "
            f"service_ledger_entry_has_exactly_one_target: {[str(pk) for pk in untargeted]}"
        )

    non_billable_with_origin = list(
        service_log._base_manager.filter(
            applied_billing_route="non_billable",
        )
        .filter(Q(debited_period__isnull=False) | Q(debited_allowance__isnull=False))
        .values_list("id", flat=True)[:_MAX_REPORTED_IDS]
    )

    if non_billable_with_origin:
        problems.append(
            "service_logs de rota non_billable com origem debitada, o que viola "
            f"service_log_non_billable_debits_no_origin: {[str(pk) for pk in non_billable_with_origin]}"
        )

    if problems:
        raise RuntimeError(
            "A migracao 0129 encontrou linhas que violam invariantes que o codigo da "
            "Fase 4 deveria garantir. Isso e um bug a corrigir, nao uma constraint a "
            "relaxar -- as linhas abaixo estao com a origem do debito errada e "
            "afetariam faturamento.\n\n" + "\n\n".join(problems)
        )


class Migration(migrations.Migration):
    dependencies = [
        ("db", "0128_service_contract_and_hour_ledger"),
    ]

    operations = [
        migrations.CreateModel(
            name="ServiceIssueAllowance",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Created At")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Last Modified At")),
                ("deleted_at", models.DateTimeField(blank=True, null=True, verbose_name="Deleted At")),
                (
                    "id",
                    models.UUIDField(
                        db_index=True,
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                        unique=True,
                    ),
                ),
                ("reference", models.CharField(blank=True, max_length=255)),
                ("notes", models.TextField(blank=True)),
                (
                    "credited_hours",
                    models.DecimalField(decimal_places=4, default=Decimal("0.0000"), max_digits=10),
                ),
                (
                    "consumed_hours",
                    models.DecimalField(decimal_places=4, default=Decimal("0.0000"), max_digits=10),
                ),
                (
                    "overage_hours",
                    models.DecimalField(decimal_places=4, default=Decimal("0.0000"), max_digits=10),
                ),
                (
                    "expired_hours",
                    models.DecimalField(decimal_places=4, default=Decimal("0.0000"), max_digits=10),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[("open", "Open"), ("closed", "Closed")], default="open", max_length=20
                    ),
                ),
                ("closed_at", models.DateTimeField(blank=True, null=True)),
                ("external_source", models.CharField(blank=True, max_length=255, null=True)),
                ("external_id", models.CharField(blank=True, max_length=255, null=True)),
            ],
            options={
                "verbose_name": "Service Issue Allowance",
                "verbose_name_plural": "Service Issue Allowances",
                "db_table": "service_issue_allowances",
                "ordering": ("-created_at",),
            },
        ),
        migrations.RemoveConstraint(
            model_name="servicehourledgerentry",
            name="service_ledger_hours_sign_matches_entry_type",
        ),
        migrations.AlterField(
            model_name="servicehourledgerentry",
            name="contract",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.DO_NOTHING,
                related_name="ledger_entries",
                to="db.servicecontract",
            ),
        ),
        migrations.AlterField(
            model_name="servicehourledgerentry",
            name="entry_type",
            field=models.CharField(
                choices=[
                    ("grant", "Monthly quota granted"),
                    ("carry_in", "Balance carried in"),
                    ("carry_out", "Balance carried out"),
                    ("debit", "Work log debited"),
                    ("reversal", "Work log debit reversed"),
                    ("overage_billed", "Deficit billed as overage"),
                    ("expired_by_validity", "Expired by carryover validity"),
                    ("expired_by_cap", "Discarded by accrual cap"),
                    ("expired_by_contract_end", "Expired at contract end"),
                    ("transferred_to_contract", "Transferred to a successor contract"),
                    ("converted_to_issue_allowance", "Converted to a work item allowance"),
                    ("credit", "Hours credited to a work item allowance"),
                    ("expired_by_allowance_close", "Allowance surplus written off at close"),
                ],
                max_length=32,
            ),
        ),
        migrations.AlterField(
            model_name="servicehourledgerentry",
            name="period",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.DO_NOTHING,
                related_name="ledger_entries",
                to="db.servicecontractperiod",
            ),
        ),
        migrations.AddField(
            model_name="serviceissueallowance",
            name="closed_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="closed_service_issue_allowances",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="serviceissueallowance",
            name="created_by",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="%(class)s_created_by",
                to=settings.AUTH_USER_MODEL,
                verbose_name="Created By",
            ),
        ),
        migrations.AddField(
            model_name="serviceissueallowance",
            name="issue",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="service_allowances",
                to="db.issue",
            ),
        ),
        migrations.AddField(
            model_name="serviceissueallowance",
            name="project",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="project_%(class)s",
                to="db.project",
            ),
        ),
        migrations.AddField(
            model_name="serviceissueallowance",
            name="updated_by",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="%(class)s_updated_by",
                to=settings.AUTH_USER_MODEL,
                verbose_name="Last Modified By",
            ),
        ),
        migrations.AddField(
            model_name="serviceissueallowance",
            name="workspace",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="workspace_%(class)s",
                to="db.workspace",
            ),
        ),
        migrations.AddField(
            model_name="servicehourledgerentry",
            name="allowance",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.DO_NOTHING,
                related_name="ledger_entries",
                to="db.serviceissueallowance",
            ),
        ),
        migrations.AddField(
            model_name="servicelog",
            name="debited_allowance",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.DO_NOTHING,
                related_name="service_logs",
                to="db.serviceissueallowance",
            ),
        ),
        # Diagnose before constraining. See the module docstring: these are the only two
        # constraints here that are declared over columns that already hold data.
        migrations.RunPython(
            _refuse_on_rows_that_break_the_new_invariants,
            reverse_code=migrations.RunPython.noop,
            elidable=False,
        ),
        migrations.AddIndex(
            model_name="servicehourledgerentry",
            index=models.Index(fields=["allowance", "entry_type"], name="svc_ledger_allowance_idx"),
        ),
        migrations.AddConstraint(
            model_name="servicehourledgerentry",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("allowance__isnull", True), ("contract__isnull", False), ("period__isnull", False)),
                    models.Q(("allowance__isnull", False), ("contract__isnull", True), ("period__isnull", True)),
                    _connector="OR",
                ),
                name="service_ledger_entry_has_exactly_one_target",
            ),
        ),
        migrations.AddConstraint(
            model_name="servicehourledgerentry",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        (
                            "entry_type__in",
                            [
                                "debit",
                                "expired_by_validity",
                                "expired_by_cap",
                                "expired_by_contract_end",
                                "transferred_to_contract",
                                "converted_to_issue_allowance",
                                "expired_by_allowance_close",
                            ],
                        ),
                        ("hours__lte", 0),
                    ),
                    models.Q(("entry_type__in", ["reversal", "overage_billed", "credit"]), ("hours__gte", 0)),
                    ("entry_type__in", ["grant", "carry_in", "carry_out"]),
                    _connector="OR",
                ),
                name="service_ledger_hours_sign_matches_entry_type",
            ),
        ),
        migrations.AddConstraint(
            model_name="servicelog",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("debited_allowance__isnull", False), ("debited_period__isnull", False), _negated=True
                ),
                name="service_log_debits_at_most_one_origin",
            ),
        ),
        migrations.AddConstraint(
            model_name="servicelog",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("applied_billing_route", "non_billable"), _negated=True),
                    models.Q(("debited_allowance__isnull", True), ("debited_period__isnull", True)),
                    _connector="OR",
                ),
                name="service_log_non_billable_debits_no_origin",
            ),
        ),
        migrations.AddIndex(
            model_name="serviceissueallowance",
            index=models.Index(fields=["issue"], name="svc_allowance_issue_idx"),
        ),
        migrations.AddIndex(
            model_name="serviceissueallowance",
            index=models.Index(fields=["workspace", "status"], name="svc_allowance_ws_status_idx"),
        ),
        migrations.AddIndex(
            model_name="serviceissueallowance",
            index=models.Index(fields=["project", "status"], name="svc_allowance_proj_status_idx"),
        ),
        migrations.AddConstraint(
            model_name="serviceissueallowance",
            constraint=models.UniqueConstraint(
                condition=models.Q(("deleted_at__isnull", True)),
                fields=("issue",),
                name="service_issue_allowance_unique_per_issue_when_deleted_at_null",
            ),
        ),
        migrations.AddConstraint(
            model_name="serviceissueallowance",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("credited_hours__gte", 0), ("expired_hours__gte", 0), ("overage_hours__gte", 0)
                ),
                name="service_issue_allowance_hours_are_not_negative",
            ),
        ),
        migrations.AddConstraint(
            model_name="serviceissueallowance",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("closed_at__isnull", True), ("status", "open")),
                    models.Q(("closed_at__isnull", False), ("status", "closed")),
                    _connector="OR",
                ),
                name="service_issue_allowance_closed_at_matches_status",
            ),
        ),
        migrations.AlterUniqueTogether(
            name="serviceissueallowance",
            unique_together={("issue", "deleted_at")},
        ),
    ]
