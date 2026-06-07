"""planner · environment-driven configuration constants.

All env-var lookups live here so the rest of the package can import
constants without re-reading os.environ. Keep this file BORING · just
names + defaults, no logic.
"""

from __future__ import annotations

import os

# ─── infra ───────────────────────────────────────────────────────────────

REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")

# ─── LLM (Gemini) ────────────────────────────────────────────────────────

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
GEMINI_THINKING_LEVEL = os.environ.get("GEMINI_THINKING_LEVEL", "medium")
GEMINI_THINKING_LEGACY = os.environ.get("GEMINI_THINKING_LEGACY") == "1"
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")

# ─── TTS ─────────────────────────────────────────────────────────────────

# AI Studio keys do NOT have access to bidiGenerateContent (Gemini Live).
# Confirmed by listing models · zero return Live as a supported method.
# We use the preview-tts model family via generate_content_stream API.
#
# Available TTS models on a typical AI Studio key:
#   - gemini-3.1-flash-tts-preview  (newest · best latency · default)
#   - gemini-2.5-flash-preview-tts  (older · slower first chunk)
#   - gemini-2.5-pro-preview-tts    (higher quality · slowest)
#
# Identical text is cached in Redis (~10ms replay).
GEMINI_TTS_MODEL = os.environ.get("GEMINI_TTS_MODEL", "gemini-3.1-flash-tts-preview")
TTS_VOICE = os.environ.get("CONCIERGE_VOICE_NAME", "Charon")
TTS_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60   # 7 days

# Cloud TTS uses a SEPARATE API key because Google Cloud policy doesn't
# allow a single restricted key to permit BOTH Gemini API AND Cloud
# Text-to-Speech API. Falls back to GOOGLE_API_KEY if not set (works
# only when GOOGLE_API_KEY is unrestricted).
GOOGLE_CLOUD_TTS_API_KEY = (
    os.environ.get("GOOGLE_CLOUD_TTS_API_KEY", "") or GOOGLE_API_KEY
)

# Chirp3-HD-Charon = high-quality Indian English MALE voice · same Charon
# character as the Gemini fallback. Alternatives (also Indian English):
#   en-IN-Chirp3-HD-Puck       (male · younger)
#   en-IN-Chirp3-HD-Fenrir     (male · deeper)
#   en-IN-Chirp3-HD-Aoede      (female · warm)
#   en-IN-Wavenet-B            (male · older Wavenet · cheaper if billing matters)
CLOUD_TTS_VOICE = os.environ.get("CLOUD_TTS_VOICE", "en-IN-Chirp3-HD-Charon")
CLOUD_TTS_LANGUAGE = os.environ.get("CLOUD_TTS_LANGUAGE", "en-IN")

# ─── A2A sub-agent URLs ──────────────────────────────────────────────────
# Real A2A sub-agents · Planner reaches them via delegate_to_a2a_agent().

FLIGHT_AGENT_A2A_URL    = os.environ.get("FLIGHT_AGENT_A2A_URL",    "http://flight-agent:8010/")
HOTEL_AGENT_A2A_URL     = os.environ.get("HOTEL_AGENT_A2A_URL",     "http://hotel-agent:8011/")
RESEARCH_AGENT_A2A_URL  = os.environ.get("RESEARCH_AGENT_A2A_URL",  "http://research-agent:8018/")
CRITIC_AGENT_A2A_URL    = os.environ.get("CRITIC_AGENT_A2A_URL",    "http://critic-agent:8015/")
ITINERARY_AGENT_A2A_URL = os.environ.get("ITINERARY_AGENT_A2A_URL", "http://itinerary-agent:8012/")
TODO_AGENT_A2A_URL      = os.environ.get("TODO_AGENT_A2A_URL",      "http://todo-agent:8016/")

# ─── MCP server URLs (Planner calls these DIRECTLY · no agent layer) ─────
# Deterministic non-reasoning ops: budget, payment, calendar. Replaced
# Phase 5's budget-agent / payment-agent / calendar-agent legacy services.

MCP_TRIP_STATE_URL = os.environ.get("MCP_TRIP_STATE_URL", "http://mcp-trip-state:9015/mcp")
MCP_PAYMENT_URL    = os.environ.get("MCP_PAYMENT_URL",    "http://mcp-payment:9013/mcp")
MCP_CALENDAR_URL   = os.environ.get("MCP_CALENDAR_URL",   "http://mcp-calendar:9014/mcp")
