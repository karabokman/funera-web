import json
import re

from django.test import TestCase, override_settings
from django.urls import reverse


class PublicPagesTests(TestCase):
    def test_every_public_page_renders(self):
        for name in ("home", "features", "pricing", "security", "privacy", "terms", "leads:contact"):
            with self.subTest(page=name):
                response = self.client.get(reverse(name if ":" in name else "pages:" + name))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "<title>")
                self.assertContains(response, 'rel="canonical"')

    def test_unknown_url_gives_branded_404(self):
        response = self.client.get("/no-such-page/")
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Funera", status_code=404)

    def test_slogan_and_copyright_present(self):
        response = self.client.get(reverse("pages:home"))
        self.assertContains(response, "Manage with care.")
        self.assertContains(response, "Operate with confidence.")
        self.assertRegex(response.content.decode(), r"© \d{4} Funera\. All rights reserved\.")

    def test_healthz_is_plain_ok_and_checks_database(self):
        response = self.client.get(reverse("pages:healthz"))
        self.assertEqual(response.status_code, 200)

    def test_robots_points_at_sitemap_and_does_not_reveal_private_addresses(self):
        body = self.client.get(reverse("pages:robots")).content.decode()
        self.assertIn("Sitemap: https://funera.test/sitemap.xml", body)
        self.assertIn("Disallow: /portal/", body)
        self.assertNotIn("/admin/", body)
        self.assertNotIn("/staff/", body)

    def test_no_public_page_links_to_the_staff_back_office_or_admin(self):
        pages = [reverse(name) for name in ("pages:home", "pages:robots", "pages:sitemap")]
        pages += ["/features/", "/pricing/", "/security/", "/contact/", "/privacy/", "/terms/"]
        for path in pages:
            response = self.client.get(path)
            if response.status_code != 200:
                continue
            body = response.content.decode()
            for secret in ("/staff/", "/admin/", "staff/login"):
                with self.subTest(path=path, secret=secret):
                    self.assertNotIn(secret, body)

    def test_sitemap_lists_public_pages_only(self):
        body = self.client.get(reverse("pages:sitemap")).content.decode()
        for path in ("/features/", "/pricing/", "/security/", "/contact/"):
            self.assertIn(f"https://funera.test{path}", body)
        self.assertNotIn("thanks", body)
        self.assertNotIn("admin", body)

    def test_pricing_has_faq_jsonld_that_parses(self):
        html = self.client.get(reverse("pages:pricing")).content.decode()
        blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
        self.assertTrue(blocks)
        kinds = {json.loads(b).get("@type") for b in blocks}
        self.assertIn("FAQPage", kinds)

    def test_home_has_organization_jsonld(self):
        html = self.client.get(reverse("pages:home")).content.decode()
        blocks = [json.loads(b) for b in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)]
        self.assertIn("Organization", json.dumps(blocks))


class SecurityHeaderTests(TestCase):
    def test_csp_forbids_inline_script_and_style(self):
        csp = self.client.get(reverse("pages:home"))["Content-Security-Policy"]
        self.assertIn("script-src 'self'", csp)
        self.assertNotIn("unsafe-inline", csp)
        self.assertIn("frame-ancestors 'none'", csp)

    def test_other_headers_set(self):
        response = self.client.get(reverse("pages:home"))
        self.assertIn("camera=()", response["Permissions-Policy"])
        self.assertEqual(response["Cross-Origin-Opener-Policy"], "same-origin")

    def test_public_pages_contain_no_inline_script_or_style(self):
        for name in ("home", "features", "pricing", "leads:contact"):
            html = self.client.get(reverse(name if ":" in name else "pages:" + name)).content.decode()
            self.assertNotRegex(html, r"<script(?![^>]*\bsrc=)(?![^>]*ld\+json)[^>]*>")
            self.assertNotIn("<style", html)
            self.assertNotIn(' style="', html)

    def test_json_ld_is_not_script_injectable(self):
        from pages.seo import _dump as json_ld

        out = str(json_ld({"name": "</script><script>alert(1)</script>"}))
        self.assertNotIn("</script>", out)
        self.assertNotIn("<", out)

    @override_settings(PLAUSIBLE_DOMAIN="funera.test", PLAUSIBLE_SCRIPT_URL="https://plausible.io/js/script.js")
    def test_plausible_origin_allowed_only_when_configured(self):
        from django.test import RequestFactory

        from pages.middleware import SecurityHeadersMiddleware

        mw = SecurityHeadersMiddleware(lambda r: __import__("django.http", fromlist=["HttpResponse"]).HttpResponse())
        self.assertIn("https://plausible.io", mw(RequestFactory().get("/"))["Content-Security-Policy"])

    def test_admin_is_not_given_the_strict_csp(self):
        response = self.client.get("/admin/login/")
        self.assertNotIn("Content-Security-Policy", response)
