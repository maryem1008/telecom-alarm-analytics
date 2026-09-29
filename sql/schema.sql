-- ============================================================
-- Telecom Network Alarm & Performance Analytics — Schema
-- ============================================================

CREATE TABLE IF NOT EXISTS network_elements (
    element_id      SERIAL PRIMARY KEY,
    element_name    VARCHAR(100) NOT NULL UNIQUE,   -- e.g. "3G_Rte_Hausaria"
    region          VARCHAR(50)
);

CREATE TABLE IF NOT EXISTS alarms (
    alarm_id            SERIAL PRIMARY KEY,
    network_element      VARCHAR(100) NOT NULL,      -- e.g. "BEN_KHAL2"
    alarming_object      VARCHAR(150),                -- e.g. "MeContext=..., Cabinet=1"
    severity             VARCHAR(10) NOT NULL CHECK (severity IN ('Critical','Major','Minor')),
    specific_problem     VARCHAR(100),                -- e.g. "Heartbeat", "Enclosure"
    event_time           TIMESTAMP NOT NULL,
    insert_time          TIMESTAMP NOT NULL,
    clear_time           TIMESTAMP                    -- NULL while still open
);

CREATE TABLE IF NOT EXISTS link_quality (
    reading_id      SERIAL PRIMARY KEY,
    network_element VARCHAR(100) NOT NULL,
    sector          VARCHAR(10),
    tx_power_dbm    NUMERIC(5,2),
    rx_power_dbm    NUMERIC(5,2),
    vswr            NUMERIC(4,2),
    connected_ues   INTEGER,
    reading_time    TIMESTAMP NOT NULL
);

-- ---------- Aggregated tables (written by spark/analytics_job.py) ----------
-- Pre-created here on purpose: the Spark job writes to these with
-- `.option("truncate", "true")` (clear rows, keep table) rather than
-- `mode="overwrite"` with no truncate option (which DROPs and recreates the
-- table from an inferred schema, silently losing these constraints).

CREATE TABLE IF NOT EXISTS agg_alarm_counts (
    network_element VARCHAR(100),
    severity        VARCHAR(10),
    alarm_count     INTEGER,
    run_date        DATE,
    PRIMARY KEY (network_element, severity, run_date)
);

CREATE TABLE IF NOT EXISTS agg_mttr (
    network_element   VARCHAR(100) PRIMARY KEY,
    avg_mttr_minutes  NUMERIC(10,2),
    resolved_alarms   INTEGER,
    run_date          DATE
);

CREATE TABLE IF NOT EXISTS agg_at_risk_towers (
    network_element  VARCHAR(100) PRIMARY KEY,
    avg_rx_power_dbm NUMERIC(5,2),
    avg_vswr         NUMERIC(4,2),
    risk_flag        BOOLEAN,
    run_date         DATE
);

CREATE TABLE IF NOT EXISTS agg_anomalies (
    network_element  VARCHAR(100) PRIMARY KEY,
    latest_count     INTEGER,
    avg_count        NUMERIC(10,2),
    stddev_count     NUMERIC(10,2),
    z_score          NUMERIC(10,2),
    is_anomaly       BOOLEAN,
    run_date         DATE
);

CREATE TABLE IF NOT EXISTS agg_incidents (
    network_element   VARCHAR(100),
    incident_group    INTEGER,
    start_time        TIMESTAMP,
    end_time          TIMESTAMP,
    alarm_count       INTEGER,
    worst_severity    VARCHAR(10),
    duration_minutes  NUMERIC(10,2),
    run_date          DATE,
    PRIMARY KEY (network_element, incident_group, run_date)
);

CREATE INDEX IF NOT EXISTS idx_alarms_element ON alarms(network_element);
CREATE INDEX IF NOT EXISTS idx_alarms_severity ON alarms(severity);
CREATE INDEX IF NOT EXISTS idx_alarms_insert_time ON alarms(insert_time);
CREATE INDEX IF NOT EXISTS idx_link_element ON link_quality(network_element);
