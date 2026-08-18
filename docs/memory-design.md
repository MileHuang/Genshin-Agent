# Phase 1 Memory Draft

The Phase 1 memory layer stores explainable evidence without choosing a database or claiming that one edit is a stable habit.

## Data flow

```text
User accepts, moves, deletes, or skips a plan item
    -> EditEvent (raw evidence)
    -> repeated similar evidence (future inference rule)
    -> BehavioralPreference (candidate, confidence + evidence)
    -> ProfileMemory (per-user snapshot)
    -> future DailyPlan context (future integration)
```

## Contracts

- `TimeSlot` validates one `HH:MM` interval.
- `EditEvent` captures the user, plan item, action, timestamp, and relevant before/after slots. A move must contain two different slots.
- `BehavioralPreference` captures one candidate or active preference, its scope, source event IDs, evidence count, confidence, and update time.
- `ProfileMemory` holds a versioned set of uniquely identified preferences for one user.

The implementation is in `memory/models.py`. Models reject extra fields and timezone-naive evidence timestamps so bad evidence fails at the boundary.

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

## Deliberately deferred

- Database or file persistence
- Rules for grouping similar edit events
- Confidence scoring and promotion thresholds
- Memory retrieval and injection into `PlannerAgent`
- Preference decay, correction, and deletion

Those behaviors need explicit product rules and tests. The current code only establishes stable, serializable contracts for that work.
