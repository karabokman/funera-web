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
- Filter by person, area, source, dates or text. The page shows the newest 1 000 entries from each source; narrow the dates for older ones.
- Entries cannot be edited or deleted in the back-office, and the Django admin shows them read-only. Entries stay when a user account is deleted (the username is copied into the entry).
- Not recorded: changes made directly in the database or to users and groups in the Django admin (the Django admin keeps its own history for objects you edit there).
- The log contains staff usernames and IP addresses. Mention that in your internal privacy notes if you need to; it is visible to Admins only.
