from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from backoffice import followups
from leads.models import Lead
from quotes import services
from quotes.models import Quote
from quotes.tests.helpers import make_lead, make_quote

from .test_backoffice import StaffTestCase

TODAY = timezone.localdate()


def day(n):
    return TODAY + timedelta(days=n)


class AutomaticSchedulingTests(TestCase):
    def test_new_lead_is_due_for_first_contact_today(self):
        lead = make_lead()
        self.assertEqual((lead.follow_up_on, lead.next_action), (TODAY, "Make first contact"))

    def test_lead_created_with_a_date_keeps_it_and_non_new_leads_get_none(self):
        self.assertEqual(make_lead(email="a@b.co", follow_up_on=day(5)).follow_up_on, day(5))
        self.assertIsNone(make_lead(email="c@d.co", status=Lead.Status.CONTACTED).follow_up_on)

    def test_sending_a_quote_schedules_a_chase(self):
        q = make_quote()
        services.send(q)
        q.refresh_from_db()
        self.assertEqual(q.follow_up_on, day(3))
        self.assertIn(q.number, q.next_action)

    def test_decision_or_cancel_clears_quote_and_lead_follow_ups(self):
        for outcome in ("accept", "decline", "cancel"):
            with self.subTest(outcome=outcome):
                lead = make_lead(
                    email=f"{outcome}@x.co", status=Lead.Status.QUOTED, follow_up_on=day(1), next_action="Call"
                )
                q = make_quote(status=Quote.Status.SENT, lead=lead, follow_up_on=day(1), next_action="Chase")
                if outcome == "accept":
                    services.accept(q, name="T")
                elif outcome == "decline":
                    services.decline(q)
                else:
                    services.cancel(q)
                q.refresh_from_db()
                lead.refresh_from_db()
                self.assertEqual((q.follow_up_on, q.next_action), (None, ""))
                if outcome != "cancel":
                    self.assertEqual((lead.follow_up_on, lead.next_action), (None, ""))


class CollectTests(TestCase):
    def test_buckets(self):
        make_lead(email="1@x.co", follow_up_on=day(-2), next_action="Call back")
        make_lead(email="2@x.co", follow_up_on=day(0))
        make_lead(email="3@x.co", follow_up_on=day(4))
        make_lead(email="4@x.co", follow_up_on=day(30))
        make_lead(email="5@x.co", status=Lead.Status.CONTACTED)  # open, no date
        make_lead(email="6@x.co", status=Lead.Status.WON, follow_up_on=day(0))  # closed: ignored
        make_quote(status=Quote.Status.SENT, follow_up_on=day(-1))
        make_quote(status=Quote.Status.SENT)  # sent, no date: must not hide
        make_quote(status=Quote.Status.DRAFT)  # undated draft: not chased
        make_quote(status=Quote.Status.DRAFT, follow_up_on=day(0))
        make_quote(status=Quote.Status.ACCEPTED, follow_up_on=day(0))
        b = followups.collect()
        self.assertEqual([len(b[k]) for k in ("overdue", "today", "soon", "later", "undated")], [2, 2, 1, 1, 2])
        self.assertEqual(b["due_count"], 4)
        self.assertEqual(followups.due_count(), 4)
        self.assertEqual(b["overdue"][0]["when"], "2 days overdue")  # oldest first
        self.assertEqual(
            (b["overdue"][0]["action"], b["today"][0]["when"], b["soon"][0]["when"]),
            ("Call back", "Today", "In 4 days"),
        )
        self.assertEqual(b["overdue"][1]["when"], "1 day overdue")

    def test_default_action_text(self):
        make_lead(follow_up_on=day(0), next_action="")
        self.assertEqual(followups.collect()["today"][0]["action"], "Make first contact")


