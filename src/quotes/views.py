"""The client-facing quotation page: read it, print it, accept it or decline it. Access is by the unguessable link e-mailed to the client."""

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils.cache import add_never_cache_headers
from django.views import View
from django.views.generic import TemplateView

from billing.services import billing_enabled

from . import pdf, services
from .errors import QuoteError
from .forms import AcceptForm, DeclineForm
from .models import Quote


def _quote(token: str) -> Quote:
    return get_object_or_404(
        Quote.objects.select_related("plan", "subscription").exclude(status=Quote.Status.DRAFT), token=token
    )


class PublicQuoteView(TemplateView):
    template_name = "quotes/public.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        quote = _quote(kwargs["token"])
        if quote.status == Quote.Status.SENT and not quote.events.filter(kind="viewed").exists():
            services.record(quote, "viewed", actor="client")
        subscription = quote.subscription
        context.update(
            quote=quote, lines=quote.lines.all(), accept_form=AcceptForm(), decline_form=DeclineForm(), page_title=f"Quotation {quote.number}",
            pay_url=subscription and subscription.can_pay and billing_enabled() and f"/subscribe/pay/{subscription.reference}/",
        )  # fmt: skip
        return context

    def dispatch(self, request, *args, **kwargs):
        response = super().dispatch(request, *args, **kwargs)
        add_never_cache_headers(response)
        return response


class _Decision(View):
    http_method_names = ["post"]

    def post(self, request, token):
        quote = _quote(token)
        try:
            self.decide(request, quote)
        except QuoteError as exc:
            messages.error(request, str(exc))
        return redirect("quotes:public", token=quote.token)

    def decide(self, request, quote):
        raise NotImplementedError


class AcceptView(_Decision):
    def decide(self, request, quote):
        form = AcceptForm(request.POST)
        if not form.is_valid():
            raise QuoteError("Please type your full name and tick the box to accept.")
        services.accept(quote, name=form.cleaned_data["name"])
        messages.success(request, "Thank you. The quotation is accepted.")


class DeclineView(_Decision):
    def decide(self, request, quote):
        form = DeclineForm(request.POST)
        services.decline(quote, reason=form.cleaned_data["reason"] if form.is_valid() else "")
        messages.success(request, "Thank you for letting us know. The quotation is declined.")


def pdf_response(quote: Quote) -> HttpResponse:
    response = HttpResponse(pdf.render(quote), content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{quote.number}.pdf"'
    add_never_cache_headers(response)
    return response


class PublicQuotePdfView(View):
    http_method_names = ["get"]

    def get(self, request, token):
        return pdf_response(_quote(token))
