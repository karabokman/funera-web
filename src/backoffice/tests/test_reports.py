import csv
import io
from datetime import UTC, date, datetime
from decimal import Decimal

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from backoffice import reports
from backoffice.models import AuditEntry
from billing.models import Customer, Payment, Refund, Subscription
from billing.tests.helpers import priced_plan
from instances.models import Instance
from leads.models import Lead
from quotes.models import Quote
from quotes.tests.helpers import make_lead

from .test_backoffice import StaffTestCase
from .test_roles import make_user

PERIOD = reports.Period(date(2026, 1, 1), date(2026, 6, 30))
FAR = date(2099, 1, 1)  # a quotation that can never have expired
PAST = date(2026, 1, 1)  # one that always has


def at(month, day, year=2026, hour=10):
    return timezone.make_aware(datetime(year, month, day, hour, 0))


def lead(name, when, status, utm=""):
    obj = make_lead(
        name=name, business_name=f"{name} Funerals", email=f"{name.lower()}@example.com", status=status, utm_source=utm
    )
    Lead.objects.filter(pk=obj.pk).update(created_at=when)
    obj.refresh_from_db()
    return obj


def subscription(name, plan, activated, status="active", cancelled=None, period="monthly"):
    customer = Customer.objects.create(
        name=name, business_name=f"{name} Funerals", email=f"{name.lower()}@example.com", phone="0821234567",
        terms_accepted_at=timezone.now(), terms_version="1.0", notice_version="1.0",
    )  # fmt: skip
    amount = plan.annual_price if period == "annual" else plan.monthly_price
    return Subscription.objects.create(
        customer=customer, plan=plan, status=status, billing_period=period, initial_amount=amount,
        recurring_amount=amount, activated_at=activated, cancelled_at=cancelled,
    )  # fmt: skip


def quote(
    number, who, plan, price, sent, status, decided=None, lead_=None, sub=None, valid_until=FAR, period="monthly"
):
    q = Quote.objects.create(
        number=f"QT-2026-{number:04d}", status=status, lead=lead_, contact_name=who, business_name=f"{who} Funerals",
        email=f"{who.lower()}@example.com", phone="0821234567", plan=plan, recurring_price=Decimal(price),
        billing_period=period, vat_rate=Decimal("0.15"), valid_until=valid_until, subscription=sub,
    )  # fmt: skip
    Quote.objects.filter(pk=q.pk).update(sent_at=sent, decided_at=decided)
    return q


def pay(sub, when, amount, status="COMPLETE", fee=None, n=[0]):  # noqa: B006
    n[0] += 1
    p = Payment.objects.create(
        subscription=sub, pf_payment_id=f"pf{n[0]}", status=status, amount_gross=Decimal(amount), amount_fee=fee
    )
    Payment.objects.filter(pk=p.pk).update(received_at=when)
    return p


