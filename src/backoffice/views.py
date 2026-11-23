"""Staff back-office. Thin views: lists and detail pages read the models, every change goes through the same service
functions the client portal and the website use, so staff and clients are held to identical rules."""

import logging

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.views import LoginView, LogoutView
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.generic import FormView, TemplateView

from billing import lifecycle, payfast, refunds
from billing.errors import BillingError
from billing.forms import PauseForm, RefundRequestForm
from billing.models import Customer, Payment, Refund, Subscription
from catalog.models import Plan
from leads.models import Lead
from quotes import services as quote_services
from quotes.errors import QuoteError
from quotes.forms import OfferForm, OfferLineFormSet, QuoteForm, QuoteLineFormSet
from quotes.models import Quote, QuoteTemplate
from quotes.views import pdf_response

from . import followups, roles, stats
from .forms import FollowUpForm, LeadUpdateForm
from .mixins import AdminRequiredMixin, StaffRequiredMixin

logger = logging.getLogger(__name__)
STAFF = "staff"
PAGE_SIZE = 25


class StaffLoginView(LoginView):
    template_name = "backoffice/login.html"
    redirect_authenticated_user = True

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.pop("site", None)  # Django's LoginView adds a Site object that hides our `site` settings dict
        context["page_title"] = "Staff sign-in"
        return context

    def get_success_url(self):
        return self.get_redirect_url() or reverse("backoffice:dashboard")


class StaffLogoutView(LogoutView):
    next_page = reverse_lazy("backoffice:login")


class StaffPage(StaffRequiredMixin, TemplateView):
    """Base for pages: sets the page title and passes the signed-in user to templates."""

    title = ""

    def get_context_data(self, **kwargs):
        kwargs.setdefault("page_title", self.title)
        return super().get_context_data(**kwargs)


def paginate(request, queryset):
    return Paginator(queryset, PAGE_SIZE).get_page(request.GET.get("page"))


# ------------------------------------------------------------------------------------------------ dashboard
class DashboardView(StaffPage):
    template_name = "backoffice/dashboard.html"
    title = "Dashboard"

    def get_context_data(self, **kwargs):
        return super().get_context_data(stats=stats.dashboard(money=roles.is_admin(self.request.user)), **kwargs)


# ------------------------------------------------------------------------------------------------ leads
class LeadListView(StaffPage):
    template_name = "backoffice/leads.html"
    title = "Leads"

    def get_context_data(self, **kwargs):
        leads = Lead.objects.select_related("plan_interest")
        status, query = self.request.GET.get("status", ""), self.request.GET.get("q", "").strip()
        if status in Lead.Status.values:
            leads = leads.filter(status=status)
        if query:
            leads = leads.filter(
                Q(name__icontains=query)
                | Q(business_name__icontains=query)
                | Q(email__icontains=query)
                | Q(phone__icontains=query)
            )
        return super().get_context_data(
            page=paginate(self.request, leads), statuses=Lead.Status.choices, status=status, q=query, **kwargs
        )


class LeadDetailView(StaffRequiredMixin, FormView):
    template_name = "backoffice/lead_detail.html"
    form_class = LeadUpdateForm

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.lead = get_object_or_404(Lead.objects.select_related("plan_interest"), pk=kwargs["pk"])

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "instance": self.lead}

    def form_valid(self, form):
        lead = form.save(commit=False)
        if lead.status != Lead.Status.NEW and lead.contacted_at is None:
            lead.contacted_at = timezone.now()
        if lead.status in (Lead.Status.WON, Lead.Status.LOST):
            followups.clear(lead)  # nothing left to chase
        lead.save()
        messages.success(self.request, "Lead updated.")
        return redirect("backoffice:lead", pk=lead.pk)

    def get_context_data(self, **kwargs):
        return super().get_context_data(
            page_title=self.lead.business_name,
            lead=self.lead,
            follow_up_form=FollowUpForm(
                initial={"follow_up_on": self.lead.follow_up_on, "next_action": self.lead.next_action}
            ),
            quotes=self.lead.quotes.all(),
            plans=Plan.objects.filter(is_active=True),
            offers=QuoteTemplate.objects.filter(is_active=True),
            **kwargs,
        )


