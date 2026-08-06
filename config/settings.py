import os
from dotenv import load_dotenv

# load .env
load_dotenv()

# Kimi API

MOONSHOT_API_KEY = os.environ["MOONSHOT_API_KEY"]

KIMI_MODEL = os.getenv(
    "KIMI_MODEL",
    "kimi-k3"
)

KIMI_BASE_URL = os.getenv(
    "KIMI_BASE_URL",
    "https://api.moonshot.ai/v1"
)

KIMI_REASONING_EFFORT = os.getenv(
    "KIMI_REASONING_EFFORT",
    "max"
)

# Agent
AGENT_SYSTEM_PROMPT = os.getenv(
    "AGENT_SYSTEM_PROMPT",
    "你是一个智能生活规划助手。"
)


# check API key
if not MOONSHOT_API_KEY:
    raise ValueError(
        "Missing MOONSHOT_API_KEY in .env"
    )