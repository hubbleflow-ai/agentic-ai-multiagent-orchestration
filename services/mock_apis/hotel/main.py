"""Mock hotel API. Returns canned hotel options for Tokyo (and other cities).

Endpoints:
  GET  /search?city=tokyo&check_in=2026-10-15&check_out=2026-10-19&pax=2
       → {"options": [{hotel_id, name, neighborhood, rating, per_night_inr, ...}, ...]}
  POST /hold {hotel_id, check_in, check_out} → {hold_id, total_inr}

Lives at port 9002.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="mock-hotel-api", version="0.1.0")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


# A canned hotel inventory per supported city. For unknown cities we fall
# back to the Tokyo set with the city name swapped in.
_INVENTORY: dict[str, list[dict]] = {
    "tokyo": [
        {"name": "Park Hyatt Shinjuku", "neighborhood": "Shinjuku", "rating": 4.8, "per_night_inr": 18500, "amenities": ["spa", "view"]},
        {"name": "Hotel Gracery Shinjuku", "neighborhood": "Shinjuku", "rating": 4.3, "per_night_inr": 12500, "amenities": ["central"]},
        {"name": "Trunk Hotel", "neighborhood": "Shibuya", "rating": 4.5, "per_night_inr": 14500, "amenities": ["design", "rooftop"]},
        {"name": "Hoshinoya Tokyo", "neighborhood": "Otemachi", "rating": 4.7, "per_night_inr": 22000, "amenities": ["onsen", "ryokan"]},
    ],
    "goa": [
        {"name": "Taj Fort Aguada", "neighborhood": "Sinquerim", "rating": 4.6, "per_night_inr": 14000, "amenities": ["beach", "pool"]},
        {"name": "W Goa", "neighborhood": "Vagator", "rating": 4.4, "per_night_inr": 16000, "amenities": ["beach", "nightlife"]},
        {"name": "Lemon Tree Candolim", "neighborhood": "Candolim", "rating": 4.0, "per_night_inr": 6500, "amenities": ["pool"]},
    ],
}


def _nights(check_in: str, check_out: str) -> int:
    try:
        ci = date.fromisoformat(check_in)
        co = date.fromisoformat(check_out)
        return max(1, (co - ci).days)
    except ValueError:
        return 1


@app.get("/search")
def search(
    city: str,
    check_in: str,
    check_out: str,
    pax: int = 2,
    max_per_night_inr: int | None = None,
) -> dict:
    nights = _nights(check_in, check_out)
    city_key = city.lower().strip()
    inventory = _INVENTORY.get(city_key) or _INVENTORY["tokyo"]

    options = []
    for i, h in enumerate(inventory):
        if max_per_night_inr and h["per_night_inr"] > max_per_night_inr:
            continue
        options.append({
            "hotel_id": f"HTL-{city_key[:3].upper()}-{i+1:03d}",
            "name": h["name"],
            "city": city.title(),
            "neighborhood": h["neighborhood"],
            "rating": h["rating"],
            "amenities": h["amenities"],
            "per_night_inr": h["per_night_inr"],
            "nights": nights,
            "total_inr": h["per_night_inr"] * nights,
            "pax": pax,
        })
    return {"options": options}


class HoldBody(BaseModel):
    hotel_id: str
    check_in: str
    check_out: str


@app.post("/hold")
def hold(body: HoldBody) -> dict:
    expires = datetime.now(timezone.utc) + timedelta(minutes=30)
    return {
        "hold_id": f"HLD-{uuid.uuid4().hex[:8].upper()}",
        "hotel_id": body.hotel_id,
        "check_in": body.check_in,
        "check_out": body.check_out,
        "expires_at": expires.isoformat(),
        "status": "held",
    }