def story():
    """Six months of a small business. Every figure the tests check was worked out by hand from this."""
    starter = priced_plan("starter", "1500.00", "2500.00")
    pro = priced_plan("professional", "3000.00", "4000.00", annual="30000.00")
    a = subscription("Alpha", starter, at(1, 10))
    b = subscription("Beta", starter, at(2, 10), "cancelled", at(4, 20))
    c = subscription("Gamma", pro, at(3, 1), period="annual")
    d = subscription("Delta", starter, at(5, 12), "past_due")
    e = subscription("Epsilon", pro, at(5, 20), "paused")

    la = lead("Alpha", at(1, 2), "won", "facebook")
    lb = lead("Beta", at(2, 1), "won", "google")
    lc = lead("Gamma", at(2, 20), "won", "Google ")
    ld = lead("Delta", at(4, 10), "lost")
    le = lead("Epsilon", at(5, 1), "quoted", "facebook")
    lf = lead("Zeta", at(6, 5), "quoted", "facebook")
    lead("Eta", at(6, 20), "new")
    quote(1, "Alpha", starter, "1500", at(1, 5), "accepted", at(1, 8), la, a)
    quote(2, "Beta", starter, "1500", at(2, 3), "accepted", at(2, 8), lb, b)
    quote(3, "Gamma", pro, "30000", at(2, 24), "accepted", at(2, 28), lc, c, period="annual")
    quote(4, "Delta", pro, "3000", at(4, 12), "declined", at(4, 20), ld)
    quote(5, "Epsilon", starter, "1500", at(5, 3), "sent", None, le, valid_until=PAST)
    quote(6, "Zeta", starter, "1500", at(6, 8), "sent", None, lf)
    quote(7, "Theta", starter, "1500", at(6, 10), "cancelled")
    quote(8, "Late", starter, "1500", at(8, 1), "accepted", at(8, 5))  # sent after the period

    p1 = pay(a, at(1, 12), "4000", fee=Decimal("3.00"))
    pay(c, at(3, 2), "30000", fee=Decimal("20.00"))
    pay(d, at(5, 13), "1500", status="FAILED")
    pay(a, datetime(2026, 5, 31, 22, 30, tzinfo=UTC), "1500")  # 00:30 on 1 June in South Africa
    refund = Refund.objects.create(payment=p1, amount=Decimal("500.00"), reason="x", status=Refund.Status.PROCESSED)
    Refund.objects.create(payment=p1, amount=Decimal("900.00"), reason="y", status=Refund.Status.DECLINED)
    Refund.objects.filter(pk=refund.pk).update(processed_at=at(2, 1))

    Instance.objects.create(customer=a.customer, subscription=a, status="live", went_live_on=date(2026, 1, 20))
    Instance.objects.create(customer=c.customer, subscription=c, status="live", went_live_on=date(2026, 3, 15))
    Instance.objects.create(customer=d.customer, subscription=d, status="awaiting")
    return {"a": a, "b": b, "c": c, "d": d, "e": e}


class HelperTests(SimpleTestCase):
    def test_pct_and_summarise(self):
        self.assertEqual(reports.pct(3, 5), 60.0)
        self.assertEqual(reports.pct(Decimal("1500"), Decimal("5500")), 27.3)
        self.assertIsNone(reports.pct(1, 0))
        self.assertEqual(reports.summarise([]), {"n": 0, "mean": None, "median": None})
        self.assertEqual(reports.summarise([1, 2, 9]), {"n": 3, "mean": 4.0, "median": 2})

    def test_monthly_equivalent_and_vat_split(self):
        self.assertEqual(reports.monthly(Decimal("30000"), "annual"), Decimal("2500"))
        self.assertEqual(reports.monthly(Decimal("1500"), "monthly"), Decimal("1500"))
        self.assertIsNone(reports.excl_vat(Decimal("115")))  # no VAT number in tests
        with override_settings(VAT_NUMBER="4123456789", VAT_RATE=0.15):
            self.assertEqual(reports.excl_vat(Decimal("115")), Decimal("100.00"))

    def test_month_start(self):
        self.assertEqual(reports.month_start(date(2026, 11, 24), 12), date(2025, 12, 1))
        self.assertEqual(reports.month_start(date(2026, 11, 24), 3), date(2026, 9, 1))
        self.assertEqual(reports.month_start(date(2027, 1, 5), 3), date(2026, 11, 1))
        self.assertEqual(reports.month_start(date(2026, 3, 1), 1), date(2026, 3, 1))

    def test_period_defaults_swaps_and_never_passes_today(self):
        today = date(2026, 11, 24)
        self.assertEqual(reports.Period.from_params({}, today), reports.Period(date(2025, 12, 1), today))
        self.assertEqual(
            reports.Period.from_params({"from": "2026-05-01", "to": "2026-02-01"}, today),
            reports.Period(date(2026, 2, 1), date(2026, 5, 1)),
        )
        self.assertEqual(reports.Period.from_params({"to": "2099-01-01"}, today).end, today)
        self.assertEqual(reports.Period.from_params({"from": "2099-01-01"}, today), reports.Period(today, today))
        self.assertEqual(
            reports.Period.from_params({"from": "garbage", "to": "also bad"}, today),
            reports.Period(date(2025, 12, 1), today),
        )

    def test_months_listing_and_cap(self):
        self.assertEqual([m.month for m in PERIOD.months()], [1, 2, 3, 4, 5, 6])
        long = reports.Period(date(2020, 1, 15), date(2026, 6, 30)).months()
        self.assertEqual(len(long), reports.MAX_MONTHS)
        self.assertEqual(long[-1], date(2026, 6, 1))


