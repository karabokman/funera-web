"""Builds the rows of the audit page from three places and puts them in time order:

* ``AuditEntry``: what staff did, with the real username and address.
* ``SubscriptionEvent`` / ``QuoteEvent`` written by clients, PayFast or the system (a payment arriving, a quotation accepted).
* Older staff events recorded before the audit log existed (they only say "staff", never who).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from django.urls import reverse
from django.utils import timezone

from billing.models import SubscriptionEvent
from quotes.models import QuoteEvent

from . import audit
from .models import AuditEntry

LIMIT = 1000  # newest rows kept per source; the page says when this was reached
SOURCES = [("staff", "Staff"), ("client", "Clients and system")]
CLIENT_ACTORS = ("client", "customer", "payfast", "system")


@dataclass
class Filters:
    who: str = ""
    area: str = ""
    source: str = ""
    start: date | None = None
    end: date | None = None
    q: str = ""

    @classmethod
    def from_request(cls, params) -> Filters:
        def day(name):
            try:
                return date.fromisoformat(params.get(name, ""))
            except ValueError:
                return None

        area = params.get("area", "")
        source = params.get("source", "")
        return cls(
            who=params.get("who", "").strip()[:150],
            area=area if area in {a[0] for a in audit.AREAS} else "",
            source=source if source in {s[0] for s in SOURCES} else "",
            start=day("from"),
            end=day("to"),
            q=params.get("q", "").strip()[:100],
        )

    @property
    def active(self) -> bool:
        return any([self.who, self.area, self.source, self.start, self.end, self.q])

    def window(self):
        start = timezone.make_aware(datetime.combine(self.start, time.min)) if self.start else None
        end = timezone.make_aware(datetime.combine(self.end + timedelta(days=1), time.min)) if self.end else None
        return start, end


def staff_names() -> list[str]:
    return list(AuditEntry.objects.exclude(actor_name="").order_by().values_list("actor_name", flat=True).distinct())


def _row(when, who, what, kind, pk, label, detail, source, ip=""):
    url = ""
    if pk:
        name = {
            "lead": "lead",
            "quote": "quote",
            "subscription": "subscription",
            "offer": "offer_edit",
            "customer": "customer",
            "instance": "instance",
        }.get(kind)
        url = reverse(f"backoffice:{name}", args=[pk]) if name else ""
    return {
        "when": when,
        "who": who,
        "what": what,
        "type": kind,
        "label": label,
        "detail": detail,
        "source": source,
        "ip": ip,
        "url": url,
    }


def rows(f: Filters) -> list[dict]:
    start, end = f.window()

    def windowed(qs):
        if start:
            qs = qs.filter(created_at__gte=start)
        if end:
            qs = qs.filter(created_at__lt=end)
        return qs

    result: list[dict] = []
    types = next((t for value, _, t in audit.AREAS if value == f.area), None)

    if f.source in ("", "staff"):
        qs = windowed(AuditEntry.objects.all())
        if f.who:
            qs = qs.filter(actor_name__icontains=f.who)
        if types:
            qs = qs.filter(object_type__in=types)
        if f.q:
            qs = (
                qs.filter(object_label__icontains=f.q)
                | qs.filter(detail__icontains=f.q)
                | qs.filter(action__icontains=f.q)
            )
        for e in qs.order_by("-created_at")[:LIMIT]:
            who = e.actor_name or "unknown"
            what = audit.ACTIONS.get(e.action, e.action)
            result.append(
                _row(
                    e.created_at,
                    who,
                    what,
                    e.object_type,
                    e.object_id,
                    e.object_label,
                    e.detail,
                    "staff",
                    e.ip_address or "",
                )
            )

    if f.source in ("", "client"):
        first = AuditEntry.objects.order_by("created_at").values_list("created_at", flat=True).first()
        events = _events(f, windowed, first, types)
        if f.who:
            events = [r for r in events if f.who.lower() in r["who"].lower()]
        result += events

    result.sort(key=lambda r: r["when"], reverse=True)
    return result


def _events(f: Filters, windowed, first_audit, types) -> list[dict]:
    """Client, PayFast and system events, plus staff events from before the audit log started (who is then unknown)."""
    out: list[dict] = []

    def match(*texts):
        return not f.q or any(f.q.lower() in (t or "").lower() for t in texts)

    if types is None or "subscription" in types:
        qs = windowed(SubscriptionEvent.objects.select_related("subscription__customer"))
        for e in qs.order_by("-created_at")[:LIMIT]:
            legacy_staff = e.actor == SubscriptionEvent.Actor.STAFF
            if legacy_staff and first_audit is not None and e.created_at >= first_audit:
                continue  # already in the audit log with the real name
            if not legacy_staff and e.actor not in CLIENT_ACTORS:
                continue
            label = f"{e.subscription.reference} · {e.subscription.customer.business_name}"
            if not match(label, e.detail, e.kind):
                continue
            who = "staff (before the audit log)" if legacy_staff else e.get_actor_display().lower()
            out.append(
                _row(
                    e.created_at,
                    who,
                    e.kind.replace("_", " ").capitalize(),
                    "subscription",
                    e.subscription_id,
                    label,
                    e.detail,
                    "client",
                )
            )
    if types is None or "quote" in types:
        qs = windowed(QuoteEvent.objects.select_related("quote"))
        for e in qs.order_by("-created_at")[:LIMIT]:
            client = e.actor == "client"
            if not client and (first_audit is None or e.created_at >= first_audit):
                continue  # staff event already in the audit log with the real name (or a system note)
            label = f"{e.quote.number} · {e.quote.business_name}"
            if not match(label, e.detail, e.kind):
                continue
            who = "client" if client else f"{e.actor or 'staff'} (before the audit log)"
            out.append(
                _row(
                    e.created_at,
                    who,
                    e.kind.replace("_", " ").capitalize(),
                    "quote",
                    e.quote_id,
                    label,
                    e.detail,
                    "client",
                )
            )
    return out
