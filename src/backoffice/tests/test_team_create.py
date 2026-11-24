import re

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase
from django.urls import reverse

from backoffice import roles
from backoffice.models import AuditEntry

from .test_backoffice import StaffTestCase
from .test_roles import make_user

User = get_user_model()
NEW = {
    "username": "thandi",
    "first_name": "Thandi",
    "last_name": "Dube",
    "email": "Thandi@Example.com",
    "role": "Sales",
}


class CreateTests(StaffTestCase):
    url = reverse("backoffice:team_new")

    def test_admin_adds_sales_who_gets_an_invitation_and_no_password(self):
        response = self.client.post(self.url, NEW)
        self.assertRedirects(response, reverse("backoffice:team"))
        user = User.objects.get(username="thandi")
        self.assertEqual((user.is_staff, user.is_active, user.is_superuser), (True, True, False))
        self.assertFalse(user.has_usable_password())
        self.assertEqual(user.email, "thandi@example.com")
        self.assertEqual(roles.role(user), "Sales")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["thandi@example.com"])
        self.assertIn("thandi", mail.outbox[0].body)
        self.assertNotIn("password:", mail.outbox[0].body.lower())

    def test_admin_can_add_another_admin_but_never_a_superuser(self):
        self.client.post(self.url, {**NEW, "role": "Admin", "is_superuser": "on", "is_staff": ""})
        user = User.objects.get(username="thandi")
        self.assertEqual(roles.role(user), "Admin")
        self.assertFalse(user.is_superuser)
        self.assertTrue(user.groups.filter(name=roles.ADMIN_GROUP).exists())

    def test_validation(self):
        make_user("taken")
        for field, value, message in (
            ("username", "TAKEN", "already taken"),
            ("email", "taken@example.com", "already has a login"),
            ("role", "Owner", "valid choice"),
            ("first_name", "", "required"),
        ):
            with self.subTest(field=field):
                response = self.client.post(self.url, {**NEW, field: value})
                self.assertContains(response, message)
        self.assertFalse(User.objects.filter(username="thandi").exists())

    def test_audit_entry_and_flash(self):
        response = self.client.post(self.url, NEW, follow=True)
        self.assertContains(response, "thandi was added as Sales")
        entry = AuditEntry.objects.get(action="team.created")
        self.assertEqual((entry.object_label, entry.actor_name), ("thandi", "sam"))
        self.assertIn("Sales", entry.detail)

    def test_failed_email_still_creates_the_user_and_shows_the_link(self):
        from unittest import mock

        with mock.patch("django.core.mail.EmailMessage.send", side_effect=OSError("smtp down")):
            with self.assertLogs("backoffice.views", level="ERROR"):
                response = self.client.post(self.url, NEW, follow=True)
        self.assertContains(response, "e-mail could not be sent")
        self.assertContains(response, "/set-password/")
        self.assertTrue(AuditEntry.objects.filter(action="team.invite_failed").exists())
        self.assertTrue(User.objects.filter(username="thandi").exists())

    def test_form_and_team_page_link(self):
        self.assertContains(self.client.get(self.url), "Add and send invitation")
        self.assertContains(self.client.get(reverse("backoffice:team")), self.url)


class AccessTests(TestCase):
    url = reverse("backoffice:team_new")

    def test_sales_cannot_see_or_use_it(self):
        self.client.force_login(make_user("sales"))
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url, NEW).status_code, 403)
        self.assertFalse(User.objects.filter(username="thandi").exists())
        self.assertEqual(self.client.get(reverse("backoffice:team")).status_code, 403)

    def test_anonymous_goes_to_login(self):
        self.assertEqual(self.client.post(self.url, NEW).status_code, 302)
        self.assertFalse(User.objects.filter(username="thandi").exists())

    def test_non_staff_is_refused(self):
        self.client.force_login(make_user("client", staff=False))
        self.assertEqual(self.client.post(self.url, NEW).status_code, 403)

    def test_group_membership_alone_does_not_let_a_non_staff_user_create(self):
        self.client.force_login(make_user("g", admin=True, staff=False))
        self.assertEqual(self.client.post(self.url, NEW).status_code, 403)


class InvitationLinkTests(StaffTestCase):
    def invite(self):
        self.client.post(reverse("backoffice:team_new"), NEW)
        link = re.search(r"https?://\S+/set-password/\S+/", mail.outbox[0].body).group(0)
        self.client.logout()
        return link

    def test_new_person_chooses_a_password_and_can_sign_in(self):
        link = self.invite()
        page = self.client.get(link, follow=True)
        self.assertContains(page, "Choose your password")
        done = self.client.post(
            page.redirect_chain[-1][0] if page.redirect_chain else link,
            {"new_password1": "a-long-Safe-pass-77", "new_password2": "a-long-Safe-pass-77"},
            follow=True,
        )
        self.assertContains(done, "Your password is set")
        self.assertTrue(self.client.login(username="thandi", password="a-long-Safe-pass-77"))
        self.assertEqual(self.client.get(reverse("backoffice:dashboard")).status_code, 200)
        self.assertTrue(AuditEntry.objects.filter(action="team.password_set", actor_name="thandi").exists())

    def test_weak_password_is_refused(self):
        link = self.invite()
        page = self.client.get(link, follow=True)
        response = self.client.post(
            page.redirect_chain[-1][0], {"new_password1": "12345678", "new_password2": "12345678"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.get(username="thandi").has_usable_password())

    def test_link_works_once(self):
        link = self.invite()
        page = self.client.get(link, follow=True)
        self.client.post(
            page.redirect_chain[-1][0], {"new_password1": "a-long-Safe-pass-77", "new_password2": "a-long-Safe-pass-77"}
        )
        self.assertContains(self.client.get(link, follow=True), "already been used or has expired")

    def test_tampered_link_is_refused(self):
        link = self.invite()
        bad = link[:-3] + "zz/"
        self.assertContains(self.client.get(bad, follow=True), "already been used or has expired")

    def test_unknown_user_cannot_sign_in_before_choosing(self):
        self.invite()
        self.assertFalse(self.client.login(username="thandi", password=""))
