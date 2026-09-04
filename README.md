# Personal Planner Agent

An explainable daily-planning assistant that combines goals, calendar events,
todo items, weather, and long-term user memory into a validated `DailyPlan`.

The project demonstrates a complete feedback loop: users can adjust a plan,
those actions become immutable evidence, repeated behavior becomes a
confidence-scored preference, and the next plan can apply that preference with
deterministic safety checks.

## Current status

The Phase 1 Daily Planner MVP is runnable through both a CLI and a Streamlit
web interface. The current implementation includes:

- mock Calendar, Todo, Profile, and Weather data;
- optional Google Calendar and Todoist reads;
- an OpenAI Agents SDK planner using Kimi through Moonshot's compatible API;
- structured `DailyPlan` output and deterministic conflict validation;
- append-only accept, move, skip, and delete feedback events;
- three-event behavioral preference learning;
- a shared `get_memory_context(user_id, goal)` interface;
- deterministic preference application and safe schedule reordering;
- a Memory page for view, edit, pause, resume, and forget actions;
- explicit-confirmation Google Calendar writes and feedback synchronization;
- fully offline automated tests.

Travel, Health, vector databases, LangGraph, multi-agent orchestration, and
production database persistence are not part of this milestone.

## End-to-end flow

```text
User goal
  + Calendar
  + Todo
  + Weather
  + Memory Context
        |
        v
PlannerAgent -> deterministic memory rules -> conflict validator -> DailyPlan
                                                                  |
                                                                  v
                                              accept / move / skip / delete
                                                                  |
                                                                  v
                                         EditEvent -> preference aggregation
                                                                  |
                                                                  v
                                                      next planning request
```

## Memory architecture

The current local memory implementation uses an event-sourced write path and a
derived read model:

1. `FeedbackService` records an immutable `EditEvent`.
2. `EditEventStore` appends the event to `data/behavior_history.jsonl`.
3. `PreferenceAggregator` requires three matching events before creating a
   `BehaviorPreference`.
4. `LocalMemoryContextService` combines explicit profile values, learned
   preferences, and goal-relevant events.
5. `PlannerAgent` receives the shared Memory Context and applies active
   preferences through deterministic rules before conflict validation.

The shared contract is:

```python
get_memory_context(user_id: str, goal: str) -> {
    "profile": dict,
    "preferences": list,
    "relevant_events": list,
}
```

Learned preferences include a stable `memory_id`, confidence, evidence count,
source event IDs, structured value, and `active` or `paused` status.

### Applied preference rules

- `preferred_time_range`: safely move the matching activity into the preferred
  range; one flexible conflicting item may be swapped into the original slot.
- `avoid_time_range`: move the activity to the nearest safe time outside the
  avoided range.
- `deprioritize_activity`: lower priority unless the current request explicitly
  asks for that activity.
- Fixed calendar events always take priority over learned preferences.
- Paused or forgotten preferences do not affect planning.

Applied rules are exposed in `DailyPlan.assumptions` for explainability.

### Local persistence

- `data/behavior_history.jsonl`: append-only raw feedback evidence.
- `data/memory_controls.json`: profile overrides and learned-preference edit,
  pause, and forget controls.
- `data/google_calendar_syncs.json`: Google Calendar duplicate-write protection.

The entire `data/` directory is ignored by Git. The Memory Context boundary is
designed so a future SQLite service can replace these local files without
changing Planner or UI contracts.

See [Long-Term Memory Design](docs/memory-design.md) for details.

## Repository structure

