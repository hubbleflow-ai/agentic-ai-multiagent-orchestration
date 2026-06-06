"""concierge-voice · the Gemini Live front-end + text trigger.

Two endpoints:

  POST /trip  · text-driven trip trigger (used by the dashboard "Plan a trip"
                button during development, and by clients that want a simple
                JSON-in JSON-out interface). Forwards the brief to the Planner
                and returns the structured result.

  WS  /voice  · Gemini Live bridge. Bidirectional audio + function-call relay.
                Browser sends 16k mono PCM frames; Concierge replies with 24k
                PCM audio. The Live session has ONE tool: delegate_trip(brief)
                that POSTs to the Planner.

Phase 2A: /trip is fully wired. /voice is implemented but depends on the
Gemini Live model name in env (GEMINI_LIVE_MODEL) matching the model the
user's Google AI Studio key has access to. If voice fails, /trip still works
so the demo isn't blocked.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from shared.observability import setup_observability

setup_observability("concierge-voice")

app = FastAPI(title="concierge-voice", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

log = logging.getLogger(__name__)

PLANNER_URL = os.environ.get("PLANNER_URL", "http://planner:8001")
LIVE_MODEL = os.environ.get("GEMINI_LIVE_MODEL", "gemini-2.0-flash-exp")
TTS_MODEL = os.environ.get("GEMINI_TTS_MODEL", "gemini-2.5-flash-preview-tts")
VOICE_NAME = os.environ.get("CONCIERGE_VOICE_NAME", "Charon")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")

_http = httpx.AsyncClient(timeout=180.0)


# ─── data shapes ─────────────────────────────────────────────────────────

class TripRequest(BaseModel):
    """Text-driven trip brief. The same shape the Live `delegate_trip` tool
    produces from the user's spoken request."""
    prompt: str = ""
    destination: str = "tokyo"
    depart_date: str = "2026-10-15"
    return_date: str = "2026-10-19"
    pax: int = 2
    budget_inr: int = 200_000
    interests: list[str] = []
    origin: str = "BLR"


# ─── health ──────────────────────────────────────────────────────────────

@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "concierge-voice",
        "live_model": LIVE_MODEL,
        "voice_name": VOICE_NAME,
        "live_available": bool(GOOGLE_API_KEY),
    }


# ─── TTS · Gemini's preview-tts model ─────────────────────────────────────

import base64
import re

from fastapi import HTTPException
from sse_starlette.sse import EventSourceResponse


class TtsRequest(BaseModel):
    text: str


@app.post("/tts/stream")
async def tts_stream(req: TtsRequest):
    """Stream Gemini-generated audio for the given text.

    Uses gemini-2.5-flash-preview-tts via generate_content_stream so audio
    arrives in chunks the browser can start playing immediately. Output is
    24kHz mono PCM int16. Each SSE event carries one chunk as base64.

    Event types:
      audio  · {data: "<base64 PCM>"}
      done   · {}
      error  · {message: "..."}
    """
    if not _LIVE_IMPORT_OK:
        raise HTTPException(503, f"google-genai SDK not available: {_live_import_err}")
    if not GOOGLE_API_KEY:
        raise HTTPException(503, "GOOGLE_API_KEY not set")
    if not req.text or not req.text.strip():
        raise HTTPException(400, "text is required")

    text = req.text.strip()
    log.info("tts.stream text_len=%d voice=%s", len(text), VOICE_NAME)

    async def events():
        try:
            client = genai.Client(api_key=GOOGLE_API_KEY)
            config = gtypes.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=gtypes.SpeechConfig(
                    voice_config=gtypes.VoiceConfig(
                        prebuilt_voice_config=gtypes.PrebuiltVoiceConfig(
                            voice_name=VOICE_NAME,
                        ),
                    ),
                ),
            )

            # Stream chunks so browser can start playback immediately.
            chunk_count = 0
            async for chunk in await client.aio.models.generate_content_stream(
                model=TTS_MODEL,
                contents=text,
                config=config,
            ):
                # Extract inline_data audio from each chunk's candidate parts.
                for cand in (chunk.candidates or []):
                    for part in (cand.content.parts or []) if cand.content else []:
                        inline = getattr(part, "inline_data", None)
                        if inline and inline.data:
                            chunk_count += 1
                            yield {
                                "event": "audio",
                                "data": json.dumps({
                                    "data": base64.b64encode(inline.data).decode("ascii"),
                                }),
                            }
            log.info("tts.stream done chunks=%d", chunk_count)
            yield {"event": "done", "data": "{}"}
        except Exception as e:
            log.exception("tts.stream.error")
            yield {"event": "error", "data": json.dumps({"message": str(e)})}

    return EventSourceResponse(events())


