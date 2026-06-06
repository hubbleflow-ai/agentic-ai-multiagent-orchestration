"use client";

/**
 * Flight segments · one rendered subsection per search_flights call.
 *
 * For a single-leg trip there's one segment (e.g. BLR↔NRT).
 * For multi-city (e.g. Europe hop BLR→LHR, LHR→CDG, CDG→FCO, FCO→BLR)
 * there's one segment per leg. Each segment independently goes through:
 *   searching → options → selected (booked)
 */

import { useDispatch, useSelector } from "react-redux";
import type { AppDispatch, RootState } from "@/store";
import type { FlightOption, FlightSegment } from "@/store/tripSlice";
import { runAgent } from "@/store/eventStream";
import { PlaneIcon, CheckIcon } from "../icons";

export function FlightsPanel() {
  const segments = useSelector((s: RootState) => s.trip.flightSegments);

  if (segments.length === 0) return null;

  return (
    <section className="space-y-3">
      <header className="flex items-center gap-2 px-1 text-[12px] uppercase tracking-wider text-muted">
        <PlaneIcon className="h-[14px] w-[14px]" />
        <span>Flights</span>
        <span className="ml-auto font-mono normal-case tracking-normal">
          {segments.length} {segments.length === 1 ? "leg" : "legs"}
        </span>
      </header>
      <div className="space-y-4">
        {segments.map((seg) => (
          <SegmentSection key={seg.key} segment={seg} />
        ))}
      </div>
    </section>
  );
}

function SegmentSection({ segment }: { segment: FlightSegment }) {
  const dispatch = useDispatch<AppDispatch>();
  const phase = useSelector((s: RootState) => s.agent.phase);
  const busy = phase === "thinking" || phase === "tool_calling" || phase === "responding";

  const pickFlight = (o: FlightOption) => {
    if (busy) return;
    dispatch(runAgent(`I'll go with the ${o.airline} ${o.flight_id} flight for the ${o.origin} → ${o.destination} leg.`));
  };

  const subheader = (
    <div className="mb-2 flex items-baseline gap-1.5 px-1 text-[11.5px] uppercase tracking-wider text-muted">
      <span className="font-medium text-ink/90">
        {segment.origin} → {segment.destination}
      </span>
      <span className="normal-case tracking-normal text-muted/80">· {segment.depart_date}</span>
      {segment.return_date && (
        <span className="normal-case tracking-normal text-muted/80">↔ {segment.return_date}</span>
      )}
      {segment.selected && (
        <span className="ml-auto rounded-full bg-success/15 px-2 py-0.5 normal-case tracking-wider text-success">
          booked
        </span>
      )}
      {!segment.selected && segment.searching && segment.options.length === 0 && (
        <span className="ml-auto flex items-center normal-case tracking-normal">
          searching
          <span className="ml-1.5 inline-flex">
            <span className="loading-dot" />
            <span className="loading-dot" />
            <span className="loading-dot" />
          </span>
        </span>
      )}
      {!segment.selected && !segment.searching && segment.options.length > 0 && (
        <span className="ml-auto font-mono normal-case tracking-normal">
          {segment.options.length} options
        </span>
      )}
    </div>
  );

  if (segment.options.length === 0 && segment.searching) {
    return (
      <div>
        {subheader}
        <ul className="space-y-2">
          {[0, 1, 2].map((i) => (
            <FlightSkeleton key={i} delay={i * 90} />
          ))}
        </ul>
      </div>
    );
  }

  if (segment.selected) {
    return (
      <div>
        {subheader}
        <ul>
          <FlightCard
            option={segment.selected}
            isRecommended={false}
            isSelected
            disabled
            onPick={() => {}}
          />
        </ul>
      </div>
    );
  }

  const ordered = [...segment.options].sort((a, b) => {
    const aRec = segment.recommendedId === a.flight_id ? -1 : 0;
    const bRec = segment.recommendedId === b.flight_id ? -1 : 0;
    return aRec - bRec || a.price_total_inr - b.price_total_inr;
  });

  return (
    <div>
      {subheader}
      <ul className="space-y-2">
        {ordered.map((o) => (
          <FlightCard
            key={o.flight_id}
            option={o}
            isRecommended={segment.recommendedId === o.flight_id}
            isSelected={false}
            disabled={busy}
            onPick={() => pickFlight(o)}
          />
        ))}
      </ul>
    </div>
  );
}

