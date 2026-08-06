# Personal Planner Agent

A personal planning agent that learns from how users organize their daily lives and travel.

The project combines daily scheduling and travel planning through one shared user-preference model. Instead of only remembering what a user says, the system records how the user edits, accepts, deletes, moves, and skips planned activities, then uses that evidence to improve future plans.

## Project Status

This repository is in the initial MVP stage. The first milestone is a small end-to-end daily-planning flow using mock calendar data. Real Google Calendar integration, travel planning, and long-term preference learning will be added incrementally.

## MVP Goal

The first working demo should support this flow:

1. A user describes tasks for a day in natural language.
2. The agent reads existing events from a mock calendar tool.
3. The agent reads mock user preferences.
4. The agent generates a structured daily-plan draft.
5. Deterministic Python validation checks for time conflicts.
6. The frontend displays the draft for user review.
7. Later iterations record edits and update long-term preferences.

The first milestone does **not** include automatic booking, payment, multi-agent orchestration, vector databases, or direct writes to a real calendar.

## Core Idea

```text
User request
    -> Planner Agent
    -> Calendar / Memory tools
    -> Structured plan
    -> Python validation
    -> User review and edits
    -> Edit-event storage
    -> Preference update
    -> Better future plans
```

The LLM is responsible for understanding requests, selecting tools, and explaining plans. Regular Python code is responsible for hard constraints such as time conflicts, durations, validation, and database writes.

## Proposed Architecture

```text
Streamlit frontend
        |
        v
FastAPI backend
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

- Python 3.12
- OpenAI Agents SDK
- Pydantic for structured inputs and outputs
- FastAPI for backend endpoints
- Streamlit for the initial frontend
- SQLite for local storage
- pytest for tests
- Git and GitHub for collaboration

## Suggested Project Structure

```text
AIAgentProject/
|-- app/
|   |-- main.py
|   |-- agent.py
|   |-- schemas.py
|   |-- api/
|   |   `-- routes.py
|   |-- tools/
|   |   |-- calendar.py
|   |   `-- memory.py
|   |-- planning/
|   |   `-- validator.py
|   `-- memory/
|       `-- preference_updater.py
|-- frontend/
|   `-- app.py
|-- data/
|-- tests/
|   |-- test_agent.py
|   `-- test_validator.py
|-- .env.example
|-- .gitignore
|-- requirements.txt
`-- README.md
```

The folders above are planned structure and will be created as implementation begins.

## Local Setup (Windows PowerShell)

### 1. Clone the repository

```powershell
git clone <repository-url>
cd AIAgentProject
```

If the repository already exists locally, open the folder directly in VS Code.

### 2. Create a virtual environment

```powershell
py -3.12 -m venv .venv
```

### 3. Activate the environment

```powershell
.\.venv\Scripts\Activate.ps1
```

### 4. Install dependencies

After `requirements.txt` is created:

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

The initial dependency set is expected to include:

```text
openai-agents
fastapi[standard]
streamlit
python-dotenv
pytest
```

### 5. Configure environment variables

Copy `.env.example` to `.env` and add your own API key:

```powershell
Copy-Item .env.example .env
```

```env
OPENAI_API_KEY=replace_with_your_key
OPENAI_MODEL=gpt-5.6-terra
OPENAI_REASONING_EFFORT=medium
# AGENT_SYSTEM_PROMPT=Optional one-line override for the built-in English prompt
```

Never commit `.env` or API keys to GitHub. Each developer should use their own local credentials.

## Planned Development Commands

These commands will work after the corresponding application files are implemented.

Run the backend:

```powershell
fastapi dev app/main.py
```

Run the frontend:

```powershell
streamlit run frontend/app.py
```

Run tests:

```powershell
pytest
```

## Initial Data Contracts

The first implementation should define these Pydantic models before building the UI or connecting real services:

- `Task`
- `CalendarEvent`
- `PlanItem`
- `DailyPlan`
- `EditEvent`
- `UserPreference`

Every layer should share these contracts:

- Tools return structured calendar and preference data.
- The agent returns a structured plan.
- The validator checks the same plan structure.
- The frontend renders the same plan structure.
- The database stores the plan and subsequent edits.

## Initial Mock Tools

The first agent should use mock implementations of:

- `get_calendar_events(date)`
- `get_user_preferences(user_id)`
- `schedule_tasks(tasks, events, preferences)`

Mock tools keep the first milestone independent from Google OAuth and other external-service setup. Real integrations should replace the tool internals later without changing their public input and output contracts.

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
