import importlib
import os
import sys
from unittest import mock

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

GOOD = {
    "SECRET_KEY": "a-very-long-random-secret-key-for-tests-0123456789abcdef",
    "ALLOWED_HOSTS": "funera.example.com",
    "SITE_URL": "https://funera.example.com",
}


def load_production(**env):
    with mock.patch.dict(os.environ, env, clear=False):
        base = importlib.import_module("config.settings.base")
        importlib.reload(base)
        try:
            sys.modules.pop("config.settings.production", None)
            return importlib.import_module("config.settings.production")
        finally:
            sys.modules.pop("config.settings.production", None)
            importlib.reload(base)


class ProductionGuardTests(SimpleTestCase):
    def test_good_configuration_loads_with_secure_defaults(self):
        s = load_production(**GOOD)
        self.assertFalse(s.DEBUG)
        self.assertTrue(s.SESSION_COOKIE_SECURE and s.CSRF_COOKIE_SECURE and s.SECURE_SSL_REDIRECT)
        self.assertGreaterEqual(s.SECURE_HSTS_SECONDS, 31536000)
        self.assertIn(r"^healthz/$", s.SECURE_REDIRECT_EXEMPT)

    def test_weak_or_default_secret_refused(self):
        for key in ("short", "insecure-development-key-do-not-use-in-production-xxxxxxxxxxxx"):
            with self.subTest(key=key), self.assertRaises(ImproperlyConfigured):
                load_production(**{**GOOD, "SECRET_KEY": key})

    def test_default_hosts_refused(self):
        with mock.patch.dict(os.environ, {k: v for k, v in GOOD.items() if k != "ALLOWED_HOSTS"}):
            os.environ.pop("ALLOWED_HOSTS", None)
            with self.assertRaises(ImproperlyConfigured):
                load_production(**{"SECRET_KEY": GOOD["SECRET_KEY"], "SITE_URL": GOOD["SITE_URL"]})

    def test_http_site_url_refused(self):
        with self.assertRaises(ImproperlyConfigured):
            load_production(**{**GOOD, "SITE_URL": "http://funera.example.com"})


class PayFastProductionGuardTests(SimpleTestCase):
    def test_sandbox_with_real_merchant_refused(self):
        with self.assertRaises(ImproperlyConfigured):
            load_production(
                **GOOD,
                PAYFAST_MERCHANT_ID="1",
                PAYFAST_MERCHANT_KEY="k",
                PAYFAST_PASSPHRASE="p",
                PAYFAST_SANDBOX="True",
            )

    def test_missing_passphrase_refused(self):
        with self.assertRaises(ImproperlyConfigured):
            load_production(
                **GOOD,
                PAYFAST_MERCHANT_ID="1",
                PAYFAST_MERCHANT_KEY="k",
                PAYFAST_PASSPHRASE="",
                PAYFAST_SANDBOX="False",
            )

    def test_live_configuration_loads(self):
        s = load_production(
            **GOOD, PAYFAST_MERCHANT_ID="1", PAYFAST_MERCHANT_KEY="k", PAYFAST_PASSPHRASE="p", PAYFAST_SANDBOX="False"
        )
        self.assertFalse(s.PAYFAST_SANDBOX)

    def test_unconfigured_payfast_is_allowed(self):
        load_production(**GOOD)
