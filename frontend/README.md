# Trip Concierge Frontend

Next.js 14 + Tailwind + Framer Motion. The split-screen demo dashboard.

## Status

Scaffolding only. The `ConciergePlan` component is implemented as a reference for the animation vocabulary (Framer Motion + the `pulse-dots` keyframe + the design tokens in `animations.ts`). The other panels (Flights, Hotels, Budget, Itinerary, Payment, Calendar, PreTripTodos) and the Gemini Live voice wiring are TODOs.

## Quickstart

```bash
npm install
npm run dev
# open http://localhost:3000
```

Or run via Docker as part of the full mesh:

```bash
cd ..
docker compose up --build
# open http://localhost:3000
```

## Animation vocabulary

See `components/animations.ts` for the Framer Motion variants and `styles/globals.css` for the custom keyframes. Reach for these tokens; don't invent new motion per component:

| Token | Where |
|---|---|
| `staggerFade` | Lists of options appearing (Flights, Hotels) |
| `selectedCard` | Selected option grows, others fade |
| `hitlSlideUp` | Payment approval card appearing |
| `blurReplace` | Stale data being replaced |
| `slideInRight` | Calendar events dropping in |
| `.skeleton` (CSS) | Shimmering placeholder cards |
| `.dot` (CSS) | Three-dot loading indicator |
| `.card-glass` (CSS) | Glass-morphism panel background |
| `.flash-success` (CSS) | Brief green flash on step completion |
| `.wave-bar` (CSS) | Voice waveform bars synced to audio |

## Component map (when fully built)

```
app/page.tsx                      · the dashboard route (split-screen layout)
app/layout.tsx                    · root layout + globals.css import
components/
  ConciergePlan.tsx               · 🧠 the plan panel (top of dashboard) [DONE]
  FlightsPanel.tsx                · 🛫 flight options + selected [TODO]
  HotelsPanel.tsx                 · 🏨 hotel options + selected [TODO]
  BudgetPanel.tsx                 · 💳 running budget bar [TODO]
  ItineraryPanel.tsx              · 🍱 day-by-day itinerary [TODO]
  PaymentPanel.tsx                · 💳 HITL approval card + status [TODO]
  CalendarPanel.tsx               · 📅 scheduled events [TODO]
  PreTripTodos.tsx                · 📋 user-facing prep todos [TODO]
  VoiceWaveform.tsx               · 🎙 mic + waveform (Web Audio API) [TODO]
  animations.ts                   · shared Framer Motion variants [DONE]
styles/globals.css                · Tailwind + custom keyframes [DONE]
```
