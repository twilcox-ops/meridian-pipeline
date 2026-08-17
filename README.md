# Project 1 — Scheduled Cloud Ingestion Pipeline

A scaffold for the project described in
[`../PROJECT-1-scheduled-pipeline.md`](../PROJECT-1-scheduled-pipeline.md).
Pulls earthquake events from the USGS public API, upserts them into
Postgres/SQLite, and emails a digest of what's notable since the last run.
Personal project built for skill development against a public API — no
production deployment, no real users.

---

## Schema, and why the primary key is what it is

```
earthquakes
├── id                 TEXT PRIMARY KEY   -- USGS event id, e.g. "us7000abcd"
├── magnitude          FLOAT
├── mag_type           TEXT               -- "ml", "mww", etc.
├── place              TEXT
├── event_time_ms      BIGINT NOT NULL    -- origin time, UTC epoch ms
├── updated_time_ms    BIGINT NOT NULL    -- USGS revision time, UTC epoch ms (indexed)
├── longitude / latitude / depth_km   FLOAT
├── event_type         TEXT               -- "earthquake", "quarry blast", ...
├── status             TEXT               -- "automatic" | "reviewed"
├── tsunami            BOOLEAN
├── alert_level        TEXT               -- PAGER alert color, or NULL
├── felt_reports       INTEGER
├── url                TEXT
├── raw_json           TEXT               -- full GeoJSON feature, for future reprocessing
├── first_seen_at_ms   BIGINT NOT NULL    -- when this pipeline first wrote the row
└── last_seen_at_ms    BIGINT NOT NULL    -- when this pipeline last touched the row

pipeline_watermark
├── job_name                    TEXT PRIMARY KEY
├── last_updated_watermark_ms   BIGINT    -- incremental cursor (see below)
├── last_run_started_at_ms      BIGINT
├── last_run_completed_at_ms    BIGINT
├── last_run_status             TEXT      -- "running" | "success" | "failed"
└── last_run_error              TEXT
```

**Why the primary key is USGS's own event id, not an autoincrement integer:**
USGS guarantees that id is stable and unique for the life of the event. That
guarantee is what makes reruns safe — the same event fetched twice (a retry,
an overlapping backfill, a crash-and-restart) always maps to the same row,
so there is no way to produce a duplicate. An autoincrement key would make
every fetch a blind insert and push deduplication into application logic
that's easy to get subtly wrong; the natural key pushes it into the
database, where `ON CONFLICT DO UPDATE` handles it in one line.

**Why the watermark tracks `updated_time_ms`, not `event_time_ms`:** USGS
revises magnitude and location on existing events for hours to days after
they occur, as more seismograph data comes in. A watermark on origin time
would fetch each event once and never see later corrections. Filtering the
USGS API on `updatedafter` instead — and watermarking on the same field —
catches new events and revisions to old ones in the same pass. This is also
why a "last 24 hours" window is wrong for a resumed job: it has no memory of
revisions that happened during a gap, whereas `updatedafter=<watermark>`
picks up exactly where the last successful run left off, however long the
gap was.

All timestamps are stored as UTC epoch milliseconds (matching USGS's own
representation) rather than a `DATETIME`/`TIMESTAMP` column, specifically to
avoid timezone round-trip inconsistencies between SQLite (no native
timezone-aware type) and Postgres. See `src/pipeline/timeutil.py`.

---

## Why a rerun and a crash are both safe

- **Idempotent upsert.** `upsert_earthquakes()` compares each incoming
  record's `updated_time_ms` against what's stored. Identical → skipped.
  Different → `INSERT ... ON CONFLICT (id) DO UPDATE`. Fetching the same
  window five times in a row inserts once and skips four times; row count
  never moves. Proved in `tests/test_idempotency.py`.
- **Crash consistency.** Each call to `upsert_earthquakes()` runs as one
  database transaction. If the process is killed mid-batch, nothing in that
  call has committed — the database is exactly as it was before the run
  started. The watermark only advances in a *separate*, later call
  (`complete_run`), so a crash between "data committed" and "watermark
  advanced" just means the next run refetches the same window and reapplies
  it, which is safe for the same reason a plain rerun is safe.
- **Concurrent runs.** Two overlapping runs both upserting the same window
  is harmless for the same reason a rerun is harmless — the natural key and
  the `updated_time_ms` comparison make the operation commutative. This
  scaffold doesn't add a distributed lock on top, because the upsert already
  makes double-execution a no-op rather than a correctness problem; the
  trade-off worth knowing is that two truly simultaneous runs will each pay
  the API/DB cost even though only one changes anything.

---

## Running it locally

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev,postgres]"
copy .env.example .env      # defaults to sqlite:///./local.db — nothing else required

