# Quotations and the staff back-office

Funera is sold by quotation. Plan prices are left blank in the catalogue, so the public site shows "Get a quote" instead of a price.

## The flow
1. A visitor fills in the contact or demo form and becomes a **Lead** (`/staff/leads/`).
2. Staff open the lead and choose **Quote** for a plan. The form is pre-filled from the lead; add the monthly (or annual) price and any once-off lines (set-up, training, discounts as negative lines). Enter the **final price the client pays** (South African prices include VAT) and keep **Price includes VAT** ticked; the quotation then shows the VAT portion (`VAT_RATE`, 15%). Untick it if you are not VAT-registered: the quotation then says no VAT is charged. The tick never changes what the client pays, only what the quotation states.
3. **Send** e-mails the client a private link (`/quote/<token>/`) with the quotation attached as a PDF. If the e-mail cannot be sent, the quotation stays a draft and the screen tells you why, so a quotation is never marked sent unless the e-mail went out. The lead moves to *Quoted*. A sent quotation can be re-sent, cancelled or duplicated; only a draft can be edited.
4. The client reads it, downloads the PDF (`/quote/<token>/pdf/`) or prints it, then **accepts** (typed name plus a tick) or **declines** with an optional reason. Acceptance creates a Customer and a *pending* Subscription at the quoted amounts (VAT included) and marks the lead *Won*. Staff are e-mailed either way.
5. If online billing (PayFast) is configured, the client is offered the payment link straight after accepting. Otherwise staff invoice manually.
6. Quotations expire on their `valid_until` date. Expiry is calculated when viewed, so no scheduled job is needed.

## Staff access
Sign in at `/<STAFF_URL>login/`. Set `STAFF_URL` in `.env` to an address only your team knows (for example `STAFF_URL=team-k4p9x2/`), the same way you move the Django admin with `ADMIN_URL`. Nothing on the public site links to it, and neither address appears in `robots.txt` or the sitemap. The default `staff/` is for development only. Any active user with `is_staff` can sign in. What they can do depends on their role (below). Create users with `python manage.py createsuperuser` or in the Django admin by ticking *Staff status*. Sign-in is rate-limited by django-axes. Back-office pages are never cached or indexed.

Staff can also pause, resume and cancel subscriptions, raise refunds and process or decline refund requests (all through PayFast first, then saved locally).

## Settings
| Setting | Meaning |
| --- | --- |
| `VAT_RATE` | `0.15`. Each quotation stores the rate it was made with. |
| `VAT_NUMBER` | Shown on quotations when set. |
| `QUOTE_VALID_DAYS` | Default validity (30). |

## Caveats
- The accept step is a typed name and a tick, not a legally qualified e-signature.
- The PDF is built on the server with ReportLab (`pip install -r requirements.txt`). Staff can open it from the quotation page.
- The quotation and subscription terms are drafts and need legal review.

## E-mail
Set `EMAIL_BACKEND`, `EMAIL_HOST` and the other `EMAIL_*` values in `.env`. In development, leave them out and messages print in the terminal. An SMTP backend with an empty `EMAIL_HOST` falls back to the terminal in development, and production refuses to start in that state.

## Follow-up reminders
There is no scheduler on the host, so reminders are a date plus a list that staff open each day.
- Every lead and quotation can carry a **follow-up date** and a **next action**. A new enquiry is due for first contact the day it arrives; a quotation is due for a chase `QUOTE_FOLLOW_UP_DAYS` (3) days after it is sent.
- The dashboard **Today** list shows what is overdue or due today; the nav badge counts the same. **Follow-ups** shows everything: overdue, today, the next 7 days, later, and open leads or sent quotations with **no date** (so nothing hides).
- On each row: **Done** (clears it; a new lead becomes Contacted), **+1 day**, **+3 days**. Open the lead or quotation to set an exact date and action, or +1 week.
- A follow-up is cleared automatically when a quotation is accepted, declined or cancelled, and when a lead is marked Won or Lost.
- Quotation follow-up changes are written to its history.

