from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from backoffice import roles
from billing.models import Payment, Refund
from billing.tests.helpers import live_subscription
from leads.models import Lead
from quotes.models import Quote, QuoteTemplate
from quotes.tests.helpers import make_lead, make_quote

from .test_offers import make_offer

User = get_user_model()
PW = "pw-12345-long"


def make_user(name, *, admin=False, superuser=False, staff=True, active=True):
    user = User.objects.create_user(
        name, f"{name}@example.com", PW, is_staff=staff, is_superuser=superuser, is_active=active
    )
    if admin:
        user.groups.add(roles.admin_group())
    return user


class RoleDefinitionTests(TestCase):
    def test_who_is_admin(self):
        self.assertTrue(roles.is_admin(make_user("a", admin=True)))
        self.assertTrue(roles.is_admin(make_user("b", superuser=True)))
        self.assertFalse(roles.is_admin(make_user("c")))
        self.assertFalse(roles.is_admin(make_user("d", staff=False, admin=True)))  # the group alone is not enough
        self.assertFalse(roles.is_admin(make_user("e", admin=True, active=False)))
        self.assertFalse(roles.is_admin(None))
        self.assertEqual((roles.role(make_user("f", admin=True)), roles.role(make_user("g"))), ("Admin", "Sales"))

    def test_group_exists_after_migrations(self):
        self.assertTrue(roles.Group.objects.filter(name="Funera Admin").exists())


class SalesCanWorkTheSalesPipeline(TestCase):
    def setUp(self):
        self.client.force_login(make_user("sales"))

    def test_sales_pages_open(self):
        lead, quote = make_lead(), make_quote()
        for url in (
            reverse("backoffice:dashboard"), reverse("backoffice:follow_ups"), reverse("backoffice:leads"), reverse("backoffice:lead", args=[lead.pk]),
            reverse("backoffice:quotes"), reverse("backoffice:quote", args=[quote.pk]), reverse("backoffice:quote_new"), reverse("backoffice:quote_pdf", args=[quote.pk]),
            reverse("backoffice:customers"), reverse("backoffice:subscriptions"), reverse("backoffice:offers"),
        ):  # fmt: skip
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_sales_can_quote_send_cancel_duplicate_and_use_an_offer(self):
        offer, lead = make_offer(), make_lead()
        self.assertEqual(
            self.client.get(reverse("backoffice:quote_new"), {"lead": lead.pk, "offer": offer.pk}).status_code, 200
        )
        q = make_quote()
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("backoffice:quote_send", args=[q.pk]))
        q.refresh_from_db()
        self.assertEqual(q.status, "sent")
        self.client.post(reverse("backoffice:quote_duplicate", args=[q.pk]))
        self.assertEqual(Quote.objects.count(), 2)
        self.client.post(reverse("backoffice:quote_cancel", args=[q.pk]))
        q.refresh_from_db()
        self.assertEqual(q.status, "cancelled")

    def test_sales_can_manage_follow_ups_and_leads(self):
        lead = make_lead()
        self.client.post(reverse("backoffice:follow_up", args=["lead", lead.pk]), {"action": "done"})
        self.client.post(
            reverse("backoffice:lead", args=[lead.pk]), {"status": "contacted", "internal_notes": "called"}
        )
        lead.refresh_from_db()
        self.assertEqual((lead.status, lead.internal_notes), (Lead.Status.CONTACTED, "called"))


class SalesCannotMoveMoney(TestCase):
    def setUp(self):
        self.sub = live_subscription()
        self.payment = Payment.objects.create(
            subscription=self.sub, pf_payment_id="777", status="COMPLETE", amount_gross=4000
        )
        self.refund = Refund.objects.create(payment=self.payment, amount=4000, reason="x")
        self.client.force_login(make_user("sales"))

    def test_every_money_action_is_refused_and_changes_nothing(self):
        actions = [
            ("subscription_pause", [self.sub.pk], {"cycles": 1}), ("subscription_resume", [self.sub.pk], {}), ("subscription_cancel", [self.sub.pk], {"reason": "x"}),
            ("subscription_refund", [self.sub.pk], {"payment": self.payment.pk, "reason": "x"}),
            ("refund_action", [self.refund.pk, "process"], {}), ("refund_action", [self.refund.pk, "decline"], {"note": "x"}),
        ]  # fmt: skip
        with mock.patch("billing.payfast._call") as gateway:
            for name, args, data in actions:
                with self.subTest(action=name, args=args):
                    response = self.client.post(reverse(f"backoffice:{name}", args=args), data)
                    self.assertEqual(response.status_code, 403)
                    self.assertContains(response, "Administrators only", status_code=403)
        gateway.assert_not_called()  # PayFast was never contacted
        self.sub.refresh_from_db()
        self.refund.refresh_from_db()
        self.assertEqual((self.sub.status, self.refund.status, Refund.objects.count()), ("active", "requested", 1))

    def test_refund_list_and_team_page_are_refused(self):
        for name in ("refunds", "team"):
            self.assertEqual(self.client.get(reverse(f"backoffice:{name}")).status_code, 403)

    def test_saved_offer_editing_is_refused(self):
        offer, q = make_offer(), make_quote()
        for url in (reverse("backoffice:offer_new"), reverse("backoffice:offer_edit", args=[offer.pk])):
            self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(reverse("backoffice:offer_delete", args=[offer.pk])).status_code, 403)
        self.assertEqual(
            self.client.post(reverse("backoffice:quote_save_offer", args=[q.pk]), {"name": "x"}).status_code, 403
        )
        self.assertEqual(QuoteTemplate.objects.count(), 1)

    def test_sales_see_subscription_but_no_action_buttons(self):
        page = self.client.get(reverse("backoffice:subscription", args=[self.sub.pk]))
        self.assertContains(page, "done by an administrator")
        for text in ("Cancel subscription", "Raise refund", "Pause", "Resume now"):
            self.assertNotContains(page, text)

    def test_navigation_hides_admin_areas(self):
        page = self.client.get(reverse("backoffice:dashboard")).content.decode()
        self.assertNotIn(reverse("backoffice:refunds"), page)
        self.assertNotIn(reverse("backoffice:team"), page)
        self.assertIn("Signed in as Sales", page)

    def test_offers_page_is_read_only_for_sales(self):
        make_offer()
        page = self.client.get(reverse("backoffice:offers")).content.decode()
        self.assertIn("Starter, 10 branches, standard set-up", page)
        self.assertNotIn("New saved offer", page)
        self.assertNotIn(reverse("backoffice:offer_new"), page)
        self.assertIn("Use", page)

    def test_quote_page_has_no_save_as_offer_for_sales(self):
        self.assertNotContains(self.client.get(reverse("backoffice:quote", args=[make_quote().pk])), "Save as offer")


