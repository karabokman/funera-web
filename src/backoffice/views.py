"""Staff back-office. Thin views: lists and detail pages read the models, every change goes through the same service
functions the client portal and the website use, so staff and clients are held to identical rules."""

import logging

from django.conf import settings
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
from instances.models import Instance
from leads.models import Lead
from quotes import services as quote_services
from quotes.errors import QuoteError
from quotes.forms import OfferForm, OfferLineFormSet, QuoteForm, QuoteLineFormSet
from quotes.models import Quote, QuoteTemplate
from quotes.views import pdf_response

from . import audit, audit_log, followups, roles, stats
from .forms import FollowUpForm, InstanceForm, LeadUpdateForm
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
        changes = []
        if "status" in form.changed_data:
            changes.append(f"status {form.initial.get('status')} to {form.cleaned_data['status']}")
        if "internal_notes" in form.changed_data:
            changes.append("notes edited")
        lead = form.save(commit=False)
        if lead.status != Lead.Status.NEW and lead.contacted_at is None:
            lead.contacted_at = timezone.now()
        if lead.status in (Lead.Status.WON, Lead.Status.LOST):
            followups.clear(lead)  # nothing left to chase
        lead.save()
        if changes:
            audit.record(self.request, "lead.updated", lead, "; ".join(changes))
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
            created = self.quote is None
            if created:
                quote_services.save_new(quote, request.user, lead=self.lead)
            else:
                quote.save()
            lines.instance = quote
            lines.save()
            audit.record(
                request,
                "quote.created" if created else "quote.edited",
                quote,
                f"R{quote.recurring_price} {quote.billing_period}",
            )
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
    audit_action = ""

    def audit_detail(self, quote, result) -> str:
        return ""

    def post(self, request, pk):
        quote = get_object_or_404(Quote, pk=pk)
        try:
            result = self.act(quote, request.user)
        except QuoteError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, self.success)
            target = result if isinstance(result, Quote) else quote
            audit.record(request, self.audit_action, target, self.audit_detail(quote, result))
            if isinstance(result, Quote):
                return redirect("backoffice:quote", pk=result.pk)
        return redirect("backoffice:quote", pk=quote.pk)

    def act(self, quote, user):
        raise NotImplementedError


class QuoteSend(QuoteAction):
    success = "Quotation e-mailed to the client."
    audit_action = "quote.sent"

    def act(self, quote, user):
        if quote.status == Quote.Status.SENT:
            self.audit_action = "quote.resent"
        quote_services.send(quote, user)

    def audit_detail(self, quote, result) -> str:
        return f"to {quote.email}"


class QuoteCancel(QuoteAction):
    success = "Quotation cancelled. The client's link now shows it as withdrawn."
    audit_action = "quote.cancelled"

    def act(self, quote, user):
        quote_services.cancel(quote, user)


class QuoteDuplicate(QuoteAction):
    success = "A new draft was created from this quotation."
    audit_action = "quote.duplicated"

    def audit_detail(self, quote, result) -> str:
        return f"copy of {quote.number}"

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
            audit.record(request, self.audit_action, sub, self.audit_detail(request))
            messages.success(request, self.success)
        return redirect("backoffice:subscription", pk=sub.pk)

    audit_action = ""

    def audit_detail(self, request) -> str:
        return ""

    def act(self, sub, request):
        raise NotImplementedError


class SubscriptionPause(SubscriptionAction):
    success = "Subscription paused."
    audit_action = "subscription.paused"

    def audit_detail(self, request) -> str:
        return f"for {request.POST.get('cycles', '?')} cycle(s)"

    def act(self, sub, request):
        form = PauseForm(request.POST, subscription=sub)
        if not form.is_valid():
            raise BillingError("Choose how long to pause for.")
        lifecycle.pause(sub, form.cleaned_data["cycles"], actor=STAFF)


class SubscriptionResume(SubscriptionAction):
    success = "Subscription resumed."
    audit_action = "subscription.resumed"

    def act(self, sub, request):
        lifecycle.resume(sub, actor=STAFF)


class SubscriptionCancel(SubscriptionAction):
    success = "Subscription cancelled at PayFast and here."
    audit_action = "subscription.cancelled"

    def audit_detail(self, request) -> str:
        return request.POST.get("reason", "")[:200]

    def act(self, sub, request):
        lifecycle.cancel(sub, actor=STAFF, reason=request.POST.get("reason", ""))


