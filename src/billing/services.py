"""Subscription rules: creating a sign-up, building the PayFast checkout, and applying verified notifications."""

from __future__ import annotations

import calendar
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from catalog.models import Plan

from . import payfast
from .models import Customer, Payment, Subscription, SubscriptionEvent
from .signals import subscription_activated, subscription_status_changed

logger = logging.getLogger(__name__)
ZERO = Decimal("0.00")


class ItnRejected(Exception):
    """A notification that failed a security check. It is logged and answered with HTTP 400; it changes nothing."""


@dataclass(frozen=True)
class Checkout:
    url: str
    fields: list[tuple[str, str]]


def billing_enabled() -> bool:
    return payfast.is_configured()


def plan_is_purchasable(plan: Plan) -> bool:
    return billing_enabled() and plan.is_active and plan.has_price


def is_rate_limited(visitor: str) -> bool:
    since = timezone.now() - timedelta(hours=1)
    return (
        Customer.objects.filter(visitor_hash=visitor, created_at__gte=since).count()
        >= settings.LEAD_RATE_LIMIT_PER_HOUR
    )


def add_months(day: date, months: int) -> date:
    """The same day ``months`` later, clamped to the end of a shorter month (31 Jan + 1 month = 28 Feb)."""
    index = day.year * 12 + (day.month - 1) + months
    year, month = divmod(index, 12)
    return date(year, month + 1, min(day.day, calendar.monthrange(year, month + 1)[1]))


def add_one_month(day: date) -> date:
    return add_months(day, 1)


def record_event(
    subscription: Subscription, kind: str, detail: str = "", actor: str = SubscriptionEvent.Actor.SYSTEM
) -> None:
    SubscriptionEvent.objects.create(subscription=subscription, kind=kind, detail=detail[:300], actor=actor)


def set_status(subscription: Subscription, new: str, *, actor: str, kind: str, detail: str = "", **fields) -> None:
    """The one place a subscription changes status: saves it, writes the audit event and tells listeners."""
    old = subscription.status
    subscription.status = new
    for name, value in fields.items():
        setattr(subscription, name, value)
    subscription.save()
    record_event(subscription, kind, detail, actor)
    if old != new:
        transaction.on_commit(
            lambda: subscription_status_changed.send_robust(
                sender=Subscription, subscription=subscription, old=old, new=new
            )
        )


def client_ip(request) -> str:
    """The sender's address. With N trusted proxies in front, the real client is the Nth entry from the right of
    X-Forwarded-For; entries further left are written by the sender and cannot be trusted."""
    remote = request.META.get("REMOTE_ADDR", "")
    proxies = settings.BILLING_TRUSTED_PROXIES
    if proxies <= 0:
        return remote
    chain = [part.strip() for part in request.META.get("HTTP_X_FORWARDED_FOR", "").split(",") if part.strip()]
    return chain[-proxies] if len(chain) >= proxies else remote


@transaction.atomic
def create_subscription(*, plan: Plan, cleaned: dict, visitor_hash: str) -> Subscription:
    if not plan.has_price:
        raise ValueError("A plan without a price cannot be bought online.")
    period = cleaned.get("billing_period") or Subscription.Period.MONTHLY
    if period == Subscription.Period.ANNUAL and not plan.has_annual:
        raise ValueError("This plan has no annual price.")
    recurring = plan.annual_price if period == Subscription.Period.ANNUAL else plan.monthly_price
    now = timezone.now()
    customer = Customer.objects.create(
        name=cleaned["name"],
        business_name=cleaned["business_name"],
        email=cleaned["email"],
        phone=cleaned["phone"],
        terms_accepted_at=now,
        terms_version=settings.SUBSCRIPTION_TERMS_VERSION,
        notice_version=settings.PRIVACY_NOTICE_VERSION,
        visitor_hash=visitor_hash,
    )
    return Subscription.objects.create(
        customer=customer,
        plan=plan,
        billing_period=period,
        initial_amount=recurring + (plan.setup_fee or ZERO),
        recurring_amount=recurring,
    )


