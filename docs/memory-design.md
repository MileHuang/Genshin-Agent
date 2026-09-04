# Long-Term Memory Design

The current implementation is a local, explainable vertical slice. It uses an
append-only JSONL event stream for behavioral evidence and a small JSON control
overlay for user-managed profile and preference changes. Both stores sit behind
a replaceable Memory Context interface so a durable SQLite service can replace
them later.

## Memory layers

| Layer | Contents | Creation | Planner role |
| --- | --- | --- | --- |
| Profile | Timezone, wake/sleep times, exercise habit, focus period | Explicit user input | Stable background and soft constraints |
| Behavioral | Repeated accept, move, skip, or delete patterns | Aggregated after three matching events | Preferred times, avoided times, and deprioritization |
| Episodic | Goal-relevant edit events | Selected from event history | Explainable recent context |

## Runtime data flow

```text
User feedback
    -> FeedbackService
    -> immutable EditEvent
    -> append-only behavior_history.jsonl
    -> PreferenceAggregator (minimum three matching events)
    -> BehaviorPreference with confidence and evidence IDs
    -> LocalMemoryContextService
    -> get_memory_context(user_id, goal)
    -> Planner prompt + deterministic memory rules
    -> validated DailyPlan
```

## Shared contract

`get_memory_context(user_id, goal)` returns exactly three top-level fields:

```json
{
  "profile": {
    "timezone": "local",
    "wake_time": "08:00",
    "sleep_time": "23:00",
    "exercise_habit": "evening",
    "focus_period": "morning"
  },
  "preferences": [
    {
      "memory_id": "stable-id",
      "activity_type": "gym",
      "attribute": "preferred_time_range",
      "value": {"start": "20:00", "end": "21:00"},
      "confidence": 0.76,
      "evidence_count": 3,
      "evidence_event_ids": ["event-1", "event-2", "event-3"],
      "status": "active"
    }
  ],
  "relevant_events": []
}
```

The planner validates this shape before use. Paused preferences remain visible
but are ignored by deterministic scheduling rules.

## Deterministic planner rules

- `preferred_time_range`: move the matching activity into the preferred range
  when the slot is free. If one flexible item occupies the range, swap it into
  the original slot when that is safe.
- `avoid_time_range`: move the activity to the nearest safe interval outside
  the avoided range.
- `deprioritize_activity`: lower the generated priority unless the current goal
  explicitly requests the activity.
- Fixed calendar events always win. A memory rule is skipped when it cannot be
  applied without a conflict.

Every applied rule adds an explanation to `DailyPlan.assumptions`.

## User controls

The Streamlit Memory page supports:

- viewing and editing explicit profile values;
- viewing learned preferences, confidence, evidence count, and stable IDs;
- editing a structured preference value;
- pausing or resuming a preference;
- forgetting a learned preference;
- inspecting recent feedback evidence.

Profile edits and learned-memory controls are stored in
`data/memory_controls.json`. Forgetting a derived preference adds a suppression
marker; it does not rewrite the append-only raw event history.

## Contracts and runtime models

- `memory/models.py` contains strict Pydantic contract prototypes.
- `tools/edit_event.py` and `tools/behavior_preference.py` contain the current
  immutable runtime dataclasses.
- `tools/memory_context.py` is the integration boundary that a future SQLite
  implementation should preserve.

## Deferred production work

- SQLite migrations and durable service implementation
- authentication and authorization for multi-user memory
- retention, export, and raw-event deletion policy
- contradictory-evidence handling and preference decay
- audit history for profile and control changes
