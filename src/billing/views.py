import logging
from urllib.parse import parse_qsl

from django.conf import settings
from django.contrib import messages
from django.http import Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import FormView, TemplateView

from catalog.models import Plan
from leads.services import visitor_hash

from . import payfast, services
from .forms import SubscribeForm
from .models import Subscription

logger = logging.getLogger(__name__)


class SubscribeView(FormView):
    """Collect the client's details, record the sign-up, then hand over to PayFast."""

    template_name = "billing/subscribe.html"
    form_class = SubscribeForm

    def dispatch(self, request, *args, **kwargs):
        self.plan = get_object_or_404(Plan, code=kwargs["plan"], is_active=True)
        if not services.plan_is_purchasable(self.plan):
            return redirect(f"{reverse('leads:contact')}?plan={self.plan.code}")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            plan=self.plan,
            page_title=f"Subscribe to {self.plan.name}",
            terms_version=settings.SUBSCRIPTION_TERMS_VERSION,
        )
        context["first_payment"] = self.plan.monthly_price + (self.plan.setup_fee or 0)
        return context

    def form_valid(self, form):
        visitor = visitor_hash(self.request)
        if services.is_rate_limited(visitor):
            messages.error(self.request, "Too many sign-up attempts. Please try again later, or e-mail us.")
            return self.form_invalid(form)
        subscription = services.create_subscription(plan=self.plan, cleaned=form.cleaned_data, visitor_hash=visitor)
        return redirect("billing:pay", reference=subscription.reference)


def _open_subscription(reference: str) -> Subscription:
    return get_object_or_404(Subscription.objects.select_related("customer", "plan"), reference=reference)


class PayView(TemplateView):
    """A summary and one button that posts the signed form to PayFast (no JavaScript is needed)."""

    template_name = "billing/pay.html"

    def get(self, request, *args, **kwargs):
        subscription = _open_subscription(kwargs["reference"])
        if not subscription.can_pay:
            return redirect("billing:return", reference=subscription.reference)
        if not services.billing_enabled():
            raise Http404
        return self.render_to_response(
            {
                "subscription": subscription,
                "checkout": services.build_checkout(subscription),
                "page_title": "Confirm and pay",
            }
        )


class ReturnView(TemplateView):
    """Where PayFast sends the client back. It only displays the status we hold: activation comes from the notification."""

    template_name = "billing/return.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(subscription=_open_subscription(kwargs["reference"]), page_title="Payment received")
        return context


class CancelledView(TemplateView):
    template_name = "billing/cancelled.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(subscription=_open_subscription(kwargs["reference"]), page_title="Payment cancelled")
        return context


@method_decorator(csrf_exempt, name="dispatch")
class PayFastITNView(View):
    """PayFast's server-to-server notification. Always answers 200 for genuine notifications (even repeats),
    400 for ones that fail a check and 503 when PayFast cannot be reached to confirm, so that it retries."""

    http_method_names = ["post"]

    def post(self, request):
        pairs = parse_qsl(
            request.body.decode("utf-8", "replace"), keep_blank_values=True
        )  # keep PayFast's order: it is signed
        try:
            services.handle_itn(pairs, services.client_ip(request))
        except services.ItnRejected as exc:
            logger.warning("PayFast notification rejected: %s", exc)
            return HttpResponseBadRequest()
        except payfast.GatewayUnavailable:
            logger.warning("PayFast notification could not be confirmed; asking PayFast to retry")
            return HttpResponse(status=503)
        return HttpResponse(status=200)