function FlightCard({
  option: o,
  isRecommended,
  isSelected,
  disabled,
  onPick,
}: {
  option: FlightOption;
  isRecommended: boolean;
  isSelected: boolean;
  disabled: boolean;
  onPick: () => void;
}) {
  const borderClass = isSelected
    ? "border-success/60 ring-1 ring-success/30"
    : isRecommended
    ? "border-accent/60 ring-1 ring-accent/20"
    : "border-edge";

  return (
    <li>
      <button
        type="button"
        onClick={onPick}
        disabled={disabled}
        className={`cot-card group flex w-full flex-col gap-2 rounded-xl border bg-surface px-4 py-3 text-left shadow-soft transition-all ${borderClass} ${
          disabled ? "cursor-not-allowed opacity-90" : "hover:border-accent hover:shadow-md"
        }`}
      >
        <div className="flex items-baseline gap-2">
          <span className="font-serif text-[15px] font-medium text-ink">{o.airline}</span>
          <span className="font-mono text-[11.5px] text-muted">{o.flight_id}</span>
          {isSelected && (
            <span className="ml-auto inline-flex items-center gap-1 rounded-full bg-success/15 px-2 py-0.5 text-[10.5px] font-medium uppercase tracking-wider text-success">
              <CheckIcon className="h-[10px] w-[10px]" />
              selected
            </span>
          )}
          {!isSelected && isRecommended && (
            <span className="ml-auto rounded-full bg-accentSoft px-2 py-0.5 text-[10.5px] font-medium uppercase tracking-wider text-accent">
              recommended
            </span>
          )}
        </div>
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-[12.5px] text-muted">
          <span className="font-mono text-ink">{o.dep_time}</span>
          <span>→</span>
          <span className="font-mono text-ink">{o.arr_time}</span>
          <span>· {o.duration_hours}h</span>
          <span>· {o.stops === 0 ? "non-stop" : `${o.stops} stop`}</span>
          {o.alliance && o.alliance !== "no alliance" && (
            <span className="text-muted/70">· {o.alliance}</span>
          )}
        </div>
        <div className="flex items-baseline justify-between text-[12.5px]">
          <span className="text-muted">{o.origin} → {o.destination}</span>
          <span className="font-mono text-[15px] font-medium text-ink">
            ₹{(o.price_total_inr / 1000).toFixed(0)}k
            <span className="ml-1 text-[10.5px] text-muted">total for {o.pax}</span>
          </span>
        </div>
      </button>
    </li>
  );
}

function FlightSkeleton({ delay }: { delay: number }) {
  return (
    <li
      className="skeleton-card cot-card rounded-xl border border-edge bg-surface px-4 py-3 shadow-soft"
      style={{ animationDelay: `${delay}ms` }}
    >
      <div className="flex items-baseline gap-2">
        <div className="skeleton-bar h-4 w-28" />
        <div className="skeleton-bar h-3 w-14" />
        <div className="skeleton-bar ml-auto h-4 w-20" />
      </div>
      <div className="mt-2 flex gap-3">
        <div className="skeleton-bar h-3 w-12" />
        <div className="skeleton-bar h-3 w-3" />
        <div className="skeleton-bar h-3 w-12" />
        <div className="skeleton-bar h-3 w-10" />
        <div className="skeleton-bar h-3 w-14" />
      </div>
      <div className="mt-2 flex items-baseline justify-between">
        <div className="skeleton-bar h-3 w-20" />
        <div className="skeleton-bar h-5 w-24" />
      </div>
    </li>
  );
}
