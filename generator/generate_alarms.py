"""
Synthetic data generator for the Telecom Network Alarm & Performance
Analytics Pipeline.

Produces two datasets, modeled on a real Ericsson OSS Alarm Monitor:
  - alarms.csv         : network alarms (severity, specific problem, times)
  - link_quality.csv   : RRU/radio link readings (TX/RX power, VSWR, UEs)

...and, by default, loads both straight into Postgres. Previously this
script only wrote the CSVs — nothing ever called load_to_postgres(), so
the rest of the pipeline (Spark job, dashboard) always ran against empty
tables. That's fixed: running this script with no arguments now leaves
Postgres populated and ready.

Usage:
    python generator/generate_alarms.py                 # generate + load (default)
    python generator/generate_alarms.py --no-load        # write CSVs only
    python generator/generate_alarms.py --alarms 5000 --links 10000
"""

import argparse
import os
import random
import csv
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from common.constants import (
    NETWORK_ELEMENTS, PROBLEM_ELEMENTS, SEVERITIES, SEVERITY_WEIGHTS_NORMAL,
    SEVERITY_WEIGHTS_PROBLEM, SPECIFIC_PROBLEMS, SECTORS,
)
from common.db import connect

random.seed(42)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
os.makedirs(DATA_DIR, exist_ok=True)


def _random_time_in_last_n_days(n=30):
    now = datetime.now()
    delta = timedelta(
        days=random.randint(0, n - 1),
        hours=random.randint(0, 23),
        minutes=random.randint(0, 59),
    )
    return now - delta


def generate_alarms(n=2000):
    rows = []
    for _ in range(n):
        element = random.choice(NETWORK_ELEMENTS)
        is_problem_elem = element in PROBLEM_ELEMENTS

        # Problem elements skew toward higher severity and are less likely to be
        # resolved quickly.
        if is_problem_elem:
            severity = random.choices(SEVERITIES, weights=SEVERITY_WEIGHTS_PROBLEM)[0]
        else:
            severity = random.choices(SEVERITIES, weights=SEVERITY_WEIGHTS_NORMAL)[0]

        event_time = _random_time_in_last_n_days(30)
        insert_time = event_time + timedelta(seconds=random.randint(1, 30))

        # Resolution time: problem elements take longer, Critical alarms hopefully
        # get fixed faster than Minor ones (more attention), but not always.
        if random.random() < 0.85:  # 85% of alarms eventually clear
            base_minutes = {"Critical": 45, "Major": 120, "Minor": 240}[severity]
            if is_problem_elem:
                base_minutes *= 1.8
            resolve_minutes = max(5, int(random.gauss(base_minutes, base_minutes * 0.4)))
            clear_time = event_time + timedelta(minutes=resolve_minutes)
        else:
            clear_time = None

        rows.append({
            "network_element": element,
            "alarming_object": f"MeContext={element},Cabinet={random.randint(1, 3)}",
            "severity": severity,
            "specific_problem": random.choice(SPECIFIC_PROBLEMS),
            "event_time": event_time.isoformat(sep=" "),
            "insert_time": insert_time.isoformat(sep=" "),
            "clear_time": clear_time.isoformat(sep=" ") if clear_time else "",
        })

    path = os.path.join(DATA_DIR, "alarms.csv")
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} alarm rows to {path}")
    return path


def generate_link_quality(n=5000):
    rows = []
    for _ in range(n):
        element = random.choice(NETWORK_ELEMENTS)
        is_problem_elem = element in PROBLEM_ELEMENTS

        tx_power = round(random.gauss(43, 1.5), 2)  # dBm, typical macro site
        if is_problem_elem:
            rx_power = round(random.gauss(-100, 6), 2)   # weaker signal
            vswr = round(max(1.0, random.gauss(2.3, 0.6)), 2)  # worse VSWR
        else:
            rx_power = round(random.gauss(-85, 4), 2)
            vswr = round(max(1.0, random.gauss(1.3, 0.2)), 2)

        rows.append({
            "network_element": element,
            "sector": random.choice(SECTORS),
            "tx_power_dbm": tx_power,
            "rx_power_dbm": rx_power,
            "vswr": vswr,
            "connected_ues": random.randint(0, 180),
            "reading_time": _random_time_in_last_n_days(30).isoformat(sep=" "),
        })

    path = os.path.join(DATA_DIR, "link_quality.csv")
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} link quality rows to {path}")
    return path


def load_to_postgres():
    """Loads the generated CSVs into Postgres (truncating first, so re-runs
    don't accumulate duplicate rows)."""
    conn = connect()
    cur = conn.cursor()
    cur.execute("TRUNCATE TABLE alarms, link_quality RESTART IDENTITY;")

    with open(os.path.join(DATA_DIR, "alarms.csv")) as f:
        next(f)  # skip header
        cur.copy_expert(
            """COPY alarms (network_element, alarming_object, severity,
                             specific_problem, event_time, insert_time, clear_time)
               FROM STDIN WITH (FORMAT csv, NULL '')""",
            f,
        )

    with open(os.path.join(DATA_DIR, "link_quality.csv")) as f:
        next(f)
        cur.copy_expert(
            """COPY link_quality (network_element, sector, tx_power_dbm,
                                   rx_power_dbm, vswr, connected_ues, reading_time)
               FROM STDIN WITH (FORMAT csv, NULL '')""",
            f,
        )

    conn.commit()
    cur.close()
    conn.close()
    print("Loaded alarms.csv and link_quality.csv into Postgres.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--alarms", type=int, default=2000, help="Number of alarm rows to generate.")
    parser.add_argument("--links", type=int, default=5000, help="Number of link-quality rows to generate.")
    parser.add_argument("--no-load", action="store_true", help="Only write CSVs; skip loading into Postgres.")
    args = parser.parse_args()

    generate_alarms(args.alarms)
    generate_link_quality(args.links)
    if not args.no_load:
        load_to_postgres()
    else:
        print("Skipped loading into Postgres (--no-load). Run with load_to_postgres() when ready.")


if __name__ == "__main__":
    main()
