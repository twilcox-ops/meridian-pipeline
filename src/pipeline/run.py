"""CLI entrypoint. Three invocations, all through the same
fetch -> upsert -> watermark -> digest pipeline:

    python -m pipeline.run                                    # scheduled run
    python -m pipeline.run --dry-run                           # preview only
    python -m pipeline.run --backfill-start 2026-01-01T00:00:00 --backfill-end 2026-02-01T00:00:00
"""
from __future__ import annotations

import argparse
import sys
import time
from typing import Optional

from . import config as config_module
from . import db, digest, fetch, logging_setup, timeutil

JOB_NAME = "usgs_earthquakes"
DEFAULT_LOOKBACK_MS = 7 * 24 * 60 * 60 * 1000  # first-ever run bootstrap window only


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="USGS earthquake ingestion pipeline")
    parser.add_argument("--backfill-start", help="ISO 8601 origin-time start for a backfill run")
    parser.add_argument("--backfill-end", help="ISO 8601 origin-time end for a backfill run")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and log what would happen; write nothing to the database and send no email",
    )
    parser.add_argument("--min-magnitude", type=float, default=None, help="Override MIN_MAGNITUDE for this run")
    args = parser.parse_args(argv)
    if bool(args.backfill_start) != bool(args.backfill_end):
        parser.error("--backfill-start and --backfill-end must be given together")
    return args


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    cfg = config_module.load_config()
    logger = logging_setup.get_logger()

    min_magnitude = args.min_magnitude if args.min_magnitude is not None else cfg.min_magnitude
    client = fetch.USGSClient(min_magnitude=min_magnitude)

    started_at_ms = timeutil.now_ms()
    t0 = time.monotonic()
    is_backfill = args.backfill_start is not None

    # Everything below is inside the try, including engine/schema/start_run,
    # so a DB-connectivity failure gets the same structured "run_failed" log
    # entry as a mid-run failure — the case we most care about catching is
    # not the one that should fall through to a bare traceback.
    engine = None
    try:
        engine = db.get_engine(cfg.database_url)
        db.ensure_schema(engine)

        if not args.dry_run:
            db.start_run(engine, JOB_NAME, started_at_ms)

        if is_backfill:
            start_ms = timeutil.iso_to_ms(args.backfill_start)
            end_ms = timeutil.iso_to_ms(args.backfill_end)
            records = list(client.fetch_origin_window(start_ms, end_ms))
            run_type = "backfill"
        else:
            since_ms = db.get_watermark_ms(engine, JOB_NAME)
            if since_ms is None:
                since_ms = started_at_ms - DEFAULT_LOOKBACK_MS
            records = list(client.fetch_updated_since(since_ms))
            run_type = "scheduled"

        logging_setup.log_event(logger, "fetch_complete", job=JOB_NAME, run_type=run_type, fetched=len(records))

        if args.dry_run:
            result = db.UpsertResult(inserted=0, updated=0, skipped=0)
            logging_setup.log_event(
                logger, "dry_run_preview", fetched=len(records), sample_ids=[r["id"] for r in records[:10]]
            )
        else:
            result = db.upsert_earthquakes(engine, records, now_ms=timeutil.now_ms())

        duration_seconds = round(time.monotonic() - t0, 2)

        logging_setup.log_event(
            logger,
            "run_complete",
            job=JOB_NAME,
            run_type=run_type,
            status="success",
            fetched=len(records),
            inserted=result.inserted,
            updated=result.updated,
            skipped=result.skipped,
            duration_seconds=duration_seconds,
        )

        if not args.dry_run and not is_backfill:
            # Backfill never advances the watermark — it's a bounded replay of a
            # historical origin-time window, not the forward-moving cursor.
            updated_times = [r["updated_time_ms"] for r in records if r.get("updated_time_ms") is not None]
            new_watermark_ms = max(updated_times) if updated_times else None
            db.complete_run(engine, JOB_NAME, new_watermark_ms, timeutil.now_ms())
        elif not args.dry_run:
            db.complete_run(engine, JOB_NAME, None, timeutil.now_ms())

        notable = digest.select_notable(records, cfg.notable_min_magnitude)
        if notable and not args.dry_run:
            html_body = digest.build_digest_html(
                run_summary={
                    "run_at": timeutil.ms_to_iso(started_at_ms),
                    "fetched": len(records),
                    "inserted": result.inserted,
                    "updated": result.updated,
                    "skipped": result.skipped,
                    "duration_seconds": duration_seconds,
                },
                notable=notable,
            )
            mailer = digest.get_mailer(cfg, logger)
            sent = mailer.send(
                cfg.digest_to.split(","),
                subject=f"Earthquake digest — {len(notable)} notable event(s)",
                html_body=html_body,
            )
            event = "digest_sent" if sent else "digest_logged_only"
            logging_setup.log_event(logger, event, notable_count=len(notable))
        else:
            reason = "dry_run" if args.dry_run else "nothing_notable"
            logging_setup.log_event(logger, "digest_skipped", reason=reason)

        return 0

    except Exception as exc:  # top-level run boundary — must record failure, not crash silently
        logging_setup.log_event(logger, "run_failed", job=JOB_NAME, error=repr(exc))
        logger.exception("pipeline run failed")
        if not args.dry_run and engine is not None:
            try:
                db.fail_run(engine, JOB_NAME, timeutil.now_ms(), repr(exc))
            except Exception:
                logger.exception("also failed to record the failure in pipeline_watermark")
        return 1


if __name__ == "__main__":
    sys.exit(main())
