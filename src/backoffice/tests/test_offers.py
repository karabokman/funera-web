from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from catalog.models import Plan
from quotes import services
from quotes.errors import QuoteError
from quotes.models import Quote, QuoteTemplate, QuoteTemplateLine
from quotes.tests.helpers import make_lead, make_quote

from .test_backoffice import StaffTestCase, quote_post


def make_offer(name="Starter, 10 branches, standard set-up", **extra):
    offer = QuoteTemplate.objects.create(
        name=name,
        plan=Plan.objects.get(code="starter"),
        billing_period=extra.pop("billing_period", "monthly"),
        recurring_price=extra.pop("recurring_price", Decimal("1150.00")),
        intro=extra.pop("intro", "Thank you for your interest."),
        **extra,
    )
    QuoteTemplateLine.objects.create(
        template=offer, description="Set-up and data import", quantity=1, unit_price=Decimal("3450.00"), order=0
    )
    QuoteTemplateLine.objects.create(
        template=offer, description="Staff training", quantity=2, unit_price=Decimal("500.00"), order=1
    )
    return offer


def offer_post(**extra):
    data = {
        "name": "Professional, annual", "description": "For parlours with several branches", "plan": Plan.objects.get(code="professional").pk,
        "billing_period": "annual", "recurring_price": "14990.00", "vat_included": "on", "valid_days": "14", "intro": "Hello", "is_active": "on",
        "lines-TOTAL_FORMS": "3", "lines-INITIAL_FORMS": "0", "lines-MIN_NUM_FORMS": "0", "lines-MAX_NUM_FORMS": "1000",
        "lines-0-description": "Set-up", "lines-0-quantity": "1", "lines-0-unit_price": "3500.00",
        "lines-1-description": "", "lines-1-quantity": "1", "lines-1-unit_price": "",
        "lines-2-description": "", "lines-2-quantity": "1", "lines-2-unit_price": "",
    }  # fmt: skip
    data.update(extra)
    return data


class ServiceTests(TestCase):
    def test_initial_from_offer_fills_price_wording_lines_and_validity(self):
        offer = make_offer(valid_days=10)
        initial, lines = services.initial_for(None, make_lead(), offer)
        self.assertEqual(
            (initial["plan"].code, initial["recurring_price"], initial["billing_period"], initial["vat_included"]),
            ("starter", Decimal("1150.00"), "monthly", True),
        )
        self.assertEqual(
            (initial["intro"], initial["valid_until"]),
            ("Thank you for your interest.", timezone.localdate() + timedelta(days=10)),
        )
        self.assertEqual(
            [(line["description"], line["quantity"], line["unit_price"]) for line in lines],
            [("Set-up and data import", 1, Decimal("3450.00")), ("Staff training", 2, Decimal("500.00"))],
        )
        self.assertEqual((initial["business_name"], initial["email"]), ("Mokoena Funerals", "thandi@example.com"))

    def test_validity_defaults_when_offer_has_none(self):
        initial, _ = services.initial_for(None, None, make_offer())
        self.assertEqual(initial["valid_until"], timezone.localdate() + timedelta(days=30))

    def test_save_from_quote_copies_offer_details_but_never_the_client(self):
        q = make_quote(
            price="14990.00",
            billing_period="annual",
            vat_included=False,
            intro="Hi",
            lines=(("Set-up", 1, "2500.00"), ("Discount", 1, "-500.00")),
        )
        offer = services.save_template_from_quote(q, "  Pro annual  ", None)
        self.assertEqual(
            (offer.name, offer.plan, offer.billing_period, offer.recurring_price, offer.vat_included, offer.intro),
            ("Pro annual", q.plan, "annual", Decimal("14990.00"), False, "Hi"),
        )
        self.assertEqual(
            [(line.description, line.unit_price) for line in offer.lines.all()],
            [("Set-up", Decimal("2500.00")), ("Discount", Decimal("-500.00"))],
        )
        self.assertFalse(hasattr(offer, "email") or hasattr(offer, "business_name"))
        self.assertEqual(offer.once_off_total, Decimal("2000.00"))

    def test_save_from_quote_needs_a_new_name(self):
        q = make_quote()
        services.save_template_from_quote(q, "Offer", None)
        for name in ("", "   ", "offer"):
            with self.subTest(name=name), self.assertRaises(QuoteError):
                services.save_template_from_quote(q, name, None)

    def test_deleting_an_offer_leaves_existing_quotes_intact(self):
        offer = make_offer()
        initial, _ = services.initial_for(None, None, offer)
        q = make_quote(price=str(initial["recurring_price"]))
        offer.delete()
        q.refresh_from_db()
        self.assertEqual(q.recurring_price, Decimal("1150.00"))


