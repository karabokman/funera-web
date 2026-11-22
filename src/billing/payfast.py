"""PayFast adapter for subscriptions: signed checkout form, notification (ITN) checks, cancellation API.

Follows PayFast's custom-integration, ITN and subscription-API documentation (the same adapter the Funera platform uses
for member premiums). Nothing here touches the database.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import socket
import urllib.error
import urllib.request
from decimal import Decimal
from urllib.parse import quote_plus, urlencode

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

PROCESS_URL = {True: "https://sandbox.payfast.co.za/eng/process", False: "https://www.payfast.co.za/eng/process"}
VALIDATE_URL = {
    True: "https://sandbox.payfast.co.za/eng/query/validate",
    False: "https://www.payfast.co.za/eng/query/validate",
}
API_URL = "https://api.payfast.co.za"
VALID_HOSTS = ("www.payfast.co.za", "sandbox.payfast.co.za", "w1w.payfast.co.za", "w2w.payfast.co.za")
FREQUENCY_MONTHLY = "3"

# The checkout signature is calculated over the fields in PayFast's documented order, not alphabetically.
FIELD_ORDER = [
    "merchant_id", "merchant_key", "return_url", "cancel_url", "notify_url", "name_first", "name_last",
    "email_address", "cell_number", "m_payment_id", "amount", "item_name", "item_description",
    "custom_int1", "custom_int2", "custom_int3", "custom_int4", "custom_int5",
    "custom_str1", "custom_str2", "custom_str3", "custom_str4", "custom_str5",
    "email_confirmation", "confirmation_address", "payment_method",
    "subscription_type", "billing_date", "recurring_amount", "frequency", "cycles",
]  # fmt: skip


class GatewayUnavailable(Exception):
    """PayFast could not be reached or answered unexpectedly; the caller lets it retry later."""


def php_urlencode(value) -> str:
    """URL-encode exactly as PHP's ``urlencode`` (PayFast's reference code): ``+`` for spaces, upper-case hex, ``~`` encoded."""
    return quote_plus(str(value), safe="").replace("~", "%7E")


def checkout_signature(pairs, passphrase: str = "") -> str:
    parts = [f"{key}={php_urlencode(str(value).strip())}" for key, value in pairs if str(value).strip() != ""]
    if passphrase:
        parts.append(f"passphrase={php_urlencode(passphrase.strip())}")
    return hashlib.md5("&".join(parts).encode()).hexdigest()  # noqa: S324 - the algorithm PayFast specifies


def itn_param_string(pairs) -> str:
    """The notification as received (same order, blanks kept), minus ``signature``."""
    return "&".join(f"{key}={php_urlencode(value)}" for key, value in pairs if key != "signature")


def itn_signature(pairs, passphrase: str = "") -> str:
    string = itn_param_string(pairs)
    if passphrase:
        string += f"&passphrase={php_urlencode(passphrase.strip())}"
    return hashlib.md5(string.encode()).hexdigest()  # noqa: S324


def api_signature(params: dict, passphrase: str) -> str:
    items = {**params, "passphrase": passphrase}
    string = "&".join(f"{key}={php_urlencode(items[key])}" for key in sorted(items))
    return hashlib.md5(string.encode()).hexdigest()  # noqa: S324


def is_configured() -> bool:
    return bool(settings.PAYFAST_MERCHANT_ID and settings.PAYFAST_MERCHANT_KEY)


def process_origin() -> str:
    """Scheme and host of the PayFast checkout, for the content-security-policy ``form-action``."""
    return "https://sandbox.payfast.co.za" if settings.PAYFAST_SANDBOX else "https://www.payfast.co.za"


def signed_checkout(values: dict) -> tuple[str, list[tuple[str, str]]]:
    """The URL to post to and the ordered, signed form fields."""
    pairs = [(key, str(values[key])) for key in FIELD_ORDER if str(values.get(key, "")).strip() != ""]
    pairs.append(("signature", checkout_signature(pairs, settings.PAYFAST_PASSPHRASE)))
    return PROCESS_URL[settings.PAYFAST_SANDBOX], pairs


def valid_signature(pairs) -> bool:
    sent = dict(pairs).get("signature", "")
    return bool(sent) and hmac.compare_digest(sent.lower(), itn_signature(pairs, settings.PAYFAST_PASSPHRASE))


def _resolved_hosts() -> set[str]:
    addresses = cache.get("payfast:host_ips")
    if addresses is None:
        addresses = set()
        for host in VALID_HOSTS:
            try:
                addresses.update(info[4][0] for info in socket.getaddrinfo(host, None, socket.AF_INET))
            except OSError:
                logger.warning("Could not resolve %s while checking a PayFast notification", host)
        cache.set("payfast:host_ips", addresses, 600)
    return addresses


def valid_source(ip: str) -> bool:
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if any(address in ipaddress.ip_network(net, strict=False) for net in settings.PAYFAST_ALLOWED_NETWORKS):
        return True
    return str(address) in _resolved_hosts()


def confirmed_by_payfast(pairs) -> bool:
    """Post the notification back to PayFast, which answers VALID for genuine ones. Raises ``GatewayUnavailable`` when
    PayFast cannot be reached, so the notification is retried instead of being lost or wrongly rejected."""
    request = urllib.request.Request(
        VALIDATE_URL[settings.PAYFAST_SANDBOX],
        data=itn_param_string(pairs).encode(),
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(request, timeout=settings.PAYFAST_TIMEOUT) as response:  # noqa: S310
            return response.read().decode().strip() == "VALID"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise GatewayUnavailable(str(exc)) from exc


def _call(method: str, path: str, body: dict | None = None) -> dict:
    """One signed call to PayFast's REST API. Body fields are signed together with the header fields. Raises
    ``GatewayUnavailable`` unless PayFast answers ``status: success``."""
    stamp = timezone.localtime().replace(microsecond=0).isoformat()
    body = {key: str(value) for key, value in (body or {}).items()}
    header_fields = {"merchant-id": settings.PAYFAST_MERCHANT_ID, "version": "v1", "timestamp": stamp}
    headers = {**header_fields, "signature": api_signature({**header_fields, **body}, settings.PAYFAST_PASSPHRASE)}
    url = f"{API_URL}{path}" + ("?testing=true" if settings.PAYFAST_SANDBOX else "")
    data = urlencode(body).encode() if body else None
    if data:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=settings.PAYFAST_TIMEOUT) as response:  # noqa: S310
            result = json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        raise GatewayUnavailable(f"PayFast answered {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise GatewayUnavailable(str(exc)) from exc
    if result.get("status") != "success":
        raise GatewayUnavailable(f"PayFast did not confirm the request: {result}")
    return result


def cents(amount) -> int:
    return int((Decimal(amount) * 100).to_integral_value())


def cancel_subscription(token: str) -> None:
    """Stop future collections."""
    _call("PUT", f"/subscriptions/{token}/cancel")


def pause_subscription(token: str, cycles: int) -> None:
    """Skip the next ``cycles`` collections; PayFast restarts them by itself afterwards."""
    _call("PUT", f"/subscriptions/{token}/pause", {"cycles": cycles})


def unpause_subscription(token: str) -> None:
    _call("PUT", f"/subscriptions/{token}/unpause")


def update_subscription(token: str, *, amount, frequency: str, run_date) -> None:
    """Change what future collections charge (amount in rand), how often, and the next collection date."""
    _call(
        "PATCH",
        f"/subscriptions/{token}/update",
        {"amount": cents(amount), "frequency": frequency, "run_date": run_date.isoformat()},
    )


def refund_payment(pf_payment_id: str, amount, reason: str) -> None:
    """Return ``amount`` (rand) of an earlier payment to the buyer's card."""
    _call("POST", f"/refunds/{pf_payment_id}", {"amount": cents(amount), "reason": reason[:100], "notify_buyer": 1})