class FunnelAndSourceTests(TestCase):
    def setUp(self):
        story()

    def test_funnel(self):
        f = reports.funnel(PERIOD)
        self.assertEqual((f["total"], f["contacted"], f["quoted"], f["won"], f["lost"], f["open"]), (7, 6, 6, 3, 1, 3))
        self.assertEqual((f["win_rate"], f["conversion"]), (75.0, 42.9))
        self.assertEqual(dict(f["by_status"])["Won"], 3)

    def test_funnel_respects_the_period(self):
        self.assertEqual(reports.funnel(reports.Period(date(2026, 3, 1), date(2026, 3, 31)))["total"], 0)
        self.assertIsNone(reports.funnel(reports.Period(date(2026, 3, 1), date(2026, 3, 31)))["win_rate"])

    def test_sources_are_tidied_and_ranked(self):
        rows = {r["source"]: r for r in reports.sources(PERIOD)}
        self.assertEqual(list(rows), ["facebook", "Direct / not tracked", "google"])  # 3, 2 and 2 leads; ties by name
        self.assertEqual(
            (
                rows["facebook"]["leads"],
                rows["facebook"]["quoted"],
                rows["facebook"]["won"],
                rows["facebook"]["win_rate"],
            ),
            (3, 3, 1, 100.0),
        )
        self.assertEqual(
            (rows["google"]["leads"], rows["google"]["won"]), (2, 2)
        )  # "Google " and "google" are one source
        self.assertEqual((rows["Direct / not tracked"]["lost"], rows["Direct / not tracked"]["win_rate"]), (1, 0.0))


class QuotationTests(TestCase):
    def setUp(self):
        story()

    def test_outcomes_and_rates(self):
        q = reports.quotations(PERIOD)
        self.assertEqual(q["sent"], 7)  # the one sent in August is outside the period
        self.assertEqual(q["counts"], {"Accepted": 3, "Declined": 1, "Expired": 1, "Withdrawn": 1, "Open": 1})
        self.assertEqual(q["decided"], 5)
        self.assertEqual((q["win_rate"], q["accepted_of_sent"]), (60.0, 42.9))

    def test_value_counts_annual_as_a_twelfth(self):
        self.assertEqual(reports.quotations(PERIOD)["value_won_monthly"], Decimal("5500.00"))  # 1500 + 1500 + 30000/12

    def test_days_to_decide(self):
        d = reports.quotations(PERIOD)["days_to_decide"]
        self.assertEqual((d["n"], d["mean"], d["median"]), (3, 4.0, 4.0))

    def test_by_plan(self):
        rows = {r["plan"]: r for r in reports.quotations(PERIOD)["per_plan"]}
        self.assertEqual(
            (rows["Starter"]["sent"], rows["Starter"]["accepted"], rows["Starter"]["expired"], rows["Starter"]["rate"]),
            (5, 2, 1, 66.7),
        )
        self.assertEqual(
            (rows["Professional"]["sent"], rows["Professional"]["declined"], rows["Professional"]["rate"]), (2, 1, 50.0)
        )

    def test_outcome_labels(self):
        by_number = {q.number: reports.quote_outcome(q) for q in Quote.objects.all()}
        self.assertEqual(by_number["QT-2026-0005"], "Expired")
        self.assertEqual(by_number["QT-2026-0006"], "Open")
        self.assertEqual(by_number["QT-2026-0007"], "Withdrawn")

    def test_no_quotations_gives_no_rates_and_no_errors(self):
        q = reports.quotations(reports.Period(date(2025, 1, 1), date(2025, 1, 31)))
        self.assertEqual((q["sent"], q["win_rate"], q["accepted_of_sent"], q["per_plan"]), (0, None, None, []))
        self.assertEqual(q["days_to_decide"]["n"], 0)


