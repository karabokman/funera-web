"""The audit log: who did what, and when. ``record()`` is called by the back-office views (and the sign-in signals) after an
action has succeeded, so the log shows what actually happened rather than what was attempted."""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.utils import timezone

from billing.models import Customer, Refund, Subscription
from billing.services import client_ip
from instances.models import Instance
from leads.models import Lead
from quotes.models import Quote, QuoteTemplate

from .models import AuditEntry

# action code -> the sentence shown on the audit page
ACTIONS = {
    "staff.signed_in": "Signed in",
    "staff.signed_out": "Signed out",
    "staff.sign_in_failed": "Failed sign-in",
    "lead.updated": "Updated lead",
    "followup.set": "Set a follow-up",
    "followup.done": "Completed a follow-up",
    "followup.snooze": "Moved a follow-up",
    "quote.created": "Created quotation",
    "quote.edited": "Edited quotation",
    "quote.sent": "Sent quotation",
    "quote.resent": "Re-sent quotation",
    "quote.cancelled": "Cancelled quotation",
    "quote.duplicated": "Duplicated quotation",
    "offer.created": "Created saved offer",
    "offer.edited": "Edited saved offer",
    "offer.deleted": "Deleted saved offer",
    "instance.created": "Added hosted instance",
    "instance.edited": "Edited hosted instance",
    "instance.live": "Marked instance live",
    "instance.version_updated": "Recorded an instance update",
    "subscription.paused": "Paused subscription",
    "subscription.resumed": "Resumed subscription",
    "subscription.cancelled": "Cancelled subscription",
    "subscription.refund_raised": "Raised a refund",
    "refund.processed": "Processed refund",
    "refund.failed": "Refund failed at PayFast",
    "refund.declined": "Declined refund",
    "user.created": "Created a user (site admin)",
    "user.changed": "Changed a user (site admin)",
    "user.deleted": "Deleted a user (site admin)",
    "group.created": "Created a group (site admin)",
    "group.changed": "Changed a group (site admin)",
    "group.deleted": "Deleted a group (site admin)",
    "audit.purged": "Deleted old audit entries",
}

# the areas offered as a filter on the audit page: (value, label, object types it covers)
AREAS = [
    ("lead", "Leads and follow-ups", ("lead",)),
    ("quote", "Quotations", ("quote",)),
    ("offer", "Saved offers", ("offer",)),
    ("subscription", "Subscriptions", ("subscription",)),
    ("instance", "Hosted instances", ("instance",)),
    ("refund", "Refunds", ("refund",)),
    ("user", "Users and roles", ("user", "group")),
    ("staff", "Sign-ins and log clean-up", ("staff",)),
]


def describe(obj) -> tuple[str, int | None, str]:
    """(object type, id, label) for anything the back-office can act on."""
    if isinstance(obj, Lead):
        return "lead", obj.pk, obj.business_name
    if isinstance(obj, Quote):
        return "quote", obj.pk, f"{obj.number} · {obj.business_name}"
    if isinstance(obj, QuoteTemplate):
        return "offer", obj.pk, obj.name
    if isinstance(obj, Subscription):
        return "subscription", obj.pk, f"{obj.reference} · {obj.customer.business_name}"
    if isinstance(obj, Refund):
        return "refund", obj.pk, f"R{obj.amount} on {obj.payment.subscription.reference}"
    if isinstance(obj, Instance):
        return "instance", obj.pk, f"{obj.customer.business_name} (hosted instance)"
    if isinstance(obj, Customer):
        return "customer", obj.pk, obj.business_name
    if isinstance(obj, get_user_model()):
        return "user", obj.pk, obj.get_username()
    if isinstance(obj, Group):
        return "group", obj.pk, obj.name
    return "", None, str(obj) if obj is not None else ""


def record(request, action: str, obj=None, detail: str = "", *, user=None, label: str = "") -> AuditEntry:
    user = user if user is not None else getattr(request, "user", None)
    if user is not None and not getattr(user, "is_authenticated", False):
        user = None
    kind, pk, auto_label = describe(obj)
    if action.startswith(("staff.", "audit.")):
        kind = "staff"
    return AuditEntry.objects.create(
        actor=user,
        actor_name=(user.get_username() if user is not None else "")[:150],
        action=action,
        object_type=kind,
        object_id=pk,
        object_label=(label or auto_label)[:200],
        detail=detail[:300],
        ip_address=(client_ip(request) or None) if request is not None else None,
    )


def retention_cutoff(months: int | None = None):
    months = settings.AUDIT_RETENTION_MONTHS if months is None else months
    return timezone.now() - timedelta(days=int(months) * 30)


def expired(months: int | None = None):
    """Staff audit entries older than the retention period (0 months means keep everything)."""
    if (settings.AUDIT_RETENTION_MONTHS if months is None else months) <= 0:
        return AuditEntry.objects.none()
    return AuditEntry.objects.filter(created_at__lt=retention_cutoff(months))


def purge(request=None, months: int | None = None, *, user=None) -> int:
    """Delete entries past the retention period, then log that it happened (the new entry is not itself deleted)."""
    count, _ = expired(months).delete()
    if count:
        months = settings.AUDIT_RETENTION_MONTHS if months is None else months
        record(request, "audit.purged", detail=f"{count} entries older than {months} months", user=user)
    return count
