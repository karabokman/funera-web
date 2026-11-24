from datetime import timedelta
from io import StringIO

from django.contrib.admin.models import LogEntry  # noqa: F401
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from backoffice import audit, audit_log, roles
from backoffice.models import AuditEntry
from billing.models import SubscriptionEvent
from billing.tests.helpers import live_subscription
from quotes.tests.helpers import make_quote

from .test_backoffice import StaffTestCase
from .test_roles import PW, make_user

User = get_user_model()


def many_entries(n):
    AuditEntry.objects.bulk_create(
        AuditEntry(actor_name="sam", action="lead.updated", object_type="lead", object_label=f"Lead {i}")
        for i in range(n)
    )


class PagingTests(StaffTestCase):
    def test_every_entry_is_reachable_without_a_cap(self):
        many_entries(1100)
        AuditEntry.objects.filter(object_label="Lead 0").update(created_at=timezone.now() - timedelta(days=30))
        feed = audit_log.Feed(audit_log.Filters())
        self.assertGreaterEqual(len(feed), 1100)
        last = self.client.get(reverse("backoffice:audit"), {"page": 9999}).context["page"]
        self.assertEqual(last.number, last.paginator.num_pages)
        self.assertTrue(any(r["label"] == "Lead 0" for r in last.object_list))

    def test_sources_are_merged_newest_first_across_pages(self):
        now = timezone.now()
        sub = live_subscription()
        for i in range(30):
            SubscriptionEvent.objects.create(subscription=sub, kind=f"note{i}", actor="payfast")
            SubscriptionEvent.objects.filter(kind=f"note{i}").update(created_at=now - timedelta(minutes=2 * i))
            AuditEntry.objects.create(actor_name="sam", action="lead.updated", object_label=f"L{i}")
            AuditEntry.objects.filter(object_label=f"L{i}").update(created_at=now - timedelta(minutes=2 * i + 1))
        feed = audit_log.Feed(audit_log.Filters(source=""))
        everything = feed[0 : len(feed)]
        whens = [r["when"] for r in everything]
        self.assertEqual(whens, sorted(whens, reverse=True))
        self.assertEqual(feed[10:20], everything[10:20])

    def test_filters_are_applied_in_the_database(self):
        make_quote()
        AuditEntry.objects.create(actor_name="ann", action="quote.sent", object_type="quote", object_label="Q-1 · Zed")
        AuditEntry.objects.create(actor_name="bob", action="lead.updated", object_type="lead", object_label="Acme")
        f = audit_log.Filters(who="ann")
        self.assertEqual([r["who"] for r in audit_log.rows(f)], ["ann"])
        self.assertEqual(len(audit_log.rows(audit_log.Filters(q="Acme", source="staff"))), 1)
        self.assertEqual(len(audit_log.rows(audit_log.Filters(area="quote", source="staff"))), 1)

    def test_legacy_staff_events_are_still_labelled(self):
        AuditEntry.objects.all().delete()  # the sign-in made by the test login would otherwise mark the start of the log
        sub = live_subscription()
        SubscriptionEvent.objects.create(subscription=sub, kind="paused", actor="staff")
        self.assertEqual(audit_log.rows(audit_log.Filters(source="client"))[0]["who"], "staff (before the audit log)")
        AuditEntry.objects.create(actor_name="sam", action="x", created_at=timezone.now() - timedelta(days=1))
        SubscriptionEvent.objects.create(subscription=sub, kind="resumed", actor="staff")
        who = [r["who"] for r in audit_log.rows(audit_log.Filters(source="client"))]
        self.assertEqual(who.count("staff (before the audit log)"), 1)  # the newer one is already logged with a name

    def test_page_no_longer_shows_a_cap_notice(self):
        many_entries(30)
        self.assertNotContains(self.client.get(reverse("backoffice:audit")), "Showing the newest")


class RetentionTests(StaffTestCase):
    def old(self, days=800):
        e = AuditEntry.objects.create(actor_name="old", action="lead.updated")
        AuditEntry.objects.filter(pk=e.pk).update(created_at=timezone.now() - timedelta(days=days))
        return e

    def test_purge_deletes_only_expired_entries_and_logs_itself(self):
        old, fresh = self.old(), AuditEntry.objects.create(actor_name="new", action="lead.updated")
        self.assertEqual(audit.purge(user=self.staff), 1)
        self.assertFalse(AuditEntry.objects.filter(pk=old.pk).exists())
        self.assertTrue(AuditEntry.objects.filter(pk=fresh.pk).exists())
        note = AuditEntry.objects.get(action="audit.purged")
        self.assertIn("1 entries older than 24 months", note.detail)
        self.assertEqual(note.actor_name, "sam")

    def test_zero_months_keeps_everything(self):
        self.old(5000)
        with override_settings(AUDIT_RETENTION_MONTHS=0):
            self.assertEqual(audit.purge(), 0)
        self.assertEqual(AuditEntry.objects.filter(actor_name="old").count(), 1)

    def test_button_appears_only_when_something_is_due_and_works(self):
        url = reverse("backoffice:audit")
        self.assertNotContains(self.client.get(url), "Delete them now")
        self.old()
        self.assertContains(self.client.get(url), "Delete them now")
        self.assertEqual(self.client.get(reverse("backoffice:audit_purge")).status_code, 405)
        self.assertRedirects(self.client.post(reverse("backoffice:audit_purge")), url)
        self.assertFalse(AuditEntry.objects.filter(actor_name="old").exists())

    def test_sales_cannot_purge(self):
        self.old()
        self.client.force_login(make_user("sales"))
        self.assertEqual(self.client.post(reverse("backoffice:audit_purge")).status_code, 403)
        self.assertTrue(AuditEntry.objects.filter(actor_name="old").exists())

    def test_management_command(self):
        self.old()
        out = StringIO()
        call_command("purge_audit_log", "--dry-run", stdout=out)
        self.assertIn("1 entries are older", out.getvalue())
        self.assertTrue(AuditEntry.objects.filter(actor_name="old").exists())
        call_command("purge_audit_log", stdout=out)
        self.assertFalse(AuditEntry.objects.filter(actor_name="old").exists())
        with override_settings(AUDIT_RETENTION_MONTHS=0):
            call_command("purge_audit_log", stdout=out)
        self.assertIn("keep everything", out.getvalue())