# ─── text-driven trip trigger ────────────────────────────────────────────

# Minimal keyword parser · enough to make "plan a goa trip foodie 50k"
# actually book Goa instead of always defaulting to Tokyo.

_CITY_KEYWORDS = {
    "tokyo":  ["tokyo", "japan"],
    "goa":    ["goa"],
    "bali":   ["bali"],
    "jaipur": ["jaipur"],
    "delhi":  ["delhi"],
    "mumbai": ["mumbai", "bombay"],
    "kerala": ["kerala", "munnar", "kochi", "alleppey"],
}
_INTEREST_KEYWORDS = {
    "food":       ["food", "foodie", "culinary", "eat"],
    "beach":      ["beach", "coast", "ocean", "shore"],
    "shopping":   ["shopping", "shop", "market"],
    "adventure":  ["adventure", "trek", "hike", "rafting"],
    "culture":    ["culture", "temple", "history", "museum"],
}


def _parse_text_brief(prompt: str, base: TripRequest) -> TripRequest:
    """Pull destination / budget / pax / interests out of free-form text.
    Falls back to the brief's defaults whenever a field isn't found."""
    t = prompt.lower()
    fields: dict = {}

    # Destination
    for city, kws in _CITY_KEYWORDS.items():
        if any(k in t for k in kws):
            fields["destination"] = city
            break

    # Budget · "X lakh" → X*100k, "X thousand" / "Xk" → X*1k, raw INR
    m = re.search(r'(\d+(?:\.\d+)?)\s*lakh', t)
    if m:
        fields["budget_inr"] = int(float(m.group(1)) * 100_000)
    else:
        m = re.search(r'(\d+)\s*(?:thousand|k)\b', t)
        if m:
            fields["budget_inr"] = int(m.group(1)) * 1_000
        else:
            m = re.search(r'₹\s*([\d,]+)', t)
            if m:
                fields["budget_inr"] = int(m.group(1).replace(",", ""))

    # Pax · "for two", "for three", "two pax"
    word_to_int = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "couple": 2, "solo": 1}
    for word, n in word_to_int.items():
        if re.search(rf'\b{word}\b', t):
            fields["pax"] = n
            break
    else:
        m = re.search(r'(\d+)\s*(?:pax|people|persons?|travellers?)', t)
        if m:
            fields["pax"] = int(m.group(1))

    # Interests · accumulate any that match
    interests = []
    for tag, kws in _INTEREST_KEYWORDS.items():
        if any(k in t for k in kws):
            interests.append(tag)
    if interests:
        fields["interests"] = interests

    # Merge over the base brief
    merged = {**base.model_dump(), **fields, "prompt": prompt}
    return TripRequest(**merged)


@app.post("/trip")
async def trigger_trip(req: TripRequest) -> dict:
    """Forward the brief to the Planner. Returns the final trip artifacts.

    If the caller provided ONLY `prompt` (the typical free-form text case),
    we run the keyword parser to extract destination/budget/pax/interests
    before forwarding. Structured callers (e.g. EmptyState example cards)
    that pre-fill the brief get their values respected.
    """
    # Detect "text-only" calls: every field except prompt at its default.
    defaults = TripRequest()
    looks_text_only = (
        req.destination == defaults.destination
        and req.budget_inr == defaults.budget_inr
        and req.pax == defaults.pax
        and not req.interests
    )
    if req.prompt and looks_text_only:
        req = _parse_text_brief(req.prompt, defaults)
        log.info("concierge.trip.parsed destination=%s budget=%d pax=%d interests=%s",
                 req.destination, req.budget_inr, req.pax, req.interests)

    log.info("concierge.trip destination=%s budget=%d", req.destination, req.budget_inr)
    try:
        r = await _http.post(f"{PLANNER_URL}/plan", json=req.model_dump())
        r.raise_for_status()
        return r.json()
    except httpx.HTTPError as e:
        log.warning("concierge.trip.planner_error err=%s", e)
        return {"error": "planner unreachable", "detail": str(e)}


