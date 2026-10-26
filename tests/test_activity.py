import pytest

from tools.activity import activity_type, mentions_activity
from tools.memory_rules import apply_memory_rules


@pytest.mark.parametrize(("title", "expected"), [
    ("Focused study session", "focused_study_session"),
    ("健身", "健身"),
    ("读书", "读书"),
    ("复习 MATH 7200", "复习_math_7200"),
    ("Ｇｙｍ", "gym"),
])
def test_activity_identifiers_preserve_language_and_english_compatibility(title, expected):
    assert activity_type(title) == expected


def test_activity_matching_does_not_confuse_english_substrings_or_chinese_activities():
    assert mentions_activity("今晚安排健身", "健身")
    assert not mentions_activity("今天读书", "健身")
    assert not mentions_activity("gymnastics", "gym")
    assert not mentions_activity("reading", "focused_study_session")


def test_chinese_preference_moves_only_matching_activity():
    schedule, explanations = apply_memory_rules(
        [
            {"title": "健身", "start_time": "18:00", "end_time": "19:00", "category": "运动"},
            {"title": "读书", "start_time": "19:00", "end_time": "20:00", "category": "学习"},
        ], [],
        {"profile": {}, "preferences": [{
            "activity_type": activity_type("健身"), "attribute": "preferred_time_range",
            "value": {"start": "20:00", "end": "21:00"},
            "confidence": 0.9,
        }]}, goal="安排健身和读书",
    )
    by_title = {item["title"]: item for item in schedule}
    assert by_title["健身"]["start_time"] == "20:00"
    assert by_title["读书"]["start_time"] == "19:00"
    assert len(explanations) == 1


def test_explicit_chinese_goal_overrides_deprioritization():
    schedule, explanations = apply_memory_rules(
        [{"title": "健身", "start_time": "18:00", "end_time": "19:00", "priority": "high"}], [],
        {"profile": {}, "preferences": [{
            "activity_type": "健身", "attribute": "deprioritize_activity", "value": {"level": "low"},
            "confidence": 0.9,
        }]}, goal="今天一定要健身",
    )
    assert schedule[0]["priority"] == "high"
    assert explanations == []
