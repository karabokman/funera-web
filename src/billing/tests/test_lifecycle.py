from datetime import timedelta
from unittest import mock

from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from billing import lifecycle, payfast, refunds
from billing.errors import BillingError
from billing.models import Payment, Refund, Subscription
from billing.signals import subscription_status_changed

from .helpers import live_subscription, priced_plan


class PauseResumeTests(TestCase):
    def setUp(self):
        self.sub = live_subscription()

    def test_pause_calls_payfast_then_records_state_event_and_notifies(self):
        with mock.patch("billing.payfast.pause_subscription") as api, self.captureOnCommitCallbacks(execute=True):
            lifecycle.pause(self.sub, 2)
        api.assert_called_once_with("tok-123", 2)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, Subscription.Status.PAUSED)
        self.assertGreater(self.sub.paused_until, self.sub.next_billing_date)
        self.assertEqual(self.sub.events.get().kind, "paused")
        self.assertEqual([m.to[0] for m in mail.outbox], ["sales@example.com"])

    def test_pause_limits_and_state_rules(self):
        for cycles in (0, 4):
            with self.subTest(cycles=cycles), self.assertRaises(BillingError):
                lifecycle.pause(self.sub, cycles)
        Subscription.objects.update(status=Subscription.Status.CANCELLED)
        self.sub.refresh_from_db()
        with self.assertRaises(BillingError):
            lifecycle.pause(self.sub, 1)

    @override_settings(PAUSE_MAX_CYCLES=1)
    def test_pause_limit_is_a_setting(self):
        with self.assertRaises(BillingError):
            lifecycle.pause(self.sub, 2)

    def test_payfast_failure_changes_nothing(self):
        with mock.patch("billing.payfast.pause_subscription", side_effect=payfast.GatewayUnavailable("down")):
            with self.assertRaises(payfast.GatewayUnavailable):
                lifecycle.pause(self.sub, 1)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, Subscription.Status.ACTIVE)
        self.assertFalse(self.sub.events.exists())

    def test_resume(self):
        with mock.patch("billing.payfast.pause_subscription"):
            lifecycle.pause(self.sub, 1)
        with mock.patch("billing.payfast.unpause_subscription") as api:
            lifecycle.resume(self.sub)
        api.assert_called_once_with("tok-123")
        self.sub.refresh_from_db()
        self.assertEqual((self.sub.status, self.sub.paused_until), (Subscription.Status.ACTIVE, None))

    def test_cannot_resume_what_is_not_paused(self):
        with self.assertRaises(BillingError):
            lifecycle.resume(self.sub)

    def test_subscription_without_payfast_token_cannot_be_paused(self):
        Subscription.objects.update(payfast_token="")
        self.sub.refresh_from_db()
        with self.assertRaises(BillingError):
            lifecycle.pause(self.sub, 1)

    def test_status_change_signal_sent_with_old_and_new(self):
        seen = []

        def listener(sender, subscription, old, new, **kw):
            seen.append((old, new))

        subscription_status_changed.connect(listener, weak=False)
        self.addCleanup(subscription_status_changed.disconnect, listener)
        with mock.patch("billing.payfast.pause_subscription"), self.captureOnCommitCallbacks(execute=True):
            lifecycle.pause(self.sub, 1)
        self.assertEqual(seen, [("active", "paused")])


