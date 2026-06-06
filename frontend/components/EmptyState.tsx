"use client";

/**
 * Hero shown when there's no conversation yet.
 *
 * Example prompts are now actual free-form messages — clicking one drops
 * the text into the composer (via the ref). The planner agent decides what
 * to do with it: extract destination/dates/budget if present, ask clarifying
 * questions if not. No more structured TripBrief hardcoding.
 */

import { useDispatch } from "react-redux";
import type { AppDispatch } from "@/store";
import { runAgent } from "@/store/eventStream";
import { SparkleIcon } from "./icons";

const EXAMPLE_PROMPTS = [
  "Plan a 4-day Tokyo trip for two, foodie focus, budget two lakh",
  "Weekend in Goa next month, beachfront stay, under fifty thousand",
  "Plan a trip",  // tests that the agent asks clarifying questions
  "Bali for two, culture and food, mid-March, 1.5 lakh",
];

export function EmptyState() {
  const dispatch = useDispatch<AppDispatch>();

  const sendDirect = (prompt: string) => {
    dispatch(runAgent(prompt));
  };

  return (
    <div className="flex h-full flex-col items-center justify-center gap-8 px-6 py-12 text-center">
      <div className="flex flex-col items-center gap-4">
        <span
          aria-hidden
          className="inline-flex h-11 w-11 items-center justify-center rounded-full bg-accentSoft text-accent"
        >
          <SparkleIcon className="h-5 w-5" />
        </span>
        <h1 className="font-serif text-[34px] font-medium leading-tight tracking-tight text-ink">
          Where would you like to go?
        </h1>
        <p className="max-w-md text-[14px] text-muted">
          Tell the planner about your trip — destination, dates, budget,
          interests. It'll ask for anything missing and walk you through the
          flights, hotel, itinerary, and payment.
        </p>
      </div>

      <ul className="grid w-full max-w-2xl grid-cols-1 gap-2 sm:grid-cols-2">
        {EXAMPLE_PROMPTS.map((p) => (
          <li key={p}>
            <button
              type="button"
              onClick={() => sendDirect(p)}
              className="theme-surface group flex w-full items-start gap-3 rounded-xl border border-edge bg-surface px-4 py-3 text-left transition-all hover:border-accent/30 hover:bg-surfaceMuted"
            >
              <span className="mt-0.5 text-accent/60 transition-colors group-hover:text-accent">
                ▸
              </span>
              <span className="text-[13px] leading-snug text-ink">{p}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
