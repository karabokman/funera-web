from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from backoffice import audit
from backoffice.models import AuditEntry
from billing import payfast
from billing.models import Payment, Refund, SubscriptionEvent
from billing.tests.helpers import live_subscription
from quotes import services
from quotes.models import Quote
from quotes.tests.helpers import make_lead, make_quote

from .test_backoffice import StaffTestCase, quote_post
from .test_offers import make_offer, offer_post
from .test_roles import PW, make_user

User = get_user_model()


def last():
    return AuditEntry.objects.first()


class ActionsAreRecordedTests(StaffTestCase):
    """Every back-office action leaves an entry naming the signed-in user."""

    def test_lead_update_records_what_changed_only_when_something_did(self):
        lead = make_lead()
        url = reverse("backoffice:lead", args=[lead.pk])
        self.client.post(url, {"status": "contacted", "internal_notes": "called"})
        entry = last()
        self.assertEqual(
            (entry.actor, entry.actor_name, entry.action, entry.object_type, entry.object_id),
            (self.staff, "sam", "lead.updated", "lead", lead.pk),
        )
        self.assertIn("status new to contacted", entry.detail)
        self.assertIn("notes edited", entry.detail)
        count = AuditEntry.objects.filter(action="lead.updated").count()
        self.client.post(url, {"status": "contacted", "internal_notes": "called"})  # nothing changed
        self.assertEqual(AuditEntry.objects.filter(action="lead.updated").count(), count)

    def test_follow_ups(self):
        lead = make_lead()
        url = reverse("backoffice:follow_up", args=["lead", lead.pk])
        self.client.post(url, {"action": "snooze:3"})
        self.assertEqual(last().action, "followup.snooze")
        self.client.post(url, {"action": "set", "follow_up_on": "2030-01-05", "next_action": "Call"})
        self.assertEqual(last().action, "followup.set")
        self.client.post(url, {"action": "done"})
        self.assertEqual((last().action, last().object_label), ("followup.done", "Mokoena Funerals"))
        self.client.post(url, {"action": "bogus"})  # refused: not logged
        self.assertEqual(last().action, "followup.done")

    def test_quote_lifecycle(self):
        self.client.post(reverse("backoffice:quote_new"), quote_post())
        quote = Quote.objects.get()
        self.assertEqual((last().action, last().object_label.startswith(quote.number)), ("quote.created", True))
        self.client.post(
            reverse("backoffice:quote_edit", args=[quote.pk]),
            quote_post(recurring_price="1200.00", **{"lines-INITIAL_FORMS": "0"}),
        )
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("backoffice:quote_send", args=[quote.pk]))
        self.assertEqual((last().action, last().detail), ("quote.sent", "to thandi@example.com"))
        self.client.post(reverse("backoffice:quote_send", args=[quote.pk]))
        self.assertEqual(last().action, "quote.resent")
        self.client.post(reverse("backoffice:quote_duplicate", args=[quote.pk]))
        self.assertEqual((last().action, last().detail), ("quote.duplicated", f"copy of {quote.number}"))
        self.client.post(reverse("backoffice:quote_cancel", args=[quote.pk]))
        self.assertEqual(last().action, "quote.cancelled")

    def test_failed_actions_are_not_logged(self):
        q = make_quote(price="0")
        self.client.post(reverse("backoffice:quote_send", args=[q.pk]))
        self.assertFalse(AuditEntry.objects.filter(action__startswith="quote.").exists())

    def test_offers(self):
        self.client.post(reverse("backoffice:offer_new"), offer_post())
        self.assertEqual(last().action, "offer.created")
        offer = make_offer()
        self.client.post(reverse("backoffice:offer_delete", args=[offer.pk]))
        self.assertEqual((last().action, last().object_label), ("offer.deleted", offer.name))
        self.client.post(reverse("backoffice:quote_save_offer", args=[make_quote().pk]), {"name": "From quote"})
        self.assertEqual((last().action, last().object_label), ("offer.created", "From quote"))

    def test_subscription_and_refund_actions(self):
        sub = live_subscription()
        payment = Payment.objects.create(subscription=sub, pf_payment_id="55", status="COMPLETE", amount_gross=4000)
        with mock.patch("billing.payfast._call"):
            self.client.post(reverse("backoffice:subscription_pause", args=[sub.pk]), {"cycles": 2})
            self.assertEqual((last().action, last().detail), ("subscription.paused", "for 2 cycle(s)"))
            self.client.post(reverse("backoffice:subscription_resume", args=[sub.pk]))
            self.assertEqual(last().action, "subscription.resumed")
            self.client.post(
                reverse("backoffice:subscription_refund", args=[sub.pk]), {"payment": payment.pk, "reason": "Goodwill"}
            )
            self.assertEqual((last().action, last().detail), ("subscription.refund_raised", "Goodwill"))
            refund = Refund.objects.get()
            with self.captureOnCommitCallbacks(execute=True):
                self.client.post(reverse("backoffice:refund_action", args=[refund.pk, "process"]))
            self.assertEqual(last().action, "refund.processed")
            self.client.post(reverse("backoffice:subscription_cancel", args=[sub.pk]), {"reason": "Closed down"})
        entry = last()
        self.assertEqual(
            (entry.action, entry.detail, entry.actor_name), ("subscription.cancelled", "Closed down", "sam")
        )
        self.assertIn(sub.reference, entry.object_label)

    def test_refund_failure_and_decline(self):
        sub = live_subscription()
        payment = Payment.objects.create(subscription=sub, pf_payment_id="56", status="COMPLETE", amount_gross=4000)
        refund = Refund.objects.create(payment=payment, amount=4000, reason="x")
        with mock.patch("billing.payfast.refund_payment", side_effect=payfast.GatewayUnavailable("PayFast is down")):
            self.client.post(reverse("backoffice:refund_action", args=[refund.pk, "process"]))
        self.assertEqual((last().action, last().detail), ("refund.failed", "PayFast is down"))
        refund.refresh_from_db()
        refund.status = Refund.Status.REQUESTED
        refund.save()
        self.client.post(reverse("backoffice:refund_action", args=[refund.pk, "decline"]), {"note": "Outside policy"})
        self.assertEqual((last().action, last().detail), ("refund.declined", "Outside policy"))

    def test_the_entry_records_the_address(self):
        make_lead()
        lead = make_lead(email="z@x.co")
        self.client.post(
            reverse("backoffice:follow_up", args=["lead", lead.pk]), {"action": "done"}, REMOTE_ADDR="203.0.113.7"
        )
        self.assertEqual(last().ip_address, "203.0.113.7")


