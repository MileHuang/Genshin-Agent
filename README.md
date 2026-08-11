# Personal Planner Agent

A personal planning agent that learns from how users organize their daily lives and travel.

The project combines daily scheduling and travel planning through one shared user-preference model. Instead of only remembering what a user says, the system records how the user edits, accepts, deletes, moves, and skips planned activities, then uses that evidence to improve future plans.

## Project Status

This repository now contains the backend core for the first MVP: an OpenAI
Agents SDK planner, a Kimi K3 model configured through Moonshot's
OpenAI-compatible API, mock calendar/preference tools, live Open-Meteo weather,
a scheduler, deterministic conflict validation, and offline tests. Real
calendar integration, a frontend, and long-term preference learning remain
planned.

The repository also contains database-free Phase 1 memory contracts for edit
evidence and learned-preference snapshots. Preference inference, persistence,
and planner integration are still planned.

## MVP Goal

The first working demo should support this flow:

1. A user describes tasks for a day in natural language.
2. The agent reads existing events from a mock calendar tool.
3. The agent reads mock user preferences.
4. The agent reads live weather when a goal involves travel or outdoor activity.
5. The agent generates a structured daily-plan draft.
6. Deterministic Python validation checks for time conflicts.
7. The frontend displays the draft for user review.
8. Later iterations record edits and update long-term preferences.

The first milestone does **not** include automatic booking, payment,
multi-agent handoffs, vector databases, or direct writes to a real calendar.

## Core Idea

```text
User request
    -> OpenAI Agents SDK Runner
    -> Personal Planner Agent
    -> Calendar / Memory / Weather tools
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

Calendar writes must be treated as side effects. The system should generate a draft first and write to a real calendar only after explicit user confirmation.

## Technology Stack

- Python 3.11+
- OpenAI Agents SDK for agent orchestration, tool calling, and structured output
- Kimi K3 through Moonshot's OpenAI-compatible Chat Completions API
- Open-Meteo for live geocoding and daily weather forecasts
- OpenAI Python SDK as the underlying compatible API client
- HTTPX for injectable offline transports and live weather requests
- Pydantic for structured inputs and outputs
- pytest for tests
- Git and GitHub for collaboration

FastAPI, Streamlit, and SQLite remain planned for later phases.

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
|   |-- weather_tool.py
|   |-- preference_tool.py
|   `-- validator_tool.py
|-- tests/
|   |-- test_planner_agent.py
|   |-- test_calendar_tool.py
|   |-- test_weather_tool.py
|   |-- test_memory_models.py
|   `-- test_daily_pipeline.py
|-- docs/
|   |-- memory-design.md
|   `-- phase-1-task-list.md
|-- .codex/skills/daily-planner-mvp/
|   |-- agents/openai.yaml
|   `-- SKILL.md
|-- main.py
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

## Development Commands

Run the OpenAI Agents SDK planning demo:

```powershell
python main.py
```

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

- Tools return structured calendar and preference data.
- The agent returns a structured plan.
- The validator checks the same plan structure.
- The frontend renders the same plan structure.
- The database stores the plan and subsequent edits.

## Tool Providers

The planner currently uses:

- `get_calendar_events(date)`
- `get_user_preferences(user_id)`
- `schedule_tasks(tasks, events, preferences)`
- `get_weather(location, date)`

Calendar and preference data are mocked. Weather uses Open-Meteo by default and
retains a deterministic mock provider for offline tests. The provider interfaces
allow future APIs to replace the current implementations without changing the
planner-facing contracts.

## Memory Design

The project separates memory into three categories:

1. **Conversation memory**: recent messages needed for a multi-turn interaction.
2. **Profile memory**: structured, relatively stable preferences such as preferred start time or maximum activities per day.
3. **Trajectory memory**: raw evidence such as moving, deleting, accepting, or skipping a plan item.

Long-term preferences should not be overwritten after one edit. The preference updater should store evidence count, confidence, and last-updated time, then update a preference only after sufficient evidence.

## First Milestone Acceptance Criteria

The first milestone is complete when:

- [ ] A fixed natural-language request can be submitted.
- [ ] The agent calls a mock calendar tool.
- [ ] The agent calls a mock preference tool.
- [ ] The agent returns a validated `DailyPlan` object.
- [ ] The validator detects overlapping events.
- [ ] A valid plan contains no overlaps with existing events.
- [ ] The plan can be displayed in a terminal or minimal Streamlit page.
- [ ] Tests cover at least one valid plan and one conflicting plan.

## Roadmap

### Phase 1: Daily-planning vertical slice

- Define data models.
- Implement mock tools.
- Create one planner agent.
- Add deterministic conflict validation.
- Display a plan draft.

### Phase 2: Editing and memory

- Save plans in SQLite.
- Record move, delete, accept, and skip events.
- Implement initial preference-update rules.
- Use updated preferences in later plans.

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
