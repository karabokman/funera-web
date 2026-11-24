"""Business reports: sales funnel, quotation results, time to close, recurring revenue, churn and cash collected.

Everything is calculated from the records already held (leads, quotations, subscriptions, payments, instances) with plain
queries, for a period the Admin chooses. There is no stored history of prices or plan changes, so the revenue trend is a
*reconstruction* from activation and cancellation dates at today's prices; the page says so.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from statistics import mean, median

from django.conf import settings
from django.db.models import Count
from django.utils import timezone

from billing.models import Payment, Refund, Subscription
from instances.models import Instance
from leads.models import Lead
from quotes.models import Quote

CENT = Decimal("0.01")
MAX_MONTHS = 24
LIVE = (Subscription.Status.ACTIVE, Subscription.Status.PAST_DUE)


def money(value) -> Decimal:
    return Decimal(value or 0).quantize(CENT, ROUND_HALF_UP)


def excl_vat(gross) -> Decimal | None:
    """The amount without VAT, or None when no VAT number is configured (then nothing is split out)."""
    if not settings.VAT_NUMBER:
        return None
    return money(Decimal(gross) / (1 + Decimal(str(settings.VAT_RATE))))


def pct(part, whole) -> float | None:
    return round(float(100 * part / whole), 1) if whole else None


def monthly(amount, period) -> Decimal:
    return Decimal(amount) / (12 if period == "annual" else 1)


def summarise(days: list[float]) -> dict:
    if not days:
        return {"n": 0, "mean": None, "median": None}
    return {"n": len(days), "mean": round(float(mean(days)), 1), "median": round(float(median(days)), 1)}


def local_date(moment: datetime | None) -> date | None:
    return timezone.localtime(moment).date() if moment else None


def days_between(start: datetime | None, end: datetime | None) -> float | None:
    if not start or not end:
        return None
    return max((end - start).total_seconds() / 86400, 0.0)


# ------------------------------------------------------------------------------------------------ the period
def month_start(today: date, count: int) -> date:
    """The first day of the month that begins a run of ``count`` months ending with today's month."""
    index = today.year * 12 + today.month - 1 - (count - 1)
    return date(index // 12, index % 12 + 1, 1)


@dataclass(frozen=True)
class Period:
    start: date
    end: date  # inclusive

    @classmethod
    def from_params(cls, params, today: date | None = None) -> Period:
        """The chosen dates, defaulting to the last twelve months. Never reaches past today; reversed dates are swapped."""
        today = today or timezone.localdate()

        def one(key):
            try:
                return date.fromisoformat((params.get(key) or "").strip())
            except ValueError:
                return None

        start, end = one("from") or month_start(today, 12), one("to") or today
        if start > end:
            start, end = end, start
        end = min(end, today)
        return cls(min(start, end), end)

    def months(self) -> list[date]:
        """First day of each calendar month touched by the period (the most recent MAX_MONTHS)."""
        out, cursor = [], self.start.replace(day=1)
        while cursor <= self.end:
            out.append(cursor)
            cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
        return out[-MAX_MONTHS:]


def next_month(day: date) -> date:
    return (day.replace(day=28) + timedelta(days=4)).replace(day=1)


# ------------------------------------------------------------------------------------------------ funnel
def funnel(period: Period) -> dict:
    """Leads that arrived in the period and where they stand now."""
    leads = Lead.objects.filter(created_at__date__gte=period.start, created_at__date__lte=period.end)
    total = leads.count()
    by_status = {row["status"]: row["n"] for row in leads.values("status").annotate(n=Count("id"))}
    won, lost = by_status.get(Lead.Status.WON, 0), by_status.get(Lead.Status.LOST, 0)
    quoted = leads.filter(quotes__sent_at__isnull=False).distinct().count()
    return {
        "total": total,
        "contacted": total - by_status.get(Lead.Status.NEW, 0),
        "quoted": quoted,
        "won": won,
        "lost": lost,
        "open": total - won - lost,
        "win_rate": pct(won, won + lost),
        "conversion": pct(won, total),
        "by_status": [(label, by_status.get(key, 0)) for key, label in Lead.Status.choices],
    }


def sources(period: Period) -> list[dict]:
    """Where leads came from and how well each source converts."""
    rows: dict[str, dict] = {}
    leads = Lead.objects.filter(created_at__date__gte=period.start, created_at__date__lte=period.end)
    for lead in leads.only("utm_source", "status").prefetch_related("quotes"):
        name = lead.utm_source.strip().lower() or "Direct / not tracked"
        row = rows.setdefault(name, {"source": name, "leads": 0, "quoted": 0, "won": 0, "lost": 0})
        row["leads"] += 1
        row["quoted"] += any(q.sent_at for q in lead.quotes.all())
        row["won"] += lead.status == Lead.Status.WON
        row["lost"] += lead.status == Lead.Status.LOST
    out = sorted(rows.values(), key=lambda r: (-r["leads"], r["source"]))
    for row in out:
        row["win_rate"] = pct(row["won"], row["won"] + row["lost"])
    return out


# ------------------------------------------------------------------------------------------------ quotations
def quote_outcome(quote: Quote) -> str:
    if quote.status == Quote.Status.ACCEPTED:
        return "Accepted"
    if quote.status == Quote.Status.DECLINED:
        return "Declined"
    if quote.status == Quote.Status.CANCELLED:
        return "Withdrawn"
    if quote.is_expired:
        return "Expired"
    return "Open"


def sent_quotes(period: Period):
    return (
        Quote.objects.filter(sent_at__date__gte=period.start, sent_at__date__lte=period.end)
        .select_related("plan", "lead")
        .order_by("sent_at", "id")
    )


def quotations(period: Period) -> dict:
    """Quotations *sent* in the period and how they turned out. The win rate counts only those with an outcome."""
    quotes = list(sent_quotes(period))
    counts = {"Accepted": 0, "Declined": 0, "Expired": 0, "Withdrawn": 0, "Open": 0}
    per_plan: dict[str, dict] = {}
    value_won = Decimal("0")
    decide_days: list[float] = []
    for q in quotes:
        outcome = quote_outcome(q)
        counts[outcome] += 1
        plan = per_plan.setdefault(
            q.plan.name, {"plan": q.plan.name, "sent": 0, "accepted": 0, "declined": 0, "expired": 0}
        )
        plan["sent"] += 1
        if outcome in ("Accepted", "Declined", "Expired"):
            plan[outcome.lower()] += 1
        if outcome == "Accepted":
            value_won += q.monthly_equivalent
            if (d := days_between(q.sent_at, q.decided_at)) is not None:
                decide_days.append(d)
    decided = counts["Accepted"] + counts["Declined"] + counts["Expired"]
    for plan in per_plan.values():
        plan["rate"] = pct(plan["accepted"], plan["accepted"] + plan["declined"] + plan["expired"])
    return {
        "sent": len(quotes),
        "counts": counts,
        "decided": decided,
        "win_rate": pct(counts["Accepted"], decided),
        "accepted_of_sent": pct(counts["Accepted"], len(quotes)),
        "value_won_monthly": money(value_won),
        "days_to_decide": summarise(decide_days),
        "per_plan": sorted(per_plan.values(), key=lambda p: -p["sent"]),
    }


# ------------------------------------------------------------------------------------------------ time to close
def time_to_close(period: Period) -> dict:
    """For quotations accepted in the period: how long each step took, in days."""
    accepted = Quote.objects.filter(
        status=Quote.Status.ACCEPTED, decided_at__date__gte=period.start, decided_at__date__lte=period.end
    ).select_related("lead", "subscription")
    lead_to_quote, quote_to_accept, lead_to_accept, accept_to_paid, paid_to_live = [], [], [], [], []
    for q in accepted:
        if q.lead and (d := days_between(q.lead.created_at, q.sent_at)) is not None:
            lead_to_quote.append(d)
        if (d := days_between(q.sent_at, q.decided_at)) is not None:
            quote_to_accept.append(d)
        if q.lead and (d := days_between(q.lead.created_at, q.decided_at)) is not None:
            lead_to_accept.append(d)
        sub = q.subscription
        if sub and sub.activated_at:
            if (d := days_between(q.decided_at, sub.activated_at)) is not None:
                accept_to_paid.append(d)
            instance = Instance.objects.filter(subscription=sub, went_live_on__isnull=False).first()
            if instance:
                paid_to_live.append(max((instance.went_live_on - local_date(sub.activated_at)).days, 0))
    result = {
        "accepted": accepted.count(),
        "lead_to_quote": summarise(lead_to_quote),
        "quote_to_accept": summarise(quote_to_accept),
        "lead_to_accept": summarise(lead_to_accept),
        "accept_to_paid": summarise(accept_to_paid),
        "paid_to_live": summarise(paid_to_live),
        "waiting_to_pay": Quote.objects.filter(status=Quote.Status.ACCEPTED)
        .exclude(subscription__activated_at__isnull=False)
        .count(),
    }
    result["steps"] = [
        ("Lead received to quotation sent", result["lead_to_quote"]),
        ("Quotation sent to accepted", result["quote_to_accept"]),
        ("Lead received to accepted (the whole sale)", result["lead_to_accept"]),
        ("Accepted to first payment", result["accept_to_paid"]),
        ("First payment to instance live", result["paid_to_live"]),
    ]
    return result


# ------------------------------------------------------------------------------------------------ recurring revenue
def mrr_by_plan() -> dict:
    """Today's recurring revenue per month, by plan. Annual subscriptions count one twelfth. Paused ones are listed apart."""
    rows: dict[str, dict] = {}
    for sub in Subscription.objects.select_related("plan").filter(status__in=[*LIVE, Subscription.Status.PAUSED]):
        row = rows.setdefault(
            sub.plan.name,
            {
                "plan": sub.plan.name,
                "customers": 0,
                "mrr": Decimal("0"),
                "paused": 0,
                "paused_mrr": Decimal("0"),
                "past_due": 0,
                "at_risk": Decimal("0"),
            },
        )
        value = monthly(sub.recurring_amount, sub.billing_period)
        if sub.status == Subscription.Status.PAUSED:
            row["paused"] += 1
            row["paused_mrr"] += value
            continue
        row["customers"] += 1
        row["mrr"] += value
        if sub.status == Subscription.Status.PAST_DUE:
            row["past_due"] += 1
            row["at_risk"] += value
    out = sorted(rows.values(), key=lambda r: (-r["mrr"], r["plan"]))
    total = {"customers": sum(r["customers"] for r in out), "mrr": sum((r["mrr"] for r in out), Decimal("0")),
             "paused": sum(r["paused"] for r in out), "paused_mrr": sum((r["paused_mrr"] for r in out), Decimal("0")),
             "past_due": sum(r["past_due"] for r in out), "at_risk": sum((r["at_risk"] for r in out), Decimal("0"))}  # fmt: skip
    for row in [*out, total]:
        for key in ("mrr", "paused_mrr", "at_risk"):
            row[key] = money(row[key])
        row["mrr_excl_vat"] = excl_vat(row["mrr"])
        row["share"] = pct(row["mrr"], total["mrr"])
        row["average"] = money(row["mrr"] / row["customers"]) if row["customers"] else None
    total["arr"] = money(total["mrr"] * 12)
    return {"plans": out, "total": total}


def monthly_trend(period: Period) -> list[dict]:
    """One row per month: recurring revenue at month end, what was added and lost, churn and cash collected.

    Rebuilt from activation and cancellation dates at *today's* recurring amounts, and it ignores pauses and failed
    payments. A subscription cancelled with no recorded date is treated as cancelled on its activation day."""
    subs = list(Subscription.objects.filter(activated_at__isnull=False))
    spans = []
    for s in subs:
        began = local_date(s.activated_at)
        ended = (
            local_date(s.cancelled_at)
            if s.cancelled_at
            else (began if s.status == Subscription.Status.CANCELLED else None)
        )
        spans.append((began, ended, monthly(s.recurring_amount, s.billing_period)))
    rows = []
    for first in period.months():
        end_exclusive = next_month(first)
        start_live = [v for b, e, v in spans if b < first and (e is None or e >= first)]
        end_live = [v for b, e, v in spans if b < end_exclusive and (e is None or e >= end_exclusive)]
        added = [v for b, e, v in spans if first <= b < end_exclusive]
        lost = [v for b, e, v in spans if e is not None and first <= e < end_exclusive and b < end_exclusive]
        start_mrr, end_mrr = sum(start_live, Decimal("0")), sum(end_live, Decimal("0"))
        gross = Payment.objects.filter(
            status="COMPLETE", received_at__date__gte=first, received_at__date__lt=end_exclusive
        )
        refunded = Refund.objects.filter(
            status=Refund.Status.PROCESSED, processed_at__date__gte=first, processed_at__date__lt=end_exclusive
        )
        collected = sum((p.amount_gross for p in gross), Decimal("0"))
        refunds = sum((r.amount for r in refunded), Decimal("0"))
        rows.append(
            {
                "month": first,
                "customers_start": len(start_live),
                "customers_end": len(end_live),
                "new": len(added),
                "lost": len(lost),
                "logo_churn": pct(
                    len([1 for b, e, v in spans if e is not None and first <= e < end_exclusive and b < first]),
                    len(start_live),
                ),
                "mrr_end": money(end_mrr),
                "mrr_new": money(sum(added, Decimal("0"))),
                "mrr_lost": money(sum(lost, Decimal("0"))),
                "revenue_churn": pct(
                    sum(
                        (v for b, e, v in spans if e is not None and first <= e < end_exclusive and b < first),
                        Decimal("0"),
                    ),
                    start_mrr,
                ),
                "collected": money(collected),
                "refunded": money(refunds),
                "net_collected": money(collected - refunds),
            }
        )
    return rows


def churn_summary(trend: list[dict]) -> dict:
    """Average monthly churn over the months with customers at the start, and the number of customers lost."""
    with_base = [r for r in trend if r["customers_start"]]
    return {
        "lost": sum(r["lost"] for r in trend),
        "new": sum(r["new"] for r in trend),
        "avg_logo_churn": round(mean(r["logo_churn"] for r in with_base), 1) if with_base else None,
        "avg_revenue_churn": round(mean(r["revenue_churn"] for r in with_base if r["revenue_churn"] is not None), 1)
        if any(r["revenue_churn"] is not None for r in with_base) else None,
        "mrr_lost": money(sum((r["mrr_lost"] for r in trend), Decimal("0"))),
        "mrr_new": money(sum((r["mrr_new"] for r in trend), Decimal("0"))),
    }  # fmt: skip


def cash(period: Period) -> dict:
    payments = Payment.objects.filter(received_at__date__gte=period.start, received_at__date__lte=period.end)
    complete = list(payments.filter(status="COMPLETE"))
    failed = payments.exclude(status="COMPLETE").count()
    refunds = Refund.objects.filter(
        status=Refund.Status.PROCESSED, processed_at__date__gte=period.start, processed_at__date__lte=period.end
    )
    gross = sum((p.amount_gross for p in complete), Decimal("0"))
    fees = sum((p.amount_fee or Decimal("0") for p in complete), Decimal("0"))
    refunded = sum((r.amount for r in refunds), Decimal("0"))
    return {
        "payments": len(complete),
        "failed": failed,
        "failure_rate": pct(failed, failed + len(complete)),
        "gross": money(gross),
        "fees": money(fees),
        "refunded": money(refunded),
        "refund_count": refunds.count(),
        "net": money(gross - refunded),
    }


def operations() -> dict:
    """Where things stand right now: hosted instances."""
    instances = list(Instance.objects.exclude(status=Instance.Status.RETIRED))
    waiting = [i for i in instances if i.is_open]
    return {
        "live": sum(i.status == Instance.Status.LIVE for i in instances),
        "waiting": len(waiting),
        "avg_wait": round(mean(i.days_waiting for i in waiting), 1) if waiting else None,
        "outdated": sum(i.is_outdated for i in instances),
        "missing": Subscription.objects.filter(
            status__in=[*LIVE, Subscription.Status.PAUSED], instance__isnull=True
        ).count(),
    }


# ------------------------------------------------------------------------------------------------ everything
def build(period: Period) -> dict:
    trend = monthly_trend(period)
    revenue = mrr_by_plan()
    quotes = quotations(period)
    close = time_to_close(period)
    return {
        "period": period,
        "funnel": funnel(period),
        "sources": sources(period),
        "quotes": quotes,
        "close": close,
        "revenue": revenue,
        "trend": trend,
        "churn": churn_summary(trend),
        "cash": cash(period),
        "ops": operations(),
        "max_mrr": max((r["mrr_end"] for r in trend), default=Decimal("0")),
    }


def chart(trend: list[dict], width: int = 900, height: int = 150) -> dict:
    """Bars for the recurring-revenue-by-month picture, as numbers for an inline SVG (the site's security policy forbids
    inline styles, and SVG attributes are fine). The table beside it carries the same figures for screen readers."""
    top = max((r["mrr_end"] for r in trend), default=Decimal("0"))
    plot, base = height - 34, height - 20
    slot = width / max(len(trend), 1)
    step = 1 if len(trend) <= 12 else 2
    bars = []
    for i, row in enumerate(trend):
        h = int(plot * row["mrr_end"] / top) if top else 0
        bars.append(
            {
                "x": round(i * slot + slot * 0.18, 1),
                "w": round(slot * 0.64, 1),
                "y": base - h,
                "h": max(h, 1 if row["mrr_end"] else 0),
                "label_x": round(i * slot + slot / 2, 1),
                "label": row["month"].strftime("%b") if i % step == 0 else "",
                "title": f"{row['month']:%b %Y}: R{row['mrr_end']:,.2f}",
            }
        )
    return {"width": width, "height": height, "base": base, "label_y": height - 5, "bars": bars, "top": money(top)}


# ------------------------------------------------------------------------------------------------ CSV data
def _summary_rows(data: dict) -> list[list]:
    f, q, c, r, ch, cash_, ops = (
        data["funnel"],
        data["quotes"],
        data["close"],
        data["revenue"]["total"],
        data["churn"],
        data["cash"],
        data["ops"],
    )
    rows = [
        ["Period", "From", data["period"].start], ["Period", "To", data["period"].end],
        ["Leads", "Received", f["total"]], ["Leads", "Quoted", f["quoted"]], ["Leads", "Won", f["won"]], ["Leads", "Lost", f["lost"]],
        ["Leads", "Win rate % (won / (won + lost))", f["win_rate"]], ["Leads", "Conversion % (won / received)", f["conversion"]],
        ["Quotations", "Sent", q["sent"]], *[["Quotations", label, n] for label, n in q["counts"].items()],
        ["Quotations", "Quote-to-won rate % (accepted / those with an outcome)", q["win_rate"]],
        ["Quotations", "Accepted as % of all sent", q["accepted_of_sent"]],
        ["Quotations", "Monthly value won (R, incl. VAT)", q["value_won_monthly"]],
        ["Quotations", "Days from sent to decision (average)", q["days_to_decide"]["mean"]],
        ["Quotations", "Days from sent to decision (median)", q["days_to_decide"]["median"]],
        ["Time to close", "Days lead to accepted (average)", c["lead_to_accept"]["mean"]],
        ["Time to close", "Days lead to accepted (median)", c["lead_to_accept"]["median"]],
        ["Time to close", "Days accepted to first payment (average)", c["accept_to_paid"]["mean"]],
        ["Time to close", "Days first payment to live (average)", c["paid_to_live"]["mean"]],
        ["Time to close", "Accepted, still waiting to pay", c["waiting_to_pay"]],
        ["Revenue today", "Paying customers", r["customers"]], ["Revenue today", "Monthly recurring revenue (R, incl. VAT)", r["mrr"]],
        ["Revenue today", "Annualised (R, incl. VAT)", r["arr"]], ["Revenue today", "MRR at risk, payment failed (R)", r["at_risk"]],
        ["Revenue today", "Paused customers", r["paused"]],
        ["Churn", "Customers gained", ch["new"]], ["Churn", "Customers lost", ch["lost"]],
        ["Churn", "Average monthly customer churn %", ch["avg_logo_churn"]], ["Churn", "Average monthly revenue churn %", ch["avg_revenue_churn"]],
        ["Cash", "Payments received", cash_["payments"]], ["Cash", "Amount received (R)", cash_["gross"]],
        ["Cash", "PayFast fees (R)", cash_["fees"]], ["Cash", "Refunded (R)", cash_["refunded"]], ["Cash", "Net of refunds (R)", cash_["net"]],
        ["Cash", "Failed payments", cash_["failed"]],
        ["Hosting", "Live instances", ops["live"]], ["Hosting", "Waiting for set-up", ops["waiting"]],
    ]  # fmt: skip
    return rows


def _quote_rows(period: Period) -> list[list]:
    rows = []
    for q in sent_quotes(period):
        outcome = quote_outcome(q)
        rows.append([
            q.number, q.business_name, q.plan.name, q.get_billing_period_display(), q.monthly_equivalent, q.sent_at, outcome,
            q.decided_at if outcome in ("Accepted", "Declined") else None,
            None if (d := days_between(q.sent_at, q.decided_at)) is None else round(d, 1),
            q.lead.created_at if q.lead else None, q.subscription.reference if q.subscription else "",
        ])  # fmt: skip
    return rows


QUOTE_HEADER = ["Quotation", "Business", "Plan", "Billing", "Monthly value (R, incl. VAT)", "Sent", "Outcome", "Decided", "Days to decide", "Lead received", "Subscription"]  # fmt: skip
MONTH_HEADER = ["Month", "Customers at start", "Customers at end", "New", "Lost", "Customer churn %", "MRR at end (R)", "New MRR (R)", "Lost MRR (R)", "Revenue churn %", "Collected (R)", "Refunded (R)", "Net collected (R)"]  # fmt: skip
PLAN_HEADER = ["Plan", "Paying customers", "MRR (R, incl. VAT)", "MRR (R, excl. VAT)", "Share of MRR %", "Average per customer (R)", "Payment failed", "MRR at risk (R)", "Paused", "Paused MRR (R)"]  # fmt: skip
SOURCE_HEADER = ["Source", "Leads", "Quoted", "Won", "Lost", "Win rate %"]


def csv_data(kind: str, data: dict) -> tuple[list[str], list[list]] | None:
    """(header, rows) for one downloadable report table, or None for an unknown name."""
    if kind == "summary":
        return ["Section", "Measure", "Value"], _summary_rows(data)
    if kind == "monthly":
        return MONTH_HEADER, [
            [r["month"].strftime("%Y-%m"), r["customers_start"], r["customers_end"], r["new"], r["lost"], r["logo_churn"], r["mrr_end"], r["mrr_new"], r["mrr_lost"], r["revenue_churn"], r["collected"], r["refunded"], r["net_collected"]]
            for r in data["trend"]
        ]  # fmt: skip
    if kind == "plans":
        rows = data["revenue"]["plans"]
        return PLAN_HEADER, [
            [r["plan"], r["customers"], r["mrr"], r["mrr_excl_vat"], r["share"], r["average"], r["past_due"], r["at_risk"], r["paused"], r["paused_mrr"]]
            for r in rows
        ]  # fmt: skip
    if kind == "quotes":
        return QUOTE_HEADER, _quote_rows(data["period"])
    if kind == "sources":
        return SOURCE_HEADER, [
            [r["source"], r["leads"], r["quoted"], r["won"], r["lost"], r["win_rate"]] for r in data["sources"]
        ]
    return None


EXPORT_LABELS = {
    "summary": "Summary of every figure",
    "monthly": "Month by month: revenue, new, lost, churn, cash",
    "plans": "Recurring revenue by plan (today)",
    "quotes": "Every quotation sent in the period",
    "sources": "Lead sources and results",
}
