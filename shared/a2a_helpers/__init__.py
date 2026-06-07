"""Small client-side helpers for calling A2A sub-agents.

Used by the Planner (Phase 3+) and reused unchanged by sub-agents in
Phase 4 that delegate to peer agents (e.g. critic-agent receiving an
itinerary from itinerary-agent).
"""

from shared.a2a_helpers.client import delegate_to_a2a_agent

__all__ = ["delegate_to_a2a_agent"]
