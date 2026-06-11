"""Stripe billing — Checkout, customer portal, webhook → users.tier.

Talks to Stripe's REST API directly with httpx (form-encoded, like their
SDK does under the hood) so we don't carry the stripe package. Webhook
signatures are verified manually per
https://stripe.com/docs/webhooks/signatures — HMAC-SHA256 over
"{timestamp}.{raw_body}" with the webhook signing secret.

All endpoints 503 until the four STRIPE_* env vars are set, so the module
is inert in dev and on deploys that haven't enabled billing yet.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from typing import Any

import httpx
from fastapi import APIRouter, Header, HTTPException, Request

from jobfox import analytics, db
from jobfox import config

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/billing")

_STRIPE_API = "https://api.stripe.com/v1"
_HTTP_TIMEOUT = httpx.Timeout(20.0, connect=10.0)
_SIGNATURE_TOLERANCE_SECONDS = 5 * 60

PLAN_PRICES: dict[str, str] = {}  # plan name -> price id, filled lazily


def _settings():
    s = config.settings()
    if not s.stripe_configured:
        raise HTTPException(status_code=503, detail="billing not configured")
    return s


def _plan_for_price(price_id: str) -> str | None:
    s = config.settings()
    if price_id == s.stripe_price_pro:
        return "pro"
    if price_id == s.stripe_price_power:
        return "power"
    return None


def _stripe_post(path: str, data: dict[str, str]) -> dict[str, Any]:
    s = _settings()
    with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
        r = client.post(
            f"{_STRIPE_API}/{path}",
            data=data,
            auth=(s.stripe_secret_key, ""),
        )
    if r.status_code >= 400:
        log.error("stripe %s failed: %s %s", path, r.status_code, r.text[:300])
        raise HTTPException(status_code=502, detail="stripe call failed")
    return r.json()


# ---------------------------------------------------------------------------
# Webhook signature
# ---------------------------------------------------------------------------

def verify_stripe_signature(payload: bytes, sig_header: str, secret: str) -> bool:
    """True iff any v1 signature in the header matches and is fresh."""
    timestamp = ""
    candidates: list[str] = []
    for part in sig_header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t":
            timestamp = value
        elif key == "v1":
            candidates.append(value)
    if not timestamp or not candidates:
        return False
    try:
        if abs(time.time() - int(timestamp)) > _SIGNATURE_TOLERANCE_SECONDS:
            return False
    except ValueError:
        return False
    signed = f"{timestamp}.".encode() + payload
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, c) for c in candidates)


# ---------------------------------------------------------------------------
# Routes (auth via the same session tokens as the rest of /api)
# ---------------------------------------------------------------------------

@router.post("/checkout")
def create_checkout(
    body: dict[str, Any],
    authorization: str | None = Header(default=None),
) -> dict[str, str]:
    """Returns a Stripe Checkout URL for the requested plan."""
    from jobfox.web_api import _current_user

    s = _settings()
    user = _current_user(authorization)
    plan = str(body.get("plan", "")).lower()
    price = {"pro": s.stripe_price_pro, "power": s.stripe_price_power}.get(plan)
    if not price:
        raise HTTPException(status_code=400, detail="plan must be 'pro' or 'power'")

    app_url = s.app_url.rstrip("/")
    data = {
        "mode": "subscription",
        "line_items[0][price]": price,
        "line_items[0][quantity]": "1",
        "success_url": f"{app_url}/account?upgraded=1",
        "cancel_url": f"{app_url}/#pricing",
        # client_reference_id is how the webhook maps the session back to us.
        "client_reference_id": str(user["id"]),
    }
    if user.get("stripe_customer_id"):
        data["customer"] = user["stripe_customer_id"]
    elif user.get("email"):
        data["customer_email"] = user["email"]

    session = _stripe_post("checkout/sessions", data)
    return {"url": str(session.get("url") or "")}


@router.post("/portal")
def create_portal(authorization: str | None = Header(default=None)) -> dict[str, str]:
    """Customer portal — manage/cancel the subscription."""
    from jobfox.web_api import _current_user

    s = _settings()
    user = _current_user(authorization)
    if not user.get("stripe_customer_id"):
        raise HTTPException(status_code=400, detail="no subscription on file")
    session = _stripe_post(
        "billing_portal/sessions",
        {
            "customer": user["stripe_customer_id"],
            "return_url": f"{s.app_url.rstrip('/')}/account",
        },
    )
    return {"url": str(session.get("url") or "")}


@router.post("/webhook")
async def stripe_webhook(
    request: Request,
    stripe_signature: str | None = Header(default=None),
) -> dict[str, str]:
    s = _settings()
    payload = await request.body()
    if not stripe_signature or not verify_stripe_signature(
        payload, stripe_signature, s.stripe_webhook_secret
    ):
        raise HTTPException(status_code=400, detail="bad signature")

    import json

    event = json.loads(payload)
    etype = str(event.get("type", ""))
    obj = (event.get("data") or {}).get("object") or {}

    if etype == "checkout.session.completed":
        user_id = int(obj.get("client_reference_id") or 0)
        customer = str(obj.get("customer") or "")
        user = db.get_user(user_id) if user_id else None
        if user is None:
            log.error("stripe webhook: unknown user in session %s", obj.get("id"))
            return {"status": "ignored"}
        # The session doesn't carry the price id directly; fetch line items.
        session_id = str(obj.get("id") or "")
        items = _stripe_post(f"checkout/sessions/{session_id}/line_items", {})
        price_id = ""
        for item in items.get("data") or []:
            price_id = str(((item.get("price") or {}).get("id")) or "")
            if price_id:
                break
        plan = _plan_for_price(price_id) or "pro"
        db.update_user(user_id, tier=plan, stripe_customer_id=customer)
        analytics.track(user_id, "subscription_started", {"plan": plan})
        log.info("user %d upgraded to %s", user_id, plan)

    elif etype in ("customer.subscription.deleted", "customer.subscription.paused"):
        customer = str(obj.get("customer") or "")
        row = db.query(
            "SELECT id FROM users WHERE stripe_customer_id = %s",
            (customer,),
            fetch="one",
        )
        if row:
            db.update_user(int(row["id"]), tier="free")
            analytics.track(int(row["id"]), "subscription_ended")
            log.info("user %d downgraded to free", int(row["id"]))

    return {"status": "ok"}
