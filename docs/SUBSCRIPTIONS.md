# Subscriptions and automatic provisioning

## Built: PayFast subscriptions (`billing` app)
A visitor picks a priced plan (`/subscribe/<plan>/`), enters business details and accepts the subscription terms and privacy notice (versions are recorded), and is sent to PayFast with a signed recurring checkout: the first payment (set-up fee + first month), then the monthly amount every month with no end date. Prices are copied onto the subscription at sign-up.

- **Activation comes only from PayFast's server notification (ITN)** at `/billing/payfast/itn/`, never from the return page. Each notification is checked for: sender address (last-proxy-aware), signature, merchant id, known reference, amount (first or monthly), then confirmed back with PayFast. Repeats are idempotent (`Payment.pf_payment_id` is unique). Failed collection → *Payment failed*; PayFast cancellation → *Cancelled*.
- **Sign-up is off until configured**: with no merchant values the site keeps its "Talk to us" buttons. Plans without a price are never sold online.
- **Cancelling**: staff use the admin action on Subscriptions; it calls PayFast's API first and changes the local status only if PayFast confirms.
- **Records**: customers, subscriptions and payments cannot be deleted in the admin (financial records; keep for the period your accountant advises).
- **Hook for provisioning**: `billing.signals.subscription_activated` fires once, after the first confirmed payment, with the subscription. A receiver can create the client's platform. A failing receiver is logged and never fails the notification.
- **Not built yet**: client self-service portal (change plan, update card, cancel), refunds, invoices/VAT receipts, pausing, annual billing, and the provisioning itself.

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
