import re
from datetime import timedelta
from unittest import mock

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing import payfast, portal_auth
from billing.models import LoginToken, Payment, Refund, Subscription

from .helpers import live_subscription, make_subscription, priced_plan


def token_from_mail():
    return re.search(r"/portal/login/([\w-]+)/", mail.outbox[-1].body).group(1)


def sign_in(client, email="thandi@example.com"):
    session = client.session
    session[portal_auth.SESSION_KEY] = email
    session.save()


class PortalTestCase(TestCase):
    def request_link(self, email="thandi@example.com", **extra):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(reverse("billing:portal_login"), {"email": email}, **extra)


class LoginFlowTests(PortalTestCase):
    def setUp(self):
        self.sub = live_subscription()

    def test_known_address_gets_a_link_and_unknown_gets_the_same_page_without_mail(self):
        known = self.request_link()
        self.assertEqual(len(mail.outbox), 1)
        unknown = self.request_link(email="nobody@example.com")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(known.status_code, unknown.status_code)
        self.assertTemplateUsed(unknown, "billing/portal/login_sent.html")

    def test_pending_only_customer_gets_no_link(self):
        make_subscription(email="new@example.com")
        self.request_link(email="new@example.com")
        self.assertEqual(mail.outbox, [])

    def test_address_match_ignores_case(self):
        self.request_link(email="THANDI@Example.com")
        self.assertEqual(len(mail.outbox), 1)

    def test_link_signs_in_once_only_and_only_by_post(self):
        self.request_link()
        token = token_from_mail()
        url = reverse("billing:portal_login_confirm", args=[token])
        self.assertEqual(self.client.get(url).status_code, 200)  # a mail scanner opening the link...
        self.assertEqual(self.client.get(reverse("billing:portal")).status_code, 302)  # ...signs nobody in
        self.assertRedirects(self.client.post(url), reverse("billing:portal"))
        self.assertEqual(self.client.get(reverse("billing:portal")).status_code, 200)
        other = self.client_class()
        self.assertEqual(other.post(url).status_code, 400)  # second use fails

    def test_token_is_stored_hashed(self):
        self.request_link()
        token = token_from_mail()
        self.assertFalse(LoginToken.objects.filter(token_hash=token).exists())

    def test_expired_and_garbage_links_rejected(self):
        self.request_link()
        token = token_from_mail()
        LoginToken.objects.update(created_at=timezone.now() - timedelta(minutes=21))
        self.assertEqual(self.client.post(reverse("billing:portal_login_confirm", args=[token])).status_code, 400)
        self.assertEqual(self.client.post(reverse("billing:portal_login_confirm", args=["nonsense"])).status_code, 400)

    def test_sign_in_rotates_session_key_and_sets_short_expiry(self):
        self.request_link()
        self.client.get(reverse("billing:portal_login"))
        before = self.client.session.session_key
        self.client.post(reverse("billing:portal_login_confirm", args=[token_from_mail()]))
        self.assertNotEqual(before, self.client.session.session_key)
        self.assertLessEqual(self.client.session.get_expiry_age(), 2 * 3600)

    @override_settings(PORTAL_LOGIN_RATE_PER_HOUR=2)
    def test_per_visitor_limit_stops_mail_flooding(self):
        for _ in range(4):
            self.request_link()
        self.assertEqual(len(mail.outbox), 1)  # the second attempt used up the allowance of two

    def test_per_address_limit(self):
        for i in range(6):
            self.request_link(REMOTE_ADDR=f"10.0.0.{i}")
        self.assertEqual(len(mail.outbox), 2)

    def test_logout_ends_the_session(self):
        sign_in(self.client)
        self.client.post(reverse("billing:portal_logout"))
        self.assertEqual(self.client.get(reverse("billing:portal")).status_code, 302)

    def test_portal_pages_are_not_cached_and_are_noindex(self):
        sign_in(self.client)
        response = self.client.get(reverse("billing:portal"))
        self.assertIn("no-store", response["Cache-Control"])
        self.assertContains(response, 'content="noindex, nofollow"')

    def test_robots_hides_portal(self):
        self.assertIn("Disallow: /portal/", self.client.get(reverse("pages:robots")).content.decode())


