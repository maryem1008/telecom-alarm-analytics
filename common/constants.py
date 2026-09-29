"""
Single source of truth for domain constants shared across the pipeline.

Previously these were copy-pasted independently into generate_alarms.py,
stream_alarms.py, analytics_job.py, and generate_report.py, so a severity
weight or a risk threshold tuned in one place silently drifted out of sync
everywhere else. Everything below is imported, never redefined.
"""

NETWORK_ELEMENTS = [
    "3G_Rte_Hausaria", "BEN_KHAL2", "ARIANA_CTR1", "SFAX_NORD3",
    "SOUSSE_EST2", "TUNIS_CBD5", "NABEUL_SUD1", "BIZERTE_PORT2",
    "GABES_IND4", "KAIROUAN_CTR1",
]

# Elements deliberately made "problematic" so the analytics has something
# interesting to find (more alarms, worse link quality).
PROBLEM_ELEMENTS = {"BEN_KHAL2", "SFAX_NORD3", "GABES_IND4"}

# Approximate coordinates for the dashboard map (fictional Tunisian sites).
NETWORK_ELEMENT_COORDS = {
    "3G_Rte_Hausaria": (36.8989, 10.1858),
    "BEN_KHAL2": (36.7333, 10.2167),
    "ARIANA_CTR1": (36.8625, 10.1956),
    "SFAX_NORD3": (34.7406, 10.7603),
    "SOUSSE_EST2": (35.8256, 10.6084),
    "TUNIS_CBD5": (36.8065, 10.1815),
    "NABEUL_SUD1": (36.4561, 10.7376),
    "BIZERTE_PORT2": (37.2744, 9.8739),
    "GABES_IND4": (33.8815, 10.0982),
    "KAIROUAN_CTR1": (35.6781, 10.0963),
}

SEVERITIES = ["Critical", "Major", "Minor"]
SEVERITY_WEIGHTS_NORMAL = [0.15, 0.35, 0.50]    # most alarms are Minor, few Critical
SEVERITY_WEIGHTS_PROBLEM = [0.30, 0.40, 0.30]   # problem elements skew higher

SPECIFIC_PROBLEMS = [
    "Heartbeat", "Enclosure", "Loss Of Signal", "VSWR Alarm",
    "Power Supply Failure", "Transmission Link Down", "Temperature Alarm",
    "Battery Low", "Cell Out Of Service",
]

SECTORS = ["S1", "S2", "S3"]

# Severity -> numeric weight used to rank "problematic" network elements.
# Used by both the Spark job (compute_top_offenders) and the PDF report
# (fetch_top_offenders) so they can never disagree with each other.
SEVERITY_WEIGHT = {"Critical": 5, "Major": 2, "Minor": 1}

# At-risk tower thresholds — tune based on real equipment specs.
RX_POWER_THRESHOLD_DBM = -95.0   # weaker (more negative) than this = risk
VSWR_THRESHOLD = 1.8             # higher than this = risk

# Incident grouping: alarms on the same element within this gap are merged
# into a single incident (so a cascading failure reads as one event).
INCIDENT_GAP_MINUTES = 2

# Anomaly detection tuning.
ANOMALY_Z_THRESHOLD = 2.0
ANOMALY_BUCKET_MINUTES = 5
ANOMALY_LOOKBACK_HOURS = 3

# ---- Shared visual palette — professional, neutral, no blue chrome ----
# Used by the Streamlit dashboard's charts/map and the PDF report's header,
# so the two look like one product instead of two differently styled tools.
BRAND_INK = "#111827"          # near-black — headings, primary text
BRAND_MUTED = "#6b7280"        # gray — secondary text, captions
BRAND_LINE = "#e5e7eb"         # hairline borders / dividers
BRAND_SURFACE = "#f8f9fa"      # off-white card / section background
BRAND_ACCENT = "#111827"       # buttons / active states (ink, not blue)

SEVERITY_COLORS = {
    "Critical": "#b91c1c",     # red
    "Major": "#b45309",        # amber
    "Minor": "#6b7280",        # gray
}
STATUS_COLORS = {
    "critical": "#b91c1c",
    "major": "#b45309",
    "minor": "#ca8a04",
    "healthy": "#15803d",
}
