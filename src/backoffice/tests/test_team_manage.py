import re
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase
from django.urls import reverse

from backoffice import roles
from backoffice.models import AuditEntry

from .test_backoffice import StaffTestCase
from .test_roles import make_user

User = get_user_model()


def edit_url(user):
    return reverse("backoffice:team_edit", args=[user.pk])


def data(user, **extra):
    base = {
        "first_name": user.first_name or "Pat", "last_name": user.last_name, "email": user.email,
        "role": roles.role(user), "is_active": "on" if user.is_active else "",
    }  # fmt: skip
    return {**base, **extra}


class EditTests(StaffTestCase):
    def test_promote_and_demote(self):
        pat = make_user("pat")
        self.client.post(edit_url(pat), data(pat, role="Admin"))
        self.assertEqual(roles.role(User.objects.get(pk=pat.pk)), "Admin")
        entry = AuditEntry.objects.get(action="team.edited")
        self.assertIn("role", entry.detail)
        self.assertEqual(entry.actor_name, "sam")
        self.client.post(edit_url(pat), data(pat, role="Sales"))
        self.assertEqual(roles.role(User.objects.get(pk=pat.pk)), "Sales")

    def test_details_and_disabling(self):
        pat = make_user("pat")
        self.client.post(edit_url(pat), data(pat, first_name="Patricia", email="New@Example.com", is_active=""))
        pat.refresh_from_db()
        self.assertEqual((pat.first_name, pat.email, pat.is_active), ("Patricia", "new@example.com", False))
        self.assertIn("is active", AuditEntry.objects.get(action="team.edited").detail)

    def test_disabled_user_is_locked_out_at_once(self):
        pat = make_user("pat")
        other = self.client_class()
        other.force_login(pat)
        self.assertEqual(other.get(reverse("backoffice:dashboard")).status_code, 200)
        self.client.post(edit_url(pat), data(pat, is_active=""))
        self.assertEqual(other.get(reverse("backoffice:dashboard")).status_code, 302)

    def test_no_change_writes_no_audit_entry(self):
        pat = make_user("pat")
        User.objects.filter(pk=pat.pk).update(first_name="Pat")
        pat.refresh_from_db()
        self.client.post(edit_url(pat), data(pat))
        self.assertFalse(AuditEntry.objects.filter(action="team.edited").exists())

    def test_validation(self):
        pat, other = make_user("pat"), make_user("other")
        for extra, message in (
            ({"email": other.email}, "already has a login"),
            ({"first_name": ""}, "required"),
            ({"role": "Owner"}, "valid choice"),
        ):
            with self.subTest(extra=extra):
                self.assertContains(self.client.post(edit_url(pat), data(pat, **extra)), message)

    def test_cannot_demote_or_disable_yourself(self):
        make_user("second", admin=True)  # so the "last admin" rule is not what stops it
        for extra in ({"role": "Sales"}, {"is_active": ""}):
            with self.subTest(extra=extra):
                self.assertContains(
                    self.client.post(edit_url(self.staff), data(self.staff, **extra)), "own admin access"
                )
        self.assertEqual(roles.role(User.objects.get(pk=self.staff.pk)), "Admin")

    def test_last_active_admin_is_protected(self):
        self.assertTrue(roles.would_leave_no_admin(self.staff))  # sam is the only Admin
        self.assertFalse(roles.would_leave_no_admin(make_user("pat")))  # Sales were never an admin
        second = make_user("second", admin=True)
        self.assertFalse(roles.would_leave_no_admin(self.staff))
        User.objects.filter(pk=second.pk).update(is_active=False)  # a disabled admin does not count
        self.assertTrue(roles.would_leave_no_admin(self.staff))

    def test_superusers_are_off_limits_unless_you_are_one(self):
        root = make_user("root", superuser=True)
        self.assertRedirects(self.client.post(edit_url(root), data(root, role="Sales")), reverse("backoffice:team"))
        self.assertTrue(User.objects.get(pk=root.pk).is_superuser)
        self.assertContains(self.client.get(reverse("backoffice:team")), "Site admin only")
        self.client.force_login(make_user("boss", superuser=True))
        self.assertEqual(self.client.get(edit_url(root)).status_code, 200)

    def test_only_team_members_can_be_edited(self):
        client_user = make_user("client", staff=False)
        self.assertEqual(self.client.get(edit_url(client_user)).status_code, 404)


