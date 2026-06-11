"""planner · streaming TTS pipeline (Cloud TTS primary, Gemini fallback).

Each `agent.message_segment` emitted from the SSE producer kicks off a
parallel TTS task that fans out audio chunks into the same SSE queue
under `agent.audio_chunk` events. The frontend consumes those via the
Web Audio API for inline playback synchronised with text rendering.

Path priority for each segment:
  1. Redis cache hit (either cloud or gemini source) · ~10ms replay
  2. Google Cloud TTS Streaming · true streaming, ~300-500ms first chunk
  3. Gemini preview-tts (fallback) · batch behavior, 1-6s for long text

All audio is 24kHz mono PCM int16 either way.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging

import redis.asyncio as redis_asyncio

from services.planner.config import (
    CLOUD_TTS_LANGUAGE,
    CLOUD_TTS_VOICE,
    GEMINI_TTS_MODEL,
    GOOGLE_API_KEY,
    GOOGLE_CLOUD_TTS_API_KEY,
    REDIS_URL,
    TTS_CACHE_TTL_SECONDS,
    TTS_VOICE,
)

log = logging.getLogger(__name__)


# ─── optional SDK imports (graceful degradation if missing) ──────────────

try:
    from google import genai as _genai
    from google.genai import types as _gtypes
    _TTS_SDK_OK = True
except Exception:
    _TTS_SDK_OK = False
    _genai = None
    _gtypes = None

try:
    from google.cloud import texttospeech as _cloud_tts
    _CLOUD_TTS_SDK_OK = True
except Exception:
    _CLOUD_TTS_SDK_OK = False
    _cloud_tts = None


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


async def stream_tts_to_queue(
    text: str,
    segment_id: str,
    queue: asyncio.Queue,
) -> None:
    """Stream TTS audio chunks for `text` to `queue`.

    Errors are swallowed · the segment text already appeared; missing
    audio is graceful degradation.
    """
    # 1. Cache check (try cloud first since it's the primary generation path).
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

    # 2. Cloud TTS Streaming (primary).
    cloud_chunks = await _stream_via_cloud_tts(text, segment_id, queue)
    if cloud_chunks is not None and cloud_chunks:
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

    # 3. Gemini preview-tts (fallback).
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
        "data": json.dumps({"segment_id": segment_id, "error": "all tts paths failed"}),
    })


async def _stream_via_cloud_tts(text: str, segment_id: str, queue: asyncio.Queue) -> list[str] | None:
    """Stream audio via Google Cloud TTS streaming_synthesize.

    Returns the list of base64 chunks on success (also pushes them to
    queue as they arrive), or None on failure.
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
                    # PCM (not LINEAR16) is the only LINEAR-style encoding the
                    # streaming endpoint accepts. Same byte format (16-bit
                    # signed) so frontend Web Audio plays it identically.
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


async def _stream_via_gemini_tts(text: str, segment_id: str, queue: asyncio.Queue) -> list[str] | None:
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
