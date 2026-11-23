"""Follow-up reminders. There is no scheduler on the host, so a reminder is simply a date on a lead or quotation and a list
that staff open: what is overdue, what is due today, what is coming, and what has no date at all (so nothing hides)."""

from __future__ import annotations

from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from leads.models import Lead
from quotes.models import Quote, QuoteEvent

OPEN_LEAD = [Lead.Status.NEW, Lead.Status.CONTACTED, Lead.Status.DEMO, Lead.Status.QUOTED]
OPEN_QUOTE = [Quote.Status.DRAFT, Quote.Status.SENT]
SNOOZE_DAYS = (1, 3, 7)
KINDS = {"lead": Lead, "quote": Quote}


def _when(due, today) -> str:
    if due is None:
        return "No date"
    days = (due - today).days
    if days < 0:
        return f"{-days} day{'s' if days < -1 else ''} overdue"
    return {0: "Today", 1: "Tomorrow"}.get(days, f"In {days} days")


def _item(kind: str, obj, today) -> dict:
    if kind == "lead":
        title, subtitle, url = obj.business_name, obj.name, reverse("backoffice:lead", args=[obj.pk])
        default = "Make first contact" if obj.status == Lead.Status.NEW else "Get in touch"
    else:
        title, subtitle = f"{obj.number} · {obj.business_name}", obj.contact_name
        url, default = reverse("backoffice:quote", args=[obj.pk]), "Follow up on quotation"
    return {
        "kind": kind, "obj": obj, "pk": obj.pk, "title": title, "subtitle": subtitle, "url": url,
        "due": obj.follow_up_on, "action": obj.next_action or default, "when": _when(obj.follow_up_on, today),
        "status": obj.get_status_display(),
        "tone": "neutral" if not obj.follow_up_on else "bad" if obj.follow_up_on < today else "warn" if obj.follow_up_on == today else "neutral",
    }  # fmt: skip


def collect(today=None) -> dict:
    """Every open lead and quotation that has, or ought to have, a follow-up date, in buckets."""
    today = today or timezone.localdate()
    items = [_item("lead", lead, today) for lead in Lead.objects.filter(status__in=OPEN_LEAD)]
    quotes = Quote.objects.filter(status__in=OPEN_QUOTE).select_related("plan")
    items += [_item("quote", q, today) for q in quotes if q.follow_up_on or q.status == Quote.Status.SENT]
    dated = sorted((i for i in items if i["due"]), key=lambda i: (i["due"], i["title"]))
    week = today + timedelta(days=7)
    buckets = {
        "overdue": [i for i in dated if i["due"] < today],
        "today": [i for i in dated if i["due"] == today],
        "soon": [i for i in dated if today < i["due"] <= week],
        "later": [i for i in dated if i["due"] > week],
        "undated": sorted((i for i in items if not i["due"]), key=lambda i: i["title"]),
    }
    buckets["due_count"] = len(buckets["overdue"]) + len(buckets["today"])
    return buckets


def due_count(today=None) -> int:
    """Cheap count for the navigation badge: open items due today or earlier."""
    today = today or timezone.localdate()
    return (
        Lead.objects.filter(status__in=OPEN_LEAD, follow_up_on__lte=today).count()
        + Quote.objects.filter(status__in=OPEN_QUOTE, follow_up_on__lte=today).count()
    )


def clear(obj) -> None:
    obj.follow_up_on, obj.next_action = None, ""


def apply(kind: str, obj, *, action: str, user, follow_up_on=None, next_action: str = "") -> str:
    """Change a follow-up and return a short message for the staff member. ``action`` is set, done or snooze:N."""
    today = timezone.localdate()
    who = str(user)
    if action == "done":
        clear(obj)
        message = "Marked done."
        if kind == "lead" and obj.status == Lead.Status.NEW:
            obj.status, obj.contacted_at = Lead.Status.CONTACTED, timezone.now()
            message = "Marked done. The lead is now Contacted. Set the next follow-up if there is one."
        detail = "done"
    elif (
        action.startswith("snooze:")
        and action.split(":", 1)[1].isdigit()
        and int(action.split(":", 1)[1]) in SNOOZE_DAYS
    ):
        days = int(action.split(":", 1)[1])
        obj.follow_up_on = today + timedelta(days=days)
        message = f"Follow-up moved to {obj.follow_up_on:%a %d %b}."
        detail = f"snoozed to {obj.follow_up_on:%d %b %Y}"
    elif action == "set":
        obj.follow_up_on, obj.next_action = follow_up_on, next_action.strip()[:200]
        message = f"Follow-up set for {follow_up_on:%a %d %b}." if follow_up_on else "Follow-up cleared."
        detail = f"set to {follow_up_on:%d %b %Y}: {obj.next_action}" if follow_up_on else "cleared"
    else:
        raise ValueError("Unknown follow-up action")
    obj.save()
    if kind == "quote":
        QuoteEvent.objects.create(quote=obj, kind="follow_up", detail=detail[:300], actor=who[:60])
    return message
