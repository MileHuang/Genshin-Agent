# Next-Phase Agent Roadmap

## Purpose

Evolve the current Personal Planner into a reliable, tool-driven platform for
daily planning and travel planning. Preserve the existing safety boundaries:
Calendar writes require explicit confirmation, personal memory stays per-user,
and deterministic validation remains the final authority for schedules.

## Current baseline

- One OpenAI Agents SDK ReAct planner backed by Kimi.
- Read-only Calendar, Todo, Weather, and Personal Memory tools.
- A bounded agent loop: Calendar and Memory are required; Todo and Weather are
  selected when useful; the SDK run is capped at six turns.
- Hybrid memory: temporary Session Markdown, SQLite Personal RAG, and
  deterministic learned preferences.
- Kimi-assisted feedback summaries require preview and user approval before
  they enter long-term RAG.

## Phase 2A — ReAct reliability and tool platform

Goal: make tool use observable, testable, and safe before adding more domains.

- [ ] Add a typed tool registry with name, input/output schema, permission
  level (`read`, `draft`, `write`), timeout, and user-facing description.
- [ ] Record a compact execution trace: which tools were called, whether cache
  was used, and whether a fallback was applied. Never expose hidden reasoning.
- [ ] Add ReAct evaluations for tool selection, required Calendar/Memory reads,
  turn limits, malformed tool output, and provider failure recovery.
- [ ] Add explicit read-only failure fallbacks. A failed Weather or Place lookup
  must not make the planner invent facts or silently fail.
- [ ] Keep all write-capable tools behind an approval boundary; no automatic
  Calendar changes, reservations, purchases, or messages.

Exit criteria:

- Every tool has an offline mock and schema validation.
- The UI can explain the tools used for a plan in human-readable language.
- Evaluation coverage verifies both useful tool calls and unnecessary-call
  avoidance.

## Phase 2B — Shared planning tools and skills

Goal: introduce reusable capabilities that work for both daily and travel
planning.

- [ ] Place Search tool: candidate places with category, coordinates, rating,
  price range, and source metadata.
- [ ] Route tool: travel time and distance between two places.
- [ ] Opening-hours and timezone tool: validate that a proposed stop is open at
  its scheduled local time.
- [ ] Budget tool: track estimated daily and trip spending.
- [ ] Create reusable skills/workflows:
  - `daily-planning`: daily schedule constraints and memory use.
  - `travel-research`: source checking, place filtering, and uncertainty.
  - `itinerary-builder`: route-aware, time-aware itinerary construction.
  - `travel-safety`: approval requirements for any external write or booking.

Exit criteria:

- Each shared tool is callable independently with deterministic test fixtures.
- A skill documents when to call each tool and what evidence must be returned.

## Phase 3 — Travel data and Travel Agent

Goal: add a focused travel-planning capability without mixing public data with
private user memory.

```text
Travel ReAct Agent
  ├─ Personal RAG: user preferences, accessibility needs, travel habits
  ├─ Public Travel RAG: places, neighborhoods, restaurants, attractions
  ├─ Place / Route / Hours / Weather / Timezone / Budget tools
  └─ Itinerary validator
```

Rules:

- Personal RAG remains user-scoped and private.
- Public Travel RAG is a separate corpus with provenance, freshness timestamp,
  and location metadata; it never stores private behavioral evidence.
- The Travel Agent may recommend and draft an itinerary, but booking, payment,
  sending messages, and Calendar writes require explicit user approval.
- Verify opening hours, route feasibility, and timezones before presenting a
  final itinerary.

Exit criteria:

- The agent can create a one-day itinerary that respects fixed Calendar events,
  personal preferences, routes, opening hours, weather, and a stated budget.
- Every recommendation can show its source type and confidence.

## Deferred decisions

- Whether Travel Agent stays a skill/tool set on the main planner or becomes a
  separate specialist agent. Start as a focused agent only after Phase 2A/2B.
- Choice of durable hosted vector store and relational database.
- Authentication, authorization, encryption, retention policy, and user data
  deletion semantics for a shared deployment.
- Live booking or payment integrations.