def build_checkout(subscription: Subscription) -> Checkout:
    """The signed form that sends the client to PayFast: the first payment now, then the monthly amount every month."""
    customer = subscription.customer
    first, _, rest = customer.name.partition(" ")
    phone = "".join(ch for ch in customer.phone if ch.isdigit())
    site = settings.SITE_URL
    values = {
        "merchant_id": settings.PAYFAST_MERCHANT_ID,
        "merchant_key": settings.PAYFAST_MERCHANT_KEY,
        "return_url": f"{site}{reverse('billing:return', args=[subscription.reference])}",
        "cancel_url": f"{site}{reverse('billing:cancelled', args=[subscription.reference])}",
        "notify_url": f"{site}{reverse('billing:payfast_itn')}",
        "name_first": first[:100],
        "name_last": rest[:100],
        "email_address": customer.email,
        "cell_number": phone if len(phone) == 10 else "",
        "m_payment_id": subscription.reference,
        "amount": f"{subscription.initial_amount:.2f}",
        "item_name": f"{settings.BRAND_NAME} {subscription.plan.name} plan"[:100],
        "item_description": f"{subscription.get_billing_period_display()} subscription for {customer.business_name}"[
            :255
        ],
        "email_confirmation": "1",
        "subscription_type": "1",
        "billing_date": add_months(timezone.localdate(), subscription.months_per_period).isoformat(),
        "recurring_amount": f"{subscription.recurring_amount:.2f}",
        "frequency": subscription.payfast_frequency,
        "cycles": "0",
    }
    url, fields = payfast.signed_checkout(values)
    return Checkout(url, fields)


def _money(raw: str | None) -> Decimal | None:
    try:
        return Decimal(raw) if raw not in (None, "") else None
    except InvalidOperation:
        return None


def handle_itn(pairs: list[tuple[str, str]], source_ip: str) -> Payment | None:
    """Verify one PayFast notification and apply it. Raises ``ItnRejected`` for a failed check and
    ``payfast.GatewayUnavailable`` if PayFast cannot confirm it right now (the caller answers 503 so PayFast retries).

    Checks, in order: sender address, signature, merchant, known subscription, amount, then PayFast's own confirmation."""
    data = dict(pairs)
    if settings.PAYFAST_CHECK_SOURCE_IP and not payfast.valid_source(source_ip):
        raise ItnRejected(f"unexpected sender {source_ip}")
    if not payfast.valid_signature(pairs):
        raise ItnRejected("bad signature")
    if data.get("merchant_id") != settings.PAYFAST_MERCHANT_ID:
        raise ItnRejected("wrong merchant")
    subscription = (
        Subscription.objects.select_related("customer", "plan").filter(reference=data.get("m_payment_id", "")).first()
    )
    if subscription is None:
        raise ItnRejected("unknown payment reference")
    gross = _money(data.get("amount_gross"))
    if gross is None or gross not in (subscription.initial_amount, subscription.recurring_amount):
        raise ItnRejected(f"amount {data.get('amount_gross')} does not match the subscription")
    if settings.PAYFAST_VALIDATE_ITN and not payfast.confirmed_by_payfast(pairs):
        raise ItnRejected("PayFast did not confirm the notification")
    return _apply(subscription.pk, data, gross)


