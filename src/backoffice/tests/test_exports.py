import csv
import io
from datetime import UTC, timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from backoffice import exports
from backoffice.models import AuditEntry
from billing.models import Payment, Refund, Subscription
from billing.tests.helpers import live_subscription
from leads.models import Lead
from quotes.tests.helpers import make_lead

from .test_backoffice import StaffTestCase
from .test_roles import make_user


def parse(response, delimiter=","):
    text = response.content.decode("utf-8")
    assert text.startswith("﻿"), "needs a byte-order mark for Excel"
    return list(csv.reader(io.StringIO(text[1:]), delimiter=delimiter))


def url(kind, **params):
    base = reverse("backoffice:export", args=[kind])
    return base + ("?" + "&".join(f"{k}={v}" for k, v in params.items()) if params else "")


class SafeCellTests(TestCase):
    def test_formulas_are_defused_but_phone_numbers_survive(self):
        for dangerous in ("=1+1", "@SUM(A1)", "+cmd|' /C calc'!A0", "-2+3", "\t=1", '=HYPERLINK("http://x")'):
            with self.subTest(dangerous=dangerous):
                self.assertTrue(exports.safe(dangerous).startswith("'"), dangerous)
        for fine in ("+27 82 123 4567", "082 123 4567", "(011) 555-1234", "-5", "Mokoena Funerals", "R 100", "a=b"):
            with self.subTest(fine=fine):
                self.assertEqual(exports.safe(fine), fine)

    def test_types(self):
        self.assertEqual(exports.safe(None), "")
        self.assertEqual(exports.safe(True), "yes")
        self.assertEqual(exports.safe(Decimal("1234.5")), "1234.50")
        self.assertEqual(exports.safe(Decimal("0.005")), "0.01")
        self.assertEqual(exports.safe("line one\r\nline two"), "line one  line two")
        self.assertEqual(exports.safe(timezone.now().date()), timezone.localdate().isoformat())

    def test_decimal_mark_follows_the_separator(self):
        self.assertEqual(exports.safe(Decimal("1234.5"), ","), "1234,50")
        self.assertEqual(exports.safe(33.3, ","), "33,3")
        self.assertEqual(exports.safe(33.3), "33.3")
        self.assertEqual(exports.safe(7, ","), "7")
        self.assertEqual(exports.safe("A.B. Funerals", ","), "A.B. Funerals")  # text is never touched
        self.assertEqual(exports.safe("0.5", ","), "0.5")

    def test_aware_times_are_shown_in_local_time(self):
        from datetime import datetime

        self.assertEqual(exports.safe(datetime(2026, 11, 24, 8, 30, tzinfo=UTC)), "2026-11-24 10:30")  # UTC+2

    def test_vat_part(self):
        with override_settings(VAT_NUMBER="4123456789", VAT_RATE=0.15):
            self.assertEqual(exports.vat_part(Decimal("115.00")), Decimal("15.00"))
            self.assertEqual(exports.vat_part(Decimal("1000.00")), Decimal("130.43"))
        self.assertIsNone(exports.vat_part(Decimal("115")))  # no VAT number in tests


