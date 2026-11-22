import hashlib
import hmac
import logging
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone

from .models import Lead

logger = logging.getLogger(__name__)


def visitor_hash(request) -> str:
    """A keyed hash of the visitor's IP. Enough to rate-limit, not enough to identify anyone from a database leak."""
    ip = request.META.get("REMOTE_ADDR", "")
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        ip = forwarded.split(",")[0].strip()  # the proxy sets this; gunicorn only trusts it from forwarded-allow-ips
    return hmac.new(settings.SECRET_KEY.encode(), ip.encode(), hashlib.sha256).hexdigest()


def is_rate_limited(visitor: str) -> bool:
    since = timezone.now() - timedelta(hours=1)
    return Lead.objects.filter(visitor_hash=visitor, created_at__gte=since).count() >= settings.LEAD_RATE_LIMIT_PER_HOUR


@transaction.atomic
def create_lead(form, request) -> Lead:
    lead = form.save(commit=False)
    lead.consent_at = timezone.now()
    lead.notice_version = settings.PRIVACY_NOTICE_VERSION
    lead.visitor_hash = visitor_hash(request)
    lead.source_page = request.POST.get("source", "")[:200]
    for key in ("utm_source", "utm_medium", "utm_campaign"):
        setattr(lead, key, request.POST.get(key, "")[:120])
    lead.save()
    transaction.on_commit(lambda: notify(lead))  # e-mail only once the row is safely saved
    return lead


def notify(lead: Lead) -> None:
    """Tell sales, and acknowledge the enquirer. A mail failure is logged, never shown to the visitor: the lead is saved."""
    context = {
        "lead": lead,
        "site_url": settings.SITE_URL,
        "brand": settings.BRAND_NAME,
        "admin_url": settings.ADMIN_URL,
    }
    try:
        if settings.SALES_NOTIFY_EMAILS:
            send_mail(
                f"New Funera enquiry: {lead.business_name}",
                render_to_string("emails/lead_notification.txt", context),
                settings.DEFAULT_FROM_EMAIL,
                settings.SALES_NOTIFY_EMAILS,
                fail_silently=False,
            )
        send_mail(
            "We received your enquiry",
            render_to_string("emails/lead_ack.txt", context),
            settings.DEFAULT_FROM_EMAIL,
            [lead.email],
            fail_silently=False,
        )
    except Exception:  # noqa: BLE001 - never lose the lead because the mail server is down
        logger.exception("Could not send enquiry e-mails for lead %s", lead.pk)


def purge_expired(today=None) -> int:
    """Delete enquiries older than LEAD_RETENTION_MONTHS that never became customers (POPIA: do not keep data longer than needed)."""
    cutoff = (today or timezone.now()) - timedelta(days=int(settings.LEAD_RETENTION_MONTHS * 30.44))
    deleted, _ = Lead.objects.filter(created_at__lt=cutoff).exclude(status=Lead.Status.WON).delete()
    return deleted
