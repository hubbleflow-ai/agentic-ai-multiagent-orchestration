"""planner · build LangChain tools dynamically from A2A Agent Cards.

For each registered A2A sub-agent URL:
  1. Fetch /.well-known/agent-card.json
  2. Concatenate card.description + every skill's description +
     every skill's examples into one rich docstring
  3. Wrap as a StructuredTool whose `description` (the field the LLM
     actually sees) IS that rich docstring
  4. Return the tool · graph.py merges it into TOOLS, binds to Gemini

What the planner LLM now sees about each sub-agent comes from the
agent's OWN card. Edit a card → restart that agent → restart the
planner → planner's tool catalogue updates. Real dynamic discovery,
nothing about any sub-agent's capabilities is hand-written here.

What remains hardcoded in the planner (correctly · supervisor-level
concerns, not single-agent capability knowledge):

  - Cross-agent orchestration order (system prompt, prompt.py)
  - The agent URL registry (config.py + A2A_SUBAGENTS below)
  - The HITL gate on capture_payment (graph.py interrupt_before)

Defensive choices:
  - Retry on card fetch (8 attempts × 1.5s) · Docker `service_healthy`
    gates this but A2A SDK route mounts can lag /health by a few ms
  - Sync httpx (not async) · module import isn't an async context;
    graph.py imports this and binds tools eagerly at startup
  - Fail loudly if any card never arrives · the planner can't usefully
    run with a missing tool, better to crash than serve broken state
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx
from langchain_core.tools import StructuredTool

from services.planner.config import (
    CRITIC_AGENT_A2A_URL,
    FLIGHT_AGENT_A2A_URL,
    HOTEL_AGENT_A2A_URL,
    ITINERARY_AGENT_A2A_URL,
    RESEARCH_AGENT_A2A_URL,
    TODO_AGENT_A2A_URL,
)
from shared.a2a_helpers import delegate_to_a2a_agent

log = logging.getLogger(__name__)


# ─── A2A sub-agent registry · the ONLY hand-coded knowledge about ────────
# which agents exist in the mesh. Adding a 7th sub-agent: add its URL
# here · everything else (its tool description, examples, brief format)
# comes from that agent's Agent Card.
#
# Key shape: f"delegate_to_{key}" becomes the @tool name the LLM sees.
# So the keys MUST stay in `<agent>_agent` form to preserve the existing
# tool naming convention referenced in prompt.py.

A2A_SUBAGENTS: dict[str, str] = {
    "research_agent":  RESEARCH_AGENT_A2A_URL,
    "flight_agent":    FLIGHT_AGENT_A2A_URL,
    "hotel_agent":     HOTEL_AGENT_A2A_URL,
    "itinerary_agent": ITINERARY_AGENT_A2A_URL,
    "critic_agent":    CRITIC_AGENT_A2A_URL,
    "todo_agent":      TODO_AGENT_A2A_URL,
}


# ─── card fetch (sync · for module-import time) ──────────────────────────

def _fetch_agent_card(agent_url: str, *, attempts: int = 8, delay: float = 1.5) -> dict[str, Any]:
    """GET /.well-known/agent-card.json with retry.

    Even with docker-compose `depends_on: service_healthy`, there's a tiny
    window where /health is 200 but the A2A SDK's card route isn't fully
    mounted yet. Retry covers that race.
    """
    card_url = agent_url.rstrip("/") + "/.well-known/agent-card.json"
    last_err: Exception | None = None
    for i in range(attempts):
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.get(card_url)
                resp.raise_for_status()
                card = resp.json()
                log.info(
                    "planner.dynamic_a2a.card_fetched url=%s name=%s skills=%d",
                    card_url, card.get("name"), len(card.get("skills") or []),
                )
                return card
        except Exception as e:
            last_err = e
            log.warning(
                "planner.dynamic_a2a.fetch_retry attempt=%d/%d url=%s err=%s",
                i + 1, attempts, card_url, e,
            )
            if i < attempts - 1:
                time.sleep(delay)
    raise RuntimeError(
        f"planner could not fetch Agent Card from {card_url} after "
        f"{attempts} attempts. Last error: {last_err}"
    )


# ─── description synthesis ───────────────────────────────────────────────

def _build_rich_description(card: dict[str, Any]) -> str:
    """Concatenate the card's prose into one docstring the LLM will see.

    Layout:

        <top-level card.description>

        ## Skill · <skill 1 name>
        <skill 1 description>

        Example briefs:
          - <example a>
          - <example b>

        ## Skill · <skill 2 name>
        ...
    """
    parts: list[str] = []

    top_desc = (card.get("description") or "").strip()
    if top_desc:
        parts.append(top_desc)

    for skill in (card.get("skills") or []):
        parts.append("")
        skill_label = skill.get("name") or skill.get("id") or "unnamed-skill"
        parts.append(f"## Skill · {skill_label}")
        skill_desc = (skill.get("description") or "").strip()
        if skill_desc:
            parts.append(skill_desc)
        examples = skill.get("examples") or []
        if examples:
            parts.append("")
            parts.append("Example briefs:")
            for ex in examples:
                parts.append(f"  - {ex}")

    return "\n".join(parts).strip()


# ─── tool factory ────────────────────────────────────────────────────────

def _make_delegate_tool(agent_name: str, agent_url: str, description: str) -> StructuredTool:
    """Build a StructuredTool whose .description is what we discovered.

    StructuredTool.from_function lets us set name+description at runtime ·
    the standard `@tool` decorator can't easily take a runtime-computed
    description string.
    """

    async def delegate(brief: str) -> str:
        """Delegate a brief to the A2A sub-agent and return its JSON result."""
        result = await delegate_to_a2a_agent(agent_url, brief)
        return json.dumps(result, ensure_ascii=False)

    return StructuredTool.from_function(
        coroutine=delegate,
        name=f"delegate_to_{agent_name}",
        description=description,
    )


# ─── public entrypoints ──────────────────────────────────────────────────

def build_all_a2a_tools() -> list[StructuredTool]:
    """Build delegate_to_<agent> tools for every entry in A2A_SUBAGENTS.

    Called once at planner startup by graph.py. Returns a list of 6
    StructuredTools, each fully described by its agent's card.
    """
    tools: list[StructuredTool] = []
    for agent_name, agent_url in A2A_SUBAGENTS.items():
        card = _fetch_agent_card(agent_url)
        description = _build_rich_description(card)
        log.info(
            "planner.dynamic_a2a.tool_built name=delegate_to_%s description_chars=%d",
            agent_name, len(description),
        )
        tools.append(_make_delegate_tool(agent_name, agent_url, description))
    log.info("planner.dynamic_a2a.tools_ready count=%d", len(tools))
    return tools


# Backward-compat shim · the earlier (flight-only) iteration exported
# this name. Keep it pointing at the new generic builder for any external
# imports that still reference it. Prefer build_all_a2a_tools().
def build_flight_agent_tool() -> StructuredTool:
    card = _fetch_agent_card(FLIGHT_AGENT_A2A_URL)
    return _make_delegate_tool(
        "flight_agent",
        FLIGHT_AGENT_A2A_URL,
        _build_rich_description(card),
    )