class TimeToCloseTests(TestCase):
    def setUp(self):
        story()

    def test_each_step(self):
        c = reports.time_to_close(PERIOD)
        self.assertEqual(c["accepted"], 3)
        self.assertEqual((c["lead_to_quote"]["mean"], c["lead_to_quote"]["median"]), (3.0, 3.0))
        self.assertEqual((c["quote_to_accept"]["mean"], c["quote_to_accept"]["median"]), (4.0, 4.0))
        self.assertEqual((c["lead_to_accept"]["mean"], c["lead_to_accept"]["median"]), (7.0, 7.0))
        self.assertEqual(
            (c["accept_to_paid"]["n"], c["accept_to_paid"]["mean"], c["accept_to_paid"]["median"]), (3, 1.7, 2.0)
        )
        self.assertEqual((c["paid_to_live"]["n"], c["paid_to_live"]["mean"]), (2, 12.0))  # 10 and 14 days
        self.assertEqual(len(c["steps"]), 5)

    def test_waiting_to_pay_ignores_the_period(self):
        self.assertEqual(
            reports.time_to_close(PERIOD)["waiting_to_pay"], 1
        )  # the August acceptance has no subscription

    def test_a_quotation_with_no_lead_still_counts_for_its_own_steps(self):
        c = reports.time_to_close(reports.Period(date(2026, 8, 1), date(2026, 8, 31)))
        self.assertEqual((c["accepted"], c["quote_to_accept"]["n"], c["lead_to_accept"]["n"]), (1, 1, 0))


class RevenueTests(TestCase):
    def setUp(self):
        story()

    def test_mrr_by_plan(self):
        r = reports.mrr_by_plan()
        plans = {p["plan"]: p for p in r["plans"]}
        self.assertEqual([p["plan"] for p in r["plans"]], ["Starter", "Professional"])
        self.assertEqual(
            (
                plans["Starter"]["customers"],
                plans["Starter"]["mrr"],
                plans["Starter"]["past_due"],
                plans["Starter"]["at_risk"],
            ),
            (2, Decimal("3000.00"), 1, Decimal("1500.00")),
        )
        self.assertEqual(
            (plans["Professional"]["customers"], plans["Professional"]["mrr"]), (1, Decimal("2500.00"))
        )  # annual / 12
        self.assertEqual(
            (plans["Professional"]["paused"], plans["Professional"]["paused_mrr"]), (1, Decimal("3000.00"))
        )
        total = r["total"]
        self.assertEqual(
            (total["customers"], total["mrr"], total["arr"], total["at_risk"], total["paused"]),
            (3, Decimal("5500.00"), Decimal("66000.00"), Decimal("1500.00"), 1),
        )
        self.assertEqual((plans["Starter"]["share"], plans["Professional"]["share"]), (54.5, 45.5))
        self.assertEqual((total["average"], plans["Starter"]["average"]), (Decimal("1833.33"), Decimal("1500.00")))
        self.assertIsNone(total["mrr_excl_vat"])

    @override_settings(VAT_NUMBER="4123456789", VAT_RATE=0.15)
    def test_mrr_excluding_vat_when_registered(self):
        self.assertEqual(reports.mrr_by_plan()["total"]["mrr_excl_vat"], Decimal("4782.61"))

    def test_cancelled_and_pending_are_not_counted(self):
        self.assertEqual(sum(p["customers"] for p in reports.mrr_by_plan()["plans"]), 3)

    def test_empty(self):
        Refund.objects.all().delete()
        Payment.objects.all().delete()
        Instance.objects.all().delete()
        Quote.objects.all().delete()
        Subscription.objects.all().delete()
        r = reports.mrr_by_plan()
        self.assertEqual(
            (r["plans"], r["total"]["mrr"], r["total"]["average"], r["total"]["share"]),
            ([], Decimal("0.00"), None, None),
        )


