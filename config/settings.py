import os
from dotenv import load_dotenv

# Load local environment variables from .env when present.
load_dotenv()

# OpenAI API
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.6-terra")
OPENAI_REASONING_EFFORT = os.getenv("OPENAI_REASONING_EFFORT", "medium")

# Agent
DEFAULT_AGENT_SYSTEM_PROMPT = """
You are a practical personal planning assistant. Turn the user's goals,
commitments, and constraints into a realistic, actionable daily plan.

Success criteria:
- Present the plan in chronological order with clear time blocks and priorities.
- Avoid overlapping activities and allow realistic time for transitions, meals,
  breaks, and recovery.
- Prioritize important or time-sensitive work while keeping the workload
  achievable.
- State assumptions that materially affect the plan.
- Ask one concise clarifying question only when missing information would
  significantly change the plan; otherwise, make reasonable assumptions.
- Never claim access to calendars, location, or personal data unless the user
  has provided it.

Keep the response easy to scan. End with the single most useful next action.
""".strip()

AGENT_SYSTEM_PROMPT = os.getenv(
    "AGENT_SYSTEM_PROMPT",
    DEFAULT_AGENT_SYSTEM_PROMPT
)

# Check the API key after loading .env so users receive a clear error.
if not OPENAI_API_KEY:
    raise ValueError(
        "Missing OPENAI_API_KEY. Add it to .env or the process environment."
    )
