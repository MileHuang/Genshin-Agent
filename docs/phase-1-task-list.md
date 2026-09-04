# Phase 1 Daily Planner MVP Task List

## Completed vertical slice

```text
Goal + Calendar + Todo + Memory + Weather
    -> PlannerAgent
    -> deterministic memory preference rules
    -> conflict-validated DailyPlan
    -> CLI or Streamlit review
    -> accept/move/skip/delete feedback
    -> explainable long-term preference evidence
```

## Completed inputs and planning

- [x] Mock Calendar, Todo, Profile, and Weather providers
- [x] Optional Google Calendar and Todoist providers
- [x] OpenAI Agents SDK planner with Kimi-compatible JSON parsing
- [x] Structured `DailyPlan` output
- [x] Deterministic schedule and fixed-event conflict validation
- [x] `get_memory_context(user_id, goal)` planner integration
- [x] Active/paused preference handling
- [x] Deterministic preferred-time, avoid-time, and deprioritization rules

## Completed feedback and memory

- [x] Immutable accept, move, skip, and delete events
- [x] Append-only local JSONL evidence store
- [x] Three-event behavioral preference threshold
- [x] Confidence, evidence counts, and source event IDs
- [x] Per-user retrieval and goal-relevant event selection
- [x] Editable profile controls
- [x] Learned preference edit, pause, resume, and forget controls
- [x] Streamlit Memory page
- [x] End-to-end test: three gym moves influence the next plan

## Completed product entry points

- [x] Deterministic offline CLI demo
- [x] Optional interactive CLI feedback
- [x] Streamlit Planner page
- [x] Streamlit Memory page
- [x] Explicit-confirmation Google Calendar sync
- [x] Google Calendar MOVE and DELETE synchronization

## Next backend integration task

Replace the local JSONL/control-file implementation with the durable
MemoryService while preserving this shared contract:

```python
get_memory_context(user_id: str, goal: str) -> {
    "profile": dict,
    "preferences": list,
    "relevant_events": list,
}
```

The replacement must keep stable memory IDs and support profile updates,
preference edits, pause/resume, and forget operations.

## Deferred

- Production SQLite migrations and data migration
- Authentication and authorization
- Preference decay and contradiction resolution
- Raw-event retention and deletion policies
- Travel, Health, LangGraph, multi-agent orchestration, vector databases,
  PostgreSQL, and Redis
