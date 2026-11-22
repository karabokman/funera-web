"""Refunds. A client may request one for a recent payment; staff approve it, and PayFast returns the money to the card."""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Sum
from django.template.loader import render_to_string
from django.utils import timezone

from . import payfast, services
from .errors import BillingError
from .models import Payment, Refund, SubscriptionEvent

logger = logging.getLogger(__name__)
ZERO = Decimal("0.00")


def refunded_amount(payment: Payment) -> Decimal:
    return payment.refunds.filter(status=Refund.Status.PROCESSED).aggregate(total=Sum("amount"))["total"] or ZERO


def refundable_amount(payment: Payment) -> Decimal:
    if payment.status != "COMPLETE":
        return ZERO
    return max(Decimal(payment.amount_gross) - refunded_amount(payment), ZERO).quantize(ZERO)


def within_window(payment: Payment) -> bool:
    return timezone.now() <= payment.received_at + timedelta(days=settings.REFUND_WINDOW_DAYS)


def _mail(subject: str, template: str, context: dict, to: list[str]) -> None:
    if not to:
        return

    def send():
        try:
            send_mail(subject, render_to_string(template, context), settings.DEFAULT_FROM_EMAIL, to)
        except Exception:  # noqa: BLE001
            logger.exception("Could not send %s", template)

    transaction.on_commit(send)


@transaction.atomic
def request_refund(payment: Payment, reason: str, *, actor: str = SubscriptionEvent.Actor.CLIENT) -> Refund:
    """Record a request for the whole refundable amount. Clients are held to the refund window; staff are not."""
    if actor == SubscriptionEvent.Actor.CLIENT and not within_window(payment):
        raise BillingError(
            f"Refunds can be requested within {settings.REFUND_WINDOW_DAYS} days of a payment. Please contact us."
        )
    amount = refundable_amount(payment)
    if amount <= 0:
        raise BillingError("This payment has nothing left to refund.")
    if payment.refunds.filter(status=Refund.Status.REQUESTED).exists():
        raise BillingError("A refund has already been requested for this payment.")
    refund = Refund.objects.create(payment=payment, amount=amount, reason=reason.strip()[:300], requested_by=actor)
    services.record_event(
        payment.subscription, "refund_requested", f"R{amount} on payment {payment.pf_payment_id}", actor
    )
    subscription = payment.subscription
    _mail(f"Refund requested: {subscription.customer.business_name}", "emails/refund_requested.txt",
          {"refund": refund, "subscription": subscription, "customer": subscription.customer, "site_url": settings.SITE_URL, "admin_url": settings.ADMIN_URL}, settings.SALES_NOTIFY_EMAILS)  # fmt: skip
    return refund


def process(refund: Refund) -> None:
    """Ask PayFast to refund. On success the refund is marked processed and the client is told. If PayFast does not
    confirm, the refund is marked failed (nothing was refunded) and the error is raised so staff see why."""
    failure = None
    with transaction.atomic():
        refund = Refund.objects.select_for_update().select_related("payment__subscription__customer").get(pk=refund.pk)
        if refund.status not in (Refund.Status.REQUESTED, Refund.Status.FAILED):
            raise BillingError("This refund has already been dealt with.")
        if refund.amount <= 0 or refund.amount > refundable_amount(refund.payment):
            raise BillingError("The amount is more than what is left to refund on this payment.")
        subscription = refund.payment.subscription
        try:
            payfast.refund_payment(refund.payment.pf_payment_id, refund.amount, refund.reason or "Refund")
        except payfast.GatewayUnavailable as exc:
            refund.status = Refund.Status.FAILED
            refund.staff_note = str(exc)[:300]
            refund.save(update_fields=["status", "staff_note"])
            failure = exc  # raised after the block so that the "failed" status is saved, not rolled back
        else:
            refund.status = Refund.Status.PROCESSED
            refund.processed_at = timezone.now()
            refund.save(update_fields=["status", "processed_at"])
            services.record_event(
                subscription,
                "refunded",
                f"R{refund.amount} on payment {refund.payment.pf_payment_id}",
                SubscriptionEvent.Actor.STAFF,
            )
    if failure:
        raise failure
    _mail(
        "Your refund",
        "emails/refund_processed.txt",
        {"refund": refund, "customer": subscription.customer, "brand": settings.BRAND_NAME},
        [subscription.customer.email],
    )


@transaction.atomic
def decline(refund: Refund, note: str = "") -> None:
    refund = Refund.objects.select_for_update().get(pk=refund.pk)
    if refund.status != Refund.Status.REQUESTED:
        raise BillingError("Only a requested refund can be declined.")
    refund.status = Refund.Status.DECLINED
    refund.staff_note = note[:300]
    refund.processed_at = timezone.now()
    refund.save(update_fields=["status", "staff_note", "processed_at"])
    services.record_event(refund.payment.subscription, "refund_declined", note, SubscriptionEvent.Actor.STAFF)
