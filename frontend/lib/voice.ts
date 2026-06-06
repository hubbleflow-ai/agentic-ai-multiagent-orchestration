/**
 * Browser-native voice I/O · STT + TTS + VAD.
 *
 * Why browser-native (not Gemini Live):
 *   - Web Speech API has VAD built in (onspeechend) and gives interim
 *     transcripts in real time. Free, zero backend.
 *   - SpeechSynthesis emits `boundary` events at every word boundary,
 *     which lets us highlight the currently-spoken word in the chat.
 *   - Keeps the planner as the single source of truth for reasoning.
 *
 * Trade-off: voice quality is less polished than Gemini Live's Charon.
 * Easy to swap TTS later if needed — the rest of the system doesn't care.
 */

/* ─── Feature detection ─────────────────────────────────────────────── */

type SR = typeof window & {
  webkitSpeechRecognition?: any;
  SpeechRecognition?: any;
};

export function browserSupportsVoice(): { stt: boolean; tts: boolean } {
  if (typeof window === "undefined") return { stt: false, tts: false };
  const w = window as SR;
  return {
    stt: !!(w.SpeechRecognition || w.webkitSpeechRecognition),
    tts: typeof window.speechSynthesis !== "undefined",
  };
}

/* ─── Speech recognition (STT + VAD) ─────────────────────────────────── */

export type SttCallbacks = {
  /** Fires on every recognition update with the accumulated transcript so far.
   *  `isFinal` flips to true when speech ends and the transcript is settled. */
  onTranscript: (text: string, isFinal: boolean) => void;
  /** Fires when speech starts (mic detects voice). */
  onSpeechStart?: () => void;
  /** Fires when speech ends (sustained silence). The hook AUTO-SENDS at this
   *  point — caller doesn't need to do anything explicit. */
  onSpeechEnd?: (finalText: string) => void;
  /** Fires on any error · permission denied, mic unavailable, etc. */
  onError?: (err: string) => void;
};

export class SttSession {
  private rec: any | null = null;
  private finalText = "";
  private interimText = "";
  private cbs: SttCallbacks;
  // Fallback VAD: fire onSpeechEnd after this many ms of silence following
  // the last result event.
  private silenceTimer: number | null = null;
  private readonly SILENCE_MS = 1200;
  // Guard against double-firing. The silence timer AND the browser's `onend`
  // event can both reach the "end of utterance" code path for the same
  // utterance — without this flag, both would fire onSpeechEnd, causing the
  // same message to be sent twice.
  private endFired = false;

  constructor(cbs: SttCallbacks) {
    this.cbs = cbs;
  }

  start(): boolean {
    const w = window as SR;
    const Ctor = w.SpeechRecognition || w.webkitSpeechRecognition;
    if (!Ctor) {
      this.cbs.onError?.("Web Speech API not supported in this browser. Use Chrome.");
      return false;
    }

    const rec = new Ctor();
    rec.continuous = true;
    rec.interimResults = true;
    rec.lang = "en-IN";
    rec.maxAlternatives = 1;

    rec.onstart = () => {
      this.finalText = "";
      this.interimText = "";
      this.endFired = false;
    };

    rec.onspeechstart = () => {
      this.cbs.onSpeechStart?.();
      this.clearSilenceTimer();
    };

    rec.onresult = (e: any) => {
      let interim = "";
      let added = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const transcript = e.results[i][0].transcript;
        if (e.results[i].isFinal) added += transcript;
        else interim += transcript;
      }
      if (added) this.finalText += added;
      this.interimText = interim;

      const combined = (this.finalText + " " + this.interimText).trim();
      this.cbs.onTranscript(combined, false);

      // Reset silence timer · user is still speaking.
      this.resetSilenceTimer();
    };

    rec.onerror = (e: any) => {
      // "no-speech" / "aborted" are normal lifecycle events — not real errors.
      const code = e.error || "unknown";
      if (code === "no-speech" || code === "aborted") return;
      this.cbs.onError?.(`STT error: ${code}`);
    };

