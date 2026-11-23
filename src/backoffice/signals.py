from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from . import audit


@receiver(user_logged_in)
def signed_in(sender, request, user, **kwargs):
    if user.is_staff:
        audit.record(request, "staff.signed_in", user=user)


@receiver(user_logged_out)
def signed_out(sender, request, user, **kwargs):
    if user is not None and user.is_staff:
        audit.record(request, "staff.signed_out", user=user)


@receiver(user_login_failed)
def sign_in_failed(sender, credentials, request=None, **kwargs):
    """Failed attempts are recorded with the username that was typed (never the password)."""
    typed = str(credentials.get("username", ""))[:150]
    audit.record(request, "staff.sign_in_failed", detail=f"username: {typed}", label=typed)
