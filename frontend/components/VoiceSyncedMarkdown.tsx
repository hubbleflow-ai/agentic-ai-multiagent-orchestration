"use client";

/**
 * Markdown for assistant content · stable layout, no reflow during TTS.
 *
 * Earlier iterations split the text at the spoken-cutoff position and
 * rendered two separate Markdown blocks (`<Markdown>{spoken}</...>` +
 * `<Markdown>{unspoken}</...>`). That caused visible reflow every time
 * the cutoff advanced because both halves re-parsed independently and
 * markdown constructs (lists, bold, etc.) wrapped differently.
 *
 * This version renders the markdown ONCE. When voice mode is on AND this
 * turn is currently being spoken, it adds a subtle "speaking" indicator
 * (animated dot strip) and a soft accent ring around the message so the
 * user knows which message is being read aloud — without per-word reveal.
 *
 * If you need per-word karaoke later, the right approach is to render
 * markdown to HTML, walk the DOM to wrap each word in a span with a
 * sequence index, and toggle a class. That's significantly more work and
 * a trade-off against stable layout — for the demo, "this message is
 * being spoken" subtle styling is enough.
 */

import { useSelector } from "react-redux";
import type { RootState } from "@/store";
import { Markdown } from "./Markdown";

type Props = {
  text: string;
  done?: boolean;
  messageId?: string;
};

export function VoiceSyncedMarkdown({ text, messageId }: Props) {
  const voiceMode = useSelector((s: RootState) => s.voice.mode);
  const speaking = useSelector((s: RootState) => s.voice.speaking);
  const spokenMessageId = useSelector((s: RootState) => s.voice.spokenMessageId);

  const isBeingSpoken =
    voiceMode === "on" && speaking && messageId !== undefined && spokenMessageId === messageId;

  return (
    <div
      className={`relative rounded-lg transition-all duration-200 ${
        isBeingSpoken ? "ring-1 ring-accent/30 bg-accentSoft/40 px-3 -mx-3" : ""
      }`}
    >
      <Markdown variant="response">{text}</Markdown>
      {isBeingSpoken && (
        <div className="mt-1.5 flex items-center gap-1.5 text-[11px] text-accent">
          <span className="thinking-dot inline-block h-1 w-1 rounded-full bg-accent" />
          <span className="thinking-dot thinking-dot-2 inline-block h-1 w-1 rounded-full bg-accent" />
          <span className="thinking-dot thinking-dot-3 inline-block h-1 w-1 rounded-full bg-accent" />
          <span className="uppercase tracking-wider opacity-70">speaking</span>
        </div>
      )}
    </div>
  );
}
