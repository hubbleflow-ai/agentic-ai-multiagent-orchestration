"use client";

/**
 * Trip Plan · the revealing right panel · hosts trip ARTIFACTS.
 *
 *   - Flight options (selectable cards · recommended highlighted)
 *   - Hotel options (same pattern)
 *   - Budget bar with category breakdown
 *   - Itinerary days
 *   - Payment status
 *   - Calendar + pre-trip todos
 *
 * Auto-reveals on artifact arrival (notifyPanelUpdate dispatched from
 * eventStream on relevant tool.finished events).
 */

import { useEffect } from "react";
import { useDispatch, useSelector } from "react-redux";
import type { AppDispatch, RootState } from "@/store";
import { closePanel, openPanel, togglePinned } from "@/store/uiSlice";
import { DashboardIcon, PinIcon, XIcon } from "./icons";
import { FlightsPanel } from "./panels/FlightsPanel";
import { HotelsPanel } from "./panels/HotelsPanel";
import { BudgetPanel } from "./panels/BudgetPanel";
import { ItineraryPanel } from "./panels/ItineraryPanel";
import { PaymentPanel } from "./panels/PaymentPanel";
import { PostBookingPanel } from "./panels/PostBookingPanel";

export function ArtifactPanel() {
  const dispatch = useDispatch<AppDispatch>();
  const panel = useSelector((s: RootState) => s.ui.panel);
  const hasAnything = useSelector((s: RootState) => {
    const t = s.trip;
    return (
      t.flightSegments.length > 0 ||
      t.hotelStays.length > 0 ||
      t.itineraries.length > 0 ||
      !!t.budget ||
      t.payment.status !== "idle" ||
      !!t.calendar ||
      !!t.todos
    );
  });

  useEffect(() => {
    if (!panel.open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") dispatch(closePanel());
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [panel.open, dispatch]);

  return (
    <>
      <aside
        aria-hidden={!panel.open}
        aria-label="Trip plan"
        className="theme-surface relative flex h-full flex-shrink-0 overflow-hidden border-l border-edge bg-surfaceMuted transition-[width] duration-300 ease-soft"
        style={{ width: panel.open ? "50%" : "0px" }}
      >
        <div className="relative flex h-full flex-col" style={{ width: "50vw" }}>
          <div className="flex items-center justify-between border-b border-edge px-5 py-3">
            <div className="flex items-center gap-2">
              <DashboardIcon className="h-[18px] w-[18px] text-muted" />
              <span className="font-serif text-[15px] font-medium text-ink">
                Trip Plan
              </span>
            </div>
            <div className="flex items-center gap-0.5">
              <button
                type="button"
                onClick={() => dispatch(togglePinned())}
                aria-label={panel.pinned ? "Unpin panel" : "Pin panel open"}
                title={panel.pinned ? "Unpin" : "Keep open across runs"}
                className={`inline-flex h-8 w-8 items-center justify-center rounded-md transition-colors ${
                  panel.pinned ? "text-accent" : "text-muted hover:bg-bg hover:text-ink"
                }`}
              >
                <PinIcon className="h-[16px] w-[16px]" />
              </button>
              <button
                type="button"
                onClick={() => dispatch(closePanel())}
                aria-label="Close trip plan"
                disabled={panel.pinned}
                className="inline-flex h-8 w-8 items-center justify-center rounded-md text-muted transition-colors hover:bg-bg hover:text-ink disabled:cursor-not-allowed disabled:opacity-40"
              >
                <XIcon className="h-[16px] w-[16px]" />
              </button>
            </div>
          </div>

          <div className="flex-1 overflow-y-auto p-5">
            {hasAnything ? (
              <div className="space-y-4">
                <FlightsPanel />
                <HotelsPanel />
                <BudgetPanel />
                <ItineraryPanel />
                <PaymentPanel />
                <PostBookingPanel />
              </div>
            ) : (
              <EmptyPanel />
            )}
          </div>
        </div>
      </aside>

      {!panel.open && (
        <button
          type="button"
          onClick={() => dispatch(openPanel())}
          aria-label="Show trip plan"
          title="Show trip plan"
          className="theme-surface fixed right-0 top-1/2 z-20 flex -translate-y-1/2 items-center gap-2 rounded-l-xl border border-r-0 border-edge bg-surface px-2 py-3 text-muted shadow-soft transition-all hover:bg-surfaceMuted hover:text-ink"
        >
          <span className="relative inline-flex">
            <DashboardIcon className="h-[18px] w-[18px]" />
            {panel.pendingPulse > 0 && (
              <span
                aria-hidden
                className="absolute -right-1 -top-1 h-2 w-2 rounded-full bg-accent ring-2 ring-surface"
              />
            )}
          </span>
        </button>
      )}
    </>
  );
}

function EmptyPanel() {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 text-center text-muted">
      <p className="text-[13px]">Trip artifacts will appear here.</p>
      <p className="text-[11.5px] text-muted/70">
        Flight options, hotels, budget, itinerary, and booking status — all the
        things the planner produces — land in this panel.
      </p>
    </div>
  );
}