class RefundRaise(SubscriptionAction):
    """Staff raise a refund request on a payment (outside the client's refund window if they choose)."""

    success = "Refund request recorded. Process it from the refunds list."
    audit_action = "subscription.refund_raised"

    def audit_detail(self, request) -> str:
        return request.POST.get("reason", "")[:200]

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
                audit.record(request, "refund.processed", refund)
                messages.success(request, "Refunded through PayFast. The client was e-mailed.")
            elif action == "decline":
                refunds.decline(refund, request.POST.get("note", ""))
                audit.record(request, "refund.declined", refund, request.POST.get("note", "")[:200])
                messages.success(request, "Refund declined.")
            else:
                raise BillingError("Unknown action.")
        except BillingError as exc:
            messages.error(request, str(exc))
        except payfast.GatewayUnavailable as exc:
            audit.record(request, "refund.failed", refund, str(exc)[:200])
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
        audit.record(request, f"followup.{action.split(':')[0]}", obj, message)
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
            created = self.offer is None
            if created:
                offer.created_by = request.user
            offer.save()
            lines.instance = offer
            lines.save()
            audit.record(
                request,
                "offer.created" if created else "offer.edited",
                offer,
                f"R{offer.recurring_price} {offer.billing_period}",
            )
            messages.success(request, f'Saved offer "{offer.name}" saved.')
            return redirect("backoffice:offers")
        return self.render_to_response(self.get_context_data(form_pair=(form, lines)))


class OfferDelete(AdminRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk):
        offer = get_object_or_404(QuoteTemplate, pk=pk)
        name = offer.name
        audit.record(request, "offer.deleted", offer, f"R{offer.recurring_price} {offer.billing_period}")
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
        audit.record(request, "offer.created", offer, f"saved from {quote.number}")
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


# ------------------------------------------------------------------------------------------------ audit log
class AuditLogView(AdminRequiredMixin, StaffPage):
    """Who did what and when: staff actions from the audit log, plus what clients and PayFast did to the same records."""

    template_name = "backoffice/audit.html"
    title = "Audit log"

    def get_context_data(self, **kwargs):
        filters = audit_log.Filters.from_request(self.request.GET)
        return super().get_context_data(
            page=paginate(self.request, audit_log.Feed(filters)),
            f=filters,
            areas=[(value, label) for value, label, _ in audit.AREAS],
            staff_names=audit_log.staff_names(),
            expired=audit.expired().count(),
            retention_months=settings.AUDIT_RETENTION_MONTHS,
            **kwargs,
        )


class AuditPurgeView(AdminRequiredMixin, View):
    """Delete staff entries past the retention period (there is no scheduler, so an Admin presses this now and then)."""

    http_method_names = ["post"]

    def post(self, request):
        count = audit.purge(request)
        messages.success(
            request,
            f"Deleted {count} audit entries older than {settings.AUDIT_RETENTION_MONTHS} months."
            if count
            else "Nothing to delete.",
        )
        return redirect("backoffice:audit")


# ------------------------------------------------------------------------------------------------ hosted instances
LIVE_SUBSCRIPTION = [Subscription.Status.ACTIVE, Subscription.Status.PAST_DUE, Subscription.Status.PAUSED]


class InstanceListView(StaffPage):
    template_name = "backoffice/instances.html"
    title = "Hosted instances"

    def get_context_data(self, **kwargs):
        qs = Instance.objects.select_related("customer", "subscription", "owner")
        status = self.request.GET.get("status", "")
        show = self.request.GET.get("show", "")
        query = self.request.GET.get("q", "").strip()
        if status == "open":
            qs = qs.filter(status__in=[Instance.Status.AWAITING, Instance.Status.SETTING_UP])
        elif status in Instance.Status.values:
            qs = qs.filter(status=status)
        elif show != "all":
            qs = qs.exclude(status=Instance.Status.RETIRED)
        if query:
            qs = qs.filter(
                Q(customer__business_name__icontains=query)
                | Q(url__icontains=query)
                | Q(host__icontains=query)
                | Q(version__icontains=query)
                | Q(admin_name__icontains=query)
                | Q(admin_email__icontains=query)
            )
        instances = list(qs)
        if show == "outdated":
            instances = [i for i in instances if i.is_outdated]
        elif show == "attention":
            instances = [i for i in instances if i.billing_flag or i.is_overdue or i.stale_for_setup or i.is_outdated]
        missing = Subscription.objects.filter(status__in=LIVE_SUBSCRIPTION, instance__isnull=True).select_related(
            "customer", "plan"
        )
        return super().get_context_data(
            page=paginate(self.request, instances),
            statuses=[("open", "Awaiting or being set up"), *Instance.Status.choices],
            status=status,
            show=show,
            q=query,
            missing=missing,
            keep=f"show={show}&" if show else "",
            latest_version=settings.FUNERA_LATEST_VERSION,
            **kwargs,
        )


