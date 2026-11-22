"""What a client (or staff) can do to a live subscription: pause, resume, switch between monthly and annual, cancel.

Each change is made at PayFast first; the local record changes only once PayFast confirms, so the two never disagree.
Prices charged to existing clients never change by themselves: switching period uses the plan's current price.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone

from . import payfast, services
from .errors import BillingError
from .models import Subscription, SubscriptionEvent

logger = logging.getLogger(__name__)
Actor = SubscriptionEvent.Actor
Status = Subscription.Status


def _need_token(subscription: Subscription) -> str:
    if not subscription.payfast_token:
        raise BillingError("This subscription is not linked to a PayFast subscription yet. Please contact us.")
    return subscription.payfast_token


def _tell_staff(subscription: Subscription, headline: str) -> None:
    """E-mail sales about a client-made change. A mail failure is logged and never undoes the change."""
    if not settings.SALES_NOTIFY_EMAILS:
        return
    context = {
        "subscription": subscription,
        "customer": subscription.customer,
        "headline": headline,
        "site_url": settings.SITE_URL,
        "admin_url": settings.ADMIN_URL,
    }

    def send():
        try:
            send_mail(
                f"{headline}: {subscription.customer.business_name}",
                render_to_string("emails/subscription_event.txt", context),
                settings.DEFAULT_FROM_EMAIL,
                settings.SALES_NOTIFY_EMAILS,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Could not e-mail staff about %s", subscription.reference)

    transaction.on_commit(send)


def pause(subscription: Subscription, cycles: int, *, actor: str = Actor.CLIENT) -> None:
    if subscription.status != Status.ACTIVE:
        raise BillingError("Only an active subscription can be paused.")
    if not 1 <= cycles <= settings.PAUSE_MAX_CYCLES:
        raise BillingError(f"You can pause for 1 to {settings.PAUSE_MAX_CYCLES} billing periods.")
    payfast.pause_subscription(_need_token(subscription), cycles)
    resumes = services.add_months(
        subscription.next_billing_date or timezone.localdate(), cycles * subscription.months_per_period
    )
    unit = "year" if subscription.months_per_period == 12 else "month"
    with transaction.atomic():
        services.set_status(
            subscription,
            Status.PAUSED,
            actor=actor,
            kind="paused",
            detail=f"Skipping {cycles} {unit}(s); collections restart about {resumes:%d %b %Y}",
            paused_until=resumes,
        )
    _tell_staff(subscription, "Subscription paused")


def resume(subscription: Subscription, *, actor: str = Actor.CLIENT) -> None:
    if subscription.status != Status.PAUSED:
        raise BillingError("This subscription is not paused.")
    payfast.unpause_subscription(_need_token(subscription))
    with transaction.atomic():
        services.set_status(subscription, Status.ACTIVE, actor=actor, kind="resumed", paused_until=None)
    _tell_staff(subscription, "Subscription resumed")


def switch_period(subscription: Subscription, new_period: str, *, actor: str = Actor.CLIENT) -> None:
    """Move to monthly or annual billing from the next collection. What is already paid is not refunded or prorated."""
    if subscription.status != Status.ACTIVE:
        raise BillingError("Billing can be changed only on an active subscription.")
    if new_period == subscription.billing_period:
        raise BillingError("You are already on that billing period.")
    plan = subscription.plan
    if new_period == Subscription.Period.ANNUAL:
        if not (plan.has_annual and plan.is_active):
            raise BillingError("Annual billing is not available for this plan.")
        amount = plan.annual_price
    elif new_period == Subscription.Period.MONTHLY:
        if not plan.has_price:
            raise BillingError("Monthly billing is not available for this plan.")
        amount = plan.monthly_price
    else:
        raise BillingError("Unknown billing period.")
    run_date = subscription.next_billing_date
    if run_date is None or run_date <= timezone.localdate():
        raise BillingError(
            "The next billing date is not known yet. Please try again after your next payment, or contact us."
        )
    frequency = "6" if new_period == Subscription.Period.ANNUAL else "3"
    payfast.update_subscription(_need_token(subscription), amount=amount, frequency=frequency, run_date=run_date)
    old = subscription.get_billing_period_display()
    with transaction.atomic():
        subscription.billing_period = new_period
        subscription.recurring_amount = amount
        services.set_status(
            subscription,
            subscription.status,
            actor=actor,
            kind="billing_period_changed",
            detail=f"{old} to {subscription.get_billing_period_display()}: R{amount} from {run_date:%d %b %Y}",
        )
    _tell_staff(subscription, "Billing period changed")


def cancel(subscription: Subscription, *, actor: str = Actor.CLIENT, reason: str = "") -> None:
    if subscription.status == Status.PENDING:
        raise BillingError("There is nothing to cancel yet.")
    if subscription.status == Status.CANCELLED:
        raise BillingError("This subscription is already cancelled.")
    services.cancel(subscription, actor=actor, detail=reason[:300])
    _tell_staff(subscription, "Subscription cancelled")
