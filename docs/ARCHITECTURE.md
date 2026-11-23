# Architecture

## Principles
- **Small apps with one job each.** `catalog` knows plans and features, `leads` knows enquiries, `pages` knows presentation. Apps depend downwards only: `leads` → `catalog`; `pages` → `catalog`.
- **Thin views, logic in services.** Views parse the request and choose a response. Rules live in `catalog/services.py` and `leads/services.py`, which are easy to test and reuse (for example from a future provisioning job).
- **Content as data where it changes.** Plans and features are database rows edited in the admin; long-form copy is in `pages/content.py` and templates.
- **Fail safe.** Production settings refuse to start with a weak secret, default hosts or a non-https `SITE_URL`.

## Plans and features
Plans are cumulative (Starter ⊂ Professional ⊂ Enterprise). Each feature stores only the *lowest* plan that includes it (`available_from`); `Feature.included_in(plan)` compares plan order. Adding a plan or moving a feature is a data change, not a code change. `Plan.code` equals the platform's `LICENSE_PACKAGE` value, which is what ties a sale to the product that gets provisioned. Plans without a price show "Get a quote": prices are never invented.

## Enquiry flow
1. `GET /contact/` renders a form with a signed timestamp and a hidden honeypot.
2. `POST` is validated by `LeadForm`. A filled honeypot or a submission faster than 3 seconds is treated as spam: the visitor is redirected as if it worked, nothing is saved or sent.
3. A visitor (keyed HMAC of the IP) is limited to `LEAD_RATE_LIMIT_PER_HOUR` enquiries per hour.
4. `create_lead` saves the lead with consent time and privacy-notice version, then e-mails sales and the enquirer in `transaction.on_commit`. Mail failures are logged and never lose the lead.
5. Sales works the lead in the admin (status, notes, CSV export). `purge_old_leads` deletes unconverted leads older than `LEAD_RETENTION_MONTHS`.

## Subscriptions
`billing` owns Customer, Subscription and Payment and depends on `catalog` and `leads` only. `billing/payfast.py` is the gateway adapter (no database); `billing/services.py` holds the rules; views are thin. Modules: `lifecycle.py` (pause, resume, switch, cancel), `refunds.py`, `portal_auth.py` (e-mailed sign-in links) and `portal_views.py`. Every change goes to PayFast first and is saved locally only when PayFast confirms, so the two never disagree. See [SUBSCRIPTIONS.md](SUBSCRIPTIONS.md).

## Security
Strict CSP without inline code (admin excluded), Permissions-Policy, COOP, HSTS, secure cookies, `django-axes` on admin logins, configurable admin URL, formula-safe CSV export, JSON-LD escaped for `<script>`. Health check at `/healthz/` (database round trip) is exempt from the HTTPS redirect.

## Front end
Tailwind v4 built with `@tailwindcss/cli`. Brand tokens are defined once in `src/static/src/input.css`. Button styles are `@utility` rules (Tailwind v4 cannot `@apply` classes defined in `@layer components`).

## quotes and backoffice apps
`quotes` holds Quote/QuoteLine/QuoteEvent, `services.py` (send, accept, decline, cancel, duplicate) and the public token pages. `backoffice` is the staff UI under `/staff/` (StaffRequiredMixin: anonymous to login, non-staff 403). Accepting a quote creates a Customer and pending Subscription via `billing`. See QUOTATIONS.md.
