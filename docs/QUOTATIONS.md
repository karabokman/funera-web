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
Sign in at `/<STAFF_URL>login/`. Set `STAFF_URL` in `.env` to an address only your team knows (for example `STAFF_URL=team-k4p9x2/`), the same way you move the Django admin with `ADMIN_URL`. Nothing on the public site links to it, and neither address appears in `robots.txt` or the sitemap. The default `staff/` is for development only. Any active user with `is_staff` can use the back-office (there are no separate roles). Create users with `python manage.py createsuperuser` or in the Django admin by ticking *Staff status*. Sign-in is rate-limited by django-axes. Back-office pages are never cached or indexed.

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
