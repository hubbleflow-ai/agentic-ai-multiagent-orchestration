# Trip Concierge · Multi-Agent Service Mesh

Cohort Session 6 demo. Plans, books, and confirms a 4-day Tokyo trip end to end via voice. Built as **17 Docker services** — 1 Gemini Live voice front-end, 1 Planner supervisor, 9 A2A-speaking worker agents, 3 mock external APIs, Redis, Phoenix, and a Next.js dashboard.

> **Status: scaffolding only.** Directory tree, compose file, Dockerfiles, and service stubs exist. No business logic yet. Each service responds on `/health` but doesn't do anything else.

## What this demo teaches

- **Multi-agent patterns** · Supervisor, Plan-and-Execute, Swarm/Handoff, Critic-driven revision (Reflexion)
- **A2A protocol** · the inter-service contract between agents (real wire format, not just slides)
- **HITL via voice** · the Concierge asks for payment approval aloud, user says yes, the Planner unblocks
- **Microservices for agents** · why agents-as-services beats agents-in-one-process at production scale
- **Observability across the mesh** · Phoenix trace tree spans all 9 workers

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│  Browser                                                 │
│  ┌────────────────────┐  ┌────────────────────────────┐  │
│  │  Mic + waveform UI │  │  Trip Dashboard (right)    │  │
│  │  (Gemini Live WS)  │  │  · Concierge's Plan        │  │
│  │                    │  │  · Flights · Hotels        │  │
│  │                    │  │  · Budget · Itinerary      │  │
│  │                    │  │  · Payment · Calendar      │  │
│  │                    │  │  · Pre-trip todos          │  │
│  └─────────┬──────────┘  └──────────▲─────────────────┘  │
└────────────┼─────────────────────────┼───────────────────┘
             │ audio                   │ SSE plan updates
             ▼                         │
┌──────────────────────────────────────┴───────────────────┐
│  concierge-voice (Gemini Live)                           │
│  · single tool: delegate_trip(brief)                     │
└─────────────────────────┬────────────────────────────────┘
                          │ HTTP
                          ▼
┌──────────────────────────────────────────────────────────┐
│  planner (Plan-and-Execute supervisor)                   │
│  · writes plan to Redis · routes steps to A2A peers      │
└────┬────────┬────────┬────────┬────────┬────────┬────────┘
     │        │        │        │        │        │   (A2A)
     ▼        ▼        ▼        ▼        ▼        ▼
  flight   hotel  itinerary budget payment critic   todo, calendar, research
     │        │                       │
     ▼        ▼                       ▼
  mock-      mock-                  mock-
  airline    hotel                  payment
     (port 9001-9003 · mock external APIs)

Shared: redis (port 6379 · plan state + pub/sub)
        phoenix (port 6006 · observability)
```

## Service inventory

| Service | Port | Source | A2A endpoints (planned) |
|---|---|---|---|
| `concierge-voice` | 8000 | new · Gemini Live + WS | (front-end, not an A2A peer) |
| `planner` | 8001 | new · LangGraph supervisor | `delegate_trip` |
| `flight-agent` | 8010 | new | `search_flights`, `hold_flight` |
| `hotel-agent` | 8011 | new | `search_hotels`, `hold_hotel` |
| `itinerary-agent` | 8012 | new | `build_itinerary`, `revise_itinerary` |
| `budget-agent` | 8013 | new | `check_budget`, `commit_spend` |
| `payment-agent` | 8014 | new + HITL hook | `authorize`, `capture`, `refund` |
| `critic-agent` | 8015 | reused S5 | `review_artifact` |
| `todo-agent` | 8016 | reused S4 | `create_todos` |
| `calendar-agent` | 8017 | reused S5 gcal MCP | `add_events` |
| `research-agent` | 8018 | reused S5 | `research_destination` |
| `mock-airline-api` | 9001 | new | (REST, not A2A) |
| `mock-hotel-api` | 9002 | new | (REST, not A2A) |
| `mock-payment-api` | 9003 | new | (REST, not A2A) |
| `redis` | 6379 | infra | shared plan state, pub/sub |
| `phoenix` | 6006 | infra | observability collector |
| `frontend` | 3000 | new | Next.js dashboard |

## Quickstart

```bash
# 1. Copy env template and fill in your Gemini API key
cp .env.template .env
# edit .env, set GOOGLE_API_KEY

