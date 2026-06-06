import type { Config } from "tailwindcss";

/**
 * Trip Concierge palette · ported wholesale from agentic-ai-introduction (S5).
 * Two themes share the same CSS-variable names so toggling `data-theme` on
 * <html> retints every surface in one transition.
 *
 * Trip-specific tokens (`flightTint`, `hotelTint`, `budgetTint`) sit on top
 * of the same warm-Claude base so per-panel accents stay in the same family
 * rather than fighting the global palette.
 */

const config: Config = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  darkMode: ["class", '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        bg:           "var(--bg)",
        surface:      "var(--surface)",
        surfaceMuted: "var(--surface-muted)",
        ink:          "var(--ink)",
        muted:        "var(--muted)",
        edge:         "var(--edge)",
        accent:       "var(--accent)",
        accentSoft:   "var(--accent-soft)",
        success:      "var(--success)",
        danger:       "var(--danger)",
        // Per-panel tints sit on top of the warm base, kept low-saturation
        // so they don't compete with --accent.
        flightTint:   "var(--flight-tint)",
        hotelTint:    "var(--hotel-tint)",
        budgetTint:   "var(--budget-tint)",
        paymentTint:  "var(--payment-tint)",
        planTint:     "var(--plan-tint)",
      },
      fontFamily: {
        sans:  ["var(--font-inter)", "system-ui", "sans-serif"],
        serif: ["var(--font-serif)", "Georgia", "serif"],
        mono:  ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      transitionTimingFunction: {
        soft: "cubic-bezier(0.22, 1, 0.36, 1)",
      },
      boxShadow: {
        soft:  "0 1px 2px rgba(0,0,0,0.04), 0 4px 16px rgba(0,0,0,0.04)",
        panel: "-12px 0 32px -16px rgba(0,0,0,0.18), -2px 0 8px -4px rgba(0,0,0,0.06)",
      },
    },
  },
  plugins: [],
};

export default config;
