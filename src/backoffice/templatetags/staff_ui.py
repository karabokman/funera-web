from django import template

from backoffice import followups, roles

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
    # hosted instances
    "awaiting": "warn", "setting_up": "info", "live": "good", "suspended": "bad", "retired": "neutral",
}  # fmt: skip


@register.filter
def tone(status) -> str:
    """The badge colour for a status value (see ``_badge.html``)."""
    return TONES.get(str(status).lower(), "neutral")


@register.simple_tag
def follow_up_badge() -> int:
    """How many follow-ups are due today or overdue, for the navigation."""
    return followups.due_count()


@register.filter
def is_admin(user) -> bool:
    """True for the Admin role (superuser or the Funera Admin group)."""
    return roles.is_admin(user)


@register.filter
def role_name(user) -> str:
    return roles.role(user)


@register.simple_tag(takes_context=True)
def keep_filters(context, *names) -> str:
    """The current query string minus ``page``, for pagination links on pages with several filters."""
    request = context["request"]
    params = request.GET.copy()
    params.pop("page", None)
    return (params.urlencode() + "&") if params else ""
