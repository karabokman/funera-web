from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from backoffice.forms import InstanceForm
from backoffice.models import AuditEntry
from billing.models import Subscription
from billing.signals import subscription_activated
from billing.tests.helpers import live_subscription, make_subscription
from instances.models import Instance

from .test_backoffice import StaffTestCase
from .test_roles import make_user

S = Instance.Status


def make_instance(sub=None, **extra):
    sub = sub or live_subscription()
    return Instance.objects.create(customer=sub.customer, subscription=sub, **extra)


def backdate(instance, days):
    Instance.objects.filter(pk=instance.pk).update(created_at=timezone.now() - timedelta(days=days))
    instance.refresh_from_db()
    return instance


class CreationTests(TestCase):
    def test_first_payment_creates_one_awaiting_record(self):
        sub = make_subscription()
        subscription_activated.send(sender=Subscription, subscription=sub)
        subscription_activated.send(sender=Subscription, subscription=sub)
        self.assertEqual(Instance.objects.filter(subscription=sub).count(), 1)
        inst = sub.instance
        self.assertEqual((inst.status, inst.customer), (S.AWAITING, sub.customer))


class ModelTests(TestCase):
    def test_days_waiting_and_staleness(self):
        inst = backdate(make_instance(), 5)
        self.assertEqual(inst.days_waiting, 5)
        self.assertTrue(inst.stale_for_setup)  # alert after 3 days in tests
        inst.status = S.LIVE
        self.assertEqual(inst.days_waiting, 0)
        self.assertFalse(inst.stale_for_setup)

    def test_overdue_only_while_open_and_after_target(self):
        today = timezone.localdate()
        inst = make_instance(go_live_target=today - timedelta(days=1))
        self.assertTrue(inst.is_overdue)
        inst.go_live_target = today
        self.assertFalse(inst.is_overdue)
        inst.go_live_target, inst.status = today - timedelta(days=9), S.LIVE
        self.assertFalse(inst.is_overdue)

    def test_outdated_needs_a_configured_latest_version(self):
        inst = make_instance(version="1.0.0", status=S.LIVE)
        self.assertFalse(inst.is_outdated)  # flag is off in tests
        with override_settings(FUNERA_LATEST_VERSION="1.1.0"):
            self.assertTrue(inst.is_outdated)
            inst.version = "1.1.0"
            self.assertFalse(inst.is_outdated)

    def test_billing_flag_only_when_live(self):
        sub = live_subscription()
        inst = make_instance(sub, status=S.LIVE)
        self.assertEqual(inst.billing_flag, "")
        for status in (Subscription.Status.PAST_DUE, Subscription.Status.PAUSED, Subscription.Status.CANCELLED):
            sub.status = status
            self.assertTrue(inst.billing_flag, status)
        inst.status = S.AWAITING
        self.assertEqual(inst.billing_flag, "")

    def test_checklist_has_five_steps(self):
        self.assertEqual(len(make_instance().checklist), 5)


class FormTests(TestCase):
    def data(self, **extra):
        return {"status": S.SETTING_UP, **extra}

    def test_live_gets_a_go_live_date(self):
        inst = make_instance()
        form = InstanceForm(self.data(status=S.LIVE), instance=inst, customer=inst.customer)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().went_live_on, timezone.localdate())

    def test_version_change_records_the_date(self):
        inst = make_instance()
        form = InstanceForm(self.data(version="1.2.0"), instance=inst, customer=inst.customer)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().version_updated_on, timezone.localdate())

    def test_only_this_customers_free_subscriptions_are_offered(self):
        taken = make_instance()
        other = make_subscription(email="other@example.com")
        free = Subscription.objects.create(
            customer=taken.customer, plan=taken.subscription.plan, initial_amount=1, recurring_amount=1
        )
        form = InstanceForm(customer=taken.customer)
        self.assertEqual(list(form.fields["subscription"].queryset), [free])
        self.assertNotIn(other, form.fields["subscription"].queryset)
        self.assertIn(
            taken.subscription, InstanceForm(instance=taken, customer=taken.customer).fields["subscription"].queryset
        )

    def test_owner_must_be_active_staff(self):
        gone = make_user("gone", active=False)
        inst = make_instance()
        form = InstanceForm(self.data(owner=gone.pk), instance=inst, customer=inst.customer)
        self.assertFalse(form.is_valid())
        self.assertIn("owner", form.errors)