class InstanceDetailView(StaffPage):
    template_name = "backoffice/instance_detail.html"

    def get_context_data(self, **kwargs):
        instance = get_object_or_404(
            Instance.objects.select_related("customer", "subscription__plan", "owner"), pk=kwargs["pk"]
        )
        return super().get_context_data(
            page_title=f"{instance.customer.business_name} · hosted instance",
            instance=instance,
            latest_version=settings.FUNERA_LATEST_VERSION,
            **kwargs,
        )


class InstanceEditView(AdminRequiredMixin, TemplateView):
    """Add or edit the record of a client's hosted platform (Admin only: it is operational data)."""

    template_name = "backoffice/instance_form.html"

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.instance = None
        self.initial_subscription = None
        if "pk" in kwargs:
            self.instance = get_object_or_404(Instance.objects.select_related("customer"), pk=kwargs["pk"])
            self.customer = self.instance.customer
            return
        sub_id, customer_id = request.GET.get("subscription", ""), request.GET.get("customer", "")
        sub = Subscription.objects.select_related("customer").filter(pk=sub_id).first() if sub_id.isdigit() else None
        self.initial_subscription = sub
        self.customer = (
            sub.customer
            if sub
            else (Customer.objects.filter(pk=customer_id).first() if customer_id.isdigit() else None)
        )

    def dispatch(self, request, *args, **kwargs):
        if self.customer is None and request.user.is_authenticated and request.user.is_staff:
            messages.error(request, "Open a customer or subscription first, then add its hosted instance.")
            return redirect("backoffice:instances")
        return super().dispatch(request, *args, **kwargs)

    def form(self, data=None):
        initial = {"subscription": self.initial_subscription} if self.initial_subscription else {}
        return InstanceForm(data, instance=self.instance, customer=self.customer, initial=initial)

    def get_context_data(self, **kwargs):
        form = kwargs.pop("form", None) or self.form()
        return super().get_context_data(
            form=form,
            instance=self.instance,
            customer=self.customer,
            page_title="Edit hosted instance" if self.instance else "New hosted instance",
            **kwargs,
        )

    def post(self, request, *args, **kwargs):
        form = self.form(request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        created = self.instance is None
        saved = form.save()
        audit.record(
            request,
            "instance.created" if created else "instance.edited",
            saved,
            f"{saved.get_status_display()}, version {saved.version or 'not recorded'}",
        )
        messages.success(request, "Hosted instance saved.")
        return redirect("backoffice:instance", pk=saved.pk)


class InstanceQuickAction(AdminRequiredMixin, View):
    """One-click updates: mark live, or record that the latest version was deployed."""

    http_method_names = ["post"]

    def post(self, request, pk, action):
        instance = get_object_or_404(Instance.objects.select_related("customer"), pk=pk)
        today = timezone.localdate()
        if action == "live":
            instance.status = Instance.Status.LIVE
            instance.went_live_on = instance.went_live_on or today
            instance.save(update_fields=["status", "went_live_on", "updated_at"])
            audit.record(request, "instance.live", instance)
            messages.success(request, f"{instance.customer.business_name} is marked live.")
        elif action == "updated":
            if not settings.FUNERA_LATEST_VERSION:
                messages.error(request, "Set FUNERA_LATEST_VERSION in .env first, so there is a version to record.")
            else:
                instance.version, instance.version_updated_on = settings.FUNERA_LATEST_VERSION, today
                instance.save(update_fields=["version", "version_updated_on", "updated_at"])
                audit.record(request, "instance.version_updated", instance, f"now on {instance.version}")
                messages.success(request, f"Recorded as running {instance.version}.")
        else:
            raise Http404
        return redirect(reverse("backoffice:instance", args=[instance.pk]))
