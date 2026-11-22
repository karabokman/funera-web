import logging

from django.conf import settings
from django.db import connection
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET
from django.views.generic import TemplateView

from catalog import services as catalog

from . import content, seo

logger = logging.getLogger(__name__)


class Home(TemplateView):
    template_name = "pages/home.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            plans=catalog.active_plans(),
            highlights=catalog.highlights(),
            problems=content.PROBLEMS,
            steps=content.STEPS,
            trust=content.TRUST,
            faq=content.GENERAL_FAQ,
            org_ld=seo.organization_ld(),
        )
        return context


class Features(TemplateView):
    template_name = "pages/features.html"
    extra = {
        "page_title": "Features",
        "meta_description": "Applications, members, premiums, claims, SMS, cards, agents, branches and POPIA tools in one platform for funeral parlours.",
    }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.extra, sections=catalog.feature_sections())
        return context


class Pricing(TemplateView):
    template_name = "pages/pricing.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        plans = catalog.active_plans()
        context.update(
            page_title="Plans and pricing",
            meta_description="Starter, Professional and Enterprise plans for funeral parlours. Every plan includes members, premiums and claims.",
            plans=plans,
            sections=catalog.feature_sections(plans),
            faq=content.PRICING_FAQ,
            faq_ld=seo.faq_ld(content.PRICING_FAQ),
        )
        return context


class Security(TemplateView):
    template_name = "pages/security.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            page_title="Security and POPIA",
            meta_description="How Funera protects members' information: isolated instances, roles, audit trails and POPIA tools.",
            trust=content.TRUST,
        )
        return context


class Privacy(TemplateView):
    template_name = "pages/privacy.html"
    extra_context = {"page_title": "Privacy notice"}


class SubscriptionTerms(TemplateView):
    template_name = "pages/subscription_terms.html"
    extra_context = {"page_title": "Subscription terms"}


class Terms(TemplateView):
    template_name = "pages/terms.html"
    extra_context = {"page_title": "Website terms"}


@require_GET
def robots_txt(request):
    admin_path = "/" + settings.ADMIN_URL
    body = f"User-agent: *\nDisallow: {admin_path}\nDisallow: /portal/\nAllow: /\n\nSitemap: {settings.SITE_URL}/sitemap.xml\n"
    return HttpResponse(body, content_type="text/plain")


@never_cache
@require_GET
def healthz(request):
    """Liveness and database check for the container health check and uptime monitors."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:  # noqa: BLE001
        logger.exception("Health check: database unreachable")
        return JsonResponse({"status": "error"}, status=503)
    return JsonResponse({"status": "ok"})


def not_found(request, exception=None):
    return render(request, "errors/404.html", {"page_title": "Page not found"}, status=404)


def server_error(request):
    return render(request, "errors/500.html", {"page_title": "Something went wrong"}, status=500)
