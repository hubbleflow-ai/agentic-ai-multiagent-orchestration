"""mcp-payment · MCP server exposing payment authorize/capture/refund tools.

Phase 5. Merges the old `payment-agent` + `mock-payment-api` into one
process · the agent layer was unnecessary (no reasoning happens here ·
it's a deterministic state machine).

Idempotency teaching point preserved: capturing the same auth_id twice
returns the SAME transaction_id, not two charges.

Tools (MCP):
  - authorize(amount_inr, vendor, items?)  → AuthResult
  - capture(auth_id)                       → CaptureResult (idempotent)
  - refund(transaction_id, amount_inr)     → RefundResult

State is in-memory (process lifetime · fine for the cohort demo).

Transport: streamable-http (stateless).
Endpoint:  POST http://mcp-payment:9013/mcp
Health:    GET  http://mcp-payment:9013/health
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse

from shared.observability import setup_observability

setup_observability("mcp-payment")
log = logging.getLogger(__name__)


# ─── MCP server ──────────────────────────────────────────────────────────

mcp = FastMCP(
    "payment",
    instructions=(
        "Mock payment processor · authorize, capture, refund. Capture is "
        "idempotent on auth_id (calling twice returns the same transaction_id)."
    ),
    stateless_http=True,
    transport_security=TransportSecuritySettings(
        allowed_hosts=[
            "mcp-payment:9013",
            "mcp-payment",
            "localhost:9013",
            "localhost",
            "127.0.0.1:9013",
            "127.0.0.1",
        ],
        allowed_origins=["*"],
    ),
)


# ─── tool schemas ────────────────────────────────────────────────────────

class AuthResult(BaseModel):
    auth_id: str
    amount_inr: int
    vendor: str
    expires_at: str = Field(description="ISO-8601 UTC · hold expires 10 min after issue")
    status: str = Field(description="'authorized'")


class CaptureResult(BaseModel):
    transaction_id: str
    auth_id: str
    amount_inr: int
    vendor: str
    status: str = Field(description="'approved'")
    idempotent_replay: bool = False


class RefundResult(BaseModel):
    refund_id: str
    transaction_id: str
    amount_inr: int
    status: str = Field(description="'refunded'")


# ─── in-memory state ─────────────────────────────────────────────────────
# auth_id → {amount_inr, vendor, captured_txn_id, items}
_AUTHS: dict[str, dict] = {}
# transaction_id → {amount_inr, vendor, refunded_amount, auth_id}
_TXNS: dict[str, dict] = {}


# ─── MCP tools ───────────────────────────────────────────────────────────

@mcp.tool()
async def authorize(
    amount_inr: int,
    vendor: str = "hubbleflow-trip-planner",
    items: Optional[list[dict]] = None,
) -> AuthResult:
    """Authorize a payment · puts a hold but DOES NOT charge.

    Inputs:
      amount_inr:  total to authorize in INR
      vendor:      display name for the charge (default: hubbleflow-trip-planner)
      items:       optional line-items list [{label, amount_inr}]

    Returns: AuthResult with auth_id (use this with capture).

    The authorization expires in 10 minutes. Call capture before then.
    """
    auth_id = f"AUTH-{uuid.uuid4().hex[:12].upper()}"
    expires = datetime.now(timezone.utc) + timedelta(minutes=10)
    _AUTHS[auth_id] = {
        "amount_inr": int(amount_inr),
        "vendor": vendor,
        "captured_txn_id": None,
        "items": items or [],
    }
    log.info("mcp.payment.authorize auth_id=%s amount=%d vendor=%s",
             auth_id, amount_inr, vendor)
    return AuthResult(
        auth_id=auth_id,
        amount_inr=int(amount_inr),
        vendor=vendor,
        expires_at=expires.isoformat(),
        status="authorized",
    )


@mcp.tool()
async def capture(auth_id: str) -> CaptureResult:
    """Capture a previously-authorized payment · IRREVERSIBLE charge.

    Idempotent on auth_id: calling twice returns the SAME transaction_id.

    ONLY call after the user has explicitly approved (yes / confirm / proceed).
    The supervisor's HITL gate is implemented in the planner's prompt rules ·
    this tool itself does not verify approval.
    """
    auth = _AUTHS.get(auth_id)
    if not auth:
        raise ValueError(f"auth_id {auth_id} not found")

    # Idempotency: if this auth was already captured, return the same txn.
    if auth["captured_txn_id"]:
        return CaptureResult(
            transaction_id=auth["captured_txn_id"],
            auth_id=auth_id,
            amount_inr=auth["amount_inr"],
            vendor=auth["vendor"],
            status="approved",
            idempotent_replay=True,
        )

    txn_id = f"TXN-{uuid.uuid4().hex[:12].upper()}"
    _TXNS[txn_id] = {
        "amount_inr": auth["amount_inr"],
        "vendor": auth["vendor"],
        "refunded_amount": 0,
        "auth_id": auth_id,
    }
    auth["captured_txn_id"] = txn_id
    log.info("mcp.payment.capture txn=%s auth=%s amount=%d",
             txn_id, auth_id, auth["amount_inr"])
    return CaptureResult(
        transaction_id=txn_id,
        auth_id=auth_id,
        amount_inr=auth["amount_inr"],
        vendor=auth["vendor"],
        status="approved",
        idempotent_replay=False,
    )


@mcp.tool()
async def refund(transaction_id: str, amount_inr: int) -> RefundResult:
    """Refund part (or all) of a captured transaction."""
    txn = _TXNS.get(transaction_id)
    if not txn:
        raise ValueError(f"transaction_id {transaction_id} not found")
    available = txn["amount_inr"] - txn["refunded_amount"]
    if amount_inr > available:
        raise ValueError(f"refund {amount_inr} > available {available}")
    refund_id = f"REF-{uuid.uuid4().hex[:10].upper()}"
    txn["refunded_amount"] += int(amount_inr)
    log.info("mcp.payment.refund ref=%s txn=%s amount=%d",
             refund_id, transaction_id, amount_inr)
    return RefundResult(
        refund_id=refund_id,
        transaction_id=transaction_id,
        amount_inr=int(amount_inr),
        status="refunded",
    )


# ─── HTTP surface ────────────────────────────────────────────────────────

@mcp.custom_route("/health", methods=["GET"])
async def health(_request):
    return JSONResponse({"status": "ok", "service": "mcp-payment"})


app = mcp.streamable_http_app()