class SwitchPeriodTests(TestCase):
    def setUp(self):
        self.plan = priced_plan(annual="15000.00")
        self.sub = live_subscription(plan=self.plan)

    def test_monthly_to_annual_updates_payfast_and_local_amounts(self):
        with mock.patch("billing.payfast.update_subscription") as api:
            lifecycle.switch_period(self.sub, "annual")
        api.assert_called_once()
        kwargs = api.call_args.kwargs
        self.assertEqual(
            (str(kwargs["amount"]), kwargs["frequency"], kwargs["run_date"]),
            ("15000.00", "6", self.sub.next_billing_date),
        )
        self.sub.refresh_from_db()
        self.assertEqual((self.sub.billing_period, str(self.sub.recurring_amount)), ("annual", "15000.00"))
        self.assertEqual(self.sub.events.get().kind, "billing_period_changed")

    def test_annual_back_to_monthly(self):
        sub = live_subscription(plan=self.plan, period="annual")
        with mock.patch("billing.payfast.update_subscription") as api:
            lifecycle.switch_period(sub, "monthly")
        self.assertEqual((str(api.call_args.kwargs["amount"]), api.call_args.kwargs["frequency"]), ("1500.00", "3"))

    def test_rules(self):
        with self.assertRaises(BillingError):
            lifecycle.switch_period(self.sub, "monthly")  # already monthly
        with self.assertRaises(BillingError):
            lifecycle.switch_period(self.sub, "weekly")
        priced_plan(annual=None)
        self.sub.plan.refresh_from_db()
        with self.assertRaises(BillingError):
            lifecycle.switch_period(self.sub, "annual")  # no annual price any more

    def test_needs_a_known_future_billing_date(self):
        Subscription.objects.update(next_billing_date=timezone.localdate() - timedelta(days=1))
        self.sub.refresh_from_db()
        with self.assertRaises(BillingError):
            lifecycle.switch_period(self.sub, "annual")

    def test_payfast_failure_leaves_billing_unchanged(self):
        with mock.patch("billing.payfast.update_subscription", side_effect=payfast.GatewayUnavailable("x")):
            with self.assertRaises(payfast.GatewayUnavailable):
                lifecycle.switch_period(self.sub, "annual")
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.billing_period, "monthly")

    def test_update_call_sends_cents_and_signs_body(self):
        captured = {}

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"status": "success"}'

        def fake(request, timeout):
            captured.update(
                method=request.get_method(),
                data=request.data.decode(),
                headers=dict(request.header_items()),
                url=request.full_url,
            )
            return Resp()

        with mock.patch("billing.payfast.urllib.request.urlopen", fake):
            payfast.update_subscription("tok", amount="15000.00", frequency="6", run_date=timezone.localdate())
        self.assertEqual(captured["method"], "PATCH")
        self.assertIn("amount=1500000", captured["data"])
        self.assertIn("frequency=6", captured["data"])
        self.assertIn("Signature", captured["headers"])


class ClientCancelTests(TestCase):
    def test_client_cancel_is_recorded_as_the_clients_action(self):
        sub = live_subscription()
        with mock.patch("billing.payfast.cancel_subscription") as api:
            lifecycle.cancel(sub, reason="closing the parlour")
        api.assert_called_once_with("tok-123")
        sub.refresh_from_db()
        event = sub.events.get()
        self.assertEqual((sub.status, event.actor, event.detail), ("cancelled", "client", "closing the parlour"))

    def test_nothing_to_cancel_when_pending_or_already_cancelled(self):
        from .helpers import make_subscription

        with self.assertRaises(BillingError):
            lifecycle.cancel(make_subscription())
        with self.assertRaises(BillingError):
            lifecycle.cancel(make_subscription(status=Subscription.Status.CANCELLED))