class TrendAndChurnTests(TestCase):
    def setUp(self):
        story()
        self.rows = {r["month"].month: r for r in reports.monthly_trend(PERIOD)}

    def test_customer_counts(self):
        self.assertEqual([self.rows[m]["customers_end"] for m in range(1, 7)], [1, 2, 3, 2, 4, 4])
        self.assertEqual([self.rows[m]["new"] for m in range(1, 7)], [1, 1, 1, 0, 2, 0])
        self.assertEqual([self.rows[m]["lost"] for m in range(1, 7)], [0, 0, 0, 1, 0, 0])

    def test_revenue_at_each_month_end(self):
        self.assertEqual(
            [self.rows[m]["mrr_end"] for m in range(1, 7)],
            [Decimal(x) for x in ("1500.00", "3000.00", "5500.00", "4000.00", "8500.00", "8500.00")],
        )
        self.assertEqual((self.rows[4]["mrr_lost"], self.rows[5]["mrr_new"]), (Decimal("1500.00"), Decimal("4500.00")))

    def test_churn_rates(self):
        april = self.rows[4]
        self.assertEqual((april["customers_start"], april["logo_churn"], april["revenue_churn"]), (3, 33.3, 27.3))
        self.assertIsNone(self.rows[1]["logo_churn"])  # nobody to lose at the start
        self.assertEqual(self.rows[6]["logo_churn"], 0.0)

    def test_summary_averages_only_months_that_started_with_customers(self):
        s = reports.churn_summary(reports.monthly_trend(PERIOD))
        self.assertEqual(
            (s["lost"], s["new"], s["mrr_lost"], s["mrr_new"]), (1, 5, Decimal("1500.00"), Decimal("10000.00"))
        )
        self.assertEqual((s["avg_logo_churn"], s["avg_revenue_churn"]), (6.7, 5.5))

    def test_cash_by_month_uses_south_african_dates(self):
        self.assertEqual(self.rows[1]["collected"], Decimal("4000.00"))
        self.assertEqual(
            (self.rows[2]["refunded"], self.rows[2]["net_collected"]), (Decimal("500.00"), Decimal("-500.00"))
        )
        self.assertEqual(self.rows[3]["collected"], Decimal("30000.00"))
        self.assertEqual(
            (self.rows[5]["collected"], self.rows[6]["collected"]), (Decimal("0.00"), Decimal("1500.00"))
        )  # 22:30 UTC on 31 May is 1 June here

    def test_a_cancelled_subscription_with_no_date_is_treated_as_gone_on_its_first_day(self):
        sub = subscription("Nodate", priced_plan("starter", "1500.00", "2500.00"), at(6, 3), "cancelled")
        rows = {r["month"].month: r for r in reports.monthly_trend(PERIOD)}
        self.assertEqual((rows[6]["new"], rows[6]["lost"], rows[6]["customers_end"]), (1, 1, 4))
        sub.delete()

    def test_empty_database_gives_zero_rows_not_errors(self):
        Refund.objects.all().delete()
        Payment.objects.all().delete()
        Instance.objects.all().delete()
        Quote.objects.all().delete()
        Subscription.objects.all().delete()
        rows = reports.monthly_trend(PERIOD)
        self.assertEqual(len(rows), 6)
        self.assertEqual({r["mrr_end"] for r in rows}, {Decimal("0.00")})
        summary = reports.churn_summary(rows)
        self.assertEqual((summary["avg_logo_churn"], summary["avg_revenue_churn"], summary["lost"]), (None, None, 0))
        self.assertEqual(reports.chart(rows)["top"], Decimal("0.00"))


class CashAndOperationsTests(TestCase):
    def setUp(self):
        story()

    def test_cash(self):
        c = reports.cash(PERIOD)
        self.assertEqual((c["payments"], c["failed"], c["failure_rate"]), (3, 1, 25.0))
        self.assertEqual(
            (c["gross"], c["fees"], c["refunded"], c["net"], c["refund_count"]),
            (Decimal("35500.00"), Decimal("23.00"), Decimal("500.00"), Decimal("35000.00"), 1),
        )

    def test_declined_refunds_are_not_counted(self):
        self.assertEqual(reports.cash(PERIOD)["refunded"], Decimal("500.00"))

    def test_operations(self):
        o = reports.operations()
        self.assertEqual(
            (o["live"], o["waiting"], o["outdated"], o["missing"]), (2, 1, 0, 1)
        )  # the paused customer has no record


class ChartTests(TestCase):
    def test_bars_scale_to_the_tallest(self):
        story()
        c = reports.chart(reports.monthly_trend(PERIOD))
        self.assertEqual(len(c["bars"]), 6)
        heights = [b["h"] for b in c["bars"]]
        self.assertEqual(max(heights), heights[4])  # May and June are the peak at R8 500
        self.assertEqual(c["top"], Decimal("8500.00"))
        self.assertTrue(all(b["y"] + b["h"] == c["base"] for b in c["bars"]))
        self.assertIn("Jan 2026: R1,500.00", c["bars"][0]["title"])

    def test_long_runs_label_every_other_month(self):
        rows = reports.monthly_trend(reports.Period(date(2025, 1, 1), date(2026, 6, 30)))
        labels = [b["label"] for b in reports.chart(rows)["bars"]]
        self.assertEqual(len(labels), 18)
        self.assertEqual(labels[0], "Jan")
        self.assertEqual(labels[1], "")


