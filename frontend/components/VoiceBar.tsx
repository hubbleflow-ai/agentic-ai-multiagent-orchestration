"use client";

/**
 * Composer · text + voice in one bar.
 *
 *   [ 🎤 ]  [ textarea · auto-grows up to 6 rows ]  [ ↑ ]
 *           helper text
 *
 * Voice mode behaviour:
 *   - Click mic → SttSession.start() · listens with VAD
 *   - As you speak, interim transcript fills the textbox in real time
 *   - When you stop (sustained silence ~1.2s), speech is auto-sent to
 *     the planner via runAgent(transcript)
 *   - The agent's response streams in AND is spoken via SpeechSynthesis
 *     with word-by-word sync to the chat (see VoiceTranscript)
 *   - Click mic again to stop
 *
 * Text mode behaviour (mic off):
 *   - Normal type + Enter to send · agent reply is NOT spoken
 */

import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from "react";
import { useDispatch, useSelector } from "react-redux";
import type { AppDispatch, RootState } from "@/store";
import { runAgent } from "@/store/eventStream";
import {
  setMode,
  setListening,
  setInterimTranscript,
  setVoiceError,
} from "@/store/voiceSlice";
import { SttSession, browserSupportsVoice, preWarmAudio } from "@/lib/voice";
import { ArrowUpIcon, MicIcon, MicOffIcon } from "./icons";

export type VoiceBarHandle = {
  setValue: (next: string) => void;
  focus: () => void;
};

