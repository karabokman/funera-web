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
    "EMAIL_BACKEND": "django.core.mail.backends.smtp.EmailBackend",
    "EMAIL_HOST": "smtp.example.com",
}


def load_production(**env):
    # clear=True and no .env: the result depends only on the values given here, not on this machine's configuration.
    with mock.patch.dict(os.environ, env, clear=True), mock.patch("environ.Env.read_env"):
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
        with self.assertRaises(ImproperlyConfigured):
            load_production(SECRET_KEY=GOOD["SECRET_KEY"], SITE_URL=GOOD["SITE_URL"])

    def test_http_site_url_refused(self):
        with self.assertRaises(ImproperlyConfigured):
            load_production(**{**GOOD, "SITE_URL": "http://funera.example.com"})


class EmailGuardTests(SimpleTestCase):
    def test_smtp_without_host_is_refused_in_production(self):
        with self.assertRaises(ImproperlyConfigured):
            load_production(**{**GOOD, "EMAIL_HOST": ""})

    def test_console_backend_is_refused_in_production(self):
        with self.assertRaises(ImproperlyConfigured):
            load_production(**{**GOOD, "EMAIL_BACKEND": "django.core.mail.backends.console.EmailBackend"})

    def test_smtp_without_host_falls_back_to_console_outside_production(self):
        with (
            mock.patch.dict(
                os.environ,
                {"EMAIL_BACKEND": "django.core.mail.backends.smtp.EmailBackend", "EMAIL_HOST": ""},
                clear=True,
            ),
            mock.patch("environ.Env.read_env"),
        ):
            base = importlib.reload(importlib.import_module("config.settings.base"))
            try:
                self.assertTrue(base.EMAIL_BACKEND.endswith("console.EmailBackend"))
                self.assertTrue(base.EMAIL_MISCONFIGURED)
            finally:
                importlib.reload(base)


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


class EnvExampleTests(SimpleTestCase):
    """`.env.example` is what people copy, so it must load as written: django-environ keeps text after a value."""

    def test_example_file_parses_and_has_no_inline_comments(self):
        from pathlib import Path

        import environ

        path = Path(__file__).resolve().parents[3] / ".env.example"
        if not path.exists():  # e.g. an installed copy without the repository root
            self.skipTest(".env.example not found")
        for number, line in enumerate(path.read_text().splitlines(), 1):
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                key, _, value = stripped.partition("=")
                self.assertNotIn(" #", value, f".env.example line {number}: inline comment after {key}")
        with mock.patch.dict(os.environ, {}, clear=True):
            environ.Env.read_env(path)
            env = environ.Env()
            self.assertEqual(env.int("BILLING_TRUSTED_PROXIES"), 1)
            self.assertTrue(env.bool("PAYFAST_SANDBOX"))
            self.assertEqual(env.int("PAUSE_MAX_CYCLES"), 3)
