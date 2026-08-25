# Phase 1 Memory Draft

The Phase 1 memory layer stores explainable evidence in a local append-only
JSONL file without claiming that one edit is a stable habit. The store remains
replaceable so production persistence can be added later.

## Data flow

```text
User accepts, moves, deletes, or skips a plan item
    -> EditEvent (raw evidence)
    -> repeated similar evidence (minimum three matching events)
    -> BehavioralPreference (candidate, confidence + evidence)
    -> ProfileMemory (per-user snapshot)
    -> future DailyPlan context (future integration)
```

## Contracts

- `TimeSlot` validates one `HH:MM` interval.
- `EditEvent` captures the user, plan item, action, timestamp, and relevant before/after slots. A move must contain two different slots.
- `BehavioralPreference` captures one candidate or active preference, its scope, source event IDs, evidence count, confidence, and update time.
- `ProfileMemory` holds a versioned set of uniquely identified preferences for one user.

The durable contracts are in `memory/models.py`; the current runtime feedback
pipeline is exposed through `tools/edit_event.py`, `tools/feedback_service.py`,
and `tools/preference_aggregator.py`. Runtime events are immutable, require
timezone-aware datetimes, and preserve their event IDs in every learned
preference. Preference retrieval filters evidence by `user_id`.

## Example evidence

Moving exercise from 18:00-18:45 to 19:00-19:45 produces one `EditEvent`. Repeating a similar move on several days could later create a category-scoped preference such as:

```text
key: preferred_exercise_period
value: evening
scope: category
category: exercise
evidence_count: 3
confidence: 0.70
status: candidate
```

## Implemented locally

- Append-only JSONL feedback evidence
- Accept, move, skip, and delete events
- Three-event preference threshold
- Evidence IDs, counts, confidence, and timestamps
- Per-user preference retrieval
- Streamlit evidence progress and learned-preference display

## Deliberately deferred

- Production database persistence
- More advanced contradictory-evidence and decay rules
- User controls to correct or disable a learned preference
- Preference decay, correction, and deletion

Those behaviors need explicit product rules and tests. The current code only establishes stable, serializable contracts for that work.
