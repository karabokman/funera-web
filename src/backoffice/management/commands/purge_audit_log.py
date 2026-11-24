from django.conf import settings
from django.core.management.base import BaseCommand

from backoffice import audit


class Command(BaseCommand):
    help = "Delete staff audit entries older than AUDIT_RETENTION_MONTHS (or --months). Run it now and then; there is no scheduler."

    def add_arguments(self, parser):
        parser.add_argument("--months", type=int, default=None, help="override AUDIT_RETENTION_MONTHS")
        parser.add_argument("--dry-run", action="store_true", help="only say how many would be deleted")

    def handle(self, *args, months=None, dry_run=False, **options):
        months = settings.AUDIT_RETENTION_MONTHS if months is None else months
        if months <= 0:
            self.stdout.write("Retention is 0 months, which means keep everything. Nothing to do.")
            return
        if dry_run:
            self.stdout.write(
                f"{audit.expired(months).count()} entries are older than {months} months (dry run, none deleted)."
            )
            return
        self.stdout.write(f"Deleted {audit.purge(months=months)} entries older than {months} months.")
