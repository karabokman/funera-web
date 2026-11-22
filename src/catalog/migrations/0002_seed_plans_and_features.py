"""Starting catalogue: the three plans and the features of the Funera platform. Edit prices, wording and features in the admin."""
from django.db import migrations

PLANS = [
    # code, name, tagline, best_for, featured, order
    ("starter", "Starter", "Run your scheme properly from day one.", "A single parlour moving from paper and spreadsheets.", False, 1),
    ("professional", "Professional", "Collect more, communicate more, stay compliant.", "Growing schemes that chase arrears, send SMS and must meet POPIA.", True, 2),
    ("enterprise", "Enterprise", "Many branches, agents and your own brand.", "Larger operations with branches, agents, funeral services and their own branding.", False, 3),
]

# category, name, description, plan code it starts at, highlight on home, order
FEATURES = [
    ("members", "Online applications and member portal", "People apply online; members see their cover, pay and download their certificate on any device.", "starter", True, 1),
    ("members", "Members, dependants and beneficiaries", "Complete policy records with family members, waiting periods and status history.", "starter", False, 2),
    ("members", "Policy changes and reinstatement", "Upgrades, downgrades, cancellations and reinstatement handled as proper requests with an audit trail.", "starter", False, 3),
    ("members", "Excel client import", "Bring your existing members across from your spreadsheets.", "starter", False, 4),
    ("members", "Private document storage", "ID and claim documents stored privately; every access is logged.", "starter", False, 5),
    ("money", "Premium capture, receipts and pay-ahead", "Record cash and EFT payments for one month or many months ahead, with a yearly calendar for every member.", "starter", True, 1),
    ("money", "Online payments", "Members pay premiums online through PayFast or EasyPay and payments reconcile automatically.", "starter", False, 2),
    ("money", "Management reports on screen", "Premium income, arrears, membership and claims at a glance.", "starter", False, 3),
    ("money", "Arrears reminders and recovery worklist", "A reminder ladder, promise-to-pay tracking and a daily worklist for your collectors.", "professional", True, 4),
    ("money", "Report downloads and management pack", "Excel and PDF downloads for your board and your accountant.", "professional", False, 5),
    ("claims", "Claims from first call to payout", "Capture, verify, decide and pay claims with documents, decisions and dates on record.", "starter", True, 1),
    ("claims", "Funeral service management", "Funeral quotes, coffin and tombstone price lists and scheduling of funerals.", "enterprise", False, 2),
    ("comms", "Email notifications", "Members are told when their application, payment or claim changes.", "starter", False, 1),
    ("comms", "SMS messaging", "Important-event SMS with delivery reports, replies and opt-out handling.", "professional", False, 2),
    ("growth", "Member cards with scan verification", "Issue cards and verify a member's standing with one scan.", "professional", False, 1),
    ("growth", "REST API", "Connect your own apps and systems to the platform.", "professional", False, 2),
    ("growth", "Agents and commission", "Track sales agents, referrals and commission runs.", "enterprise", False, 3),
    ("growth", "Multiple branches", "Branch-scoped staff and reporting across all your offices.", "enterprise", False, 4),
    ("growth", "Your own branding", "Your name, colours and logo throughout the platform.", "enterprise", False, 5),
    ("trust", "Roles and permissions", "Reception, managers, branch staff and owners each see only what they should.", "starter", False, 1),
    ("trust", "Audit trail", "Who changed what, and when, on every policy, payment and claim.", "starter", False, 2),
    ("trust", "Your own isolated platform", "Each parlour has its own instance and database.", "starter", True, 3),
    ("trust", "POPIA and policy-rules pack", "Information Officer details, consent records, data requests, retention clean-up, incident register, cooling-off and claim timelines.", "professional", True, 4),
]


def seed(apps, schema_editor):
    Plan = apps.get_model("catalog", "Plan")
    Feature = apps.get_model("catalog", "Feature")
    plans = {}
    for code, name, tagline, best_for, featured, order in PLANS:
        plans[code], _ = Plan.objects.get_or_create(code=code, defaults={"name": name, "tagline": tagline, "best_for": best_for, "is_featured": featured, "order": order})
    for category, name, description, code, highlight, order in FEATURES:
        Feature.objects.get_or_create(category=category, name=name, defaults={"description": description, "available_from": plans[code], "is_highlight": highlight, "order": order})


class Migration(migrations.Migration):
    dependencies = [("catalog", "0001_initial")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
