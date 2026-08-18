# Phase 1 Daily Planner MVP Task List

This backlog is written so each section can be copied into a GitHub issue. Keep Phase 1 limited to the daily-planning vertical slice.

## Milestone acceptance path

```text
Calendar + Todo + Preferences + Weather
    -> PlannerAgent
    -> validated DailyPlan
    -> CLI review/edit simulation
    -> EditEvent evidence
```

## P0 - Complete the planner input loop

### [ ] Add the mock Todo tool and planner integration

Labels: `phase-1`, `P0`, `tool`, `in-progress`

Owned files: `tools/todo_tool.py`, `tests/test_todo_tool.py`, and the smallest necessary planner/README integration.

Acceptance criteria:

- `get_todo_context` returns deterministic structured mock data.
- `PlannerAgent` receives Todo context in both offline and SDK paths.
- `DailyPlan.tools_used` includes `todo` when Todo context is used.
- Calendar, Todo, Preferences, and Weather can produce a conflict-free `DailyPlan` in an offline test.
- README lists the implemented tool and tests pass without network access.

Coordination: one contributor owns planner integration to avoid concurrent edits to `planner_agents/planner_agent.py`.

### [ ] Add an end-to-end Phase 1 pipeline test

Labels: `phase-1`, `P0`, `test`

Depends on: Todo integration.

Acceptance criteria:

- Uses deterministic agent and tool doubles.
- Asserts all four input labels appear in `tools_used` when relevant.
- Asserts no generated item overlaps another item or a fixed calendar event.
- Exercises at least one invalid/conflicting draft and the expected failure or revision path.

## P1 - Capture feedback evidence

### [x] Define the initial memory contracts

Labels: `phase-1`, `P1`, `memory`

Delivered in `memory/models.py` with tests in `tests/test_memory_models.py`.

Acceptance criteria:

- Models exist for `EditEvent`, `BehavioralPreference`, and `ProfileMemory`.
- Move evidence requires distinct original and new time slots.
- Preferences retain evidence IDs, evidence count, confidence, and update time.
- No persistence dependency is introduced.

### [ ] Add an in-process edit simulation

Labels: `phase-1`, `P1`, `demo`, `memory`

Depends on: memory contracts and CLI demo.

Acceptance criteria:

- A user can simulate accept, move, delete, or skip for a plan item.
- The action produces a validated `EditEvent` and prints its JSON representation.
- Moving an item records both old and new slots.
- The simulation does not mutate a real calendar or write to a database.

### [ ] Prototype preference evidence aggregation

Labels: `phase-1`, `P1`, `memory`

Depends on: memory contracts.

Acceptance criteria:

- A pure function groups repeated, similar `EditEvent` values.
- One event remains evidence only and does not become an active preference.
- Repeated evidence produces a candidate `BehavioralPreference` with explainable confidence.
- Tests cover repeated moves and contradictory evidence.

## P1 - Provide a demo entry point

### [ ] Build the CLI demo loop

Labels: `phase-1`, `P1`, `demo`

Acceptance criteria:

- Accepts a target date, location, and natural-language daily goal.
- Prints the structured, validated `DailyPlan` in a readable form.
- Offers a minimal accept/move/delete/skip simulation.
- Supports a deterministic offline mode for demos and tests.
- Documents the exact command in README.

## P1 - Keep collaboration assets current

### [x] Add the project-local Codex skill

Labels: `phase-1`, `P1`, `documentation`

Delivered in `.codex/skills/daily-planner-mvp/`.

Acceptance criteria:

- States the Phase 1 goal and explicit non-goals.
- Defines tool location, testing, `tools_used`, and `DailyPlan` integration rules.
- Describes the EditEvent-to-BehavioralPreference direction.
- Passes the skill validator.

### [ ] Reconcile README after Todo and CLI merge

Labels: `phase-1`, `P1`, `documentation`

Acceptance criteria:

- Project tree, tools, setup commands, and current-status claims match the merged code.
- Phase 1 completion boxes reflect passing tests rather than planned behavior.
- Deferred features remain clearly separated from the current MVP.

## Out of scope for this milestone

Do not open implementation issues yet for Travel, Health, LangGraph, multi-agent orchestration, vector databases, PostgreSQL, Redis, or real Google Calendar writes. Track them as roadmap items only.