# ------------------------------------------------------------------------------------------------ quotes
class QuoteListView(StaffPage):
    template_name = "backoffice/quotes.html"
    title = "Quotations"

    def get_context_data(self, **kwargs):
        quotes = Quote.objects.select_related("plan")
        status, query = self.request.GET.get("status", ""), self.request.GET.get("q", "").strip()
        if status in Quote.Status.values:
            quotes = quotes.filter(status=status)
        if query:
            quotes = quotes.filter(
                Q(number__icontains=query) | Q(business_name__icontains=query) | Q(email__icontains=query)
            )
        return super().get_context_data(
            page=paginate(self.request, quotes), statuses=Quote.Status.choices, status=status, q=query, **kwargs
        )


class QuoteEditView(StaffRequiredMixin, TemplateView):
    """Create (from a lead or from scratch) or edit a draft. The header form and the once-off lines are saved together."""

    template_name = "backoffice/quote_form.html"

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.quote = get_object_or_404(Quote, pk=kwargs["pk"]) if "pk" in kwargs else None
        offer_id = request.GET.get("offer")
        self.offer = (
            QuoteTemplate.objects.filter(pk=offer_id).first()
            if offer_id and offer_id.isdigit() and not self.quote
            else None
        )
        lead_id = request.GET.get("lead")
        self.lead = (
            get_object_or_404(Lead, pk=lead_id)
            if lead_id and not self.quote
            else (self.quote.lead if self.quote else None)
        )

    def dispatch(self, request, *args, **kwargs):
        if self.quote is not None and not self.quote.is_editable and request.user.is_authenticated:
            messages.error(request, "Only a draft can be edited. Use Duplicate to make a revised copy.")
            return redirect("backoffice:quote", pk=self.quote.pk)
        return super().dispatch(request, *args, **kwargs)

    def _plan(self):
        code = self.request.GET.get("plan")
        plans = Plan.objects.filter(is_active=True)
        return (
            plans.filter(code=code).first()
            or (self.lead.plan_interest if self.lead and self.lead.plan_interest_id else None)
            or plans.first()
        )

    def forms(self, data=None):
        if self.quote:
            return QuoteForm(data, instance=self.quote), QuoteLineFormSet(data, instance=self.quote)
        initial, lines = quote_services.initial_for(self._plan(), self.lead, self.offer)
        return QuoteForm(data, initial=initial), QuoteLineFormSet(data, instance=Quote(), initial=lines)

    def get_context_data(self, **kwargs):
        form, lines = kwargs.pop("form_pair", None) or self.forms()
        return super().get_context_data(
            form=form,
            lines=lines,
            quote=self.quote,
            lead=self.lead,
            plans=Plan.objects.filter(is_active=True),
            offers=QuoteTemplate.objects.filter(is_active=True),
            offer=self.offer,
            page_title="Edit quotation" if self.quote else "New quotation",
            **kwargs,
        )

    def post(self, request, *args, **kwargs):
        form, lines = self.forms(request.POST)
        if form.is_valid() and lines.is_valid():
            quote = form.save(commit=False)
            if self.quote is None:
                quote_services.save_new(quote, request.user, lead=self.lead)
            else:
                quote.save()
            lines.instance = quote
            lines.save()
            messages.success(request, f"Quotation {quote.number} saved.")
            return redirect("backoffice:quote", pk=quote.pk)
        return self.render_to_response(self.get_context_data(form_pair=(form, lines)))


class QuoteDetailView(StaffPage):
    template_name = "backoffice/quote_detail.html"

    def get_context_data(self, **kwargs):
        quote = get_object_or_404(
            Quote.objects.select_related("plan", "lead", "subscription__customer"), pk=kwargs["pk"]
        )
        return super().get_context_data(
            page_title=quote.number,
            quote=quote,
            lines=quote.lines.all(),
            events=quote.events.all()[:15],
            public_url=quote_services.public_url(quote),
            follow_up_form=FollowUpForm(initial={"follow_up_on": quote.follow_up_on, "next_action": quote.next_action}),
            **kwargs,
        )