class SiteAdminChangesTests(TestCase):
    def setUp(self):
        self.root = User.objects.create_superuser("root", "root@example.com", PW)
        self.client.force_login(self.root)

    def entries(self, action):
        return AuditEntry.objects.filter(action=action)

    def test_user_created_changed_and_role_change_are_logged(self):
        response = self.client.post(
            reverse("admin:auth_user_add"), {"username": "newbie", "password1": PW, "password2": PW}, follow=True
        )
        self.assertEqual(response.status_code, 200)
        user = User.objects.get(username="newbie")
        created = self.entries("user.created").get()
        self.assertEqual((created.actor_name, created.object_label), ("root", "newbie"))
        group = roles.admin_group()
        data = {
            "username": "newbie", "is_active": "on", "is_staff": "on", "groups": [group.pk],
            "date_joined_0": "2026-01-01", "date_joined_1": "00:00:00",
        }  # fmt: skip
        self.client.post(reverse("admin:auth_user_change", args=[user.pk]), data)
        changed = self.entries("user.changed").get()
        self.assertIn("Role now: Admin", changed.detail)
        self.assertEqual(changed.object_type, "user")

    def test_user_delete_and_password_change_are_logged(self):
        user = User.objects.create_user("gone", "g@example.com", PW)
        self.client.post(
            reverse("admin:auth_user_password_change", args=[user.pk]),
            {"password1": "another-pass-9876", "password2": "another-pass-9876"},
        )
        self.assertIn("password", self.entries("user.changed").get().detail.lower())
        self.assertNotIn("another-pass", self.entries("user.changed").get().detail)
        self.client.post(reverse("admin:auth_user_delete", args=[user.pk]), {"post": "yes"})
        self.assertEqual(self.entries("user.deleted").get().object_label, "gone")

    def test_group_changes_are_logged(self):
        self.client.post(reverse("admin:auth_group_add"), {"name": "Auditors", "permissions": []})
        group = Group.objects.get(name="Auditors")
        self.assertEqual(self.entries("group.created").get().object_label, "Auditors")
        self.client.post(reverse("admin:auth_group_delete", args=[group.pk]), {"post": "yes"})
        self.assertEqual(self.entries("group.deleted").get().object_label, "Auditors")

    def test_changes_show_on_the_audit_page_under_users_and_roles(self):
        self.client.post(reverse("admin:auth_group_add"), {"name": "Auditors", "permissions": []})
        rows = audit_log.rows(audit_log.Filters(area="user"))
        self.assertEqual([r["what"] for r in rows], ["Created a group (site admin)"])


class PrivacyAndNavTests(StaffTestCase):
    def test_privacy_notice_describes_staff_logging(self):
        page = self.client.get(reverse("pages:privacy")).content.decode() if self._has("pages:privacy") else ""
        self.assertIn("IP address", page)
        self.assertIn("username", page)
        self.assertIn("deleted after 24 months", page)

    def _has(self, name):
        try:
            reverse(name)
            return True
        except Exception:
            return False

    def test_notice_version_was_bumped(self):
        from django.conf import settings

        self.assertNotEqual(settings.PRIVACY_NOTICE_VERSION, "1.0")

    def test_admin_menu_groups_the_admin_pages(self):
        page = self.client.get(reverse("backoffice:dashboard")).content.decode()
        for name in ("refunds", "audit", "team"):
            self.assertIn(reverse(f"backoffice:{name}"), page)
        self.assertIn("Admin", page)
        self.assertIn("Sign out", page)


class ContentSecurityPolicyTests(StaffTestCase):
    """The site's CSP blocks inline scripts and handlers, so menus and confirm prompts live in static/js/site.js."""

    def test_no_inline_scripts_or_handlers_in_the_back_office_pages(self):
        old = AuditEntry.objects.create(actor_name="old", action="lead.updated")
        AuditEntry.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=900))
        page = self.client.get(reverse("backoffice:audit")).content.decode()
        self.assertNotIn("onsubmit=", page)
        self.assertNotIn("<script>", page)
        self.assertIn("data-confirm=", page)

    def test_site_script_handles_menus_and_confirmations(self):
        from pathlib import Path

        from django.conf import settings

        js = (Path(settings.BASE_DIR) / "static" / "js" / "site.js").read_text(encoding="utf-8")
        for needle in ("details[data-menu]", "data-confirm", "Escape"):
            self.assertIn(needle, js)
