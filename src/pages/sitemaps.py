from urllib.parse import urlparse

from django.conf import settings
from django.contrib.sitemaps import Sitemap
from django.urls import reverse


class StaticPagesSitemap(Sitemap):
    """Addresses are built from SITE_URL, never from the request, so the sitemap always names the canonical host."""

    changefreq = "monthly"

    PAGES = {
        "pages:home": 1.0,
        "pages:features": 0.9,
        "pages:pricing": 0.9,
        "pages:security": 0.7,
        "leads:contact": 0.8,
        "pages:privacy": 0.3,
        "pages:terms": 0.3,
        "pages:subscription_terms": 0.3,
    }

    @property
    def protocol(self):
        return urlparse(settings.SITE_URL).scheme

    def get_domain(self, site=None):
        return urlparse(settings.SITE_URL).netloc

    def items(self):
        return list(self.PAGES)

    def location(self, item):
        return reverse(item)

    def priority(self, item):
        return self.PAGES[item]
