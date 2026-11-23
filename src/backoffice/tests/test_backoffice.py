from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from billing import payfast
from billing.models import Payment, Refund, Subscription
from billing.tests.helpers import live_subscription, make_subscription, priced_plan
from catalog.models import Plan
from leads.models import Lead
from quotes.models import Quote
from quotes.tests.helpers import make_lead, make_quote

User = get_user_model()


class StaffTestCase(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("sam", "sam@example.com", "pw-12345-long", is_staff=True)
        self.client.force_login(self.staff)


class AccessTests(TestCase):
    URLS = ("dashboard", "leads", "quotes", "quote_new", "customers", "subscriptions", "refunds")

    def test_anonymous_is_sent_to_staff_login(self):
        for name in self.URLS:
            with self.subTest(name=name):
                response = self.client.get(reverse(f"backoffice:{name}"))
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response["Location"].startswith("/staff/login/?next="))

    def test_signed_in_non_staff_is_refused(self):
        self.client.force_login(User.objects.create_user("client", "c@example.com", "pw-12345-long"))
        for name in self.URLS:
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(f"backoffice:{name}")).status_code, 403)

    def test_inactive_staff_refused(self):
        user = User.objects.create_user("gone", "g@example.com", "pw-12345-long", is_staff=True)
        self.client.force_login(user)
        User.objects.filter(pk=user.pk).update(is_active=False)
        self.assertNotEqual(self.client.get(reverse("backoffice:dashboard")).status_code, 200)

    def test_actions_refuse_anonymous_and_non_staff_posts(self):
        q = make_quote()
        url = reverse("backoffice:quote_send", args=[q.pk])
        self.assertEqual(self.client.post(url).status_code, 302)
        self.client.force_login(User.objects.create_user("c2", "c2@example.com", "pw-12345-long"))
        self.assertEqual(self.client.post(url).status_code, 403)
        q.refresh_from_db()
        self.assertEqual(q.status, "draft")

    def test_login_logout_flow(self):
        response = (
            self.client.post(reverse("backoffice:login"), {"username": "sam", "password": "pw-12345-long"})
            if User.objects.create_user("sam", "s@example.com", "pw-12345-long", is_staff=True)
            else None
        )
        self.assertRedirects(response, "/staff/", fetch_redirect_response=False)
        self.client.post(reverse("backoffice:logout"))
        self.assertEqual(self.client.get(reverse("backoffice:dashboard")).status_code, 302)

    def test_bad_login_shows_error(self):
        User.objects.create_user("sam", "s@example.com", "pw-12345-long", is_staff=True)
        response = self.client.post(reverse("backoffice:login"), {"username": "sam", "password": "wrong"})
        self.assertContains(response, "Wrong username or password")
        self.assertEqual(self.client.get(reverse("backoffice:dashboard")).status_code, 302)

    def test_pages_are_noindex_never_cached_and_robots_hides_them(self):
        self.client.force_login(User.objects.create_user("s2", "s2@example.com", "pw-12345-long", is_staff=True))
        response = self.client.get(reverse("backoffice:dashboard"))
        self.assertContains(response, 'content="noindex, nofollow"')
        self.assertIn("no-store", response["Cache-Control"])
        self.assertNotIn("/staff/", self.client.get(reverse("pages:robots")).content.decode())  # never advertised

    def test_staff_address_comes_from_the_setting(self):
        from django.conf import settings

        self.assertEqual(reverse("backoffice:dashboard"), "/" + settings.STAFF_URL)
        self.assertEqual(settings.STAFF_LOGIN_URL, reverse("backoffice:login"))

    def test_staff_notification_links_use_the_configured_address(self):
        from django.core import mail as m

        from quotes import services

        q = make_quote(status=Quote.Status.SENT)
        with self.captureOnCommitCallbacks(execute=True):
            services.accept(q, name="T")
        staff_mail = next(x for x in m.outbox if x.to == ["sales@example.com"])
        self.assertIn(
            f"/{__import__('django.conf', fromlist=['settings']).settings.STAFF_URL}quotes/{q.pk}/", staff_mail.body
        )

    def test_next_redirect_after_login_stays_on_site(self):
        User.objects.create_user("sam", "s@example.com", "pw-12345-long", is_staff=True)
        response = self.client.post(
            reverse("backoffice:login") + "?next=https://evil.example/",
            {"username": "sam", "password": "pw-12345-long", "next": "https://evil.example/"},
        )
        self.assertRedirects(response, "/staff/", fetch_redirect_response=False)


