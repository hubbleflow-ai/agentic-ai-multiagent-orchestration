"use client";

import { useSelector } from "react-redux";
import type { RootState } from "@/store";
import { CalendarIcon, ChecklistIcon } from "../icons";

export function PostBookingPanel() {
  const calendar = useSelector((s: RootState) => s.trip.calendar);
  const todos = useSelector((s: RootState) => s.trip.todos);
  if (!calendar && !todos) return null;

  return (
    <section className="space-y-2">
      {calendar && (
        <div className="cot-card rounded-xl border border-edge bg-surface px-4 py-3 shadow-soft">
          <header className="flex items-center gap-2 text-[12px] uppercase tracking-wider text-muted">
            <CalendarIcon className="h-[14px] w-[14px]" />
            <span>Calendar</span>
            <span className="ml-auto rounded-full bg-bg/60 px-2 py-0.5 text-[10px] tracking-wider">
              {calendar.mode}
            </span>
          </header>
          <p className="mt-1.5 text-[13px] text-ink">
            {calendar.count} event{calendar.count === 1 ? "" : "s"} added.
          </p>
        </div>
      )}
      {todos && (
        <div className="cot-card rounded-xl border border-edge bg-surface px-4 py-3 shadow-soft">
          <header className="flex items-center gap-2 text-[12px] uppercase tracking-wider text-muted">
            <ChecklistIcon className="h-[14px] w-[14px]" />
            <span>Pre-trip todos</span>
            <span className="ml-auto font-mono normal-case tracking-normal">
              {todos.count}
            </span>
          </header>
          <ul className="mt-2 space-y-1 text-[12.5px]">
            {todos.items.slice(0, 6).map((t, i) => (
              <li key={i} className="flex items-baseline gap-2">
                <span
                  className={`h-1.5 w-1.5 flex-shrink-0 rounded-full ${
                    t.priority === "high"
                      ? "bg-danger"
                      : t.priority === "medium"
                      ? "bg-accent"
                      : "bg-muted"
                  }`}
                />
                <span className="flex-1 text-ink/90">{t.text}</span>
                <span className="font-mono text-[10.5px] text-muted">
                  {t.due_date}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
