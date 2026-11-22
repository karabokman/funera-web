"""Passwordless sign-in for clients: a single-use link e-mailed to the address on the subscription.

No passwords are stored, so there is nothing to leak or reset. The link proves control of the e-mail address; what a
signed-in client can see is every non-pending subscription whose customer e-mail matches it.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from .models import LoginToken, Subscription

logger = logging.getLogger(__name__)
SESSION_KEY = "portal_email"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def normalise(email: str) -> str:
    return email.strip().lower()


def subscriptions_for(email: str):
    return (
        Subscription.objects.filter(customer__email__iexact=email)
        .exclude(status=Subscription.Status.PENDING)
        .select_related("plan", "customer")
    )


def request_link(email: str, visitor: str) -> None:
    """Send a sign-in link if this address has subscriptions. The caller shows the same message either way, so the form
    cannot be used to find out who the clients are. Per-visitor and per-address limits stop it being used to spam anyone."""
    email = normalise(email)
    since = timezone.now() - timedelta(hours=1)
    if (
        LoginToken.objects.filter(visitor_hash=visitor, created_at__gte=since).count()
        >= settings.PORTAL_LOGIN_RATE_PER_HOUR
    ):
        return
    if LoginToken.objects.filter(email=email, created_at__gte=since).count() >= 3:
        return
    LoginToken.objects.create(
        email=email, token_hash=_hash(secrets.token_hex(8)), visitor_hash=visitor, used_at=timezone.now()
    )  # counts the attempt, cannot be used
    if not subscriptions_for(email).exists():
        return
    token = secrets.token_urlsafe(32)
    LoginToken.objects.create(email=email, token_hash=_hash(token), visitor_hash=visitor)
    context = {
        "link": f"{settings.SITE_URL}{reverse('billing:portal_login_confirm', args=[token])}",
        "minutes": settings.PORTAL_LOGIN_MINUTES,
        "brand": settings.BRAND_NAME,
    }

    def send():
        try:
            send_mail(
                f"Your {settings.BRAND_NAME} sign-in link",
                render_to_string("emails/portal_login.txt", context),
                settings.DEFAULT_FROM_EMAIL,
                [email],
            )
        except Exception:  # noqa: BLE001
            logger.exception("Could not send a portal sign-in link")

    transaction.on_commit(send)


def redeem(token: str) -> str | None:
    """Turn a link token into the e-mail address it was issued for, once. Expired, used or unknown tokens give None."""
    oldest = timezone.now() - timedelta(minutes=settings.PORTAL_LOGIN_MINUTES)
    row = LoginToken.objects.filter(token_hash=_hash(token), used_at__isnull=True, created_at__gte=oldest).first()
    if row is None:
        return None
    claimed = LoginToken.objects.filter(pk=row.pk, used_at__isnull=True).update(
        used_at=timezone.now()
    )  # two clicks cannot both win
    return row.email if claimed else None


def sign_in(request, email: str) -> None:
    request.session.cycle_key()  # a new session id at sign-in prevents session fixation
    request.session[SESSION_KEY] = email
    request.session.set_expiry(settings.PORTAL_SESSION_HOURS * 3600)


def sign_out(request) -> None:
    request.session.pop(SESSION_KEY, None)
    request.session.cycle_key()
