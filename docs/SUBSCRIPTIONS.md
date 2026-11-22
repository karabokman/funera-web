# Subscriptions and automatic provisioning

## Built: PayFast subscriptions (`billing` app)
A visitor picks a priced plan (`/subscribe/<plan>/`), enters business details and accepts the subscription terms and privacy notice (versions are recorded), and is sent to PayFast with a signed recurring checkout: the first payment (set-up fee + first month), then the monthly amount every month with no end date. Prices are copied onto the subscription at sign-up.

- **Activation comes only from PayFast's server notification (ITN)** at `/billing/payfast/itn/`, never from the return page. Each notification is checked for: sender address (last-proxy-aware), signature, merchant id, known reference, amount (first or monthly), then confirmed back with PayFast. Repeats are idempotent (`Payment.pf_payment_id` is unique). Failed collection → *Payment failed*; PayFast cancellation → *Cancelled*.
- **Sign-up is off until configured**: with no merchant values the site keeps its "Talk to us" buttons. Plans without a price are never sold online.
- **Cancelling**: staff use the admin action on Subscriptions; it calls PayFast's API first and changes the local status only if PayFast confirms.
- **Records**: customers, subscriptions and payments cannot be deleted in the admin (financial records; keep for the period your accountant advises).
- **Hook for provisioning**: `billing.signals.subscription_activated` fires once, after the first confirmed payment, with the subscription. A receiver can create the client's platform. A failing receiver is logged and never fails the notification.
- **Annual billing**: give a plan an *annual price* in the admin and clients can choose monthly or annual at sign-up (PayFast frequency 3 or 6). The pricing page shows the annual price and the saving. Clients can later switch period in the portal: PayFast is updated first, and the new amount applies from the next collection date (nothing already paid is prorated or refunded). Switching uses the plan's *current* price.
- **Client portal** (`/portal/`): passwordless. The client enters their e-mail address and is sent a single-use link (expires in 20 minutes; only a hash is stored; opening it only shows a button, so mail scanners cannot use it up). The same answer is shown for unknown addresses, and attempts are rate-limited per visitor and per address. The session lasts 2 hours and is renewed at sign-in. A client sees only subscriptions whose e-mail matches theirs. From the portal they can see status, next payment and payments, **pause** (1 to `PAUSE_MAX_CYCLES` periods; PayFast restarts collections by itself, or they can resume sooner), **switch period**, **cancel** (with a confirmation), and **request a refund**.
- **Refunds**: a client may request a refund of a recent payment (`REFUND_WINDOW_DAYS`, default 7: this is a policy choice for you to set). Staff then use *Process selected refunds* in the admin, which refunds the card through PayFast; if PayFast does not confirm, the refund is marked *Failed*, nothing is changed, and it can be retried. Staff can also raise a refund themselves (any time) or decline one. Refunds are partial-safe: the total refunded can never exceed the payment.
- **Audit trail**: every pause, resume, period change, cancellation, refund and payment is written to the subscription's activity log (who did it: client, staff or PayFast). Sales is e-mailed when a client pauses, resumes, switches or cancels.
- **Hooks**: `billing.signals.subscription_status_changed(subscription, old, new)` fires on every status change, so a provisioner can suspend a platform when a subscription is paused or cancelled and restore it on resume.
- **Not built yet**: card/e-mail updates by the client (PayFast hosts card changes), VAT invoices/receipts, proration on period changes, upgrades/downgrades between plans, and the provisioning itself.

## Planned: automatic provisioning

This document is the design for the next phase: clients subscribe on the website and a platform instance is created for them automatically. Nothing here is built yet; the current code is shaped so it slots in without rework.

## Domain model (new app `customers`)
- **Customer**: legal name, contact, billing e-mail, optional `lead` (FK to `leads.Lead`, set when a won lead converts), POPIA/terms acceptance record.
- **Subscription**: customer, `plan` (FK `catalog.Plan`), status (`pending`, `active`, `past_due`, `suspended`, `cancelled`), billing period, provider reference.
- **Instance**: subscription, subdomain/slug, status (`requested`, `provisioning`, `ready`, `failed`, `suspended`, `decommissioned`), platform URL, version, provisioned_at.
- **ProvisioningEvent**: append-only log (instance, step, outcome, detail) for audit and support.

## Flow
1. The client checks out (built) or a lead is marked **Won** and a customer is created by staff.
2. PayFast confirms the first payment (built) → subscription `active` and `subscription_activated` is sent.
3. `provisioning.services.request_instance(subscription)` creates an `Instance(requested)` and enqueues a job.
4. The job drives a **Provisioner** (interface below) through idempotent steps: create database, write env (`LICENSE_PACKAGE = plan.code`, `SECRET_KEY`, hosts, branding), run migrations, create the first superuser with a one-time set-password link, start the container, wait for `/healthz/`, register the domain. Each step writes a ProvisioningEvent; failure leaves the instance `failed` and alerts support. Re-running resumes.
5. Welcome e-mail with the platform URL and set-password link.
6. Plan change → update the instance's licence key/package. Non-payment → `suspended` (read-only or offline per policy). Cancellation → export window, then `decommissioned` with data deletion.

## Extension points already in place
- `Plan.code` equals the platform's `LICENSE_PACKAGE`; `Feature.available_from` mirrors the platform's cumulative packages, so what the site sells is what gets enabled.
- `Lead.status` includes Won, and won leads are exempt from retention purging.
- Services layer (`leads/services.py`, `catalog/services.py`) is callable from jobs and webhooks.
- `/healthz/` pattern is reusable for instance health polling.

## Interfaces (sketch)
```python
class Provisioner(Protocol):
    def create(self, instance: Instance) -> None: ...      # idempotent, resumes after failure
    def update_plan(self, instance: Instance, plan: Plan) -> None: ...
    def suspend(self, instance: Instance) -> None: ...
    def decommission(self, instance: Instance) -> None: ...

class PaymentGateway(Protocol):
    def parse_webhook(self, request) -> PaymentEvent: ...  # verifies signature, returns normalised event
```
Implementations (`DockerProvisioner` using the existing multi-customer deploy script, a gateway adapter for the chosen provider) are swappable and testable with fakes.

## Decisions needed
Payment provider (e.g. PayFast/Paystack/Stripe), billing cycle and proration rules, trial policy, data-retention period after cancellation, whether clients self-serve or sales approves each, and a job runner (Celery/RQ vs a database-backed queue). Admin-only staff access for customers first; a client billing portal can follow.
