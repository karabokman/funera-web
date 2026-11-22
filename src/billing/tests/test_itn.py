from unittest import mock

from django.core import mail
from django.test import Client, TestCase, override_settings

from billing import payfast
from billing.models import Payment, Subscription
from billing.signals import subscription_activated

from .helpers import itn_pairs, make_subscription, post_itn


class ItnTests(TestCase):
    def setUp(self):
        self.sub = make_subscription()
        self.activated = []
        subscription_activated.connect(self.listener, weak=False)
        self.addCleanup(subscription_activated.disconnect, self.listener)

    def listener(self, sender, subscription, **kwargs):
        self.activated.append(subscription.reference)

    def send(self, pairs=None, **kw):
        with self.captureOnCommitCallbacks(execute=True):
            return post_itn(self.client, pairs or itn_pairs(self.sub), **kw)

    def test_first_payment_activates_stores_token_emails_and_announces_once(self):
        self.assertEqual(self.send().status_code, 200)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, Subscription.Status.ACTIVE)
        self.assertEqual(self.sub.payfast_token, "tok-123")
        self.assertIsNotNone(self.sub.activated_at)
        self.assertEqual(sorted(m.to[0] for m in mail.outbox), ["sales@example.com", "thandi@example.com"])
        self.assertEqual(self.activated, [self.sub.reference])
        self.assertEqual(Payment.objects.get().pf_payment_id, "1089250")

    def test_repeated_notification_changes_nothing(self):
        self.send()
        self.send()
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(len(self.activated), 1)

    def test_monthly_payment_is_recorded_without_a_second_welcome(self):
        self.send()
        mail.outbox.clear()
        self.sub.refresh_from_db()
        first = self.sub.activated_at
        self.send(itn_pairs(self.sub, amount=self.sub.recurring_amount, pf_id="1089999"))
        self.sub.refresh_from_db()
        self.assertEqual(Payment.objects.count(), 2)
        self.assertEqual(self.sub.activated_at, first)
        self.assertEqual(mail.outbox, [])
        self.assertEqual(len(self.activated), 1)

    def test_failed_collection_marks_past_due_and_later_payment_recovers(self):
        self.send()
        self.send(itn_pairs(self.sub, status="FAILED", amount=self.sub.recurring_amount, pf_id="2"))
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, Subscription.Status.PAST_DUE)
        self.send(itn_pairs(self.sub, amount=self.sub.recurring_amount, pf_id="3"))
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, Subscription.Status.ACTIVE)

    def test_failed_first_payment_stays_pending(self):
        self.send(itn_pairs(self.sub, status="FAILED"))
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, Subscription.Status.PENDING)
        self.assertEqual(mail.outbox, [])

    def test_cancelled_notification_cancels(self):
        self.send()
        self.send(itn_pairs(self.sub, status="CANCELLED", amount=self.sub.recurring_amount, pf_id="4"))
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, Subscription.Status.CANCELLED)
        self.assertIsNotNone(self.sub.cancelled_at)

    def test_bad_signature_rejected(self):
        pairs = itn_pairs(self.sub, sign=False) + [("signature", "0" * 32)]
        self.assertEqual(self.send(pairs).status_code, 400)
        self.assertFalse(Payment.objects.exists())

    def test_wrong_passphrase_rejected(self):
        self.assertEqual(self.send(itn_pairs(self.sub, passphrase="other")).status_code, 400)

    def test_tampered_amount_rejected(self):
        pairs = itn_pairs(self.sub)
        pairs = [(k, "1.00" if k == "amount_gross" else v) for k, v in pairs]  # signature no longer matches
        self.assertEqual(self.send(pairs).status_code, 400)

    def test_correctly_signed_but_wrong_amount_rejected(self):
        self.assertEqual(self.send(itn_pairs(self.sub, amount=1)).status_code, 400)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, Subscription.Status.PENDING)

    def test_wrong_merchant_and_unknown_reference_rejected(self):
        self.assertEqual(self.send(itn_pairs(self.sub, merchant="999")).status_code, 400)
        pairs = [(k, "FN-UNKNOWN" if k == "m_payment_id" else v) for k, v in itn_pairs(self.sub, sign=False)]
        pairs.append(("signature", payfast.itn_signature(pairs, "test-passphrase")))
        self.assertEqual(self.send(pairs).status_code, 400)

    def test_get_not_allowed_and_no_csrf_needed_for_post(self):
        self.assertEqual(self.client.get("/billing/payfast/itn/").status_code, 405)
        strict = Client(enforce_csrf_checks=True)
        self.assertEqual(post_itn(strict, itn_pairs(self.sub)).status_code, 200)

    @override_settings(PAYFAST_CHECK_SOURCE_IP=True)
    def test_sender_address_checked(self):
        with mock.patch("billing.payfast._resolved_hosts", return_value=set()):
            self.assertEqual(self.send(REMOTE_ADDR="8.8.8.8").status_code, 400)
            self.assertEqual(self.send(REMOTE_ADDR="197.97.145.150").status_code, 200)

    @override_settings(PAYFAST_VALIDATE_ITN=True)
    def test_payfast_confirmation_required(self):
        with mock.patch("billing.payfast.confirmed_by_payfast", return_value=False):
            self.assertEqual(self.send().status_code, 400)
        with mock.patch("billing.payfast.confirmed_by_payfast", return_value=True):
            self.assertEqual(self.send().status_code, 200)

    @override_settings(PAYFAST_VALIDATE_ITN=True)
    def test_payfast_unreachable_asks_for_retry_and_changes_nothing(self):
        with mock.patch("billing.payfast.confirmed_by_payfast", side_effect=payfast.GatewayUnavailable("down")):
            self.assertEqual(self.send().status_code, 503)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, Subscription.Status.PENDING)

    def test_mail_failure_does_not_undo_activation_and_listener_still_runs(self):
        with mock.patch("billing.services.send_mail", side_effect=OSError("smtp down")):
            self.assertEqual(self.send().status_code, 200)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, Subscription.Status.ACTIVE)
        self.assertEqual(len(self.activated), 1)

    def test_failing_listener_does_not_break_the_notification(self):
        def boom(sender, subscription, **kwargs):
            raise RuntimeError("provisioner down")

        subscription_activated.connect(boom, weak=False)
        self.addCleanup(subscription_activated.disconnect, boom)
        self.assertEqual(self.send().status_code, 200)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, Subscription.Status.ACTIVE)