# ─── voice bridge (Gemini Live) ──────────────────────────────────────────

# Lazy-import google-genai so the service still boots if the SDK has a
# dependency issue. /voice will return a stub message in that case.
try:
    from google import genai  # type: ignore
    from google.genai import types as gtypes  # type: ignore
    _LIVE_IMPORT_OK = True
    _live_import_err: str | None = None
except Exception as e:  # pragma: no cover
    _LIVE_IMPORT_OK = False
    _live_import_err = repr(e)
    log.warning("concierge.live.sdk_unavailable err=%s", e)


_CONCIERGE_SYSTEM_PROMPT = """You are the Trip Concierge — a warm, professional travel concierge for Indian travellers.

When the user describes a trip, call the `delegate_trip` function with the structured brief. Once you have a result back, narrate it briefly (~30 seconds): what flight, what hotel, what's on the itinerary, what the total comes to, and any prep todos.

Then ASK for payment approval clearly: "shall I confirm <amount> rupees?" Wait for their yes or no.

Tone: confident, friendly, conversational. Match Indian English idioms.
Numbers: speak in lakh / thousand, e.g. "one lakh eighty-seven thousand", not "187,000".
"""


_DELEGATE_TRIP_DECL: dict = {
    "name": "delegate_trip",
    "description": "Plan and book a trip based on the user's request. Returns flights, hotel, itinerary, payment, calendar events, and prep todos.",
    "parameters": {
        "type": "object",
        "properties": {
            "destination": {"type": "string", "description": "City the user wants to visit, e.g. 'tokyo'"},
            "depart_date": {"type": "string", "description": "Departure date in YYYY-MM-DD"},
            "return_date": {"type": "string", "description": "Return date in YYYY-MM-DD"},
            "pax": {"type": "integer", "description": "Number of travellers"},
            "budget_inr": {"type": "integer", "description": "Total budget in INR"},
            "interests": {"type": "array", "items": {"type": "string"}, "description": "Theme(s) like 'food', 'nature'"},
            "origin": {"type": "string", "description": "Origin airport IATA code, e.g. 'BLR'"},
        },
        "required": ["destination", "budget_inr"],
    },
}


@app.websocket("/voice")
async def voice_socket(ws: WebSocket) -> None:
    """Browser ↔ Gemini Live bridge.

    Protocol (browser → server):
      {"type": "audio", "data": "<base64 PCM 16k mono>"}
      {"type": "text",  "data": "<text fallback>"}   # for testing without mic
      {"type": "end"}                                 # half-close

    Protocol (server → browser):
      {"type": "audio", "data": "<base64 PCM 24k mono>"}
      {"type": "transcript", "role": "user"|"concierge", "text": "..."}
      {"type": "tool_call", "name": "...", "args": {...}}
      {"type": "tool_result", "name": "...", "summary": "..."}
      {"type": "error", "detail": "..."}
    """
    await ws.accept()

    if not _LIVE_IMPORT_OK or not GOOGLE_API_KEY:
        await ws.send_json({
            "type": "error",
            "detail": (
                f"Gemini Live unavailable: "
                f"sdk_ok={_LIVE_IMPORT_OK} api_key_set={bool(GOOGLE_API_KEY)} "
                f"err={_live_import_err}"
            ),
        })
        await ws.close()
        return

    client = genai.Client(api_key=GOOGLE_API_KEY)
    config = gtypes.LiveConnectConfig(
        response_modalities=["AUDIO"],
        speech_config=gtypes.SpeechConfig(
            voice_config=gtypes.VoiceConfig(
                prebuilt_voice_config=gtypes.PrebuiltVoiceConfig(voice_name=VOICE_NAME),
            ),
        ),
        system_instruction=gtypes.Content(
            parts=[gtypes.Part(text=_CONCIERGE_SYSTEM_PROMPT)],
            role="user",
        ),
        tools=[gtypes.Tool(function_declarations=[_DELEGATE_TRIP_DECL])],
    )

    try:
        async with client.aio.live.connect(model=LIVE_MODEL, config=config) as session:
            await ws.send_json({"type": "ready", "model": LIVE_MODEL, "voice": VOICE_NAME})
            await asyncio.gather(
                _browser_to_live(ws, session),
                _live_to_browser(ws, session),
            )
    except WebSocketDisconnect:
        log.info("concierge.live.client_disconnect")
    except Exception as e:
        log.exception("concierge.live.session_error")
        try:
            await ws.send_json({"type": "error", "detail": f"Live session error: {e!r}"})
        except Exception:
            pass


