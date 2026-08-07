# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Export schema for work logs. Section 5 of the pricing phase, acceptance criterion 11.

Fills the ``("issue_worklogs", "Issue Worklogs")`` choice that has sat unused in
``plane.db.models.exporter`` since before this feature existed. **Nothing new is built
here**: the queue, the history row, the status transitions, the zip, the S3 upload, the
presigned URL and the eight-day expiry all come from the existing pipeline. This is the
handler that pipeline was always missing.

Uses the schema-driven stack (``plane.utils.exporters``) rather than the DRF-serializer one
(``plane.utils.porters``), because a billing document needs **explicit column order and
explicit header text**, and that stack derives both from declared fields with ``label=``
instead of from whatever order a serializer happened to emit.

**Money is in this export, so the endpoint that triggers it is Admin only** -- see
``plane.app.views.service_pricing``. R11 is a serializer-level rule and a CSV is a
serialisation.
"""

# Module imports
from plane.utils.service_log_time import format_hours
from plane.utils.service_money import format_money

from .base import DateField, ExportSchema, NumberField, StringField


class ServiceLogExportSchema(ExportSchema):
    """One row per work log segment, with the hours, the money and the reasons.

    **A segment per row, not an entry per row.** A batch that crossed 18:00 is two rows
    with two different multipliers and therefore two different values, and collapsing them
    would hide the arithmetic the invoice is built from -- which is the one thing an export
    exists to let somebody check.

    Every decimal is rendered as a **pt-BR formatted string** rather than a raw number.
    That is deliberate for a document a human opens in a spreadsheet: the value that
    appears has to be the value on the invoice, and a float written by a formatter that
    reformats numbers is how R$ 1.234,56 becomes something else. The raw decimals are in
    the API for machines.
    """

    # ---- identity -----------------------------------------------------------------
    service_client = StringField(source="project.service_client.name", label="Cliente")
    project = StringField(source="project.name", label="Projeto")
    work_item = StringField(source="issue.name", label="Chamado")
    worked_on = DateField(source="worked_on", label="Data do atendimento")
    author = StringField(source="author.display_name", label="Tecnico")
    description = StringField(source="description", label="Descricao")

    # ---- the four hour quantities, all four distinct ------------------------------
    # Section 6 of the work log brief warns that confusing these is the most likely error
    # in the feature, so the export names them apart instead of shipping one "total".
    raw_duration = StringField(label="Tempo informado")
    logged_hours = StringField(label="Horas apontadas")
    equivalent_hours = StringField(label="Horas equivalentes")
    debited_hours = StringField(label="Horas debitadas")

    # ---- how it was classified and charged ----------------------------------------
    hour_type = StringField(source="hour_type.name", label="Tipo de hora")
    applied_multiplier = NumberField(source="applied_multiplier", label="Multiplicador")
    billing_type = StringField(source="billing_type.name", label="Tipo de atendimento")
    chosen_route = StringField(source="applied_billing_route", label="Rota escolhida")
    settled_route = StringField(source="settled_billing_route", label="Rota aplicada")

    # ---- the money ----------------------------------------------------------------
    applied_hour_rate = StringField(label="Valor/hora aplicado")
    rate_basis = StringField(source="applied_rate_basis", label="Base do valor")
    amount = StringField(label="Valor")

    # ---- why, when the answer is not self evident ---------------------------------
    # The two reason columns are separate because they are separate facts that can be true
    # at once: a contract can be expired (a deviation) *and* the client can have no price
    # sheet (a failure). One column would lose whichever was written second.
    route_deviation_reason = StringField(source="route_deviation_reason", label="Motivo do desvio")
    pricing_failure_reason = StringField(source="pricing_failure_reason", label="Pendencia de preco")

    def prepare_raw_duration(self, obj):
        from plane.utils.service_log_time import format_duration

        return format_duration(obj.raw_duration_minutes)

    def prepare_logged_hours(self, obj):
        return format_hours(obj.logged_hours)

    def prepare_equivalent_hours(self, obj):
        return format_hours(obj.equivalent_hours)

    def prepare_debited_hours(self, obj):
        return format_hours(obj.debited_hours)

    def prepare_applied_hour_rate(self, obj):
        """Blank, not R$ 0,00, when no rate applied.

        A row the pool paid for and a row nobody could price both have no rate, and neither
        of them costs zero -- one costs hours and the other costs an unanswered question.
        Printing R$ 0,00 would make both look like completed calculations.
        """
        if obj.applied_hour_rate is None:
            return ""

        return format_money(obj.applied_hour_rate)

    def prepare_amount(self, obj):
        """Same reasoning as the rate: blank when there is no value, not a zero."""
        if obj.applied_hour_rate is None:
            return ""

        return format_money(obj.amount)

    def prepare_service_client(self, obj):
        """Internal work says so, rather than leaving the client column empty.

        A blank cell in a billing export reads as data that failed to load. "Trabalho
        interno" is the actual fact, and section 1 of Phase 1 is what makes it a fact rather
        than a gap: a project with no client is not billable to anyone.
        """
        client = obj.project.service_client

        return client.name if client is not None else "Trabalho interno"
