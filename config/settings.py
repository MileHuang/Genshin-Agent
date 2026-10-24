"""Application settings loaded from environment variables."""

import os
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


PROJECT_ROOT = Path(__file__).resolve().parent.parent


_raw_moonshot_api_key = os.getenv("MOONSHOT_API_KEY", "").strip()
MOONSHOT_API_KEY = (
    None
    if not _raw_moonshot_api_key
    or _raw_moonshot_api_key == "replace_with_your_moonshot_api_key"
    else _raw_moonshot_api_key
)
KIMI_MODEL = os.getenv("KIMI_MODEL", "kimi-k3")
KIMI_BASE_URL = os.getenv("KIMI_BASE_URL", "https://api.moonshot.ai/v1")
KIMI_REASONING_EFFORT = os.getenv("KIMI_REASONING_EFFORT", "max")
KIMI_TIMEOUT_SECONDS = float(os.getenv("KIMI_TIMEOUT_SECONDS", "120"))

WEATHER_PROVIDER = os.getenv("WEATHER_PROVIDER", "open-meteo").strip().lower()
WEATHER_TIMEOUT_SECONDS = float(os.getenv("WEATHER_TIMEOUT_SECONDS", "20"))
OPEN_METEO_GEOCODING_URL = os.getenv(
    "OPEN_METEO_GEOCODING_URL",
    "https://geocoding-api.open-meteo.com/v1/search",
)
OPEN_METEO_FORECAST_URL = os.getenv(
    "OPEN_METEO_FORECAST_URL",
    "https://api.open-meteo.com/v1/forecast",
)

CALENDAR_PROVIDER = os.getenv("CALENDAR_PROVIDER", "mock").strip().lower()
GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary").strip() or "primary"
GOOGLE_CALENDAR_TIMEZONE = os.getenv(
    "GOOGLE_CALENDAR_TIMEZONE", "Asia/Shanghai"
).strip() or "Asia/Shanghai"
GOOGLE_CLIENT_SECRET_FILE = PROJECT_ROOT / os.getenv(
    "GOOGLE_CLIENT_SECRET_FILE", "secrets/google_client_secret.json"
)
GOOGLE_TOKEN_FILE = PROJECT_ROOT / os.getenv(
    "GOOGLE_TOKEN_FILE", "data/google_token.json"
)
GOOGLE_SYNC_RECORD_FILE = PROJECT_ROOT / os.getenv(
    "GOOGLE_SYNC_RECORD_FILE", "data/google_calendar_syncs.json"
)

_raw_todoist_api_token = os.getenv("TODOIST_API_TOKEN", "").strip()
TODOIST_API_TOKEN = (
    None
    if not _raw_todoist_api_token
    or _raw_todoist_api_token == "replace_with_your_todoist_api_token"
    else _raw_todoist_api_token
)
TODOIST_API_BASE_URL = os.getenv(
    "TODOIST_API_BASE_URL", "https://api.todoist.com/api/v1"
).rstrip("/")
TODOIST_TIMEOUT_SECONDS = float(os.getenv("TODOIST_TIMEOUT_SECONDS", "20"))


DEFAULT_AGENT_SYSTEM_PROMPT = """
You are Kimi, the reasoning engine for a practical personal planning assistant.
Help users turn goals, commitments, and constraints into realistic, actionable
plans.

Working principles:
- Identify the user's real objective, fixed commitments, preferences, and
  missing constraints before proposing a plan.
- Use calendar, weather, and preference context only when it is supplied; never
  imply access to tools or personal data that you have not received.
- Protect fixed events, avoid overlapping activities, and allow realistic time
  for transitions, meals, breaks, and recovery.
- Prioritize important or time-sensitive work without making the day
  unrealistically full.
- State material assumptions. Ask one concise clarifying question only when the
  missing information would significantly change the result; otherwise make a
  reasonable assumption and continue.
- Follow the requested output schema exactly when one is provided.

Keep responses clear, grounded, and easy to act on. Reply in the user's
language unless the caller explicitly requests another language.
""".strip()


AGENT_SYSTEM_PROMPT = os.getenv(
    "AGENT_SYSTEM_PROMPT",
    DEFAULT_AGENT_SYSTEM_PROMPT,
)
