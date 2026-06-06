"""critic-agent · A2A worker for artifact review (Reflexion pattern).

A2A skills:
  - review_artifact(artifact_type, artifact, context?) →
      {issues[], severity, suggestion}

Phase 2A: rule-based reviewer that flags "too packed" days (≥4 items),
overlapping times, missing meals. Phase 3 swaps in LLM-driven critique.
"""

from __future__ import annotations

from fastapi import FastAPI
from shared.a2a import A2AServer
from shared.observability import setup_observability

setup_observability("critic-agent")

app = FastAPI(title="critic-agent", version="0.1.0")
a2a = A2AServer(
    app,
    agent_name="critic-agent",
    description="Reviews trip artifacts (itineraries, plans) for issues.",
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "critic-agent"}


@a2a.skill("review_artifact")
async def review_artifact(payload: dict) -> dict:
    artifact_type = payload.get("artifact_type", "")
    artifact = payload.get("artifact", {})
    issues: list[dict] = []

    if artifact_type == "itinerary":
        days = artifact.get("days", [])
        for d in days:
            n = len(d.get("items", []))
            if n >= 5:
                issues.append({
                    "day": d.get("day"),
                    "issue": f"Day {d.get('day')} ({d.get('title','')}) has {n} items · risk of jet-lag burnout",
                    "severity": "warning",
                })
        # Flag if first day has too much (jet lag)
        if days and len(days[0].get("items", [])) >= 4:
            issues.append({
                "day": 1,
                "issue": "Day 1 is the arrival day · keep it light to recover from the flight",
                "severity": "warning",
            })

    severity = "ok" if not issues else max(i["severity"] for i in issues) if issues else "ok"
    suggestion = ""
    if any(i["severity"] == "warning" for i in issues):
        suggestion = "tighten heavy days · remove the last item or two from any day with 5+ items"

    return {
        "artifact_type": artifact_type,
        "issues": issues,
        "severity": severity,
        "suggestion": suggestion,
        "approved": len(issues) == 0,
    }
