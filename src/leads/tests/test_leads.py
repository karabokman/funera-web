import csv
import io
import time
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail, signing
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from catalog.models import Plan
from leads import services
from leads.forms import SIGNING_SALT
from leads.models import Lead


def started(seconds_ago=60):
    return signing.dumps(int(time.time()) - seconds_ago, salt=SIGNING_SALT)


def payload(**overrides):
    data = {
        "name": "Thandi Mokoena",
        "business_name": "Mokoena Funerals",
        "email": "thandi@example.com",
        "phone": "082 123 4567",
        "member_count": Lead.Size.S,
        "message": "Hello",
        "consent_given": "on",
        "website": "",
        "started": started(),
    }
    data.update(overrides)
    return data


class ContactFormTests(TestCase):
    url = reverse("leads:contact")

    def test_valid_submission_saves_lead_with_consent_record_and_sends_mail(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(self.url, payload(utm_source="google", source="/pricing/"))
        self.assertRedirects(response, reverse("leads:thanks"))
        lead = Lead.objects.get()
        self.assertTrue(lead.consent_given)
        self.assertIsNotNone(lead.consent_at)
        self.assertEqual(lead.notice_version, "1.0")
        self.assertEqual(lead.utm_source, "google")
        self.assertEqual(lead.status, Lead.Status.NEW)
        self.assertEqual(len(lead.visitor_hash), 64)
        self.assertNotIn("127.0.0.1", lead.visitor_hash)
        self.assertEqual(sorted(m.to[0] for m in mail.outbox), ["sales@example.com", "thandi@example.com"])

    def test_consent_is_required(self):
        data = payload()
        del data["consent_given"]
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Lead.objects.exists())
        self.assertContains(response, "required")

    def test_bad_phone_rejected(self):
        for phone in ("123", "abc defg hij", "0821234567; DROP"):
            with self.subTest(phone=phone):
                self.client.post(self.url, payload(phone=phone))
        self.assertFalse(Lead.objects.exists())

    def test_honeypot_looks_successful_but_saves_and_sends_nothing(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(self.url, payload(website="http://spam.example"))
        self.assertRedirects(response, reverse("leads:thanks"))
        self.assertFalse(Lead.objects.exists())
        self.assertEqual(mail.outbox, [])

    def test_too_fast_submission_is_treated_as_spam(self):
        response = self.client.post(self.url, payload(started=started(seconds_ago=0)))
        self.assertRedirects(response, reverse("leads:thanks"))
        self.assertFalse(Lead.objects.exists())

    def test_tampered_timestamp_asks_to_retry(self):
        response = self.client.post(self.url, payload(started="garbage"))
        self.assertContains(response, "expired")
        self.assertFalse(Lead.objects.exists())

    def test_form_left_open_too_long_asks_to_retry(self):
        with mock.patch("leads.forms.MAX_AGE_SECONDS", -1):
            response = self.client.post(self.url, payload())
        self.assertContains(response, "expired")
        self.assertFalse(Lead.objects.exists())

    @override_settings(LEAD_RATE_LIMIT_PER_HOUR=2)
    def test_rate_limit_per_visitor(self):
        for _ in range(2):
            self.client.post(self.url, payload())
        response = self.client.post(self.url, payload())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Lead.objects.count(), 2)
        self.assertContains(response, "several enquiries")

    @override_settings(LEAD_RATE_LIMIT_PER_HOUR=1)
    def test_rate_limit_is_per_visitor(self):
        self.client.post(self.url, payload(), REMOTE_ADDR="10.0.0.1")
        self.client.post(self.url, payload(), REMOTE_ADDR="10.0.0.2")
        self.assertEqual(Lead.objects.count(), 2)

    def test_plan_prefill_from_query_string(self):
        response = self.client.get(self.url, {"plan": "professional"})
        self.assertEqual(response.context["form"].initial["plan_interest"], Plan.objects.get(code="professional"))

    def test_unknown_plan_in_query_string_is_ignored(self):
        self.assertEqual(self.client.get(self.url, {"plan": "nope"}).status_code, 200)

    def test_mail_failure_does_not_lose_the_lead_or_show_an_error(self):
        with mock.patch("leads.services.send_mail", side_effect=OSError("smtp down")):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(self.url, payload())
        self.assertRedirects(response, reverse("leads:thanks"))
        self.assertEqual(Lead.objects.count(), 1)

    @override_settings(SALES_NOTIFY_EMAILS=[])
    def test_no_sales_recipients_still_acknowledges(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(self.url, payload())
        self.assertEqual([m.to[0] for m in mail.outbox], ["thandi@example.com"])


class AdminExportTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser("boss", "b@example.com", "pw-12345-long")
        self.client.force_login(self.admin)

    def test_csv_export_neutralises_formulas(self):
        lead = Lead.objects.create(
            name="=HYPERLINK(1)",
            business_name="+cmd",
            email="a@example.com",
            phone="0821234567",
            member_count=Lead.Size.S,
            message="@SUM(1)",
            consent_given=True,
            consent_at=timezone.now(),
        )
        response = self.client.post(
            reverse("admin:leads_lead_changelist"), {"action": "export_csv", "_selected_action": [lead.pk]}
        )
        rows = list(csv.reader(io.StringIO(response.content.decode())))
        self.assertEqual(rows[1][1:3], ["'+cmd", "'=HYPERLINK(1)"])
        self.assertEqual(rows[1][-1], "'@SUM(1)")

    def test_anonymous_cannot_reach_admin(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("admin:leads_lead_changelist")).status_code, 302)


class PurgeTests(TestCase):
    def make(self, months_old, status=Lead.Status.NEW):
        lead = Lead.objects.create(
            name="n",
            business_name="b",
            email="e@example.com",
            phone="0821234567",
            member_count=Lead.Size.S,
            consent_given=True,
            consent_at=timezone.now(),
            status=status,
        )
        Lead.objects.filter(pk=lead.pk).update(created_at=timezone.now() - timedelta(days=int(months_old * 30.5)))
        return lead

    def test_purges_only_old_unwon_leads(self):
        recent, old, won = self.make(1), self.make(30), self.make(30, Lead.Status.WON)
        self.assertEqual(services.purge_expired(), 1)
        self.assertTrue(Lead.objects.filter(pk=recent.pk).exists())
        self.assertFalse(Lead.objects.filter(pk=old.pk).exists())
        self.assertTrue(Lead.objects.filter(pk=won.pk).exists())

    def test_command_reports_count(self):
        self.make(30)
        out = io.StringIO()
        call_command("purge_old_leads", stdout=out)
        self.assertIn("Deleted 1", out.getvalue())


class FormStylingTests(TestCase):
    def test_visible_inputs_use_the_field_style(self):
        html = self.client.get(reverse("leads:contact")).content.decode()
        for name in ("name", "business_name", "email", "phone", "member_count", "plan_interest", "message"):
            with self.subTest(field=name):
                self.assertRegex(html, rf'<[^>]*name="{name}"[^>]*class="field"|<[^>]*class="field"[^>]*name="{name}"')
