from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing.models import Customer, Subscription
from catalog.models import Plan

from .helpers import make_subscription, priced_plan

FORM = {
    "name": "Thandi Mokoena",
    "business_name": "Mokoena Funerals",
    "email": "thandi@example.com",
    "phone": "082 123 4567",
    "accept": "on",
}


class SubscribeTests(TestCase):
    def setUp(self):
        self.plan = priced_plan()
        self.url = reverse("billing:subscribe", args=["starter"])

    def test_page_shows_prices_and_amount_due_today(self):
        html = self.client.get(self.url).content.decode()
        self.assertIn("R1 500.00", html.replace("\xa0", " ").replace(",", " "))
        self.assertIn("R4 000.00", html.replace("\xa0", " ").replace(",", " "))

    def test_unpriced_plan_goes_to_contact(self):
        response = self.client.get(reverse("billing:subscribe", args=["professional"]))
        self.assertRedirects(response, "/contact/?plan=professional", fetch_redirect_response=False)

    def test_unknown_or_inactive_plan_is_404(self):
        self.assertEqual(self.client.get(reverse("billing:subscribe", args=["nope"])).status_code, 404)
        Plan.objects.filter(code="starter").update(is_active=False)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    @override_settings(PAYFAST_MERCHANT_ID="", PAYFAST_MERCHANT_KEY="")
    def test_billing_off_when_payfast_not_configured(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_valid_signup_records_customer_and_price_snapshot(self):
        response = self.client.post(self.url, FORM)
        sub = Subscription.objects.get()
        self.assertRedirects(response, reverse("billing:pay", args=[sub.reference]))
        self.assertEqual((str(sub.initial_amount), str(sub.recurring_amount)), ("4000.00", "1500.00"))
        self.assertEqual(sub.status, Subscription.Status.PENDING)
        self.assertEqual(sub.customer.terms_version, "1.0")
        self.assertIsNotNone(sub.customer.terms_accepted_at)
        self.assertTrue(sub.reference.startswith("FN-"))

    def test_price_change_does_not_alter_existing_subscription(self):
        self.client.post(self.url, FORM)
        Plan.objects.filter(code="starter").update(monthly_price=9999)
        self.assertEqual(str(Subscription.objects.get().recurring_amount), "1500.00")

    def test_terms_must_be_accepted(self):
        data = {k: v for k, v in FORM.items() if k != "accept"}
        self.assertEqual(self.client.post(self.url, data).status_code, 200)
        self.assertFalse(Customer.objects.exists())

    def test_bad_phone_rejected(self):
        self.assertEqual(self.client.post(self.url, {**FORM, "phone": "12"}).status_code, 200)
        self.assertFalse(Customer.objects.exists())

    @override_settings(LEAD_RATE_LIMIT_PER_HOUR=2)
    def test_rate_limit(self):
        for _ in range(3):
            self.client.post(self.url, FORM)
        self.assertEqual(Customer.objects.count(), 2)

    def test_inputs_are_styled(self):
        self.assertIn('class="field"', self.client.get(self.url).content.decode())


class PayPageTests(TestCase):
    def setUp(self):
        self.sub = make_subscription()
        self.url = reverse("billing:pay", args=[self.sub.reference])

    def test_form_posts_signed_recurring_checkout_to_payfast(self):
        response = self.client.get(self.url)
        html = response.content.decode()
        self.assertIn('action="https://sandbox.payfast.co.za/eng/process"', html)
        for needle in (
            'name="subscription_type" value="1"',
            'name="recurring_amount" value="1500.00"',
            'name="amount" value="4000.00"',
            'name="frequency" value="3"',
            'name="cycles" value="0"',
            f'name="m_payment_id" value="{self.sub.reference}"',
            'name="signature"',
        ):
            self.assertIn(needle, html)
        self.assertIn("https://funera.test/billing/payfast/itn/", html)

    def test_merchant_key_is_a_form_field_but_passphrase_never_appears(self):
        html = self.client.get(self.url).content.decode()
        self.assertNotIn("test-passphrase", html)

    def test_csp_allows_posting_to_payfast_only(self):
        csp = self.client.get(self.url)["Content-Security-Policy"]
        self.assertIn("form-action 'self' https://sandbox.payfast.co.za", csp)

    def test_paid_subscription_redirects_to_status_page(self):
        Subscription.objects.update(status=Subscription.Status.ACTIVE)
        self.assertRedirects(self.client.get(self.url), reverse("billing:return", args=[self.sub.reference]))

    def test_status_pages_are_noindex_and_unknown_reference_404(self):
        for name in ("billing:return", "billing:cancelled"):
            html = self.client.get(reverse(name, args=[self.sub.reference])).content.decode()
            self.assertIn('name="robots" content="noindex"', html)
        self.assertEqual(self.client.get(reverse("billing:return", args=["FN-NOPE"])).status_code, 404)

    def test_return_page_does_not_activate_anything(self):
        self.client.get(reverse("billing:return", args=[self.sub.reference]))
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, Subscription.Status.PENDING)


