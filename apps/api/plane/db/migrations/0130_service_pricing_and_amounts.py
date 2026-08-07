# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Phase 6: client price sheets, the value in reais, and priced overage.

Additive except for one data write, and reversible: reversing drops the two new tables,
the eight new columns, the two new indexes and the fourteen new constraints. The reverse
of the back-fill is a no-op because the column it wrote is itself dropped.

**The one data write is mandatory, not cosmetic.** ``settled_billing_route`` arrives with
the field default ``debit_pool`` on every existing row, while
``service_log_route_deviation_is_coherent`` requires ``settled_billing_route ==
applied_billing_route`` wherever no deviation is recorded -- so every pre-existing
``non_billable`` or ``bill_amount`` work log would violate it. The back-fill copies
``applied_billing_route`` across, and that copy is an **exact fact rather than a guess**:
before this migration no deviation was possible, because nothing but the chosen route
existed, so the route that was applied *was* the route that was chosen. Nothing is
invented, which is the bar the migration conventions set.

Four constraints here are declared over **pre-existing columns**, so unlike the rest of
this migration they can fail on real data:

* ``service_ledger_unique_overage_billed_per_period`` and its allowance twin. Until now
  billing a period's overage twice was refused only by ``close_period`` raising
  ``PERIOD_ALREADY_CLOSED`` -- an ``if``, which the existing partial index does not back
  up because that index is conditioned on ``service_log__isnull=False`` and an
  ``OVERAGE_BILLED`` row has no work log. Duplicates were therefore *representable*.
* ``service_contract_overage_rate_is_positive``. Phase 4 stored ``overage_hour_rate``
  without a guard because nothing read it; a negative value was representable and would
  now credit a client for exceeding their pool.
* ``service_log_billed_debits_no_pool``. No ``bill_amount`` work log should name a
  debited origin -- ``apply_debit`` returns at its first guard for any non-pool route --
  but "should" is the word that makes this worth checking.

``_refuse_on_rows_that_break_the_new_invariants`` runs **before** all four, for the
reason 0129 gives: a raw Postgres ``IntegrityError`` naming a constraint tells whoever is
running the deploy nothing about which rows are wrong, and this names them. If it fires,
the finding is a bug to fix, not a constraint to relax.