class OwnershipTests(TestCase):
    def setUp(self):
        self.mine = live_subscription()
        self.theirs = live_subscription(email="other@example.com")
        sign_in(self.client)

    def test_dashboard_lists_only_own_subscriptions(self):
        response = self.client.get(reverse("billing:portal"))
        self.assertEqual([s.pk for s in response.context["subscriptions"]], [self.mine.pk])

    def test_cannot_open_or_act_on_someone_elses_subscription(self):
        ref = self.theirs.reference
        self.assertEqual(self.client.get(reverse("billing:portal_subscription", args=[ref])).status_code, 404)
        with mock.patch("billing.payfast.cancel_subscription") as api:
            for name in ("portal_pause", "portal_resume", "portal_switch", "portal_cancel"):
                self.assertEqual(
                    self.client.post(
                        reverse(f"billing:{name}", args=[ref]), {"confirm": "on", "cycles": 1}
                    ).status_code,
                    404,
                )
        api.assert_not_called()

    def test_cannot_request_refund_on_someone_elses_payment(self):
        payment = Payment.objects.create(
            subscription=self.theirs, pf_payment_id="777", status="COMPLETE", amount_gross=100
        )
        self.assertEqual(
            self.client.post(
                reverse("billing:portal_refund", args=[payment.pf_payment_id]), {"reason": "x"}
            ).status_code,
            404,
        )

    def test_signed_out_visitor_is_sent_to_login_for_every_portal_url(self):
        self.client.logout()
        session = self.client.session
        session.flush()
        for name, args in (("portal", []), ("portal_subscription", [self.mine.reference]), ("portal_refund", ["1"])):
            self.assertRedirects(
                self.client.get(reverse(f"billing:{name}", args=args)), reverse("billing:portal_login")
            )

    def test_actions_are_post_only_and_csrf_protected(self):
        url = reverse("billing:portal_cancel", args=[self.mine.reference])
        self.assertEqual(self.client.get(url).status_code, 405)
        strict = self.client_class(enforce_csrf_checks=True)
        sign_in(strict)
        self.assertEqual(strict.post(url, {"confirm": "on"}).status_code, 403)

    def test_pending_subscription_is_not_shown(self):
        pending = make_subscription()
        self.assertNotIn(
            pending.pk, [s.pk for s in self.client.get(reverse("billing:portal")).context["subscriptions"]]
        )


class PortalActionTests(TestCase):
    def setUp(self):
        self.plan = priced_plan(annual="15000.00")
        self.sub = live_subscription(plan=self.plan)
        sign_in(self.client)

    def post(self, name, data=None):
        return self.client.post(reverse(f"billing:{name}", args=[self.sub.reference]), data or {}, follow=True)

    def test_pause_and_resume(self):
        with mock.patch("billing.payfast.pause_subscription"), mock.patch("billing.payfast.unpause_subscription"):
            response = self.post("portal_pause", {"cycles": 2})
            self.assertContains(response, "Your subscription is paused.")
            self.assertContains(response, "Resume now")
            self.sub.refresh_from_db()
            self.assertEqual(self.sub.status, Subscription.Status.PAUSED)
            self.assertContains(self.post("portal_resume"), "active again")

    def test_pause_beyond_limit_shows_message_and_changes_nothing(self):
        with mock.patch("billing.payfast.pause_subscription") as api:
            response = self.post("portal_pause", {"cycles": 9})
        api.assert_not_called()
        self.assertContains(response, "Choose how long to pause for")

    def test_payfast_down_shows_friendly_error_and_keeps_state(self):
        with mock.patch("billing.payfast.pause_subscription", side_effect=payfast.GatewayUnavailable("x")):
            response = self.post("portal_pause", {"cycles": 1})
        self.assertContains(response, "nothing was changed")
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, Subscription.Status.ACTIVE)

    def test_switch_to_annual(self):
        with mock.patch("billing.payfast.update_subscription"):
            response = self.post("portal_switch", {"billing_period": "annual"})
        self.assertContains(response, "takes effect at your next payment")
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.billing_period, "annual")

    def test_switch_offer_only_when_the_other_period_exists(self):
        self.assertContains(
            self.client.get(reverse("billing:portal_subscription", args=[self.sub.reference])),
            "Switch to annual billing",
        )
        priced_plan(annual=None)
        self.assertNotContains(
            self.client.get(reverse("billing:portal_subscription", args=[self.sub.reference])),
            "Switch to annual billing",
        )

    def test_cancel_needs_confirmation(self):
        with mock.patch("billing.payfast.cancel_subscription") as api:
            self.post("portal_cancel", {})
        api.assert_not_called()
        with mock.patch("billing.payfast.cancel_subscription") as api:
            response = self.post("portal_cancel", {"confirm": "on", "reason": "closing"})
        api.assert_called_once()
        self.assertContains(response, "No further payments will be collected")
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, Subscription.Status.CANCELLED)

    def test_cancelled_subscription_page_has_no_actions(self):
        Subscription.objects.update(status=Subscription.Status.CANCELLED)
        html = self.client.get(reverse("billing:portal_subscription", args=[self.sub.reference])).content.decode()
        for text in ("Pause payments", "Cancel subscription", "Resume payments"):
            self.assertNotIn(text, html)


class PortalRefundTests(TestCase):
    def setUp(self):
        self.sub = live_subscription()
        self.payment = Payment.objects.create(
            subscription=self.sub, pf_payment_id="900", status="COMPLETE", amount_gross=4000
        )
        sign_in(self.client)
        self.url = reverse("billing:portal_refund", args=["900"])

    def test_request_inside_window(self):
        response = self.client.post(self.url, {"reason": "Bought by mistake"}, follow=True)
        self.assertContains(response, "refund request was sent")
        self.assertEqual(Refund.objects.get().status, "requested")
        self.assertContains(response, "refund requested")

    def test_link_hidden_and_request_refused_outside_window(self):
        Payment.objects.update(received_at=timezone.now() - timedelta(days=30))
        self.assertNotContains(
            self.client.get(reverse("billing:portal_subscription", args=[self.sub.reference])), "Request a refund"
        )
        response = self.client.post(self.url, {"reason": "x"}, follow=True)
        self.assertContains(response, "within 7 days")
        self.assertFalse(Refund.objects.exists())

    def test_reason_required(self):
        self.assertEqual(self.client.post(self.url, {"reason": ""}).status_code, 200)
        self.assertFalse(Refund.objects.exists())
