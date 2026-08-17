"""Upsert semantics: the natural key means a revised event updates its
existing row instead of creating a duplicate, and a genuinely unchanged
event is skipped rather than rewritten.
"""
from sqlalchemy import select

from pipeline import db

from .factories import make_record


def test_new_record_is_inserted(engine):
    result = db.upsert_earthquakes(engine, [make_record(id="us0001")], now_ms=100)
    assert result.inserted == 1

    with engine.connect() as conn:
        row = conn.execute(select(db.earthquakes).where(db.earthquakes.c.id == "us0001")).fetchone()
    assert row is not None
    assert row.first_seen_at_ms == 100
    assert row.last_seen_at_ms == 100


def test_revision_updates_the_existing_row_not_a_duplicate(engine):
    original = make_record(id="us0001", magnitude=4.0, updated_time_ms=1000)
    db.upsert_earthquakes(engine, [original], now_ms=100)

    revised = make_record(id="us0001", magnitude=4.6, updated_time_ms=2000)
    result = db.upsert_earthquakes(engine, [revised], now_ms=200)

    assert result.inserted == 0
    assert result.updated == 1

    with engine.connect() as conn:
        rows = conn.execute(select(db.earthquakes).where(db.earthquakes.c.id == "us0001")).fetchall()

    assert len(rows) == 1
    assert rows[0].magnitude == 4.6
    assert rows[0].first_seen_at_ms == 100  # preserved across the revision
    assert rows[0].last_seen_at_ms == 200


def test_unchanged_updated_time_is_skipped_not_rewritten(engine):
    record = make_record(id="us0001", magnitude=4.0, updated_time_ms=1000)
    db.upsert_earthquakes(engine, [record], now_ms=100)

    result = db.upsert_earthquakes(engine, [record], now_ms=999)
    assert result.skipped == 1
    assert result.inserted == 0
    assert result.updated == 0

    with engine.connect() as conn:
        row = conn.execute(select(db.earthquakes).where(db.earthquakes.c.id == "us0001")).fetchone()
    assert row.last_seen_at_ms == 100  # untouched by the skipped run


def test_primary_key_is_the_source_system_id_not_an_autoincrement(engine):
    assert [c.name for c in db.earthquakes.primary_key.columns] == ["id"]