class OfferScreenTests(StaffTestCase):
    def test_list_shows_totals_and_hidden_state(self):
        make_offer()
        make_offer("Old offer", is_active=False)
        page = self.client.get(reverse("backoffice:offers"))
        self.assertContains(page, "Starter, 10 branches, standard set-up")
        self.assertContains(page, "Hidden")
        self.assertContains(page, "1,150.00")
        self.assertContains(page, "4,450.00")

    def test_create_with_lines(self):
        response = self.client.post(reverse("backoffice:offer_new"), offer_post())
        self.assertRedirects(response, reverse("backoffice:offers"))
        offer = QuoteTemplate.objects.get()
        self.assertEqual(
            (offer.name, offer.valid_days, offer.created_by, offer.lines.count(), offer.billing_period),
            ("Professional, annual", 14, self.staff, 1, "annual"),
        )

    def test_validation(self):
        make_offer()
        for bad in (
            {"name": ""},
            {"name": "starter, 10 branches, standard set-up"},
            {"recurring_price": "0"},
            {"recurring_price": "-1"},
            {"valid_days": "0"},
        ):
            with self.subTest(bad=bad):
                response = self.client.post(reverse("backoffice:offer_new"), offer_post(**bad))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Please correct the errors")
        self.assertEqual(QuoteTemplate.objects.count(), 1)

    def test_edit_changes_price_and_removes_a_line(self):
        offer = make_offer()
        first, second = offer.lines.all()
        data = offer_post(
            name=offer.name, recurring_price="1299.00", **{
                "lines-TOTAL_FORMS": "2", "lines-INITIAL_FORMS": "2",
                "lines-0-id": first.pk, "lines-0-template": offer.pk, "lines-0-description": first.description, "lines-0-quantity": "1", "lines-0-unit_price": "3450.00",
                "lines-1-id": second.pk, "lines-1-template": offer.pk, "lines-1-description": second.description, "lines-1-quantity": "2", "lines-1-unit_price": "500.00", "lines-1-DELETE": "on",
            },
        )  # fmt: skip
        data.pop("lines-2-description"), data.pop("lines-2-quantity"), data.pop("lines-2-unit_price")
        self.client.post(reverse("backoffice:offer_edit", args=[offer.pk]), data)
        offer.refresh_from_db()
        self.assertEqual((offer.recurring_price, offer.lines.count()), (Decimal("1299.00"), 1))

    def test_delete_is_post_only(self):
        offer = make_offer()
        self.assertEqual(self.client.get(reverse("backoffice:offer_delete", args=[offer.pk])).status_code, 405)
        self.client.post(reverse("backoffice:offer_delete", args=[offer.pk]))
        self.assertFalse(QuoteTemplate.objects.exists())

    def test_staff_only(self):
        offer = make_offer()
        self.client.logout()
        for url in (
            reverse("backoffice:offers"),
            reverse("backoffice:offer_new"),
            reverse("backoffice:offer_edit", args=[offer.pk]),
        ):
            self.assertEqual(self.client.get(url).status_code, 302)
        self.assertEqual(self.client.post(reverse("backoffice:offer_delete", args=[offer.pk])).status_code, 302)
        self.assertTrue(QuoteTemplate.objects.exists())