class RefundTests(TestCase):
    def setUp(self):
        self.sub = live_subscription()
        self.payment = Payment.objects.create(
            subscription=self.sub, pf_payment_id="900", status="COMPLETE", amount_gross=4000
        )

    def test_client_request_records_refund_and_notifies_sales(self):
        with self.captureOnCommitCallbacks(execute=True):
            refund = refunds.request_refund(self.payment, " Changed my mind ")
        self.assertEqual(
            (refund.status, str(refund.amount), refund.reason), ("requested", "4000.00", "Changed my mind")
        )
        self.assertEqual(mail.outbox[0].to, ["sales@example.com"])
        self.assertEqual(self.sub.events.get().kind, "refund_requested")

    def test_client_outside_window_refused_but_staff_may(self):
        Payment.objects.update(received_at=timezone.now() - timedelta(days=8))
        self.payment.refresh_from_db()
        with self.assertRaises(BillingError):
            refunds.request_refund(self.payment, "late")
        refund = refunds.request_refund(self.payment, "goodwill", actor="staff")
        self.assertEqual(refund.requested_by, "staff")

    def test_only_one_open_request_per_payment(self):
        refunds.request_refund(self.payment, "a")
        with self.assertRaises(BillingError):
            refunds.request_refund(self.payment, "b")

    def test_process_calls_payfast_in_cents_marks_refunded_and_emails_client(self):
        refund = refunds.request_refund(self.payment, "a")
        mail.outbox.clear()
        with mock.patch("billing.payfast.refund_payment") as api, self.captureOnCommitCallbacks(execute=True):
            refunds.process(refund)
        api.assert_called_once()
        self.assertEqual(api.call_args.args[0], "900")
        refund.refresh_from_db()
        self.assertEqual(refund.status, Refund.Status.PROCESSED)
        self.assertIsNotNone(refund.processed_at)
        self.assertEqual(str(refunds.refundable_amount(self.payment)), "0.00")
        self.assertEqual(mail.outbox[0].to, ["thandi@example.com"])

    def test_partial_refund_leaves_remainder(self):
        refund = refunds.request_refund(self.payment, "a")
        refund.amount = 1000
        refund.save()
        with mock.patch("billing.payfast.refund_payment"):
            refunds.process(refund)
        self.assertEqual(str(refunds.refundable_amount(self.payment)), "3000.00")

    def test_cannot_refund_more_than_remains_or_twice(self):
        refund = refunds.request_refund(self.payment, "a")
        refund.amount = 5000
        refund.save()
        with self.assertRaises(BillingError):
            refunds.process(refund)
        refund.amount = 4000
        refund.save()
        with mock.patch("billing.payfast.refund_payment"):
            refunds.process(refund)
        with self.assertRaises(BillingError):
            refunds.process(refund)

    def test_payfast_failure_marks_failed_keeps_money_and_can_be_retried(self):
        refund = refunds.request_refund(self.payment, "a")
        with mock.patch("billing.payfast.refund_payment", side_effect=payfast.GatewayUnavailable("no funds")):
            with self.assertRaises(payfast.GatewayUnavailable):
                refunds.process(refund)
        refund.refresh_from_db()
        self.assertEqual(refund.status, Refund.Status.FAILED)
        self.assertIn("no funds", refund.staff_note)
        self.assertEqual(str(refunds.refundable_amount(self.payment)), "4000.00")
        with mock.patch("billing.payfast.refund_payment"):
            refunds.process(refund)
        refund.refresh_from_db()
        self.assertEqual(refund.status, Refund.Status.PROCESSED)

    def test_decline(self):
        refund = refunds.request_refund(self.payment, "a")
        refunds.decline(refund, "outside policy")
        refund.refresh_from_db()
        self.assertEqual((refund.status, refund.staff_note), ("declined", "outside policy"))
        with self.assertRaises(BillingError):
            refunds.decline(refund)

    def test_failed_payment_has_nothing_to_refund(self):
        Payment.objects.update(status="FAILED")
        self.payment.refresh_from_db()
        with self.assertRaises(BillingError):
            refunds.request_refund(self.payment, "a")

    def test_refund_api_call_shape(self):
        captured = {}

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"status": "success"}'

        def fake(request, timeout):
            captured.update(method=request.get_method(), data=request.data.decode(), url=request.full_url)
            return Resp()

        with mock.patch("billing.payfast.urllib.request.urlopen", fake):
            payfast.refund_payment("900", "1500.50", "Changed my mind")
        self.assertEqual(captured["method"], "POST")
        self.assertTrue(captured["url"].endswith("/refunds/900?testing=true"))
        self.assertIn("amount=150050", captured["data"])
