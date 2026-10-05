"""Engineer-only simulated alarm remediation page."""

import os
import sys

import psycopg2
import streamlit as st

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from remediation.auth import login, logout, require_engineer  # noqa: E402
from remediation.catalog import (  # noqa: E402
    runbooks_for_alarm,
    validate_runbook_selection,
)
from remediation.executor import (  # noqa: E402
    SimulatedExecutor,
    dispatch_non_remote_alarm,
)
from remediation.repository import (  # noqa: E402
    apply_schema,
    clear_alarm,
    create_dispatch_ticket,
    create_remediation_action,
    get_alarm,
    list_open_alarms,
)

st.set_page_config(page_title="Remediation", layout="wide")
st.markdown(
    '<style>[data-testid="stSidebarNav"] {display: none;}</style>',
    unsafe_allow_html=True,
)
st.title("Engineer Remediation")
st.warning(
    "SIMULATED ONLY — commands are displayed and simulated; "
    "no real equipment is contacted."
)


@st.cache_resource
def prepare_database() -> None:
    """Apply the idempotent project schema once per Streamlit process."""
    apply_schema()


try:
    prepare_database()
except (ConnectionError, OSError, psycopg2.Error) as exc:
    st.error(f"Could not prepare the remediation database: {exc}")
    st.info("Run `python run_local.py` after configuring Postgres.")
    st.stop()

session = st.session_state
if "remediation_executor" not in session:
    session["remediation_executor"] = SimulatedExecutor()

try:
    engineer = require_engineer(session)
except PermissionError:
    st.subheader("Engineer login required")
    st.caption(
        "This page is restricted to company-issued engineer accounts. "
        "Provision an account with `python manage_users.py add <name>`."
    )
    with st.form("engineer_login"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Log in")
    if submitted:
        succeeded, message = login(username.strip(), password, session)
        if succeeded:
            st.success(message)
            st.rerun()
        st.error(message)
    st.stop()

st.caption(f"Signed in as {engineer} · engineer role")
if st.button("Log out"):
    logout(session)
    st.rerun()

try:
    alarms = list_open_alarms(session)
except Exception as exc:
    st.error(f"Could not load open alarms: {exc}")
    st.stop()

if not alarms:
    st.info("There are no open alarms to remediate.")
    st.stop()

alarm_labels = {
    alarm["alarm_id"]: (
        f"#{alarm['alarm_id']} · {alarm['network_element']} · "
        f"{alarm['severity']} · {alarm['specific_problem']}"
    )
    for alarm in alarms
}
passed_selection = session.get("remediation_selection", {})
default_alarm_id = passed_selection.get("alarm_id")
alarm_ids = list(alarm_labels)
selected_alarm_id = st.selectbox(
    "Open alarm",
    options=alarm_ids,
    index=alarm_ids.index(
        default_alarm_id) if default_alarm_id in alarm_ids else 0,
    format_func=alarm_labels.get,
)
alarm = get_alarm(session, selected_alarm_id)
if alarm is None:
    st.error("The selected alarm no longer exists.")
    st.stop()
st.write(
    f"**Element:** {alarm['network_element']}  ·  "
    f"**Problem:** {alarm['specific_problem']}  ·  "
    f"**Severity:** {alarm['severity']}"
)

runbooks = runbooks_for_alarm(alarm["specific_problem"])
if not runbooks:
    st.error("No approved runbook exists for this alarm type.")
    st.stop()

runbook = st.selectbox(
    "Approved runbook",
    options=runbooks,
    format_func=lambda entry: f"{entry['name']} · {entry['risk']} risk",
)
params: dict[str, object] = {}
for name, spec in runbook["params"].items():
    if spec["type"] == "integer":
        params[name] = st.number_input(
            name.replace("_", " ").title(),
            min_value=spec["min"],
            max_value=spec["max"],
            value=spec["min"],
            step=1,
            key=f"param_{name}",
        )
    elif spec["type"] == "choice":
        params[name] = st.selectbox(
            name.replace("_", " ").title(),
            options=spec["values"],
            key=f"param_{name}",
        )

executor = session["remediation_executor"]
try:
    runbook = validate_runbook_selection(
        runbook["id"],
        alarm["specific_problem"],
        params,
        alarm["network_element"],
    )
except ValueError as exc:
    st.error(str(exc))
    st.stop()

if not runbook["remote"]:
    st.info(
        "This alarm is not remotely remediable; "
        "a field dispatch ticket will be created."
    )
    dispatch_key = f"dispatch_ticket_{alarm['alarm_id']}_{runbook['id']}"
    if dispatch_key not in session:
        ticket_id = dispatch_non_remote_alarm(
            session,
            alarm["alarm_id"],
            alarm["network_element"],
            alarm["specific_problem"],
        )
        session[dispatch_key] = ticket_id
        st.success(f"Dispatch ticket #{ticket_id} created.")
else:
    dry_col, execute_col = st.columns(2)
    with dry_col:
        if st.button("Dry run"):
            result = executor.dry_run(
                session, runbook, alarm["network_element"], params)
            create_remediation_action(
                session, alarm["alarm_id"], alarm["network_element"],
                runbook["id"], params, "dry_run", "dry_run", result.output,
            )
            st.code(result.output)

    with execute_col:
        approved = st.checkbox(
            "I reviewed these actions and approve this simulated execution.")
        if st.button("Approve & Execute", disabled=not approved):
            result = executor.execute(
                session, runbook, alarm["network_element"], params)
            create_remediation_action(
                session, alarm["alarm_id"], alarm["network_element"],
                runbook["id"], params, "execute", "completed", result.output,
            )
            st.code(result.output)
            verification = executor.verify(
                session, runbook, alarm["network_element"], params
            )
            st.write(verification.output)
            if verification.success:
                clear_alarm(session, alarm["alarm_id"])
                if runbook.get("dispatch_after"):
                    ticket_id = create_dispatch_ticket(
                        session,
                        alarm["alarm_id"],
                        alarm["network_element"],
                        "Temperature alarm requires field inspection after "
                        "simulated Tx power reduction.",
                    )
                    st.warning(f"Field dispatch ticket #{ticket_id} created.")
                st.success(
                    "Simulated verification passed; alarm marked cleared.")
            else:
                st.error("Verification failed. Rollback is available.")
                session["rollback_pending"] = {
                    "alarm_id": alarm["alarm_id"],
                    "network_element": alarm["network_element"],
                    "runbook_id": runbook["id"],
                    "params": params,
                }
                ticket_id = create_dispatch_ticket(
                    session,
                    alarm["alarm_id"],
                    alarm["network_element"],
                    "Simulated remediation verification failed; "
                    "field inspection required.",
                )
                st.warning(f"Field dispatch ticket #{ticket_id} created.")

if pending := session.get("rollback_pending"):
    if pending["alarm_id"] == alarm["alarm_id"] and st.button(
            "Rollback simulated changes"):
        rollback_runbook = next(
            item for item in runbooks if item["id"] == pending["runbook_id"]
        )
        rollback = executor.rollback(
            session,
            rollback_runbook,
            pending["network_element"],
            pending["params"],
        )
        create_remediation_action(
            session,
            alarm["alarm_id"],
            alarm["network_element"],
            rollback_runbook["id"],
            pending["params"],
            "rollback",
            "completed" if rollback.success else "failed",
            rollback.output,
        )
        session.pop("rollback_pending", None)
        st.code(rollback.output)