class StartingAQuoteFromAnOfferTests(StaffTestCase):
    def test_form_is_prefilled_from_the_offer_and_lead(self):
        offer, lead = make_offer(), make_lead()
        response = self.client.get(reverse("backoffice:quote_new"), {"lead": lead.pk, "offer": offer.pk})
        form, lines = response.context["form"], response.context["lines"]
        self.assertEqual(
            (form.initial["recurring_price"], form.initial["business_name"], form.initial["intro"]),
            (Decimal("1150.00"), "Mokoena Funerals", "Thank you for your interest."),
        )
        self.assertEqual(
            [f.initial["description"] for f in lines.forms[:2]], ["Set-up and data import", "Staff training"]
        )
        self.assertContains(response, 'Filled in from "Starter, 10 branches, standard set-up"')

    def test_picker_lists_only_active_offers(self):
        make_offer()
        make_offer("Hidden one", is_active=False)
        page = self.client.get(reverse("backoffice:quote_new")).content.decode()
        self.assertIn("Start from a saved offer", page)
        self.assertIn("Starter, 10 branches, standard set-up", page)
        self.assertNotIn("Hidden one", page)

    def test_no_picker_without_offers_and_unknown_offer_is_ignored(self):
        self.assertNotContains(self.client.get(reverse("backoffice:quote_new")), "Start from a saved offer")
        for value in ("999", "abc"):
            self.assertEqual(self.client.get(reverse("backoffice:quote_new"), {"offer": value}).status_code, 200)

    def test_lead_page_offers_each_saved_offer(self):
        offer, lead = make_offer(), make_lead()
        self.assertContains(self.client.get(reverse("backoffice:lead", args=[lead.pk])), f"offer={offer.pk}")

    def test_thirty_second_quote_end_to_end(self):
        offer, lead = make_offer(), make_lead()
        page = self.client.get(reverse("backoffice:quote_new"), {"lead": lead.pk, "offer": offer.pk})
        form, lines = page.context["form"], page.context["lines"]
        data = {
            name: ("" if value is None else value)
            for name, value in form.initial.items()
            if name not in ("plan", "valid_until", "vat_included")
        }
        data.update(
            plan=form.initial["plan"].pk,
            valid_until=form.initial["valid_until"].isoformat(),
            vat_included="on",
            billing_period="monthly",
        )
        data.update(
            {
                "lines-TOTAL_FORMS": "3",
                "lines-INITIAL_FORMS": "0",
                "lines-MIN_NUM_FORMS": "0",
                "lines-MAX_NUM_FORMS": "1000",
            }
        )
        for i, line in enumerate(lines.initial_extra):
            data.update(
                {
                    f"lines-{i}-description": line["description"],
                    f"lines-{i}-quantity": line["quantity"],
                    f"lines-{i}-unit_price": line["unit_price"],
                }
            )
        data.update({"lines-2-description": "", "lines-2-quantity": "1", "lines-2-unit_price": ""})
        self.client.post(reverse("backoffice:quote_new") + f"?lead={lead.pk}&offer={offer.pk}", data)
        q = Quote.objects.get()
        self.assertEqual(
            (q.recurring_price, q.once_off_gross, q.first_payment_gross, q.lead),
            (Decimal("1150.00"), Decimal("4450.00"), Decimal("5600.00"), lead),
        )

    def test_save_as_offer_button(self):
        q = make_quote()
        response = self.client.post(reverse("backoffice:quote_save_offer", args=[q.pk]), {"name": "My offer"})
        offer = QuoteTemplate.objects.get()
        self.assertRedirects(response, reverse("backoffice:offer_edit", args=[offer.pk]))
        self.assertEqual(offer.lines.count(), 1)
        again = self.client.post(reverse("backoffice:quote_save_offer", args=[q.pk]), {"name": "my offer"}, follow=True)
        self.assertContains(again, "already exists")
        self.assertEqual(QuoteTemplate.objects.count(), 1)
        self.assertEqual(self.client.get(reverse("backoffice:quote_save_offer", args=[q.pk])).status_code, 405)

    def test_quote_detail_has_the_save_panel(self):
        self.assertContains(self.client.get(reverse("backoffice:quote", args=[make_quote().pk])), "Save as offer")


class PrefilledLinesTests(StaffTestCase):
    def test_untouched_prefilled_setup_fee_line_is_saved(self):
        plan = Plan.objects.get(code="professional")
        plan.monthly_price, plan.setup_fee = Decimal("1500.00"), Decimal("3500.00")
        plan.save()
        page = self.client.get(reverse("backoffice:quote_new"), {"plan": "professional"})
        lines = page.context["lines"]
        data = quote_post(recurring_price="1500.00")
        data.update(
            {
                "lines-TOTAL_FORMS": "2",
                "lines-0-description": lines.initial_extra[0]["description"],
                "lines-0-quantity": "1",
                "lines-0-unit_price": "3500.00",
            }
        )
        self.client.post(reverse("backoffice:quote_new"), data)
        self.assertEqual(Quote.objects.get().once_off_gross, Decimal("3500.00"))

    def test_blank_extra_lines_are_still_ignored(self):
        self.client.post(
            reverse("backoffice:quote_new"), quote_post(**{"lines-0-description": "", "lines-0-unit_price": ""})
        )
        self.assertEqual(Quote.objects.get().lines.count(), 0)

    def test_a_half_filled_line_is_an_error_not_silently_dropped(self):
        response = self.client.post(
            reverse("backoffice:quote_new"), quote_post(**{"lines-0-description": "Training", "lines-0-unit_price": ""})
        )
        self.assertContains(response, "Please correct the errors")
        self.assertFalse(Quote.objects.exists())
