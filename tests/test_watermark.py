"""Watermark tracks the incremental cursor and last-run outcome — the thing
a 'did the job even run?' alert would query in a real deployment.
"""
from sqlalchemy import select

from pipeline import db


def test_watermark_starts_unset(engine):
    assert db.get_watermark_ms(engine, "usgs_earthquakes") is None


def test_watermark_advances_after_a_successful_run(engine):
    db.start_run(engine, "usgs_earthquakes", now_ms=100)
    db.complete_run(engine, "usgs_earthquakes", new_watermark_ms=5000, now_ms=150)
    assert db.get_watermark_ms(engine, "usgs_earthquakes") == 5000


def test_watermark_does_not_move_backward_on_an_empty_run(engine):
    db.start_run(engine, "usgs_earthquakes", now_ms=100)
    db.complete_run(engine, "usgs_earthquakes", new_watermark_ms=5000, now_ms=150)

    db.start_run(engine, "usgs_earthquakes", now_ms=200)
    db.complete_run(engine, "usgs_earthquakes", new_watermark_ms=None, now_ms=250)

    assert db.get_watermark_ms(engine, "usgs_earthquakes") == 5000


def test_failed_run_records_status_and_error_without_advancing_watermark(engine):
    db.start_run(engine, "usgs_earthquakes", now_ms=100)
    db.fail_run(engine, "usgs_earthquakes", now_ms=150, error="boom")

    with engine.connect() as conn:
        row = conn.execute(
            select(db.pipeline_watermark).where(db.pipeline_watermark.c.job_name == "usgs_earthquakes")
        ).fetchone()
    assert row.last_run_status == "failed"
    assert row.last_run_error == "boom"
    assert row.last_updated_watermark_ms is None
