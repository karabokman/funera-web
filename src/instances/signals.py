import logging

from django.dispatch import receiver

from billing.models import Subscription
from billing.signals import subscription_activated

from .models import Instance

logger = logging.getLogger(__name__)


@receiver(subscription_activated)
def create_instance_record(sender, subscription: Subscription, **kwargs):
    """The first payment is in, so a platform now has to be set up for this client. Open a record for it (once), so it
    shows on the dashboard until someone has made it live."""
    _, created = Instance.objects.get_or_create(subscription=subscription, defaults={"customer": subscription.customer})
    if created:
        logger.info("Instance record opened for %s", subscription.reference)