# 2. (For calendar-agent) drop your gcal OAuth files in secrets/
#    google-creds.json + google-token.json
#    See agentic-ai-introduction/backend/scripts/bootstrap_gcal_token.py

# 3. Spin up the mesh
docker compose up --build

# 4. Wait for all services to be healthy (about 30-60s cold start)
./scripts/preflight.sh    # smoke-tests every service

# 5. Open the dashboard
open http://localhost:3000
```

## Repo layout

```
agentic-ai-multiagent-orchestration/
├── docker-compose.yml
├── .env.template
├── pyproject.toml          · shared deps for all Python services
├── docker/
│   ├── agent.Dockerfile    · base image for the 11 Python agents
│   ├── mock-api.Dockerfile · base image for the 3 mock APIs
│   └── frontend.Dockerfile · Next.js production build
├── services/
│   ├── concierge_voice/    · Gemini Live + WebSocket
│   ├── planner/            · Plan-and-Execute supervisor
│   ├── flight_agent/  hotel_agent/  itinerary_agent/
│   ├── budget_agent/  payment_agent/  critic_agent/
│   ├── todo_agent/    calendar_agent/  research_agent/
│   └── mock_apis/{airline, hotel, payment}/
├── shared/
│   ├── a2a/                · A2A client + server scaffolding
│   ├── plan_state/         · Redis-backed Concierge's Plan
│   └── observability/      · Phoenix + LangSmith init shared by all
├── frontend/               · Next.js dashboard + Gemini Live WS client
│   ├── components/         · ConciergePlan, FlightsPanel, ..., animations.ts
│   └── styles/             · Tailwind + shimmer/blur/dots keyframes
├── secrets/                · gitignored · OAuth tokens, credentials
└── scripts/
    ├── preflight.sh        · smoke-test all services before demo
    └── setup-dev.sh        · first-time setup helper
```

## Animation design tokens (the right-pane feel)

Defined in `frontend/components/animations.ts` and `frontend/styles/globals.css`. Reach for these vocabularies; don't invent new ones per component:

| Token | Where it goes | What it does |
|---|---|---|
| `shimmer` | Skeleton placeholder cards (Flights, Hotels while searching) | Light-sweep gradient pulses left-to-right |
| `pulse-dots` | In-progress plan steps, Concierge "thinking" indicator | Three dots animate `• ○ ○` → `○ • ○` → `○ ○ •` |
| `glass` | Cards with backdrop blur (HITL payment panel) | `backdrop-filter: blur(12px)`, semi-transparent bg |
| `flash-success` | A plan step transitioning to done | Brief green flash + scale 1.02 then back |
| `slide-up-spring` | Payment HITL gate appearing | Spring physics from `translateY(20px)` |
| `stagger-fade` | Flight/Hotel option lists rendering | Items appear 100ms apart with opacity 0→1 |
| `glow-fill` | Budget bar updating | Inner glow during width transition |
| `blur-replace` | Stale data being replaced | Old content `filter: blur(4px)` out, new fades in |
| `waveform-pulse` | Concierge voice bars while speaking | 5-8 vertical bars sync to Web Audio API analyser |

All implemented with **Framer Motion + Tailwind + custom CSS keyframes**. No heavyweight animation library needed.

## Observability

- **Phoenix** at `http://localhost:6006` collects spans from every service via OpenInference. The trace tree shows the full distributed run: Concierge → Planner → each worker → mock APIs.
- **LangSmith** is optional; set `LANGSMITH_API_KEY` in `.env` to dual-sink.
- Per-service `/health` returns `{"status": "ok", "service": "<name>"}` for compose healthchecks.

## What's intentionally NOT here yet

- Business logic in any service (all stubs · just `/health`)
- A2A protocol implementation (`shared/a2a/` is a stub)
- Plan state in Redis (`shared/plan_state/` is a stub)
- Actual Planner LangGraph
- Gemini Live wiring
- Frontend components beyond stubs
- Tests
- CI

These get filled in across the Session 6 build. Scaffolding here exists so the topology is concrete and we can iterate.

## Provenance

Session 6 of the **Agentic AI Mastery** cohort (hubbleflow.ai/ai-engineer). Reuses code from `agentic-ai-introduction/` (the S4/S5 repo) — specifically the ToDoAgent, the gcal MCP, and the Critic/Research/Estimator/Notification subagents — A2A-wrapping each as its own service.
