"""CSV exports for the accountant: leads, customers, subscriptions and payments.

Each dataset has one function that applies the filters (the same search and status filters as the list pages, plus an
optional date range) and one list of columns. The list pages and the export use the same filter function, so what you
download is exactly what you were looking at.
"""

from __future__ import annotations

import csv
import re
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db.models import DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.http import HttpResponse
from django.utils import timezone

from billing.models import Customer, Payment, Refund, Subscription
from leads.models import Lead

CENT = Decimal("0.01")
DELIMITERS = {"comma": ",", "semicolon": ";"}


# ------------------------------------------------------------------------------------------------ cleaning values
_PHONE_LIKE = re.compile(r"^[+-]?[\d\s().-]+$")


def safe(value) -> str:
    """One cell as text. Anything a spreadsheet could run as a formula is defused with a leading apostrophe, because
    names and messages come from the public contact form. Phone numbers such as +27 82 123 4567 are left alone."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, datetime):
        return (
            timezone.localtime(value).strftime("%Y-%m-%d %H:%M")
            if timezone.is_aware(value)
            else value.strftime("%Y-%m-%d %H:%M")
        )
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return f"{value.quantize(CENT, ROUND_HALF_UP)}"
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    if text and text[0] in "=@\t" or (text and text[0] in "+-" and not _PHONE_LIKE.match(text)):
        return "'" + text
    return text


def money(value) -> Decimal:
    return Decimal(value or 0).quantize(CENT, ROUND_HALF_UP)


def vat_part(gross) -> Decimal | None:
    """The VAT inside a VAT-inclusive amount, at today's rate. Blank when no VAT number is configured."""
    if not settings.VAT_NUMBER:
        return None
    rate = Decimal(str(settings.VAT_RATE))
    return (Decimal(gross) * rate / (1 + rate)).quantize(CENT, ROUND_HALF_UP)


# ------------------------------------------------------------------------------------------------ filters
def _dates(params) -> tuple[date | None, date | None]:
    def one(key):
        try:
            return date.fromisoformat((params.get(key) or "").strip())
        except ValueError:
            return None

    return one("from"), one("to")


def _between(qs, field, params):
    start, end = _dates(params)
    if start:
        qs = qs.filter(**{f"{field}__date__gte": start})
    if end:
        qs = qs.filter(**{f"{field}__date__lte": end})
    return qs


def leads(params):
    qs = Lead.objects.select_related("plan_interest")
    status, query = params.get("status", ""), params.get("q", "").strip()
    if status in Lead.Status.values:
        qs = qs.filter(status=status)
    if query:
        qs = qs.filter(
            Q(name__icontains=query)
            | Q(business_name__icontains=query)
            | Q(email__icontains=query)
            | Q(phone__icontains=query)
        )
    return _between(qs, "created_at", params)


def customers(params):
    qs = Customer.objects.prefetch_related("subscriptions__plan")
    query = params.get("q", "").strip()
    if query:
        qs = qs.filter(Q(name__icontains=query) | Q(business_name__icontains=query) | Q(email__icontains=query))
    return _between(qs, "created_at", params)


def subscriptions(params):
    qs = Subscription.objects.select_related("customer", "plan")
    status, query = params.get("status", ""), params.get("q", "").strip()
    if status in Subscription.Status.values:
        qs = qs.filter(status=status)
    if query:
        qs = qs.filter(
            Q(reference__icontains=query)
            | Q(customer__business_name__icontains=query)
            | Q(customer__email__icontains=query)
        )
    return _between(qs, "created_at", params)


def payments(params):
    qs = Payment.objects.select_related("subscription__customer", "subscription__plan")
    query = params.get("q", "").strip()
    if query:
        qs = qs.filter(
            Q(subscription__reference__icontains=query)
            | Q(subscription__customer__business_name__icontains=query)
            | Q(pf_payment_id__icontains=query)
        )
    return _between(qs, "received_at", params)


# ------------------------------------------------------------------------------------------------ columns
def _lead_rows(qs):
    for x in qs.order_by("created_at", "id").iterator(chunk_size=500):
        yield [
            x.pk, x.created_at, x.get_status_display(), x.business_name, x.name, x.email, x.phone, x.get_member_count_display(),
            x.plan_interest.name if x.plan_interest else "", x.consent_given, x.consent_at, x.notice_version, x.contacted_at,
            x.follow_up_on, x.next_action, x.source_page, x.utm_source, x.utm_medium, x.utm_campaign,
        ]  # fmt: skip


LEAD_HEADER = [
    "Lead ID", "Received", "Status", "Business", "Contact name", "E-mail", "Phone", "Size", "Plan of interest", "Consent given",
    "Consent recorded", "Privacy notice version", "First contacted", "Follow up on", "Next action", "Source page", "UTM source",
    "UTM medium", "UTM campaign",
]  # fmt: skip