class LeadExportTests(StaffTestCase):
    def test_leads_download(self):
        make_lead(business_name="=cmd|' /C calc'!A0", message="secret message", internal_notes="private note")
        response = self.client.get(url("leads"))
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn('attachment; filename="funera-leads-', response["Content-Disposition"])
        self.assertIn("no-store", response["Cache-Control"])
        rows = parse(response)
        self.assertEqual(rows[0][:4], ["Lead ID", "Received", "Status", "Business"])
        self.assertEqual(len(rows), 2)
        record = dict(zip(rows[0], rows[1], strict=True))
        self.assertTrue(record["Business"].startswith("'="))
        self.assertEqual(record["Phone"], "0821234567")
        self.assertEqual(record["Consent given"], "yes")
        text = response.content.decode()
        self.assertNotIn("secret message", text)
        self.assertNotIn("private note", text)
        self.assertNotIn("visitor", text.lower())

    def test_filters_match_the_list_page(self):
        make_lead(business_name="Alpha Funerals", email="a@example.com")
        make_lead(business_name="Beta Funerals", email="b@example.com", status=Lead.Status.WON)
        self.assertEqual(len(parse(self.client.get(url("leads", q="Alpha")))), 2)
        self.assertEqual(len(parse(self.client.get(url("leads", status="won")))), 2)
        self.assertEqual(len(parse(self.client.get(url("leads", status="bogus")))), 3)  # ignored, as on the list page
        listing = self.client.get(reverse("backoffice:leads"), {"q": "Alpha"}).context["page"]
        self.assertEqual(len(listing), 1)

    def test_date_range(self):
        old = make_lead(business_name="Old Parlour", email="o@example.com")
        Lead.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=40))
        make_lead(business_name="New Parlour", email="n@example.com")
        today = timezone.localdate()
        recent = parse(self.client.get(url("leads", **{"from": (today - timedelta(days=5)).isoformat()})))
        self.assertEqual([r[3] for r in recent[1:]], ["New Parlour"])
        older = parse(self.client.get(url("leads", to=(today - timedelta(days=30)).isoformat())))
        self.assertEqual([r[3] for r in older[1:]], ["Old Parlour"])
        self.assertEqual(len(parse(self.client.get(url("leads", **{"from": "not-a-date"})))), 3)

    def test_semicolon_files_use_a_decimal_comma_and_comma_files_a_point(self):
        sub = live_subscription()
        Payment.objects.create(subscription=sub, pf_payment_id="p1", status="COMPLETE", amount_gross=Decimal("250.50"))
        semi = parse(self.client.get(url("subscriptions", delimiter="semicolon")), ";")
        self.assertEqual(dict(zip(semi[0], semi[1], strict=True))["Paid to date (R)"], "250,50")
        comma = parse(self.client.get(url("subscriptions")))
        self.assertEqual(dict(zip(comma[0], comma[1], strict=True))["Paid to date (R)"], "250.50")

    def test_semicolon_separator(self):
        make_lead()
        rows = parse(self.client.get(url("leads", delimiter="semicolon")), ";")
        self.assertEqual(rows[1][3], "Mokoena Funerals")
        self.assertGreater(len(rows[0]), 10)
        self.assertEqual(parse(self.client.get(url("leads", delimiter="weird")))[0][0], "Lead ID")


class CustomerAndSubscriptionExportTests(StaffTestCase):
    def test_customers(self):
        sub = live_subscription()
        record = dict(zip(*parse(self.client.get(url("customers"))), strict=True))
        self.assertEqual(record["Business"], sub.customer.business_name)
        self.assertEqual(record["Subscriptions"], "1")
        self.assertIn(sub.reference, record["Subscription details"])

    def test_subscriptions_with_paid_to_date(self):
        sub = live_subscription()
        Payment.objects.create(subscription=sub, pf_payment_id="p1", status="COMPLETE", amount_gross=Decimal("250.00"))
        Payment.objects.create(subscription=sub, pf_payment_id="p2", status="FAILED", amount_gross=Decimal("999.00"))
        record = dict(zip(*parse(self.client.get(url("subscriptions"))), strict=True))
        self.assertEqual((record["Reference"], record["Status"]), (sub.reference, "Active"))
        self.assertEqual(record["Paid to date (R)"], "250.00")  # failed payments do not count
        self.assertRegex(record["Next billing date"], r"^\d{4}-\d{2}-\d{2}$")

    def test_status_filter(self):
        live_subscription()
        live_subscription(email="x@example.com")
        Subscription.objects.filter(customer__email="x@example.com").update(status=Subscription.Status.CANCELLED)
        self.assertEqual(len(parse(self.client.get(url("subscriptions", status="cancelled")))), 2)
        self.assertEqual(len(parse(self.client.get(url("subscriptions")))), 3)


