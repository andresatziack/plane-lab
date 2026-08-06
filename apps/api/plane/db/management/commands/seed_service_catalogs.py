# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
from django.core.management.base import BaseCommand, CommandError

# Module imports
from plane.db.models import ServiceBillingType, ServiceHourType, Workspace
from plane.utils.service_catalog_seed import seed_service_catalogs


class Command(BaseCommand):
    help = (
        "Create the initial work log catalogues (hour types and billing types) for "
        "one workspace or for all of them. Idempotent: existing options are never "
        "overwritten, only missing ones are added."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--workspace",
            type=str,
            default=None,
            help="Workspace slug. Seeds every workspace when omitted.",
        )

    def handle(self, *args, **options):
        """Repair path for a workspace whose catalogues are missing.

        Data migration 0124 seeds every workspace that existed when it ran, and
        workspace creation seeds new ones. This command covers the gap in between: a
        workspace created while the seeding hook was failing, or one whose catalogue
        was emptied by hand.
        """
        slug = options.get("workspace")

        workspaces = Workspace.objects.filter(deleted_at__isnull=True)

        if slug:
            workspaces = workspaces.filter(slug=slug)

            if not workspaces.exists():
                raise CommandError(f"No workspace with slug '{slug}'.")

        total_hour_types = 0
        total_billing_types = 0

        for workspace in workspaces:
            created = seed_service_catalogs(ServiceHourType, ServiceBillingType, workspace.id)
            total_hour_types += created["hour_types_created"]
            total_billing_types += created["billing_types_created"]

            self.stdout.write(
                f"{workspace.slug}: +{created['hour_types_created']} hour type(s), "
                f"+{created['billing_types_created']} billing type(s)"
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. {total_hour_types} hour type(s) and {total_billing_types} "
                f"billing type(s) created across {workspaces.count()} workspace(s)."
            )
        )
