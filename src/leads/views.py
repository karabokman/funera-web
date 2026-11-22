from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect
from django.views.generic import FormView, TemplateView

from catalog.models import Plan

from . import services
from .forms import LeadForm


class ContactView(FormView):
    template_name = "leads/contact.html"
    form_class = LeadForm

    def get_initial(self):
        initial = {}
        plan = Plan.objects.filter(code=self.request.GET.get("plan", ""), is_active=True).first()
        if plan:
            initial["plan_interest"] = plan
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["utm"] = {k: self.request.GET.get(k, "") for k in ("utm_source", "utm_medium", "utm_campaign")}
        context["page_title"] = "Book a demo"
        return context

    def form_valid(self, form):
        if getattr(form, "is_spam", False):
            # Look successful so the bot learns nothing, but save and send nothing.
            return redirect("leads:thanks")
        visitor = services.visitor_hash(self.request)
        if services.is_rate_limited(visitor):
            messages.error(
                self.request,
                "You have sent several enquiries in the last hour. Please try again later, or e-mail us directly.",
            )
            return self.form_invalid(form)
        services.create_lead(form, self.request)
        self.request.session["lead_submitted"] = True
        return redirect("leads:thanks")


class ThanksView(TemplateView):
    template_name = "leads/thanks.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["contact_email"] = settings.CONTACT_EMAIL
        context["page_title"] = "Thank you"
        return context
