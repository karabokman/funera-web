"""Production: every secret comes from the environment and the site refuses to start without them."""

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403

DEBUG = False
if "insecure" in SECRET_KEY or len(SECRET_KEY) < 40:  # noqa: F405
    raise ImproperlyConfigured("Set SECRET_KEY to a long random value (at least 40 characters).")
if ALLOWED_HOSTS == ["localhost", "127.0.0.1"]:  # noqa: F405
    raise ImproperlyConfigured("Set ALLOWED_HOSTS to your domain name(s).")
if not SITE_URL.startswith("https://"):  # noqa: F405
    raise ImproperlyConfigured("SITE_URL must be the public https:// address of the site.")

if PAYFAST_MERCHANT_ID and PAYFAST_SANDBOX:  # noqa: F405
    raise ImproperlyConfigured(
        "PayFast merchant details are set but PAYFAST_SANDBOX is still on: set PAYFAST_SANDBOX=False to take real payments."
    )
if PAYFAST_MERCHANT_ID and not PAYFAST_PASSPHRASE:  # noqa: F405
    raise ImproperlyConfigured("Set PAYFAST_PASSPHRASE (and the same value in your PayFast dashboard).")

# The container sits behind a TLS-terminating proxy (nginx, Caddy, the host's load balancer).
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)  # noqa: F405
SECURE_REDIRECT_EXEMPT = [r"^healthz/$"]  # the container health check speaks plain HTTP
SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=31536000)  # noqa: F405
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = False  # opt in deliberately once every subdomain is https
SILENCED_SYSTEM_CHECKS = ["security.W021"]  # preload is a deliberate, irreversible opt-in; see docs/DEPLOYMENT.md
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
X_FRAME_OPTIONS = "DENY"
CSRF_TRUSTED_ORIGINS = CSRF_TRUSTED_ORIGINS or [SITE_URL]  # noqa: F405