    rec.onend = () => {
      // Browser may end recognition spontaneously after long silence. Only
      // fire onSpeechEnd if the silence timer hasn't already done so.
      this.clearSilenceTimer();
      if (this.endFired) return;
      this.endFired = true;
      const final = (this.finalText + " " + this.interimText).trim();
      if (final) {
        this.cbs.onTranscript(final, true);
        this.cbs.onSpeechEnd?.(final);
      }
    };

    try {
      rec.start();
      this.rec = rec;
      return true;
    } catch (err) {
      this.cbs.onError?.(String(err));
      return false;
    }
  }

  stop() {
    this.clearSilenceTimer();
    try {
      this.rec?.stop();
    } catch {
      /* noop */
    }
    this.rec = null;
  }

  /** Force-fire the silence VAD (e.g. on explicit user button press). */
  flushAsSpeechEnd() {
    this.clearSilenceTimer();
    if (this.endFired) {
      this.stop();
      return;
    }
    this.endFired = true;
    const final = (this.finalText + " " + this.interimText).trim();
    if (final) this.cbs.onSpeechEnd?.(final);
    this.stop();
  }

  private resetSilenceTimer() {
    this.clearSilenceTimer();
    this.silenceTimer = window.setTimeout(() => {
      // Trigger end of utterance after sustained silence.
      if (this.endFired) return;
      this.endFired = true;
      const final = (this.finalText + " " + this.interimText).trim();
      if (final) {
        this.cbs.onTranscript(final, true);
        this.cbs.onSpeechEnd?.(final);
      }
      // stop() triggers rec.onend, which will see endFired=true and no-op.
      this.stop();
    }, this.SILENCE_MS);
  }

  private clearSilenceTimer() {
    if (this.silenceTimer != null) {
      window.clearTimeout(this.silenceTimer);
      this.silenceTimer = null;
    }
  }
}

/* ─── Speech synthesis (TTS with word-level sync) ─────────────────────── */

export type TtsCallbacks = {
  /** Fires at every word boundary · `charIndex` is into the original text. */
  onWordBoundary?: (charIndex: number, wordLength: number) => void;
  onStart?: () => void;
  onEnd?: () => void;
  onError?: (msg: string) => void;
};

export class TtsSession {
  private utterance: SpeechSynthesisUtterance | null = null;
  private cancelled = false;

