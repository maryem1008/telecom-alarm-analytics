"""
Spark batch job for the Telecom Network Alarm & Performance Analytics Pipeline.

Reads `alarms` and `link_quality` from Postgres, computes:
  1. Alarm counts per network element / severity
  2. MTTR (Mean Time To Repair) per network element
  3. Top 10 problematic network elements (by weighted alarm score)
  4. "At-risk towers" flag from RX power / VSWR thresholds
  5. Anomaly detection — network elements with unusually high recent alarm rates
  6. Incident grouping — alarms on the same element close together in time,
     merged into a single incident so a cascading failure reads as one event

Writes the results back to Postgres (agg_alarm_counts, agg_mttr,
agg_at_risk_towers, agg_anomalies, agg_incidents).

Run:
    python spark/analytics_job.py            # loop every STREAM_INTERVAL_SECONDS
    python spark/analytics_job.py --once      # single pass, then exit (good for run_local.py)

No Docker required: if JAVA_HOME isn't already set, this auto-detects a
local JDK (see find_java_home / configure_java_environment below) so
`pip install -r requirements.txt` + a local Java install is enough.
"""

import argparse
import glob
import os
import shutil
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from common.constants import (
    SEVERITY_WEIGHT, RX_POWER_THRESHOLD_DBM, VSWR_THRESHOLD,
    INCIDENT_GAP_MINUTES, ANOMALY_Z_THRESHOLD, ANOMALY_BUCKET_MINUTES,
    ANOMALY_LOOKBACK_HOURS,
)
from common.db import jdbc_url_and_props

STREAM_INTERVAL_SECONDS = int(os.environ.get("STREAM_INTERVAL_SECONDS", 30))

# Common install locations to search when JAVA_HOME isn't set — one level
# deeper than each dir (glob "<dir>/*/bin/java*") so both "a folder full of
# JDK installs" and "a folder that IS a JDK install" style PATHs resolve.
_JAVA_SEARCH_ROOTS = [
    "/usr/lib/jvm",
    "/usr/lib64/jvm",
    "/opt",
    "/Library/Java/JavaVirtualMachines/*/Contents/Home",
    r"C:\Program Files\Java",
    r"C:\Program Files\Eclipse Adoptium",
    r"C:\Program Files\Microsoft",
]


def find_java_home():
    """Best-effort JDK discovery, so PySpark works without manual setup.

    Order: an already-set JAVA_HOME env var, then `java` on PATH, then a
    one-level-deep scan of PATH directories, then common per-OS install
    locations. Returns None if nothing is found.
    """
    env_home = os.environ.get("JAVA_HOME")
    if env_home:
        return env_home

    which_java = shutil.which("java")
    if which_java:
        # .../some-jdk/bin/java(.exe) -> .../some-jdk
        candidate = Path(which_java).resolve().parent.parent
        if candidate.exists():
            return str(candidate)

    def _scan(root_pattern):
        for bin_java in glob.glob(os.path.join(root_pattern, "*", "bin", "java")) + \
                         glob.glob(os.path.join(root_pattern, "*", "bin", "java.exe")):
            return str(Path(bin_java).parent.parent)
        return None

    for path_entry in os.environ.get("PATH", "").split(os.pathsep):
        if path_entry:
            found = _scan(path_entry)
            if found:
                return found

    for root in _JAVA_SEARCH_ROOTS:
        found = _scan(root)
        if found:
            return found

    return None


def configure_java_environment():
    """Sets JAVA_HOME (and prepends its bin/ to PATH) if not already
    configured. Returns the JAVA_HOME in effect, or raises a clear error
    telling the user what to install."""
    java_home = os.environ.get("JAVA_HOME") or find_java_home()
    if not java_home:
        raise EnvironmentError(
            "No Java installation found. PySpark needs a local JDK (17 recommended). "
            "Install one (e.g. https://adoptium.net) and either let this auto-detect it "
            "or set JAVA_HOME yourself, then re-run."
        )
    os.environ["JAVA_HOME"] = java_home
    java_bin = os.path.join(java_home, "bin")
    if java_bin not in os.environ.get("PATH", ""):
        os.environ["PATH"] = java_bin + os.pathsep + os.environ.get("PATH", "")
    return java_home


def read_table(spark, table_name):
    url, props = jdbc_url_and_props()
    return spark.read.jdbc(url=url, table=table_name, properties=props)


