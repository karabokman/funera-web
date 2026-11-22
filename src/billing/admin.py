from django.contrib import admin, messages

from . import payfast, services
from .models import Customer, Payment, Subscription


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0
    can_delete = False
    readonly_fields = ("pf_payment_id", "status", "amount_gross", "amount_fee", "received_at")

    def has_add_permission(self, request, obj=None):
        return False


@admin.action(description="Cancel selected subscriptions (stops PayFast collections)")
def cancel_subscriptions(modeladmin, request, queryset):
    for subscription in queryset:
        try:
            services.cancel(subscription)
        except payfast.GatewayUnavailable as exc:
            modeladmin.message_user(
                request,
                f"{subscription.reference}: PayFast did not confirm the cancellation ({exc}). Nothing was changed.",
                messages.ERROR,
            )
        else:
            modeladmin.message_user(request, f"{subscription.reference} cancelled.", messages.SUCCESS)


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ("reference", "customer", "plan", "status", "recurring_amount", "activated_at", "created_at")
    list_filter = ("status", "plan")
    search_fields = ("reference", "customer__business_name", "customer__email")
    readonly_fields = (
        "reference",
        "customer",
        "plan",
        "initial_amount",
        "recurring_amount",
        "payfast_token",
        "activated_at",
        "cancelled_at",
        "created_at",
        "status",
    )
    inlines = [PaymentInline]
    actions = [cancel_subscriptions]

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
