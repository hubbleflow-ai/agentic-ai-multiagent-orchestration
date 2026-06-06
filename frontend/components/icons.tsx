/**
 * Inline SVG icon set · ported from agentic-ai-introduction with additions
 * for the trip-planner demo (Plane, Hotel, Wallet, Mic, Brain, etc.).
 *
 * Stroke style approximates lucide-react at 1.6px / 24-unit viewBox.
 */

import type { SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement> & { className?: string };

function Base({
  children,
  className,
  ...rest
}: IconProps & { children: React.ReactNode }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width="1em"
      height="1em"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
      {...rest}
    >
      {children}
    </svg>
  );
}

/* ── theme + nav ──────────────────────────────────────────────────── */

export const SunIcon = (p: IconProps) => (
  <Base {...p}>
    <circle cx="12" cy="12" r="4" />
    <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" />
  </Base>
);

export const MoonIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M21 12.79A9 9 0 1 1 11.21 3a7 7 0 0 0 9.79 9.79z" />
  </Base>
);

export const RefreshIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M3 12a9 9 0 0 1 15.5-6.4L21 8" />
    <path d="M21 3v5h-5" />
    <path d="M21 12a9 9 0 0 1-15.5 6.4L3 16" />
    <path d="M3 21v-5h5" />
  </Base>
);

export const ChevronDownIcon = (p: IconProps) => (
  <Base {...p}><path d="m6 9 6 6 6-6" /></Base>
);

export const ArrowUpIcon = (p: IconProps) => (
  <Base {...p}><path d="M12 19V5M5 12l7-7 7 7" /></Base>
);

export const ChevronRightIcon = (p: IconProps) => (
  <Base {...p}><path d="m9 6 6 6-6 6" /></Base>
);

export const XIcon = (p: IconProps) => (
  <Base {...p}><path d="M18 6 6 18M6 6l12 12" /></Base>
);

export const PinIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M12 17v5" />
    <path d="M9 10.76V6h6v4.76l3 3.24v2H6v-2z" />
  </Base>
);

export const CheckIcon = (p: IconProps) => (
  <Base {...p}><path d="M20 6 9 17l-5-5" /></Base>
);

export const SparkleIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M12 3v4M12 17v4M3 12h4M17 12h4M5.6 5.6l2.8 2.8M15.6 15.6l2.8 2.8M5.6 18.4l2.8-2.8M15.6 8.4l2.8-2.8" />
  </Base>
);

/* ── trip-planner specific ────────────────────────────────────────── */

export const BrainIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M9.5 2A2.5 2.5 0 0 0 7 4.5v.5a2.5 2.5 0 0 0-1 4.83V11a3 3 0 0 0 3 3h.5v5.5A2.5 2.5 0 0 0 12 22a2.5 2.5 0 0 0 2.5-2.5V14H15a3 3 0 0 0 3-3V9.83A2.5 2.5 0 0 0 17 5v-.5A2.5 2.5 0 0 0 14.5 2h-5z" />
    <path d="M12 13v2M9 7h1M14 7h1" />
  </Base>
);

export const PlaneIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M17.8 19.2 16 11l3.5-3.5C21 6 21.5 4 21 3c-1-.5-3 0-4.5 1.5L13 8 4.8 6.2c-.5-.1-.9.1-1.1.5l-.3.5c-.2.5-.1 1 .3 1.3L9 12l-2 3H4l-1 1 3 2 2 3 1-1v-3l3-2 3.5 5.3c.3.4.8.5 1.3.3l.5-.2c.4-.3.6-.7.5-1.2z" />
  </Base>
);

export const HotelIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M3 22V4a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v18" />
    <path d="M3 22h18" />
    <path d="M8 6h2M8 10h2M8 14h2M14 6h2M14 10h2M14 14h2" />
    <path d="M10 22v-4h4v4" />
  </Base>
);

export const WalletIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M20 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2z" />
    <path d="M20 7V5a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v2" />
    <circle cx="17" cy="13" r="1.5" />
  </Base>
);

export const UtensilsIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M3 2v7c0 1.1.9 2 2 2h2v11" />
    <path d="M7 2v20" />
    <path d="M21 15V2a4 4 0 0 0-4 4v7c0 1.1.9 2 2 2h2zM21 15v7" />
  </Base>
);

export const CardIcon = (p: IconProps) => (
  <Base {...p}>
    <rect x="2" y="5" width="20" height="14" rx="2" />
    <path d="M2 10h20M6 15h4" />
  </Base>
);

export const CalendarIcon = (p: IconProps) => (
  <Base {...p}>
    <rect x="3" y="4" width="18" height="17" rx="2" />
    <path d="M3 9h18M8 2v4M16 2v4" />
  </Base>
);

export const ChecklistIcon = (p: IconProps) => (
  <Base {...p}>
    <rect x="3" y="3" width="6" height="6" rx="1" />
    <path d="m5 6 1.5 1.5L9 5" />
    <path d="M13 6h8M13 12h8M13 18h8" />
    <rect x="3" y="9" width="6" height="6" rx="1" />
    <rect x="3" y="15" width="6" height="6" rx="1" />
  </Base>
);

export const MicIcon = (p: IconProps) => (
  <Base {...p}>
    <rect x="9" y="2" width="6" height="12" rx="3" />
    <path d="M19 11a7 7 0 0 1-14 0M12 18v4M9 22h6" />
  </Base>
);

export const MicOffIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M2 2l20 20" />
    <path d="M9 9v3a3 3 0 0 0 5.12 2.12M15 9.34V4a3 3 0 0 0-5.94-.6" />
    <path d="M19 11a7 7 0 0 1-9.84 6.4M5 11a7 7 0 0 0 .39 2.3M12 18v4M9 22h6" />
  </Base>
);

export const DashboardIcon = (p: IconProps) => (
  <Base {...p}>
    <rect x="3" y="3" width="7" height="9" rx="1.5" />
    <rect x="14" y="3" width="7" height="5" rx="1.5" />
    <rect x="14" y="12" width="7" height="9" rx="1.5" />
    <rect x="3" y="16" width="7" height="5" rx="1.5" />
  </Base>
);