## Saved offers (quotation templates)
A saved offer is a plan, price, billing period, VAT setting, wording and once-off lines you quote often, for example "Starter, 10 branches, standard set-up".
- **Create:** Quotations > Saved offers > New saved offer, or open any quotation and use **Save as offer** (the client's details are never saved).
- **Use:** when starting a quotation, or from a lead, click the offer. The whole form is filled in, including the lead's contact details and a valid-until date; change anything before saving.
- Each offer can set how many days its quotations stay open (empty uses `QUOTE_VALID_DAYS`). Untick **Show in the list** to hide an offer without deleting it.
- Editing or deleting an offer never changes quotations already made from it.

## Staff roles
| | Sales | Admin |
| --- | --- | --- |
| Leads, follow-ups, quotations (create, send, cancel, duplicate, PDF), use saved offers | Yes | Yes |
| View customers and subscriptions (read-only) | Yes | Yes |
| Pause, resume or cancel a subscription | No | Yes |
| Raise, process or decline refunds; the Refunds list | No | Yes |
| Create, edit or delete saved offers | No | Yes |
| Revenue figures on the dashboard (monthly recurring revenue, quotation value, refunds waiting) | No | Yes |
| Team page and Audit log | No | Yes |
| Django admin site | No | Superusers only |

- **Admin** is a superuser or any staff user in the group **Funera Admin**. Every other active staff user is **Sales**, so existing staff accounts become Sales when you apply this update; add the people who need it to the group.
- To change a role, a superuser opens the user in the Django admin: tick *Staff status*, then add or remove the group *Funera Admin*. Untick *Active* to switch an account off.
- The Django admin (`ADMIN_URL`) is open to superusers only. An Admin who is not a superuser works in the back-office and cannot open it.
- A Sales user who opens an admin-only page, or presses an admin-only button, sees an "Administrators only" page. Nothing is changed and PayFast is not contacted. Sales can still see each subscription's amounts and payments, because they need them to answer clients; tell me if you want that hidden too.

## Audit log
Admins open **Audit log** in the back-office to see who did what, and when.
- **Staff actions** are written the moment they succeed: lead changes, follow-ups, creating/editing/sending/cancelling/duplicating quotations, saved offers, pausing/resuming/cancelling subscriptions, raising/processing/declining refunds, and sign-ins, sign-outs and failed sign-ins. Each entry has the username, the time, what it was on, a short detail and the address the request came from. Passwords are never stored. Bulk actions in the Django admin are logged too ("via the site admin").
- **Clients and PayFast** (a quotation viewed or accepted, a payment arriving) are shown alongside, marked client/system.
- Staff history from before this update is kept but shows "staff (before the audit log)", because it never recorded who.
- Filter by person, area, source, dates or text. The page pages through **every** entry (the earlier 1 000-per-source cap is gone); counting and filtering are done by the database.
- **Users and roles:** adding, changing or deleting a user or a group in the Django admin (this is how roles are changed) is now logged under "Users and roles", with who did it, the user's new role, and whether the account is disabled. Passwords are never recorded, only that the password was changed.
- Entries cannot be edited, and the Django admin shows them read-only. Entries stay when a user account is deleted (the username is copied into the entry).
- **Retention:** staff entries are kept for `AUDIT_RETENTION_MONTHS` (default 24; `0` keeps everything). Because HCode has no scheduler, an Admin clears them: the audit page shows a **Delete them now** button when anything is past the period, or run `python manage.py purge_audit_log` (add `--dry-run` to see the count first). The clean-up writes its own entry ("Deleted old audit entries") that is not deleted. Client and PayFast events are business records and are not purged.
- **Still not recorded:** changes made directly in the database, with `manage.py shell` or `createsuperuser`, and changes by programs other than the site admin. The Django admin keeps its own history for objects you edit there.
- **Before the audit log:** staff events from before you applied the audit-log update never recorded who, so they show "staff (before the audit log)". That cannot be repaired afterwards.
- **Privacy:** the log holds staff usernames and IP addresses. The public privacy notice now says so (section "Our staff and administrators", notice version 1.1) and states the retention period from `AUDIT_RETENTION_MONTHS`. If you change the period, the notice follows automatically. Review the wording with your information officer or lawyer. The `django-axes` access log (a second copy of every address that signs in) is switched off, because this audit log replaces it; axes still keeps failed attempts briefly to block guessing.

## Adding team members
Admins open **Team** and press **Add a team member**: username, first name, last name, e-mail and a role (**Sales** or **Admin**).
- **No password is typed or shown.** The new person is e-mailed a link to choose their own password (checked against the usual password rules). The link works once and expires after 3 days (Django's `PASSWORD_RESET_TIMEOUT`). They then sign in at the staff sign-in page.
- If the e-mail cannot be sent (SMTP down), the person is still added and the page shows the link so you can pass it on yourself. Treat it like a password: it lets whoever opens it first choose the password.
- **Only Admins** can add people; Sales get an "administrators only" page. A **superuser** (access to the Django admin) can never be created here: that stays a `createsuperuser` job.
- Username and e-mail must be unused. Adding someone, and them choosing their password, are written to the audit log.

### Changing access and removing people
On **Team**, every row has actions (Admins only):
- **Edit:** name, e-mail, role (Sales or Admin) and **Can sign in**. Untick *Can sign in* to block someone for a while; it takes effect on their next click. The username cannot be changed.
- **Send password link:** e-mails a fresh one-time link to choose a new password (forgotten password, or an invitation that expired). If the e-mail fails, the link is shown to you.
- **Remove:** takes access away for good: not staff, switched off, password wiped, role removed, gone from the Team list. The account itself is kept so the audit log and any records that name them stay meaningful. To bring someone back, a superuser restores them in the Django admin (they cannot be re-added here with the same username or e-mail).
- **Safeguards:** you cannot demote, disable or remove **yourself**, and nobody can take away the **last active Admin**. **Superusers** can only be changed by another superuser, in the site admin. Sales users cannot reach any of this.
- Every change is in the audit log (changed, removed, password link sent).

## CSV exports (for your accountant)
**Admin → Exports** downloads four files, and the Leads, Customers and Subscriptions pages have a "Download these N as CSV" link that follows the search and status you have set.
- **Leads:** contact details, size, plan of interest, consent record, follow-up and where they came from. Internal notes and the message text are not included.
- **Customers:** one row per customer, with their subscriptions listed.
- **Subscriptions:** plan, status, billing period, first and recurring amounts (incl. VAT), dates and what has been paid to date (completed payments only).
- **Payments** (added because it is what an accountant actually needs): every PayFast payment with the amount received, the VAT it contains, the amount excluding VAT, the PayFast fee, the net after the fee, and anything refunded.
- **Date range:** each card can be limited to From/To (lead and customer received/created dates, payment received date). Dates are South African dates.
- **Opening in Excel:** UTF-8 with a byte-order mark, so accents display. South African Excel expects semicolons, so each download has a **Separator** choice. Amounts are plain numbers (115.00), dates are 2026-11-24.
- **VAT columns** are worked out at the *current* `VAT_RATE` and stay blank until `VAT_NUMBER` is set. The rate in force on the day is not stored, so if the rate ever changes, check older payments by hand. Treat the file as a working paper, not a VAT return.
- **Safety:** text that a spreadsheet could run as a formula (starting with `=`, `@`, or a stray `+`/`-`, which can come from the public contact form) is given a leading apostrophe. Phone numbers such as +27 82 123 4567 are left alone.
- **Who:** Admins only (the files hold personal details and money). Every download is written to the audit log under "CSV downloads" with the number of rows and the filters used. The files are not stored on the server.
- Large exports are streamed from the database in chunks, but they are built in one request: tens of thousands of rows are fine, millions would not be.

## Back-office menu
The main menu holds the day-to-day pages (Dashboard, Follow-ups, Leads, Quotations, Customers, Instances, Subscriptions). Admins also get an **Admin** menu (Refunds, Audit log, Team, and Site admin for superusers) and everyone has an account menu with their username and Sign out. Menus close when you click elsewhere or press Esc.

## Hosted instances
Each client runs their own copy of the platform on your hosting. **Instances** in the back-office keeps one record per client so nothing is lost between "paid" and "live".
- **Created automatically** the first time a subscription is paid, as *Awaiting set-up*. Subscriptions that were already active before this update show in a yellow box at the top of the Instances page with an **Add record** link.
- **What a record holds:** status (awaiting, being set up, live, suspended, retired), address, host, version, the go-live date you promised, five set-up dates (instance created, data imported, administrator login given, training done, went live), who at the client holds the administrator login, who looks after it on your side, and notes.
- **Never type a password** into the record. Write how and when the login was delivered; the field is for a note only.
- **Flags:** *Waiting too long* (open for more than `INSTANCE_SETUP_ALERT_DAYS`, default 3), *Past go-live date*, *Out of date*, and *Billing*, shown when a live instance's subscription is past due, paused or cancelled.
- **Out of date** only works if you set `FUNERA_LATEST_VERSION` in `.env` (e.g. `1.4.2`). Leave it blank to switch it off. After you update a client, press **Record update to …** on their page.
- **The dashboard** lists instances waiting for set-up (oldest first), live instances that need attention, and paying clients with no record.
- **Who can do what:** all staff can look; only Admins can add, edit, mark live or record an update. Saves are written to the audit log.
- Nothing here touches the clients' servers: it is a record you keep up to date, not a monitor. Retiring a record does not cancel the subscription.
