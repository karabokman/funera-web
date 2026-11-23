"""Quotation rules: numbering, sending, acceptance (which creates the customer and a pending subscription), declining."""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.mail import EmailMessage
from django.db import IntegrityError, transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from billing.models import Customer, Subscription
from leads.models import Lead

from . import pdf
from .errors import QuoteError
from .models import Quote, QuoteEvent, QuoteLine

logger = logging.getLogger(__name__)


def record(quote: Quote, kind: str, detail: str = "", actor: str = "") -> None:
    QuoteEvent.objects.create(quote=quote, kind=kind, detail=detail[:300], actor=actor[:60])


def _next_number() -> str:
    year = timezone.localdate().year
    prefix = f"QT-{year}-"
    last = Quote.objects.filter(number__startswith=prefix).order_by("-number").values_list("number", flat=True).first()
    return f"{prefix}{(int(last.rsplit('-', 1)[1]) + 1 if last else 1):04d}"


def initial_for(plan, lead: Lead | None = None) -> tuple[dict, list[dict]]:
    """Starting values for a new quotation: the lead's details, the plan's list price and its set-up fee as a line."""
    initial = {
        "plan": plan,
        "billing_period": "monthly",
        "valid_until": timezone.localdate() + timedelta(days=settings.QUOTE_VALID_DAYS),
    }
    if plan is not None and plan.has_price:
        initial["recurring_price"] = plan.monthly_price
    if lead is not None:
        initial.update(contact_name=lead.name, business_name=lead.business_name, email=lead.email, phone=lead.phone)
    lines = []
    if plan is not None and plan.setup_fee:
        lines.append({"description": "Set-up, data import and onboarding", "quantity": 1, "unit_price": plan.setup_fee})
    return initial, lines


@transaction.atomic
def save_new(quote: Quote, user, lead: Lead | None = None) -> Quote:
    """Number, price-stamp and save a new draft. The number is unique per year; a clash from two staff saving at the same
    moment is retried."""
    quote.created_by = user if getattr(user, "is_authenticated", False) else None
    quote.lead = lead
    quote.vat_rate = Decimal(str(settings.VAT_RATE))
    for attempt in range(5):
        quote.number = _next_number()
        try:
            with transaction.atomic():
                quote.save()
            break
        except IntegrityError:
            if attempt == 4:
                raise
    record(quote, "created", actor=str(user))
    return quote


def _staff_name(user) -> str:
    return str(user) if user is not None else "staff"


@transaction.atomic
def send(quote: Quote, user=None) -> None:
    """E-mail the quotation (with the PDF attached). The e-mail goes out before anything is committed: if it cannot be
    sent, the quotation is left exactly as it was and the staff member is told why."""
    if quote.status not in (Quote.Status.DRAFT, Quote.Status.SENT):
        raise QuoteError("Only a draft or an already-sent quotation can be sent.")
    if quote.valid_until < timezone.localdate():
        raise QuoteError("The valid-until date has passed. Edit the date first.")
    if quote.recurring_price <= 0:
        raise QuoteError("Set the recurring price before sending.")
    if quote.first_payment_gross < 5:
        raise QuoteError("The first payment must be at least R5 for PayFast to collect it.")
    resent = quote.status == Quote.Status.SENT
    quote.status = Quote.Status.SENT
    quote.sent_at = timezone.now()
    quote.save(update_fields=["status", "sent_at"])
    record(quote, "resent" if resent else "sent", f"to {quote.email}", _staff_name(user))
    if quote.lead_id and quote.lead.status in (Lead.Status.NEW, Lead.Status.CONTACTED, Lead.Status.DEMO):
        quote.lead.status = Lead.Status.QUOTED
        quote.lead.save(update_fields=["status"])
    try:
        _deliver(
            f"Your {settings.BRAND_NAME} quotation {quote.number}",
            _body("emails/quote_sent.txt", quote, link=public_url(quote)),
            [quote.email],
            attachments=[(f"{quote.number}.pdf", pdf.render(quote), "application/pdf")],
        )
    except Exception as exc:  # noqa: BLE001 - any failure (SMTP, DNS, TLS, PDF) must undo the "sent" state
        logger.exception("Could not e-mail %s", quote.number)
        raise QuoteError(
            f"The e-mail could not be sent, so the quotation was not marked as sent ({type(exc).__name__}: {exc}). "
            "Check the EMAIL_* settings and try again."
        ) from exc


def public_url(quote: Quote) -> str:
    return f"{settings.SITE_URL}{reverse('quotes:public', args=[quote.token])}"


def _body(template: str, quote: Quote, **extra) -> str:
    context = {
        "quote": quote,
        "brand": settings.BRAND_NAME,
        "site_url": settings.SITE_URL,
        "admin_url": settings.ADMIN_URL,
        "staff_url": f"{settings.SITE_URL}/{settings.STAFF_URL}",
        **extra,
    }
    return render_to_string(template, context)


