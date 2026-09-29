"""
Continuous alarm & link-quality stream simulator.

Runs forever, inserting a new alarm or link reading every 1-4 seconds —
simulating a real telecom network where alarms never stop coming in. It also
periodically "resolves" some open alarms, so MTTR has something to compute,
and periodically triggers a short "burst" of alarms on one random element,
so anomaly detection has something real to detect.

The Spark job's anomaly detector only looks at the last few hours of
`event_time`. A one-off historical CSV load (generate_alarms.py) will never
satisfy that window on its own — run this alongside it if you want the
Anomalies and live-feed panels on the dashboard to show anything.

Usage:
    python generator/stream_alarms.py
    (Ctrl+C to stop)
"""

import os
import random
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from common.constants import (
    NETWORK_ELEMENTS, PROBLEM_ELEMENTS, SEVERITIES, SEVERITY_WEIGHTS_NORMAL,
    SEVERITY_WEIGHTS_PROBLEM, SPECIFIC_PROBLEMS, SECTORS,
)
from common.db import connect

random.seed()

# --- Burst mode settings ---
BURST_MIN_INTERVAL_SECONDS = 120   # earliest a new burst can start after the last one
BURST_MAX_INTERVAL_SECONDS = 240   # latest a new burst can start after the last one
BURST_DURATION_SECONDS = 25        # how long a burst lasts
BURST_ALARM_INTERVAL = (0.2, 0.5)  # seconds between alarms *during* a burst (much faster than normal)


def insert_alarm(cur, forced_element=None, forced_severity=None):
    element = forced_element or random.choice(NETWORK_ELEMENTS)
    is_problem = element in PROBLEM_ELEMENTS

    if forced_severity:
        severity = forced_severity
    elif is_problem:
        severity = random.choices(SEVERITIES, weights=SEVERITY_WEIGHTS_PROBLEM)[0]
    else:
        severity = random.choices(SEVERITIES, weights=SEVERITY_WEIGHTS_NORMAL)[0]

    now = datetime.now()
    cur.execute(
        """INSERT INTO alarms
           (network_element, alarming_object, severity, specific_problem,
            event_time, insert_time, clear_time)
           VALUES (%s, %s, %s, %s, %s, %s, NULL)""",
        (
            element,
            f"MeContext={element},Cabinet={random.randint(1, 3)}",
            severity,
            random.choice(SPECIFIC_PROBLEMS),
            now,
            now + timedelta(seconds=random.randint(1, 5)),
        ),
    )
    return element, severity, now


def insert_link_reading(cur):
    element = random.choice(NETWORK_ELEMENTS)
    is_problem = element in PROBLEM_ELEMENTS
    tx_power = round(random.gauss(43, 1.5), 2)
    if is_problem:
        rx_power = round(random.gauss(-100, 6), 2)
        vswr = round(max(1.0, random.gauss(2.3, 0.6)), 2)
    else:
        rx_power = round(random.gauss(-85, 4), 2)
        vswr = round(max(1.0, random.gauss(1.3, 0.2)), 2)

    cur.execute(
        """INSERT INTO link_quality
           (network_element, sector, tx_power_dbm, rx_power_dbm, vswr,
            connected_ues, reading_time)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (element, random.choice(SECTORS), tx_power, rx_power, vswr,
         random.randint(0, 180), datetime.now()),
    )


def resolve_some_open_alarms(cur):
    cur.execute(
        """SELECT alarm_id, severity FROM alarms
           WHERE clear_time IS NULL
           ORDER BY random() LIMIT 3"""
    )
    for alarm_id, severity in cur.fetchall():
        if random.random() < 0.25:
            cur.execute(
                "UPDATE alarms SET clear_time = %s WHERE alarm_id = %s",
                (datetime.now(), alarm_id),
            )
            print(f"           RESOLVED   alarm_id={alarm_id} ({severity})")


def run_burst(cur, conn):
    """Floods one random element with rapid alarms for BURST_DURATION_SECONDS,
    so anomaly detection has a real spike to catch."""
    target = random.choice(NETWORK_ELEMENTS)
    print(f"\n*** BURST STARTING on {target} for {BURST_DURATION_SECONDS}s ***\n")

    end_time = time.time() + BURST_DURATION_SECONDS
    count = 0
    while time.time() < end_time:
        element, severity, now = insert_alarm(cur, forced_element=target, forced_severity=random.choice(["Critical", "Major"]))
        conn.commit()
        count += 1
        print(f"[{now:%H:%M:%S}] BURST ALARM  {severity:8s} {element}")
        time.sleep(random.uniform(*BURST_ALARM_INTERVAL))

    print(f"\n*** BURST ENDED on {target} — {count} alarms fired ***\n")


def main():
    conn = connect()
    conn.autocommit = True
    cur = conn.cursor()
    print("Streaming alarms + link quality data... Ctrl+C to stop.")

    next_burst_at = time.time() + random.uniform(BURST_MIN_INTERVAL_SECONDS, BURST_MAX_INTERVAL_SECONDS)

    try:
        while True:
            if time.time() >= next_burst_at:
                run_burst(cur, conn)
                next_burst_at = time.time() + random.uniform(BURST_MIN_INTERVAL_SECONDS, BURST_MAX_INTERVAL_SECONDS)
                continue  # skip the normal tick right after a burst

            element, severity, now = insert_alarm(cur)
            print(f"[{now:%H:%M:%S}] NEW ALARM  {severity:8s} {element}")

            if random.random() < 0.7:
                insert_link_reading(cur)
            resolve_some_open_alarms(cur)
            time.sleep(random.uniform(1, 4))
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
