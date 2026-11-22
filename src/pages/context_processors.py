from django.conf import settings

from billing.payfast import is_configured


def site(request):
    """Site-wide values every template can use as ``site.<name>``."""
    url = settings.SITE_URL
    return {
        "site": {
            "name": settings.BRAND_NAME,
            "slogan": settings.BRAND_SLOGAN,
            "url": url,
            "canonical": url + request.path,
            "og_image": f"{url}/static/img/og.png",
            "email": settings.CONTACT_EMAIL,
            "phone": settings.CONTACT_PHONE,
            "legal_name": settings.COMPANY_LEGAL_NAME,
            "io_email": settings.INFORMATION_OFFICER_EMAIL,
            "notice_version": settings.PRIVACY_NOTICE_VERSION,
            "retention_months": settings.LEAD_RETENTION_MONTHS,
            "plausible_domain": settings.PLAUSIBLE_DOMAIN,
            "portal_minutes": settings.PORTAL_LOGIN_MINUTES,
            "billing_enabled": is_configured(),
            "plausible_script": settings.PLAUSIBLE_SCRIPT_URL,
        }
    }
