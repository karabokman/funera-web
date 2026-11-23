from django.contrib import admin, messages

from . import lifecycle, payfast, refunds
from .errors import BillingError
from .models import Customer, Payment, Refund, Subscription, SubscriptionEvent

STAFF = SubscriptionEvent.Actor.STAFF


def _each(modeladmin, request, queryset, action, done, audit_action):
    """Run ``action(obj)`` on every selected row and report each outcome; one failure never stops the others.
    Every success is written to the audit log with the signed-in user."""
    from backoffice import audit

    for obj in queryset:
        try:
            action(obj)
        except (payfast.GatewayUnavailable, BillingError) as exc:
            modeladmin.message_user(request, f"{obj}: not done ({exc}).", messages.ERROR)
        else:
            audit.record(request, audit_action, obj, "via the site admin")
            modeladmin.message_user(request, f"{obj}: {done}.", messages.SUCCESS)


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0
    can_delete = False
    readonly_fields = ("pf_payment_id", "status", "amount_gross", "amount_fee", "received_at")

    def has_add_permission(self, request, obj=None):
        return False


class EventInline(admin.TabularInline):
    model = SubscriptionEvent
    extra = 0
    can_delete = False
    readonly_fields = ("created_at", "actor", "kind", "detail")
    fields = readonly_fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.action(description="Cancel selected subscriptions (stops PayFast collections)")
def cancel_subscriptions(modeladmin, request, queryset):
    _each(
        modeladmin, request, queryset, lambda s: lifecycle.cancel(s, actor=STAFF), "cancelled", "subscription.cancelled"
    )


@admin.action(description="Pause selected subscriptions for one billing period")
def pause_subscriptions(modeladmin, request, queryset):
    _each(modeladmin, request, queryset, lambda s: lifecycle.pause(s, 1, actor=STAFF), "paused", "subscription.paused")


@admin.action(description="Resume selected paused subscriptions")
def resume_subscriptions(modeladmin, request, queryset):
    _each(modeladmin, request, queryset, lambda s: lifecycle.resume(s, actor=STAFF), "resumed", "subscription.resumed")


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = (
        "reference",
        "customer",
        "plan",
        "status",
        "billing_period",
        "recurring_amount",
        "next_billing_date",
        "created_at",
    )
    list_filter = ("status", "billing_period", "plan")
    search_fields = ("reference", "customer__business_name", "customer__email")
    readonly_fields = (
        "reference", "customer", "plan", "billing_period", "status", "initial_amount", "recurring_amount",
        "next_billing_date", "paused_until", "payfast_token", "activated_at", "cancelled_at", "created_at",
    )  # fmt: skip
    inlines = [PaymentInline, EventInline]
    actions = [pause_subscriptions, resume_subscriptions, cancel_subscriptions]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False  # financial records are kept


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("business_name", "name", "email", "phone", "created_at")
    search_fields = ("business_name", "name", "email")
    readonly_fields = ("terms_accepted_at", "terms_version", "notice_version", "visitor_hash", "created_at")

    def has_delete_permission(self, request, obj=None):
        return False


@admin.action(description="Process selected refunds (refund the card via PayFast)")
def process_refunds(modeladmin, request, queryset):
    _each(modeladmin, request, queryset, refunds.process, "refunded", "refund.processed")


@admin.action(description="Decline selected refunds")
def decline_refunds(modeladmin, request, queryset):
    _each(modeladmin, request, queryset, refunds.decline, "declined", "refund.declined")


@admin.register(Refund)
class RefundAdmin(admin.ModelAdmin):
    list_display = ("payment", "amount", "status", "requested_by", "created_at", "processed_at")
    list_filter = ("status", "requested_by")
    search_fields = (
        "payment__pf_payment_id",
        "payment__subscription__reference",
        "payment__subscription__customer__business_name",
    )
    readonly_fields = ("status", "requested_by", "created_at", "processed_at")
    actions = [process_refunds, decline_refunds]

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        if not change:
            obj.requested_by = STAFF  # a refund raised by staff in the admin
        super().save_model(request, obj, form, change)
