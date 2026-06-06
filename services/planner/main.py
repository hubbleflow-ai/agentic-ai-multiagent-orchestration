"""planner · the real Trip Planner agent.

LangGraph agent with the same shape as Session 5's ToDoAgent:

  StateGraph(MessagesState)
    ├── model node   · Gemini chat with bound tools + system prompt
    └── tools node   · ToolNode that runs whatever the model called

Each A2A worker is a LangChain @tool — the planner LLM decides which
worker to call, with what arguments, and when to stop. The plan is NOT
hardcoded; it emerges from the agent's reasoning.

Multi-turn conversation via LangGraph's MemorySaver checkpointer keyed by
session_id. The user can interrupt with clarifying replies and the agent
picks up where it left off.

Endpoints:
  POST /agent/stream  · streams the agent's events for one user message
                        as SSE (tool.started / tool.finished / agent.message_chunk
                        / agent.message / error / run.complete)
  POST /agent/reset   · drop a session's history
  GET  /health
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import uuid
from contextvars import ContextVar
from typing import Any, AsyncIterator

import redis.asyncio as redis_asyncio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

# google-genai for Gemini fallback TTS · lazy/optional.
try:
    from google import genai as _genai
    from google.genai import types as _gtypes
    _TTS_SDK_OK = True
except Exception as _e:  # pragma: no cover
    _TTS_SDK_OK = False
    _tts_sdk_err = repr(_e)
    _genai = None  # type: ignore
    _gtypes = None  # type: ignore

# google-cloud-texttospeech for the primary streaming path.
try:
    from google.cloud import texttospeech as _cloud_tts
    _CLOUD_TTS_SDK_OK = True
except Exception as _e:  # pragma: no cover
    _CLOUD_TTS_SDK_OK = False
    _cloud_tts_err = repr(_e)
    _cloud_tts = None  # type: ignore

from shared.a2a import A2AClient
from shared.observability import setup_observability

setup_observability("planner")

app = FastAPI(title="planner", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

log = logging.getLogger(__name__)

REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")

# TTS config · planner generates audio inline with the agent stream.
#
# IMPORTANT: AI Studio keys do NOT have access to bidiGenerateContent
# (Gemini Live). Verified by listing models · zero return Live as a
# supported method. So we use the preview-tts model family via the
# standard generate_content_stream API.
#
# Available TTS models on your key:
#   - gemini-3.1-flash-tts-preview  (newest · best latency · default here)
#   - gemini-2.5-flash-preview-tts  (older · slower first chunk)
#   - gemini-2.5-pro-preview-tts    (higher quality · slowest)
#
# Identical text is cached in Redis (~10ms replay) so repeat narrations
# like "Let me check flights now" are instant after first generation.
GEMINI_TTS_MODEL = os.environ.get("GEMINI_TTS_MODEL", "gemini-3.1-flash-tts-preview")
TTS_VOICE = os.environ.get("CONCIERGE_VOICE_NAME", "Charon")
TTS_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60   # 7 days
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")

# Cloud TTS uses a separate API key because Google Cloud policy doesn't
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

_tts_redis = redis_asyncio.from_url(REDIS_URL, decode_responses=True)
_gemini_tts_client = None
_cloud_tts_client = None


def _get_gemini_tts_client():
    """Lazy-init Gemini client (fallback TTS path)."""
    global _gemini_tts_client
    if _gemini_tts_client is None and _TTS_SDK_OK and GOOGLE_API_KEY:
        _gemini_tts_client = _genai.Client(api_key=GOOGLE_API_KEY)
    return _gemini_tts_client


def _get_cloud_tts_client():
    """Lazy-init Cloud TTS async client (primary path)."""
    global _cloud_tts_client
    if _cloud_tts_client is None and _CLOUD_TTS_SDK_OK and GOOGLE_CLOUD_TTS_API_KEY:
        _cloud_tts_client = _cloud_tts.TextToSpeechAsyncClient(
            client_options={"api_key": GOOGLE_CLOUD_TTS_API_KEY},
        )
    return _cloud_tts_client


def _tts_cache_key(text: str, source: str) -> str:
    """Source-tagged cache key so swapping providers re-caches cleanly."""
    voice = CLOUD_TTS_VOICE if source == "cloud" else TTS_VOICE
    h = hashlib.sha256(f"{source}|{voice}|{text}".encode()).hexdigest()
    return f"tts-{source}:{h[:24]}"


async def _stream_tts_to_queue(
    text: str,
    segment_id: str,
    queue: asyncio.Queue,
) -> None:
    """Stream TTS audio chunks for `text` to `queue`.

    Path priority:
      1. Cache hit (either source) · ~10ms replay
      2. Google Cloud TTS Streaming · true streaming, first chunk ~300-500ms
      3. Fallback: Gemini preview-tts · batch behavior, 1-6s for long text

    Audio is 24kHz mono PCM int16 either way. Errors are swallowed — the
    segment text already appeared; missing audio is graceful degradation.
    """
    # 1. Cache check · check Cloud TTS cache first (the path we'll prefer
    #    on fresh generation too), then Gemini cache.
    for src in ("cloud", "gemini"):
        try:
            cached = await _tts_redis.get(_tts_cache_key(text, src))
        except Exception:
            cached = None
        if cached:
            try:
                chunks = json.loads(cached)
                for b64 in chunks:
                    await queue.put({
                        "event": "agent.audio_chunk",
                        "data": json.dumps({"segment_id": segment_id, "data": b64}),
                    })
                await queue.put({
                    "event": "agent.audio_done",
                    "data": json.dumps({"segment_id": segment_id, "cached": True}),
                })
                return
            except (json.JSONDecodeError, TypeError):
                continue

    # 2. Try Cloud TTS Streaming (primary path).
    cloud_chunks = await _stream_via_cloud_tts(text, segment_id, queue)
    if cloud_chunks is not None and cloud_chunks:
        # Cache + done.
        try:
            await _tts_redis.setex(
                _tts_cache_key(text, "cloud"),
                TTS_CACHE_TTL_SECONDS,
                json.dumps(cloud_chunks),
            )
        except Exception:
            pass
        await queue.put({
            "event": "agent.audio_done",
            "data": json.dumps({"segment_id": segment_id, "cached": False, "source": "cloud"}),
        })
        return

    # 3. Fallback to Gemini preview-tts.
    log.info("tts.fallback_to_gemini segment=%s", segment_id)
    gemini_chunks = await _stream_via_gemini_tts(text, segment_id, queue)
    if gemini_chunks is not None and gemini_chunks:
        try:
            await _tts_redis.setex(
                _tts_cache_key(text, "gemini"),
                TTS_CACHE_TTL_SECONDS,
                json.dumps(gemini_chunks),
            )
        except Exception:
            pass
        await queue.put({
            "event": "agent.audio_done",
            "data": json.dumps({"segment_id": segment_id, "cached": False, "source": "gemini"}),
        })
        return

    # Both failed.
    await queue.put({
        "event": "agent.audio_done",
        "data": json.dumps({
            "segment_id": segment_id,
            "error": "all tts paths failed",
        }),
    })


async def _stream_via_cloud_tts(
    text: str,
    segment_id: str,
    queue: asyncio.Queue,
) -> list[str] | None:
    """Stream audio via Google Cloud TTS streaming_synthesize.

    Returns the list of base64 chunks on success (also pushes them to queue
    as they arrive), or None on failure (caller falls back to Gemini).
    """
    client = _get_cloud_tts_client()
    if client is None:
        return None

    async def _requests():
        # First request: streaming config (voice, encoding).
        yield _cloud_tts.StreamingSynthesizeRequest(
            streaming_config=_cloud_tts.StreamingSynthesizeConfig(
                voice=_cloud_tts.VoiceSelectionParams(
                    name=CLOUD_TTS_VOICE,
                    language_code=CLOUD_TTS_LANGUAGE,
                ),
                streaming_audio_config=_cloud_tts.StreamingAudioConfig(
                    # PCM (not LINEAR16) is the only LINEAR-style encoding
                    # the streaming endpoint accepts. Same byte format
                    # (16-bit signed) so frontend Web Audio plays it
                    # identically.
                    audio_encoding=_cloud_tts.AudioEncoding.PCM,
                    sample_rate_hertz=24000,
                ),
            ),
        )
        # Second request: the input text.
        yield _cloud_tts.StreamingSynthesizeRequest(
            input=_cloud_tts.StreamingSynthesisInput(text=text),
        )

    chunks: list[str] = []
    try:
        responses = await client.streaming_synthesize(requests=_requests())
        async for response in responses:
            if response.audio_content:
                b64 = base64.b64encode(response.audio_content).decode("ascii")
                chunks.append(b64)
                await queue.put({
                    "event": "agent.audio_chunk",
                    "data": json.dumps({"segment_id": segment_id, "data": b64}),
                })
        return chunks
    except Exception as e:
        log.warning("cloud_tts.failed segment=%s err=%s", segment_id, e)
        return None


async def _stream_via_gemini_tts(
    text: str,
    segment_id: str,
    queue: asyncio.Queue,
) -> list[str] | None:
    """Fallback path · Gemini preview-tts via generate_content_stream."""
    client = _get_gemini_tts_client()
    if client is None or not _TTS_SDK_OK:
        return None

    config = _gtypes.GenerateContentConfig(
        response_modalities=["AUDIO"],
        speech_config=_gtypes.SpeechConfig(
            voice_config=_gtypes.VoiceConfig(
                prebuilt_voice_config=_gtypes.PrebuiltVoiceConfig(
                    voice_name=TTS_VOICE,
                ),
            ),
        ),
    )

    chunks: list[str] = []
    try:
        async for chunk in await client.aio.models.generate_content_stream(
            model=GEMINI_TTS_MODEL,
            contents=text,
            config=config,
        ):
            for cand in (chunk.candidates or []):
                if not cand.content:
                    continue
                for part in (cand.content.parts or []):
                    inline = getattr(part, "inline_data", None)
                    if inline and inline.data:
                        b64 = base64.b64encode(inline.data).decode("ascii")
                        chunks.append(b64)
                        await queue.put({
                            "event": "agent.audio_chunk",
                            "data": json.dumps({"segment_id": segment_id, "data": b64}),
                        })
        return chunks
    except Exception as e:
        log.warning("gemini_tts.failed segment=%s err=%s", segment_id, e)
        return None


# A2A peer registry — URLs injected by docker-compose.
PEER_URLS: dict[str, str | None] = {
    "research":  os.environ.get("RESEARCH_AGENT_URL"),
    "flight":    os.environ.get("FLIGHT_AGENT_URL"),
    "hotel":     os.environ.get("HOTEL_AGENT_URL"),
    "itinerary": os.environ.get("ITINERARY_AGENT_URL"),
    "budget":    os.environ.get("BUDGET_AGENT_URL"),
    "payment":   os.environ.get("PAYMENT_AGENT_URL"),
    "critic":    os.environ.get("CRITIC_AGENT_URL"),
    "todo":      os.environ.get("TODO_AGENT_URL"),
    "calendar":  os.environ.get("CALENDAR_AGENT_URL"),
}
_peers: dict[str, A2AClient] = {
    name: A2AClient(url) for name, url in PEER_URLS.items() if url
}

# Each agent run binds a trip_id to a ContextVar so tools that need it
# (budget, todo, calendar) can pick it up without the LLM having to
# remember to pass it.
_trip_id: ContextVar[str] = ContextVar("trip_id", default="unbound")


# ─── TOOLS · each is an A2A call to a worker ────────────────────────────

def _wrap(result: dict | list | str) -> str:
    """LangChain tools must return str. We JSON-encode dicts so the LLM
    can reason about structured fields naturally."""
    if isinstance(result, str):
        return result
    return json.dumps(result, ensure_ascii=False)


@tool
async def research_destination(city: str) -> str:
    """Research a travel destination. Returns visa info, weather by month,
    neighborhoods, must-see, and gotchas.

    Args:
        city: Destination city name (e.g. 'tokyo', 'goa', 'bali').
    """
    return _wrap(await _peers["research"].invoke("research_destination", {"city": city}))


@tool
async def set_budget(limit_inr: int) -> str:
    """Set the total trip budget in INR. Call this once at the start once you know the user's budget."""
    return _wrap(await _peers["budget"].invoke(
        "set_budget", {"trip_id": _trip_id.get(), "limit_inr": int(limit_inr)},
    ))


