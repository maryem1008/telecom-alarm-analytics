"""
One-command pipeline runner for machines where Docker isn't an option
(no virtualization support, restricted install permissions, etc).

Does everything `docker-compose up` was supposed to do, natively:
  1. Applies sql/schema.sql to Postgres
  2. Generates synthetic data and loads it into Postgres
  3. Runs the Spark job once to populate the aggregate tables
  4. Tells you the one command left to run: the dashboard

Needs, before running this:
  - Python deps:  pip install -r requirements.txt
  - A Postgres to point at — either installed locally, or a free hosted
    one (Neon, Supabase, ElephantSQL...). No Docker either way. See
    README.md > "Quick start (no Docker)" for both options.
  - A local Java 17 install, only for the Spark step. This auto-detects
    it (see spark/analytics_job.py's find_java_home) if it's anywhere
    findable; set JAVA_HOME yourself if it isn't.
  - A .env file with your Postgres connection details (copy .env.example).

Usage:
    python run_local.py                 # generate + load + run Spark once
    python run_local.py --skip-spark    # skip the Spark step (needs Java)
    python run_local.py --alarms 5000 --links 10000
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from common.db import connect, get_pg_config


def apply_schema():
    schema_path = os.path.join(os.path.dirname(__file__), "sql", "schema.sql")
    with open(schema_path) as f:
        schema_sql = f.read()

    print(f"[1/3] Applying schema to {get_pg_config()['host']}:{get_pg_config()['port']}/{get_pg_config()['dbname']}...")
    conn = connect()
    cur = conn.cursor()
    cur.execute(schema_sql)
    conn.commit()
    cur.close()
    conn.close()
    print("      Schema ready.")


def generate_and_load(n_alarms, n_links):
    print(f"[2/3] Generating {n_alarms} alarms + {n_links} link readings, loading into Postgres...")
    from generator.generate_alarms import generate_alarms, generate_link_quality, load_to_postgres
    generate_alarms(n_alarms)
    generate_link_quality(n_links)
    load_to_postgres()


def run_spark_once():
    print("[3/3] Running the Spark job once to populate aggregate tables...")
    from spark.analytics_job import build_spark_session, run_once
    spark = build_spark_session()
    try:
        run_once(spark)
    finally:
        spark.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--alarms", type=int, default=2000)
    parser.add_argument("--links", type=int, default=5000)
    parser.add_argument("--skip-spark", action="store_true", help="Skip the Spark aggregation step (dashboard will show raw data only).")
    args = parser.parse_args()

    apply_schema()
    generate_and_load(args.alarms, args.links)
    if not args.skip_spark:
        run_spark_once()
    else:
        print("[3/3] Skipped Spark step (--skip-spark).")

    print(
        "\nDone. Now run:\n"
        "    streamlit run dashboard/app.py\n\n"
        "Optional, for the live feed / anomaly detection panels to show anything:\n"
        "    python generator/stream_alarms.py\n"
    )


if __name__ == "__main__":
    main()