class SignInTests(TestCase):
    def test_staff_sign_in_and_out_and_failures_are_recorded(self):
        make_user("sales")
        self.client.post(reverse("backoffice:login"), {"username": "sales", "password": "wrong-password"})
        self.assertEqual(
            (last().action, last().detail, last().actor), ("staff.sign_in_failed", "username: sales", None)
        )
        self.client.post(reverse("backoffice:login"), {"username": "sales", "password": PW})
        self.assertEqual((last().action, last().actor_name, last().object_type), ("staff.signed_in", "sales", "staff"))
        self.client.post(reverse("backoffice:logout"))
        self.assertEqual(last().action, "staff.signed_out")

    def test_failed_attempts_never_store_the_password(self):
        self.client.post(reverse("backoffice:login"), {"username": "nobody", "password": "hunter2-secret"})
        for entry in AuditEntry.objects.all():
            self.assertNotIn("hunter2", f"{entry.detail}{entry.object_label}{entry.actor_name}")

    def test_non_staff_sign_ins_are_not_logged(self):
        User.objects.create_user("client", "c@x.co", PW)
        self.client.force_login(User.objects.get(username="client"))
        self.assertFalse(AuditEntry.objects.filter(action="staff.signed_in").exists())

    def test_site_admin_sign_in_is_logged_too(self):
        make_user("root", superuser=True)
        self.client.post("/admin/login/?next=/admin/", {"username": "root", "password": PW})
        self.assertEqual((last().action, last().actor_name), ("staff.signed_in", "root"))

    def test_entry_survives_the_account_being_deleted(self):
        user = make_user("temp")
        audit.record(None, "lead.updated", make_lead(), "x", user=user)
        user.delete()
        entry = last()
        self.assertEqual((entry.actor, entry.actor_name), (None, "temp"))


class SiteAdminActionTests(TestCase):
    def test_bulk_cancel_in_the_site_admin_is_logged_with_the_superuser(self):
        sub = live_subscription()
        self.client.force_login(make_user("root", superuser=True))
        with mock.patch("billing.payfast._call"):
            self.client.post(
                "/admin/billing/subscription/", {"action": "cancel_subscriptions", "_selected_action": [sub.pk]}
            )
        entry = AuditEntry.objects.get(action="subscription.cancelled")
        self.assertEqual((entry.actor_name, entry.detail), ("root", "via the site admin"))

    def test_audit_entries_are_read_only_in_the_site_admin(self):
        self.client.force_login(make_user("root", superuser=True))
        entry = audit.record(None, "lead.updated", make_lead(), "x")
        self.assertEqual(self.client.get("/admin/backoffice/auditentry/").status_code, 200)
        self.assertEqual(self.client.get("/admin/backoffice/auditentry/add/").status_code, 403)
        self.assertEqual(
            self.client.post(f"/admin/backoffice/auditentry/{entry.pk}/delete/", {"post": "yes"}).status_code, 403
        )
        self.assertTrue(AuditEntry.objects.filter(pk=entry.pk).exists())


