# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contracts, monthly hour pools and the append-only hour ledger.

Four new tables and two new columns on existing ones:

* ``service_contracts`` -- the agreement: monthly hours, vigency, carryover validity,
  accrual ceiling, overage preference and the alert thresholds.
* ``service_contract_periods`` -- one competency month per contract, holding the pool
  totals. This is the row that ``select_for_update()`` locks on every debit.
* ``service_hour_ledger_entries`` -- every movement of hours, append-only. The partial
  unique index on ``(service_log, entry_type)`` is what makes a pool debit and its
  reversal idempotent in the database rather than in application code.
* ``service_contract_alert_dismissals`` -- an acknowledged alert, together with the
  balance at the moment it was acknowledged, so the alert can re-arm when things get
  materially worse (decision B2).
* ``project.service_contract`` -- which of a client's contracts this project debits.
* ``service_logs.debited_period`` -- R4's snapshot of which pool actually paid.

**No back-fill, and none is possible or needed.** Both new columns are nullable, and
null is the correct historical value: no contract existed when those work logs were
written, so no pool was debited. Inventing a contract to attach them to would fabricate
financial history -- the modelling convention is explicit that a migration must "recusar
migrar em vez de inventar" an audit value. Existing work logs are reconciled by being
left alone; a contract registered later starts debiting from its own vigency forward.

