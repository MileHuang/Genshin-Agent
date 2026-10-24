# Development Log

## 2026-09-03 - Member B Memory and Planner Integration

### Delivered

- Added the shared `get_memory_context(user_id, goal)` contract.
- Added a local adapter that returns profile, evidence-backed preferences, and
  goal-relevant events.
- Added deterministic preferred-time, avoid-time, and deprioritization rules.
- Added Planner user isolation and explainable memory-rule application.
- Added a Streamlit Memory page for profile editing and learned-memory
  inspection, editing, pause/resume, and forget actions.
- Added an end-to-end test proving that three gym moves affect the next plan.
- Migrated source code, UI text, error messages, mock data, tests, and project
  documentation to English.

### Current persistence boundary

- Behavioral evidence remains in append-only local JSONL.
- User-authored memory controls use a replaceable local JSON overlay.
- SQLite persistence remains a separate backend task.

## 2026-08-25 - Real Calendar and Todo Integration

### Delivered

- Added optional Google Calendar OAuth integration for reading existing events.
- Added explicit-confirmation writes to Google Calendar for generated plans.
- Added local sync records that store the Google event IDs for each synced plan.
- Added duplicate-sync protection that survives application restarts for plans synced after this release.
- Added Google Calendar feedback synchronization: MOVE updates an event and DELETE removes it.
- Added optional Todoist integration that reads open tasks and selects the most relevant tasks for planning.
- Added Streamlit controls for feedback and a dashboard showing evidence and learned preferences.
- Reworked the online planning path to fetch Calendar, Todoist, and Preference context before creating a plan.

### Safety and privacy

- Calendar writes require an explicit confirmation click.
- OAuth client JSON, OAuth tokens, Todoist tokens, `.env`, and local user data remain ignored by Git.
- SKIP records feedback only; it does not delete a Google Calendar event.

### Verification

- `conda run -n genshin-agent python -m pytest -q`
- Result: 85 tests passed.

### Next candidate

- Add Todoist write-back for completing a source task after a user confirms it is done.
