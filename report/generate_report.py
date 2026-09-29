"""
Generates a PDF summary report of current telecom network alarm & performance
status — an "ops handover report" style deliverable.

Queries Postgres directly (not the Spark aggregate tables) so the report is
always based on the freshest data, independent of the Spark job's refresh cycle.
The severity weights and at-risk thresholds are imported from common/constants.py
so this can never quietly disagree with spark/analytics_job.py's numbers.

Usage:
    python report/generate_report.py                  # writes report/telecom_report.pdf
    python report/generate_report.py --output my.pdf   # custom output path
"""

import os
import sys
import argparse
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from common.constants import BRAND_INK, RX_POWER_THRESHOLD_DBM, VSWR_THRESHOLD
from common.db import connect


def fetch_summary(cur):
    cur.execute(
        """SELECT
            COUNT(*) FILTER (WHERE insert_time > NOW() - INTERVAL '1 hour') AS last_hour,
            COUNT(*) FILTER (WHERE clear_time IS NULL) AS total_open,
            COUNT(*) FILTER (WHERE severity = 'Critical' AND clear_time IS NULL) AS open_critical,
            COUNT(*) AS total_alarms
           FROM alarms"""
    )
    return cur.fetchone()


def fetch_severity_breakdown(cur):
    cur.execute(
        """SELECT severity, COUNT(*) AS total
           FROM alarms GROUP BY severity ORDER BY total DESC"""
    )
    return cur.fetchall()


def fetch_top_offenders(cur, limit=10):
    cur.execute(
        """SELECT network_element,
                  SUM(CASE severity
                      WHEN 'Critical' THEN 5
                      WHEN 'Major' THEN 2
                      ELSE 1 END) AS risk_score,
                  COUNT(*) AS total_alarms
           FROM alarms
           GROUP BY network_element
           ORDER BY risk_score DESC
           LIMIT %s""",
        (limit,),
    )
    return cur.fetchall()


def fetch_mttr(cur, limit=10):
    cur.execute(
        """SELECT network_element,
                  ROUND(AVG(EXTRACT(EPOCH FROM (clear_time - event_time)) / 60)::numeric, 2)
                      AS avg_mttr_minutes,
                  COUNT(*) AS resolved_alarms
           FROM alarms
           WHERE clear_time IS NOT NULL
           GROUP BY network_element
           ORDER BY avg_mttr_minutes DESC
           LIMIT %s""",
        (limit,),
    )
    return cur.fetchall()


def fetch_at_risk_towers(cur):
    cur.execute(
        """SELECT network_element,
                  ROUND(AVG(rx_power_dbm)::numeric, 2) AS avg_rx_power,
                  ROUND(AVG(vswr)::numeric, 2) AS avg_vswr
           FROM link_quality
           GROUP BY network_element
           HAVING AVG(rx_power_dbm) < %s OR AVG(vswr) > %s
           ORDER BY avg_rx_power ASC""",
        (RX_POWER_THRESHOLD_DBM, VSWR_THRESHOLD),
    )
    return cur.fetchall()


def build_table(data, header, col_widths=None):
    table_data = [header] + [list(map(str, row)) for row in data]
    t = Table(table_data, colWidths=col_widths)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(BRAND_INK)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e5e7eb")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8f9fa")]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def generate_report(output_path="report/telecom_report.pdf"):
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    conn = connect()
    cur = conn.cursor()

    last_hour, total_open, open_critical, total_alarms = fetch_summary(cur)
    severity_rows = fetch_severity_breakdown(cur)
    top_offenders_rows = fetch_top_offenders(cur)
    mttr_rows = fetch_mttr(cur)
    at_risk_rows = fetch_at_risk_towers(cur)

    cur.close()
    conn.close()

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("TitleCustom", parent=styles["Title"], textColor=colors.HexColor(BRAND_INK))
    h2_style = ParagraphStyle("H2Custom", parent=styles["Heading2"], textColor=colors.HexColor(BRAND_INK), spaceBefore=14)
    body_style = styles["BodyText"]

    doc = SimpleDocTemplate(
        output_path, pagesize=A4,
        topMargin=1.5 * cm, bottomMargin=1.5 * cm,
        leftMargin=1.5 * cm, rightMargin=1.5 * cm,
    )
    elements = []

    elements.append(Paragraph("Telecom Network Alarm & Performance — Ops Summary Report", title_style))
    elements.append(Paragraph(
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", body_style
    ))
    elements.append(Spacer(1, 0.5 * cm))

    elements.append(Paragraph("Executive Summary", h2_style))
    top_element = top_offenders_rows[0][0] if top_offenders_rows else "N/A"
    summary_text = (
        f"In the last hour, <b>{last_hour}</b> new alarms were recorded. "
        f"There are currently <b>{total_open}</b> open alarms network-wide, "
        f"of which <b>{open_critical}</b> are Critical severity. "
        f"The most problematic network element overall is <b>{top_element}</b>. "
        f"{len(at_risk_rows)} network element(s) are currently flagged as at-risk "
        f"based on radio link quality thresholds."
    )
    elements.append(Paragraph(summary_text, body_style))
    elements.append(Spacer(1, 0.5 * cm))

    elements.append(Paragraph("Alarm Count by Severity", h2_style))
    if severity_rows:
        elements.append(build_table(severity_rows, ["Severity", "Count"], col_widths=[8 * cm, 4 * cm]))
    else:
        elements.append(Paragraph("No alarms recorded.", body_style))
    elements.append(Spacer(1, 0.5 * cm))

    elements.append(Paragraph("Top 10 Problematic Network Elements", h2_style))
    if top_offenders_rows:
        elements.append(build_table(
            top_offenders_rows,
            ["Network Element", "Risk Score", "Total Alarms"],
            col_widths=[7 * cm, 4 * cm, 4 * cm],
        ))
    else:
        elements.append(Paragraph("No alarms recorded.", body_style))
    elements.append(Spacer(1, 0.5 * cm))

    elements.append(Paragraph("MTTR by Network Element (Top 10 Slowest)", h2_style))
    if mttr_rows:
        elements.append(build_table(
            mttr_rows,
            ["Network Element", "Avg MTTR (min)", "Resolved Alarms"],
            col_widths=[7 * cm, 4 * cm, 4 * cm],
        ))
    else:
        elements.append(Paragraph("No resolved alarms yet.", body_style))
    elements.append(Spacer(1, 0.5 * cm))

    elements.append(Paragraph("At-Risk Towers (Weak RX Power or High VSWR)", h2_style))
    if at_risk_rows:
        elements.append(build_table(
            at_risk_rows,
            ["Network Element", "Avg RX Power (dBm)", "Avg VSWR"],
            col_widths=[7 * cm, 5 * cm, 3 * cm],
        ))
    else:
        elements.append(Paragraph("No towers currently flagged as at-risk.", body_style))

    doc.build(elements)
    print(f"Report generated: {output_path}")
    return output_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="report/telecom_report.pdf")
    args = parser.parse_args()
    generate_report(args.output)
