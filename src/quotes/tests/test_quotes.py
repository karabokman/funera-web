import smtplib
from decimal import Decimal
from unittest import mock

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing.models import Customer, Subscription
from leads.models import Lead
from quotes import services
from quotes.errors import QuoteError
from quotes.models import Quote

from .helpers import make_lead, make_quote


class TotalsTests(TestCase):
    def test_price_is_what_the_client_pays_when_no_vat(self):
        q = make_quote(lines=(("Set-up", 1, "2500.00"), ("Training", 2, "400.00")), vat_included=False)
        self.assertEqual(
            (q.once_off_gross, q.recurring_gross, q.first_payment_gross),
            (Decimal("3300.00"), Decimal("1500.00"), Decimal("4800.00")),
        )
        self.assertEqual((q.total_vat, q.has_vat), (Decimal("0.00"), False))

    def test_vat_is_the_portion_inside_the_entered_price(self):
        q = make_quote(price="1150.00", lines=(("Set-up", 1, "2300.00"),), vat="0.15")
        self.assertEqual(
            (q.recurring_vat, q.recurring_net, q.recurring_gross),
            (Decimal("150.00"), Decimal("1000.00"), Decimal("1150.00")),
        )
        self.assertEqual((q.once_off_vat, q.once_off_net), (Decimal("300.00"), Decimal("2000.00")))
        self.assertEqual((q.first_payment_gross, q.total_vat, q.has_vat), (Decimal("3450.00"), Decimal("450.00"), True))

    def test_ticking_includes_vat_never_changes_what_is_paid(self):
        with_vat = make_quote(price="1499.00", vat="0.15", vat_included=True)
        without = make_quote(price="1499.00", vat="0.15", vat_included=False)
        self.assertEqual(with_vat.first_payment_gross, without.first_payment_gross)
        self.assertEqual(without.recurring_vat, Decimal("0.00"))

    def test_discount_line_reduces_the_total(self):
        q = make_quote(lines=(("Set-up", 1, "2500.00"), ("Launch discount", 1, "-500.00")))
        self.assertEqual(q.once_off_gross, Decimal("2000.00"))

    def test_annual_monthly_equivalent(self):
        q = make_quote(price="14990.00", billing_period="annual")
        self.assertEqual(q.monthly_equivalent, Decimal("1249.17"))

    def test_vat_rate_is_a_snapshot(self):
        q = make_quote(vat="0.15")
        with override_settings(VAT_RATE=0.20):
            q.refresh_from_db()
            self.assertEqual(q.recurring_vat, Decimal("195.65"))


class NumberingAndCreationTests(TestCase):
    def test_numbers_are_sequential_per_year_and_unique(self):
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.create_user("s", "s@example.com", "pw", is_staff=True)
        numbers = []
        plan = make_quote().plan
        for _ in range(3):
            quote = Quote(
                contact_name="a",
                business_name="b",
                email="a@b.co",
                phone="0821234567",
                plan=plan,
                recurring_price=1,
                valid_until=timezone.localdate(),
            )
            numbers.append(services.save_new(quote, user).number)
        year = timezone.localdate().year
        self.assertEqual(numbers[1], f"QT-{year}-{int(numbers[0][-4:]) + 1:04d}")
        self.assertEqual(len(set(numbers)), 3)

    def test_initial_for_prefills_from_plan_and_lead(self):
        lead = make_lead()
        plan = make_quote().plan
        plan.monthly_price, plan.setup_fee = Decimal("1500"), Decimal("3500")
        initial, lines = services.initial_for(plan, lead)
        self.assertEqual(
            (initial["recurring_price"], initial["business_name"], initial["email"]),
            (Decimal("1500"), "Mokoena Funerals", "thandi@example.com"),
        )
        self.assertEqual(lines[0]["unit_price"], Decimal("3500"))

    def test_initial_for_without_prices_leaves_them_blank(self):
        plan = make_quote().plan
        plan.monthly_price = plan.setup_fee = None
        initial, lines = services.initial_for(plan)
        self.assertNotIn("recurring_price", initial)
        self.assertEqual(lines, [])