@transaction.atomic
def _apply(subscription_id: int, data: dict, gross: Decimal) -> Payment | None:
    subscription = Subscription.objects.select_for_update().select_related("customer", "plan").get(pk=subscription_id)
    status = data.get("payment_status", "")
    payment = None
    pf_id = data.get("pf_payment_id", "")
    if pf_id:
        try:
            with transaction.atomic():
                payment, created = Payment.objects.get_or_create(
                    pf_payment_id=pf_id,
                    defaults={
                        "subscription": subscription,
                        "status": status,
                        "amount_gross": gross,
                        "amount_fee": _money(data.get("amount_fee")),
                    },
                )
        except IntegrityError:  # two copies of the same notification arrived together
            return Payment.objects.filter(pf_payment_id=pf_id).first()
        if not created and payment.status == status:
            return payment  # a repeat: nothing more to do
        if not created:
            payment.status = status
            payment.save(update_fields=["status"])
    actor = SubscriptionEvent.Actor.PAYFAST
    if status == "COMPLETE":
        _mark_paid(subscription, data)
    elif status == "FAILED" and subscription.status == Subscription.Status.ACTIVE:
        set_status(
            subscription,
            Subscription.Status.PAST_DUE,
            actor=actor,
            kind="payment_failed",
            detail=f"PayFast payment {pf_id}",
        )
    elif status == "CANCELLED" and subscription.status != Subscription.Status.CANCELLED:
        set_status(
            subscription,
            Subscription.Status.CANCELLED,
            actor=actor,
            kind="cancelled",
            detail="Cancelled at PayFast",
            cancelled_at=timezone.now(),
        )
    return payment


def _next_date(data: dict, subscription: Subscription) -> date:
    """PayFast's own next-collection date when it sends one, otherwise one period after today."""
    try:
        return date.fromisoformat(data.get("billing_date", ""))
    except ValueError:
        return add_months(timezone.localdate(), subscription.months_per_period)


def _mark_paid(subscription: Subscription, data: dict) -> None:
    first_time = subscription.activated_at is None
    fields = {"next_billing_date": _next_date(data, subscription), "paused_until": None}
    if data.get("token"):
        fields["payfast_token"] = data["token"]
    if first_time:
        fields["activated_at"] = timezone.now()
    kind = (
        "activated"
        if first_time
        else ("recovered" if subscription.status != Subscription.Status.ACTIVE else "payment_received")
    )
    set_status(
        subscription,
        Subscription.Status.ACTIVE,
        actor=SubscriptionEvent.Actor.PAYFAST,
        kind=kind,
        detail=f"PayFast payment {data.get('pf_payment_id', '')}",
        **fields,
    )
    if first_time:
        transaction.on_commit(lambda: _welcome(subscription))


def _welcome(subscription: Subscription) -> None:
    """Tell sales and the client, then announce the activation to any listener (provisioning). One failing step never blocks the others."""
    context = {
        "subscription": subscription,
        "customer": subscription.customer,
        "brand": settings.BRAND_NAME,
        "site_url": settings.SITE_URL,
        "admin_url": settings.ADMIN_URL,
    }
    try:
        send_mail(
            f"Welcome to {settings.BRAND_NAME}",
            render_to_string("emails/subscription_welcome.txt", context),
            settings.DEFAULT_FROM_EMAIL,
            [subscription.customer.email],
        )
        if settings.SALES_NOTIFY_EMAILS:
            send_mail(
                f"New subscription: {subscription.customer.business_name}",
                render_to_string("emails/subscription_notification.txt", context),
                settings.DEFAULT_FROM_EMAIL,
                settings.SALES_NOTIFY_EMAILS,
            )
    except Exception:  # noqa: BLE001
        logger.exception("Could not send subscription e-mails for %s", subscription.reference)
    for receiver, outcome in subscription_activated.send_robust(sender=Subscription, subscription=subscription):
        if isinstance(outcome, Exception):
            logger.error("subscription_activated receiver %r failed: %r", receiver, outcome)


def cancel(subscription: Subscription, *, actor: str = SubscriptionEvent.Actor.STAFF, detail: str = "") -> None:
    """Cancel at PayFast first; only when PayFast confirms is the subscription marked cancelled here."""
    if subscription.status == Subscription.Status.CANCELLED:
        return
    if subscription.payfast_token:
        payfast.cancel_subscription(subscription.payfast_token)
    set_status(
        subscription,
        Subscription.Status.CANCELLED,
        actor=actor,
        kind="cancelled",
        detail=detail,
        cancelled_at=timezone.now(),
    )
