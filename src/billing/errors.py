class BillingError(Exception):
    """A request the rules do not allow (for example pausing a cancelled subscription). The message is safe to show the client."""