def write_table(df, table_name):
    """Writes aggregate results back to a table already created by
    sql/schema.sql, truncating its rows first rather than dropping and
    recreating it.

    Plain `mode="overwrite"` makes Spark DROP the table and infer a fresh
    one from the DataFrame's schema — which silently discards the PRIMARY
    KEY / indexes defined in schema.sql on every single run.
    `option("truncate", "true")` clears the rows but keeps the table (and
    its constraints) intact, as long as the DataFrame's schema still
    matches the existing table — which it does here since schema.sql was
    written to match these jobs' output columns.
    """
    url, props = jdbc_url_and_props()
    (
        df.write
        .option("truncate", "true")
        .jdbc(url=url, table=table_name, mode="overwrite", properties=props)
    )


def compute_alarm_counts(alarms_df, run_date):
    from pyspark.sql import functions as F
    counts = (
        alarms_df.groupBy("network_element", "severity")
        .agg(F.count("*").alias("alarm_count"))
        .withColumn("run_date", F.to_date(F.lit(run_date)))
    )
    return counts


def compute_mttr(alarms_df, run_date):
    from pyspark.sql import functions as F
    resolved = alarms_df.filter(F.col("clear_time").isNotNull())
    mttr = (
        resolved
        .withColumn(
            "resolution_minutes",
            (F.col("clear_time").cast("long") - F.col("event_time").cast("long")) / 60.0,
        )
        .groupBy("network_element")
        .agg(
            F.round(F.avg("resolution_minutes"), 2).alias("avg_mttr_minutes"),
            F.count("*").alias("resolved_alarms"),
        )
        .withColumn("run_date", F.to_date(F.lit(run_date)))
    )
    return mttr


def _severity_weight_column():
    from pyspark.sql import functions as F
    weight_map = F.create_map(
        *[x for pair in SEVERITY_WEIGHT.items() for x in (F.lit(pair[0]), F.lit(pair[1]))]
    )
    return weight_map[F.col("severity")]


def compute_top_offenders(alarms_df, top_n=10):
    from pyspark.sql import functions as F
    scored = alarms_df.withColumn("weight", _severity_weight_column())
    ranked = (
        scored.groupBy("network_element")
        .agg(F.sum("weight").alias("risk_score"), F.count("*").alias("total_alarms"))
        .orderBy(F.desc("risk_score"))
        .limit(top_n)
    )
    return ranked


def compute_at_risk_towers(link_df, run_date):
    from pyspark.sql import functions as F
    agg = (
        link_df.groupBy("network_element")
        .agg(
            F.round(F.avg("rx_power_dbm"), 2).alias("avg_rx_power_dbm"),
            F.round(F.avg("vswr"), 2).alias("avg_vswr"),
        )
        .withColumn(
            "risk_flag",
            (F.col("avg_rx_power_dbm") < RX_POWER_THRESHOLD_DBM)
            | (F.col("avg_vswr") > VSWR_THRESHOLD),
        )
        .withColumn("run_date", F.to_date(F.lit(run_date)))
    )
    return agg


def compute_anomalies(
    alarms_df, run_date,
    z_threshold=ANOMALY_Z_THRESHOLD,
    bucket_minutes=ANOMALY_BUCKET_MINUTES,
    lookback_hours=ANOMALY_LOOKBACK_HOURS,
):
    """
    Buckets alarms into fixed time windows per network element, then flags
    the most recent bucket as anomalous if its count is more than
    `z_threshold` standard deviations above that element's own historical
    average — "this element is behaving very differently than usual", not
    just "this element crossed some fixed number".

    Needs a continuous stream of recent alarms to find anything — see
    generator/stream_alarms.py. A one-off historical CSV load, generated
    once and never added to, will never have alarms inside the lookback
    window, so this will come back empty (by design, not a bug) until
    stream_alarms.py is also running.
    """
    from pyspark.sql import functions as F
    cutoff = F.expr(f"current_timestamp() - INTERVAL {lookback_hours} HOURS")
    recent = alarms_df.filter(F.col("event_time") >= cutoff)

    bucketed = recent.withColumn(
        "bucket",
        F.floor(F.unix_timestamp("event_time") / (bucket_minutes * 60)) * (bucket_minutes * 60),
    )

    counts = (
        bucketed.groupBy("network_element", "bucket")
        .agg(F.count("*").alias("alarm_count"))
    )

    stats = (
        counts.groupBy("network_element")
        .agg(
            F.avg("alarm_count").alias("avg_count"),
            F.stddev("alarm_count").alias("stddev_count"),
            F.max("bucket").alias("latest_bucket"),
        )
    )

    latest = counts.join(
        stats, on="network_element"
    ).filter(F.col("bucket") == F.col("latest_bucket"))

    result = (
        latest
        .withColumn(
            "stddev_count",
            F.when(F.col("stddev_count").isNull() | (F.col("stddev_count") == 0), F.lit(1.0))
             .otherwise(F.col("stddev_count")),
        )
        .withColumn(
            "z_score",
            F.round((F.col("alarm_count") - F.col("avg_count")) / F.col("stddev_count"), 2),
        )
        .withColumn("is_anomaly", F.col("z_score") > z_threshold)
        .select(
            F.col("network_element"),
            F.col("alarm_count").alias("latest_count"),
            F.round(F.col("avg_count"), 2).alias("avg_count"),
            F.round(F.col("stddev_count"), 2).alias("stddev_count"),
            F.col("z_score"),
            F.col("is_anomaly"),
        )
        .withColumn("run_date", F.to_date(F.lit(run_date)))
    )
    return result


