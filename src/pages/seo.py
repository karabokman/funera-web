"""Structured data (JSON-LD) so search engines understand the site. Output is escaped for safe use inside a <script> block."""

import json

from django.conf import settings
from django.utils.safestring import mark_safe


def _dump(data: dict):
    # "<" and "&" are escaped so nothing inside the data can close the script tag.
    return mark_safe(
        json.dumps(data, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    )


def organization_ld():
    url = settings.SITE_URL
    return _dump(
        {
            "@context": "https://schema.org",
            "@graph": [
                {
                    "@type": "Organization",
                    "@id": f"{url}/#org",
                    "name": settings.BRAND_NAME,
                    "legalName": settings.COMPANY_LEGAL_NAME,
                    "url": url,
                    "logo": f"{url}/static/img/logo.png",
                    "slogan": settings.BRAND_SLOGAN,
                    "email": settings.CONTACT_EMAIL,
                },
                {
                    "@type": "SoftwareApplication",
                    "name": f"{settings.BRAND_NAME} platform",
                    "applicationCategory": "BusinessApplication",
                    "operatingSystem": "Web",
                    "description": "Funeral cover management platform: applications, members, premiums, claims, communication and POPIA compliance.",
                    "publisher": {"@id": f"{url}/#org"},
                },
            ],
        }
    )


def faq_ld(faq):
    return _dump(
        {
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": [
                {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in faq
            ],
        }
    )
