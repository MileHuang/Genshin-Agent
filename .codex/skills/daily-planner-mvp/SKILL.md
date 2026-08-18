---
name: daily-planner-mvp
description: Develop and review this repository's Phase 1 Daily Planner MVP. Use for work on daily-plan inputs, planner tools, DailyPlan output, conflict validation, edit-event evidence, behavioral-preference models, tests, demos, README alignment, and Phase 1 task planning while keeping later product phases out of scope.
---

# Daily Planner MVP

Keep development focused on one demonstrable vertical slice:

```text
Goal + Calendar + Todo + Preferences + Weather
    -> PlannerAgent
    -> conflict-validated DailyPlan
    -> user review/edit
    -> memory evidence draft
```

## Protect the Phase 1 boundary

- Build only the daily-planning loop, required input adapters, deterministic validation, a minimal demo, and edit-evidence contracts.
- Do not add Travel, Health, vector databases, LangGraph, multi-agent orchestration, PostgreSQL, Redis, or real Google Calendar integration.
- Keep calendar writes and durable memory persistence out of this phase. Produce drafts and data contracts only.
- If a request crosses this boundary, isolate the smallest Phase 1-compatible part and call out the deferred work.

## Work safely in the repository

1. Inspect `git status`, `README.md`, relevant implementation files, and tests before editing.
2. Preserve unrelated or in-progress changes. Avoid editing the same integration file as another contributor when the work can be isolated.
3. Implement one bounded, reviewable change at a time.
4. Keep README claims synchronized with behavior that exists and has been tested.
5. Run narrow tests first, then `python -m pytest -q` before handoff.

## Add or change a planner tool

- Put each tool in `tools/` with a narrow function contract and structured return value.
- Provide deterministic mock behavior so tests never require network access.
- Add a matching `tests/test_<tool>.py` file, including validation and fresh-copy/isolation behavior where relevant.
- Register the tool with `PlannerAgent` only after its standalone contract is tested.
- Add the canonical tool label to `DailyPlan.tools_used` when the planner actually uses it. Use `calendar`, `todo`, `preferences`, or `weather` for the Phase 1 inputs.
- Ensure tool output is represented in the context used to create `DailyPlan`; do not collect unused context.
- Update the README tool list and project tree in the same change.

## Preserve planning guarantees

- Return structured Pydantic output through `DailyPlan`.
- Treat existing calendar events as fixed unavailable time.
- Run deterministic conflict validation after model generation.
- Keep offline tests independent of Moonshot, OpenAI, and Open-Meteo availability.
- Reject malformed dates, times, empty required text, and inconsistent edit evidence at the model or tool boundary.

## Extend the memory draft

- Treat `EditEvent` as immutable evidence of accepting, moving, deleting, or skipping a plan item.
- Record both original and new time slots for a move.
- Aggregate repeated evidence into `BehavioralPreference`; retain evidence IDs, evidence count, confidence, and last-updated time.
- Store learned preferences in `ProfileMemory` for later planner use.
- Do not promote a long-term preference from a single edit. Preference inference and persistence remain follow-up work.
- Keep memory code independent from `PlannerAgent` until an explicit integration task owns both the learning rule and its tests.

## Finish a task

- Demonstrate the behavior with a focused test or CLI path.
- Confirm generated schedules do not overlap themselves or fixed events.
- Update `docs/phase-1-task-list.md` when task status or dependencies change.
- Report changed files, verification commands, and intentionally deferred work.