class PageTests(StaffTestCase):
    def test_list_detail_and_form_render(self):
        inst = make_instance(url="https://mokoena.example.co.za", version="1.0.0", admin_name="Thandi")
        self.assertContains(self.client.get(reverse("backoffice:instances")), "Mokoena Funerals")
        detail = self.client.get(reverse("backoffice:instance", args=[inst.pk]))
        self.assertContains(detail, "mokoena.example.co.za")
        self.assertContains(detail, "Mark live")
        self.assertEqual(self.client.get(reverse("backoffice:instance_edit", args=[inst.pk])).status_code, 200)

    def test_list_filters(self):
        live = make_instance(status=S.LIVE, url="https://live.example.org")
        waiting = make_instance(live_subscription(email="w@example.com"))
        retired = make_instance(
            live_subscription(email="r@example.com"), status=S.RETIRED, url="https://old.example.org"
        )
        url = reverse("backoffice:instances")
        ids = lambda **q: {i.pk for i in self.client.get(url, q).context["page"]}  # noqa: E731
        self.assertEqual(ids(), {live.pk, waiting.pk})
        self.assertEqual(ids(status="open"), {waiting.pk})
        self.assertEqual(ids(status="retired"), {retired.pk})
        self.assertEqual(ids(show="all"), {live.pk, waiting.pk, retired.pk})
        self.assertEqual(ids(q="live.example"), {live.pk})

    def test_outdated_and_attention_filters(self):
        old = make_instance(status=S.LIVE, version="1.0.0")
        make_instance(live_subscription(email="n@example.com"), status=S.LIVE, version="1.1.0")
        url = reverse("backoffice:instances")
        with override_settings(FUNERA_LATEST_VERSION="1.1.0"):
            self.assertEqual({i.pk for i in self.client.get(url, {"show": "outdated"}).context["page"]}, {old.pk})
            self.assertEqual({i.pk for i in self.client.get(url, {"show": "attention"}).context["page"]}, {old.pk})

    def test_missing_records_are_listed_with_add_link(self):
        sub = live_subscription()
        response = self.client.get(reverse("backoffice:instances"))
        self.assertEqual(list(response.context["missing"]), [sub])
        self.assertContains(response, f"?subscription={sub.pk}")
        make_instance(sub)
        self.assertEqual(list(self.client.get(reverse("backoffice:instances")).context["missing"]), [])

    def test_pagination(self):
        for n in range(30):
            make_instance(live_subscription(email=f"p{n}@example.com"))
        response = self.client.get(reverse("backoffice:instances"), {"page": 2})
        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(response.context["page"]), 0)

    def test_create_from_subscription_and_audit(self):
        sub = live_subscription()
        url = reverse("backoffice:instance_new") + f"?subscription={sub.pk}"
        self.assertEqual(self.client.get(url).context["form"].initial["subscription"], sub)
        response = self.client.post(
            url, {"status": S.SETTING_UP, "subscription": sub.pk, "url": "https://x.example.org", "version": "1.0.0"}
        )
        inst = Instance.objects.get(subscription=sub)
        self.assertRedirects(response, reverse("backoffice:instance", args=[inst.pk]))
        self.assertEqual((inst.customer, inst.status), (sub.customer, S.SETTING_UP))
        self.assertTrue(AuditEntry.objects.filter(action="instance.created", object_id=inst.pk).exists())

    def test_create_from_customer_and_edit_audit(self):
        sub = live_subscription()
        self.client.post(reverse("backoffice:instance_new") + f"?customer={sub.customer.pk}", {"status": S.AWAITING})
        inst = Instance.objects.get(customer=sub.customer)
        self.client.post(reverse("backoffice:instance_edit", args=[inst.pk]), {"status": S.LIVE})
        inst.refresh_from_db()
        self.assertEqual(inst.status, S.LIVE)
        self.assertTrue(AuditEntry.objects.filter(action="instance.edited", object_id=inst.pk).exists())

    def test_new_without_customer_redirects_to_list(self):
        response = self.client.get(reverse("backoffice:instance_new"))
        self.assertRedirects(response, reverse("backoffice:instances"))

    def test_invalid_form_shows_errors(self):
        inst = make_instance()
        response = self.client.post(
            reverse("backoffice:instance_edit", args=[inst.pk]), {"status": S.LIVE, "url": "not a url"}
        )
        self.assertContains(response, "Please correct the errors")

    def test_mark_live(self):
        inst = make_instance()
        self.client.post(reverse("backoffice:instance_action", args=[inst.pk, "live"]))
        inst.refresh_from_db()
        self.assertEqual((inst.status, inst.went_live_on), (S.LIVE, timezone.localdate()))
        self.assertTrue(AuditEntry.objects.filter(action="instance.live", object_id=inst.pk).exists())

    def test_record_update_needs_a_configured_version(self):
        inst = make_instance(version="1.0.0")
        url = reverse("backoffice:instance_action", args=[inst.pk, "updated"])
        self.client.post(url)
        inst.refresh_from_db()
        self.assertEqual(inst.version, "1.0.0")
        with override_settings(FUNERA_LATEST_VERSION="1.1.0"):
            self.client.post(url)
        inst.refresh_from_db()
        self.assertEqual((inst.version, inst.version_updated_on), ("1.1.0", timezone.localdate()))
        self.assertTrue(AuditEntry.objects.filter(action="instance.version_updated").exists())

    def test_quick_actions_are_post_only_and_unknown_is_404(self):
        inst = make_instance()
        self.assertEqual(
            self.client.get(reverse("backoffice:instance_action", args=[inst.pk, "live"])).status_code, 405
        )
        self.assertEqual(
            self.client.post(reverse("backoffice:instance_action", args=[inst.pk, "nope"])).status_code, 404
        )

    def test_links_from_customer_and_subscription_pages(self):
        sub = live_subscription()
        add = reverse("backoffice:instance_new")
        self.assertContains(self.client.get(reverse("backoffice:customer", args=[sub.customer.pk])), f"{add}?customer=")
        self.assertContains(self.client.get(reverse("backoffice:subscription", args=[sub.pk])), f"{add}?subscription=")
        inst = make_instance(sub)
        link = reverse("backoffice:instance", args=[inst.pk])
        self.assertContains(self.client.get(reverse("backoffice:customer", args=[sub.customer.pk])), link)
        self.assertContains(self.client.get(reverse("backoffice:subscription", args=[sub.pk])), link)

    def test_nav_link(self):
        self.assertContains(self.client.get(reverse("backoffice:dashboard")), reverse("backoffice:instances"))


