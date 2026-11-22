import secrets
import string

from django.db import models

from catalog.models import Plan


def new_reference() -> str:
    """An unguessable, printable reference. It is also PayFast's ``m_payment_id`` and the key of the checkout pages."""
    alphabet = string.ascii_uppercase + string.digits
    return "FN-" + "".join(secrets.choice(alphabet) for _ in range(12))


class Customer(models.Model):
    """A business that has subscribed (or started to). Financial records are kept for the legal retention period."""

    name = models.CharField(max_length=120)
    business_name = models.CharField(max_length=160)
    email = models.EmailField()
    phone = models.CharField(max_length=30)
    lead = models.ForeignKey("leads.Lead", null=True, blank=True, on_delete=models.SET_NULL, related_name="customers")
    terms_accepted_at = models.DateTimeField()
    terms_version = models.CharField(max_length=20)
    notice_version = models.CharField(max_length=20)
    visitor_hash = models.CharField(max_length=64, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.business_name


class Subscription(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Awaiting first payment"
        ACTIVE = "active", "Active"
        PAST_DUE = "past_due", "Payment failed"
        CANCELLED = "cancelled", "Cancelled"

    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="subscriptions")
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="subscriptions")
    reference = models.CharField(max_length=20, unique=True, default=new_reference, editable=False)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True)
    # Prices are copied at sign-up so a later price change never alters what an existing client is charged.
    initial_amount = models.DecimalField(
        max_digits=9, decimal_places=2, help_text="First payment: set-up fee plus first month."
    )
    recurring_amount = models.DecimalField(max_digits=9, decimal_places=2, help_text="Charged every month after that.")
    payfast_token = models.CharField(
        max_length=64, blank=True, help_text="PayFast's subscription token, needed to cancel."
    )
    activated_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.reference} · {self.customer} · {self.plan.name}"

    @property
    def can_pay(self) -> bool:
        return self.status == self.Status.PENDING


class Payment(models.Model):
    """One PayFast notification that was verified. ``pf_payment_id`` is unique so a repeated notification changes nothing."""

    subscription = models.ForeignKey(Subscription, on_delete=models.PROTECT, related_name="payments")
    pf_payment_id = models.CharField(max_length=40, unique=True)
    status = models.CharField(max_length=20)
    amount_gross = models.DecimalField(max_digits=9, decimal_places=2)
    amount_fee = models.DecimalField(max_digits=9, decimal_places=2, null=True, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-received_at"]

    def __str__(self) -> str:
        return f"{self.pf_payment_id} {self.status} R{self.amount_gross}"
