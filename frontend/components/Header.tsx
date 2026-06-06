"use client";

/**
 * Slim top bar. Wordmark on the left, action icons on the right.
 *  - New trip   (resets agent session)
 *  - Theme      (light ↔ dark, persisted)
 *  - Plan       (open/close the right panel, pulses if pending updates)
 */

import { useEffect } from "react";
import { useDispatch, useSelector } from "react-redux";
import type { AppDispatch, RootState } from "@/store";
import { resetSession } from "@/store/chatSlice";
import { resetForNewRun } from "@/store/agentSlice";
import { resetTrip } from "@/store/tripSlice";
import {
  setTheme,
  toggleTheme,
  openPanel,
  closePanel,
  type Theme,
} from "@/store/uiSlice";
import { resetAgentSession } from "@/lib/api";
import {
  DashboardIcon,
  MoonIcon,
  RefreshIcon,
  SunIcon,
} from "./icons";

export function Header() {
  const dispatch = useDispatch<AppDispatch>();
  const theme = useSelector((s: RootState) => s.ui.theme);
  const panel = useSelector((s: RootState) => s.ui.panel);
  const sessionId = useSelector((s: RootState) => s.chat.sessionId);

  useEffect(() => {
    const persisted = (typeof window !== "undefined"
      ? (localStorage.getItem("theme") as Theme | null)
      : null);
    if ((persisted === "light" || persisted === "dark") && persisted !== theme) {
      dispatch(setTheme(persisted));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    try {
      localStorage.setItem("theme", theme);
    } catch {
      /* ignore */
    }
  }, [theme]);

  const onNewTrip = async () => {
    // Reset both client and server state for a fresh conversation.
    await resetAgentSession(sessionId);
    dispatch(resetForNewRun());
    dispatch(resetTrip());
    dispatch(resetSession());
  };

  const togglePanel = () => {
    if (panel.open) dispatch(closePanel());
    else dispatch(openPanel());
  };

  return (
    <header className="theme-surface sticky top-0 z-20 flex items-center justify-between border-b border-edge bg-bg/85 px-5 py-3 backdrop-blur">
      <div className="flex items-center gap-2.5">
        <span
          aria-hidden
          className="inline-block h-6 w-6 rounded-full"
          style={{
            background:
              "radial-gradient(circle at 30% 30%, var(--accent), var(--accent) 38%, transparent 70%), var(--surface-muted)",
          }}
        />
        <span className="font-serif text-[17px] font-medium tracking-tight text-ink">
          Hubbleflow Trip Planner
        </span>
      </div>

      <div className="flex items-center gap-1">
        <IconButton label="New trip" onClick={onNewTrip} title="Start a fresh trip · plan and conversation are cleared">
          <RefreshIcon className="h-[18px] w-[18px]" />
        </IconButton>

        <IconButton
          label="Toggle theme"
          onClick={() => dispatch(toggleTheme())}
          title={theme === "light" ? "Switch to dark" : "Switch to light"}
        >
          {theme === "light" ? (
            <MoonIcon className="h-[18px] w-[18px]" />
          ) : (
            <SunIcon className="h-[18px] w-[18px]" />
          )}
        </IconButton>

        <IconButton
          label={panel.open ? "Hide plan" : "Show plan"}
          onClick={togglePanel}
          title={panel.open ? "Hide plan" : "Show plan"}
        >
          <span className="relative inline-flex">
            <DashboardIcon className="h-[18px] w-[18px]" />
            {!panel.open && panel.pendingPulse > 0 && (
              <span
                aria-hidden
                className="absolute -right-0.5 -top-0.5 h-1.5 w-1.5 rounded-full bg-accent ring-2 ring-bg"
              />
            )}
          </span>
        </IconButton>
      </div>
    </header>
  );
}

function IconButton({
  children,
  label,
  onClick,
  title,
}: {
  children: React.ReactNode;
  label: string;
  onClick: () => void;
  title?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      title={title ?? label}
      className="inline-flex h-9 w-9 items-center justify-center rounded-lg text-muted transition-colors hover:bg-surfaceMuted hover:text-ink"
    >
      {children}
    </button>
  );
}
