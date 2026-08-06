# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
from django.core.management.base import BaseCommand, CommandError

# Module imports
from plane.db.models import ServiceContractPeriod, Workspace
from plane.utils.service_pool import reconcile_period, repair_period


class Command(BaseCommand):
    help = (
        "Check that every hour pool's totals still agree with its ledger, and "
        "optionally rebuild the totals from the ledger. Reports per column, so the "
        "output names which number diverged rather than only that something did."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--workspace",
            type=str,
            default=None,
            help="Workspace slug. Omit to check every workspace.",
        )
        parser.add_argument(
            "--contract",
            type=str,
            default=None,
            help="Contract id, to narrow the check to one agreement.",
        )
        parser.add_argument(
            "--repair",
            action="store_true",
            help=(
                "Rewrite diverging totals from the ledger. The ledger always wins: it "
                "is append-only and every row records its own cause, so it is the only "
                "one of the two that can be audited."
            ),
        )

    def handle(self, *args, **options):
        """Reconcile hour pools, and repair them on request.

        This exists because the explicit branch added to the deletion cascade closes
        *today's* route to divergence, and any future ``update(deleted_at=...)`` on a
        work log reopens the same class of bug. Two numbers that are supposed to be
        equal drift silently otherwise, and nobody finds out until a client disputes an
        invoice -- so a cheap, tested repair is the difference between an incident and a
        command somebody runs.

        Reads without locking, deliberately: it has to be cheap enough to run across a
        whole workspace, and a debit landing mid-scan shows up as a discrepancy that is
        gone on the next run. ``--repair`` does take the row lock, one period at a time.
        """
        periods = ServiceContractPeriod.objects.select_related("contract").order_by(
            "contract__code", "competence_year", "competence_month"
        )

        if options["workspace"]:
            workspace = Workspace.objects.filter(slug=options["workspace"]).first()

            if workspace is None:
                raise CommandError(f"No workspace with slug {options['workspace']!r}.")

            periods = periods.filter(workspace_id=workspace.id)

        if options["contract"]:
            periods = periods.filter(contract_id=options["contract"])

        checked = 0
        diverged = 0
        repaired = 0

        for period in periods.iterator():
            checked += 1
            result = reconcile_period(period)

            if result["is_consistent"]:
                continue

            diverged += 1

            self.stdout.write(
                self.style.WARNING(
                    f"{period.contract.code} {result['competence']} diverged: "
                    + ", ".join(
                        f"{item['field']} ledger={item['ledger']} period={item['period']}"
                        for item in result["discrepancies"]
                    )
                )
            )

            if options["repair"]:
                repair_period(period)
                after = reconcile_period(
                    ServiceContractPeriod.objects.get(pk=period.pk)
                )

                if after["is_consistent"]:
                    repaired += 1
                    self.stdout.write(f"  repaired {period.contract.code} {result['competence']}")
                else:
                    # A repair that does not converge means the ledger itself is
                    # inconsistent -- a wrong sign, say -- and rewriting the totals
                    # again would only hide it. Reported rather than retried.
                    self.stdout.write(
                        self.style.ERROR(
                            f"  repair did NOT reconcile {period.contract.code} "
                            f"{result['competence']}; the ledger itself needs review"
                        )
                    )

        summary = f"Checked {checked} period(s); {diverged} diverged"

        if options["repair"]:
            summary += f"; {repaired} repaired"

        self.stdout.write(self.style.SUCCESS(summary))