@tool
async def check_budget(proposed_amount_inr: int, category: str) -> str:
    """Check whether a proposed spend fits the remaining budget. Returns {ok, remaining_inr, ...}.

    Call this BEFORE commit_spend, especially after the user has picked a flight or hotel."""
    return _wrap(await _peers["budget"].invoke("check_budget", {
        "trip_id": _trip_id.get(),
        "proposed_amount_inr": int(proposed_amount_inr),
        "category": category,
    }))


@tool
async def commit_spend(amount_inr: int, category: str, description: str) -> str:
    """Commit a spend against the trip's budget AFTER check_budget returned ok=true."""
    return _wrap(await _peers["budget"].invoke("commit_spend", {
        "trip_id": _trip_id.get(),
        "amount_inr": int(amount_inr),
        "category": category,
        "description": description,
    }))


@tool
async def search_flights(
    origin: str,
    destination: str,
    depart_date: str,
    return_date: str,
    pax: int,
    max_budget_inr: int,
) -> str:
    """Search flights between two airports. Returns up to 3 options.

    Args:
        origin: IATA airport code (e.g. 'BLR', 'DEL').
        destination: IATA airport code (e.g. 'NRT' for Tokyo Narita, 'GOI' for Goa).
        depart_date: YYYY-MM-DD.
        return_date: YYYY-MM-DD (empty string for one-way).
        pax: Number of passengers.
        max_budget_inr: Total flight budget cap in INR.

    After this returns, PRESENT the options to the user (bulleted list with airline + price)
    and ASK which one they'd like before calling hold_flight.
    """
    return _wrap(await _peers["flight"].invoke("search_flights", {
        "origin": origin, "destination": destination,
        "depart_date": depart_date, "return_date": return_date,
        "pax": int(pax), "max_budget_inr": int(max_budget_inr),
    }))