class SendTests(TestCase):
    def test_send_emails_link_marks_sent_and_advances_lead(self):
        lead = make_lead()
        q = make_quote(lead=lead)
        with self.captureOnCommitCallbacks(execute=True):
            services.send(q)
        q.refresh_from_db()
        lead.refresh_from_db()
        self.assertEqual((q.status, lead.status), ("sent", Lead.Status.QUOTED))
        self.assertIn(f"/quote/{q.token}/", mail.outbox[0].body)
        self.assertEqual(mail.outbox[0].to, ["thandi@example.com"])
        self.assertEqual(q.events.first().kind, "sent")

    def test_resend_is_allowed_and_recorded(self):
        q = make_quote(status=Quote.Status.SENT)
        services.send(q)
        self.assertEqual(q.events.first().kind, "resent")

    def test_cannot_send_without_price_after_expiry_or_when_decided(self):
        with self.assertRaises(QuoteError):
            services.send(make_quote(price="0"))
        with self.assertRaises(QuoteError):
            services.send(make_quote(valid_days=-1))
        with self.assertRaises(QuoteError):
            services.send(make_quote(status=Quote.Status.ACCEPTED))
        with self.assertRaises(QuoteError):
            services.send(make_quote(price="1", lines=()))  # below PayFast's R5 minimum

    def test_email_failure_leaves_quote_unsent_and_says_why(self):
        lead = make_lead()
        q = make_quote(lead=lead)
        with mock.patch(
            "quotes.services._deliver", side_effect=smtplib.SMTPServerDisconnected("please run connect() first")
        ):
            with self.assertRaises(QuoteError) as ctx:
                services.send(q)
        self.assertIn("not marked as sent", str(ctx.exception))
        q.refresh_from_db()
        lead.refresh_from_db()
        self.assertEqual((q.status, q.sent_at, lead.status), ("draft", None, Lead.Status.NEW))
        self.assertFalse(q.events.filter(kind="sent").exists())

    def test_email_carries_the_pdf(self):
        services.send(make_quote())
        name, content, mimetype = mail.outbox[0].attachments[0]
        self.assertEqual((mimetype, content[:5]), ("application/pdf", b"%PDF-"))
        self.assertTrue(name.endswith(".pdf"))

    def test_lead_already_won_is_not_moved_back(self):
        lead = make_lead(status=Lead.Status.WON)
        services.send(make_quote(lead=lead))
        lead.refresh_from_db()
        self.assertEqual(lead.status, Lead.Status.WON)


class AcceptDeclineTests(TestCase):
    def test_accept_creates_customer_and_pending_subscription_at_quoted_gross_prices(self):
        lead = make_lead(status=Lead.Status.QUOTED)
        q = make_quote(
            status=Quote.Status.SENT,
            lead=lead,
            vat="0.15",
            billing_period="annual",
            price="14990.00",
            lines=(("Set-up", 1, "2500.00"),),
        )
        with self.captureOnCommitCallbacks(execute=True):
            sub = services.accept(q, name="Thandi Mokoena")
        q.refresh_from_db()
        lead.refresh_from_db()
        self.assertEqual((q.status, q.decision_by, lead.status), ("accepted", "Thandi Mokoena", Lead.Status.WON))
        self.assertEqual(
            (sub.status, sub.billing_period, sub.plan.code), (Subscription.Status.PENDING, "annual", "professional")
        )
        self.assertEqual((sub.recurring_amount, sub.initial_amount), (Decimal("14990.00"), Decimal("17490.00")))
        self.assertEqual(q.subscription, sub)
        customer = sub.customer
        self.assertEqual((customer.email, customer.lead), ("thandi@example.com", lead))
        self.assertTrue(customer.terms_accepted_at)
        self.assertEqual(sorted(m.to[0] for m in mail.outbox), ["sales@example.com", "thandi@example.com"])

    def test_accepting_twice_creates_nothing_new(self):
        q = make_quote(status=Quote.Status.SENT)
        first = services.accept(q, name="A")
        again = services.accept(q, name="A")
        self.assertEqual(first.pk, again.pk)
        self.assertEqual((Customer.objects.count(), Subscription.objects.count()), (1, 1))

    def test_cannot_accept_draft_expired_declined_or_cancelled(self):
        for status, days in (
            (Quote.Status.DRAFT, 30),
            (Quote.Status.SENT, -1),
            (Quote.Status.DECLINED, 30),
            (Quote.Status.CANCELLED, 30),
        ):
            with self.subTest(status=status, days=days), self.assertRaises(QuoteError):
                services.accept(make_quote(status=status, valid_days=days), name="A")
        self.assertFalse(Subscription.objects.exists())

    def test_decline_records_reason_and_marks_lead_lost(self):
        lead = make_lead(status=Lead.Status.QUOTED)
        q = make_quote(status=Quote.Status.SENT, lead=lead)
        with self.captureOnCommitCallbacks(execute=True):
            services.decline(q, reason="Too expensive")
        q.refresh_from_db()
        lead.refresh_from_db()
        self.assertEqual((q.status, q.decision_note, lead.status), ("declined", "Too expensive", Lead.Status.LOST))
        self.assertEqual(mail.outbox[0].to, ["sales@example.com"])

    def test_cancel_and_duplicate(self):
        q = make_quote(status=Quote.Status.SENT)
        services.cancel(q)
        self.assertEqual(q.status, "cancelled")
        with self.assertRaises(QuoteError):
            services.cancel(q)
        copy = services.duplicate(q)
        self.assertEqual((copy.status, copy.lines.count(), copy.recurring_price), ("draft", 1, q.recurring_price))
        self.assertNotEqual((copy.number, copy.token), (q.number, q.token))
        self.assertGreater(copy.valid_until, timezone.localdate())

    def test_is_expired_flag(self):
        self.assertTrue(make_quote(status=Quote.Status.SENT, valid_days=-1).is_expired)
        self.assertFalse(make_quote(status=Quote.Status.SENT).is_expired)
        self.assertFalse(make_quote(status=Quote.Status.DRAFT, valid_days=-1).is_expired)


