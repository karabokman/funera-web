from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from catalog.models import Plan
from leads.models import Lead
from quotes.models import Quote, QuoteLine


def make_lead(**extra):
    defaults = dict(
        name="Thandi Mokoena",
        business_name="Mokoena Funerals",
        email="thandi@example.com",
        phone="0821234567",
        consent_given=True,
        consent_at=timezone.now(),
    )
    return Lead.objects.create(**{**defaults, **extra})


def make_quote(
    status=Quote.Status.DRAFT,
    price="1500.00",
    lines=(("Set-up", 1, "2500.00"),),
    vat="0",
    vat_included=True,
    valid_days=30,
    lead=None,
    **extra,
):
    quote = Quote.objects.create(
        number=extra.pop("number", f"QT-2026-{Quote.objects.count() + 1:04d}"), status=status, lead=lead, vat_included=vat_included, contact_name="Thandi Mokoena",
        business_name="Mokoena Funerals", email="thandi@example.com", phone="0821234567", plan=Plan.objects.get(code="professional"),
        recurring_price=Decimal(price), vat_rate=Decimal(vat), valid_until=timezone.localdate() + timedelta(days=valid_days), **extra,
    )  # fmt: skip
    for order, (description, qty, unit) in enumerate(lines):
        QuoteLine.objects.create(
            quote=quote, description=description, quantity=qty, unit_price=Decimal(unit), order=order
        )
    if status == Quote.Status.SENT:
        quote.sent_at = timezone.now()
        quote.save(update_fields=["sent_at"])
    return quote
