"""Proves the acceptance criterion from PROJECT-1-scheduled-pipeline.md:

'Run the job five times in a row against the same window; the row count
must not change.'
"""
from sqlalchemy import select

from pipeline import db

from .factories import make_record


def test_five_reruns_leave_row_count_and_contents_unchanged(engine):
    records = [make_record(id=f"us{i:04d}", updated_time_ms=1_700_000_000_000) for i in range(25)]

    first = db.upsert_earthquakes(engine, records, now_ms=1)
    assert first.inserted == 25
    assert first.updated == 0
    assert first.skipped == 0

    for attempt in range(4):
        result = db.upsert_earthquakes(engine, records, now_ms=2 + attempt)
        assert result.inserted == 0
        assert result.updated == 0
        assert result.skipped == 25, f"rerun {attempt} should be a pure no-op"

    with engine.connect() as conn:
        rows = conn.execute(select(db.earthquakes.c.id)).fetchall()
    assert len(rows) == 25


def test_empty_batch_is_a_noop(engine):
    result = db.upsert_earthquakes(engine, [], now_ms=1)
    assert result == db.UpsertResult(inserted=0, updated=0, skipped=0)