@tool
async def hold_flight(flight_id: str, pax: int) -> str:
    """Reserve a specific flight. Only call this AFTER the user has explicitly picked an option."""
    return _wrap(await _peers["flight"].invoke("hold_flight", {
        "flight_id": flight_id, "pax": int(pax),
    }))


@tool
async def search_hotels(
    city: str,
    check_in: str,
    check_out: str,
    pax: int,
    max_per_night_inr: int,
) -> str:
    """Search hotels in a city. Returns options.

    After this returns, PRESENT the options and ASK which one before hold_hotel.
    """
    return _wrap(await _peers["hotel"].invoke("search_hotels", {
        "city": city, "check_in": check_in, "check_out": check_out,
        "pax": int(pax), "max_per_night_inr": int(max_per_night_inr),
    }))


@tool
async def hold_hotel(hotel_id: str, check_in: str, check_out: str) -> str:
    """Reserve a specific hotel. Only call AFTER user has picked an option."""
    return _wrap(await _peers["hotel"].invoke("hold_hotel", {
        "hotel_id": hotel_id, "check_in": check_in, "check_out": check_out,
    }))


@tool
async def build_itinerary(
    city: str,
    days: int,
    interests: list[str],
    hotel_neighborhood: str = "",
) -> str:
    """Build a day-by-day itinerary. Call AFTER hotel is chosen so neighborhood is known."""
    return _wrap(await _peers["itinerary"].invoke("build_itinerary", {
        "city": city, "days": int(days),
        "interests": interests, "hotel_neighborhood": hotel_neighborhood,
    }))


