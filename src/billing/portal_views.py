"""The client portal: sign in by e-mailed link, see subscriptions and payments, pause, switch period, cancel, ask for a refund."""

import logging

from django.conf import settings
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.cache import add_never_cache_headers
from django.views import View
from django.views.generic import FormView, TemplateView

from leads.services import visitor_hash

from . import lifecycle, payfast, portal_auth, refunds
from .errors import BillingError
from .forms import CancelForm, PauseForm, PortalLoginForm, RefundRequestForm, SwitchForm
from .models import Payment, Subscription

logger = logging.getLogger(__name__)


class LoginView(FormView):
    template_name = "billing/portal/login.html"
    form_class = PortalLoginForm

    def form_valid(self, form):
        portal_auth.request_link(form.cleaned_data["email"], visitor_hash(self.request))
        return render(
            self.request, "billing/portal/login_sent.html", {"page_title": "Check your e-mail"}
        )  # same answer whether or not the address is known

    def get_context_data(self, **kwargs):
        return super().get_context_data(page_title="Client portal", **kwargs)


class LoginConfirmView(View):
    """The e-mailed link opens a page with a button; the button signs in. Opening a link alone (mail scanners do) uses nothing up."""

    def get(self, request, token):
        return render(request, "billing/portal/login_confirm.html", {"page_title": "Sign in"})

    def post(self, request, token):
        email = portal_auth.redeem(token)
        if email is None:
            return render(request, "billing/portal/login_expired.html", {"page_title": "Link expired"}, status=400)
        portal_auth.sign_in(request, email)
        return redirect("billing:portal")


class LogoutView(View):
    def post(self, request):
        portal_auth.sign_out(request)
        return redirect("billing:portal_login")


class PortalMixin:
    """Every portal page needs a signed-in client; subscriptions are always looked up through their e-mail address."""

    def dispatch(self, request, *args, **kwargs):
        self.email = request.session.get(portal_auth.SESSION_KEY)
        if not self.email:
            return redirect("billing:portal_login")
        response = super().dispatch(request, *args, **kwargs)
        add_never_cache_headers(response)  # account pages are never kept by the browser or a proxy
        return response

    def my_subscriptions(self):
        return portal_auth.subscriptions_for(self.email)

    def my_subscription(self, reference):
        return get_object_or_404(self.my_subscriptions(), reference=reference)


class DashboardView(PortalMixin, TemplateView):
    template_name = "billing/portal/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(email=self.email, subscriptions=self.my_subscriptions(), page_title="Your subscriptions")
        return context


class SubscriptionView(PortalMixin, TemplateView):
    template_name = "billing/portal/subscription.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        subscription = self.my_subscription(kwargs["reference"])
        payments = []
        for payment in subscription.payments.prefetch_related("refunds"):
            open_request = any(r.status == "requested" for r in payment.refunds.all())
            payments.append({
                "payment": payment, "refunded": refunds.refunded_amount(payment), "open_request": open_request,
                "can_request": payment.status == "COMPLETE" and refunds.refundable_amount(payment) > 0 and refunds.within_window(payment) and not open_request,
            })  # fmt: skip
        plan = subscription.plan
        other = "monthly" if subscription.billing_period == "annual" else "annual"
        can_switch = subscription.status == Subscription.Status.ACTIVE and (
            plan.has_annual and plan.is_active if other == "annual" else plan.has_price
        )
        context.update(
            email=self.email, subscription=subscription, payments=payments, page_title=f"{plan.name} plan",
            pause_form=PauseForm(subscription=subscription), cancel_form=CancelForm(), other_period=other,
            other_price=plan.annual_price if other == "annual" else plan.monthly_price, can_switch=can_switch,
            events=subscription.events.all()[:10], max_pause=settings.PAUSE_MAX_CYCLES, refund_days=settings.REFUND_WINDOW_DAYS,
        )  # fmt: skip
        return context


class ActionView(PortalMixin, View):
    """Base for the buttons on the subscription page: POST only, PayFast errors become a friendly message, then back to the page."""

    http_method_names = ["post"]
    success = ""

    def post(self, request, reference):
        subscription = self.my_subscription(reference)
        try:
            self.act(subscription, request)
        except BillingError as exc:
            messages.error(request, str(exc))
        except payfast.GatewayUnavailable:
            logger.exception("PayFast did not confirm %s for %s", type(self).__name__, subscription.reference)
            messages.error(
                request,
                "We could not reach PayFast to make that change, so nothing was changed. Please try again in a few minutes, or contact us.",
            )
        else:
            messages.success(request, self.success)
        return redirect("billing:portal_subscription", reference=subscription.reference)

    def act(self, subscription, request):
        raise NotImplementedError


class PauseView(ActionView):
    success = "Your subscription is paused."

    def act(self, subscription, request):
        form = PauseForm(request.POST, subscription=subscription)
        if not form.is_valid():
            raise BillingError("Choose how long to pause for.")
        lifecycle.pause(subscription, form.cleaned_data["cycles"])


class ResumeView(ActionView):
    success = "Your subscription is active again."

    def act(self, subscription, request):
        lifecycle.resume(subscription)


class SwitchView(ActionView):
    success = "Your billing period has been changed. It takes effect at your next payment."

    def act(self, subscription, request):
        form = SwitchForm(request.POST)
        if not form.is_valid():
            raise BillingError("Choose a billing period.")
        lifecycle.switch_period(subscription, form.cleaned_data["billing_period"])


class CancelView(ActionView):
    success = "Your subscription is cancelled. No further payments will be collected."

    def act(self, subscription, request):
        form = CancelForm(request.POST)
        if not form.is_valid():
            raise BillingError("Please tick the box to confirm you want to cancel.")
        lifecycle.cancel(subscription, reason=form.cleaned_data["reason"])


class RefundRequestView(PortalMixin, FormView):
    template_name = "billing/portal/refund.html"
    form_class = RefundRequestForm

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.payment_id = kwargs.get("pf_payment_id")

    def get_payment(self) -> Payment:
        return get_object_or_404(
            Payment.objects.select_related("subscription__plan", "subscription__customer"),
            pf_payment_id=self.payment_id,
            subscription__in=self.my_subscriptions(),
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        payment = self.get_payment()
        context.update(
            payment=payment,
            refundable=refunds.refundable_amount(payment),
            refund_days=settings.REFUND_WINDOW_DAYS,
            page_title="Request a refund",
        )
        return context

    def form_valid(self, form):
        payment = self.get_payment()
        try:
            refunds.request_refund(payment, form.cleaned_data["reason"])
        except BillingError as exc:
            messages.error(self.request, str(exc))
        else:
            messages.success(self.request, "Your refund request was sent. We will reply by e-mail.")
        return redirect("billing:portal_subscription", reference=payment.subscription.reference)