class PricingButtonTests(TestCase):
    def test_subscribe_button_only_for_priced_plans(self):
        priced_plan()
        html = self.client.get(reverse("pages:pricing")).content.decode()
        self.assertIn("Subscribe to Starter", html)
        self.assertNotIn("Subscribe to Professional", html)
        self.assertIn("Talk to us about Professional", html)

    @override_settings(PAYFAST_MERCHANT_ID="")
    def test_no_subscribe_button_without_payfast(self):
        priced_plan()
        self.assertNotIn("Subscribe to", self.client.get(reverse("pages:pricing")).content.decode())


class AnnualBillingTests(TestCase):
    def setUp(self):
        self.plan = priced_plan(annual="15000.00")
        self.url = reverse("billing:subscribe", args=["starter"])

    def test_pricing_shows_annual_price_and_saving(self):
        html = self.client.get(reverse("pages:pricing")).content.decode().replace("\xa0", " ")
        self.assertIn("/ year", html)
        self.assertIn("save R3", html.replace(",", " ").replace("3 000", "3"))
        self.assertIn("?period=annual", html)

    def test_annual_option_hidden_when_plan_has_no_annual_price(self):
        Plan.objects.filter(code="starter").update(annual_price=None)
        self.assertNotContains(self.client.get(self.url), 'value="annual"')
        self.client.post(self.url, {**FORM, "billing_period": "annual"})
        self.assertFalse(Subscription.objects.exists())

    def test_query_string_preselects_annual(self):
        html = self.client.get(self.url, {"period": "annual"}).content.decode()
        self.assertRegex(html, r'value="annual"\s+class="[^"]*"\s+checked|value="annual"[^>]*checked')

    def test_annual_signup_amounts_and_checkout(self):
        self.client.post(self.url, {**FORM, "billing_period": "annual"})
        sub = Subscription.objects.get()
        self.assertEqual(
            (sub.billing_period, str(sub.initial_amount), str(sub.recurring_amount)), ("annual", "17500.00", "15000.00")
        )
        html = self.client.get(reverse("billing:pay", args=[sub.reference])).content.decode()
        for needle in (
            'name="frequency" value="6"',
            'name="recurring_amount" value="15000.00"',
            'name="amount" value="17500.00"',
        ):
            self.assertIn(needle, html)
        year_later = (timezone.localdate().replace(year=timezone.localdate().year + 1, day=1)).year
        self.assertRegex(html, rf'name="billing_date" value="{year_later}-')

    def test_annual_first_notification_accepts_initial_and_sets_next_date_a_year_out(self):
        from billing.tests.helpers import itn_pairs, post_itn

        self.client.post(self.url, {**FORM, "billing_period": "annual"})
        sub = Subscription.objects.get()
        self.assertEqual(post_itn(self.client, itn_pairs(sub)).status_code, 200)
        sub.refresh_from_db()
        self.assertGreater(sub.next_billing_date, timezone.localdate() + timedelta(days=360))
