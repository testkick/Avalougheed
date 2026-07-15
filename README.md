# Ava Lougheed Shoes — Presale

Presale landing page with Stripe deposit checkout, email capture, Postgres persistence, and a password-protected admin dashboard.

**Stack:** FastAPI · Postgres (SQLite fallback for local dev) · Stripe Checkout · static frontend · Docker · Railway

## Routes

| Route | What it does |
|---|---|
| `/` | Landing page |
| `POST /api/subscribe` | Adds an email to the list (deduped, case-insensitive) |
| `POST /api/create-checkout-session` | Creates a Stripe Checkout session for the $25 deposit |
| `POST /api/stripe-webhook` | Records paid reservations + refunds (signature-verified) |
| `GET /api/checkout-status` | Confirms a session after redirect back |
| `/admin` | Dashboard: counts, email list, reservations (HTTP Basic auth) |
| `/admin/subscribers.csv` | Mailchimp-friendly CSV export |
| `/admin/reservations.csv` | Reservations CSV export |
| `/healthz` | Health check (used by Railway) |

## Local dev

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in values, then:
export $(grep -v '^#' .env | xargs)
uvicorn app.main:app --reload
```

No `DATABASE_URL` → falls back to `local.db` (SQLite). Use `stripe listen --forward-to localhost:8000/api/stripe-webhook` to test webhooks locally; it prints the `whsec_...` secret to use.

## Deploy to Railway

1. **Create the project** — push this repo to GitHub, then in Railway: New Project → Deploy from GitHub repo. The `Dockerfile` and `railway.json` are picked up automatically.
2. **Add Postgres** — in the project: New → Database → PostgreSQL. Then on your app service → Variables, add a reference to `DATABASE_URL` from the Postgres service. Tables are created automatically on first boot.
3. **Set environment variables** on the app service:
   - `STRIPE_SECRET_KEY` — from Stripe dashboard → Developers → API keys (start with the test key)
   - `ADMIN_USER` / `ADMIN_PASSWORD` — credentials for `/admin`
   - `BASE_URL` — your public URL, e.g. `https://ava-presale.up.railway.app` (Settings → Networking → Generate Domain)
4. **Create the webhook** — Stripe dashboard → Developers → Webhooks → Add endpoint:
   - URL: `{BASE_URL}/api/stripe-webhook`
   - Events: `checkout.session.completed`, `charge.refunded`
   - Copy the signing secret into `STRIPE_WEBHOOK_SECRET` on Railway.
5. **Test** — use card `4242 4242 4242 4242` (any future expiry, any CVC) in test mode. Check `/admin` for the reservation, then flip to live keys when ready.

## Notes

- The design prototype had raw card-number fields in the deposit modal; those are replaced with a redirect to Stripe-hosted Checkout, so no card data ever touches this server (PCI compliance stays Stripe's problem).
- Deposit buyers are automatically added to the email list with `source=deposit`; landing-page signups get `source=landing`, so you can segment when importing to Mailchimp.
- Checkout enables Stripe's marketing-consent collection (`consent_collection: promotions`), so buyer emails carry usable consent.
- Refunds issued in the Stripe dashboard flip the reservation to `refunded` via the `charge.refunded` webhook — remaining-pairs count on `/admin` only counts `paid`.
- `DEPOSIT_AMOUNT_CENTS` env var changes the deposit amount (default 2500).