class RemoveTests(StaffTestCase):
    def test_confirm_page_then_remove_keeps_the_account_but_ends_access(self):
        pat = make_user("pat", admin=True)
        url = reverse("backoffice:team_remove", args=[pat.pk])
        self.assertContains(self.client.get(url), "Remove access")
        self.assertRedirects(self.client.post(url), reverse("backoffice:team"))
        pat = User.objects.get(pk=pat.pk)
        self.assertEqual(
            (pat.is_staff, pat.is_active, pat.has_usable_password(), pat.groups.count()), (False, False, False, 0)
        )
        entry = AuditEntry.objects.get(action="team.removed")
        self.assertEqual((entry.object_label, entry.actor_name), ("pat", "sam"))
        self.assertNotContains(self.client.get(reverse("backoffice:team")), ">pat<")
        self.assertFalse(AuditEntry.objects.get(action="team.removed").actor == pat)

    def test_removed_person_is_locked_out(self):
        pat = make_user("pat")
        other = self.client_class()
        other.force_login(pat)
        self.client.post(reverse("backoffice:team_remove", args=[pat.pk]))
        self.assertEqual(other.get(reverse("backoffice:dashboard")).status_code, 302)

    def test_cannot_remove_yourself_a_superuser_or_the_last_admin(self):
        root = make_user("root", superuser=True)
        for target in (self.staff, root):
            with self.subTest(target=target.username):
                url = reverse("backoffice:team_remove", args=[target.pk])
                self.assertRedirects(self.client.post(url), reverse("backoffice:team"))
                self.assertTrue(User.objects.get(pk=target.pk).is_staff)

    def test_history_survives(self):
        pat = make_user("pat")
        AuditEntry.objects.create(actor=pat, actor_name="pat", action="lead.updated")
        self.client.post(reverse("backoffice:team_remove", args=[pat.pk]))
        self.assertEqual(AuditEntry.objects.filter(actor_name="pat", action="lead.updated").count(), 1)


class PasswordLinkTests(StaffTestCase):
    def url(self, user):
        return reverse("backoffice:team_password_link", args=[user.pk])

    def test_link_is_emailed_and_works(self):
        pat = make_user("pat")
        pat.set_password("old-password-123")
        pat.save()
        self.assertRedirects(self.client.post(self.url(pat)), reverse("backoffice:team"))
        link = re.search(r"https?://\S+/set-password/\S+/", mail.outbox[0].body).group(0)
        self.assertEqual(mail.outbox[0].to, ["pat@example.com"])
        self.assertTrue(AuditEntry.objects.filter(action="team.link_sent", object_label="pat").exists())
        self.client.logout()
        page = self.client.get(link, follow=True)
        self.client.post(
            page.redirect_chain[-1][0], {"new_password1": "a-long-Safe-pass-77", "new_password2": "a-long-Safe-pass-77"}
        )
        self.assertTrue(self.client.login(username="pat", password="a-long-Safe-pass-77"))

    def test_post_only_and_failures(self):
        pat = make_user("pat")
        self.assertEqual(self.client.get(self.url(pat)).status_code, 405)
        with mock.patch("django.core.mail.EmailMessage.send", side_effect=OSError("smtp down")):
            with self.assertLogs("backoffice.views", level="ERROR"):
                response = self.client.post(self.url(pat), follow=True)
        self.assertContains(response, "/set-password/")
        pat.is_active = False
        pat.save()
        self.assertContains(self.client.post(self.url(pat), follow=True), "cannot sign in at the moment")


class AccessTests(TestCase):
    def test_sales_and_anonymous_cannot_do_any_of_it(self):
        pat, sales = make_user("pat"), make_user("sales")
        urls = [
            edit_url(pat),
            reverse("backoffice:team_remove", args=[pat.pk]),
            reverse("backoffice:team_password_link", args=[pat.pk]),
        ]
        for client, expected in ((self.client_class(), 302), (self.client_class(), 403)):
            if expected == 403:
                client.force_login(sales)
            for url in urls:
                with self.subTest(url=url, expected=expected):
                    self.assertEqual(client.post(url, data(pat, role="Admin")).status_code, expected)
        pat.refresh_from_db()
        self.assertEqual((pat.is_staff, roles.role(pat)), (True, "Sales"))
        self.assertEqual(len(mail.outbox), 0)

    def test_team_page_shows_actions_to_admins(self):
        admin = make_user("boss", admin=True)
        self.client.force_login(admin)
        pat = make_user("pat")
        page = self.client.get(reverse("backoffice:team")).content.decode()
        for name in ("team_edit", "team_remove", "team_password_link"):
            self.assertIn(reverse(f"backoffice:{name}", args=[pat.pk]), page)
        self.assertNotIn(reverse("backoffice:team_remove", args=[admin.pk]), page)  # no "remove" for yourself