class DashboardTests(StaffTestCase):
    def test_numbers(self):
        make_lead()
        make_lead(email="b@example.com", status=Lead.Status.CONTACTED)
        make_quote(status=Quote.Status.SENT)
        make_quote(status=Quote.Status.SENT, price="14990.00", billing_period="annual", valid_days=3)
        make_quote()
        live_subscription()
        live_subscription(period="annual", plan=priced_plan(annual="15000.00"), email="x@example.com")
        Refund.objects.create(
            payment=Payment.objects.create(
                subscription=Subscription.objects.first(), pf_payment_id="1", status="COMPLETE", amount_gross=10
            ),
            amount=10,
            reason="x",
        )
        stats = self.client.get(reverse("backoffice:dashboard")).context["stats"]
        self.assertEqual(
            (stats["new_leads"], stats["open_quotes"], stats["draft_quotes"], stats["refunds_waiting"]), (1, 2, 1, 1)
        )
        self.assertEqual(len(stats["expiring_quotes"]), 1)
        self.assertEqual(stats["mrr"], Decimal("1500.00") + Decimal("15000.00") / 12)
        self.assertEqual(stats["subscriptions"]["Active"], 2)

    def test_empty_database_renders(self):
        self.assertContains(self.client.get(reverse("backoffice:dashboard")), "Nothing needs attention")

    def test_expired_quote_not_counted_as_open(self):
        make_quote(status=Quote.Status.SENT, valid_days=-1)
        self.assertEqual(self.client.get(reverse("backoffice:dashboard")).context["stats"]["open_quotes"], 0)

    def test_cancelled_and_pending_do_not_count_toward_mrr(self):
        live_subscription(status=Subscription.Status.CANCELLED) if False else None
        make_subscription(status=Subscription.Status.CANCELLED)
        make_subscription(status=Subscription.Status.PENDING)
        self.assertEqual(self.client.get(reverse("backoffice:dashboard")).context["stats"]["mrr"], Decimal("0.00"))


class LeadTests(StaffTestCase):
    def test_list_filter_and_search(self):
        make_lead()
        make_lead(business_name="Zulu Burial Society", email="z@example.com", status=Lead.Status.WON)
        page = self.client.get(reverse("backoffice:leads"), {"status": "won"}).context["page"]
        self.assertEqual([lead.business_name for lead in page], ["Zulu Burial Society"])
        page = self.client.get(reverse("backoffice:leads"), {"q": "zulu"}).context["page"]
        self.assertEqual(len(page), 1)
        self.assertEqual(len(self.client.get(reverse("backoffice:leads"), {"status": "bogus"}).context["page"]), 2)

    def test_update_status_and_notes_sets_contacted_at_once(self):
        lead = make_lead()
        url = reverse("backoffice:lead", args=[lead.pk])
        self.client.post(url, {"status": "contacted", "internal_notes": "Called, wants a quote"})
        lead.refresh_from_db()
        first = lead.contacted_at
        self.assertEqual((lead.status, lead.internal_notes), ("contacted", "Called, wants a quote"))
        self.assertIsNotNone(first)
        self.client.post(url, {"status": "demo", "internal_notes": "x"})
        lead.refresh_from_db()
        self.assertEqual(lead.contacted_at, first)

    def test_detail_offers_a_quote_per_plan(self):
        lead = make_lead()
        html = self.client.get(reverse("backoffice:lead", args=[lead.pk])).content.decode()
        for plan in Plan.objects.filter(is_active=True):
            self.assertIn(f"plan={plan.code}", html)

    def test_pagination(self):
        for i in range(30):
            make_lead(email=f"l{i}@example.com")
        self.assertEqual(len(self.client.get(reverse("backoffice:leads")).context["page"]), 25)
        self.assertEqual(len(self.client.get(reverse("backoffice:leads"), {"page": 2}).context["page"]), 5)
        self.assertEqual(len(self.client.get(reverse("backoffice:leads"), {"page": 99}).context["page"]), 5)


