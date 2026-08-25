from tools.calendar_sync_store import CalendarSyncStore, build_plan_sync_key


def test_plan_sync_key_is_stable_and_changes_with_schedule_content():
    items = [
        {
            "title": "Study",
            "start_time": "09:00",
            "end_time": "10:00",
            "notes": "Review notes",
        }
    ]

    original_key = build_plan_sync_key("2026-08-25", items)
    assert build_plan_sync_key("2026-08-25", items) == original_key

    changed_items = [dict(items[0], start_time="10:00")]
    assert build_plan_sync_key("2026-08-25", changed_items) != original_key


def test_sync_store_persists_event_ids_across_instances(tmp_path):
    path = tmp_path / "google_calendar_syncs.json"
    store = CalendarSyncStore(path)

    store.record(
        "plan-key",
        target_date="2026-08-25",
        event_ids=["google-event-1", "google-event-2"],
    )

    reloaded_store = CalendarSyncStore(path)
    assert reloaded_store.is_synced("plan-key") is True
    record = reloaded_store.get("plan-key")
    assert record is not None
    assert record["target_date"] == "2026-08-25"
    assert record["event_ids"] == ["google-event-1", "google-event-2"]
    assert record["synced_at"]


def test_sync_store_replaces_record_after_schedule_edit(tmp_path):
    store = CalendarSyncStore(tmp_path / "google_calendar_syncs.json")
    store.record("old-plan", target_date="2026-08-25", event_ids=["event-1"])

    store.replace(
        "old-plan",
        "updated-plan",
        target_date="2026-08-25",
        event_ids=["event-1"],
    )

    assert store.is_synced("old-plan") is False
    assert store.get("updated-plan")["event_ids"] == ["event-1"]