class PublicPageTests(TestCase):
    def setUp(self):
        self.q = make_quote(status=Quote.Status.SENT)
        self.url = reverse("quotes:public", args=[self.q.token])

    def test_page_shows_quote_and_is_private(self):
        response = self.client.get(self.url)
        self.assertContains(response, self.q.number)
        self.assertContains(response, "Accept this quotation")
        self.assertContains(response, 'content="noindex, nofollow"')
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(self.q.events.filter(kind="viewed").count(), 1)
        self.client.get(self.url)
        self.assertEqual(self.q.events.filter(kind="viewed").count(), 1)

    def test_unknown_token_and_draft_are_404(self):
        self.assertEqual(self.client.get(reverse("quotes:public", args=["nope"])).status_code, 404)
        draft = make_quote()
        self.assertEqual(self.client.get(reverse("quotes:public", args=[draft.token])).status_code, 404)

    def test_accept_needs_name_and_tick(self):
        accept = reverse("quotes:accept", args=[self.q.token])
        self.client.post(accept, {"name": "", "accept": "on"})
        self.client.post(accept, {"name": "Thandi"})
        self.q.refresh_from_db()
        self.assertEqual(self.q.status, "sent")

    def test_accept_flow_then_payment_link(self):
        response = self.client.post(
            reverse("quotes:accept", args=[self.q.token]), {"name": "Thandi Mokoena", "accept": "on"}, follow=True
        )
        self.assertContains(response, "The quotation is accepted")
        self.q.refresh_from_db()
        self.assertContains(response, f"/subscribe/pay/{self.q.subscription.reference}/")
        self.assertEqual(self.client.get(f"/subscribe/pay/{self.q.subscription.reference}/").status_code, 200)

    def test_pay_page_charges_the_quoted_amounts(self):
        self.client.post(reverse("quotes:accept", args=[self.q.token]), {"name": "T", "accept": "on"})
        self.q.refresh_from_db()
        html = self.client.get(f"/subscribe/pay/{self.q.subscription.reference}/").content.decode()
        self.assertIn('name="amount" value="4000.00"', html)
        self.assertIn('name="recurring_amount" value="1500.00"', html)

    def test_decline_flow(self):
        response = self.client.post(reverse("quotes:decline", args=[self.q.token]), {"reason": "No"}, follow=True)
        self.assertContains(response, "declined")
        self.q.refresh_from_db()
        self.assertEqual(self.q.status, "declined")

    def test_expired_and_cancelled_show_notice_and_cannot_be_actioned(self):
        expired = make_quote(status=Quote.Status.SENT, valid_days=-2)
        page = self.client.get(reverse("quotes:public", args=[expired.token]))
        self.assertContains(page, "expired")
        self.assertNotContains(page, "Accept this quotation")
        response = self.client.post(
            reverse("quotes:accept", args=[expired.token]), {"name": "T", "accept": "on"}, follow=True
        )
        self.assertContains(response, "can no longer be accepted")
        self.assertFalse(Subscription.objects.exists())
        cancelled = make_quote(status=Quote.Status.CANCELLED)
        self.assertContains(self.client.get(reverse("quotes:public", args=[cancelled.token])), "withdrawn")

    def test_actions_are_post_only(self):
        self.assertEqual(self.client.get(reverse("quotes:accept", args=[self.q.token])).status_code, 405)

    def test_vat_shown_only_when_charged(self):
        no_vat = make_quote(status=Quote.Status.SENT, vat="0.15", vat_included=False)
        self.assertContains(self.client.get(reverse("quotes:public", args=[no_vat.token])), "No VAT is charged")
        vat = make_quote(status=Quote.Status.SENT, vat="0.15")
        self.assertContains(self.client.get(reverse("quotes:public", args=[vat.token])), "VAT included")

    def test_pdf_download_by_token(self):
        response = self.client.get(reverse("quotes:pdf", args=[self.q.token]))
        self.assertEqual((response.status_code, response["Content-Type"]), (200, "application/pdf"))
        self.assertTrue(response.content.startswith(b"%PDF-"))
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(self.client.get(reverse("quotes:pdf", args=["nope"])).status_code, 404)
        self.assertEqual(self.client.get(reverse("quotes:pdf", args=[make_quote().token])).status_code, 404)  # a draft

    def test_pdf_builds_with_vat_discounts_and_odd_characters(self):
        from quotes import pdf

        q = make_quote(
            vat="0.15",
            billing_period="annual",
            intro="Hi <b>&\nSecond line",
            lines=(("Set-up & <data>", 2, "100.00"), ("Discount", 1, "-50.00")),
        )
        self.assertTrue(pdf.render(q).startswith(b"%PDF-"))
        self.assertTrue(pdf.render(make_quote(vat_included=False, lines=())).startswith(b"%PDF-"))