class FollowUpActionTests(StaffTestCase):
    def post(self, kind, obj, **data):
        return self.client.post(reverse("backoffice:follow_up", args=[kind, obj.pk]), data, follow=True)

    def test_set_follow_up_on_a_lead(self):
        lead = make_lead()
        response = self.post("lead", lead, action="set", follow_up_on=day(5).isoformat(), next_action="Send brochure")
        lead.refresh_from_db()
        self.assertEqual((lead.follow_up_on, lead.next_action), (day(5), "Send brochure"))
        self.assertContains(response, "Follow-up set for")

    def test_set_with_empty_date_clears_it(self):
        lead = make_lead()
        self.post("lead", lead, action="set", follow_up_on="", next_action="")
        lead.refresh_from_db()
        self.assertIsNone(lead.follow_up_on)

    def test_invalid_date_is_rejected_and_nothing_changes(self):
        lead = make_lead()
        response = self.post("lead", lead, action="set", follow_up_on="not-a-date", next_action="x")
        self.assertContains(response, "Enter a valid date")
        lead.refresh_from_db()
        self.assertEqual(lead.follow_up_on, TODAY)

    def test_done_on_a_new_lead_marks_it_contacted(self):
        lead = make_lead()
        self.post("lead", lead, action="done")
        lead.refresh_from_db()
        self.assertEqual((lead.follow_up_on, lead.status), (None, Lead.Status.CONTACTED))
        self.assertIsNotNone(lead.contacted_at)

    def test_snooze_whitelist(self):
        lead = make_lead(status=Lead.Status.CONTACTED, follow_up_on=day(-3))
        self.post("lead", lead, action="snooze:3")
        lead.refresh_from_db()
        self.assertEqual(lead.follow_up_on, day(3))
        self.post("lead", lead, action="snooze:99")
        self.post("lead", lead, action="snooze:abc")
        self.post("lead", lead, action="bogus")
        lead.refresh_from_db()
        self.assertEqual(lead.follow_up_on, day(3))

    def test_quote_follow_up_is_recorded_in_its_history(self):
        q = make_quote(status=Quote.Status.SENT, follow_up_on=day(-1))
        self.post("quote", q, action="snooze:1")
        self.post("quote", q, action="done")
        q.refresh_from_db()
        self.assertIsNone(q.follow_up_on)
        self.assertEqual(q.events.filter(kind="follow_up").count(), 2)
        self.assertEqual(q.events.filter(kind="follow_up").first().actor, "sam")

    def test_closed_items_cannot_be_scheduled(self):
        lead = make_lead(status=Lead.Status.WON)
        q = make_quote(status=Quote.Status.ACCEPTED)
        for kind, obj in (("lead", lead), ("quote", q)):
            response = self.post(kind, obj, action="set", follow_up_on=day(2).isoformat(), next_action="x")
            self.assertContains(response, "closed")
            obj.refresh_from_db()
            self.assertIsNone(obj.follow_up_on)

    def test_unknown_kind_404_and_get_not_allowed(self):
        lead = make_lead()
        self.assertEqual(
            self.client.post(
                reverse("backoffice:follow_up", args=["customer", lead.pk]), {"action": "done"}
            ).status_code,
            404,
        )
        self.assertEqual(self.client.get(reverse("backoffice:follow_up", args=["lead", lead.pk])).status_code, 405)

    def test_redirect_goes_back_to_the_page_but_never_off_site(self):
        lead = make_lead()
        url = reverse("backoffice:follow_up", args=["lead", lead.pk])
        self.assertRedirects(
            self.client.post(url, {"action": "done", "next": "/staff/follow-ups/"}), "/staff/follow-ups/"
        )
        lead.follow_up_on = day(1)
        lead.save()
        response = self.client.post(url, {"action": "snooze:1", "next": "https://evil.example/"})
        self.assertEqual(response["Location"], reverse("backoffice:lead", args=[lead.pk]))

    def test_anonymous_and_non_staff_cannot_change_follow_ups(self):
        from django.contrib.auth import get_user_model

        lead = make_lead()
        url = reverse("backoffice:follow_up", args=["lead", lead.pk])
        self.client.logout()
        self.assertEqual(self.client.post(url, {"action": "done"}).status_code, 302)
        self.client.force_login(get_user_model().objects.create_user("c", "c@x.co", "pw-12345-long"))
        self.assertEqual(self.client.post(url, {"action": "done"}).status_code, 403)
        lead.refresh_from_db()
        self.assertEqual(lead.follow_up_on, TODAY)

    def test_marking_a_lead_won_or_lost_clears_its_follow_up(self):
        lead = make_lead()
        self.client.post(reverse("backoffice:lead", args=[lead.pk]), {"status": "lost", "internal_notes": ""})
        lead.refresh_from_db()
        self.assertEqual((lead.status, lead.follow_up_on, lead.next_action), ("lost", None, ""))


class FollowUpPagesTests(StaffTestCase):
    def test_follow_ups_page_and_dashboard_today_list(self):
        make_lead(
            business_name="Overdue Burial Society", email="o@x.co", follow_up_on=day(-3), next_action="Chase decision"
        )
        make_lead(business_name="Undated Co", email="u@x.co", status=Lead.Status.CONTACTED)
        page = self.client.get(reverse("backoffice:follow_ups"))
        self.assertContains(page, "Overdue Burial Society")
        self.assertContains(page, "3 days overdue")
        self.assertContains(page, "Chase decision")
        self.assertContains(page, "No follow-up date")
        dash = self.client.get(reverse("backoffice:dashboard"))
        self.assertContains(dash, "Overdue Burial Society")
        self.assertContains(dash, "1 without a date")

    def test_dashboard_when_nothing_is_due(self):
        make_lead(follow_up_on=day(2), next_action="x")
        self.assertContains(self.client.get(reverse("backoffice:dashboard")), "Nothing is due")

    def test_nav_badge_counts_due_items(self):
        make_lead()
        make_lead(email="b@x.co", follow_up_on=day(-1))
        make_lead(email="c@x.co", follow_up_on=day(4))
        self.assertContains(self.client.get(reverse("backoffice:leads")), 'aria-label="2 due"')

    def test_nav_badge_absent_when_nothing_due(self):
        self.assertNotContains(self.client.get(reverse("backoffice:leads")), 'due"')

    def test_detail_pages_show_the_panel_for_open_items_only(self):
        lead = make_lead()
        self.assertContains(self.client.get(reverse("backoffice:lead", args=[lead.pk])), "Save follow-up")
        won = make_lead(email="w@x.co", status=Lead.Status.WON)
        self.assertNotContains(self.client.get(reverse("backoffice:lead", args=[won.pk])), "Save follow-up")
        q = make_quote(status=Quote.Status.SENT, follow_up_on=day(1), next_action="Call Thandi")
        html = self.client.get(reverse("backoffice:quote", args=[q.pk])).content.decode()
        self.assertIn("Call Thandi", html)
        self.assertIn("Save follow-up", html)
        accepted = make_quote(status=Quote.Status.ACCEPTED)
        self.assertNotContains(self.client.get(reverse("backoffice:quote", args=[accepted.pk])), "Save follow-up")

    def test_lists_show_the_follow_up_date(self):
        make_lead(follow_up_on=day(2))
        make_quote(status=Quote.Status.SENT, follow_up_on=day(2))
        for name in ("leads", "quotes"):
            self.assertContains(self.client.get(reverse(f"backoffice:{name}")), f"{day(2).day} {day(2):%b}")

    def test_follow_ups_require_staff(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("backoffice:follow_ups")).status_code, 302)