def compute_incidents(alarms_df, run_date, gap_minutes=INCIDENT_GAP_MINUTES):
    """
    Groups alarms on the same network element into "incidents": a run of
    alarms where consecutive events are no more than `gap_minutes` apart.
    A cascading failure that fires 30 alarms in two minutes becomes one row
    here instead of 30 — closer to how a NOC actually reads an alarm log.
    """
    from pyspark.sql import Window, functions as F
    by_time = Window.partitionBy("network_element").orderBy("event_time")

    with_gap = (
        alarms_df
        .withColumn("prev_event_time", F.lag("event_time").over(by_time))
        .withColumn(
            "gap_minutes",
            (F.col("event_time").cast("long") - F.col("prev_event_time").cast("long")) / 60.0,
        )
        .withColumn(
            "starts_new_incident",
            F.when(
                F.col("prev_event_time").isNull() | (F.col("gap_minutes") > gap_minutes), 1
            ).otherwise(0),
        )
        .withColumn("incident_group", F.sum("starts_new_incident").over(by_time))
        .withColumn("weight", _severity_weight_column())
    )

    incidents = (
        with_gap.groupBy("network_element", "incident_group")
        .agg(
            F.min("event_time").alias("start_time"),
            F.max("event_time").alias("end_time"),
            F.count("*").alias("alarm_count"),
            F.expr("max_by(severity, weight)").alias("worst_severity"),
        )
        .withColumn(
            "duration_minutes",
            F.round(
                (F.col("end_time").cast("long") - F.col("start_time").cast("long")) / 60.0, 2
            ),
        )
        .withColumn("run_date", F.to_date(F.lit(run_date)))
    )
    return incidents


def run_once(spark):
    from pyspark.sql import functions as F
    run_date = date.today().isoformat()

    alarms_df = read_table(spark, "alarms").cache()
    link_df = read_table(spark, "link_quality")

    alarm_counts = compute_alarm_counts(alarms_df, run_date)
    mttr = compute_mttr(alarms_df, run_date)
    top_offenders = compute_top_offenders(alarms_df)
    at_risk = compute_at_risk_towers(link_df, run_date)
    anomalies = compute_anomalies(alarms_df, run_date)
    incidents = compute_incidents(alarms_df, run_date)

    print(f"\n=== Refresh at {run_date} ===")
    print("Top 5 problematic elements:")
    top_offenders.show(5, truncate=False)
    print("At-risk towers:")
    at_risk.filter(F.col("risk_flag")).show(10, truncate=False)
    print("Anomalies detected:")
    anomalies.filter(F.col("is_anomaly")).show(10, truncate=False)
    print("Recent multi-alarm incidents:")
    incidents.filter(F.col("alarm_count") > 1).orderBy(F.desc("start_time")).show(10, truncate=False)

    write_table(alarm_counts, "agg_alarm_counts")
    write_table(mttr, "agg_mttr")
    write_table(at_risk, "agg_at_risk_towers")
    write_table(anomalies, "agg_anomalies")
    write_table(incidents, "agg_incidents")
    alarms_df.unpersist()


def build_spark_session():
    configure_java_environment()
    from pyspark.sql import SparkSession
    spark = (
        SparkSession.builder
        .appName("TelecomAlarmAnalytics")
        # Resolves the Postgres JDBC driver from Maven Central on first run
        # and caches it locally afterwards — no Docker image / baked-in jar
        # needed for a native run.
        .config("spark.jars.packages", "org.postgresql:postgresql:42.7.3")
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("WARN")
    return spark


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Run a single pass and exit, instead of looping.")
    parser.add_argument("--interval", type=int, default=STREAM_INTERVAL_SECONDS, help="Seconds between refreshes when looping.")
    args = parser.parse_args()

    spark = build_spark_session()
    try:
        if args.once:
            run_once(spark)
        else:
            print(f"Starting refresh loop (every {args.interval}s). Ctrl+C to stop.")
            while True:
                run_once(spark)
                time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        spark.stop()


if __name__ == "__main__":
    main()