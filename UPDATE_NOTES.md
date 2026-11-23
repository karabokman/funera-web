# Funera website: quotations, back-office, PDF, VAT and follow-up reminders
Copy these files over your project (same paths), then from `src/`:

    pip install -r requirements.txt   # adds reportlab (quotation PDFs)
    python manage.py migrate          # leads 0002-0003, quotes 0001-0003
    python manage.py createsuperuser  # or tick "Staff status" on a user
    python manage.py test

Staff sign in at /staff/login/. Add VAT_RATE, VAT_NUMBER, QUOTE_VALID_DAYS to your .env if wanted (see .env.example). Details: docs/QUOTATIONS.md.
Tailwind CSS was rebuilt and is included (static/css).

Changes in this version:
- E-mail: `.env.example` no longer ships an empty EMAIL_HOST with the SMTP backend (the cause of "please run connect() first"). Update your own `.env`: remove or comment the EMAIL_* lines to print mail in the terminal, or set a real host.
- Sending a quotation now fails visibly (and stays a draft) if the e-mail cannot be sent.
- "Price includes VAT" tick on each quotation (default ticked). Prices are now the final amounts; existing quotations are converted by migration so clients pay the same as before. VAT_RATE now defaults to 0.15.
- Quotation PDF: attached to the e-mail, downloadable by the client and by staff.
- Hidden staff address: set `STAFF_URL` in `.env` (e.g. `STAFF_URL=team-k4p9x2/`). robots.txt no longer lists /admin/ or /staff/ (it was giving the addresses away). Bookmark your new staff address.
- Follow-up reminders (docs/QUOTATIONS.md). The migration puts every open lead and sent quotation on the follow-up list (leads: today; quotations: 3 days after they were sent, or today). Optional `QUOTE_FOLLOW_UP_DAYS` in `.env`.
- Saved offers (quotation templates): run `python manage.py migrate` (quotes 0004). See docs/QUOTATIONS.md. This version also fixes a bug where a pre-filled once-off line (a plan's set-up fee) was dropped if you saved the form without editing it.
- Staff roles (docs/QUOTATIONS.md): run `python manage.py migrate` (creates the group "Funera Admin"). **Every existing non-superuser staff account becomes Sales.** Superusers stay Admins. Add trusted people to the group "Funera Admin" in the Django admin. The Django admin is now superusers-only.
- Audit log (docs/QUOTATIONS.md): run `python manage.py migrate` (backoffice 0002). Admins see it under "Audit log". It starts empty and fills from now on.
