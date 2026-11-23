import secrets
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from catalog.models import Plan

CENT = Decimal("0.01")


def money(value) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def new_token() -> str:
    return secrets.token_urlsafe(32)


class Quote(models.Model):
    """A priced offer to one business. Staff enter the final price the client pays. ``vat_included`` says whether that
    price includes VAT (South African prices normally do); the VAT portion is then worked out from ``vat_rate``, which is
    copied at creation so a later change of rate never alters a quote that has already been sent."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SENT = "sent", "Sent"
        ACCEPTED = "accepted", "Accepted"
        DECLINED = "declined", "Declined"
        CANCELLED = "cancelled", "Cancelled"

    number = models.CharField(max_length=20, unique=True, editable=False)
    token = models.CharField(max_length=60, unique=True, default=new_token, editable=False)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT, db_index=True)
    lead = models.ForeignKey("leads.Lead", null=True, blank=True, on_delete=models.SET_NULL, related_name="quotes")

    contact_name = models.CharField(max_length=120)
    business_name = models.CharField(max_length=160)
    email = models.EmailField()
    phone = models.CharField(max_length=30)

    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="quotes")
    billing_period = models.CharField(
        max_length=8, choices=[("monthly", "Monthly"), ("annual", "Annual")], default="monthly"
    )
    recurring_price = models.DecimalField(
        max_digits=9, decimal_places=2, help_text="The final price charged each billing period."
    )
    vat_included = models.BooleanField(
        "Price includes VAT", default=True, help_text="Tick when the prices on this quotation include VAT."
    )
    vat_rate = models.DecimalField(max_digits=4, decimal_places=3, default=Decimal("0"), editable=False)
    valid_until = models.DateField()
    intro = models.TextField(blank=True, help_text="A short note shown at the top of the quotation.")
    internal_notes = models.TextField(blank=True, help_text="Never shown to the client.")

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_by = models.CharField(max_length=120, blank=True, help_text="Name the client typed when accepting.")
    decision_note = models.CharField(max_length=300, blank=True)
    terms_version = models.CharField(max_length=20, blank=True)
    subscription = models.OneToOneField(
        "billing.Subscription", null=True, blank=True, on_delete=models.SET_NULL, related_name="quote"
    )

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"{self.number} · {self.business_name}"

    # --- state -------------------------------------------------------------------------------------------
    @property
    def is_expired(self) -> bool:
        return self.status == self.Status.SENT and self.valid_until < timezone.localdate()

    @property
    def is_open(self) -> bool:
        """Sent, not yet decided and still within its validity: the client may accept it."""
        return self.status == self.Status.SENT and not self.is_expired

    @property
    def is_editable(self) -> bool:
        return self.status == self.Status.DRAFT

    @property
    def display_status(self) -> str:
        return "Expired" if self.is_expired else self.get_status_display()

    @property
    def months_per_period(self) -> int:
        return 12 if self.billing_period == "annual" else 1

    # --- totals (always computed from the lines, never stored, so they cannot disagree with them) ----------
    # The entered prices are what the client pays. When they include VAT, the VAT portion is gross - gross / (1 + rate).
    @property
    def has_vat(self) -> bool:
        return self.vat_included and self.vat_rate > 0

    def _vat_in(self, gross: Decimal) -> Decimal:
        return money(gross - gross / (1 + self.vat_rate)) if self.has_vat else Decimal("0.00")

    @property
    def recurring_gross(self) -> Decimal:
        return money(self.recurring_price)

    @property
    def recurring_vat(self) -> Decimal:
        return self._vat_in(self.recurring_gross)

    @property
    def recurring_net(self) -> Decimal:
        return self.recurring_gross - self.recurring_vat

    @property
    def once_off_gross(self) -> Decimal:
        return money(sum((line.total for line in self.lines.all()), Decimal("0")))

    @property
    def once_off_vat(self) -> Decimal:
        return self._vat_in(self.once_off_gross)

    @property
    def once_off_net(self) -> Decimal:
        return self.once_off_gross - self.once_off_vat

    @property
    def total_vat(self) -> Decimal:
        return self.recurring_vat + self.once_off_vat

    @property
    def first_payment_gross(self) -> Decimal:
        """What PayFast collects on the first payment: the first period plus every once-off line."""
        return self.recurring_gross + self.once_off_gross

    @property
    def monthly_equivalent(self) -> Decimal:
        return money(self.recurring_gross / self.months_per_period)


class QuoteLine(models.Model):
    """A once-off item (set-up, training, data import). A negative price is a discount."""

    quote = models.ForeignKey(Quote, on_delete=models.CASCADE, related_name="lines")
    description = models.CharField(max_length=200)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=9, decimal_places=2, help_text="Negative for a discount.")
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self) -> str:
        return self.description

    @property
    def total(self) -> Decimal:
        return money(self.unit_price * self.quantity)


class QuoteEvent(models.Model):
    """Append-only history of a quotation: created, sent, viewed, accepted, declined, cancelled."""

    quote = models.ForeignKey(Quote, on_delete=models.CASCADE, related_name="events")
    kind = models.CharField(max_length=30)
    detail = models.CharField(max_length=300, blank=True)
    actor = models.CharField(max_length=60, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"{self.quote.number} {self.kind}"