def _deliver(subject: str, body: str, to: list[str], attachments=()) -> None:
    message = EmailMessage(subject, body, settings.DEFAULT_FROM_EMAIL, to)
    for attachment in attachments:
        message.attach(*attachment)
    message.send(fail_silently=False)


def _mail(subject: str, template: str, quote: Quote, to: list[str], **extra) -> None:
    """A notification sent after the transaction commits. A failure is logged and never blocks the client's action."""
    to = [address for address in to if address]
    if not to:
        return
    body = _body(template, quote, **extra)

    def deliver():
        try:
            _deliver(subject, body, to)
        except Exception:  # noqa: BLE001
            logger.exception("Could not send %s for %s", template, quote.number)

    transaction.on_commit(deliver)


@transaction.atomic
def accept(quote: Quote, *, name: str) -> Subscription:
    """The client accepts: customer and a pending subscription are created at exactly the quoted prices (VAT included
    where it applies), ready for the first PayFast payment."""
    quote = Quote.objects.select_for_update().get(pk=quote.pk)
    if quote.status == Quote.Status.ACCEPTED and quote.subscription_id:
        return quote.subscription  # a second click or a resubmitted form: nothing new is created
    if not quote.is_open:
        raise QuoteError("This quotation can no longer be accepted. Please contact us for a new one.")
    now = timezone.now()
    customer = Customer.objects.create(
        name=quote.contact_name,
        business_name=quote.business_name,
        email=quote.email,
        phone=quote.phone,
        lead=quote.lead,
        terms_accepted_at=now,
        terms_version=settings.SUBSCRIPTION_TERMS_VERSION,
        notice_version=settings.PRIVACY_NOTICE_VERSION,
    )
    subscription = Subscription.objects.create(
        customer=customer,
        plan=quote.plan,
        billing_period=quote.billing_period,
        initial_amount=quote.first_payment_gross,
        recurring_amount=quote.recurring_gross,
    )
    quote.status = Quote.Status.ACCEPTED
    quote.decided_at = now
    quote.decision_by = name[:120]
    quote.terms_version = settings.SUBSCRIPTION_TERMS_VERSION
    quote.subscription = subscription
    quote.save()
    record(quote, "accepted", f"by {name}", "client")
    if quote.lead_id:
        Lead.objects.filter(pk=quote.lead_id).update(status=Lead.Status.WON)
    pay_link = f"{settings.SITE_URL}{reverse('billing:pay', args=[subscription.reference])}"
    _mail(
        f"Quotation accepted: {quote.business_name}",
        "emails/quote_accepted_staff.txt",
        quote,
        settings.SALES_NOTIFY_EMAILS,
        subscription=subscription,
    )
    _mail(
        f"Thank you, {settings.BRAND_NAME}", "emails/quote_accepted_client.txt", quote, [quote.email], pay_link=pay_link
    )
    return subscription


@transaction.atomic
def decline(quote: Quote, *, reason: str = "") -> None:
    quote = Quote.objects.select_for_update().get(pk=quote.pk)
    if not quote.is_open:
        raise QuoteError("This quotation can no longer be declined.")
    quote.status = Quote.Status.DECLINED
    quote.decided_at = timezone.now()
    quote.decision_note = reason[:300]
    quote.save(update_fields=["status", "decided_at", "decision_note"])
    record(quote, "declined", reason, "client")
    if quote.lead_id:
        Lead.objects.filter(pk=quote.lead_id, status=Lead.Status.QUOTED).update(status=Lead.Status.LOST)
    _mail(
        f"Quotation declined: {quote.business_name}",
        "emails/quote_declined_staff.txt",
        quote,
        settings.SALES_NOTIFY_EMAILS,
    )


def cancel(quote: Quote, user=None) -> None:
    if quote.status not in (Quote.Status.DRAFT, Quote.Status.SENT):
        raise QuoteError("Only a draft or sent quotation can be cancelled.")
    quote.status = Quote.Status.CANCELLED
    quote.save(update_fields=["status"])
    record(quote, "cancelled", actor=_staff_name(user))


@transaction.atomic
def duplicate(quote: Quote, user=None) -> Quote:
    """A fresh draft with the same details and lines, for revising a quotation that was already sent."""
    copy = Quote(
        contact_name=quote.contact_name, business_name=quote.business_name, email=quote.email, phone=quote.phone,
        plan=quote.plan, billing_period=quote.billing_period, recurring_price=quote.recurring_price, vat_included=quote.vat_included,
        valid_until=timezone.localdate() + timedelta(days=settings.QUOTE_VALID_DAYS), intro=quote.intro, internal_notes=quote.internal_notes,
    )  # fmt: skip
    save_new(copy, user, lead=quote.lead)
    for line in quote.lines.all():
        QuoteLine.objects.create(
            quote=copy,
            description=line.description,
            quantity=line.quantity,
            unit_price=line.unit_price,
            order=line.order,
        )
    record(copy, "duplicated", f"from {quote.number}", _staff_name(user))
    return copy