class DashboardTests(StaffTestCase):
    def test_waiting_attention_and_missing_rows(self):
        stale = backdate(make_instance(), 5)
        live_subscription(email="m@example.com")  # paying, no record
        past_due = live_subscription(email="pd@example.com")
        make_instance(past_due, status=S.LIVE)
        Subscription.objects.filter(pk=past_due.pk).update(status=Subscription.Status.PAST_DUE)
        response = self.client.get(reverse("backoffice:dashboard"))
        self.assertContains(response, "Mokoena Funerals")
        stats = response.context["stats"] if "stats" in response.context else response.context
        self.assertEqual(stats["instances_waiting_count"], 1)
        self.assertEqual([i.pk for i in stats["instances_waiting"]], [stale.pk])
        self.assertEqual(len(stats["instances_attention"]), 1)
        self.assertEqual(stats["instances_missing"], 1)  # only the paying customer with no record


class RoleTests(TestCase):
    def test_sales_can_look_but_not_change(self):
        inst = make_instance()
        self.client.force_login(make_user("sales"))
        self.assertEqual(self.client.get(reverse("backoffice:instances")).status_code, 200)
        detail = self.client.get(reverse("backoffice:instance", args=[inst.pk]))
        self.assertEqual(detail.status_code, 200)
        self.assertNotContains(detail, "Mark live")
        for name, args in (("instance_edit", [inst.pk]), ("instance_new", []), ("instance_action", [inst.pk, "live"])):
            with self.subTest(name=name):
                url = reverse(f"backoffice:{name}", args=args) + ("?customer=1" if name == "instance_new" else "")
                self.assertEqual(self.client.post(url, {"status": S.LIVE}).status_code, 403)
        inst.refresh_from_db()
        self.assertEqual(inst.status, S.AWAITING)

    def test_anonymous_is_sent_to_login(self):
        self.assertEqual(self.client.get(reverse("backoffice:instances")).status_code, 302)
        self.assertEqual(self.client.post(reverse("backoffice:instance_action", args=[1, "live"])).status_code, 302)
