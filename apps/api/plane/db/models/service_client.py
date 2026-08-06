# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

# Module imports
from plane.utils.tax_id import CNPJ_LENGTH, is_valid_cnpj, normalize_cnpj

from .workspace import WorkspaceBaseModel


def validate_tax_id(value):
    """Model level guard for the CNPJ field.

    The serializer normalizes and reports the friendly error; this exists so that
    data created outside the API (shell, data migration, management command)
    cannot persist a malformed identifier.
    """
    if value in (None, "") or is_valid_cnpj(value):
        return
    raise ValidationError("Enter a valid CNPJ.", code="invalid_tax_id")


class ServiceClient(WorkspaceBaseModel):
    """A client company that service desk work is delivered to and billed for.

    Named ``ServiceClient`` rather than ``Customer`` on purpose. Plane ships a
    separate, unrelated ``Customers`` feature (client profiles plus customer
    requests linked many-to-many to work items). Ours is a billing anchor with a
    strict one-client-to-many-projects relationship, so sharing the name would
    collide on table name, route and meaning if this fork is ever merged with
    upstream or migrated to the Commercial Edition.

    The client of a work item is always derived from ``issue.project.service_client``
    and is never stored on the work item itself. See docs/worklog/DECISOES.md, D19.

    Inherited from ``WorkspaceBaseModel``: ``workspace`` (required) and a nullable
    ``project`` FK. The ``project`` field is not used by this model -- a client
    owns projects, it does not belong to one -- and is excluded from serializers.
    """

    class BillingMode(models.TextChoices):
        """Default billing route for work logged against this client's projects.

        Phase 2 introduces a configurable Service Type catalog whose billing
        route supersedes this field. At that point this becomes the fallback used
        to seed a nullable ``default_service_type`` FK via data migration. Keep
        the values stable until then.
        """

        CONTRACT = "contract", "Contract"
        AD_HOC = "ad_hoc", "Ad hoc"

    name = models.CharField(max_length=255, verbose_name="Legal Name")
    trade_name = models.CharField(max_length=255, blank=True, verbose_name="Trade Name")

    # Normalized CNPJ: 14 characters, no mask. Nullable so that foreign
    # companies and individuals can be registered without one.
    tax_id = models.CharField(
        max_length=CNPJ_LENGTH,
        null=True,
        blank=True,
        validators=[validate_tax_id],
        verbose_name="CNPJ",
    )

    is_active = models.BooleanField(default=True)

    default_billing_mode = models.CharField(
        max_length=20,
        choices=BillingMode.choices,
        default=BillingMode.CONTRACT,
    )

    # Corporate parent, e.g. a holding company that owns this client. Records the
    # shareholding relationship for a future consolidated group report (Phase 9).
    # Deliberately carries NO behaviour: it does not affect billing, hour pool
    # debiting, or visibility. See docs/worklog/DECISOES.md, D18.
    parent = models.ForeignKey(
        "db.ServiceClient",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="children",
    )

    contact_name = models.CharField(max_length=255, blank=True)
    contact_email = models.EmailField(max_length=255, blank=True)
    contact_phone = models.CharField(max_length=32, blank=True)
    notes = models.TextField(blank=True)

    # Import provenance, following the convention used across plane.db.
    external_source = models.CharField(max_length=255, null=True, blank=True)
    external_id = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        # Uniqueness under soft delete follows the house pattern: unique_together
        # including deleted_at, plus a conditional UniqueConstraint for live rows.
        unique_together = [["workspace", "name", "deleted_at"], ["workspace", "tax_id", "deleted_at"]]
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "name"],
                condition=Q(deleted_at__isnull=True),
                name="service_client_unique_name_workspace_when_deleted_at_null",
            ),
            models.UniqueConstraint(
                fields=["workspace", "tax_id"],
                # tax_id is nullable, so exclude NULLs from the constraint to
                # allow many clients without a CNPJ.
                condition=Q(deleted_at__isnull=True, tax_id__isnull=False),
                name="service_client_unique_tax_id_workspace_when_deleted_at_null",
            ),
        ]
        indexes = [models.Index(fields=["workspace", "is_active"], name="service_client_ws_active_idx")]
        verbose_name = "Service Client"
        verbose_name_plural = "Service Clients"
        db_table = "service_clients"
        ordering = ("name",)

    def __str__(self):
        return f"{self.name} <{self.workspace.name}>"

    def save(self, *args, **kwargs):
        self.tax_id = normalize_cnpj(self.tax_id)
        super().save(*args, **kwargs)
