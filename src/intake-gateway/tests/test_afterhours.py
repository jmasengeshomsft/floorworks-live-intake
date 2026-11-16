from datetime import datetime

import pytest

from app.afterhours import is_active
from app.config import Settings, load_settings


@pytest.mark.parametrize("timestamp, expected", [
    ("2026-11-16T12:59:59+00:00", True),
    ("2026-11-16T13:00:00+00:00", False),
    ("2026-11-16T23:59:59+00:00", False),
    ("2026-11-17T00:00:00+00:00", True),
    ("2026-11-21T16:00:00+00:00", True),
])
def test_office_hours_boundaries(timestamp, expected):
    settings = Settings(after_hours="auto")
    assert is_active(settings, datetime.fromisoformat(timestamp)) is expected


@pytest.mark.parametrize("timestamp, expected", [
    ("2026-11-16T01:00:00+00:00", True),
    ("2026-11-17T01:00:00+00:00", False),
])
def test_working_day_uses_service_timezone_not_utc(timestamp, expected):
    settings = Settings(after_hours="auto", service_hours="00:00-23:59")
    assert is_active(settings, datetime.fromisoformat(timestamp)) is expected


@pytest.mark.parametrize("timestamp, expected", [
    ("2026-03-08T11:59:59+00:00", True),
    ("2026-03-08T12:00:00+00:00", False),
    ("2026-11-01T12:59:59+00:00", True),
    ("2026-11-01T13:00:00+00:00", False),
])
def test_daylight_saving_changes_utc_opening_time(timestamp, expected):
    settings = Settings(after_hours="auto", service_days="Sun")
    assert is_active(settings, datetime.fromisoformat(timestamp)) is expected


@pytest.mark.parametrize("mode, expected", [("on", True), ("off", False)])
@pytest.mark.parametrize("timestamp", ["2026-11-16T16:00:00+00:00", "2026-11-22T03:00:00+00:00"])
def test_explicit_mode_overrides_the_schedule(mode, expected, timestamp):
    assert is_active(Settings(after_hours=mode), datetime.fromisoformat(timestamp)) is expected


def test_schedule_environment_is_loaded_without_changing_legacy_mode(monkeypatch):
    monkeypatch.delenv("AFTER_HOURS", raising=False)
    for name, value in {
        "SERVICE_TZ": "Europe/Paris",
        "SERVICE_DAYS": "Tue-Sat",
        "SERVICE_HOURS": "09:00-17:30",
    }.items():
        monkeypatch.setenv(name, value)
    assert load_settings().after_hours == "off"
    monkeypatch.setenv("AFTER_HOURS", "auto")
    settings = load_settings()
    assert (settings.after_hours, settings.service_tz, settings.service_days, settings.service_hours) == (
        "auto", "Europe/Paris", "Tue-Sat", "09:00-17:30",
    )
