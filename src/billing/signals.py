from django.dispatch import Signal

# Sent once, when a subscription's first payment is confirmed. This is the hook for provisioning a client's platform:
# a receiver (see docs/SUBSCRIPTIONS.md) can create the instance without this app knowing how. Argument: ``subscription``.
subscription_activated = Signal()
