# Personal Planner Agent

A personal planning agent that learns from how users organize their daily lives and travel.

The project combines daily scheduling and travel planning through one shared user-preference model. Instead of only remembering what a user says, the system records how the user edits, accepts, deletes, moves, and skips planned activities, then uses that evidence to improve future plans.

## Project Status

This repository now contains the first working MVP: an OpenAI Agents SDK
planner, a Kimi K3 model configured through Moonshot's OpenAI-compatible API,
live Google Calendar and Todoist providers, live Open-Meteo weather,
deterministic conflict validation, and offline tests. A Streamlit frontend
generates and reviews validated plans.

The repository also contains Phase 1 memory contracts plus a local JSONL
feedback flow. Repeated accept, move, skip, and delete evidence can be
aggregated into learned preferences and supplied to later planner prompts.
Feedback and Calendar sync records are stored locally for the MVP;
database-backed persistence and production learning rules remain planned.

## MVP Goal

The first working demo should support this flow:

1. A user describes tasks for a day in natural language.
2. The agent reads existing events from either a mock provider or Google Calendar.
3. The agent reads mock todo items or open Todoist tasks, plus user preferences.
4. The agent reads live weather when a goal involves travel or outdoor activity.
5. The agent generates a structured daily-plan draft.
6. Deterministic Python validation checks for time conflicts.
7. The CLI or Streamlit frontend displays the draft and records feedback.
8. Repeated feedback is aggregated into preferences for later plans.
9. After explicit confirmation, the frontend creates the plan in Google Calendar.
10. Synced plan items can be moved or deleted from the frontend and Google Calendar together.

The first milestone does **not** include automatic booking, payment,
multi-agent handoffs, vector databases, or unconfirmed calendar writes.

## Core Idea

```text
User request
    -> OpenAI Agents SDK Runner
    -> Personal Planner Agent
    -> Calendar / Todo / Memory / Weather tools
    -> Structured plan
    -> Python validation
    -> User review and edits
    -> Edit-event storage
    -> Preference update
    -> Better future plans
```

The OpenAI Agents SDK is responsible for the agent loop, model invocation,
function-tool calls, and structured output. Regular Python code is responsible
for hard constraints such as time conflicts, durations, validation, and database
writes.

## Proposed Architecture

```text
Streamlit frontend
        |
        v
FastAPI backend
        |
        v
OpenAI Agents SDK Runner
        |
        v
Personal Planner Agent
   |         |         |
   v         v         v
Calendar   Memory   Scheduling
 tools      tools     validator
   |         |         |
   v         v         v
Google     SQLite    Python rules
Calendar   database
```

Calendar writes are treated as side effects. The system generates a draft first
and writes to Google Calendar only after explicit user confirmation. A local
sync record saves Google event IDs so the same plan is not created twice and
later MOVE/DELETE actions can update the corresponding event.

## Technology Stack

- Python 3.11+
- OpenAI Agents SDK for agent orchestration, tool calling, and structured output
- Kimi K3 through Moonshot's OpenAI-compatible Chat Completions API
- Open-Meteo for live geocoding and daily weather forecasts
- OpenAI Python SDK as the underlying compatible API client
- HTTPX for injectable offline transports and live weather requests
- Pydantic for structured inputs and outputs
- Streamlit for the minimal visual frontend
- pytest for tests
- Google Calendar API client and OAuth libraries
- Git and GitHub for collaboration

FastAPI and SQLite remain planned for later phases.

## Current Project Structure