@tool
async def review_itinerary(itinerary_json: str) -> str:
    """Have the critic review the itinerary for issues (e.g. too packed Day 1).
    Pass the itinerary as a JSON string."""
    return _wrap(await _peers["critic"].invoke("review_artifact", {
        "artifact_type": "itinerary", "artifact": json.loads(itinerary_json),
    }))


@tool
async def revise_itinerary(itinerary_json: str, critique: str) -> str:
    """Revise an itinerary based on the critic's critique."""
    return _wrap(await _peers["itinerary"].invoke("revise_itinerary", {
        "itinerary": json.loads(itinerary_json), "critique": critique,
    }))


@tool
async def authorize_payment(amount_inr: int, vendor: str, items_json: str) -> str:
    """Authorize a payment (puts a hold but DOES NOT charge). Returns {auth_id, ...}.

    AFTER this succeeds, you MUST ask the user "Shall I confirm payment of ₹X?"
    and wait for an explicit yes before calling capture_payment.

    Args:
        amount_inr: Total to authorize.
        vendor: Display name for the charge.
        items_json: JSON-encoded list of {label, amount_inr} line items.
    """
    return _wrap(await _peers["payment"].invoke("authorize", {
        "amount_inr": int(amount_inr),
        "vendor": vendor,
        "items": json.loads(items_json),
    }))


