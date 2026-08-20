"""Proves the acceptance criterion at README.md:188 / PROJECT-1-scheduled-pipeline.md:74:

'Killing the job halfway leaves the database consistent, and the next run recovers.'

upsert_earthquakes() runs its whole batch inside one `engine.begin()` block, which
SQLAlchemy only commits on clean exit. To simulate the process dying partway through
a multi-record batch, this test forces a real exception out of `Connection.execute`
after some rows in the batch have already been executed against the connection but
before the transaction commits — SQLAlchemy's real `__exit__` handling then issues an
actual ROLLBACK, the same mechanism that protects an in-flight transaction when the
process is killed and the DB engine discards whatever was never committed. This is not
an application-level "pretend it failed" call (contrast tests/test_watermark.py's
fail_run test, which just writes a status row) — it exercises the real transactional
rollback path.
"""
import pytest
from sqlalchemy import select
from sqlalchemy.engine import Connection

from pipeline import db

from .factories import make_record


def test_kill_mid_batch_rolls_back_fully_and_next_run_recovers(engine, monkeypatch):
    records = [make_record(id=f"us{i:04d}", updated_time_ms=1_700_000_000_000) for i in range(5)]

    real_execute = Connection.execute
    calls = {"n": 0}

    def flaky_execute(self, *args, **kwargs):
        calls["n"] += 1
        # call 1 = the existing-rows SELECT, calls 2-3 = the first two inserts
        # actually reaching the connection; call 4 kills it before the rest of
        # the batch (and the commit) ever happens.
        if calls["n"] == 4:
            raise RuntimeError("simulated kill mid-batch")
        return real_execute(self, *args, **kwargs)

    monkeypatch.setattr(Connection, "execute", flaky_execute)

    with pytest.raises(RuntimeError, match="simulated kill mid-batch"):
        db.upsert_earthquakes(engine, records, now_ms=1)

    monkeypatch.undo()  # restore real execute before inspecting/reusing the engine

    with engine.connect() as conn:
        rows = conn.execute(select(db.earthquakes.c.id)).fetchall()
    assert rows == [], "an interrupted batch must leave zero committed rows, not a partial write"

    # "the next run": same batch, same code path, nothing special about the retry.
    result = db.upsert_earthquakes(engine, records, now_ms=2)
    assert result.inserted == 5
    assert result.updated == 0
    assert result.skipped == 0

    with engine.connect() as conn:
        rows = conn.execute(select(db.earthquakes.c.id)).fetchall()
    assert {row.id for row in rows} == {r["id"] for r in records}