async def _browser_to_live(ws: WebSocket, session) -> None:
    """Pump browser frames into the Gemini Live session."""
    import base64

    while True:
        try:
            msg = await ws.receive_text()
        except WebSocketDisconnect:
            return
        try:
            payload = json.loads(msg)
        except json.JSONDecodeError:
            continue

        t = payload.get("type")
        if t == "audio":
            data = base64.b64decode(payload["data"])
            await session.send_realtime_input(
                audio=gtypes.Blob(data=data, mime_type="audio/pcm;rate=16000"),
            )
        elif t == "text":
            # Fallback path for testing without a mic.
            await session.send_realtime_input(text=payload["data"])
        elif t == "end":
            return


async def _live_to_browser(ws: WebSocket, session) -> None:
    """Pump Live responses back to the browser. Handle tool calls inline."""
    import base64

    async for response in session.receive():
        # 1. Audio frames out
        if getattr(response, "data", None):
            await ws.send_json({
                "type": "audio",
                "data": base64.b64encode(response.data).decode("ascii"),
            })

        # 2. Transcripts (if Live sends them)
        sc = getattr(response, "server_content", None)
        if sc and getattr(sc, "model_turn", None):
            for part in sc.model_turn.parts or []:
                if getattr(part, "text", None):
                    await ws.send_json({
                        "type": "transcript",
                        "role": "concierge",
                        "text": part.text,
                    })

        # 3. Tool calls
        tc = getattr(response, "tool_call", None)
        if tc and tc.function_calls:
            tool_responses = []
            for fc in tc.function_calls:
                await ws.send_json({
                    "type": "tool_call",
                    "name": fc.name,
                    "args": dict(fc.args or {}),
                })
                result = await _handle_tool_call(fc.name, dict(fc.args or {}))
                await ws.send_json({
                    "type": "tool_result",
                    "name": fc.name,
                    "summary": _summarize_trip_result(result),
                })
                tool_responses.append(gtypes.FunctionResponse(
                    id=fc.id,
                    name=fc.name,
                    response={"result": result},
                ))
            await session.send_tool_response(function_responses=tool_responses)


async def _handle_tool_call(name: str, args: dict) -> dict:
    """Dispatch a Live function-call to the appropriate backend."""
    if name != "delegate_trip":
        return {"error": f"unknown tool: {name}"}

    # Apply defaults so Live doesn't have to specify every field.
    brief = TripRequest(**{
        "destination": args.get("destination", "tokyo"),
        "depart_date": args.get("depart_date", "2026-10-15"),
        "return_date": args.get("return_date", "2026-10-19"),
        "pax": int(args.get("pax", 2)),
        "budget_inr": int(args.get("budget_inr", 200_000)),
        "interests": args.get("interests", []),
        "origin": args.get("origin", "BLR"),
    })
    r = await _http.post(f"{PLANNER_URL}/plan", json=brief.model_dump())
    r.raise_for_status()
    return r.json()


def _summarize_trip_result(result: dict) -> str:
    """Build a short human-readable summary for the dashboard tool-result event."""
    flight = (result.get("flights") or {}).get("recommended") or {}
    hotel  = (result.get("hotels") or {}).get("recommended") or {}
    pay    = result.get("payment_capture") or {}
    bits = []
    if flight:
        bits.append(f"{flight.get('airline','?')} {flight.get('flight_id','')}")
    if hotel:
        bits.append(hotel.get("name", ""))
    if pay.get("transaction_id"):
        bits.append(f"paid · {pay['transaction_id']}")
    return " · ".join(bits) or "trip planned"