**Deliberately NOT enforced here:** that an ``OVERAGE_BILLED`` row carries an amount.
Rows written by Phases 4 and 5 are real history from before prices existed, and there is
no honest value to back-fill them with -- so ``service_ledger_amount_only_on_overage_billed``
is one-directional, and the strong invariant lives in ``_settle_deficit`` and in a test.
"""

# Python imports
import uuid
from decimal import Decimal

# Django imports
import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.models import F, Q

# Module imports
import plane.db.mixins

#: How many offending primary keys to name before truncating. Enough to see the shape of
#: the problem; not so many that the traceback becomes unreadable. Same value as 0129.
_MAX_REPORTED_IDS = 50


def _copy_chosen_route_onto_settled_route(apps, schema_editor):
    """Back-fill ``settled_billing_route`` from ``applied_billing_route``.

    An exact copy, not an inference. Before this migration a work log recorded only the
    route it chose, and nothing could deviate from it -- D33's fallback arrives with this
    phase -- so for every existing row the settled route *is* the chosen route.

    Uses ``_base_manager`` and a queryset ``update()``: check constraints are enforced
    against every row in the table including soft deleted ones, so a back-fill that used
    the default manager would leave soft deleted rows behind to fail the DDL a few
    operations later.
    """
    service_log = apps.get_model("db", "ServiceLog")

    service_log._base_manager.update(settled_billing_route=F("applied_billing_route"))


def _refuse_on_rows_that_break_the_new_invariants(apps, schema_editor):
    """Refuse to add the constraints over old columns if any row already breaks them.

    Reads through ``_base_manager`` for the reason given in
    ``_copy_chosen_route_onto_settled_route``: a check constraint does not care about
    ``deleted_at``, so neither may this.
    """
    ledger = apps.get_model("db", "ServiceHourLedgerEntry")
    contract = apps.get_model("db", "ServiceContract")
    service_log = apps.get_model("db", "ServiceLog")

    problems = []

    # Criterion 9 was an `if` until now, so duplicates were representable. Group by the
    # target and report any target billed more than once.
    for column, label in (("period_id", "periodo"), ("allowance_id", "bolsa")):
        duplicated = list(
            ledger._base_manager.filter(entry_type="overage_billed")
            .exclude(**{f"{column}__isnull": True})
            .values_list(column, flat=True)
            .annotate(billings=models.Count("id"))
            .filter(billings__gt=1)[:_MAX_REPORTED_IDS]
        )

        if duplicated:
            problems.append(
                f"{label}(s) com mais de um lancamento overage_billed, o que viola "
                f"service_ledger_unique_overage_billed_per_{'period' if column == 'period_id' else 'allowance'}: "
                f"{[str(pk) for pk in duplicated]}"
            )

    negative_rates = list(
        contract._base_manager.filter(overage_hour_rate__lte=0).values_list("id", flat=True)[:_MAX_REPORTED_IDS]
    )

    if negative_rates:
        problems.append(
            "service_contracts com overage_hour_rate menor ou igual a zero, o que viola "
            f"service_contract_overage_rate_is_positive: {[str(pk) for pk in negative_rates]}"
        )

    billed_with_pool_origin = list(
        service_log._base_manager.filter(applied_billing_route="bill_amount")
        .filter(Q(debited_period__isnull=False) | Q(debited_allowance__isnull=False))
        .values_list("id", flat=True)[:_MAX_REPORTED_IDS]
    )

    if billed_with_pool_origin:
        problems.append(
            "service_logs de rota bill_amount com origem de pool debitada, o que viola "
            f"service_log_billed_debits_no_pool: {[str(pk) for pk in billed_with_pool_origin]}"
        )

    if problems:
        raise RuntimeError(
            "A migracao 0130 encontrou linhas que violam invariantes monetarias que o "
            "codigo das Fases 4 e 5 deveria garantir. Isso e um bug a corrigir, nao uma "
            "constraint a relaxar -- cada linha abaixo cobraria o cliente duas vezes ou "
            "cobraria o valor errado.\n\n" + "\n\n".join(problems)
        )


class Migration(migrations.Migration):

    dependencies = [
        ('db', '0129_service_issue_allowance'),
    ]

    operations = [
        migrations.CreateModel(
            name='ServiceClientHourTypeRate',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Created At')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Last Modified At')),
                ('deleted_at', models.DateTimeField(blank=True, null=True, verbose_name='Deleted At')),
                ('id', models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, primary_key=True, serialize=False, unique=True)),
                ('absolute_rate', models.DecimalField(decimal_places=2, max_digits=12, validators=[django.core.validators.MinValueValidator(Decimal('0.01'))])),
            ],
            options={
                'verbose_name': 'Service Client Hour Type Rate',
                'verbose_name_plural': 'Service Client Hour Type Rates',
                'db_table': 'service_client_hour_type_rates',
                'ordering': ('price', 'hour_type'),
            },
            bases=(plane.db.mixins.ChangeTrackerMixin, models.Model),
        ),
        migrations.CreateModel(
            name='ServiceClientPrice',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Created At')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Last Modified At')),
                ('deleted_at', models.DateTimeField(blank=True, null=True, verbose_name='Deleted At')),
                ('id', models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, primary_key=True, serialize=False, unique=True)),
                ('starts_on', models.DateField()),
                ('base_hour_rate', models.DecimalField(decimal_places=2, max_digits=12, validators=[django.core.validators.MinValueValidator(Decimal('0.01'))])),
                ('notes', models.TextField(blank=True)),
            ],
            options={
                'verbose_name': 'Service Client Price',
                'verbose_name_plural': 'Service Client Prices',
                'db_table': 'service_client_prices',
                'ordering': ('service_client', '-starts_on'),
            },
            bases=(plane.db.mixins.ChangeTrackerMixin, models.Model),
        ),
        migrations.AddField(
            model_name='servicehourledgerentry',
            name='amount',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name='servicehourledgerentry',
            name='applied_hour_rate',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name='serviceissueallowance',
            name='overage_hour_rate',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name='servicelog',
            name='amount',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12),
        ),
        migrations.AddField(
            model_name='servicelog',
            name='applied_hour_rate',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name='servicelog',
            name='applied_rate_basis',
            field=models.CharField(blank=True, choices=[('base_multiplier', 'Client base rate times the hour type multiplier'), ('absolute_override', 'Absolute rate registered for the hour type')], max_length=20, null=True),
        ),
        migrations.AddField(
            model_name='servicelog',
            name='pricing_failure_reason',
            field=models.CharField(blank=True, choices=[('internal_project_no_client', 'Internal project, no client to invoice'), ('no_price_sheet_for_client', 'The client has no price sheet at all'), ('no_price_sheet_in_force', 'No price sheet was in force on the service date')], max_length=32, null=True),
        ),
        migrations.AddField(
            model_name='servicelog',
            name='route_deviation_reason',
            field=models.CharField(blank=True, choices=[('no_contract_for_client', 'The client has no contract'), ('contract_suspended', 'The contract is suspended'), ('contract_ended', 'The contract was ended'), ('contract_expired', 'The work date is past the contract vigency')], max_length=32, null=True),
        ),
        migrations.AddField(
            model_name='servicelog',
            name='settled_billing_route',
            field=models.CharField(choices=[('debit_pool', 'Debit hour pool'), ('bill_amount', 'Bill amount'), ('non_billable', 'Non billable')], default='debit_pool', max_length=20),
        ),
        # The column above lands as `debit_pool` everywhere. Copy the chosen route across
        # before `service_log_route_deviation_is_coherent` demands they agree. See the
        # module docstring: this is an exact fact, not an inference.
        migrations.RunPython(
            _copy_chosen_route_onto_settled_route,
            reverse_code=migrations.RunPython.noop,
            elidable=False,
        ),
        # Diagnose before constraining. Four of the constraints below are declared over
        # columns that already hold data; this names the offending rows instead of letting
        # Postgres report a constraint name and nothing else.
        migrations.RunPython(
            _refuse_on_rows_that_break_the_new_invariants,
            reverse_code=migrations.RunPython.noop,
            elidable=False,
        ),
        migrations.AddIndex(
            model_name='servicelog',
            index=models.Index(fields=['workspace', 'settled_billing_route', 'worked_on'], name='svc_log_ws_settled_worked_idx'),
        ),
        migrations.AddConstraint(
            model_name='servicecontract',
            constraint=models.CheckConstraint(condition=models.Q(('overage_hour_rate__isnull', True), ('overage_hour_rate__gt', 0), _connector='OR'), name='service_contract_overage_rate_is_positive'),
        ),
        migrations.AddConstraint(
            model_name='servicehourledgerentry',
            constraint=models.UniqueConstraint(condition=models.Q(('entry_type', 'overage_billed'), ('period__isnull', False)), fields=('period',), name='service_ledger_unique_overage_billed_per_period'),
        ),
        migrations.AddConstraint(
            model_name='servicehourledgerentry',
            constraint=models.UniqueConstraint(condition=models.Q(('allowance__isnull', False), ('entry_type', 'overage_billed')), fields=('allowance',), name='service_ledger_unique_overage_billed_per_allowance'),
        ),
        migrations.AddConstraint(
            model_name='servicehourledgerentry',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('amount__isnull', True), ('applied_hour_rate__isnull', True)), models.Q(('amount__gte', 0), ('amount__isnull', False), ('applied_hour_rate__gt', 0), ('applied_hour_rate__isnull', False), ('entry_type', 'overage_billed')), _connector='OR'), name='service_ledger_amount_only_on_overage_billed'),
        ),
        migrations.AddConstraint(
            model_name='serviceissueallowance',
            constraint=models.CheckConstraint(condition=models.Q(('overage_hour_rate__isnull', True), ('overage_hour_rate__gt', 0), _connector='OR'), name='service_issue_allowance_overage_rate_is_positive'),
        ),
        migrations.AddConstraint(
            model_name='servicelog',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('settled_billing_route', 'bill_amount'), _negated=True), models.Q(('debited_allowance__isnull', True), ('debited_period__isnull', True)), _connector='OR'), name='service_log_billed_debits_no_pool'),
        ),
        migrations.AddConstraint(
            model_name='servicelog',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('settled_billing_route', 'debit_pool'), _negated=True), models.Q(('amount', 0), ('applied_hour_rate__isnull', True), ('applied_rate_basis__isnull', True)), _connector='OR'), name='service_log_pool_route_carries_no_amount'),
        ),
        migrations.AddConstraint(
            model_name='servicelog',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('applied_billing_route', 'non_billable'), _negated=True), models.Q(('amount', 0), ('applied_hour_rate__isnull', True), ('applied_rate_basis__isnull', True), ('route_deviation_reason__isnull', True), ('settled_billing_route', 'non_billable')), _connector='OR'), name='service_log_non_billable_carries_no_amount'),
        ),
        migrations.AddConstraint(
            model_name='servicelog',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('route_deviation_reason__isnull', True), ('settled_billing_route', models.F('applied_billing_route'))), models.Q(('applied_billing_route', 'debit_pool'), ('route_deviation_reason__isnull', False), ('settled_billing_route', 'bill_amount')), _connector='OR'), name='service_log_route_deviation_is_coherent'),
        ),
        migrations.AddConstraint(
            model_name='servicelog',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('amount', 0), ('applied_hour_rate__isnull', True), ('applied_rate_basis__isnull', True)), models.Q(('amount__gt', 0), ('applied_hour_rate__isnull', False), ('applied_rate_basis__isnull', False)), _connector='OR'), name='service_log_amount_requires_a_rate'),
        ),
        migrations.AddConstraint(
            model_name='servicelog',
            constraint=models.CheckConstraint(condition=models.Q(('pricing_failure_reason__isnull', True), models.Q(('amount', 0), ('applied_hour_rate__isnull', True), ('settled_billing_route', 'bill_amount')), _connector='OR'), name='service_log_pricing_failure_means_no_amount'),
        ),
        migrations.AddField(
            model_name='serviceclienthourtyperate',
            name='created_by',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_created_by', to=settings.AUTH_USER_MODEL, verbose_name='Created By'),
        ),
        migrations.AddField(
            model_name='serviceclienthourtyperate',
            name='hour_type',
            field=models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, related_name='client_rates', to='db.servicehourtype'),
        ),
        migrations.AddField(
            model_name='serviceclienthourtyperate',
            name='project',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='project_%(class)s', to='db.project'),
        ),
        migrations.AddField(
            model_name='serviceclienthourtyperate',
            name='updated_by',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_updated_by', to=settings.AUTH_USER_MODEL, verbose_name='Last Modified By'),
        ),
        migrations.AddField(
            model_name='serviceclienthourtyperate',
            name='workspace',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='workspace_%(class)s', to='db.workspace'),
        ),
        migrations.AddField(
            model_name='serviceclientprice',
            name='created_by',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_created_by', to=settings.AUTH_USER_MODEL, verbose_name='Created By'),
        ),
        migrations.AddField(
            model_name='serviceclientprice',
            name='project',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='project_%(class)s', to='db.project'),
        ),
        migrations.AddField(
            model_name='serviceclientprice',
            name='service_client',
            field=models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, related_name='prices', to='db.serviceclient'),
        ),
        migrations.AddField(
            model_name='serviceclientprice',
            name='updated_by',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_updated_by', to=settings.AUTH_USER_MODEL, verbose_name='Last Modified By'),
        ),
        migrations.AddField(
            model_name='serviceclientprice',
            name='workspace',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='workspace_%(class)s', to='db.workspace'),
        ),
        migrations.AddField(
            model_name='serviceclienthourtyperate',
            name='price',
            field=models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, related_name='hour_type_rates', to='db.serviceclientprice'),
        ),
        migrations.AddIndex(
            model_name='serviceclientprice',
            index=models.Index(fields=['service_client', '-starts_on'], name='svc_client_price_vigency_idx'),
        ),
        migrations.AddConstraint(
            model_name='serviceclientprice',
            constraint=models.UniqueConstraint(condition=models.Q(('deleted_at__isnull', True)), fields=('service_client', 'starts_on'), name='service_client_price_unique_start_when_deleted_at_null'),
        ),
        migrations.AddConstraint(
            model_name='serviceclientprice',
            constraint=models.CheckConstraint(condition=models.Q(('base_hour_rate__gt', 0)), name='service_client_price_base_rate_is_positive'),
        ),
        migrations.AddConstraint(
            model_name='serviceclientprice',
            constraint=models.CheckConstraint(condition=models.Q(('starts_on__day', 1)), name='service_client_price_starts_on_first_of_month'),
        ),
        migrations.AlterUniqueTogether(
            name='serviceclientprice',
            unique_together={('service_client', 'starts_on', 'deleted_at')},
        ),
        migrations.AddIndex(
            model_name='serviceclienthourtyperate',
            index=models.Index(fields=['price', 'hour_type'], name='svc_client_hour_rate_idx'),
        ),
        migrations.AddConstraint(
            model_name='serviceclienthourtyperate',
            constraint=models.UniqueConstraint(condition=models.Q(('deleted_at__isnull', True)), fields=('price', 'hour_type'), name='svc_client_hour_rate_unique_type_when_deleted_at_null'),
        ),
        migrations.AddConstraint(
            model_name='serviceclienthourtyperate',
            constraint=models.CheckConstraint(condition=models.Q(('absolute_rate__gt', 0)), name='svc_client_hour_rate_is_positive'),
        ),
        migrations.AlterUniqueTogether(
            name='serviceclienthourtyperate',
            unique_together={('price', 'hour_type', 'deleted_at')},
        ),
    ]