@tool
async def capture_payment(auth_id: str) -> str:
    """IRREVERSIBLY charge a previously-authorized payment.

    ONLY call this AFTER the user has explicitly approved ("yes", "confirm",
    "proceed"). Never call without confirmation.
    """
    return _wrap(await _peers["payment"].invoke("capture", {"auth_id": auth_id}))


@tool
async def add_calendar_events(events_json: str) -> str:
    """Add a list of events to the user's Google Calendar.

    Args:
        events_json: JSON-encoded list of {title, date (YYYY-MM-DD), time (HH:MM)}.
    """
    return _wrap(await _peers["calendar"].invoke("add_events", {
        "trip_id": _trip_id.get(),
        "events": json.loads(events_json),
    }))


@tool
async def create_pretrip_todos(destination: str, depart_date: str) -> str:
    """Generate pre-trip prep todos (visa, bank notice, pack, exchange currency, ...)."""
    return _wrap(await _peers["todo"].invoke("create_todos", {
        "trip_id": _trip_id.get(),
        "destination": destination,
        "depart_date": depart_date,
    }))


TOOLS = [
    research_destination,
    set_budget, check_budget, commit_spend,
    search_flights, hold_flight,
    search_hotels, hold_hotel,
    build_itinerary, review_itinerary, revise_itinerary,
    authorize_payment, capture_payment,
    add_calendar_events, create_pretrip_todos,
]


# ─── SYSTEM PROMPT · the orchestrator's role description ────────────────