class CancelTests(TestCase):
    def setUp(self):
        self.sub = make_subscription(status=Subscription.Status.ACTIVE)
        self.sub.payfast_token = "tok-123"
        self.sub.save()

    def test_cancels_at_payfast_then_locally(self):
        from billing import services

        with mock.patch("billing.payfast.cancel_subscription") as api:
            services.cancel(self.sub)
        api.assert_called_once_with("tok-123")
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, Subscription.Status.CANCELLED)

    def test_stays_active_when_payfast_does_not_confirm(self):
        from billing import services

        with mock.patch("billing.payfast.cancel_subscription", side_effect=payfast.GatewayUnavailable("x")):
            with self.assertRaises(payfast.GatewayUnavailable):
                services.cancel(self.sub)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, Subscription.Status.ACTIVE)

    def test_api_call_is_signed_and_uses_sandbox_flag(self):
        captured = {}

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"status": "success"}'

        def fake_open(request, timeout):
            captured.update(url=request.full_url, headers=dict(request.header_items()), method=request.get_method())
            return Resp()

        with mock.patch("billing.payfast.urllib.request.urlopen", fake_open):
            payfast.cancel_subscription("tok-123")
        self.assertEqual(captured["method"], "PUT")
        self.assertTrue(captured["url"].endswith("/subscriptions/tok-123/cancel?testing=true"))
        self.assertIn("Signature", captured["headers"])
        self.assertEqual(captured["headers"]["Merchant-id"], "10000100")


class AdminTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model

        self.client.force_login(get_user_model().objects.create_superuser("boss", "b@example.com", "pw-12345-long"))
        self.sub = make_subscription(status=Subscription.Status.ACTIVE)

    def test_cancel_action(self):
        from django.urls import reverse

        with mock.patch("billing.payfast.cancel_subscription"):
            self.client.post(
                reverse("admin:billing_subscription_changelist"),
                {"action": "cancel_subscriptions", "_selected_action": [self.sub.pk]},
            )
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, Subscription.Status.CANCELLED)

    def test_financial_records_cannot_be_added_or_deleted_in_admin(self):
        from django.urls import reverse

        self.assertEqual(self.client.get(reverse("admin:billing_subscription_add")).status_code, 403)
        self.assertEqual(
            self.client.get(reverse("admin:billing_subscription_delete", args=[self.sub.pk])).status_code, 403
        )
        self.assertEqual(
            self.client.get(reverse("admin:billing_customer_delete", args=[self.sub.customer.pk])).status_code, 403
        )