class QuoteAction(StaffRequiredMixin, View):
    http_method_names = ["post"]
    success = ""

    def post(self, request, pk):
        quote = get_object_or_404(Quote, pk=pk)
        try:
            result = self.act(quote, request.user)
        except QuoteError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, self.success)
            if isinstance(result, Quote):
                return redirect("backoffice:quote", pk=result.pk)
        return redirect("backoffice:quote", pk=quote.pk)

    def act(self, quote, user):
        raise NotImplementedError


class QuoteSend(QuoteAction):
    success = "Quotation e-mailed to the client."

    def act(self, quote, user):
        quote_services.send(quote, user)


class QuoteCancel(QuoteAction):
    success = "Quotation cancelled. The client's link now shows it as withdrawn."

    def act(self, quote, user):
        quote_services.cancel(quote, user)


class QuoteDuplicate(QuoteAction):
    success = "A new draft was created from this quotation."

    def act(self, quote, user):
        return quote_services.duplicate(quote, user)


# ------------------------------------------------------------------------------------------------ customers
class CustomerListView(StaffPage):
    template_name = "backoffice/customers.html"
    title = "Customers"

    def get_context_data(self, **kwargs):
        customers = Customer.objects.prefetch_related("subscriptions__plan")
        query = self.request.GET.get("q", "").strip()
        if query:
            customers = customers.filter(
                Q(name__icontains=query) | Q(business_name__icontains=query) | Q(email__icontains=query)
            )
        return super().get_context_data(page=paginate(self.request, customers), q=query, **kwargs)


class CustomerDetailView(StaffPage):
    template_name = "backoffice/customer_detail.html"

    def get_context_data(self, **kwargs):
        customer = get_object_or_404(Customer.objects.select_related("lead"), pk=kwargs["pk"])
        return super().get_context_data(
            page_title=customer.business_name,
            customer=customer,
            subscriptions=customer.subscriptions.select_related("plan"),
            **kwargs,
        )


# ------------------------------------------------------------------------------------------------ subscriptions and refunds
class SubscriptionListView(StaffPage):
    template_name = "backoffice/subscriptions.html"
    title = "Subscriptions"

    def get_context_data(self, **kwargs):
        subscriptions = Subscription.objects.select_related("customer", "plan")
        status, query = self.request.GET.get("status", ""), self.request.GET.get("q", "").strip()
        if status in Subscription.Status.values:
            subscriptions = subscriptions.filter(status=status)
        if query:
            subscriptions = subscriptions.filter(
                Q(reference__icontains=query)
                | Q(customer__business_name__icontains=query)
                | Q(customer__email__icontains=query)
            )
        return super().get_context_data(
            page=paginate(self.request, subscriptions),
            statuses=Subscription.Status.choices,
            status=status,
            q=query,
            **kwargs,
        )


class SubscriptionDetailView(StaffPage):
    template_name = "backoffice/subscription_detail.html"

    def get_context_data(self, **kwargs):
        sub = get_object_or_404(Subscription.objects.select_related("customer", "plan"), pk=kwargs["pk"])
        payments = [
            {
                "payment": p,
                "refundable": refunds.refundable_amount(p),
                "open": p.refunds.filter(status=Refund.Status.REQUESTED).exists(),
            }
            for p in sub.payments.prefetch_related("refunds")
        ]
        return super().get_context_data(
            page_title=f"{sub.customer.business_name} · {sub.plan.name}", sub=sub, payments=payments, events=sub.events.all()[:15],
            pause_form=PauseForm(subscription=sub), refund_form=RefundRequestForm(), quote=getattr(sub, "quote", None), **kwargs,
        )  # fmt: skip


