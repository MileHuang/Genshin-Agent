"""Shared activity identifiers for feedback and deterministic memory rules."""

import re
import unicodedata


def activity_tokens(text: str) -> list[str]:
    return re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", text).casefold())


def activity_type(title: str) -> str:
    """Preserve Unicode letters while keeping existing English identifiers."""
    return "_".join(activity_tokens(title)) or "general"


def mentions_activity(text: str, activity: str) -> bool:
    """Match whole words, or Chinese phrases within unsegmented text."""
    wanted = activity_tokens(activity)
    actual = activity_tokens(text)
    return bool(wanted) and all(
        token in actual
        or (any("\u3400" <= char <= "\u9fff" for char in token)
            and any(token in candidate for candidate in actual))
        for token in wanted
    )