class AuditPageTests(StaffTestCase):
    def test_admin_only(self):
        self.client.force_login(make_user("sales"))
        self.assertEqual(self.client.get(reverse("backoffice:audit")).status_code, 403)
        self.assertNotContains(self.client.get(reverse("backoffice:dashboard")), reverse("backoffice:audit"))
        self.client.logout()
        self.assertEqual(self.client.get(reverse("backoffice:audit")).status_code, 302)

    def test_shows_staff_and_client_activity_in_time_order_without_duplicates(self):
        quote = make_quote(status=Quote.Status.SENT)
        self.client.post(reverse("backoffice:quote_duplicate", args=[quote.pk]))
        services.record(quote, "viewed", actor="client")
        services.accept(quote, name="Thandi")
        sub = live_subscription()
        SubscriptionEvent.objects.create(subscription=sub, kind="payment_received", detail="R1 500.00", actor="payfast")
        rows = self.client.get(reverse("backoffice:audit")).context["page"].object_list
        whats = [r["what"] for r in rows]
        self.assertIn("Duplicated quotation", whats)
        self.assertIn("Accepted", whats)
        self.assertIn("Payment received", whats)
        self.assertEqual(whats.count("Duplicated quotation"), 1)  # the QuoteEvent twin is not repeated
        self.assertEqual([r["when"] for r in rows], sorted((r["when"] for r in rows), reverse=True))
        self.assertEqual({r["who"] for r in rows if r["source"] == "client"}, {"client", "payfast"})

    def test_older_staff_events_are_kept_but_marked(self):
        sub = live_subscription()
        old = SubscriptionEvent.objects.create(subscription=sub, kind="cancelled", actor="staff")
        SubscriptionEvent.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=30))
        make_lead()
        self.client.post(
            reverse("backoffice:follow_up", args=["lead", make_lead(email="q@x.co").pk]), {"action": "done"}
        )
        page = self.client.get(reverse("backoffice:audit"))
        self.assertContains(page, "staff (before the audit log)")

    def test_filters(self):
        lead = make_lead()
        self.client.post(reverse("backoffice:follow_up", args=["lead", lead.pk]), {"action": "done"})
        audit.record(None, "quote.sent", make_quote(), "to a@b.co", user=make_user("lerato", admin=True))
        url = reverse("backoffice:audit")

        def whats(**params):
            return [r["what"] for r in self.client.get(url, params).context["page"].object_list]

        self.assertEqual(whats(who="lerato"), ["Sent quotation"])
        self.assertEqual(whats(area="quote"), ["Sent quotation"])
        self.assertEqual(whats(area="lead"), ["Completed a follow-up"])
        self.assertEqual(whats(q="a@b.co"), ["Sent quotation"])
        self.assertNotIn("Sent quotation", whats(who="sam"))
        self.assertEqual(whats(source="client"), [])
        today = timezone.localdate().isoformat()
        self.assertTrue(whats(**{"from": today, "to": today}))
        self.assertEqual(whats(**{"to": "2001-01-01"}), [])
        self.assertTrue(whats(area="bogus", source="bogus", **{"from": "junk"}))  # bad values are ignored, not errors

    def test_pagination_keeps_the_filters(self):
        for i in range(60):
            audit.record(None, "lead.updated", make_lead(email=f"l{i}@x.co"), "x", user=self.staff)
        html = self.client.get(reverse("backoffice:audit"), {"who": "sam", "area": "lead"}).content.decode()
        self.assertIn(
            "who=sam&amp;area=lead&amp;page=2",
            html.replace("&amp;", "&amp;") if "&amp;" in html else html.replace("&", "&amp;"),
        )
        page = self.client.get(reverse("backoffice:audit"), {"who": "sam", "page": 3}).context["page"]
        self.assertEqual((page.number, len(page)), (3, AuditEntry.objects.filter(actor_name="sam").count() - 50))

    def test_empty_state(self):
        self.assertContains(self.client.get(reverse("backoffice:audit"), {"who": "nobody"}), "Nothing recorded")