def quote_post(**extra):
    data = {
        "contact_name": "Thandi Mokoena", "business_name": "Mokoena Funerals", "email": "thandi@example.com", "phone": "082 123 4567",
        "plan": Plan.objects.get(code="professional").pk, "billing_period": "monthly", "recurring_price": "1499.00",
        "valid_until": (timezone.localdate() + timedelta(days=14)).isoformat(), "intro": "Hello", "internal_notes": "",
        "lines-TOTAL_FORMS": "2", "lines-INITIAL_FORMS": "0", "lines-MIN_NUM_FORMS": "0", "lines-MAX_NUM_FORMS": "1000",
        "lines-0-description": "Set-up", "lines-0-quantity": "1", "lines-0-unit_price": "3500.00",
        "lines-1-description": "", "lines-1-quantity": "1", "lines-1-unit_price": "",
    }  # fmt: skip
    data.update(extra)
    return data


class QuoteWorkflowTests(StaffTestCase):
    def test_new_quote_from_lead_is_prefilled(self):
        lead = make_lead()
        response = self.client.get(reverse("backoffice:quote_new"), {"lead": lead.pk, "plan": "starter"})
        form = response.context["form"]
        self.assertEqual((form.initial["business_name"], form.initial["plan"].code), ("Mokoena Funerals", "starter"))

    def test_create_saves_quote_lines_creator_and_links_lead(self):
        lead = make_lead()
        response = self.client.post(reverse("backoffice:quote_new") + f"?lead={lead.pk}", quote_post())
        quote = Quote.objects.get()
        self.assertRedirects(response, reverse("backoffice:quote", args=[quote.pk]))
        self.assertEqual(
            (quote.status, quote.created_by, quote.lead, quote.lines.count()), ("draft", self.staff, lead, 1)
        )
        self.assertTrue(quote.number.startswith("QT-"))
        self.assertEqual(quote.first_payment_gross, Decimal("4999.00"))

    def test_vat_checkbox_defaults_on_and_is_saved_both_ways(self):
        self.assertTrue(self.client.get(reverse("backoffice:quote_new")).context["form"]["vat_included"].value())
        self.client.post(reverse("backoffice:quote_new"), quote_post(vat_included="on"))
        self.client.post(reverse("backoffice:quote_new"), quote_post())
        on, off = Quote.objects.order_by("id")
        self.assertEqual((on.vat_included, off.vat_included), (True, False))
        self.assertEqual(
            (on.vat_rate, on.recurring_vat), (Decimal("0.150"), Decimal("195.52"))
        )  # R1 499.00 includes R195.52 VAT
        self.assertEqual(off.recurring_vat, Decimal("0.00"))

    def test_pdf_for_staff_including_drafts(self):
        q = make_quote()
        response = self.client.get(reverse("backoffice:quote_pdf", args=[q.pk]))
        self.assertEqual((response.status_code, response["Content-Type"]), (200, "application/pdf"))
        self.client.logout()
        self.assertEqual(self.client.get(reverse("backoffice:quote_pdf", args=[q.pk])).status_code, 302)

    def test_failed_email_is_reported_and_quote_stays_draft(self):
        q = make_quote()
        with mock.patch("quotes.services._deliver", side_effect=OSError("no route")):
            response = self.client.post(reverse("backoffice:quote_send", args=[q.pk]), follow=True)
        self.assertContains(response, "could not be sent")
        q.refresh_from_db()
        self.assertEqual(q.status, "draft")

    def test_create_without_a_lead(self):
        self.client.post(reverse("backoffice:quote_new"), quote_post())
        self.assertIsNone(Quote.objects.get().lead)

    def test_invalid_form_rerenders_with_errors_and_saves_nothing(self):
        response = self.client.post(
            reverse("backoffice:quote_new"), quote_post(email="bad", recurring_price="-5", phone="12")
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Quote.objects.exists())
        self.assertContains(response, "Please correct the errors")

    def test_edit_draft_changes_lines_and_price(self):
        q = make_quote()
        line = q.lines.get()
        data = quote_post(
            recurring_price="1200.00", **{"lines-TOTAL_FORMS": "2", "lines-INITIAL_FORMS": "1", "lines-0-id": line.pk, "lines-0-quote": q.pk, "lines-0-unit_price": "1000.00", "lines-0-description": "Set-up"},
        )  # fmt: skip
        self.client.post(reverse("backoffice:quote_edit", args=[q.pk]), data)
        q.refresh_from_db()
        self.assertEqual((q.recurring_price, q.once_off_net), (Decimal("1200.00"), Decimal("1000.00")))

    def test_sent_quote_cannot_be_edited(self):
        q = make_quote(status=Quote.Status.SENT)
        response = self.client.get(reverse("backoffice:quote_edit", args=[q.pk]), follow=True)
        self.assertContains(response, "Only a draft can be edited")
        self.client.post(reverse("backoffice:quote_edit", args=[q.pk]), quote_post(recurring_price="1"))
        q.refresh_from_db()
        self.assertEqual(q.recurring_price, Decimal("1500.00"))

    def test_send_cancel_duplicate_buttons(self):
        q = make_quote()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("backoffice:quote_send", args=[q.pk]), follow=True)
        self.assertContains(response, "e-mailed to the client")
        self.assertEqual(len(mail.outbox), 1)
        self.assertContains(response, f"/quote/{q.token}/")  # the link staff can copy
        copy_response = self.client.post(reverse("backoffice:quote_duplicate", args=[q.pk]))
        self.assertEqual(Quote.objects.count(), 2)
        self.assertEqual(copy_response.status_code, 302)
        self.client.post(reverse("backoffice:quote_cancel", args=[q.pk]))
        q.refresh_from_db()
        self.assertEqual(q.status, "cancelled")

    def test_send_error_is_shown_not_raised(self):
        q = make_quote(price="0")
        response = self.client.post(reverse("backoffice:quote_send", args=[q.pk]), follow=True)
        self.assertContains(response, "Set the recurring price")
        self.assertEqual(mail.outbox, [])

    def test_list_filter_and_expired_label(self):
        make_quote(status=Quote.Status.SENT, valid_days=-1)
        make_quote()
        page = self.client.get(reverse("backoffice:quotes"), {"status": "sent"}).context["page"]
        self.assertEqual(len(page), 1)
        self.assertContains(self.client.get(reverse("backoffice:quotes")), "Expired")

    def test_detail_shows_document_history_and_client_link(self):
        q = make_quote(status=Quote.Status.SENT)
        html = self.client.get(reverse("backoffice:quote", args=[q.pk])).content.decode()
        self.assertIn(q.number, html)
        self.assertIn(q.token, html)

    def test_full_journey_lead_to_pending_subscription(self):
        lead = make_lead()
        self.client.post(reverse("backoffice:quote_new") + f"?lead={lead.pk}", quote_post())
        quote = Quote.objects.get()
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("backoffice:quote_send", args=[quote.pk]))
        anonymous = self.client_class()
        anonymous.post(reverse("quotes:accept", args=[quote.token]), {"name": "Thandi", "accept": "on"})
        lead.refresh_from_db()
        quote.refresh_from_db()
        self.assertEqual(
            (lead.status, quote.status, quote.subscription.status), (Lead.Status.WON, "accepted", "pending")
        )
        self.assertContains(
            self.client.get(reverse("backoffice:subscription", args=[quote.subscription.pk])), quote.number
        )


