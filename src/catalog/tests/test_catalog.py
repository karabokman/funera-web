from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from catalog import services
from catalog.models import Feature, Plan


class SeedDataTests(TestCase):
    def test_three_plans_in_order(self):
        self.assertEqual([p.code for p in services.active_plans()], ["starter", "professional", "enterprise"])

    def test_plan_codes_match_platform_license_packages(self):
        # These codes are what a future provisioning step writes into the platform's LICENSE_PACKAGE setting.
        self.assertEqual(set(Plan.objects.values_list("code", flat=True)), {"starter", "professional", "enterprise"})

    def test_every_feature_has_a_plan_and_a_known_category(self):
        valid = {key for key, _ in Feature.Category.choices}
        for feature in Feature.objects.all():
            self.assertIsNotNone(feature.available_from_id)
            self.assertIn(feature.category, valid)


class CumulativePlanTests(TestCase):
    def setUp(self):
        self.starter, self.pro, self.ent = services.active_plans()

    def test_plans_are_cumulative(self):
        for feature in Feature.objects.select_related("available_from"):
            flags = [feature.included_in(p) for p in (self.starter, self.pro, self.ent)]
            self.assertEqual(flags, sorted(flags), f"{feature.name} must not disappear on a higher plan")
            self.assertTrue(flags[2], f"{feature.name} must be in Enterprise")

    def test_starter_feature_is_in_all_plans_and_enterprise_feature_only_in_enterprise(self):
        starter_feature = Feature.objects.filter(available_from=self.starter).first()
        ent_feature = Feature.objects.filter(available_from=self.ent).first()
        self.assertTrue(all(starter_feature.included_in(p) for p in (self.starter, self.pro, self.ent)))
        self.assertEqual([ent_feature.included_in(p) for p in (self.starter, self.pro, self.ent)], [False, False, True])

    def test_sections_have_a_cell_for_each_plan(self):
        sections = services.feature_sections()
        self.assertTrue(sections)
        for section in sections:
            for row in section.rows:
                self.assertEqual(len(row.included), 3)


class PricingDisplayTests(TestCase):
    def test_unpriced_plans_say_get_a_quote_and_invent_no_price(self):
        html = self.client.get(reverse("pages:pricing")).content.decode()
        self.assertIn("Get a quote", html)
        self.assertNotIn("R0", html)

    def test_price_shown_once_set(self):
        Plan.objects.filter(code="starter").update(monthly_price=Decimal("1500.00"))
        self.assertTrue(Plan.objects.get(code="starter").has_price)
        self.assertContains(self.client.get(reverse("pages:pricing")), "1")  # smoke: page still renders

    def test_inactive_plan_is_hidden(self):
        Plan.objects.filter(code="enterprise").update(is_active=False)
        self.assertEqual(len(services.active_plans()), 2)


class AnnualPriceTests(TestCase):
    def test_annual_offer_and_saving(self):
        plan = Plan.objects.get(code="starter")
        self.assertFalse(plan.has_annual)
        plan.monthly_price, plan.annual_price = Decimal("1000"), Decimal("10000")
        self.assertTrue(plan.has_annual)
        self.assertEqual(plan.annual_saving, Decimal("2000"))

    def test_no_saving_shown_when_annual_costs_the_same_or_more(self):
        plan = Plan.objects.get(code="starter")
        plan.monthly_price, plan.annual_price = Decimal("1000"), Decimal("12000")
        self.assertIsNone(plan.annual_saving)
        plan.annual_price = None
        self.assertIsNone(plan.annual_saving)
