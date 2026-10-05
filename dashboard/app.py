"""
Streamlit dashboard for the Telecom Network Alarm & Performance Analytics Pipeline.

Run:
    streamlit run dashboard/app.py
"""

import os
import sys
from datetime import datetime

import pandas as pd
import altair as alt
import pydeck as pdk
import streamlit as st

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from common.constants import (
    NETWORK_ELEMENT_COORDS,
    SEVERITIES,
    SEVERITY_COLORS,
    BRAND_INK,
)
from common.db import connect
from report.generate_report import generate_report

ALL_ELEMENTS = list(NETWORK_ELEMENT_COORDS.keys())
ALL_SEVERITIES = SEVERITIES

st.set_page_config(page_title="Telecom Alarm & Performance Analytics", layout="wide")

# ---------------------------------------------------------------------------
# Look & feel: white, neutral, no blue chrome. .streamlit/config.toml sets
# the base theme; this adds card framing, spacing, and a quieter typeface
# scale than Streamlit's defaults so the page reads as one designed product
# rather than a stack of default widgets.
# ---------------------------------------------------------------------------
st.markdown(
    f"""
    <style>
        .stApp {{ background-color: #ffffff; }}
        #MainMenu, footer {{ visibility: hidden; }}
        .block-container {{ padding-top: 2rem; padding-bottom: 3rem; max-width: 1200px; }}

        h1, h2, h3 {{ color: {BRAND_INK}; font-weight: 600; letter-spacing: -0.01em; }}
        h1 {{ font-size: 1.65rem; margin-bottom: 0.1rem; }}
        h3 {{ font-size: 1.02rem; text-transform: uppercase; letter-spacing: 0.04em;
              color: #4b5563; margin-top: 0.2rem; }}
        p, .stCaption, [data-testid="stCaptionContainer"] {{ color: #6b7280; }}

        [data-testid="stMetric"] {{
            background: #f8f9fa; border: 1px solid #e5e7eb; border-radius: 10px;
            padding: 0.9rem 1rem 0.7rem 1rem;
        }}
        [data-testid="stMetricLabel"] {{ color: #6b7280; font-size: 0.8rem; }}
        [data-testid="stMetricValue"] {{ color: {BRAND_INK}; }}

        hr, [data-testid="stDivider"] {{ border-color: #e5e7eb !important; }}

        .stButton > button, .stDownloadButton > button {{
            background-color: {BRAND_INK}; color: #ffffff; border: none;
            border-radius: 8px; font-weight: 500;
        }}
        .stButton > button:hover, .stDownloadButton > button:hover {{
            background-color: #374151; color: #ffffff;
        }}

        [data-testid="stSidebar"] {{ background-color: #f8f9fa; border-right: 1px solid #e5e7eb; }}
        [data-testid="stSidebarNav"] {{ display: none; }}

        .app-header {{ display: flex; align-items: baseline; gap: 0.6rem; margin-bottom: 0.1rem; }}
        .app-header .dot {{ width: 9px; height: 9px; border-radius: 50%; background: #15803d;
                             display: inline-block; }}
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_resource
def get_connection():
    conn = connect()
    conn.autocommit = True
    return conn


def run_query(sql, params=None):
    """Runs a parameterized, read-only query. Falls back to an empty frame
    (rather than raising into the page) if a table isn't populated yet —
    e.g. before the first Spark run."""
    conn = get_connection()
    try:
        return pd.read_sql(sql, conn, params=params)
    except Exception:
        conn.rollback()
        return pd.DataFrame()


st.markdown(
    '<div class="app-header"><span class="dot"></span>'
    '<span style="color:#6b7280; font-size:0.85rem;">LIVE</span></div>',
    unsafe_allow_html=True,
)
st.title("Telecom Network Alarm & Performance Analytics")
st.caption("Synthetic data modeled on an Ericsson OSS Alarm Monitor.")

# --- Sidebar filters (apply across every section below) ---
st.sidebar.header("Filters")
selected_elements = st.sidebar.multiselect(
    "Network elements", options=ALL_ELEMENTS, default=ALL_ELEMENTS
)
selected_severities = st.sidebar.multiselect(
    "Severity", options=ALL_SEVERITIES, default=ALL_SEVERITIES
)
if st.sidebar.button("Reset filters"):
    selected_elements = ALL_ELEMENTS
    selected_severities = ALL_SEVERITIES
    st.rerun()

# --- Alarm lookup; opening an alarm reveals the engineer-only action ---
selection_rows = run_query(
    """
    SELECT alarm_id, network_element, specific_problem, severity
    FROM alarms
    WHERE clear_time IS NULL
      AND network_element = ANY(%(elements)s)
      AND severity = ANY(%(severities)s)
    ORDER BY insert_time DESC
    LIMIT 300
    """,
    params={
        "elements": list(selected_elements) or ["__none__"],
        "severities": list(selected_severities) or ["__none__"],
    },
)
st.subheader("Find an open alarm")
if selection_rows.empty:
    st.info("No open alarms match the current filters.")
else:
    selection_labels = {
        int(row.alarm_id): (
            f"#{int(row.alarm_id)} · {row.network_element} · "
            f"{row.severity} · {row.specific_problem}"
        )
        for row in selection_rows.itertuples()
    }
    selected_alarm_id = st.selectbox(
        "Search alarms by ID, element, severity, or problem",
        options=list(selection_labels),
        format_func=selection_labels.get,
        key="public_remediation_alarm",
    )
    if st.button("Open", key="open_alarm_for_remediation"):
        selected_alarm = selection_rows.loc[
            selection_rows["alarm_id"] == selected_alarm_id
        ].iloc[0]
        st.session_state["opened_remediation_alarm"] = {
            "alarm_id": int(selected_alarm_id),
            "network_element": selected_alarm["network_element"],
            "specific_problem": selected_alarm["specific_problem"],
            "severity": selected_alarm["severity"],
        }

opened_alarm = st.session_state.get("opened_remediation_alarm")
if opened_alarm:
    alarm_detail = run_query(
        """
        SELECT alarm_id, network_element, alarming_object, specific_problem,
               severity, event_time, insert_time, clear_time
        FROM alarms
        WHERE alarm_id = %(alarm_id)s
          AND clear_time IS NULL
        """,
        params={"alarm_id": opened_alarm["alarm_id"]},
    )
    if alarm_detail.empty:
        st.warning("Alarm details are currently unavailable.")
    else:
        detail = alarm_detail.iloc[0]
        element = detail["network_element"]
        latitude, longitude = NETWORK_ELEMENT_COORDS.get(
            element, (None, None)
        )
        st.markdown(f"### Alarm #{int(detail['alarm_id'])} details")
        location_col, alarm_col, status_col = st.columns(3)
        with location_col:
            st.markdown("**Site location**")
            st.write(f"Network element: {element}")
            site_object = detail["alarming_object"] or "Not recorded"
            st.write(f"Site object: {site_object}")
            if latitude is not None and longitude is not None:
                st.write(
                    f"Approx. coordinates: {latitude:.4f}, {longitude:.4f}"
                )
                st.link_button(
                    "View site on map",
                    f"https://www.google.com/maps?q={latitude},{longitude}",
                )
        with alarm_col:
            st.markdown("**Alarm information**")
            problem = detail["specific_problem"] or "Not recorded"
            st.write(f"Problem: {problem}")
            st.write(f"Severity: {detail['severity']}")
            st.write(f"Started: {detail['event_time']}")
            st.write(f"Received: {detail['insert_time']}")
        with status_col:
            st.markdown("**Latest link reading**")
            link_reading = run_query(
                """
                SELECT sector, rx_power_dbm, tx_power_dbm, vswr,
                       connected_ues, reading_time
                FROM link_quality
                WHERE network_element = %(element)s
                ORDER BY reading_time DESC
                LIMIT 1
                """,
                params={"element": element},
            )
            if link_reading.empty:
                st.write("No link-quality readings recorded.")
            else:
                reading = link_reading.iloc[0]
                st.write(f"Sector: {reading['sector'] or 'Not recorded'}")
                st.write(f"Rx power: {reading['rx_power_dbm']} dBm")
                st.write(f"Tx power: {reading['tx_power_dbm']} dBm")
                st.write(f"VSWR: {reading['vswr']}")
                st.write(f"Connected UEs: {reading['connected_ues']}")
                st.caption(f"Reading time: {reading['reading_time']}")

        same_alarm = run_query(
            """
            SELECT alarm_id, severity, event_time, insert_time, clear_time
            FROM alarms
            WHERE network_element = %(element)s
              AND specific_problem = %(problem)s
              AND alarm_id <> %(alarm_id)s
            ORDER BY event_time DESC
            LIMIT 1
            """,
            params={
                "element": element,
                "problem": detail["specific_problem"],
                "alarm_id": int(detail["alarm_id"]),
            },
        )
        st.markdown("**Most recent matching alarm**")
        if same_alarm.empty:
            st.write(
                "No previous alarm of this type is recorded for this site."
            )
        else:
            previous = same_alarm.iloc[0]
            previous_status = (
                "Open" if pd.isna(previous["clear_time"]) else "Cleared"
            )
            st.write(
                f"Alarm #{int(previous['alarm_id'])} · "
                f"{previous['severity']} · "
                f"occurred {previous['event_time']} · {previous_status}"
            )

    st.caption(
        "Engineer login required to continue. All actions are simulated."
    )
    if st.button("Remediate", key="remediate_opened_alarm"):
        st.session_state["remediation_selection"] = opened_alarm
        st.switch_page("pages/2_Remediation.py")

# psycopg2/pandas can't parameterize a variable-length IN (...) list
# directly, so build a tuple and use `= ANY(%s)` instead — no string
# interpolation of user-controlled values into SQL.
elements_param = list(selected_elements) or ["__none__"]
severities_param = list(selected_severities) or ["__none__"]

st.divider()

# --- Report generation ---
report_col1, report_col2 = st.columns([1, 4])
with report_col1:
    if st.button("Generate PDF report", key="generate_report_btn"):
        with st.spinner("Generating report..."):
            path = generate_report(os.path.join(os.path.dirname(__file__), "..", "report", "telecom_report.pdf"))
            with open(path, "rb") as f:
                st.session_state["report_bytes"] = f.read()
        st.success("Report ready.")

with report_col2:
    if "report_bytes" in st.session_state:
        st.download_button(
            label="Download report",
            data=st.session_state["report_bytes"],
            file_name=f"telecom_report_{datetime.now():%Y%m%d_%H%M}.pdf",
            mime="application/pdf",
            key="download_report_btn",
        )

st.divider()


# --- FAST fragment: live metrics + live alarm feed (refreshes every 4s) ---
@st.fragment(run_every="4s")
def live_feed_section():
    df_live = run_query(
        """
        SELECT
            COUNT(*) FILTER (WHERE insert_time > NOW() - INTERVAL '5 minutes') AS last_5min,
            COUNT(*) FILTER (WHERE severity = 'Critical' AND clear_time IS NULL) AS open_critical,
            COUNT(*) FILTER (WHERE clear_time IS NULL) AS total_open
        FROM alarms
        WHERE network_element = ANY(%(elements)s) AND severity = ANY(%(severities)s)
        """,
        params={"elements": elements_param, "severities": severities_param},
    )
    if not df_live.empty:
        m1, m2, m3 = st.columns(3)
        m1.metric("Alarms in last 5 min", int(df_live.last_5min[0]))
        m2.metric("Open critical alarms", int(df_live.open_critical[0]))
        m3.metric("Total open alarms", int(df_live.total_open[0]))

    st.subheader("Live alarm feed")
    df_feed = run_query(
        """
        SELECT network_element, severity, specific_problem, insert_time,
               CASE WHEN clear_time IS NULL THEN 'OPEN' ELSE 'RESOLVED' END AS status
        FROM alarms
        WHERE network_element = ANY(%(elements)s) AND severity = ANY(%(severities)s)
        ORDER BY insert_time DESC
        LIMIT 15
        """,
        params={"elements": elements_param, "severities": severities_param},
    )
    st.dataframe(df_feed, use_container_width=True, hide_index=True)


live_feed_section()

st.divider()


# --- MEDIUM fragment: time series (refreshes every 10s) ---
@st.fragment(run_every="10s")
def timeseries_section():
    st.subheader("Alarm volume over time (last 60 minutes)")
    df_timeseries = run_query(
        """
        SELECT date_trunc('minute', insert_time) AS minute, severity, COUNT(*) AS count
        FROM alarms
        WHERE insert_time > NOW() - INTERVAL '60 minutes'
          AND network_element = ANY(%(elements)s) AND severity = ANY(%(severities)s)
        GROUP BY minute, severity
        ORDER BY minute
        """,
        params={"elements": elements_param, "severities": severities_param},
    )
    if not df_timeseries.empty:
        line_chart = (
            alt.Chart(df_timeseries)
            .mark_line(point=True)
            .encode(
                x=alt.X("minute:T", title="Time"),
                y=alt.Y("count:Q", title="Alarms"),
                color=alt.Color(
                    "severity:N", title="Severity",
                    scale=alt.Scale(domain=list(SEVERITY_COLORS.keys()), range=list(SEVERITY_COLORS.values())),
                ),
                tooltip=["minute:T", "severity:N", "count:Q"],
            )
            .properties(height=280)
            .configure_view(strokeWidth=0)
            .configure_axis(gridColor="#f0f0f0", domainColor="#e5e7eb", labelColor="#6b7280", titleColor="#6b7280")
        )
        st.altair_chart(line_chart, use_container_width=True)
    else:
        st.info("No data for the current filter selection in this window. Run generator/stream_alarms.py alongside this dashboard for a live feed.")


timeseries_section()

st.divider()


# --- FAST fragment: map, based on currently OPEN alarms ---
@st.fragment(run_every="10s")
def map_section():
    st.subheader("Network map — current status")
    st.caption("Reflects currently open alarms in real time, respecting the sidebar filters.")

    df_status = run_query(
        """
        SELECT network_element,
               COUNT(*) FILTER (WHERE severity = 'Critical' AND clear_time IS NULL) AS critical_count,
               COUNT(*) FILTER (WHERE severity = 'Major' AND clear_time IS NULL) AS major_count,
               COUNT(*) FILTER (WHERE severity = 'Minor' AND clear_time IS NULL) AS minor_count,
               COUNT(*) FILTER (WHERE clear_time IS NULL) AS total_open
        FROM alarms
        WHERE network_element = ANY(%(elements)s) AND severity = ANY(%(severities)s)
        GROUP BY network_element
        """,
        params={"elements": elements_param, "severities": severities_param},
    )

    df_risk = run_query("SELECT network_element, risk_flag FROM agg_at_risk_towers")

    if df_status.empty:
        st.info("No alarm data for the current filter selection.")
        return

    if not df_risk.empty:
        df_status = df_status.merge(df_risk, on="network_element", how="left")
    else:
        df_status["risk_flag"] = False
    df_status["risk_flag"] = df_status["risk_flag"].fillna(False)

    def status_color(row):
        if row["critical_count"] > 0 or row["risk_flag"]:
            return [185, 28, 28, 200]      # red
        elif row["major_count"] > 0:
            return [180, 83, 9, 200]       # amber
        elif row["minor_count"] > 0:
            return [202, 138, 4, 200]      # yellow
        else:
            return [21, 128, 61, 200]      # green

    df_status["lat"] = df_status["network_element"].map(lambda e: NETWORK_ELEMENT_COORDS.get(e, (0, 0))[0])
    df_status["lon"] = df_status["network_element"].map(lambda e: NETWORK_ELEMENT_COORDS.get(e, (0, 0))[1])
    df_status["color"] = df_status.apply(status_color, axis=1)
    df_status["radius"] = 1500 + df_status["total_open"] * 150

    if "map_view_state" not in st.session_state:
        st.session_state["map_view_state"] = pdk.ViewState(latitude=35.5, longitude=10.0, zoom=6.3)

    layer = pdk.Layer(
        "ScatterplotLayer",
        data=df_status,
        get_position=["lon", "lat"],
        get_fill_color="color",
        get_radius="radius",
        pickable=True,
    )
    st.pydeck_chart(pdk.Deck(
        map_style=None,
        layers=[layer],
        initial_view_state=st.session_state["map_view_state"],
        tooltip={"text": "{network_element}\nOpen Critical: {critical_count}  Major: {major_count}  Minor: {minor_count}\nTotal open: {total_open}\nSpark at-risk: {risk_flag}"},
    ))
    st.caption("🔴 Open Critical or Spark at-risk   🟠 Open Major   🟡 Open Minor   🟢 All clear")


map_section()

st.divider()


# --- Anomaly detection ---
@st.fragment(run_every="15s")
def anomalies_section():
    st.subheader("Anomaly detection — unusual alarm spikes")
    st.caption(
        "Flags network elements whose alarm rate in the latest 5-minute window is more than "
        "2 standard deviations above their own historical average. Needs generator/stream_alarms.py "
        "running alongside the pipeline — a one-off historical load has nothing recent to compare."
    )

    df_anom = run_query(
        """
        SELECT network_element, latest_count, avg_count, stddev_count, z_score
        FROM agg_anomalies
        WHERE is_anomaly = TRUE AND network_element = ANY(%(elements)s)
        ORDER BY z_score DESC
        """,
        params={"elements": elements_param},
    )
    if df_anom.empty:
        st.success("No anomalies detected — all elements within normal range.")
    else:
        for _, row in df_anom.iterrows():
            st.warning(
                f"**{row['network_element']}** — {int(row['latest_count'])} alarms in the last 5 min "
                f"(usually ~{row['avg_count']:.1f}, z-score {row['z_score']:.2f})"
            )
        st.dataframe(df_anom, use_container_width=True, hide_index=True)


anomalies_section()

st.divider()


# --- Incident grouping ---
@st.fragment(run_every="15s")
def incidents_section():
    st.subheader("Incident timeline — related alarms grouped together")
    st.caption("Alarms from the same element within a 2-minute window are grouped into a single incident, so a cascading failure shows as one event, not many.")

    df_incidents = run_query(
        """
        SELECT network_element, start_time, end_time, alarm_count, worst_severity, duration_minutes
        FROM agg_incidents
        WHERE network_element = ANY(%(elements)s) AND worst_severity = ANY(%(severities)s)
        ORDER BY start_time DESC
        LIMIT 15
        """,
        params={"elements": elements_param, "severities": severities_param},
    )
    if df_incidents.empty:
        st.info("No multi-alarm incidents detected recently for the current filter selection.")
    else:
        st.dataframe(df_incidents, use_container_width=True, hide_index=True)


incidents_section()

st.divider()


# --- SLOW fragment: aggregate charts, driven by Spark's refresh cycle ---
@st.fragment(run_every="15s")
def aggregates_section():
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Alarm count by severity")
        df_sev = run_query(
            """
            SELECT severity, SUM(alarm_count) AS total
            FROM agg_alarm_counts
            WHERE network_element = ANY(%(elements)s) AND severity = ANY(%(severities)s)
            GROUP BY severity
            ORDER BY total DESC
            """,
            params={"elements": elements_param, "severities": severities_param},
        )
        if not df_sev.empty:
            chart = (
                alt.Chart(df_sev).mark_bar()
                .encode(
                    x=alt.X("severity", title=None),
                    y=alt.Y("total", title="Alarms"),
                    color=alt.Color(
                        "severity:N", legend=None,
                        scale=alt.Scale(domain=list(SEVERITY_COLORS.keys()), range=list(SEVERITY_COLORS.values())),
                    ),
                )
                .configure_view(strokeWidth=0)
                .configure_axis(gridColor="#f0f0f0", domainColor="#e5e7eb", labelColor="#6b7280", titleColor="#6b7280")
            )
            st.altair_chart(chart, use_container_width=True)
        else:
            st.info("No data yet — run the Spark job (`python spark/analytics_job.py --once`).")

    with col2:
        st.subheader("Top 10 problematic network elements")
        df_top = run_query(
            """
            SELECT network_element, SUM(alarm_count) AS total_alarms
            FROM agg_alarm_counts
            WHERE network_element = ANY(%(elements)s) AND severity = ANY(%(severities)s)
            GROUP BY network_element
            ORDER BY total_alarms DESC
            LIMIT 10
            """,
            params={"elements": elements_param, "severities": severities_param},
        )
        if not df_top.empty:
            chart2 = (
                alt.Chart(df_top).mark_bar(color=BRAND_INK)
                .encode(x=alt.X("total_alarms", title="Alarms"), y=alt.Y("network_element", sort="-x", title=None))
                .configure_view(strokeWidth=0)
                .configure_axis(gridColor="#f0f0f0", domainColor="#e5e7eb", labelColor="#6b7280", titleColor="#6b7280")
            )
            st.altair_chart(chart2, use_container_width=True)
        else:
            st.info("No data for the current filter selection.")

    st.divider()

    st.subheader("MTTR (Mean Time To Repair) by network element")
    df_mttr = run_query(
        """
        SELECT network_element, avg_mttr_minutes, resolved_alarms
        FROM agg_mttr
        WHERE network_element = ANY(%(elements)s)
        ORDER BY avg_mttr_minutes DESC
        """,
        params={"elements": elements_param},
    )
    if not df_mttr.empty:
        st.dataframe(df_mttr, use_container_width=True, hide_index=True)
    else:
        st.info("No resolved alarms for the current filter selection.")

    st.divider()

    st.subheader("At-risk towers (weak RX power or high VSWR)")
    df_risk = run_query(
        """
        SELECT network_element, avg_rx_power_dbm, avg_vswr, risk_flag
        FROM agg_at_risk_towers
        WHERE risk_flag = TRUE AND network_element = ANY(%(elements)s)
        ORDER BY avg_rx_power_dbm ASC
        """,
        params={"elements": elements_param},
    )
    if df_risk.empty:
        st.success("No towers currently flagged as at-risk.")
    else:
        st.dataframe(df_risk, use_container_width=True, hide_index=True)


aggregates_section()
