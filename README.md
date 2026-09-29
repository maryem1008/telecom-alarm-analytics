# Telecom Network Alarm & Performance Analytics

A small data engineering pipeline simulating telecom network alarms and
radio-link performance data, modeled on an Ericsson OSS Alarm Monitor:
synthetic data → Postgres → a PySpark batch job (MTTR, top offenders,
at-risk towers, anomaly detection, incident grouping) → a Streamlit
dashboard, plus a one-click PDF ops report.

Runs two ways — **no Docker required** for either:

- **Native** (recommended if Docker isn't usable on your machine): plain
  Python + a Postgres to point at.
- **Docker**: `docker-compose up --build`, if you do have Docker.

---

## Quick start (no Docker)

### 1. Get a Postgres to point at

Two options, pick one — nothing here needs Docker:

**A — free hosted Postgres, zero local install (easiest).**
Create a free project at [neon.tech](https://neon.tech) or
[supabase.com](https://supabase.com) (either has a generous free tier and
takes about two minutes). Copy the connection details it gives you.

**B — Postgres installed natively on your machine.**
Download the plain installer (not Docker) for your OS from
[postgresql.org/download](https://www.postgresql.org/download/) and create
a `telecom` database/user, or just use the defaults below.

### 2. Configure the connection

```bash
cp .env.example .env
# then edit .env with the details from step 1
```

### 3. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 4. Install Java (only needed for the Spark step)

The Spark aggregation step needs a local JDK — this is much lighter than
Docker Desktop (no virtualization required, ~200 MB) and works on any
Windows/macOS/Linux machine. Install
[Temurin 17](https://adoptium.net/temurin/releases/?version=17) (or any
JDK 11/17). `run_local.py` auto-detects it; if detection fails, set
`JAVA_HOME` yourself.

If you'd rather skip Spark entirely for now, `run_local.py --skip-spark`
generates and loads data without it — the dashboard's raw panels (live
feed, map) will work; the aggregate charts (MTTR, top offenders, at-risk,
anomalies, incidents) will stay empty until you do run it.

### 5. Run the pipeline

```bash
python run_local.py
```

This applies the schema, generates synthetic data, loads it into Postgres,
and runs the Spark job once. Re-run it any time to refresh the data.

### 6. Launch the dashboard

```bash
streamlit run dashboard/app.py
```

Opens at `http://localhost:8501`.

### Optional: a live feed

Two dashboard panels — the live alarm feed and anomaly detection — are
built around a continuously updating stream, not a one-off historical
load. To see them do something, run this in a second terminal (it inserts
a new alarm every 1–4 seconds and re-`load_to_postgres()`'s nothing, so
it's safe to leave running alongside step 6):

```bash
python generator/stream_alarms.py
```

And, since it's Spark reading from the same tables on a timer, keep the
aggregation refreshing too:

```bash
python spark/analytics_job.py
```

(no `--once` this time — it loops every 30s by default).

---

## Quick start (Docker, if available)

```bash
docker-compose up --build
```

Starts Postgres, generates and loads data, runs the Spark job, and serves
the dashboard at `http://localhost:8501` — same code as the native path,
just containerized.

---

## Project layout

```
common/            shared constants + DB connection helper (single source of truth)
generator/         synthetic data generator + optional live stream simulator
spark/             PySpark batch job (5 analyses, incl. incident grouping)
dashboard/         Streamlit app (white/neutral theme, see .streamlit/config.toml)
report/            one-click PDF ops summary report
sql/schema.sql     raw + aggregate table definitions
tests/             pytest suite for the Spark transformations
run_local.py        one-command native pipeline runner (the no-Docker path)
docker-compose.yml  the Docker path
docs/architecture.md  diagram + design decisions
```

See `docs/architecture.md` for the full pipeline diagram and the reasoning
behind the main design choices (severity weighting, incident grouping,
truncate-vs-overwrite, etc).

## Running the tests

```bash
pip install -r requirements.txt   # includes pytest
pytest tests/ -v
```

Needs the same local Java as the Spark step (see step 4 above).