class MoneyTotalsAreAdminOnly(TestCase):
    def setUp(self):
        live_subscription()
        make_quote(status=Quote.Status.SENT, price="1500.00")

    def test_sales_dashboard_has_no_revenue_figures(self):
        self.client.force_login(make_user("sales"))
        response = self.client.get(reverse("backoffice:dashboard"))
        for key in ("mrr", "open_quote_value", "refunds_waiting"):
            self.assertNotIn(key, response.context["stats"])
        for text in ("Monthly recurring revenue", "Refunds waiting", "worth R"):
            self.assertNotContains(response, text)
        self.assertContains(response, "Quotations out")

    def test_admin_dashboard_has_them(self):
        self.client.force_login(make_user("boss", admin=True))
        response = self.client.get(reverse("backoffice:dashboard"))
        self.assertEqual(response.context["stats"]["mrr"], Decimal("1500.00"))
        for text in ("Monthly recurring revenue", "Refunds waiting", "worth R"):
            self.assertContains(response, text)

    def test_superuser_counts_as_admin_everywhere(self):
        self.client.force_login(make_user("root", superuser=True))
        for name in ("refunds", "team"):
            self.assertEqual(self.client.get(reverse(f"backoffice:{name}")).status_code, 200)

    def test_group_member_without_staff_flag_gets_nothing(self):
        self.client.force_login(make_user("ghost", staff=False, admin=True))
        self.assertEqual(self.client.get(reverse("backoffice:dashboard")).status_code, 403)
        self.assertEqual(self.client.get(reverse("backoffice:refunds")).status_code, 403)


class TeamPageTests(TestCase):
    def test_lists_staff_with_roles(self):
        make_user("sales1")
        make_user("off", active=False)
        make_user("client", staff=False)
        self.client.force_login(make_user("boss", admin=True))
        page = self.client.get(reverse("backoffice:team"))
        self.assertContains(page, "sales1")
        self.assertContains(page, "boss")
        self.assertContains(page, "Disabled")
        self.assertNotContains(page, "client@example.com")
        self.assertEqual(
            {m["user"].username: m["role"] for m in page.context["members"]},
            {"boss": "Admin", "sales1": "Sales", "off": "Sales"},
        )


class DjangoAdminIsSuperuserOnly(TestCase):
    def test_site_admin_refuses_every_non_superuser_staff_account(self):
        for user in (make_user("sales"), make_user("boss", admin=True)):
            self.client.force_login(user)
            response = self.client.get("/admin/")
            self.assertEqual(response.status_code, 302, user.username)
            self.assertIn("login", response["Location"])
            self.assertEqual(self.client.get("/admin/auth/user/").status_code, 302)
            self.client.logout()

    def test_superuser_can_open_it(self):
        self.client.force_login(make_user("root", superuser=True))
        self.assertEqual(self.client.get("/admin/").status_code, 200)
        self.assertEqual(self.client.get("/admin/auth/group/").status_code, 200)

    def test_staff_cannot_sign_in_to_the_site_admin(self):
        make_user("sales")
        response = self.client.post("/admin/login/", {"username": "sales", "password": PW})
        self.assertContains(response, "administrators only")
        self.assertEqual(self.client.get("/admin/").status_code, 302)

    def test_superuser_signs_in_normally(self):
        make_user("root", superuser=True)
        response = self.client.post("/admin/login/?next=/admin/", {"username": "root", "password": PW})
        self.assertRedirects(response, "/admin/", fetch_redirect_response=False)
