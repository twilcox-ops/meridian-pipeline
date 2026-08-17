"""Database schema and upsert logic.

The primary key on `earthquakes` is the id USGS assigns to each event (e.g.
"us7000abcd"), not an autoincrement integer. USGS guarantees that id is
stable and unique for the life of the event, which is what makes reruns
safe: the same event fetched twice — whether because a run was retried, a
backfill overlaps a scheduled run, or the job crashed and restarted — always
maps to the same row. There is no way to get a duplicate.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Engine,
    Float,
    Integer,
    MetaData,
    Table,
    Text,
    create_engine,
    select,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

metadata = MetaData()

earthquakes = Table(
    "earthquakes",
    metadata,
    # Natural key from the source system — see module docstring.
    Column("id", Text, primary_key=True),
    Column("magnitude", Float),
    Column("mag_type", Text),
    Column("place", Text),
    Column("event_time_ms", BigInteger, nullable=False),
    Column("updated_time_ms", BigInteger, nullable=False, index=True),
    Column("longitude", Float),
    Column("latitude", Float),
    Column("depth_km", Float),
    Column("event_type", Text),
    Column("status", Text),
    Column("tsunami", Boolean),
    Column("alert_level", Text),
    Column("felt_reports", Integer),
    Column("url", Text),
    Column("raw_json", Text),  # full GeoJSON feature, for future reprocessing
    Column("first_seen_at_ms", BigInteger, nullable=False),
    Column("last_seen_at_ms", BigInteger, nullable=False),
)

# One row per job. Tracks the incremental cursor and the outcome of the most
# recent run, which is what a "did the job even run?" alert queries.
pipeline_watermark = Table(
    "pipeline_watermark",
    metadata,
    Column("job_name", Text, primary_key=True),
    Column("last_updated_watermark_ms", BigInteger),
    Column("last_run_started_at_ms", BigInteger),
    Column("last_run_completed_at_ms", BigInteger),
    Column("last_run_status", Text),
    Column("last_run_error", Text),
)

_UPSERT_EXCLUDED_FROM_UPDATE = {"id", "first_seen_at_ms"}


@dataclass
class UpsertResult:
    inserted: int
    updated: int
    skipped: int


def get_engine(database_url: str) -> Engine:
    return create_engine(database_url, future=True)


def ensure_schema(engine: Engine) -> None:
    metadata.create_all(engine)


def upsert_earthquakes(engine: Engine, records: list[dict], now_ms: int) -> UpsertResult:
    """Insert new events, update revised ones, skip unchanged ones.

    A record is "unchanged" when its `updated_time_ms` matches what's already
    stored: USGS bumps that field whenever it revises an event's magnitude or
    location, so an identical `updated_time_ms` means we've already applied
    this exact version. That's the property tests/test_idempotency.py relies
    on — fetch the same window five times, and every rerun after the first is
    all-skip, so row count and contents never move.

    Runs as a single transaction. If the process is killed mid-batch, nothing
    in this call has committed, so the database is left exactly as it was
    before the run started. The next run refetches the same window — the
    watermark only advances in a separate call, after this one returns
    successfully — and reapplies it, which is safe for the same reason a
    rerun is always safe.
    """
    if not records:
        return UpsertResult(inserted=0, updated=0, skipped=0)

    ids = [r["id"] for r in records]
    insert_fn = pg_insert if engine.dialect.name == "postgresql" else sqlite_insert

    with engine.begin() as conn:
        existing = dict(
            conn.execute(
                select(earthquakes.c.id, earthquakes.c.updated_time_ms).where(earthquakes.c.id.in_(ids))
            ).all()
        )

        inserted = updated = skipped = 0
        for record in records:
            prior_updated_ms = existing.get(record["id"])
            if prior_updated_ms is not None and prior_updated_ms == record["updated_time_ms"]:
                skipped += 1
                continue

            values = {**record, "last_seen_at_ms": now_ms}
            values.setdefault("first_seen_at_ms", now_ms)

            stmt = insert_fn(earthquakes).values(**values)
            update_cols = {
                col.name: getattr(stmt.excluded, col.name)
                for col in earthquakes.columns
                if col.name not in _UPSERT_EXCLUDED_FROM_UPDATE
            }
            stmt = stmt.on_conflict_do_update(index_elements=["id"], set_=update_cols)
            conn.execute(stmt)

            if record["id"] in existing:
                updated += 1
            else:
                inserted += 1

    return UpsertResult(inserted=inserted, updated=updated, skipped=skipped)


def get_watermark_ms(engine: Engine, job_name: str) -> Optional[int]:
    with engine.begin() as conn:
        row = conn.execute(
            select(pipeline_watermark.c.last_updated_watermark_ms).where(
                pipeline_watermark.c.job_name == job_name
            )
        ).first()
    return row[0] if row else None


def start_run(engine: Engine, job_name: str, now_ms: int) -> None:
    insert_fn = pg_insert if engine.dialect.name == "postgresql" else sqlite_insert
    stmt = insert_fn(pipeline_watermark).values(
        job_name=job_name,
        last_run_started_at_ms=now_ms,
        last_run_status="running",
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["job_name"],
        set_={"last_run_started_at_ms": now_ms, "last_run_status": "running"},
    )
    with engine.begin() as conn:
        conn.execute(stmt)


def complete_run(engine: Engine, job_name: str, new_watermark_ms: Optional[int], now_ms: int) -> None:
    values = {"last_run_completed_at_ms": now_ms, "last_run_status": "success", "last_run_error": None}
    if new_watermark_ms is not None:
        values["last_updated_watermark_ms"] = new_watermark_ms
    with engine.begin() as conn:
        conn.execute(
            pipeline_watermark.update().where(pipeline_watermark.c.job_name == job_name).values(**values)
        )


def fail_run(engine: Engine, job_name: str, now_ms: int, error: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            pipeline_watermark.update()
            .where(pipeline_watermark.c.job_name == job_name)
            .values(last_run_completed_at_ms=now_ms, last_run_status="failed", last_run_error=error[:2000])
        )
