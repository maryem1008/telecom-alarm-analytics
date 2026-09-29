# Architecture

```
 ┌────────────────────────────┐
 │ common/constants.py, db.py  │  single source of truth: element list,
 │                              │  thresholds, weights, DB connection
 └───────────┬──────────────────┘
             │ imported by every stage below
             v
 ┌────────────────────────────┐
 │ generator/generate_alarms.py│  synthetic alarms + link quality data
 │ generator/stream_alarms.py  │  (optional) continuous live stream
 └───────────┬──────────────────┘
             │  COPY (CSV -> table)
             v
 ┌────────────────────────────┐
 │ PostgreSQL                  │  raw tables: alarms, link_quality
 └───────────┬──────────────────┘
             │  JDBC read
             v
 ┌────────────────────────────┐
 │ spark/analytics_job.py      │  batch aggregation:
 │                              │   - alarm counts by element/severity
 │                              │   - MTTR per element
 │                              │   - top 10 problematic elements
 │                              │   - at-risk towers (RX power / VSWR)
 │                              │   - anomaly detection (z-score vs. history)
 │                              │   - incident grouping (same element,
 │                              │     alarms <2 min apart merged into one)
 └───────────┬──────────────────┘
             │  JDBC write (truncate + insert, keeps schema.sql's PKs)
             v
 ┌────────────────────────────┐
 │ PostgreSQL                  │  aggregated tables: agg_alarm_counts,
 │                              │   agg_mttr, agg_at_risk_towers,
 │                              │   agg_anomalies, agg_incidents
 └───────────┬──────────────────┘
             │  SQL read (parameterized)
             v
 ┌────────────────────────────┐
 │ dashboard/app.py            │  Streamlit: live feed, map, charts,
 │ report/generate_report.py   │  PDF ops summary report
 └────────────────────────────┘
```

## Two ways to run it

**Native (no Docker)** — `run_local.py` applies the schema, generates and
loads data, and runs the Spark job once, talking to whatever Postgres is in
`.env` (installed locally, or a free hosted instance — see README). Only
needs Python + a local Java 17 for the Spark step; `spark/analytics_job.py`
auto-detects it (`find_java_home` / `configure_java_environment`) if it's
anywhere findable.

**Docker** — `docker-compose.yml` runs the same stages as containers:
`postgres` → `generator` → `spark-job` → `dashboard`. `spark-job` now waits
for `generator` to actually *finish* (`condition: service_completed_successfully`),
not just start, before reading from Postgres.

Either way, the code paths in `generator/`, `spark/`, `dashboard/`, and
`report/` are identical — only how you invoke them differs.

## Key design decisions

- **Batch, not streaming.** Kafka/streaming ingestion is a natural extension
  once this version works end-to-end, but batch (run on demand or on an
  interval, like a real OSS nightly report) is simpler to build and still
  demonstrates the same Spark/Postgres skills. `generator/stream_alarms.py`
  simulates a live feed for the parts of the dashboard (live feed, anomaly
  detection) that specifically need recent, continuous data — it's optional,
  not part of the base pipeline.
- **Severity-weighted risk score.** Rather than just counting alarms, elements
  are ranked using `Critical=5, Major=2, Minor=1` weights (in
  `common/constants.py`), closer to how a NOC would actually prioritize.
  Both the Spark job and the PDF report import this one definition.
- **MTTR as a real KPI.** Mean Time To Repair is the standard telecom metric
  for how quickly a network element's issues get resolved — computed from
  `event_time` to `clear_time` for alarms that have cleared.
- **Incident grouping.** A cascading failure fires many alarms in a short
  window; grouping alarms on the same element within a 2-minute gap into one
  "incident" (`compute_incidents`) reflects how an operator actually reads
  an alarm log, rather than a flat count.
- **At-risk thresholds are configurable** (`RX_POWER_THRESHOLD_DBM`,
  `VSWR_THRESHOLD` in `common/constants.py`) so they can be tuned to match
  real equipment specs.
- **Aggregate tables are truncated, not dropped.** `spark/analytics_job.py`
  writes with `option("truncate", "true")` rather than a plain
  `mode="overwrite"`, so the tables' PKs/indexes defined in `sql/schema.sql`
  survive every refresh instead of being silently discarded on the first run.
