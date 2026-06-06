"""Mock payment processor. Idempotent on auth_id (capture called twice
returns the same transaction_id, not two charges — the idempotency teaching
moment).

Endpoints:
  POST /authorize {amount_inr, vendor} → {auth_id, expires_at}
  POST /capture {auth_id} → {transaction_id, status}
  POST /refund {transaction_id, amount_inr} → {refund_id, status}

State is in-memory (process lifetime). Fine for a single-process demo. For
multi-replica production this would move to Redis; the teaching point about
idempotency stands either way.

Lives at port 9003.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="mock-payment-api", version="0.1.0")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


# In-memory stores. auth_id → {amount, captured_txn_id or None, vendor}
_AUTHS: dict[str, dict] = {}
# transaction_id → {amount, vendor, refunded_amount}
_TXNS: dict[str, dict] = {}


class AuthBody(BaseModel):
    amount_inr: int
    vendor: str
    items: list[dict] | None = None


@app.post("/authorize")
def authorize(body: AuthBody) -> dict:
    auth_id = f"AUTH-{uuid.uuid4().hex[:12].upper()}"
    expires = datetime.now(timezone.utc) + timedelta(minutes=10)
    _AUTHS[auth_id] = {
        "amount_inr": body.amount_inr,
        "vendor": body.vendor,
        "captured_txn_id": None,
        "items": body.items or [],
    }
    return {
        "auth_id": auth_id,
        "amount_inr": body.amount_inr,
        "vendor": body.vendor,
        "expires_at": expires.isoformat(),
        "status": "authorized",
    }


class CaptureBody(BaseModel):
    auth_id: str


@app.post("/capture")
def capture(body: CaptureBody) -> dict:
    auth = _AUTHS.get(body.auth_id)
    if not auth:
        raise HTTPException(404, f"auth_id {body.auth_id} not found")

    # Idempotency: if this auth was already captured, return the SAME txn_id.
    if auth["captured_txn_id"]:
        return {
            "transaction_id": auth["captured_txn_id"],
            "auth_id": body.auth_id,
            "amount_inr": auth["amount_inr"],
            "status": "approved",
            "idempotent_replay": True,
        }

    txn_id = f"TXN-{uuid.uuid4().hex[:12].upper()}"
    _TXNS[txn_id] = {
        "amount_inr": auth["amount_inr"],
        "vendor": auth["vendor"],
        "refunded_amount": 0,
        "auth_id": body.auth_id,
    }
    auth["captured_txn_id"] = txn_id
    return {
        "transaction_id": txn_id,
        "auth_id": body.auth_id,
        "amount_inr": auth["amount_inr"],
        "vendor": auth["vendor"],
        "status": "approved",
    }


class RefundBody(BaseModel):
    transaction_id: str
    amount_inr: int


@app.post("/refund")
def refund(body: RefundBody) -> dict:
    txn = _TXNS.get(body.transaction_id)
    if not txn:
        raise HTTPException(404, f"transaction_id {body.transaction_id} not found")
    available = txn["amount_inr"] - txn["refunded_amount"]
    if body.amount_inr > available:
        raise HTTPException(
            400, f"refund {body.amount_inr} > available {available}"
        )
    refund_id = f"REF-{uuid.uuid4().hex[:10].upper()}"
    txn["refunded_amount"] += body.amount_inr
    return {
        "refund_id": refund_id,
        "transaction_id": body.transaction_id,
        "amount_inr": body.amount_inr,
        "status": "refunded",
    }