  speak(text: string, cbs: TtsCallbacks = {}): void {
    if (typeof window === "undefined" || !window.speechSynthesis) {
      cbs.onError?.("SpeechSynthesis not supported");
      return;
    }
    this.cancel();
    this.cancelled = false;

    // Strip markdown-ish characters so the TTS reads cleanly.
    // Keep punctuation that affects prosody (commas, periods, ?, !).
    const cleaned = text
      .replace(/\*\*(.*?)\*\*/g, "$1")
      .replace(/\*(.*?)\*/g, "$1")
      .replace(/`(.*?)`/g, "$1")
      .replace(/^[#>\-*\d.]+\s+/gm, "")
      .replace(/\n+/g, ". ");

    const u = new SpeechSynthesisUtterance(cleaned);
    u.rate = 1.05;
    u.pitch = 1.0;
    u.volume = 1.0;

    // Prefer an Indian English voice if available.
    const voices = window.speechSynthesis.getVoices();
    const indianVoice =
      voices.find((v) => v.lang === "en-IN") ||
      voices.find((v) => v.lang.startsWith("en-GB")) ||
      voices.find((v) => v.lang.startsWith("en"));
    if (indianVoice) u.voice = indianVoice;

    u.onstart = () => cbs.onStart?.();
    u.onend = () => {
      if (!this.cancelled) cbs.onEnd?.();
    };
    u.onerror = (e) => cbs.onError?.(`TTS error: ${e.error}`);
    u.onboundary = (e) => {
      if (e.name === "word") {
        cbs.onWordBoundary?.(e.charIndex, e.charLength ?? 0);
      }
    };

    this.utterance = u;
    window.speechSynthesis.speak(u);
  }

  cancel(): void {
    this.cancelled = true;
    if (typeof window !== "undefined" && window.speechSynthesis) {
      window.speechSynthesis.cancel();
    }
    this.utterance = null;
  }

  isSpeaking(): boolean {
    return typeof window !== "undefined" && window.speechSynthesis?.speaking;
  }
}

/* ─── Gemini TTS · streams 24kHz PCM from concierge-voice ─────────────── */

const TTS_URL = (typeof process !== "undefined" && process.env?.NEXT_PUBLIC_TTS_URL)
  || "http://localhost:8000/tts/stream";

/* ── Shared, pre-warmed AudioContext ──────────────────────────────────
 *
 * Browsers spend 100-300ms creating an AudioContext for the first time
 * in a session. We absorb that cost by creating it the MOMENT the user
 * clicks the mic (which is a valid user gesture for audio init), not the
 * moment the first audio chunk arrives.
 */

let _sharedAudioContext: AudioContext | null = null;

export function getOrCreateAudioContext(): AudioContext {
  if (!_sharedAudioContext || _sharedAudioContext.state === "closed") {
    _sharedAudioContext = new AudioContext({ sampleRate: 24000 });
  }
  if (_sharedAudioContext.state === "suspended") {
    // Fire-and-forget · resume() may fail without user gesture.
    _sharedAudioContext.resume().catch(() => {});
  }
  return _sharedAudioContext;
}

/** Call from a user-gesture handler (e.g. mic button click) to pre-warm
 *  the audio pipeline. Safe to call repeatedly. */
export function preWarmAudio(): void {
  getOrCreateAudioContext();
}

/* ── Inline audio chunk player ────────────────────────────────────────
 *
 * Used by the agent SSE stream's `agent.audio_chunk` handler to play
 * audio chunks as they arrive. Sequential gap-free playback per segment
 * AND across segments (so a multi-segment turn plays as one continuous
 * narration). Tracks per-segment timing for setSpeaking/setSpokenMessageId
 * dispatches via the callbacks.
 */

type SegmentPlaybackState = {
  endTime: number;    // ctx.currentTime when this segment's audio ends
  done: boolean;      // backend has signalled all chunks delivered
  started: boolean;   // dispatched onStart
};

const _segmentPlayback = new Map<string, SegmentPlaybackState>();
let _globalPlaybackTime = 0;

export type AudioStreamCallbacks = {
  onSegmentStart?: (segmentId: string) => void;
  onSegmentEnd?: (segmentId: string) => void;
  onAllDone?: () => void;
};

/** Play one audio chunk for the given segment. Schedules gap-free. */
export function playAudioChunk(
  segmentId: string,
  base64: string,
  cbs: AudioStreamCallbacks = {},
): void {
  const ctx = getOrCreateAudioContext();
  if (ctx.state === "suspended") ctx.resume().catch(() => {});

  let seg = _segmentPlayback.get(segmentId);
  if (!seg) {
    seg = {
      endTime: Math.max(ctx.currentTime + 0.02, _globalPlaybackTime),
      done: false,
      started: false,
    };
    _segmentPlayback.set(segmentId, seg);
  }

  const int16 = base64ToInt16(base64);
  if (int16.length === 0) return;
  const float32 = int16ToFloat32(int16);
  const buf = ctx.createBuffer(1, float32.length, 24000);
  buf.getChannelData(0).set(float32);

  const src = ctx.createBufferSource();
  src.buffer = buf;
  src.connect(ctx.destination);
  const startAt = seg.endTime;
  src.start(startAt);

  if (!seg.started) {
    seg.started = true;
    // Schedule the onSegmentStart at the moment this first chunk should
    // begin playing (slightly in the future), not right now.
    const delay = Math.max(0, (startAt - ctx.currentTime) * 1000);
    window.setTimeout(() => cbs.onSegmentStart?.(segmentId), delay);
  }

  seg.endTime = startAt + buf.duration;
  _globalPlaybackTime = Math.max(_globalPlaybackTime, seg.endTime);
}

/** Backend has signalled it has no more chunks for this segment. Schedule
 *  the onSegmentEnd callback for when the queued audio finishes playing. */
export function finalizeAudioSegment(
  segmentId: string,
  cbs: AudioStreamCallbacks = {},
): void {
  const seg = _segmentPlayback.get(segmentId);
  if (!seg) {
    // No chunks ever arrived (e.g. TTS error). Fire end immediately.
    cbs.onSegmentEnd?.(segmentId);
    return;
  }
  seg.done = true;
  const ctx = getOrCreateAudioContext();
  const remainingMs = Math.max(0, (seg.endTime - ctx.currentTime) * 1000);
  window.setTimeout(() => {
    _segmentPlayback.delete(segmentId);
    cbs.onSegmentEnd?.(segmentId);
    // If all segments have ended, fire onAllDone.
    if (_segmentPlayback.size === 0) {
      _globalPlaybackTime = 0;
      cbs.onAllDone?.();
    }
  }, remainingMs);
}

/** Cancel all queued audio · used when voice mode flips off or user
 *  starts a new turn while previous audio is still playing. */
export function cancelAllAudio(): void {
  // Best we can do for queued AudioBufferSourceNodes is reset the
  // shared playback clock; sources already scheduled keep playing.
  // For demo-grade use the next round of source.start() will start
  // immediately after currentTime which feels like an interrupt.
  _segmentPlayback.clear();
  _globalPlaybackTime = 0;
}

function base64ToInt16(b64: string): Int16Array {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  // PCM is little-endian int16.
  return new Int16Array(bytes.buffer, bytes.byteOffset, bytes.byteLength / 2);
}

function int16ToFloat32(int16: Int16Array): Float32Array {
  const out = new Float32Array(int16.length);
  for (let i = 0; i < int16.length; i++) out[i] = int16[i] / 32768;
  return out;
}

/**
 * Gemini-powered TTS · streams audio from `/tts/stream` and plays it
 * via Web Audio with gap-free chunk scheduling. Drives a time-proportional
 * highlight estimator so the karaoke effect still works (less precise than
 * SpeechSynthesis's `boundary` event but the voice quality is much better).
 */
export class GeminiTtsSession {
  private audioContext: AudioContext | null = null;
  private cancelled = false;
  private highlightTimer: number | null = null;
  private abortController: AbortController | null = null;

  async speak(text: string, cbs: TtsCallbacks = {}): Promise<void> {
    this.cancel();
    this.cancelled = false;

    // Reuse the shared pre-warmed AudioContext so we don't pay the
    // 100-300ms cold-start cost on every speak() call.
    const ctx = getOrCreateAudioContext();
    this.audioContext = ctx;
    if (ctx.state === "suspended") await ctx.resume().catch(() => {});
    let playbackTime = ctx.currentTime + 0.05;
    let firstAudioAt: number | null = null;
    let totalChunks = 0;
    let started = false;

    this.abortController = new AbortController();

    try {
      const response = await fetch(TTS_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
        signal: this.abortController.signal,
      });
      if (!response.ok || !response.body) {
        cbs.onError?.(`TTS HTTP ${response.status}`);
        return;
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      const playChunk = (b64: string) => {
        if (this.cancelled) return;
        const int16 = base64ToInt16(b64);
        if (int16.length === 0) return;
        const float32 = int16ToFloat32(int16);
        const buf = ctx.createBuffer(1, float32.length, 24000);
        buf.getChannelData(0).set(float32);
        const src = ctx.createBufferSource();
        src.buffer = buf;
        src.connect(ctx.destination);
        const startAt = Math.max(ctx.currentTime, playbackTime);
        src.start(startAt);
        playbackTime = startAt + buf.duration;
        if (firstAudioAt === null) {
          firstAudioAt = startAt;
          if (!started) {
            started = true;
            cbs.onStart?.();
          }
        }
        totalChunks++;
      };

      while (true) {
        if (this.cancelled) break;
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");

        let i: number;
        while ((i = buffer.indexOf("\n\n")) !== -1) {
          const block = buffer.slice(0, i);
          buffer = buffer.slice(i + 2);
          let eventName = "message";
          const dataLines: string[] = [];
          for (const line of block.replace(/\r/g, "").split("\n")) {
            if (line.startsWith("event:")) eventName = line.slice(6).trim();
            else if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
          }
          const dataStr = dataLines.join("\n");
          if (!dataStr) continue;

          if (eventName === "audio") {
            try {
              const { data } = JSON.parse(dataStr);
              if (typeof data === "string") playChunk(data);
            } catch {
              /* skip malformed */
            }
          } else if (eventName === "error") {
            try {
              const { message } = JSON.parse(dataStr);
              cbs.onError?.(message || "Gemini TTS error");
            } catch {
              cbs.onError?.("Gemini TTS error");
            }
            return;
          } else if (eventName === "done") {
            // Server is done sending audio. Schedule onEnd at the end of
            // the queued playback. Highlight ticker continues until then.
            this.scheduleEnd(ctx, playbackTime, firstAudioAt, text, cbs);
            return;
          }
        }
      }

      // Stream ended without explicit done event · still flush.
      if (totalChunks > 0) {
        this.scheduleEnd(ctx, playbackTime, firstAudioAt, text, cbs);
      } else if (started === false) {
        cbs.onError?.("No audio received from Gemini TTS");
      }
    } catch (e) {
      if ((e as any)?.name === "AbortError") return;
      cbs.onError?.(e instanceof Error ? e.message : String(e));
    }
  }

  private scheduleEnd(
    ctx: AudioContext,
    playbackTime: number,
    firstAudioAt: number | null,
    text: string,
    cbs: TtsCallbacks,
  ) {
    const totalDurationSec = Math.max(0, playbackTime - (firstAudioAt ?? ctx.currentTime));

    // Time-proportional highlight ticker.
    const startedAt = firstAudioAt ?? ctx.currentTime;
    const totalChars = text.length;
    if (this.highlightTimer != null) {
      window.clearInterval(this.highlightTimer);
    }
    this.highlightTimer = window.setInterval(() => {
      if (this.cancelled || ctx.state === "closed") {
        if (this.highlightTimer != null) window.clearInterval(this.highlightTimer);
        return;
      }
      const elapsed = ctx.currentTime - startedAt;
      const prop = totalDurationSec > 0 ? Math.min(1, elapsed / totalDurationSec) : 1;
      const charIdx = Math.floor(prop * totalChars);
      cbs.onWordBoundary?.(charIdx, 0);
      if (prop >= 1) {
        if (this.highlightTimer != null) window.clearInterval(this.highlightTimer);
        this.highlightTimer = null;
      }
    }, 80);

    const endDelayMs = Math.max(0, (playbackTime - ctx.currentTime) * 1000);
    window.setTimeout(() => {
      if (this.cancelled) return;
      cbs.onWordBoundary?.(text.length, 0);
      cbs.onEnd?.();
    }, endDelayMs);
  }

  cancel(): void {
    this.cancelled = true;
    if (this.abortController) {
      try { this.abortController.abort(); } catch { /* noop */ }
      this.abortController = null;
    }
    if (this.highlightTimer != null) {
      window.clearInterval(this.highlightTimer);
      this.highlightTimer = null;
    }
    // Don't close the shared AudioContext · other speak() calls reuse it.
    // Just drop our reference.
    this.audioContext = null;
  }

  isSpeaking(): boolean {
    return this.audioContext !== null && this.audioContext.state !== "closed";
  }
}