class CustomerAndSubscriptionTests(StaffTestCase):
    def test_customer_list_search_and_detail(self):
        sub = live_subscription()
        self.assertEqual(len(self.client.get(reverse("backoffice:customers"), {"q": "mokoena"}).context["page"]), 1)
        self.assertEqual(len(self.client.get(reverse("backoffice:customers"), {"q": "zzz"}).context["page"]), 0)
        self.assertContains(self.client.get(reverse("backoffice:customer", args=[sub.customer.pk])), sub.reference)

    def test_subscription_list_filters(self):
        live_subscription()
        make_subscription()
        self.assertEqual(
            len(self.client.get(reverse("backoffice:subscriptions"), {"status": "active"}).context["page"]), 1
        )
        self.assertEqual(len(self.client.get(reverse("backoffice:subscriptions"), {"q": "FN-"}).context["page"]), 2)

    def test_pause_resume_cancel_record_staff_as_actor(self):
        sub = live_subscription()
        with (
            mock.patch("billing.payfast.pause_subscription"),
            mock.patch("billing.payfast.unpause_subscription"),
            mock.patch("billing.payfast.cancel_subscription"),
        ):
            self.client.post(reverse("backoffice:subscription_pause", args=[sub.pk]), {"cycles": 2})
            sub.refresh_from_db()
            self.assertEqual(sub.status, "paused")
            self.client.post(reverse("backoffice:subscription_resume", args=[sub.pk]))
            self.client.post(reverse("backoffice:subscription_cancel", args=[sub.pk]), {"reason": "Closed"})
        sub.refresh_from_db()
        self.assertEqual(sub.status, "cancelled")
        self.assertEqual({e.actor for e in sub.events.all()}, {"staff"})

    def test_payfast_failure_is_reported_and_nothing_changes(self):
        sub = live_subscription()
        with mock.patch("billing.payfast.cancel_subscription", side_effect=payfast.GatewayUnavailable("down")):
            response = self.client.post(reverse("backoffice:subscription_cancel", args=[sub.pk]), follow=True)
        self.assertContains(response, "nothing was changed")
        sub.refresh_from_db()
        self.assertEqual(sub.status, "active")

    def test_pause_validation(self):
        sub = live_subscription()
        with mock.patch("billing.payfast.pause_subscription") as api:
            response = self.client.post(
                reverse("backoffice:subscription_pause", args=[sub.pk]), {"cycles": 9}, follow=True
            )
        api.assert_not_called()
        self.assertContains(response, "Choose how long to pause for")

    def test_actions_are_post_only(self):
        sub = live_subscription()
        for name in ("subscription_pause", "subscription_resume", "subscription_cancel", "subscription_refund"):
            self.assertEqual(self.client.get(reverse(f"backoffice:{name}", args=[sub.pk])).status_code, 405)


