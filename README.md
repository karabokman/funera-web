# Funera website

The marketing and sales website for the **Funera** funeral-cover platform. *Manage with care. Operate with confidence.*

Visitors read about the platform, compare plans, and book a demo. Enquiries are stored (with a POPIA consent record), e-mailed to sales, and managed in the Django admin. A later phase adds subscriptions and automatic provisioning of a platform per client (see [docs/SUBSCRIPTIONS.md](docs/SUBSCRIPTIONS.md)).

## Stack
Django 6.1, PostgreSQL (SQLite for development), WhiteNoise, gunicorn, Tailwind CSS v4, self-hosted Montserrat font. No JavaScript framework and no third-party scripts: the content security policy forbids inline code.

## Layout
```
src/config/    settings (base / development / test / production), urls, wsgi
src/catalog/   Plan and Feature models, seeded by migration; services combine them into the pricing and feature tables
src/leads/     enquiry form, anti-spam, e-mail, admin CSV export, retention command
src/billing/   PayFast subscriptions: sign-up, checkout, notifications, cancellation
src/pages/     public pages, SEO (JSON-LD, sitemap, robots), security-headers middleware, health check
src/templates/ base layout, components, pages, e-mails, error pages
src/static/    source CSS (src/input.css), built css/app.css, fonts, images
docs/          ARCHITECTURE, DEPLOYMENT, SUBSCRIPTIONS, CONTENT
```

## Local development
```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
npm ci && npm run fonts:copy && npm run css:build     # npm run css:watch while editing templates
cd src
python manage.py migrate --settings=config.settings.development
python manage.py createsuperuser --settings=config.settings.development
python manage.py runserver --settings=config.settings.development
```

## Quality gates (also run by CI)
```bash
cd src
ruff check . && ruff format --check .
python manage.py makemigrations --check --dry-run --settings=config.settings.test
python manage.py test --settings=config.settings.test
```
`app.css` is committed; CI fails if it is out of date with the templates.

## Before going live
Read [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) and [docs/CONTENT.md](docs/CONTENT.md). In short: set the environment variables, set plan prices in the admin, replace the placeholder contact details, and have the privacy notice and website terms reviewed by a lawyer.

## Quotations and back-office
Sales run by quotation: see [docs/QUOTATIONS.md](docs/QUOTATIONS.md). Staff sign in at `/staff/login/` (users need `is_staff`). Run `python manage.py migrate` for the `leads` and `quotes` migrations.
