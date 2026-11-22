from django.core.management.base import BaseCommand

from leads.services import purge_expired


class Command(BaseCommand):
    help = "Delete enquiries older than LEAD_RETENTION_MONTHS (customers that were won are kept). Run it monthly from a scheduled task."

    def handle(self, *args, **options):
        self.stdout.write(f"Deleted {purge_expired()} old enquiries.")
