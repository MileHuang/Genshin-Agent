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
| Behavioral | Scoped accept, move, skip, or delete patterns | Three compatible unknown events or explicit persistent declaration | Soft scheduling rules at confidence >= 0.70 |
| Candidate | Goal-relevant raw edit events | Source-linked feedback; retained 30 days | Short-lived evidence; never retrieved by RAG |
| Episodic | Reviewed LLM-consolidated summaries | Explicit user review and approval | Explainable long-term RAG context |
| Session | Current goal, temporary constraints, timezone | Session start | Short-term Markdown context; never indexed |

## Runtime data flow

```text
User feedback
    -> FeedbackService
    -> immutable EditEvent
    -> append-only behavior_history.jsonl
    -> PreferenceAggregator (scope, compatible clusters, recent evidence)
    -> BehaviorPreference with confidence and evidence IDs
    -> expiring candidate memory (not retrievable)
    -> explicit Kimi consolidation (two or more new events)
    -> reviewed, source-linked episodic RAG summary
    -> LocalMemoryContextService
    -> get_memory_context(user_id, goal)
    -> Planner prompt + deterministic memory rules
    -> validated DailyPlan
```

## Shared contract

`get_memory_context(user_id, goal)` preserves three required top-level fields
and may add two RAG/session fields:

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
  "relevant_events": [],
  "relevant_memories": [],
  "working_memory": null
}
```

`relevant_memories` contains source-linked long-term episodic retrieval results.
`working_memory` contains the latest unexpired Markdown session context and is
never indexed into long-term RAG. Provider failure uses the same required
contract with empty values and is exposed in the plan's tool trace.

The planner validates this shape before use. Paused preferences and preferences
with confidence below 0.70 remain visible but are ignored by scheduling rules.

## Behavioral evidence updates

Feedback scope defaults to `unknown` for backward compatibility. `temporary`
events are retained as facts but excluded from habit learning. `persistent`
events are explicit declarations: the latest such event starts a new evidence
epoch for that user/activity and can establish a preference on its own.

The current epoch is bounded to 12 non-temporary observations. Time clusters
have at most 60 minutes of start/end spread and require three observations
unless they contain a persistent declaration. Observation weights halve every
two subsequent observations. The highest-weight eligible cluster supplies the
time interval; its share of all evidence weight is the confidence score.
Opposing actions and incompatible times therefore reduce confidence instead
of producing a midpoint between contradictory habits. Identical event IDs are
counted only once per user. The score is heuristic, not calibrated certainty.

Preferences remain projections over the immutable event log. The RAG service
now persists observed revisions; wall-clock confidence decay remains deferred.
Scope currently has no per-weekday conditions or custom validity interval.

## Durable lifecycle

`memory/lifecycle.py` stores normalized snapshots in `preference_versions` in
the personal RAG database. Each row has a version ID, per-preference version
number, value, confidence, source events, observed validity interval, and
replacement link. Changes close the previous interval as `superseded` and
append a new version. Identical reads do not create revisions. A missing
projection is `expired`; an explicit forget is `forgotten`. Low-confidence
versions are `candidate`. The planner continues to receive only the current
preference projection, never historical versions. Initial startup records a
baseline; it does not infer unobserved revisions from old event history.

Related episodic summaries are queued as `review_required` when a preference
changes value or becomes non-active. Association is based on source event IDs
and activity, so unrelated activities/users are not affected. On bootstrap,
only older summaries with disjoint evidence are flagged. This mechanism is a
conservative review trigger, not semantic contradiction detection.

Queued summaries have no vectors and cannot be retrieved. A user can keep the
summary as dated historical context, regenerating its vectors, or archive it as
`superseded`. Review decisions are logged in `summary_reviews`. An opaque review
token ties approval to the displayed change, preventing stale forms from
restoring a summary after a newer preference change. Embedding failures roll
back status transitions. The Memory page displays versions and summary states.

Documents with an explicit expiry become `expired` during context loading;
canonical text remains inspectable, while derived vectors are removed. New
summaries do not receive an automatic TTL. Candidate expiry is 30 days, checked
both for planner event context and for consolidation preview/confirmation.
The append-only audit log remains available for traceability and behavioral
aggregation; the TTL does not delete raw events or automatically expire habits.

Only active documents receive vector chunks on write, promotion, resume, or
index rebuild. Additive schema upgrades preserve existing canonical records.
Legacy raw feedback is demoted to candidate and inactive vector chunks are
removed. Index policy changes trigger rebuilding active records only.

## LLM consolidation guardrails

Kimi is not called during normal retrieval. Raw feedback first becomes an
expiring `candidate` record, which is excluded from RAG retrieval. The user
explicitly generates a Memory-page preview, which sends only pending feedback
events for that user. They can edit, approve, or discard the proposed summary.
Only approval promotes an episodic document with every original event ID as a
source link. It is never
promoted to a `BehaviorPreference` or a hard rule; the scope-aware deterministic
aggregator separately derives behavioral rules. Once summarized, raw events
are omitted from retrieval to avoid duplicate context but remain in the
append-only JSONL audit trail. Forgetting a preference suppresses both its
source-linked episodic records and derived LLM summaries from future retrieval.

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
- inspecting preference versions and reviewing or archiving stale summaries.

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

- Production-grade migrations and shared durable service implementation
- authentication and authorization for multi-user memory
- retention, export, and raw-event deletion policy
- semantic contradiction detection and wall-clock preference decay
- audit history for profile and control changes