def _customer_rows(qs):
    for x in qs.order_by("created_at", "id").iterator(chunk_size=500):
        subs = list(x.subscriptions.all())
        yield [
            x.pk, x.created_at, x.business_name, x.name, x.email, x.phone, x.terms_accepted_at, x.terms_version, x.notice_version,
            x.lead_id, len(subs), "; ".join(f"{s.reference} ({s.plan.name}, {s.get_status_display()})" for s in subs),
        ]  # fmt: skip


CUSTOMER_HEADER = [
    "Customer ID", "Created", "Business", "Contact name", "E-mail", "Phone", "Terms accepted", "Terms version",
    "Privacy notice version", "Lead ID", "Subscriptions", "Subscription details",
]  # fmt: skip


def _subscription_rows(qs):
    paid = Coalesce(
        Sum("payments__amount_gross", filter=Q(payments__status="COMPLETE")),
        Value(Decimal("0")),
        output_field=DecimalField(),
    )
    for x in qs.annotate(paid=paid).order_by("created_at", "id").iterator(chunk_size=500):
        yield [
            x.reference, x.created_at, x.customer.business_name, x.customer.email, x.plan.name, x.get_status_display(),
            x.get_billing_period_display(), money(x.initial_amount), money(x.recurring_amount), x.next_billing_date, x.paused_until,
            x.activated_at, x.cancelled_at, money(x.paid), x.customer_id,
        ]  # fmt: skip


SUBSCRIPTION_HEADER = [
    "Reference", "Created", "Customer", "E-mail", "Plan", "Status", "Billing period", "First payment (R, incl. VAT)",
    "Recurring (R, incl. VAT)", "Next billing date", "Paused until", "Activated", "Cancelled", "Paid to date (R)", "Customer ID",
]  # fmt: skip


def _payment_rows(qs):
    refunded = Coalesce(
        Sum("refunds__amount", filter=Q(refunds__status=Refund.Status.PROCESSED)),
        Value(Decimal("0")),
        output_field=DecimalField(),
    )
    for x in qs.annotate(refunded=refunded).order_by("received_at", "id").iterator(chunk_size=500):
        gross, vat = money(x.amount_gross), vat_part(x.amount_gross)
        yield [
            x.received_at, x.pf_payment_id, x.subscription.reference, x.subscription.customer.business_name, x.subscription.plan.name,
            x.status, gross, vat, None if vat is None else gross - vat,
            None if x.amount_fee is None else money(x.amount_fee), None if x.amount_fee is None else gross - money(x.amount_fee),
            money(x.refunded),
        ]  # fmt: skip


PAYMENT_HEADER = [
    "Received", "PayFast payment ID", "Subscription", "Customer", "Plan", "Status", "Amount received (R, incl. VAT)",
    "VAT included (R, at today's rate)", "Amount excl. VAT (R)", "PayFast fee (R)", "Net after fee (R)", "Refunded (R)",
]  # fmt: skip


class Dataset:
    def __init__(self, key, label, queryset, header, rows, *, statuses=(), date_label, note=""):
        self.key, self.label, self.queryset, self.header, self.rows = key, label, queryset, header, rows
        self.statuses, self.date_label, self.note = statuses, date_label, note


DATASETS = {
    d.key: d
    for d in (
        Dataset("leads", "Leads", leads, LEAD_HEADER, _lead_rows, statuses=Lead.Status.choices, date_label="Received",
                note="Contact details and consent records. Internal notes are not included."),
        Dataset("customers", "Customers", customers, CUSTOMER_HEADER, _customer_rows, date_label="Created",
                note="One row per customer, with their subscriptions listed."),
        Dataset("subscriptions", "Subscriptions", subscriptions, SUBSCRIPTION_HEADER, _subscription_rows,
                statuses=Subscription.Status.choices, date_label="Created", note="Plan terms, status and what has been paid to date."),
        Dataset("payments", "Payments", payments, PAYMENT_HEADER, _payment_rows, date_label="Received",
                note="Every PayFast payment with the VAT it contains, the PayFast fee and anything refunded. A working paper for your VAT return."),
    )
}  # fmt: skip


def build(dataset: Dataset, params, delimiter: str = "comma") -> tuple[HttpResponse, int]:
    """The CSV download and the number of data rows in it. UTF-8 with a byte-order mark so Excel shows accents correctly."""
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    stamp = timezone.localdate().isoformat()
    response["Content-Disposition"] = f'attachment; filename="funera-{dataset.key}-{stamp}.csv"'
    response["Cache-Control"] = "no-store"
    response.write("﻿")
    writer = csv.writer(response, delimiter=DELIMITERS.get(delimiter, ","), lineterminator="\r\n")
    writer.writerow(dataset.header)
    count = 0
    for row in dataset.rows(dataset.queryset(params)):
        writer.writerow([safe(cell) for cell in row])
        count += 1
    return response, count