SYSTEM_PROMPT = """You are the **Trip Planner** — an orchestrator agent that helps Indian travellers plan and book trips end-to-end. Today's context: prices in INR, departures typically from Bangalore (BLR) / Delhi (DEL) / Mumbai (BOM).

## You orchestrate a team of specialist workers (called via tools)

**Information**
- `research_destination(city)` · visa, weather, neighborhoods, must-see, gotchas
- `search_flights(origin, destination, depart_date, return_date, pax, max_budget_inr)` · 3 options
- `search_hotels(city, check_in, check_out, pax, max_per_night_inr)` · options

**Decisions / commitments**
- `hold_flight(flight_id, pax)` · reserve after user picks
- `hold_hotel(hotel_id, check_in, check_out)` · reserve after user picks
- `build_itinerary(city, days, interests, hotel_neighborhood)` · day-by-day plan
- `review_itinerary(itinerary_json)` · critic flags issues
- `revise_itinerary(itinerary_json, critique)` · apply critic feedback

**Budget**
- `set_budget(limit_inr)` · set the ceiling once at start
- `check_budget(proposed_amount_inr, category)` · before committing
- `commit_spend(amount_inr, category, description)` · after each booking

**Payment** (the irreversible step — HITL required)
- `authorize_payment(amount_inr, vendor, items_json)` · hold, not charge
- `capture_payment(auth_id)` · charge · ONLY after user explicit yes

**Post-booking**
- `add_calendar_events(events_json)` · drop flight + hotel + activities on calendar
- `create_pretrip_todos(destination, depart_date)` · visa, bank, packing prep

## How you work

1. **Understand the request.** Extract destination(s), dates, budget, pax, interests from the user's message. If anything essential is missing (destination, dates, or budget), ask ONE concise clarifying question. Do not ask for things you can reasonably infer (e.g. origin defaults to BLR unless stated). For multi-destination requests ("Europe trip with London, Paris, Rome" or "Sydney + Melbourne"), confirm the city sequence and rough split of days.

## MULTI-CITY TRIPS (important · this is a common case)

For trips that visit multiple cities (e.g. Sydney + Melbourne, or hopping across Europe), call the search tools ONCE PER LEG / PER STAY:

  - For flights, every distinct city-to-city leg gets its OWN `search_flights` call:
      · BLR → SYD (outbound)        on depart date
      · SYD → MEL (intra-trip leg)  on the date you move cities
      · MEL → BLR (return)          on return date
    Then call `hold_flight` separately for each leg after the user picks.

  - For hotels, each city stay gets its OWN `search_hotels` call with that city's
    check-in / check-out dates. Two cities → two `search_hotels` calls.

  - Each `build_itinerary` call is per-city as well — call once for Sydney, once
    for Melbourne, with the right days count per city.

The UI surfaces each leg / stay as a separate section, so don't worry about
crowding the screen. Just keep the running total against the budget honest as
you commit each leg's spend.

2. **Set budget first.** Once you know the total budget, call `set_budget` immediately.

3. **Research the destination.** Then `research_destination` to anchor your subsequent choices.

4. **Search and PROPOSE flights.** Call `search_flights`. **DO NOT enumerate the options in your text response** — the UI already shows them as interactive cards in the Trip Plan panel on the right side. The user can see name, price, time, stops at a glance and click to pick.

   Instead, say something brief like:
   "Found 3 flight options. **IndiGo 6E503** is my pick — non-stop and the cheapest at ₹78k. Take a look on the right and let me know which you'd like."

   Always call out the RECOMMENDED option by name + one-sentence reason. Keep it under 2 sentences total.

5. **After user picks a flight:** `hold_flight` → `check_budget` (category "flights") → `commit_spend`.

6. **Search and PROPOSE hotels.** Same brief pattern · DO NOT list hotels in your text. Say something like:
   "Here are 3 hotel options in Shibuya. I'd go with the **Trunk Hotel** — top-rated and good value. Which would you like?"

7. **Build itinerary** based on hotel neighborhood + interests.

8. **Review itinerary** with the critic. If issues, `revise_itinerary` once. Then surface the final itinerary to the user briefly.

9. **Summarise the trip.** Flight + hotel + itinerary + total cost. Then `authorize_payment` for the total and ask: "Shall I confirm payment of ₹X?"

10. **ONLY after user says yes/confirm/proceed:** `capture_payment`. Never call capture without explicit user confirmation.

11. **Post-payment:** `add_calendar_events` (flight + hotel + key activities) and `create_pretrip_todos` in that order.

12. **Final message:** "Your trip is booked. Transaction `TXN-...`, 16 events added to calendar, 7 prep todos created."

## Style
- Indian English. Use **lakh** / **thousand** for money in user-facing text ("₹1.87 lakh", not "187,000").
- Keep messages SHORT — your text is being SPOKEN aloud via TTS. Long enumerations are painful to listen to.
- **NEVER enumerate flight or hotel options in text · the UI shows them as cards.** Just acknowledge ("I've found 3 options") + highlight the recommended one.
- Bold for the recommended option name. Plain prose for confirmations and questions.
- Don't ask multiple questions at once.
- Don't recommend silently — always let the USER pick from options.

## Voice-friendly narration (IMPORTANT for voice mode)

The user may be listening via TTS, not reading. Before any tool call that takes a moment, emit ONE SHORT user-facing line (one sentence, max 12 words) that says what you are about to do, so the user isn't left in silence. Examples:

  - Before search_flights:    "Let me check flight options for you now."
  - Before search_hotels:     "Now searching hotels in {city}."
  - Before build_itinerary:   "Putting together a day-by-day plan."
  - Before review_itinerary:  "Let me double-check that itinerary."
  - Before authorize_payment: "Confirming your booking total now."
  - Before capture_payment:   (do not pre-narrate · this is the HITL gate)

This pre-narration is part of your normal text response RIGHT BEFORE the tool call. A reader sees it as a friendly status line, a listener hears it as live narration of what you are doing."""


# ─── LangGraph · model + tools ───────────────────────────────────────────
#
# Match the Session 5 ReasoningStrategy config:
#   - gemini-3.5-flash (better reasoning than 2.5-flash)
#   - include_thoughts=True so the model emits reasoning we can surface
#   - disable_streaming=True because Gemini's streaming path SILENTLY DROPS
#     `thought` parts when the same response contains a `function_call`.
#     We're a tool-calling agent → this bug would hit us on every turn.
#     ainvoke (non-streaming) preserves both reasoning AND tool calls.
#   - thinking_level controls effort: minimal | low | medium | high
#
# Trade-off vs streaming: chunks don't fire token-by-token. The event
# handler below catches the full message via on_chat_model_end instead,
# which is fine for our UX (one assistant message per turn).

