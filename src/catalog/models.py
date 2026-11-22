"""What Funera sells: plans and the features each plan includes.

``Plan.code`` is the value the platform expects in its ``LICENSE_PACKAGE`` setting, so a future provisioning service can
create a customer's instance straight from the plan they bought. Plans are cumulative: a feature belongs to the lowest
plan that has it and every higher plan includes it too."""

from django.db import models


class Plan(models.Model):
    code = models.SlugField(
        unique=True, help_text="Matches the platform's LICENSE_PACKAGE: starter, professional or enterprise."
    )
    name = models.CharField(max_length=60)
    tagline = models.CharField(max_length=140)
    best_for = models.CharField(max_length=200, blank=True)
    monthly_price = models.DecimalField(
        max_digits=9,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Rand per month. Leave empty to show 'Get a quote'.",
    )
    setup_fee = models.DecimalField(
        max_digits=9,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Once-off set-up fee in rand. Leave empty to hide.",
    )
    price_note = models.CharField(
        max_length=140, blank=True, help_text="Small print under the price, e.g. 'excl. VAT'."
    )
    is_featured = models.BooleanField(default=False, help_text="Highlight this plan as the recommended one.")
    is_active = models.BooleanField(default=True, help_text="Untick to stop showing and selling this plan.")
    order = models.PositiveSmallIntegerField(
        default=0, help_text="Lowest first. Higher plans include everything in lower ones."
    )

    class Meta:
        ordering = ["order", "id"]

    def __str__(self) -> str:
        return self.name

    @property
    def has_price(self) -> bool:
        return self.monthly_price is not None


class Feature(models.Model):
    class Category(models.TextChoices):
        MEMBERS = "members", "Members and policies"
        MONEY = "money", "Premiums and payments"
        CLAIMS = "claims", "Claims and funerals"
        COMMS = "comms", "Communication"
        GROWTH = "growth", "Growth and control"
        TRUST = "trust", "Security and compliance"

    category = models.CharField(max_length=12, choices=Category.choices)
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=300)
    available_from = models.ForeignKey(
        Plan,
        on_delete=models.PROTECT,
        related_name="introduced_features",
        help_text="The lowest plan that includes this feature.",
    )
    is_highlight = models.BooleanField(default=False, help_text="Show on the home page.")
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["category", "order", "id"]

    def __str__(self) -> str:
        return self.name

    ICONS = {
        "members": "users",
        "money": "wallet",
        "claims": "file",
        "comms": "chat",
        "growth": "chart",
        "trust": "shield",
    }

    @property
    def icon(self) -> str:
        return self.ICONS.get(self.category, "check")

    def included_in(self, plan: Plan) -> bool:
        return self.available_from.order <= plan.order