class SubscriptionAction(AdminRequiredMixin, View):
    """POST-only buttons. PayFast is asked first; if it cannot confirm, the staff member is told and nothing changes."""

    http_method_names = ["post"]
    success = ""

    def post(self, request, pk):
        sub = get_object_or_404(Subscription, pk=pk)
        try:
            self.act(sub, request)
        except BillingError as exc:
            messages.error(request, str(exc))
        except payfast.GatewayUnavailable as exc:
            logger.warning("PayFast did not confirm %s for %s: %s", type(self).__name__, sub.reference, exc)
            messages.error(request, f"PayFast did not confirm the change, so nothing was changed. ({exc})")
        else:
            messages.success(request, self.success)
        return redirect("backoffice:subscription", pk=sub.pk)

    def act(self, sub, request):
        raise NotImplementedError


class SubscriptionPause(SubscriptionAction):
    success = "Subscription paused."

    def act(self, sub, request):
        form = PauseForm(request.POST, subscription=sub)
        if not form.is_valid():
            raise BillingError("Choose how long to pause for.")
        lifecycle.pause(sub, form.cleaned_data["cycles"], actor=STAFF)


class SubscriptionResume(SubscriptionAction):
    success = "Subscription resumed."

    def act(self, sub, request):
        lifecycle.resume(sub, actor=STAFF)


class SubscriptionCancel(SubscriptionAction):
    success = "Subscription cancelled at PayFast and here."

    def act(self, sub, request):
        lifecycle.cancel(sub, actor=STAFF, reason=request.POST.get("reason", ""))


class RefundRaise(SubscriptionAction):
    """Staff raise a refund request on a payment (outside the client's refund window if they choose)."""

    success = "Refund request recorded. Process it from the refunds list."

    def act(self, sub, request):
        payment = get_object_or_404(Payment, pk=request.POST.get("payment"), subscription=sub)
        form = RefundRequestForm(request.POST)
        if not form.is_valid():
            raise BillingError("Give a reason for the refund.")
        refunds.request_refund(payment, form.cleaned_data["reason"], actor=STAFF)


class RefundListView(AdminRequiredMixin, StaffPage):
    template_name = "backoffice/refunds.html"
    title = "Refunds"

    def get_context_data(self, **kwargs):
        status = self.request.GET.get("status", Refund.Status.REQUESTED)
        qs = Refund.objects.select_related("payment__subscription__customer")
        if status in Refund.Status.values:
            qs = qs.filter(status=status)
        return super().get_context_data(
            page=paginate(self.request, qs), statuses=Refund.Status.choices, status=status, **kwargs
        )


class RefundAction(AdminRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk, action):
        refund = get_object_or_404(Refund, pk=pk)
        try:
            if action == "process":
                refunds.process(refund)
                messages.success(request, "Refunded through PayFast. The client was e-mailed.")
            elif action == "decline":
                refunds.decline(refund, request.POST.get("note", ""))
                messages.success(request, "Refund declined.")
            else:
                raise BillingError("Unknown action.")
        except BillingError as exc:
            messages.error(request, str(exc))
        except payfast.GatewayUnavailable as exc:
            messages.error(
                request,
                f"PayFast did not confirm the refund, so no money was returned. It is marked failed and can be retried. ({exc})",
            )
        return redirect(request.POST.get("next") or "backoffice:refunds")


class QuotePdf(StaffRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request, pk):
        return pdf_response(get_object_or_404(Quote, pk=pk))


# ------------------------------------------------------------------------------------------------ follow-ups
class FollowUpsView(StaffPage):
    template_name = "backoffice/follow_ups.html"
    title = "Follow-ups"

    def get_context_data(self, **kwargs):
        return super().get_context_data(buckets=followups.collect(), **kwargs)


