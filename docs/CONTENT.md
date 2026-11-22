# Content and launch checklist

Items that need a human decision before launch:

- **Prices**: Plans > price fields in the admin. Empty = "Get a quote".
- **Contact details**: `CONTACT_EMAIL`, `CONTACT_PHONE`, `INFORMATION_OFFICER_EMAIL`, `COMPANY_LEGAL_NAME` (placeholders by default).
- **Subscription terms** (`templates/pages/subscription_terms.html`) are a short draft; have them reviewed together with your customer agreement. Bump `SUBSCRIPTION_TERMS_VERSION` when they change.
- **Privacy notice and website terms** (`templates/pages/privacy.html`, `terms.html`) are drafts, not legal advice. Have a South African attorney review them; bump `PRIVACY_NOTICE_VERSION` when the notice changes materially.
- **Response promise**: the site says enquiries are answered within one working day. Change it if you cannot keep it (thanks page and acknowledgement e-mail).
- **Claims**: the security page describes controls the platform has; confirm each is true for your hosting before publishing.
- **Logo**: the supplied file is a cut-out of a photographed mockup. Replace `src/static/img/logo*.png`, `mark*.png`, icons and `og.png` with files exported from the original vector artwork.
- **Open Graph image** (`og.png`) is a generated placeholder with the slogan.