```text
Genshin-Agent/
|-- planner_agents/
|   `-- planner_agent.py
|-- config/
|   `-- settings.py
|-- memory/
|   |-- __init__.py
|   `-- models.py
|-- tools/
|   |-- calendar_tool.py
|   |-- calendar_sync_store.py
|   |-- todo_tool.py
|   |-- weather_tool.py
|   |-- preference_tool.py
|   |-- edit_event.py
|   |-- feedback_service.py
|   |-- preference_aggregator.py
|   |-- behavior_preference.py
|   `-- validator_tool.py
|-- tests/
|   |-- test_planner_agent.py
|   |-- test_calendar_tool.py
|   |-- test_weather_tool.py
|   |-- test_memory_models.py
|   |-- test_todo_tool.py
|   |-- test_feedback_service.py
|   |-- test_preference_aggregator.py
|   `-- test_daily_pipeline.py
|-- docs/
|   |-- memory-design.md
|   `-- phase-1-task-list.md
|   `-- development-log.md
|-- .codex/skills/daily-planner-mvp/
|   |-- agents/openai.yaml
|   `-- SKILL.md
|-- main.py
|-- frontend.py
|-- .env.example
|-- .gitignore
|-- requirements.txt
`-- README.md
```

## Local Setup (Windows PowerShell)

### 1. Clone the repository

```powershell
git clone <repository-url>
cd AIAgentProject
```

If the repository already exists locally, open the folder directly in VS Code.

### 2. Create a virtual environment

```powershell
py -3.11 -m venv .venv
```

### 3. Activate the environment

```powershell
.\.venv\Scripts\Activate.ps1
```

### 4. Install dependencies

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

The core dependency set includes:

```text
httpx
openai
openai-agents
pydantic
python-dotenv
streamlit
pytest
```

### 5. Configure environment variables

Copy `.env.example` to `.env` and add your own API key:

```powershell
Copy-Item .env.example .env
```

```env
MOONSHOT_API_KEY=replace_with_your_moonshot_api_key
KIMI_MODEL=kimi-k3
KIMI_BASE_URL=https://api.moonshot.ai/v1
KIMI_REASONING_EFFORT=max
WEATHER_PROVIDER=open-meteo
WEATHER_TIMEOUT_SECONDS=20
```

Open-Meteo does not require an API key for this MVP. Set
`WEATHER_PROVIDER=mock` when working completely offline.

Never commit `.env` or API keys to GitHub. Each developer should use their own local credentials.

### Optional Google Calendar and Todoist setup

Google Calendar is optional. Enable the Calendar API in a Google Cloud project,
create a desktop OAuth client, and save its downloaded JSON under
`secrets/google_client_secret.json`. The first Google Calendar use opens a
browser for consent. Keep the generated OAuth token and sync records under
`data/`; both are ignored by Git.

For Todoist, set `TODOIST_API_TOKEN` in `.env`. The current Todoist integration
reads open tasks for planning; it never commits the token.

## Development Commands

Run the OpenAI Agents SDK planning demo:

```powershell
python main.py
```

Run the visual frontend:

```powershell
python -m streamlit run frontend.py
```

The frontend starts in offline Demo mode, so it works without an API key.
Disable Demo mode in the sidebar to use the configured Moonshot model.

Run tests:

```powershell
python -m pytest -q
```

## Initial Data Contracts

The planner defines structured Pydantic models for `PlanItem`, `PlanValidation`,
and `DailyPlan`. The database-free memory draft defines `TimeSlot`, `EditEvent`,
`BehavioralPreference`, and `ProfileMemory`. See
[`docs/memory-design.md`](docs/memory-design.md) for the boundary between these
contracts and the deferred learning/persistence work.

The following broader contracts remain part of the planned application
architecture:

- `Task`
- `CalendarEvent`
- `UserPreference`

Every layer should share these contracts:

- Tools return structured calendar, todo, weather, and preference data.
- The agent returns a structured plan.
- The validator checks the same plan structure.
- The frontend renders the same plan structure.
- The database stores the plan and subsequent edits.

## Tool Providers

The planner currently uses:

- `get_calendar_events(date)`
- `get_todos()`
- `get_user_preferences()`
- `get_weather(location, date)`

Calendar and todo providers can be mocked for offline tests or switched to
Google Calendar and Todoist in the Streamlit sidebar. Preferences combine a fixed profile with
locally aggregated feedback evidence. Weather uses Open-Meteo by default and
retains a deterministic mock provider for offline tests. Provider interfaces
allow future APIs to replace these implementations without changing planner
contracts.

## Memory Design

The project separates memory into three categories:

1. **Conversation memory**: recent messages needed for a multi-turn interaction.
2. **Profile memory**: structured, relatively stable preferences such as preferred start time or maximum activities per day.
3. **Trajectory memory**: raw evidence such as moving, deleting, accepting, or skipping a plan item.

Long-term preferences are not inferred from one edit. The current aggregator
requires repeated evidence and stores an evidence count, confidence, and update
time. The JSONL store is intended for local demonstration, not production data.

## First Milestone Acceptance Criteria

The first milestone is complete when:

- [x] A fixed natural-language request can be submitted.
- [x] The agent calls mock calendar, todo, and preference tools.
- [x] The agent returns a validated `DailyPlan` object.
- [x] The validator detects overlapping events.
- [x] A valid plan contains no overlaps with existing events.
- [x] The plan can be displayed in a terminal demo.
- [x] Optional CLI feedback records accept, move, delete, and skip evidence.
- [x] Tests cover valid plans, conflicts, feedback, and preference aggregation.

## Roadmap

### Phase 1: Daily-planning vertical slice

- Define data models.
- Implement mock tools.
- Create one planner agent.
- Add deterministic conflict validation.
- Display a plan draft.

### Phase 2: Editing and memory

- Replace local JSONL evidence with production persistence.
- Refine preference-update and confidence rules.
- Add user controls to inspect and correct learned preferences.
- Apply learned preferences through structured planner integration.

### Phase 3: Real calendar integration

- Configure Google Calendar OAuth.
- Read real calendar events.
- Require confirmation before calendar writes.
- Synchronize confirmed plans.

### Phase 4: Travel planning

- Search candidate places and activities.
- Reuse daily-planning preferences.
- Validate opening hours, travel time, pace, and breaks.
- Record travel-plan edits in the same memory system.

## Team Workflow

Use short-lived feature branches and pull requests:

```text
main
|-- feature/agent
|-- feature/scheduler
`-- feature/frontend
```

Recommended workflow:

1. Create a branch from the latest `main`.
2. Implement one small, testable change.
3. Commit with a clear message.
4. Push the branch and open a pull request.
5. Ask at least one teammate to review it.
6. Merge only after tests pass.

Do not commit API keys, OAuth tokens, `.env`, `.venv`, or local database files.

The issue-ready Phase 1 backlog is maintained in
[`docs/phase-1-task-list.md`](docs/phase-1-task-list.md). When using Codex for
repository work, invoke the project-local skill with `$daily-planner-mvp`; it
captures the current phase boundary, tool integration rules, testing
expectations, and memory direction.

## Definition of Project Success

The project should demonstrate more than plan generation:

> A user edits a daily plan, the system stores evidence from that edit, and a later daily or travel plan changes in an observable and explainable way because of the learned preference.

## Contributors

Add team members and responsibilities here:

- Team member 1 — Agent and tools
- Team member 2 — Scheduling, memory, and evaluation
- Team member 3 — Frontend and integrations

## License

Add a license if the repository will be made public. For a private course repository, follow the course or institution requirements.