class FollowUpAction(StaffRequiredMixin, View):
    """Set, snooze or complete the follow-up on a lead or quotation. POST only."""

    http_method_names = ["post"]

    def post(self, request, kind, pk):
        model = followups.KINDS.get(kind)
        if model is None:
            raise Http404
        obj = get_object_or_404(model, pk=pk)
        action = request.POST.get("action", "")
        target = request.POST.get("next", "")
        if not url_has_allowed_host_and_scheme(
            target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
        ):
            target = reverse("backoffice:lead" if kind == "lead" else "backoffice:quote", args=[obj.pk])
        if (
            kind == "quote"
            and obj.status not in followups.OPEN_QUOTE
            or kind == "lead"
            and obj.status not in followups.OPEN_LEAD
        ):
            messages.error(request, "This item is closed, so there is nothing to follow up.")
            return redirect(target)
        try:
            if action == "set":
                form = FollowUpForm(request.POST)
                if not form.is_valid():
                    messages.error(request, "Enter a valid date.")
                    return redirect(target)
                data = form.cleaned_data
                message = followups.apply(
                    kind,
                    obj,
                    action="set",
                    user=request.user,
                    follow_up_on=data["follow_up_on"],
                    next_action=data["next_action"],
                )
            else:
                message = followups.apply(kind, obj, action=action, user=request.user)
        except ValueError:
            messages.error(request, "That follow-up action is not recognised.")
            return redirect(target)
        messages.success(request, message)
        return redirect(target)


# ------------------------------------------------------------------------------------------------ saved offers
class OfferListView(StaffPage):
    template_name = "backoffice/offers.html"
    title = "Saved offers"

    def get_context_data(self, **kwargs):
        offers = QuoteTemplate.objects.select_related("plan").prefetch_related("lines")
        return super().get_context_data(offers=offers, **kwargs)


class OfferEditView(AdminRequiredMixin, TemplateView):
    """Create or edit a saved offer; the header form and its once-off lines are saved together."""

    template_name = "backoffice/offer_form.html"

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.offer = get_object_or_404(QuoteTemplate, pk=kwargs["pk"]) if "pk" in kwargs else None

    def forms(self, data=None):
        instance = self.offer
        return (
            OfferForm(data, instance=instance),
            OfferLineFormSet(data, instance=instance or QuoteTemplate()),
        )

    def get_context_data(self, **kwargs):
        form, lines = kwargs.pop("form_pair", None) or self.forms()
        return super().get_context_data(
            form=form,
            lines=lines,
            offer=self.offer,
            page_title="Edit saved offer" if self.offer else "New saved offer",
            **kwargs,
        )

    def post(self, request, *args, **kwargs):
        form, lines = self.forms(request.POST)
        if form.is_valid() and lines.is_valid():
            offer = form.save(commit=False)
            if self.offer is None:
                offer.created_by = request.user
            offer.save()
            lines.instance = offer
            lines.save()
            messages.success(request, f'Saved offer "{offer.name}" saved.')
            return redirect("backoffice:offers")
        return self.render_to_response(self.get_context_data(form_pair=(form, lines)))


class OfferDelete(AdminRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk):
        offer = get_object_or_404(QuoteTemplate, pk=pk)
        name = offer.name
        offer.delete()  # quotations already made from it are unaffected: they keep their own copy of every figure
        messages.success(request, f'Saved offer "{name}" deleted.')
        return redirect("backoffice:offers")


class QuoteSaveAsOffer(AdminRequiredMixin, View):
    """Keep this quotation's plan, price, wording and lines as a saved offer."""

    http_method_names = ["post"]

    def post(self, request, pk):
        quote = get_object_or_404(Quote, pk=pk)
        try:
            offer = quote_services.save_template_from_quote(quote, request.POST.get("name", ""), request.user)
        except QuoteError as exc:
            messages.error(request, str(exc))
            return redirect("backoffice:quote", pk=quote.pk)
        messages.success(request, f'Saved as "{offer.name}". Check the figures and wording below.')
        return redirect("backoffice:offer_edit", pk=offer.pk)


# ------------------------------------------------------------------------------------------------ team
class TeamView(AdminRequiredMixin, StaffPage):
    """Who can sign in to the back-office and at what level. Read-only: roles are changed in the site admin."""

    template_name = "backoffice/team.html"
    title = "Team"

    def get_context_data(self, **kwargs):
        User = get_user_model()
        members = [
            {"user": user, "role": roles.role(user)}
            for user in User.objects.filter(is_staff=True).prefetch_related("groups").order_by("-is_active", "username")
        ]
        return super().get_context_data(members=members, admin_group=roles.ADMIN_GROUP, **kwargs)
