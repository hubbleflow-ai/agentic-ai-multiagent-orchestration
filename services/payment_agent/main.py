"""payment-agent · A2A worker for payment. Calls the mock payment processor.

A2A skills:
  - authorize(amount_inr, vendor, items) → {auth_id, expires_at}
  - capture(auth_id) → {transaction_id, status}     # idempotent (mock processor)
  - refund(transaction_id, amount_inr) → {refund_id, status}

Phase 2A: pure executor (no HITL gate yet). The HITL voice-approval gate
goes BETWEEN authorize and capture, owned by the Planner+ConciergeVoice
combination (Planner pauses via LangGraph interrupt, Concierge speaks the
"approve ₹1.87L?" prompt, user says yes/no, Planner resumes).
"""

from __future__ import annotations

import os

import httpx
from fastapi import FastAPI
from shared.a2a import A2AServer
from shared.observability import setup_observability

setup_observability("payment-agent")

app = FastAPI(title="payment-agent", version="0.1.0")
a2a = A2AServer(
    app,
    agent_name="payment-agent",
    description="Authorizes, captures, and refunds payments. Idempotent on auth_id.",
)

PAYMENT_API_URL = os.environ.get("PAYMENT_API_URL", "http://mock-payment-api:9003")
_http = httpx.AsyncClient(timeout=15.0)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "payment-agent"}


@a2a.skill("authorize")
async def authorize(payload: dict) -> dict:
    r = await _http.post(f"{PAYMENT_API_URL}/authorize", json={
        "amount_inr": int(payload["amount_inr"]),
        "vendor": payload.get("vendor", "hubbleflow-trip-planner"),
        "items": payload.get("items", []),
    })
    r.raise_for_status()
    return r.json()


@a2a.skill("capture")
async def capture(payload: dict) -> dict:
    r = await _http.post(f"{PAYMENT_API_URL}/capture", json={"auth_id": payload["auth_id"]})
    r.raise_for_status()
    return r.json()


@a2a.skill("refund")
async def refund(payload: dict) -> dict:
    r = await _http.post(f"{PAYMENT_API_URL}/refund", json={
        "transaction_id": payload["transaction_id"],
        "amount_inr": int(payload["amount_inr"]),
    })
    r.raise_for_status()
    return r.json()