class PaymentExportTests(StaffTestCase):
    def make(self, **extra):
        sub = live_subscription()
        return Payment.objects.create(
            subscription=sub, pf_payment_id="pf1", status="COMPLETE", amount_gross=Decimal("115.00"), **extra
        )

    def test_columns_without_a_vat_number(self):
        payment = self.make(amount_fee=Decimal("3.45"))
        Refund.objects.create(payment=payment, amount=Decimal("15.00"), reason="x", status=Refund.Status.PROCESSED)
        Refund.objects.create(payment=payment, amount=Decimal("50.00"), reason="y", status=Refund.Status.DECLINED)
        record = dict(zip(*parse(self.client.get(url("payments"))), strict=True))
        self.assertEqual(record["Amount received (R, incl. VAT)"], "115.00")
        self.assertEqual(record["VAT included (R, at today's rate)"], "")
        self.assertEqual(record["PayFast fee (R)"], "3.45")
        self.assertEqual(record["Net after fee (R)"], "111.55")
        self.assertEqual(record["Refunded (R)"], "15.00")  # only processed refunds

    @override_settings(VAT_NUMBER="4123456789")
    def test_vat_columns_with_a_vat_number(self):
        self.make()
        record = dict(zip(*parse(self.client.get(url("payments"))), strict=True))
        self.assertEqual(record["VAT included (R, at today's rate)"], "15.00")
        self.assertEqual(record["Amount excl. VAT (R)"], "100.00")
        self.assertEqual(record["PayFast fee (R)"], "")

    def test_date_range_uses_received_date(self):
        payment = self.make()
        Payment.objects.filter(pk=payment.pk).update(received_at=timezone.now() - timedelta(days=60))
        today = timezone.localdate()
        self.assertEqual(len(parse(self.client.get(url("payments", **{"from": today.isoformat()})))), 1)
        self.assertEqual(len(parse(self.client.get(url("payments", to=(today - timedelta(days=30)).isoformat())))), 2)


class AccessAndAuditTests(StaffTestCase):
    def test_unknown_dataset_is_404(self):
        self.assertEqual(self.client.get(url("secrets")).status_code, 404)

    def test_download_is_audited_with_row_count_and_filters(self):
        make_lead()
        self.client.get(url("leads", q="Mokoena", status="new"))
        entry = AuditEntry.objects.get(action="export.csv")
        self.assertEqual((entry.object_label, entry.object_type, entry.actor_name), ("Leads", "export", "sam"))
        self.assertIn("1 rows", entry.detail)
        self.assertIn("q=Mokoena", entry.detail)

    def test_exports_page_and_menu(self):
        page = self.client.get(reverse("backoffice:exports"))
        for label in ("Leads", "Customers", "Subscriptions", "Payments", "Semicolon"):
            self.assertContains(page, label)
        self.assertContains(self.client.get(reverse("backoffice:dashboard")), reverse("backoffice:exports"))

    def test_list_pages_offer_a_download_that_keeps_the_filters(self):
        make_lead()
        page = self.client.get(reverse("backoffice:leads"), {"q": "Mokoena", "status": "new"})
        self.assertContains(page, "Download these 1 as CSV")
        self.assertContains(page, "/exports/leads.csv?q=Mokoena")
        for name in ("customers", "subscriptions"):
            self.assertContains(self.client.get(reverse(f"backoffice:{name}")), f"/exports/{name}.csv")


class SalesCannotExportTests(TestCase):
    def test_refused_everywhere(self):
        make_lead()
        sales = self.client_class()
        sales.force_login(make_user("sales"))
        for kind in exports.DATASETS:
            with self.subTest(kind=kind):
                self.assertEqual(sales.get(url(kind)).status_code, 403)
        self.assertEqual(sales.get(reverse("backoffice:exports")).status_code, 403)
        self.assertEqual(AuditEntry.objects.filter(action="export.csv").count(), 0)
        page = sales.get(reverse("backoffice:leads"))
        self.assertNotContains(page, "as CSV")
        self.assertNotContains(page, "/exports/")

    def test_anonymous_goes_to_login(self):
        for kind in exports.DATASETS:
            self.assertEqual(self.client.get(url(kind)).status_code, 302)