_model_name = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
_llm_kwargs: dict[str, Any] = {
    "model": _model_name,
    "temperature": 0,
    "include_thoughts": True,
    "disable_streaming": True,
}
if os.environ.get("GEMINI_THINKING_LEGACY") == "1":
    # 2.5-family models (gemini-2.5-flash, gemini-2.5-pro) use the older
    # numeric thinking_budget API. -1 = let the model decide.
    _llm_kwargs["thinking_budget"] = -1
else:
    # 3.x-family uses the enum thinking_level (default: medium).
    _llm_kwargs["thinking_level"] = os.environ.get("GEMINI_THINKING_LEVEL", "medium")

llm = ChatGoogleGenerativeAI(**_llm_kwargs)
llm_with_tools = llm.bind_tools(TOOLS)


async def call_model(state: MessagesState) -> dict:
    messages = [SystemMessage(content=SYSTEM_PROMPT)] + state["messages"]
    response = await llm_with_tools.ainvoke(messages)
    return {"messages": [response]}


tool_node = ToolNode(TOOLS)

_graph = StateGraph(MessagesState)
_graph.add_node("model", call_model)
_graph.add_node("tools", tool_node)
_graph.add_edge(START, "model")
_graph.add_conditional_edges("model", tools_condition)
_graph.add_edge("tools", "model")

checkpointer = MemorySaver()
agent = _graph.compile(checkpointer=checkpointer)


# ─── HTTP ────────────────────────────────────────────────────────────────

@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "planner",
        "model": os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
        "peers_configured": sum(1 for v in PEER_URLS.values() if v),
        "tools": len(TOOLS),
    }


class AgentRequest(BaseModel):
    session_id: str
    message: str


class ResetRequest(BaseModel):
    session_id: str


@app.post("/agent/reset")
async def agent_reset(req: ResetRequest) -> dict:
    """Drop a session's history. Next request to /agent/stream starts fresh."""
    try:
        await checkpointer.aput(  # type: ignore[arg-type]
            {"configurable": {"thread_id": req.session_id}},
            checkpoint=None,  # type: ignore[arg-type]
            metadata={},
            new_versions={},
        )
    except Exception:
        pass
    return {"ok": True, "session_id": req.session_id}


def _split_content(content: Any) -> tuple[str, str]:
    """Split a LangChain message's `content` into (visible_text, reasoning).

    Gemini with `include_thoughts=True` returns thoughts in two possible
    shapes depending on langchain-google-genai version:

      Shape A (older · type-tagged):
        [{"type": "text", "text": "..."},
         {"type": "thinking", "text": "..."}]

      Shape B (newer · raw Gemini parts via LangChain):
        [{"type": "text", "text": "..."},
         {"type": "text", "text": "...", "thought": true}]

    Plus the message might also be a plain string when no thoughts emitted.
    Handle all three so reasoning surfaces regardless of SDK version.
    """
    if content is None:
        return "", ""
    if isinstance(content, str):
        return content, ""
    if isinstance(content, list):
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                text_parts.append(block)
                continue
            if not isinstance(block, dict):
                continue

            # Shape A · type field signals thinking
            btype = block.get("type", "")
            # Shape B · boolean flag on a regular text block
            is_thought_flag = bool(block.get("thought") is True)

            text_val = (
                block.get("text")
                or block.get("thinking")
                or (block.get("thought") if isinstance(block.get("thought"), str) else None)
                or ""
            )
            if not isinstance(text_val, str) or not text_val:
                continue

            if is_thought_flag or btype in ("thinking", "thought", "reasoning"):
                reasoning_parts.append(text_val)
            else:
                text_parts.append(text_val)
        return "".join(text_parts), "".join(reasoning_parts)
    return str(content), ""