export const VoiceBar = forwardRef<VoiceBarHandle>(function VoiceBar(_, ref) {
  const dispatch = useDispatch<AppDispatch>();
  const phase = useSelector((s: RootState) => s.agent.phase);
  const busy = phase === "thinking" || phase === "tool_calling" || phase === "responding";

  const voiceMode = useSelector((s: RootState) => s.voice.mode);
  const listening = useSelector((s: RootState) => s.voice.listening);
  const speaking = useSelector((s: RootState) => s.voice.speaking);
  const interimTranscript = useSelector((s: RootState) => s.voice.interimTranscript);

  const [value, setValue] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const sttRef = useRef<SttSession | null>(null);

  // Auto-resize textarea up to 6 rows.
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    const displayed = listening ? interimTranscript : value;
    if (displayed.length === 0) {
      el.style.height = "auto";
    } else {
      el.style.height = Math.min(el.scrollHeight, 6 * 24 + 14) + "px";
    }
  }, [value, interimTranscript, listening]);

  useImperativeHandle(ref, () => ({
    setValue(next: string) {
      setValue(next);
      requestAnimationFrame(() => textareaRef.current?.focus());
    },
    focus() {
      textareaRef.current?.focus();
    },
  }));

  const send = (msg: string) => {
    const trimmed = msg.trim();
    if (!trimmed || busy) return;
    setValue("");
    dispatch(setInterimTranscript(""));
    dispatch(runAgent(trimmed));
  };

  const onSubmit = () => send(value);

  /* ─── mic / voice mode toggle ──────────────────────────────────── */

  const stopListening = () => {
    sttRef.current?.stop();
    sttRef.current = null;
    dispatch(setListening(false));
  };

  const startListening = () => {
    const { stt } = browserSupportsVoice();
    if (!stt) {
      dispatch(setVoiceError("Voice not supported in this browser. Use Chrome."));
      dispatch(setMode("off"));
      return;
    }
    const session = new SttSession({
      onTranscript: (text, _isFinal) => {
        dispatch(setInterimTranscript(text));
      },
      onSpeechStart: () => {
        dispatch(setListening(true));
      },
      onSpeechEnd: (finalText) => {
        // VAD detected end of utterance · auto-send.
        dispatch(setListening(false));
        sttRef.current = null;
        send(finalText);
        // Re-arm for next utterance after a beat (so the agent has time to
        // start responding). User can click mic to restart immediately.
      },
      onError: (err) => {
        dispatch(setVoiceError(err));
        dispatch(setListening(false));
        sttRef.current = null;
      },
    });
    sttRef.current = session;
    if (!session.start()) {
      sttRef.current = null;
    } else {
      dispatch(setListening(true));
    }
  };

  const toggleMic = () => {
    if (busy) return;
    if (listening) {
      sttRef.current?.flushAsSpeechEnd();
      sttRef.current = null;
      dispatch(setListening(false));
      return;
    }
    // Entering voice mode · pre-warm the AudioContext NOW (we're inside a
    // user-gesture handler, which is required for some browsers to allow
    // audio playback). This absorbs the 100-300ms AudioContext cold-start
    // so the first agent reply plays without an extra warmup delay.
    preWarmAudio();
    if (voiceMode === "off") {
      dispatch(setMode("on"));
    }
    startListening();
  };

  // Cleanup on unmount.
  useEffect(() => {
    return () => sttRef.current?.stop();
  }, []);

  /* ─── render ───────────────────────────────────────────────────── */

  const placeholder = listening
    ? "Listening…"
    : speaking
    ? "Planner is speaking…"
    : busy
    ? "Planner is working…"
    : voiceMode === "on"
    ? "Click the mic and speak · or type"
    : "Tell the planner about your trip";

  const displayedValue = listening ? interimTranscript : value;

  return (
    <div className="theme-surface w-full">
      <div className="group relative flex items-end gap-2 rounded-3xl border border-edge bg-surface px-3 py-2.5 shadow-soft transition-all focus-within:border-accent/50 focus-within:shadow-md">
        {/* Mic (left) */}
        <button
          type="button"
          onClick={toggleMic}
          disabled={busy && !listening}
          aria-label={listening ? "Stop listening" : "Start listening"}
          aria-pressed={listening}
          className={`inline-flex h-9 w-9 flex-shrink-0 items-center justify-center self-end rounded-full transition-all ${
            listening
              ? "bg-accent text-white shadow-soft hover:scale-105"
              : speaking
              ? "bg-accentSoft text-accent ring-2 ring-accent/40"
              : "bg-surfaceMuted text-muted hover:bg-accentSoft hover:text-accent"
          } disabled:cursor-not-allowed disabled:opacity-50`}
        >
          {listening ? (
            <MicOffIcon className="h-[18px] w-[18px]" />
          ) : (
            <MicIcon className="h-[18px] w-[18px]" />
          )}
        </button>

        {/* Textarea (centre) — also displays live STT transcript while listening */}
        <textarea
          ref={textareaRef}
          value={displayedValue}
          onChange={(e) => {
            if (!listening) setValue(e.target.value);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              onSubmit();
            }
          }}
          disabled={busy || listening}
          rows={1}
          placeholder={placeholder}
          className={`block flex-1 resize-none bg-transparent py-[7px] text-[15px] leading-6 text-ink placeholder:text-muted focus:outline-none disabled:opacity-100 ${
            listening ? "italic text-accent" : ""
          }`}
          style={{ maxHeight: 6 * 24 }}
        />

        {/* Send (right) */}
        <button
          type="button"
          onClick={onSubmit}
          disabled={busy || listening || !value.trim()}
          aria-label="Send"
          className="inline-flex h-9 w-9 flex-shrink-0 items-center justify-center self-end rounded-full bg-ink text-bg transition-all hover:scale-105 disabled:cursor-not-allowed disabled:bg-edge disabled:text-muted disabled:hover:scale-100"
        >
          <ArrowUpIcon className="h-[18px] w-[18px]" />
        </button>
      </div>
      <p className="mt-2 px-2 text-center text-[11px] text-muted">
        {listening
          ? "Speak naturally · pause for a moment to send"
          : speaking
          ? "Planner is speaking · click mic to interrupt"
          : busy
          ? "The planner is calling its workers…"
          : voiceMode === "on"
          ? "Voice mode on · mic VAD will auto-send · or just type as usual"
          : "Press Enter to send · Shift + Enter for a new line · or click the mic"}
      </p>
    </div>
  );
});
