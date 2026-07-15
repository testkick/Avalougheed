"""Ava Lougheed Shoes — presale backend.

Endpoints:
  GET  /                          landing page (static)
  POST /api/subscribe             email capture
  POST /api/create-checkout-session   Stripe Checkout redirect for $25 deposit
  POST /api/stripe-webhook        records paid reservations
  GET  /api/checkout-status       confirms a session after redirect back
  GET  /admin                     admin dashboard (HTTP Basic auth)
  GET  /admin/subscribers.csv     export
  GET  /admin/reservations.csv    export
"""
import csv
import io
import os
import re
import secrets
import logging

import stripe
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError

from .db import engine, init_db, reservations, subscribers, healthcheck

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ava")

STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
ADMIN_USER = os.environ.get("ADMIN_USER", "ava")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")
DEPOSIT_AMOUNT_CENTS = int(os.environ.get("DEPOSIT_AMOUNT_CENTS", "2500"))

stripe.api_key = STRIPE_SECRET_KEY

from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    if not STRIPE_SECRET_KEY:
        log.warning("STRIPE_SECRET_KEY not set — checkout will fail until configured.")
    if not ADMIN_PASSWORD:
        log.warning("ADMIN_PASSWORD not set — /admin is disabled until configured.")
    yield


app = FastAPI(title="Ava Lougheed Shoes Presale", lifespan=lifespan)
security = HTTPBasic()

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ---------- health ----------

@app.get("/healthz")
def healthz():
    return {"ok": True, "db": healthcheck()}


# ---------- email capture ----------

class SubscribeBody(BaseModel):
    email: str


@app.post("/api/subscribe")
def subscribe(body: SubscribeBody):
    email = body.email.strip().lower()
    if not EMAIL_RE.match(email) or len(email) > 320:
        raise HTTPException(status_code=422, detail="Please enter a valid email address.")
    try:
        with engine.begin() as conn:
            conn.execute(insert(subscribers).values(email=email, source="landing"))
    except IntegrityError:
        pass  # already on the list — treat as success, don't leak membership
    return {"ok": True}


# ---------- Stripe checkout ----------

class CheckoutBody(BaseModel):
    name: str = ""
    email: str = ""


@app.post("/api/create-checkout-session")
def create_checkout_session(body: CheckoutBody):
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Payments are not configured yet.")
    email = body.email.strip().lower()
    params = dict(
        mode="payment",
        line_items=[{
            "price_data": {
                "currency": "usd",
                "unit_amount": DEPOSIT_AMOUNT_CENTS,
                "product_data": {
                    "name": "Ava Lougheed — presale deposit",
                    "description": "Fully refundable $25 deposit, applied to your $75 at launch.",
                },
            },
            "quantity": 1,
        }],
        success_url=f"{BASE_URL}/?reserved=1&session_id={{CHECKOUT_SESSION_ID}}#reserve",
        cancel_url=f"{BASE_URL}/?canceled=1#reserve",
        metadata={"customer_name": body.name.strip()[:200]},
        consent_collection={"promotions": "auto"},
    )
    if EMAIL_RE.match(email):
        params["customer_email"] = email
    try:
        session = stripe.checkout.Session.create(**params)
    except stripe.error.StripeError as e:
        log.error("Stripe session create failed: %s", e)
        raise HTTPException(status_code=502, detail="Could not start checkout. Please try again.")
    return {"url": session.url}