pytest                       # runs the whole suite against an in-memory SQLite db
python -m pipeline.run --dry-run              # preview against the live USGS API, writes nothing
python -m pipeline.run                        # real run: fetch, upsert, maybe send a digest
python -m pipeline.run --backfill-start 2026-08-01T00:00:00 --backfill-end 2026-08-08T00:00:00
```

`MAILER=none` (the `.env.example` default) logs the digest instead of
sending it, so the pipeline runs end-to-end with zero external
configuration beyond a database. Set `MAILER=graph` plus the `GRAPH_*`
variables to actually send mail — see `src/pipeline/digest.py`.

The scheduled workflow in `.github/workflows/earthquake-pipeline.yml` also
currently runs with `MAILER=none`, so the "digest email" acceptance
criterion isn't actually being exercised by the live schedule yet — that
needs the `GRAPH_*` secrets added and `MAILER` flipped to `graph` in the
workflow before it's proven end-to-end.

---

## Layout

```
src/pipeline/
├── config.py          # env vars in, dataclass out
├── timeutil.py         # epoch-ms helpers; ISO only at the API boundary
├── fetch.py             # USGS client: retry+backoff, pagination, updatedafter vs. backfill window
├── db.py                 # schema, idempotent upsert, watermark
├── digest.py              # notable-event selection, HTML digest, Graph mailer
├── logging_setup.py        # JSON-lines structured logging
└── run.py                    # CLI: scheduled / --dry-run / --backfill-*
tests/                          # idempotency, upsert, watermark — see test docstrings
deploy/                          # Dockerfile, Container Apps Jobs bicep, GitHub Actions template
.github/workflows/               # earthquake-pipeline.yml — the active scheduled workflow (copied
                                  # from deploy/github-actions-schedule.yml; currently runs with
                                  # MAILER=none until Graph credentials are added, see below)
```

---

## Acceptance criteria

Tracking against `../PROJECT-1-scheduled-pipeline.md`. The scaffold gets you
to the point where all of these are true *once deployed and run*; none of
them are true just by cloning this repo — they require an actual multi-day
run against a real schedule.

- [ ] Ran on schedule, unattended, for at least seven consecutive days — schedule is live as of 2026-08-17; clock has started, not yet at seven days
- [x] Re-running any single window is a no-op on row counts — `tests/test_idempotency.py`
- [x] Killing the job halfway leaves the database consistent — see "Why a rerun and a crash are both safe" above
- [x] `git log -p | grep -i "key\|secret\|password"` returns nothing meaningful — verified: only matches are variable names/refs (`secrets.DATABASE_URL`, empty `GRAPH_CLIENT_SECRET=` placeholder in `.env.example`) and prose mentioning Key Vault, no actual credential values
- [x] Alert on the job failing to run at all, not just erroring — healthchecks.io dead-man's-switch is wired up and active; the workflow pings it on success, healthchecks.io alerts if a ping is missed
- [x] This README explains the schema and the natural-key choice

Fill in the résumé bullet in `../PROJECT-1-scheduled-pipeline.md` with real
numbers once it's actually been run for a while — specific numbers are what
make the bullet invite a good conversation instead of getting skimmed.

---

## What I'd do differently

**The digest-logging bug, and what it says about testing mailers.** An
earlier version unconditionally logged `digest_sent` after calling
`mailer.send()`, regardless of which mailer handled it — so a run with
`MAILER=none` would claim a digest was "sent" when nothing actually left
the building. Both `NullMailer.send()` and `GraphMailer.send()` now return
a `bool` for whether mail actually went out, and `run.py` picks the log
event (`digest_sent` vs. `digest_logged_only`) from that return value
instead of assuming success. The lesson generalizes: a "safe default" mailer
that silently no-ops is exactly the kind of code path that needs its own
explicit test, because it fails by *looking* like it worked, not by
crashing.

**`MAILER` has never been proven end-to-end.** Every real run so far —
local dev and the live scheduled workflow — has used `MAILER=none`. The
Graph code path (OAuth2 client-credentials flow, `sendMail` call) has never
actually sent an email against a real tenant. It's the one acceptance
criterion in the parent spec ("digest email... sent") that's still purely
theoretical; the code is written and the digest-selection logic is
unit-testable, but nothing has verified it against the real Graph API.

**SQLite is fine for development, but production should be Postgres from
day one.** The upsert logic is dialect-agnostic and the test suite exercises
both, but SQLite's single-writer locking model doesn't hold up well against
a scheduled job that might overlap with a manual backfill or a second
concurrent trigger — the kind of scenario the "why a rerun and a crash are
both safe" section above assumes is merely wasteful, not actively blocking.
If this ran unattended in the cloud, `DATABASE_URL` should point at
Postgres before the first scheduled run, not after the first lock-contention
incident.

**The GitHub Actions `working-directory` mismatch.** The original workflow
template set `defaults.run.working-directory: project-1-scheduled-pipeline`,
written on the assumption that the repo root would be a parent
`meridian-portfolio` monorepo with each project in its own subdirectory.
Since this project is actually its own standalone repo, the repo root
already *is* the project root — that line pointed at a directory that
didn't exist, and every step after checkout would have failed. It never
ran, because the mismatch was caught before the workflow's first trigger,
but it's a reminder that a deploy template copied from a "what if we
restructure later" assumption needs to be checked against the actual repo
layout, not just against how the docs describe it.

**The initial commit is a single large snapshot, not a history.**
`RESUME-AND-INTERVIEW.md` (in the parent portfolio repo) is explicit that
incremental commits with real messages tell a better story than one big
"initial commit" — and this repo's first commit is exactly that pattern:
the whole pipeline, tests, and docs landed in one commit. Everything since
(the workflow, the heartbeat wiring, these README updates) has been
incremental, which is the right shape going forward, but it doesn't undo
how the repo's history opens for anyone who reads it top to bottom.
