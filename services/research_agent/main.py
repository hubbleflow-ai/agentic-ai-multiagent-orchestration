"""research-agent · A2A worker that researches destinations.

A2A skills:
  - research_destination(city, traveller_origin, interests) →
      {visa_info, weather, neighborhoods, best_time, must_see, gotchas}

For the scaffolding pass this returns canned-but-realistic findings per
city. The Phase 3 LLM-backed implementation will use Gemini + web search.
"""

from __future__ import annotations

from fastapi import FastAPI
from shared.a2a import A2AServer
from shared.observability import setup_observability

setup_observability("research-agent")

app = FastAPI(title="research-agent", version="0.1.0")
a2a = A2AServer(
    app,
    agent_name="research-agent",
    description="Researches travel destinations (visa, weather, neighborhoods, must-see).",
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "research-agent"}


_DESTINATIONS = {
    "tokyo": {
        "visa_info": "Indian passport: e-Visa required, single entry, ~15 days processing. Valid 90 days.",
        "weather": {"oct": "Mild · 14-22°C · 6 mm rain · perfect"},
        "neighborhoods": ["Shinjuku (nightlife, hotels)", "Shibuya (shopping, food)", "Asakusa (culture)", "Ginza (luxury)"],
        "best_time": "Mar-May (sakura) · Sep-Nov (autumn colours, fewer crowds)",
        "must_see": ["Tsukiji Outer Market", "Senso-ji Temple", "Shibuya Crossing", "TeamLab Planets", "Meiji Shrine"],
        "gotchas": [
            "Carry cash · cards not universally accepted at small restaurants",
            "Suica/Pasmo IC card essential for transit",
            "Tattoos may bar entry to onsen",
        ],
    },
    "goa": {
        "visa_info": "Domestic · no visa needed.",
        "weather": {"nov": "27-32°C · dry · post-monsoon · ideal"},
        "neighborhoods": ["North Goa (Vagator, Anjuna · younger crowd)", "South Goa (Palolem · quieter beaches)"],
        "best_time": "Nov-Feb · dry, mild, peak season",
        "must_see": ["Old Goa churches", "Dudhsagar Falls", "Anjuna flea market", "Palolem beach"],
        "gotchas": ["Hire scooter at hotel · safer than street rentals", "Cash for shacks"],
    },
}


@a2a.skill("research_destination")
async def research_destination(payload: dict) -> dict:
    city = (payload.get("city") or "tokyo").lower().strip()
    data = _DESTINATIONS.get(city, _DESTINATIONS["tokyo"])
    return {"city": city.title(), **data}