@app.post("/api/stripe-webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
    except (ValueError, stripe.error.SignatureVerificationError):
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    if event["type"] == "checkout.session.completed":
        s = event["data"]["object"]
        email = (s.get("customer_details") or {}).get("email") or s.get("customer_email") or ""
        name = (s.get("metadata") or {}).get("customer_name") or \
               (s.get("customer_details") or {}).get("name") or ""
        try:
            with engine.begin() as conn:
                conn.execute(insert(reservations).values(
                    name=name,
                    email=email.lower(),
                    amount_cents=s.get("amount_total") or DEPOSIT_AMOUNT_CENTS,
                    currency=s.get("currency") or "usd",
                    stripe_session_id=s.get("id"),
                    stripe_payment_intent=s.get("payment_intent"),
                    status="paid",
                ))
                # buyers are the best mailing list — add them too
                if email:
                    try:
                        conn.execute(insert(subscribers).values(
                            email=email.lower(), source="deposit"))
                    except IntegrityError:
                        pass
        except IntegrityError:
            log.info("Webhook replay for session %s — already recorded", s.get("id"))

    elif event["type"] == "charge.refunded":
        charge = event["data"]["object"]
        pi = charge.get("payment_intent")
        if pi:
            with engine.begin() as conn:
                conn.execute(update(reservations)
                             .where(reservations.c.stripe_payment_intent == pi)
                             .values(status="refunded"))

    return {"received": True}


@app.get("/api/checkout-status")
def checkout_status(session_id: str):
    """Called by the frontend after redirect back to confirm and personalize."""
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Payments are not configured.")
    try:
        s = stripe.checkout.Session.retrieve(session_id)
    except stripe.error.StripeError:
        raise HTTPException(status_code=404, detail="Session not found")
    paid = s.get("payment_status") == "paid"
    details = s.get("customer_details") or {}
    return {
        "paid": paid,
        "name": (s.get("metadata") or {}).get("customer_name") or details.get("name") or "",
        "email": details.get("email") or "",
    }


# ---------- admin ----------

def require_admin(credentials: HTTPBasicCredentials = Depends(security)):
    if not ADMIN_PASSWORD:
        raise HTTPException(status_code=503, detail="Admin is not configured (set ADMIN_PASSWORD).")
    user_ok = secrets.compare_digest(credentials.username.encode(), ADMIN_USER.encode())
    pass_ok = secrets.compare_digest(credentials.password.encode(), ADMIN_PASSWORD.encode())
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


def _rows(table, order_col):
    with engine.connect() as conn:
        return conn.execute(select(table).order_by(order_col.desc())).mappings().all()


@app.get("/admin", response_class=HTMLResponse)
def admin(_: str = Depends(require_admin)):
    subs = _rows(subscribers, subscribers.c.created_at)
    res = _rows(reservations, reservations.c.created_at)
    paid = [r for r in res if r["status"] == "paid"]

    def esc(v):
        return str(v or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    sub_rows = "".join(
        f"<tr><td>{esc(r['email'])}</td><td>{esc(r['source'])}</td>"
        f"<td>{r['created_at']:%Y-%m-%d %H:%M}</td></tr>" for r in subs
    ) or "<tr><td colspan=3 class=empty>No signups yet</td></tr>"

    res_rows = "".join(
        f"<tr><td>{esc(r['name'])}</td><td>{esc(r['email'])}</td>"
        f"<td>${r['amount_cents']/100:.2f}</td><td>{esc(r['status'])}</td>"
        f"<td>{r['created_at']:%Y-%m-%d %H:%M}</td></tr>" for r in res
    ) or "<tr><td colspan=5 class=empty>No reservations yet</td></tr>"

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ava Lougheed — Admin</title>
<style>
  body{{font-family:-apple-system,'Work Sans',sans-serif;background:#faf8f4;color:#2b2620;margin:0;padding:40px 24px;}}
  .wrap{{max-width:960px;margin:0 auto;}}
  h1{{font-size:22px;margin:0 0 6px;}} .sub{{color:#8a8175;font-size:14px;margin:0 0 28px;}}
  .cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px;margin-bottom:32px;}}
  .card{{background:#fff;border:1px solid #e7e1d6;border-radius:12px;padding:18px;}}
  .card .n{{font-size:28px;font-weight:600;}} .card .l{{font-size:13px;color:#8a8175;}}
  h2{{font-size:16px;margin:28px 0 10px;display:flex;justify-content:space-between;align-items:baseline;}}
  h2 a{{font-size:13px;font-weight:400;color:#7a4a2e;}}
  table{{width:100%;border-collapse:collapse;background:#fff;border:1px solid #e7e1d6;border-radius:12px;overflow:hidden;font-size:14px;}}
  th,td{{text-align:left;padding:10px 14px;border-bottom:1px solid #f0ebe2;}}
  th{{background:#f6f2ea;font-weight:500;font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:#8a8175;}}
  tr:last-child td{{border-bottom:none;}} .empty{{color:#b0a89a;text-align:center;padding:24px;}}
</style></head><body><div class="wrap">
<h1>Ava Lougheed Shoes — Presale</h1>
<p class="sub">Live counts from the production database.</p>
<div class="cards">
  <div class="card"><div class="n">{len(subs)}</div><div class="l">Emails on the list</div></div>
  <div class="card"><div class="n">{len(paid)}</div><div class="l">Paid reservations</div></div>
  <div class="card"><div class="n">${sum(r['amount_cents'] for r in paid)/100:,.0f}</div><div class="l">Deposits held</div></div>
  <div class="card"><div class="n">{300 - len(paid)}</div><div class="l">Pairs remaining (of 300)</div></div>
</div>
<h2>Email list <a href="/admin/subscribers.csv">Download CSV</a></h2>
<table><tr><th>Email</th><th>Source</th><th>Signed up (UTC)</th></tr>{sub_rows}</table>
<h2>Reservations <a href="/admin/reservations.csv">Download CSV</a></h2>
<table><tr><th>Name</th><th>Email</th><th>Amount</th><th>Status</th><th>Paid (UTC)</th></tr>{res_rows}</table>
</div></body></html>"""


def _csv_response(filename: str, header: list, rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow(r)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/admin/subscribers.csv")
def subscribers_csv(_: str = Depends(require_admin)):
    rows = _rows(subscribers, subscribers.c.created_at)
    # Email Address / signup source / date — Mailchimp-friendly headers
    return _csv_response(
        "subscribers.csv",
        ["Email Address", "Source", "Signup Date (UTC)"],
        ([r["email"], r["source"], r["created_at"].isoformat()] for r in rows),
    )


@app.get("/admin/reservations.csv")
def reservations_csv(_: str = Depends(require_admin)):
    rows = _rows(reservations, reservations.c.created_at)
    return _csv_response(
        "reservations.csv",
        ["Name", "Email Address", "Amount USD", "Status", "Stripe Session", "Paid At (UTC)"],
        ([r["name"], r["email"], f"{r['amount_cents']/100:.2f}", r["status"],
          r["stripe_session_id"], r["created_at"].isoformat()] for r in rows),
    )


# ---------- static site (mounted last so /api and /admin win) ----------

app.mount("/", StaticFiles(directory="static", html=True), name="static")
