# Deployment

## What you need
A server with Docker, a domain, a TLS-terminating reverse proxy (Caddy or nginx) forwarding to `127.0.0.1:8000`, and an SMTP account.

## Steps
1. `cp .env.example .env` and fill in every value. Generate `SECRET_KEY` with `python -c "import secrets; print(secrets.token_urlsafe(64))"`. Choose a non-default `ADMIN_URL`.
2. `docker compose up -d --build`. The web container runs migrations on start, serves the site with gunicorn, and has a health check on `/healthz/`.
3. Create the first admin: `docker compose exec web python manage.py createsuperuser`.
4. In the admin, set plan prices (or leave blank to show "Get a quote").
5. Proxy must send `X-Forwarded-Proto: https` (the site trusts it for HTTPS detection and redirects).
6. Send a test enquiry and confirm both e-mails arrive. If they do not, check `docker compose logs web` for "Could not send enquiry e-mails".

## PayFast
1. In PayFast (Settings > Integration) set a passphrase and use the same value as `PAYFAST_PASSPHRASE`. Put your merchant id/key in `.env`. Test first with `PAYFAST_SANDBOX=True` and sandbox credentials, then set `PAYFAST_SANDBOX=False`.
2. The notification URL `https://<domain>/billing/payfast/itn/` must be reachable from the internet over https and must not be blocked by the proxy.
3. Confirm `PAYFAST_ALLOWED_NETWORKS` against PayFast's current "ports and IP addresses" page. Set `BILLING_TRUSTED_PROXIES` to the number of proxies in front of the app (1 for a single Caddy/nginx), otherwise the sender-address check sees the proxy, not PayFast.
4. The portal sign-in e-mails need working SMTP. Test: subscribe in the sandbox, open `/portal/`, request a link, sign in.
5. Pause, period change, cancel and refund use PayFast's REST API with your merchant id and passphrase. Try each once in the sandbox before going live; the request formats follow PayFast's documentation but have not been exercised against PayFast from the build environment.
6. Make a sandbox subscription end to end: pay, see the status change to Active in the admin (it may take a minute), receive both e-mails.

## Scheduled jobs
Monthly: `docker compose exec web python manage.py purge_old_leads`.

## Backups
Back up the `pgdata` volume (or `pg_dump`) daily. Enquiries contain personal information; encrypt backups and restrict access.

## HSTS preload
Preload is off. Enable `SECURE_HSTS_PRELOAD` only when every subdomain serves https; removal from the preload list is slow.

## Updating
`git pull && docker compose up -d --build`. CSS is built inside the image; nothing to compile on the server.

## Monitoring
Point an uptime monitor at `https://<domain>/healthz/` (returns 503 if the database is unreachable). Optional cookie-free analytics: set `PLAUSIBLE_DOMAIN`.