class RefundTests(StaffTestCase):
    def setUp(self):
        super().setUp()
        self.sub = live_subscription()
        self.payment = Payment.objects.create(
            subscription=self.sub, pf_payment_id="900", status="COMPLETE", amount_gross=4000
        )

    def test_raise_then_process(self):
        self.client.post(
            reverse("backoffice:subscription_refund", args=[self.sub.pk]),
            {"payment": self.payment.pk, "reason": "Goodwill"},
        )
        refund = Refund.objects.get()
        self.assertEqual((refund.status, refund.requested_by), ("requested", "staff"))
        self.assertContains(self.client.get(reverse("backoffice:refunds")), "Goodwill")
        with mock.patch("billing.payfast.refund_payment") as api, self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("backoffice:refund_action", args=[refund.pk, "process"]), follow=True)
        api.assert_called_once()
        self.assertContains(response, "Refunded through PayFast")
        refund.refresh_from_db()
        self.assertEqual(refund.status, "processed")
        self.assertEqual(mail.outbox[-1].to, ["thandi@example.com"])

    def test_failure_marks_failed_and_can_retry(self):
        refund = Refund.objects.create(payment=self.payment, amount=4000, reason="x")
        with mock.patch("billing.payfast.refund_payment", side_effect=payfast.GatewayUnavailable("no")):
            response = self.client.post(reverse("backoffice:refund_action", args=[refund.pk, "process"]), follow=True)
        self.assertContains(response, "no money was returned")
        refund.refresh_from_db()
        self.assertEqual(refund.status, "failed")
        self.assertContains(self.client.get(reverse("backoffice:refunds"), {"status": "failed"}), "Retry refund")

    def test_decline_and_unknown_action(self):
        refund = Refund.objects.create(payment=self.payment, amount=4000, reason="x")
        self.client.post(reverse("backoffice:refund_action", args=[refund.pk, "bogus"]))
        refund.refresh_from_db()
        self.assertEqual(refund.status, "requested")
        self.client.post(reverse("backoffice:refund_action", args=[refund.pk, "decline"]), {"note": "Outside policy"})
        refund.refresh_from_db()
        self.assertEqual((refund.status, refund.staff_note), ("declined", "Outside policy"))

    def test_cannot_raise_refund_on_another_subscriptions_payment(self):
        other = live_subscription(email="o@example.com")
        response = self.client.post(
            reverse("backoffice:subscription_refund", args=[other.pk]), {"payment": self.payment.pk, "reason": "x"}
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(Refund.objects.exists())
