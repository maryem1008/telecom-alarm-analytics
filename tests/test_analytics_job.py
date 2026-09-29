"""
Unit tests for the Spark transformations in spark/analytics_job.py.
Run with: pytest tests/
"""

import sys
import os
from datetime import datetime

import pytest
from pyspark.sql import SparkSession
from pyspark.sql.types import StructType, StructField, StringType, TimestampType

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "spark"))
from analytics_job import (
    compute_alarm_counts,
    compute_mttr,
    compute_top_offenders,
    compute_at_risk_towers,
    compute_incidents,
    configure_java_environment,
    find_java_home,
)


@pytest.fixture(scope="module")
def spark():
    configure_java_environment()
    spark = (
        SparkSession.builder
        .master("local[2]")
        .appName("test-telecom-analytics")
        .getOrCreate()
    )
    yield spark
    spark.stop()


@pytest.fixture
def sample_alarms(spark):
    data = [
        ("TUNIS_CBD5", "Critical", "Heartbeat",
         datetime(2026, 6, 1, 8, 0, 0), datetime(2026, 6, 1, 8, 30, 0)),
        ("TUNIS_CBD5", "Minor", "Enclosure",
         datetime(2026, 6, 1, 9, 0, 0), None),
        ("BEN_KHAL2", "Major", "VSWR Alarm",
         datetime(2026, 6, 1, 10, 0, 0), datetime(2026, 6, 1, 13, 0, 0)),
        ("BEN_KHAL2", "Critical", "Loss Of Signal",
         datetime(2026, 6, 1, 11, 0, 0), datetime(2026, 6, 1, 11, 45, 0)),
    ]
    cols = ["network_element", "severity", "specific_problem", "event_time", "clear_time"]
    return spark.createDataFrame(data, cols)


@pytest.fixture
def sample_links(spark):
    data = [
        ("BEN_KHAL2", -101.0, 2.4),   # weak signal + bad VSWR -> at risk
        ("BEN_KHAL2", -99.0, 2.1),
        ("TUNIS_CBD5", -80.0, 1.2),   # healthy
        ("TUNIS_CBD5", -82.0, 1.3),
    ]
    cols = ["network_element", "rx_power_dbm", "vswr"]
    return spark.createDataFrame(data, cols)


@pytest.fixture
def burst_alarms(spark):
    """Three alarms on the same element two minutes apart (one incident),
    then a fourth an hour later (a separate incident)."""
    data = [
        ("GABES_IND4", "Major", "Cell Out Of Service", datetime(2026, 6, 1, 10, 0, 0), None),
        ("GABES_IND4", "Critical", "Loss Of Signal", datetime(2026, 6, 1, 10, 1, 0), None),
        ("GABES_IND4", "Major", "Power Supply Failure", datetime(2026, 6, 1, 10, 2, 0), None),
        ("GABES_IND4", "Minor", "Battery Low", datetime(2026, 6, 1, 11, 5, 0), None),
    ]
    # Explicit schema: every clear_time is None here (all still open), which
    # leaves pandas/Spark's type inference nothing to go on for that column.
    schema = StructType([
        StructField("network_element", StringType()),
        StructField("severity", StringType()),
        StructField("specific_problem", StringType()),
        StructField("event_time", TimestampType()),
        StructField("clear_time", TimestampType()),
    ])
    return spark.createDataFrame(data, schema)


def test_find_java_home_uses_existing_environment(monkeypatch):
    monkeypatch.setenv("JAVA_HOME", r"C:\fake\java")
    assert find_java_home() == r"C:\fake\java"


def test_configure_java_environment_sets_java_home(monkeypatch, tmp_path):
    java_home = tmp_path / "java-home"
    java_bin = java_home / "bin"
    java_bin.mkdir(parents=True)
    (java_bin / "java.exe").write_text("", encoding="utf-8")

    monkeypatch.setenv("JAVA_HOME", "")
    monkeypatch.setenv("PATH", str(tmp_path))

    assert configure_java_environment() == str(java_home)
    assert os.environ["JAVA_HOME"] == str(java_home)


def test_alarm_counts_groups_by_element_and_severity(sample_alarms):
    result = compute_alarm_counts(sample_alarms, "2026-06-01").collect()
    counts = {(r.network_element, r.severity): r.alarm_count for r in result}
    assert counts[("TUNIS_CBD5", "Critical")] == 1
    assert counts[("TUNIS_CBD5", "Minor")] == 1
    assert counts[("BEN_KHAL2", "Major")] == 1
    assert counts[("BEN_KHAL2", "Critical")] == 1


def test_mttr_only_counts_resolved_alarms(sample_alarms):
    result = compute_mttr(sample_alarms, "2026-06-01").collect()
    by_element = {r.network_element: r for r in result}

    # TUNIS_CBD5 has one resolved alarm (30 min) and one still open -> only 1 counted
    assert by_element["TUNIS_CBD5"].resolved_alarms == 1
    assert by_element["TUNIS_CBD5"].avg_mttr_minutes == pytest.approx(30.0, abs=0.1)

    # BEN_KHAL2 has two resolved alarms: 180 min and 45 min -> avg 112.5
    assert by_element["BEN_KHAL2"].resolved_alarms == 2
    assert by_element["BEN_KHAL2"].avg_mttr_minutes == pytest.approx(112.5, abs=0.1)


def test_top_offenders_ranks_by_severity_weight(sample_alarms):
    result = compute_top_offenders(sample_alarms, top_n=10).collect()
    by_element = {r.network_element: r.risk_score for r in result}
    # BEN_KHAL2: Major(2) + Critical(5) = 7 ; TUNIS_CBD5: Critical(5) + Minor(1) = 6
    assert by_element["BEN_KHAL2"] == 7
    assert by_element["TUNIS_CBD5"] == 6
    assert by_element["BEN_KHAL2"] > by_element["TUNIS_CBD5"]


def test_at_risk_towers_flags_weak_signal_or_high_vswr(sample_links):
    result = compute_at_risk_towers(sample_links, "2026-06-01").collect()
    by_element = {r.network_element: r.risk_flag for r in result}
    assert by_element["BEN_KHAL2"] is True
    assert by_element["TUNIS_CBD5"] is False


def test_incidents_group_close_alarms_and_split_far_ones(burst_alarms):
    result = compute_incidents(burst_alarms, "2026-06-01", gap_minutes=2).collect()
    assert len(result) == 2

    by_size = sorted(result, key=lambda r: r.alarm_count)
    small, big = by_size[0], by_size[1]

    assert big.alarm_count == 3
    assert big.worst_severity == "Critical"  # highest weight among the 3
    assert small.alarm_count == 1
