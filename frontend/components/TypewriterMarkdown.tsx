"use client";

/**
 * Character-by-character typewriter for the live assistant response, with
 * speed that ACCELERATES as the typewriter progresses.
 *
 * When voice mode is ON, the typewriter steps aside — the TTS-driven
 * VoiceSyncedMarkdown component handles word-level reveal synced to audio
 * boundaries. In text mode, this component runs the accelerating reveal.
 */

import { useEffect, useState } from "react";
import { useSelector } from "react-redux";
import type { RootState } from "@/store";
import { Markdown } from "./Markdown";
import { VoiceSyncedMarkdown } from "./VoiceSyncedMarkdown";

type Props = {
  text: string;
  /** When true, snap to the end immediately and stop animating. */
  done?: boolean;
};

export function TypewriterMarkdown({ text, done = false }: Props) {
  const voiceMode = useSelector((s: RootState) => s.voice.mode);

  // Voice mode: TTS drives the word reveal · let it own the rendering.
  if (voiceMode === "on") {
    return <VoiceSyncedMarkdown text={text} done={done} />;
  }

  return <Typewriter text={text} done={done} />;
}

function Typewriter({ text, done }: { text: string; done: boolean }) {
  const [visibleLen, setVisibleLen] = useState(0);

  useEffect(() => {
    if (done) setVisibleLen(text.length);
  }, [done, text.length]);

  useEffect(() => {
    if (text.length === 0) setVisibleLen(0);
  }, [text.length]);

  useEffect(() => {
    if (done) return;
    if (visibleLen >= text.length) return;

    const consumed = visibleLen;
    const wordsSoFar = text.slice(0, consumed).split(/\s+/).filter(Boolean).length;

    let delay: number;
    let stride: number;
    if (wordsSoFar <= 5) { delay = 32; stride = 1; }
    else if (wordsSoFar <= 20) { delay = 16; stride = 1; }
    else if (wordsSoFar <= 50) { delay = 8; stride = 2; }
    else { delay = 3; stride = 4; }

    const timer = window.setTimeout(() => {
      setVisibleLen((v) => Math.min(text.length, v + stride));
    }, delay);
    return () => window.clearTimeout(timer);
  }, [text, visibleLen, done]);

  return <Markdown variant="response">{text.slice(0, visibleLen)}</Markdown>;
}