@app.post("/agent/stream")
async def agent_stream(req: AgentRequest):
    """Stream one agent turn (one user message) as SSE.

    Event types:
      state.phase              · {phase: "thinking" | "tool_calling" | "responding" | "done"}
      tool.started             · {name, args}
      tool.finished            · {name, result}
      agent.message_segment    · {segment_id, content, reasoning}  · each LLM
                                  invocation's text + thoughts. Frontend pushes
                                  as a new chat turn.
      agent.audio_chunk        · {segment_id, data}  · base64 24kHz PCM TTS
                                  audio for that segment, streamed inline so
                                  no second roundtrip is needed
      agent.audio_done         · {segment_id, cached?, error?}
      error                    · {message}
      run.complete             · {}
    """
    log.info("planner.agent.run session=%s msg=%s", req.session_id, req.message[:80])

    config = {"configurable": {"thread_id": req.session_id}}
    session_id = req.session_id
    message = req.message

    async def events() -> AsyncIterator[dict]:
        # ContextVar bound INSIDE the generator (sse_starlette runs this in
        # its own asyncio TaskGroup, a different context than the request
        # handler — binding outside would make reset() raise ValueError).
        token = _trip_id.set(session_id)

        # Async coordination · the planner's astream_events runs as one
        # producer task, each segment kicks off a TTS task in parallel,
        # all push to the same queue. The SSE generator drains the queue
        # in order so the frontend sees: segment_text → audio_chunks of
        # that segment → next segment_text → its audio_chunks → tool events
        # interleaved as they fire.
        queue: asyncio.Queue = asyncio.Queue()
        tts_tasks: list[asyncio.Task] = []
        segment_counter = 0

        async def producer() -> None:
            nonlocal segment_counter
            try:
                await queue.put({
                    "event": "state.phase",
                    "data": json.dumps({"phase": "thinking"}),
                })

                async for ev in agent.astream_events(
                    {"messages": [HumanMessage(content=message)]},
                    config=config,
                    version="v2",
                ):
                    kind = ev.get("event")

                    if kind == "on_chat_model_end":
                        output = ev["data"].get("output")
                        text, reasoning = _split_content(
                            getattr(output, "content", None) if output else None
                        )
                        if text or reasoning:
                            segment_counter += 1
                            segment_id = f"seg-{segment_counter}"
                            await queue.put({
                                "event": "agent.message_segment",
                                "data": json.dumps({
                                    "segment_id": segment_id,
                                    "content": text or "",
                                    "reasoning": reasoning or "",
                                }),
                            })
                            # Kick off TTS in parallel · audio chunks for
                            # this segment will arrive on the same SSE stream.
                            if text and text.strip():
                                tts_tasks.append(asyncio.create_task(
                                    _stream_tts_to_queue(text, segment_id, queue)
                                ))

                    elif kind == "on_tool_start":
                        args = ev["data"].get("input", {}) or {}
                        if isinstance(args, dict) and set(args.keys()) == {"input"}:
                            args = args["input"]
                        await queue.put({
                            "event": "state.phase",
                            "data": json.dumps({"phase": "tool_calling"}),
                        })
                        await queue.put({
                            "event": "tool.started",
                            "data": json.dumps({
                                "name": ev["name"],
                                "args": args if isinstance(args, dict) else {"input": str(args)},
                            }),
                        })

                    elif kind == "on_tool_end":
                        out = ev["data"].get("output", "")
                        result_text = getattr(out, "content", out)
                        try:
                            result = json.loads(result_text) if isinstance(result_text, str) else result_text
                        except (ValueError, TypeError):
                            result = result_text
                        await queue.put({
                            "event": "tool.finished",
                            "data": json.dumps({"name": ev["name"], "result": result}),
                        })
                        await queue.put({
                            "event": "state.phase",
                            "data": json.dumps({"phase": "thinking"}),
                        })

                # Wait for all TTS tasks to complete before signaling end so
                # the user hears every narration even if the planner finishes
                # before some TTS chunks have streamed.
                if tts_tasks:
                    await asyncio.gather(*tts_tasks, return_exceptions=True)

                await queue.put({
                    "event": "state.phase",
                    "data": json.dumps({"phase": "done"}),
                })
                await queue.put({
                    "event": "run.complete",
                    "data": json.dumps({}),
                })
            except Exception as e:
                log.exception("planner.agent.producer_error session=%s", session_id)
                await queue.put({
                    "event": "error",
                    "data": json.dumps({"message": str(e)}),
                })
            finally:
                await queue.put(None)   # sentinel · drain loop exits

        producer_task = asyncio.create_task(producer())

        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield item
        except Exception as e:
            log.exception("planner.agent.drain_error session=%s", session_id)
        finally:
            # Ensure producer is fully done so its exceptions surface.
            try:
                await producer_task
            except Exception:
                pass
            try:
                _trip_id.reset(token)
            except ValueError:
                pass

    return EventSourceResponse(events())
