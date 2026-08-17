"""Sample-record builder for tests — every field an upsert touches, with
sane defaults so a test only has to override what it's actually testing.
"""
from __future__ import annotations

_counter = {"n": 0}


def make_record(**overrides) -> dict:
    _counter["n"] += 1
    base = {
        "id": f"us{_counter['n']:06d}",
        "magnitude": 3.2,
        "mag_type": "ml",
        "place": "10km NE of Somewhere",
        "event_time_ms": 1_700_000_000_000,
        "updated_time_ms": 1_700_000_000_000,
        "longitude": -122.0,
        "latitude": 37.0,
        "depth_km": 8.1,
        "event_type": "earthquake",
        "status": "automatic",
        "tsunami": False,
        "alert_level": None,
        "felt_reports": None,
        "url": "https://earthquake.usgs.gov/example",
        "raw_json": "{}",
    }
    base.update(overrides)
    return base