```text
Genshin-Agent/
|-- planner_agents/
|   `-- planner_agent.py          # planning, memory application, validation
|-- memory/
|   |-- __init__.py
|   `-- models.py                 # strict Pydantic contract prototypes
|-- tools/
|   |-- calendar_tool.py          # mock and Google Calendar providers
|   |-- todo_tool.py              # mock and Todoist providers
|   |-- weather_tool.py           # mock and Open-Meteo providers
|   |-- validator_tool.py         # deterministic overlap checks
|   |-- edit_event.py             # immutable events and JSONL store
|   |-- feedback_service.py       # feedback application service
|   |-- behavior_preference.py    # runtime learned-preference model
|   |-- preference_aggregator.py  # three-event aggregation rules
|   |-- preference_tool.py        # legacy profile/preference adapter
|   |-- memory_context.py         # shared context and user controls
|   |-- memory_rules.py           # deterministic planner rules
|   `-- calendar_sync_store.py
|-- tests/                        # offline unit and end-to-end coverage
|-- docs/
|   |-- memory-design.md
|   |-- phase-1-task-list.md
|   `-- development-log.md
|-- frontend.py                   # Streamlit Planner and Memory pages
|-- main.py                       # CLI demo and feedback loop
|-- config/settings.py
|-- .env.example
|-- requirements.txt
`-- README.md
```

## Requirements

- Python 3.11 or newer
- Windows PowerShell commands are shown below; equivalent commands work on
  macOS and Linux.

Core packages include OpenAI Agents SDK, OpenAI Python SDK, Pydantic, HTTPX,
Streamlit, Google API clients, and pytest.

## Local setup

```powershell
git clone https://github.com/MileHuang/Genshin-Agent.git
cd Genshin-Agent
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

The deterministic demo does not require any API credentials.

For live model use, set at least:

```env
MOONSHOT_API_KEY=replace_with_your_moonshot_api_key
KIMI_MODEL=kimi-k3
KIMI_BASE_URL=https://api.moonshot.ai/v1
```

Open-Meteo requires no API key. Set `WEATHER_PROVIDER=mock` for fully offline
development.

Never commit `.env`, API keys, OAuth credentials, tokens, or user memory files.

## Run the CLI

Deterministic offline demo:

```powershell
python main.py --demo
```

Demo with interactive feedback for a named user:

```powershell
python main.py --demo --feedback --user-id member-b
```

Custom goal and date:

```powershell
python main.py --demo `
  --user-id member-b `
  --date 2026-09-03 `
  --location "Madison, WI" `
  --goal "Plan study, a gym session, and grocery shopping"
```

Omit `--demo` to use the configured Moonshot model.

## Run the web interface

```powershell
python -m streamlit run frontend.py
```

Open <http://127.0.0.1:8501>. The sidebar provides:

- `Planner`: generate, validate, adjust, and optionally sync a daily plan.
- `Memory`: edit profile values; inspect, edit, pause, resume, or forget learned
  preferences; and view recent evidence.
- `User ID`: isolate memory by user.
- `Offline demo mode`: run without a model API key.

## Optional integrations

### Google Calendar

1. Enable the Google Calendar API in a Google Cloud project.
2. Create a desktop OAuth client.
3. Save the downloaded JSON as `secrets/google_client_secret.json`.
4. Enable `Read Google Calendar` in Streamlit or pass `--google-calendar`.

The first use opens a browser for consent. Generated events are written only
after explicit confirmation. MOVE updates a synced event; DELETE removes it;
SKIP records feedback only.

### Todoist

Set `TODOIST_API_TOKEN` in `.env`, then enable `Read Todoist tasks` in
Streamlit or pass `--todoist`. The integration reads open tasks only.

## Testing

Run all offline tests:

```powershell
python -m pytest -q
```

Important coverage includes:

- provider validation and offline doubles;
- schedule and fixed-calendar conflict checks;
- feedback event validation and round trips;
- three-event preference aggregation;
- profile and preference management controls;
- safe deterministic memory rules;
- three gym moves affecting the next generated plan;
- Streamlit helper behavior and Google Calendar synchronization.

## Collaboration rules

- Keep tools in `tools/` and add matching tests.
- Keep provider output structured and usable by `DailyPlan`.
- Record actual context sources in `DailyPlan.tools_used`.
- Treat Calendar as a fixed constraint and Memory as a soft constraint.
- Keep network-independent test doubles for every external provider.
- Update this README when implementation or commands change.
- Use the repository-local `$daily-planner-mvp` Codex skill for scoped work.

## Current limitations

- JSONL and JSON control files are local-development persistence, not a
  production database.
- Forgetting a learned preference suppresses the derived memory but does not
  rewrite raw append-only events.
- Preference decay and contradictory-evidence resolution are not implemented.
- Authentication and authorization for shared deployments are not implemented.
- The Pydantic models under `memory/` and runtime dataclasses under `tools/`
  remain separate until the durable MemoryService migration.
