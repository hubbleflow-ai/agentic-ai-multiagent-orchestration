"use client";

/**
 * Running budget bar with category breakdown. Becomes visible once
 * set_budget has run; bar fills as commit_spend calls land.
 */

import { useSelector } from "react-redux";
import type { RootState } from "@/store";
import { WalletIcon } from "../icons";

export function BudgetPanel() {
  const budget = useSelector((s: RootState) => s.trip.budget);
  if (!budget) return null;

  const pct = Math.min(100, (budget.spent_inr / budget.limit_inr) * 100);
  const remaining = budget.limit_inr - budget.spent_inr;
  const tone =
    pct < 70 ? "bg-success/70" : pct < 95 ? "bg-accent/70" : "bg-danger/70";

  const sortedCats = Object.entries(budget.categories).sort((a, b) => b[1] - a[1]);

  return (
    <section className="space-y-2">
      <header className="flex items-center gap-2 px-1 text-[12px] uppercase tracking-wider text-muted">
        <WalletIcon className="h-[14px] w-[14px]" />
        <span>Budget</span>
        <span className="ml-auto font-mono normal-case tracking-normal">
          {pct.toFixed(0)}%
        </span>
      </header>
      <div className="rounded-xl border border-edge bg-surface px-4 py-3 shadow-soft">
        <div className="mb-2 flex items-baseline justify-between text-[13px]">
          <span className="font-mono">
            <span className="text-ink">₹{(budget.spent_inr / 1000).toFixed(0)}k</span>
            <span className="text-muted"> of ₹{(budget.limit_inr / 1000).toFixed(0)}k</span>
          </span>
          <span className="font-mono text-[12px] text-muted">
            ₹{(remaining / 1000).toFixed(0)}k left
          </span>
        </div>
        <div className="h-2 overflow-hidden rounded-full bg-bg">
          <div
            className={`h-full transition-[width] duration-500 ease-soft ${tone}`}
            style={{ width: `${pct}%` }}
          />
        </div>
        {sortedCats.length > 0 && (
          <ul className="mt-2.5 space-y-0.5 text-[12px]">
            {sortedCats.map(([cat, amt]) => (
              <li key={cat} className="flex justify-between">
                <span className="capitalize text-muted">{cat}</span>
                <span className="font-mono text-ink">₹{(amt / 1000).toFixed(1)}k</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