Reversible, and verified by hand in both directions -- ``pytest.ini`` sets
``--nomigrations``, so the suite builds its schema from the models and a broken
migration would not fail a single test.
"""

import django.core.validators
import django.db.models.deletion
import plane.db.mixins
import uuid
from decimal import Decimal
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('db', '0127_service_config_activity_verb'),
    ]

    operations = [
        migrations.CreateModel(
            name='ServiceContract',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Created At')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Last Modified At')),
                ('deleted_at', models.DateTimeField(blank=True, null=True, verbose_name='Deleted At')),
                ('id', models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, primary_key=True, serialize=False, unique=True)),
                ('code', models.CharField(max_length=100)),
                ('name', models.CharField(max_length=255)),
                ('monthly_hours', models.DecimalField(decimal_places=4, max_digits=10, validators=[django.core.validators.MinValueValidator(Decimal('0.0001'))])),
                ('starts_on', models.DateField()),
                ('ends_on', models.DateField()),
                ('carryover_months', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('accrual_cap_mode', models.CharField(choices=[('none', 'No ceiling'), ('absolute', 'Absolute hours'), ('multiple', 'Multiple of monthly hours')], default='none', max_length=20)),
                ('accrual_cap_value', models.DecimalField(blank=True, decimal_places=4, max_digits=10, null=True)),
                ('high_consumption_threshold_pct', models.DecimalField(decimal_places=2, default=Decimal('80.00'), max_digits=5)),
                ('low_consumption_threshold_pct', models.DecimalField(decimal_places=2, default=Decimal('30.00'), max_digits=5)),
                ('overage_policy', models.CharField(choices=[('carry_deficit', 'Carry the deficit forward'), ('bill_amount', 'Bill the overage')], default='carry_deficit', max_length=20)),
                ('overage_hour_rate', models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ('status', models.CharField(choices=[('active', 'Active'), ('suspended', 'Suspended'), ('ended', 'Ended')], default='active', max_length=20)),
                ('is_default', models.BooleanField(default=False)),
                ('notes', models.TextField(blank=True)),
                ('external_source', models.CharField(blank=True, max_length=255, null=True)),
                ('external_id', models.CharField(blank=True, max_length=255, null=True)),
                ('created_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_created_by', to=settings.AUTH_USER_MODEL, verbose_name='Created By')),
                ('previous_contract', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='successors', to='db.servicecontract')),
                ('project', models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='project_%(class)s', to='db.project')),
                ('service_client', models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, related_name='contracts', to='db.serviceclient')),
                ('updated_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_updated_by', to=settings.AUTH_USER_MODEL, verbose_name='Last Modified By')),
                ('workspace', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='workspace_%(class)s', to='db.workspace')),
            ],
            options={
                'verbose_name': 'Service Contract',
                'verbose_name_plural': 'Service Contracts',
                'db_table': 'service_contracts',
                'ordering': ('service_client__name', 'code'),
            },
        ),
        migrations.AddField(
            model_name='project',
            name='service_contract',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='projects', to='db.servicecontract'),
        ),
        migrations.CreateModel(
            name='ServiceContractPeriod',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Created At')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Last Modified At')),
                ('deleted_at', models.DateTimeField(blank=True, null=True, verbose_name='Deleted At')),
                ('id', models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, primary_key=True, serialize=False, unique=True)),
                ('competence_year', models.SmallIntegerField()),
                ('competence_month', models.SmallIntegerField()),
                ('starts_on', models.DateField()),
                ('ends_on', models.DateField()),
                ('contracted_hours', models.DecimalField(decimal_places=4, max_digits=10)),
                ('carried_hours', models.DecimalField(decimal_places=4, default=Decimal('0.0000'), max_digits=10)),
                ('consumed_hours', models.DecimalField(decimal_places=4, default=Decimal('0.0000'), max_digits=10)),
                ('discarded_by_cap_hours', models.DecimalField(decimal_places=4, default=Decimal('0.0000'), max_digits=10)),
                ('overage_hours', models.DecimalField(decimal_places=4, default=Decimal('0.0000'), max_digits=10)),
                ('overage_settlement', models.CharField(blank=True, choices=[('carried', 'Deficit carried to the next period'), ('billed', 'Overage billed to the client')], max_length=20, null=True)),
                ('status', models.CharField(choices=[('open', 'Open'), ('closed', 'Closed')], default='open', max_length=20)),
                ('closed_at', models.DateTimeField(blank=True, null=True)),
                ('closed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='closed_service_contract_periods', to=settings.AUTH_USER_MODEL)),
                ('contract', models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, related_name='periods', to='db.servicecontract')),
                ('created_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_created_by', to=settings.AUTH_USER_MODEL, verbose_name='Created By')),
                ('project', models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='project_%(class)s', to='db.project')),
                ('updated_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_updated_by', to=settings.AUTH_USER_MODEL, verbose_name='Last Modified By')),
                ('workspace', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='workspace_%(class)s', to='db.workspace')),
            ],
            options={
                'verbose_name': 'Service Contract Period',
                'verbose_name_plural': 'Service Contract Periods',
                'db_table': 'service_contract_periods',
                'ordering': ('contract', 'competence_year', 'competence_month'),
            },
            bases=(plane.db.mixins.ChangeTrackerMixin, models.Model),
        ),
        migrations.CreateModel(
            name='ServiceContractAlertDismissal',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Created At')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Last Modified At')),
                ('deleted_at', models.DateTimeField(blank=True, null=True, verbose_name='Deleted At')),
                ('id', models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, primary_key=True, serialize=False, unique=True)),
                ('alert_code', models.CharField(max_length=64)),
                ('balance_at_dismissal', models.DecimalField(decimal_places=4, max_digits=10)),
                ('dismissed_at', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_created_by', to=settings.AUTH_USER_MODEL, verbose_name='Created By')),
                ('dismissed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='service_contract_alert_dismissals', to=settings.AUTH_USER_MODEL)),
                ('project', models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='project_%(class)s', to='db.project')),
                ('updated_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_updated_by', to=settings.AUTH_USER_MODEL, verbose_name='Last Modified By')),
                ('workspace', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='workspace_%(class)s', to='db.workspace')),
                ('period', models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, related_name='alert_dismissals', to='db.servicecontractperiod')),
            ],
            options={
                'verbose_name': 'Service Contract Alert Dismissal',
                'verbose_name_plural': 'Service Contract Alert Dismissals',
                'db_table': 'service_contract_alert_dismissals',
                'ordering': ('-dismissed_at',),
            },
        ),
        migrations.AddField(
            model_name='servicelog',
            name='debited_period',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='service_logs', to='db.servicecontractperiod'),
        ),
        migrations.CreateModel(
            name='ServiceHourLedgerEntry',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Created At')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Last Modified At')),
                ('deleted_at', models.DateTimeField(blank=True, null=True, verbose_name='Deleted At')),
                ('id', models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, primary_key=True, serialize=False, unique=True)),
                ('hours', models.DecimalField(decimal_places=4, max_digits=10)),
                ('entry_type', models.CharField(choices=[('grant', 'Monthly quota granted'), ('carry_in', 'Balance carried in'), ('carry_out', 'Balance carried out'), ('debit', 'Work log debited'), ('reversal', 'Work log debit reversed'), ('overage_billed', 'Deficit billed as overage'), ('expired_by_validity', 'Expired by carryover validity'), ('expired_by_cap', 'Discarded by accrual cap'), ('expired_by_contract_end', 'Expired at contract end'), ('transferred_to_contract', 'Transferred to a successor contract'), ('converted_to_issue_allowance', 'Converted to a work item allowance')], max_length=32)),
                ('notes', models.TextField(blank=True)),
                ('actor', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='service_hour_ledger_entries', to=settings.AUTH_USER_MODEL)),
                ('contract', models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, related_name='ledger_entries', to='db.servicecontract')),
                ('created_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_created_by', to=settings.AUTH_USER_MODEL, verbose_name='Created By')),
                ('origin_period', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='originated_ledger_entries', to='db.servicecontractperiod')),
                ('period', models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, related_name='ledger_entries', to='db.servicecontractperiod')),
                ('project', models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='project_%(class)s', to='db.project')),
                ('service_log', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='ledger_entries', to='db.servicelog')),
                ('updated_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_updated_by', to=settings.AUTH_USER_MODEL, verbose_name='Last Modified By')),
                ('workspace', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='workspace_%(class)s', to='db.workspace')),
            ],
            options={
                'verbose_name': 'Service Hour Ledger Entry',
                'verbose_name_plural': 'Service Hour Ledger Entries',
                'db_table': 'service_hour_ledger_entries',
                'ordering': ('period', 'created_at'),
            },
        ),
        migrations.AddIndex(
            model_name='servicecontract',
            index=models.Index(fields=['service_client', 'status'], name='svc_contract_client_status_idx'),
        ),
        migrations.AddIndex(
            model_name='servicecontract',
            index=models.Index(fields=['workspace', 'status'], name='svc_contract_ws_status_idx'),
        ),
        migrations.AddConstraint(
            model_name='servicecontract',
            constraint=models.UniqueConstraint(condition=models.Q(('deleted_at__isnull', True)), fields=('service_client', 'code'), name='service_contract_unique_code_per_client_when_deleted_at_null'),
        ),
        migrations.AddConstraint(
            model_name='servicecontract',
            constraint=models.UniqueConstraint(condition=models.Q(('deleted_at__isnull', True), ('is_default', True)), fields=('service_client',), name='service_contract_single_default_per_client'),
        ),
        migrations.AddConstraint(
            model_name='servicecontract',
            constraint=models.CheckConstraint(condition=models.Q(('ends_on__gte', models.F('starts_on'))), name='service_contract_vigency_is_ordered'),
        ),
        migrations.AddConstraint(
            model_name='servicecontract',
            constraint=models.CheckConstraint(condition=models.Q(('monthly_hours__gt', 0)), name='service_contract_monthly_hours_is_positive'),
        ),
        migrations.AddConstraint(
            model_name='servicecontract',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('accrual_cap_mode', 'none'), ('accrual_cap_value__isnull', True)), models.Q(models.Q(('accrual_cap_mode', 'none'), _negated=True), ('accrual_cap_value__gt', 0), ('accrual_cap_value__isnull', False)), _connector='OR'), name='service_contract_accrual_cap_is_coherent'),
        ),
        migrations.AlterUniqueTogether(
            name='servicecontract',
            unique_together={('service_client', 'code', 'deleted_at')},
        ),
        migrations.AddIndex(
            model_name='servicecontractperiod',
            index=models.Index(fields=['contract', 'competence_year', 'competence_month'], name='svc_period_competence_idx'),
        ),
        migrations.AddIndex(
            model_name='servicecontractperiod',
            index=models.Index(fields=['contract', 'status'], name='svc_period_contract_status_idx'),
        ),
        migrations.AddIndex(
            model_name='servicecontractperiod',
            index=models.Index(fields=['workspace', 'status'], name='svc_period_ws_status_idx'),
        ),
        migrations.AddConstraint(
            model_name='servicecontractperiod',
            constraint=models.UniqueConstraint(condition=models.Q(('deleted_at__isnull', True)), fields=('contract', 'competence_year', 'competence_month'), name='service_period_unique_competence_when_deleted_at_null'),
        ),
        migrations.AddConstraint(
            model_name='servicecontractperiod',
            constraint=models.CheckConstraint(condition=models.Q(('competence_month__gte', 1), ('competence_month__lte', 12)), name='service_period_competence_month_is_valid'),
        ),
        migrations.AddConstraint(
            model_name='servicecontractperiod',
            constraint=models.CheckConstraint(condition=models.Q(('ends_on__gte', models.F('starts_on'))), name='service_period_bounds_are_ordered'),
        ),
        migrations.AddConstraint(
            model_name='servicecontractperiod',
            constraint=models.CheckConstraint(condition=models.Q(('contracted_hours__gte', 0)), name='service_period_contracted_hours_is_not_negative'),
        ),
        migrations.AddConstraint(
            model_name='servicecontractperiod',
            constraint=models.CheckConstraint(condition=models.Q(('discarded_by_cap_hours__gte', 0), ('overage_hours__gte', 0)), name='service_period_closing_records_are_not_negative'),
        ),
        migrations.AddConstraint(
            model_name='servicecontractperiod',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('overage_settlement__isnull', True), ('status', 'open')), ('status', 'closed'), _connector='OR'), name='service_period_settlement_requires_closed'),
        ),
        migrations.AlterUniqueTogether(
            name='servicecontractperiod',
            unique_together={('contract', 'competence_year', 'competence_month', 'deleted_at')},
        ),
        migrations.AddIndex(
            model_name='servicecontractalertdismissal',
            index=models.Index(fields=['period', 'alert_code'], name='svc_alert_dismissal_idx'),
        ),
        migrations.AddConstraint(
            model_name='servicecontractalertdismissal',
            constraint=models.UniqueConstraint(condition=models.Q(('deleted_at__isnull', True)), fields=('period', 'alert_code'), name='service_alert_dismissal_unique_per_period_when_deleted_at_null'),
        ),
        migrations.AlterUniqueTogether(
            name='servicecontractalertdismissal',
            unique_together={('period', 'alert_code', 'deleted_at')},
        ),
        migrations.AddIndex(
            model_name='servicehourledgerentry',
            index=models.Index(fields=['period', 'entry_type'], name='svc_ledger_period_type_idx'),
        ),
        migrations.AddIndex(
            model_name='servicehourledgerentry',
            index=models.Index(fields=['period', 'origin_period'], name='svc_ledger_period_origin_idx'),
        ),
        migrations.AddIndex(
            model_name='servicehourledgerentry',
            index=models.Index(fields=['service_log'], name='svc_ledger_service_log_idx'),
        ),
        migrations.AddIndex(
            model_name='servicehourledgerentry',
            index=models.Index(fields=['contract', 'created_at'], name='svc_ledger_contract_idx'),
        ),
        migrations.AddConstraint(
            model_name='servicehourledgerentry',
            constraint=models.UniqueConstraint(condition=models.Q(('entry_type__in', ['debit', 'reversal']), ('service_log__isnull', False)), fields=('service_log', 'entry_type'), name='service_ledger_unique_debit_reversal_per_service_log'),
        ),
        migrations.AddConstraint(
            model_name='servicehourledgerentry',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('entry_type__in', ['debit', 'expired_by_validity', 'expired_by_cap', 'expired_by_contract_end', 'transferred_to_contract', 'converted_to_issue_allowance']), ('hours__lte', 0)), models.Q(('entry_type__in', ['reversal', 'overage_billed']), ('hours__gte', 0)), ('entry_type__in', ['grant', 'carry_in', 'carry_out']), _connector='OR'), name='service_ledger_hours_sign_matches_entry_type'),
        ),
    ]
