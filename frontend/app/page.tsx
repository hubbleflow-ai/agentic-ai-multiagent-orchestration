"use client";

/**
 * Trip Planner · split-screen layout.
 *
 *   ┌────────────────────── Header ──────────────────────┐
 *   │  Conversation                  │  Trip Plan        │
 *   │  (100% ↔ 50%)                  │  (0 ↔ 50%)        │
 *   │                                │                   │
 *   │  - VoiceTranscript / Empty     │  - ChainOfThought │
 *   │  - VoiceBar (mic + text + ↑)   │    (live actions) │
 *   └────────────────────────────────┴───────────────────┘
 */

import { useSelector } from "react-redux";
import type { RootState } from "@/store";
import { Header } from "@/components/Header";
import { VoiceBar } from "@/components/VoiceBar";
import { VoiceTranscript } from "@/components/VoiceTranscript";
import { EmptyState } from "@/components/EmptyState";
import { ArtifactPanel } from "@/components/ArtifactPanel";

export default function HomePage() {
  const turnsCount = useSelector((s: RootState) => s.chat.turns.length);
  const phase = useSelector((s: RootState) => s.agent.phase);
  const panelOpen = useSelector((s: RootState) => s.ui.panel.open);

  const showEmpty = turnsCount === 0 && phase === "idle";

  return (
    <div className="theme-surface flex h-screen flex-col bg-bg text-ink">
      <Header />

      <main className="flex flex-1 overflow-hidden">
        <section
          className="flex h-full flex-shrink-0 flex-col overflow-hidden transition-[width] duration-300 ease-soft"
          style={{ width: panelOpen ? "50%" : "100%" }}
        >
          <div className="flex-1 overflow-y-auto">
            <div className="mx-auto flex h-full w-full max-w-3xl flex-col px-4 pt-2">
              {showEmpty ? <EmptyState /> : <VoiceTranscript />}
            </div>
          </div>

          <div className="mx-auto w-full max-w-3xl px-4 pb-5 pt-2">
            <VoiceBar />
          </div>
        </section>

        <ArtifactPanel />
      </main>
    </div>
  );
}
