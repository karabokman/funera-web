from urllib.parse import urlparse

from django.conf import settings

from billing.payfast import process_origin


class SecurityHeadersMiddleware:
    """A strict Content-Security-Policy plus a few headers Django does not set on its own.

    The public site has no inline scripts or styles, so the policy can be tight. The admin is excluded because Django's
    admin uses inline styles."""

    def __init__(self, get_response):
        self.get_response = get_response
        script_src, connect_src = ["'self'"], ["'self'"]
        if settings.PLAUSIBLE_DOMAIN:
            origin = "{0.scheme}://{0.netloc}".format(urlparse(settings.PLAUSIBLE_SCRIPT_URL))
            script_src.append(origin)
            connect_src.append(origin)
        form_action = ["'self'"]
        if settings.PAYFAST_MERCHANT_ID:
            form_action.append(process_origin())  # the checkout form posts to PayFast
        self.csp = "; ".join(
            [
                "default-src 'self'",
                f"script-src {' '.join(script_src)}",
                "style-src 'self'",
                "img-src 'self' data:",
                "font-src 'self'",
                f"connect-src {' '.join(connect_src)}",
                "object-src 'none'",
                "base-uri 'self'",
                f"form-action {' '.join(form_action)}",
                "frame-ancestors 'none'",
            ]
        )
        self.admin_prefix = "/" + settings.ADMIN_URL

    def __call__(self, request):
        response = self.get_response(request)
        if not request.path.startswith(self.admin_prefix):
            response.headers.setdefault("Content-Security-Policy", self.csp)
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        return response
