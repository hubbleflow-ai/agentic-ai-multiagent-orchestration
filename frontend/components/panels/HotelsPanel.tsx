"use client";

/**
 * Hotel stays · one rendered subsection per search_hotels call.
 *
 * Multi-city trips create multiple stays (e.g. Sydney stay + Melbourne stay).
 * Each stay independently goes through searching → options → booked.
 */

import { useDispatch, useSelector } from "react-redux";
import type { AppDispatch, RootState } from "@/store";
import type { HotelOption, HotelStay } from "@/store/tripSlice";
import { runAgent } from "@/store/eventStream";
import { HotelIcon, CheckIcon } from "../icons";

export function HotelsPanel() {
  const stays = useSelector((s: RootState) => s.trip.hotelStays);

  if (stays.length === 0) return null;

  return (
    <section className="space-y-3">
      <header className="flex items-center gap-2 px-1 text-[12px] uppercase tracking-wider text-muted">
        <HotelIcon className="h-[14px] w-[14px]" />
        <span>Hotels</span>
        <span className="ml-auto font-mono normal-case tracking-normal">
          {stays.length} {stays.length === 1 ? "stay" : "stays"}
        </span>
      </header>
      <div className="space-y-4">
        {stays.map((stay) => (
          <StaySection key={stay.key} stay={stay} />
        ))}
      </div>
    </section>
  );
}

function StaySection({ stay }: { stay: HotelStay }) {
  const dispatch = useDispatch<AppDispatch>();
  const phase = useSelector((s: RootState) => s.agent.phase);
  const busy = phase === "thinking" || phase === "tool_calling" || phase === "responding";

  const pickHotel = (o: HotelOption) => {
    if (busy) return;
    dispatch(runAgent(`Book the ${o.name} in ${o.city} for the ${stay.check_in} to ${stay.check_out} stay.`));
  };

  const subheader = (
    <div className="mb-2 flex items-baseline gap-1.5 px-1 text-[11.5px] uppercase tracking-wider text-muted">
      <span className="font-medium text-ink/90">{stay.city}</span>
      <span className="normal-case tracking-normal text-muted/80">
        · {stay.check_in} → {stay.check_out}
      </span>
      {stay.selected && (
        <span className="ml-auto rounded-full bg-success/15 px-2 py-0.5 normal-case tracking-wider text-success">
          booked
        </span>
      )}
      {!stay.selected && stay.searching && stay.options.length === 0 && (
        <span className="ml-auto flex items-center normal-case tracking-normal">
          searching
          <span className="ml-1.5 inline-flex">
            <span className="loading-dot" />
            <span className="loading-dot" />
            <span className="loading-dot" />
          </span>
        </span>
      )}
      {!stay.selected && !stay.searching && stay.options.length > 0 && (
        <span className="ml-auto font-mono normal-case tracking-normal">
          {stay.options.length} options
        </span>
      )}
    </div>
  );

  if (stay.options.length === 0 && stay.searching) {
    return (
      <div>
        {subheader}
        <ul className="space-y-2">
          {[0, 1, 2].map((i) => (
            <HotelSkeleton key={i} delay={i * 90} />
          ))}
        </ul>
      </div>
    );
  }

  if (stay.selected) {
    return (
      <div>
        {subheader}
        <ul>
          <HotelCard
            option={stay.selected}
            isRecommended={false}
            isSelected
            disabled
            onPick={() => {}}
          />
        </ul>
      </div>
    );
  }

  const ordered = [...stay.options].sort((a, b) => {
    const aRec = stay.recommendedId === a.hotel_id ? -1 : 0;
    const bRec = stay.recommendedId === b.hotel_id ? -1 : 0;
    return aRec - bRec || b.rating - a.rating;
  });

  return (
    <div>
      {subheader}
      <ul className="space-y-2">
        {ordered.map((o) => (
          <HotelCard
            key={o.hotel_id}
            option={o}
            isRecommended={stay.recommendedId === o.hotel_id}
            isSelected={false}
            disabled={busy}
            onPick={() => pickHotel(o)}
          />
        ))}
      </ul>
    </div>
  );
}

/* ─── Card ─────────────────────────────────────────────────────────────── */

function HotelCard({
  option: o,
  isRecommended,
  isSelected,
  disabled,
  onPick,
}: {
  option: HotelOption;
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
          disabled
            ? "cursor-not-allowed opacity-90"
            : "hover:border-accent hover:shadow-md"
        }`}
      >
        <div className="flex items-baseline gap-2">
          <span className="font-serif text-[15px] font-medium text-ink">{o.name}</span>
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
          <span className="text-ink">{o.neighborhood}</span>
          <span>· ★ {o.rating.toFixed(1)}</span>
          {o.amenities?.slice(0, 3).map((a) => (
            <span key={a} className="text-muted/80">
              · {a}
            </span>
          ))}
        </div>
        <div className="flex items-baseline justify-between text-[12.5px]">
          <span className="text-muted">
            {o.nights} {o.nights === 1 ? "night" : "nights"} · {o.pax} pax
          </span>
          <span className="font-mono text-[15px] font-medium text-ink">
            ₹{(o.total_inr / 1000).toFixed(0)}k
            <span className="ml-1 text-[10.5px] text-muted">
              · ₹{(o.per_night_inr / 1000).toFixed(0)}k/night
            </span>
          </span>
        </div>
      </button>
    </li>
  );
}

function HotelSkeleton({ delay }: { delay: number }) {
  return (
    <li
      className="skeleton-card cot-card rounded-xl border border-edge bg-surface px-4 py-3 shadow-soft"
      style={{ animationDelay: `${delay}ms` }}
    >
      <div className="flex items-baseline gap-2">
        <div className="skeleton-bar h-4 w-40" />
        <div className="skeleton-bar ml-auto h-4 w-20" />
      </div>
      <div className="mt-2 flex gap-3">
        <div className="skeleton-bar h-3 w-20" />
        <div className="skeleton-bar h-3 w-10" />
        <div className="skeleton-bar h-3 w-12" />
        <div className="skeleton-bar h-3 w-14" />
      </div>
      <div className="mt-2 flex items-baseline justify-between">
        <div className="skeleton-bar h-3 w-24" />
        <div className="skeleton-bar h-5 w-28" />
      </div>
    </li>
  );
}
