from django import template

register = template.Library()

TONES = {
    # leads
    "new": "info", "contacted": "neutral", "demo": "info", "quoted": "warn", "won": "good", "lost": "neutral",
    # quotes
    "draft": "neutral", "sent": "info", "accepted": "good", "declined": "bad", "cancelled": "neutral", "expired": "warn",
    # subscriptions
    "pending": "warn", "active": "good", "past_due": "bad", "paused": "warn",
    # refunds
    "requested": "warn", "processed": "good", "failed": "bad",
}  # fmt: skip


@register.filter
def tone(status) -> str:
    """The badge colour for a status value (see ``_badge.html``)."""
    return TONES.get(str(status).lower(), "neutral")