class ReportPageTests(StaffTestCase):
    url = reverse("backoffice:reports")
    query = {"from": "2026-01-01", "to": "2026-06-30"}

    def test_page_shows_the_figures_for_the_chosen_period(self):
        story()
        response = self.client.get(self.url, self.query)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["quotes"]["win_rate"], 60.0)
        self.assertEqual(response.context["revenue"]["total"]["mrr"], Decimal("5500.00"))
        self.assertEqual(response.context["period"], PERIOD)
        for heading in (
            "Quote-to-won rate",
            "Time to close",
            "Recurring revenue by plan",
            "Growth and churn by month",
            "Where leads come from",
            "Download the data",
            "How these are worked out",
        ):
            self.assertContains(response, heading)
        self.assertContains(response, "<svg")
        self.assertContains(response, "Starter")

    def test_an_empty_system_still_renders(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No paying customers yet")
        self.assertContains(response, "n/a")

    def test_bad_dates_fall_back_to_the_default(self):
        self.assertEqual(self.client.get(self.url, {"from": "nope", "to": "2026-99-99"}).status_code, 200)

    def test_presets_and_menu(self):
        response = self.client.get(self.url)
        labels = [label for label, _, _ in response.context["presets"]]
        self.assertEqual(labels, ["Last 3 months", "Last 6 months", "Last 12 months", "This year", "All time"])
        self.assertContains(self.client.get(reverse("backoffice:dashboard")), self.url)

    def test_all_time_starts_at_the_first_record(self):
        story()
        all_time = dict((label, start) for label, start, _ in self.client.get(self.url).context["presets"])["All time"]
        self.assertEqual(all_time, "2026-01-02")  # the earliest lead, created on 2 January

    def test_long_periods_say_only_the_latest_months_are_shown(self):
        response = self.client.get(self.url, {"from": "2020-01-01", "to": "2026-06-30"})
        self.assertEqual(response.context["months_shown"], reports.MAX_MONTHS)
        self.assertContains(response, "Only the most recent 24 months are shown")

    @override_settings(VAT_NUMBER="4123456789")
    def test_vat_column_appears_with_a_vat_number(self):
        story()
        self.assertContains(self.client.get(self.url, self.query), "MRR excl. VAT")

    def test_no_vat_column_without_a_vat_number(self):
        story()
        self.assertNotContains(self.client.get(self.url, self.query), "MRR excl. VAT")


class ReportAccessTests(TestCase):
    def test_sales_and_anonymous_are_refused(self):
        story()
        sales = self.client_class()
        sales.force_login(make_user("sales"))
        urls = [reverse("backoffice:reports")] + [
            reverse("backoffice:report_csv", args=[k]) for k in reports.EXPORT_LABELS
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(sales.get(url).status_code, 403)
                self.assertEqual(self.client.get(url).status_code, 302)
        self.assertFalse(AuditEntry.objects.filter(action="export.csv").exists())


class ReportCsvTests(StaffTestCase):
    def setUp(self):
        super().setUp()
        story()

    def get(self, kind, **extra):
        return self.client.get(
            reverse("backoffice:report_csv", args=[kind]), {"from": "2026-01-01", "to": "2026-06-30", **extra}
        )

    @staticmethod
    def rows(response, delimiter=","):
        text = response.content.decode("utf-8")
        assert text.startswith("﻿")
        return list(csv.reader(io.StringIO(text[1:]), delimiter=delimiter))

    def test_headers_and_file_names(self):
        for kind in reports.EXPORT_LABELS:
            with self.subTest(kind=kind):
                response = self.get(kind)
                self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
                self.assertIn(f'filename="funera-report-{kind}-', response["Content-Disposition"])
                self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(self.get("nonsense").status_code, 404)

    def test_summary(self):
        rows = self.rows(self.get("summary"))
        self.assertEqual(rows[0], ["Section", "Measure", "Value"])
        lookup = {(r[0], r[1]): r[2] for r in rows[1:]}
        self.assertEqual(lookup[("Period", "From")], "2026-01-01")
        self.assertEqual(lookup[("Quotations", "Quote-to-won rate % (accepted / those with an outcome)")], "60.0")
        self.assertEqual(lookup[("Revenue today", "Monthly recurring revenue (R, incl. VAT)")], "5500.00")
        self.assertEqual(lookup[("Churn", "Customers lost")], "1")
        self.assertEqual(lookup[("Cash", "Net of refunds (R)")], "35000.00")
        self.assertEqual(lookup[("Leads", "Won")], "3")

    def test_monthly(self):
        rows = self.rows(self.get("monthly"))
        self.assertEqual(len(rows), 7)
        april = dict(zip(rows[0], rows[4], strict=True))
        self.assertEqual(
            (april["Month"], april["Lost"], april["Customer churn %"], april["MRR at end (R)"]),
            ("2026-04", "1", "33.3", "4000.00"),
        )
        self.assertEqual(dict(zip(rows[0], rows[1], strict=True))["Customer churn %"], "")  # nothing to lose yet

    def test_plans(self):
        rows = self.rows(self.get("plans"))
        starter = dict(zip(rows[0], rows[1], strict=True))
        self.assertEqual(
            (starter["Plan"], starter["MRR (R, incl. VAT)"], starter["MRR at risk (R)"], starter["MRR (R, excl. VAT)"]),
            ("Starter", "3000.00", "1500.00", ""),
        )

    def test_quotes(self):
        rows = self.rows(self.get("quotes"))
        self.assertEqual(len(rows), 8)  # header and the seven sent in the period
        first = dict(zip(rows[0], rows[1], strict=True))
        self.assertEqual(
            (first["Quotation"], first["Outcome"], first["Days to decide"], first["Subscription"]),
            ("QT-2026-0001", "Accepted", "3.0", Subscription.objects.get(customer__name="Alpha").reference),
        )
        outcomes = {r[0]: r[6] for r in rows[1:]}
        self.assertEqual(outcomes["QT-2026-0005"], "Expired")
        gamma = dict(zip(rows[0], [r for r in rows if r[0] == "QT-2026-0003"][0], strict=True))
        self.assertEqual(gamma["Monthly value (R, incl. VAT)"], "2500.00")

    def test_business_names_cannot_inject_formulas(self):
        Quote.objects.filter(number="QT-2026-0001").update(business_name='=HYPERLINK("http://evil")')
        rows = self.rows(self.get("quotes"))
        self.assertTrue(rows[1][1].startswith("'="))

    def test_sources(self):
        rows = self.rows(self.get("sources"))
        self.assertEqual(rows[0], ["Source", "Leads", "Quoted", "Won", "Lost", "Win rate %"])
        self.assertEqual(rows[1][:4], ["facebook", "3", "3", "1"])

    def test_semicolon_separator_and_decimal_comma(self):
        rows = self.rows(self.get("monthly", delimiter="semicolon"), ";")
        self.assertEqual(rows[0][0], "Month")
        self.assertGreater(len(rows[0]), 10)
        april = dict(zip(rows[0], rows[4], strict=True))
        self.assertEqual((april["MRR at end (R)"], april["Customer churn %"]), ("4000,00", "33,3"))
        table = self.rows(self.get("monthly"))
        point = dict(zip(table[0], table[4], strict=True))
        self.assertEqual((point["MRR at end (R)"], point["Customer churn %"]), ("4000.00", "33.3"))

    def test_every_download_is_audited_with_the_period(self):
        self.get("summary")
        self.get("monthly")
        entries = AuditEntry.objects.filter(action="export.csv").order_by("id")
        self.assertEqual(entries.count(), 2)
        first = entries.first()
        self.assertEqual((first.object_type, first.actor_name), ("export", "sam"))
        self.assertIn("Report: Summary of every figure", first.object_label)
        self.assertIn("2026-01-01 to 2026-06-30", first.detail)

    def test_page_links_carry_the_period(self):
        page = self.client.get(reverse("backoffice:reports"), {"from": "2026-01-01", "to": "2026-06-30"})
        for kind in reports.EXPORT_LABELS:
            self.assertContains(page, f"/reports/{kind}.csv?from=2026-01-01&amp;to=2026-06-30")
        self.assertContains(page, "delimiter=semicolon")
