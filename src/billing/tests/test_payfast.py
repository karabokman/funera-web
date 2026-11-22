import hashlib
from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings

from billing import payfast
from billing.services import add_one_month, client_ip


class SignatureTests(SimpleTestCase):
    def test_php_urlencode_matches_php(self):
        self.assertEqual(payfast.php_urlencode("a b~c/d@e.f"), "a+b%7Ec%2Fd%40e.f")

    def test_checkout_signature_is_md5_of_ordered_pairs_with_passphrase(self):
        pairs = [("merchant_id", "10000100"), ("amount", "100.00"), ("item_name", "Cover plan")]
        expected = hashlib.md5(
            b"merchant_id=10000100&amount=100.00&item_name=Cover+plan&passphrase=pass+phrase"
        ).hexdigest()
        self.assertEqual(payfast.checkout_signature(pairs, "pass phrase"), expected)

    def test_blank_fields_are_left_out_of_the_checkout_signature(self):
        self.assertEqual(
            payfast.checkout_signature([("a", "1"), ("b", " "), ("c", "3")]), hashlib.md5(b"a=1&c=3").hexdigest()
        )

    def test_itn_signature_keeps_blank_fields_and_ignores_signature(self):
        pairs = [("a", "1"), ("b", ""), ("signature", "zzz")]
        self.assertEqual(payfast.itn_signature(pairs), hashlib.md5(b"a=1&b=").hexdigest())

    def test_valid_signature_detects_tampering(self):
        pairs = [("a", "1"), ("b", "2")]
        good = pairs + [("signature", payfast.itn_signature(pairs, "test-passphrase"))]
        self.assertTrue(payfast.valid_signature(good))
        self.assertFalse(payfast.valid_signature([("a", "1"), ("b", "3"), good[-1]]))
        self.assertFalse(payfast.valid_signature(pairs))

    def test_signed_checkout_orders_fields_and_signs_last(self):
        url, fields = payfast.signed_checkout(
            {"amount": "5.00", "merchant_id": "1", "m_payment_id": "X", "frequency": "3"}
        )
        self.assertIn("sandbox.payfast.co.za", url)
        self.assertEqual([k for k, _ in fields], ["merchant_id", "m_payment_id", "amount", "frequency", "signature"])


class SourceAndHelpersTests(TestCase):
    def test_source_must_be_in_allowed_networks_or_resolve_to_payfast(self):
        with mock.patch("billing.payfast._resolved_hosts", return_value={"1.2.3.4"}):
            self.assertTrue(payfast.valid_source("197.97.145.150"))
            self.assertTrue(payfast.valid_source("1.2.3.4"))
            self.assertFalse(payfast.valid_source("8.8.8.8"))
            self.assertFalse(payfast.valid_source("not-an-ip"))

    def test_add_one_month_clamps_to_month_end(self):
        from datetime import date

        self.assertEqual(add_one_month(date(2026, 1, 31)), date(2026, 2, 28))
        self.assertEqual(add_one_month(date(2026, 12, 15)), date(2027, 1, 15))

    def test_client_ip_ignores_spoofed_left_entries(self):
        class Req:
            META = {"REMOTE_ADDR": "10.0.0.1", "HTTP_X_FORWARDED_FOR": "6.6.6.6, 197.97.145.150"}

        with override_settings(BILLING_TRUSTED_PROXIES=1):
            self.assertEqual(client_ip(Req), "197.97.145.150")
        with override_settings(BILLING_TRUSTED_PROXIES=0):
            self.assertEqual(client_ip(Req), "10.0.0.1")
