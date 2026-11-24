"""Builds the rows of the audit page from three places and puts them in time order:

* ``AuditEntry``: what staff did, with the real username and address.
* ``SubscriptionEvent`` / ``QuoteEvent`` written by clients, PayFast or the system (a payment arriving, a quotation accepted).
* Older staff events recorded before the audit log existed (they only say "staff", never who).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from billing.models import SubscriptionEvent
from quotes.models import QuoteEvent

from . import audit
from .models import AuditEntry

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


class Feed:
    """The audit page's rows, newest first, from all three sources, without loading everything.

    Counting and filtering happen in the database. Showing page N takes the newest N x page-size rows of each source and
    merges them, so every entry can be reached (there is no cap), and the cost grows only with how deep you page.
    """

    def __init__(self, f: Filters):
        self.f = f
        start, end = f.window()
        self.start, self.end = start, end
        self.types = next((t for value, _, t in audit.AREAS if value == f.area), None)
        self.first = AuditEntry.objects.order_by("created_at").values_list("created_at", flat=True).first()
        self.staff = self._staff_qs() if f.source in ("", "staff") else None
        self.subs = (
            self._sub_qs()
            if f.source in ("", "client") and (self.types is None or "subscription" in self.types)
            else None
        )
        self.quotes = (
            self._quote_qs() if f.source in ("", "client") and (self.types is None or "quote" in self.types) else None
        )
        self._count: int | None = None

    def _window(self, qs):
        if self.start:
            qs = qs.filter(created_at__gte=self.start)
        if self.end:
            qs = qs.filter(created_at__lt=self.end)
        return qs

    def _staff_qs(self):
        f, qs = self.f, self._window(AuditEntry.objects.all())
        if f.who:
            qs = qs.filter(actor_name__icontains=f.who)
        if self.types:
            qs = qs.filter(object_type__in=self.types)
        if f.q:
            qs = qs.filter(Q(object_label__icontains=f.q) | Q(detail__icontains=f.q) | Q(action__icontains=f.q))
        return qs.order_by("-created_at", "-id")

    def _sub_qs(self):
        """Client, PayFast and system events, plus staff events from before the audit log existed (who is then unknown)."""
        f = self.f
        legacy = Q(actor=SubscriptionEvent.Actor.STAFF)
        if self.first is not None:
            legacy &= Q(created_at__lt=self.first)  # later staff events are already in the audit log with the real name
        qs = self._window(SubscriptionEvent.objects.select_related("subscription__customer")).filter(
            Q(actor__in=CLIENT_ACTORS) | legacy
        )
        if f.who:
            qs = qs.filter(actor__icontains=f.who)
        if f.q:
            qs = qs.filter(
                Q(subscription__reference__icontains=f.q)
                | Q(subscription__customer__business_name__icontains=f.q)
                | Q(detail__icontains=f.q)
                | Q(kind__icontains=f.q)
            )
        return qs.order_by("-created_at", "-id")

    def _quote_qs(self):
        f = self.f
        keep = Q(actor="client")
        if self.first is not None:
            keep |= Q(created_at__lt=self.first)
        qs = self._window(QuoteEvent.objects.select_related("quote")).filter(keep)
        if f.who:
            qs = qs.filter(actor__icontains=f.who)
        if f.q:
            qs = qs.filter(
                Q(quote__number__icontains=f.q)
                | Q(quote__business_name__icontains=f.q)
                | Q(detail__icontains=f.q)
                | Q(kind__icontains=f.q)
            )
        return qs.order_by("-created_at", "-id")

    def __len__(self) -> int:
        if self._count is None:
            self._count = sum(qs.count() for qs in (self.staff, self.subs, self.quotes) if qs is not None)
        return self._count

    def __getitem__(self, key):
        if not isinstance(key, slice):
            return self[key : key + 1][0]
        start, stop, _ = key.indices(len(self))
        return self._merged(stop)[start:stop]

    def _merged(self, upto: int) -> list[dict]:
        out: list[dict] = []
        if self.staff is not None:
            for e in self.staff[:upto]:
                out.append(
                    _row(
                        e.created_at,
                        e.actor_name or "unknown",
                        audit.ACTIONS.get(e.action, e.action),
                        e.object_type,
                        e.object_id,
                        e.object_label,
                        e.detail,
                        "staff",
                        e.ip_address or "",
                    )
                )
        if self.subs is not None:
            for e in self.subs[:upto]:
                label = f"{e.subscription.reference} · {e.subscription.customer.business_name}"
                legacy = e.actor == SubscriptionEvent.Actor.STAFF
                who = "staff (before the audit log)" if legacy else e.get_actor_display().lower()
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
        if self.quotes is not None:
            for e in self.quotes[:upto]:
                label = f"{e.quote.number} · {e.quote.business_name}"
                who = "client" if e.actor == "client" else f"{e.actor or 'staff'} (before the audit log)"
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
        out.sort(key=lambda r: r["when"], reverse=True)
        return out


def rows(f: Filters) -> list[dict]:
    """Every matching row (used by tests and small exports; the page itself pages through ``Feed``)."""
    feed = Feed(f)
    return feed[0 : len(feed)]
