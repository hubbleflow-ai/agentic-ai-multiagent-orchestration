"""Mock airline API. Returns canned but realistic flight options.

Endpoints:
  GET  /search?origin=BLR&destination=NRT&depart_date=2026-10-15&pax=2
       → {"options": [{flight_id, airline, dep_time, arr_time, price_inr, ...}, ...]}
  POST /hold {flight_id, pax} → {hold_id, expires_at, price_inr}

Lives at port 9001. The data is deterministic per (origin, destination) so
demos are reproducible.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="mock-airline-api", version="0.1.0")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


_AIRLINES = [
    ("Air India",   "AI",  "Star Alliance"),
    ("IndiGo",      "6E",  "no alliance"),
    ("Vistara",     "UK",  "Star Alliance"),
    ("ANA",         "NH",  "Star Alliance"),
    ("Japan Airlines", "JL", "Oneworld"),
]


def _seed(origin: str, destination: str) -> int:
    """Deterministic seed from route so options are stable across runs."""
    h = hashlib.md5(f"{origin}-{destination}".encode()).digest()
    return int.from_bytes(h[:4], "big")


@app.get("/search")
def search(
    origin: str,
    destination: str,
    depart_date: str,
    pax: int = 2,
    return_date: str | None = None,
) -> dict:
    base = _seed(origin, destination) % 30000 + 60000  # ₹60k-90k base
    options = []
    for i, (name, code, alliance) in enumerate(_AIRLINES[:3]):
        # Spread prices around base
        price_one_way = base + (i - 1) * 6500
        total = price_one_way * pax * (2 if return_date else 1)
        dep_hour = 8 + i * 4
        options.append({
            "flight_id": f"{code}{(base % 900) + 100 + i * 17}",
            "airline": name,
            "alliance": alliance,
            "origin": origin,
            "destination": destination,
            "depart_date": depart_date,
            "return_date": return_date,
            "dep_time": f"{dep_hour:02d}:30",
            "arr_time": f"{(dep_hour + 10) % 24:02d}:45 (+1 day)",
            "duration_hours": 10 + i * 0.5,
            "stops": 0 if i < 2 else 1,
            "price_per_pax_inr": price_one_way,
            "price_total_inr": total,
            "pax": pax,
        })
    return {"options": options}


class HoldBody(BaseModel):
    flight_id: str
    pax: int = 2


@app.post("/hold")
def hold(body: HoldBody) -> dict:
    expires = datetime.now(timezone.utc) + timedelta(minutes=30)
    return {
        "hold_id": f"HLD-{uuid.uuid4().hex[:8].upper()}",
        "flight_id": body.flight_id,
        "expires_at": expires.isoformat(),
        "status": "held",
    }
