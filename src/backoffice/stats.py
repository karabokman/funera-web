"""Numbers for the staff dashboard. One function, plain queries, no caching: the data set is small and staff want it current."""

from datetime import timedelta
from decimal import Decimal

from django.db.models import Count
from django.utils import timezone

from billing.models import Refund, Subscription, SubscriptionEvent
from leads.models import Lead
from quotes.models import Quote, QuoteEvent

from . import followups

Status = Subscription.Status


def monthly_recurring_revenue() -> Decimal:
    """Each live subscription's recurring amount expressed per month (an annual one counts one twelfth)."""
    live = Subscription.objects.filter(status__in=[Status.ACTIVE, Status.PAST_DUE])
    return sum((s.recurring_amount / s.months_per_period for s in live), Decimal("0")).quantize(Decimal("0.01"))


def dashboard(money: bool = True) -> dict:
    """``money`` False leaves out every revenue figure (what Sales staff may see)."""
    today = timezone.localdate()
    now = timezone.now()
    sent = [q for q in Quote.objects.filter(status=Quote.Status.SENT).select_related("plan")]
    open_quotes = [q for q in sent if not q.is_expired]
    week = today + timedelta(days=7)
    activity = [
        {
            "when": e.created_at,
            "text": f"{e.subscription.customer.business_name}: {e.kind.replace('_', ' ')}",
            "link": ("subscription", e.subscription_id),
        }
        for e in SubscriptionEvent.objects.select_related("subscription__customer")[:8]
    ] + [
        {
            "when": e.created_at,
            "text": f"{e.quote.number} {e.quote.business_name}: {e.kind.replace('_', ' ')}",
            "link": ("quote", e.quote_id),
        }
        for e in QuoteEvent.objects.select_related("quote")[:8]
    ]
    activity.sort(key=lambda row: row["when"], reverse=True)
    counts = {row["status"]: row["n"] for row in Subscription.objects.values("status").annotate(n=Count("id"))}
    result = {
        "follow_ups": followups.collect(today),
        "new_leads": Lead.objects.filter(status=Lead.Status.NEW).count(),
        "leads_30d": Lead.objects.filter(created_at__gte=now - timedelta(days=30)).count(),
        "draft_quotes": Quote.objects.filter(status=Quote.Status.DRAFT).count(),
        "open_quotes": len(open_quotes),
        "expiring_quotes": [q for q in open_quotes if q.valid_until <= week],
        "stale_quotes": [q for q in open_quotes if q.sent_at and q.sent_at < now - timedelta(days=5)],
        "accepted_unpaid": Subscription.objects.filter(status=Status.PENDING, quote__isnull=False).count(),
        "subscriptions": {
            label: counts.get(key, 0)
            for key, label in [
                (Status.ACTIVE, "Active"),
                (Status.PAST_DUE, "Payment failed"),
                (Status.PAUSED, "Paused"),
                (Status.PENDING, "Awaiting first payment"),
                (Status.CANCELLED, "Cancelled"),
            ]
        },
        "funnel": [(label, Lead.objects.filter(status=key).count()) for key, label in Lead.Status.choices],
        "activity": activity[:10],
    }
    if money:
        result["open_quote_value"] = sum((q.monthly_equivalent for q in open_quotes), Decimal("0"))
        result["mrr"] = monthly_recurring_revenue()
        result["refunds_waiting"] = Refund.objects.filter(status=Refund.Status.REQUESTED).count()
    return result
