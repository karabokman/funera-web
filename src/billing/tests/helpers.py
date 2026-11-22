from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlencode

from django.utils import timezone

from billing import payfast
from billing.models import Customer, Subscription
from catalog.models import Plan

PASSPHRASE = "test-passphrase"


def priced_plan(code="starter", price="1500.00", setup="2500.00", annual=None):
    Plan.objects.filter(code=code).update(
        monthly_price=Decimal(price),
        setup_fee=Decimal(setup) if setup else None,
        annual_price=Decimal(annual) if annual else None,
    )
    return Plan.objects.get(code=code)


def make_subscription(
    plan=None,
    status=Subscription.Status.PENDING,
    period=Subscription.Period.MONTHLY,
    email="thandi@example.com",
    **extra,
):
    plan = plan or priced_plan()
    customer = Customer.objects.create(
        name="Thandi Mokoena",
        business_name="Mokoena Funerals",
        email=email,
        phone="0821234567",
        terms_accepted_at=timezone.now(),
        terms_version="1.0",
        notice_version="1.0",
    )
    recurring = plan.annual_price if period == Subscription.Period.ANNUAL else plan.monthly_price
    return Subscription.objects.create(
        customer=customer,
        plan=plan,
        status=status,
        billing_period=period,
        initial_amount=recurring + (plan.setup_fee or 0),
        recurring_amount=recurring,
        **extra,
    )


def live_subscription(**extra):
    """An active, PayFast-linked subscription with a known next billing date, as the portal needs."""
    extra.setdefault("payfast_token", "tok-123")
    extra.setdefault("next_billing_date", timezone.localdate() + timedelta(days=20))
    return make_subscription(status=Subscription.Status.ACTIVE, **extra)


def itn_pairs(
    subscription,
    *,
    status="COMPLETE",
    amount=None,
    pf_id="1089250",
    token="tok-123",
    merchant="10000100",
    passphrase=PASSPHRASE,
    sign=True,
):
    amount = amount if amount is not None else subscription.initial_amount
    pairs = [
        ("m_payment_id", subscription.reference), ("pf_payment_id", pf_id), ("payment_status", status),
        ("item_name", "Funera Starter plan"), ("item_description", ""), ("amount_gross", f"{amount:.2f}"),
        ("amount_fee", "-35.00"), ("amount_net", "3965.00"), ("custom_str1", ""), ("name_first", "Thandi"),
        ("name_last", "Mokoena"), ("email_address", "thandi@example.com"), ("merchant_id", merchant), ("token", token),
    ]  # fmt: skip
    if sign:
        pairs.append(("signature", payfast.itn_signature(pairs, passphrase)))
    return pairs


def post_itn(client, pairs, **extra):
    return client.post(
        "/billing/payfast/itn/", urlencode(pairs), content_type="application/x-www-form-urlencoded", **extra
    )
