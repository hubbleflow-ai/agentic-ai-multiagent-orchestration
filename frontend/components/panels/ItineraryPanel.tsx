"use client";

import { useSelector } from "react-redux";
import type { RootState } from "@/store";
import type { Itinerary } from "@/store/tripSlice";
import { UtensilsIcon } from "../icons";

export function ItineraryPanel() {
  const itineraries = useSelector((s: RootState) => s.trip.itineraries);
  if (itineraries.length === 0) return null;

  return (
    <section className="space-y-3">
      <header className="flex items-center gap-2 px-1 text-[12px] uppercase tracking-wider text-muted">
        <UtensilsIcon className="h-[14px] w-[14px]" />
        <span>Itinerary</span>
        <span className="ml-auto font-mono normal-case tracking-normal">
          {itineraries.length} {itineraries.length === 1 ? "city" : "cities"}
        </span>
      </header>
      <div className="space-y-4">
        {itineraries.map((it) => (
          <CityItinerary key={it.city} itinerary={it} />
        ))}
      </div>
    </section>
  );
}

function CityItinerary({ itinerary }: { itinerary: Itinerary }) {
  return (
    <div>
      <div className="mb-2 flex items-baseline gap-1.5 px-1 text-[11.5px] uppercase tracking-wider text-muted">
        <span className="font-medium text-ink/90">{itinerary.city}</span>
        <span className="normal-case tracking-normal text-muted/80">
          · {itinerary.days.length} {itinerary.days.length === 1 ? "day" : "days"}
        </span>
      </div>
      <div className="space-y-2">
        {itinerary.days.map((day) => (
          <div
            key={day.day}
            className="cot-card rounded-xl border border-edge bg-surface px-4 py-3 shadow-soft"
          >
            <div className="mb-2 flex items-baseline gap-2">
              <span className="font-mono text-[11px] uppercase tracking-wider text-muted">
                Day {day.day}
              </span>
              <span className="font-serif text-[14px] font-medium text-ink">
                {day.title}
              </span>
            </div>
            <ol className="space-y-1 text-[12.5px]">
              {day.items.map((it, i) => (
                <li key={i} className="flex gap-3">
                  <span className="w-12 flex-shrink-0 font-mono text-muted">{it.time}</span>
                  <span className="text-ink/90">{it.what}</span>
                </li>
              ))}
            </ol>
            {day.notes && day.notes.length > 0 && (
              <ul className="mt-2 space-y-0.5 border-t border-edge pt-2">
                {day.notes.map((n, i) => (
                  <li key={i} className="text-[11.5px] italic text-muted">
                    {n}
                  </li>
                ))}
              </ul>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
