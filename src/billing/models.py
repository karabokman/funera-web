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
        PAUSED = "paused", "Paused"
        CANCELLED = "cancelled", "Cancelled"

    class Period(models.TextChoices):
        MONTHLY = "monthly", "Monthly"
        ANNUAL = "annual", "Annual"

    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="subscriptions")
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="subscriptions")
    reference = models.CharField(max_length=20, unique=True, default=new_reference, editable=False)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True)
    billing_period = models.CharField(max_length=8, choices=Period.choices, default=Period.MONTHLY)
    # Prices are copied at sign-up so a later price change never alters what an existing client is charged.
    initial_amount = models.DecimalField(
        max_digits=9, decimal_places=2, help_text="First payment: set-up fee plus the first period."
    )
    recurring_amount = models.DecimalField(max_digits=9, decimal_places=2, help_text="Charged every period after that.")
    next_billing_date = models.DateField(null=True, blank=True)
    paused_until = models.DateField(
        null=True, blank=True, help_text="Approximate date collections restart after a pause."
    )
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
    def months_per_period(self) -> int:
        return 12 if self.billing_period == self.Period.ANNUAL else 1

    @property
    def payfast_frequency(self) -> str:
        return "6" if self.billing_period == self.Period.ANNUAL else "3"

    @property
    def is_live(self) -> bool:
        """Active or paused: a client the portal still manages."""
        return self.status in (self.Status.ACTIVE, self.Status.PAST_DUE, self.Status.PAUSED)

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


class SubscriptionEvent(models.Model):
    """Append-only audit trail of what changed on a subscription, and who did it."""

    class Actor(models.TextChoices):
        CLIENT = "client", "Client"
        STAFF = "staff", "Staff"
        PAYFAST = "payfast", "PayFast"
        SYSTEM = "system", "System"

    subscription = models.ForeignKey(Subscription, on_delete=models.PROTECT, related_name="events")
    kind = models.CharField(max_length=30)
    detail = models.CharField(max_length=300, blank=True)
    actor = models.CharField(max_length=10, choices=Actor.choices, default=Actor.SYSTEM)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"{self.subscription.reference} {self.kind}"


class Refund(models.Model):
    class Status(models.TextChoices):
        REQUESTED = "requested", "Requested"
        PROCESSED = "processed", "Refunded"
        DECLINED = "declined", "Declined"
        FAILED = "failed", "Failed at PayFast"

    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="refunds")
    amount = models.DecimalField(max_digits=9, decimal_places=2)
    reason = models.CharField(max_length=300)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.REQUESTED, db_index=True)
    requested_by = models.CharField(
        max_length=10, choices=SubscriptionEvent.Actor.choices, default=SubscriptionEvent.Actor.CLIENT
    )
    staff_note = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Refund R{self.amount} on {self.payment.pf_payment_id} ({self.status})"


class LoginToken(models.Model):
    """A single-use, short-lived sign-in link for the client portal. Only a hash of the token is stored."""

    email = models.EmailField(db_index=True)
    token_hash = models.CharField(max_length=64, unique=True)
    visitor_hash = models.CharField(max_length=64, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    used_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"Sign-in link for {self.email}"
