from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

from billing.models import Customer, Subscription


class Instance(models.Model):
    """One client's own copy of the funeral-cover platform that you host for them: where it runs, which version, how far
    set-up has got, when it went live and who at the client holds its administrator login. It is a record only: nothing
    here starts, stops or changes a server, and passwords are never stored."""

    class Status(models.TextChoices):
        AWAITING = "awaiting", "Awaiting set-up"
        SETTING_UP = "setting_up", "Being set up"
        LIVE = "live", "Live"
        SUSPENDED = "suspended", "Suspended"
        RETIRED = "retired", "Retired"

    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="instances")
    subscription = models.OneToOneField(
        Subscription, null=True, blank=True, on_delete=models.SET_NULL, related_name="instance"
    )
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.AWAITING, db_index=True)
    url = models.URLField(
        blank=True, help_text="The address the client's staff use, e.g. https://mokoena.example.co.za"
    )
    host = models.CharField(
        max_length=120, blank=True, help_text="Where it runs, e.g. the HCode project or server name."
    )
    version = models.CharField(max_length=30, blank=True, help_text="The platform version deployed, e.g. 1.4.2")
    version_updated_on = models.DateField(null=True, blank=True)

    go_live_target = models.DateField(null=True, blank=True, help_text="The date you promised the client.")
    provisioned_on = models.DateField(null=True, blank=True, help_text="The instance was created and reachable.")
    data_imported_on = models.DateField(
        null=True, blank=True, help_text="Their existing members and policies were loaded."
    )
    login_sent_on = models.DateField(null=True, blank=True, help_text="Their administrator was given access.")
    training_done_on = models.DateField(null=True, blank=True)
    went_live_on = models.DateField(null=True, blank=True)

    admin_name = models.CharField(
        "Administrator", max_length=120, blank=True, help_text="Who at the client holds the administrator login."
    )
    admin_email = models.EmailField("Administrator e-mail", blank=True)
    admin_login_note = models.CharField(
        max_length=300, blank=True, help_text="e.g. the username and how it was delivered. Never type a password here."
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name="Looked after by",
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["customer__business_name", "id"]

    def __str__(self) -> str:
        return f"{self.customer.business_name} ({self.get_status_display()})"

    CHECKLIST = [
        ("provisioned_on", "Instance created"),
        ("data_imported_on", "Data imported"),
        ("login_sent_on", "Administrator login given"),
        ("training_done_on", "Training done"),
        ("went_live_on", "Went live"),
    ]

    @property
    def checklist(self) -> list[tuple[str, object]]:
        return [(label, getattr(self, field)) for field, label in self.CHECKLIST]

    @property
    def is_open(self) -> bool:
        return self.status in (self.Status.AWAITING, self.Status.SETTING_UP)

    @property
    def days_waiting(self) -> int:
        return (timezone.localdate() - self.created_at.date()).days if self.is_open else 0

    @property
    def is_overdue(self) -> bool:
        """Still being set up after the promised go-live date."""
        return self.is_open and self.go_live_target is not None and self.go_live_target < timezone.localdate()

    @property
    def is_outdated(self) -> bool:
        latest = settings.FUNERA_LATEST_VERSION
        return bool(latest) and self.status == self.Status.LIVE and self.version != latest

    @property
    def billing_flag(self) -> str:
        """A plain-words warning when what the client pays no longer matches what is running for them."""
        sub = self.subscription
        if sub is None or self.status in (self.Status.RETIRED, self.Status.AWAITING, self.Status.SETTING_UP):
            return ""
        if sub.status == Subscription.Status.CANCELLED:
            return "Subscription cancelled: decide when to retire this instance."
        if sub.status == Subscription.Status.PAST_DUE:
            return "Payment failed: follow up before suspending."
        if sub.status == Subscription.Status.PAUSED and self.status == self.Status.LIVE:
            return "Subscription paused: consider suspending this instance."
        return ""

    @property
    def stale_for_setup(self) -> bool:
        return self.is_open and timezone.localdate() - self.created_at.date() > timedelta(
            days=settings.INSTANCE_SETUP_ALERT_DAYS
        )
