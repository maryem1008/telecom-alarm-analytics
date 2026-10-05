"""Whitelisted remediation runbooks and input validation."""

from typing import Any

from common.constants import (
    MAX_TX_POWER_REDUCTION_DB,
    NETWORK_ELEMENTS,
    RX_POWER_THRESHOLD_DBM,
    SPECIFIC_PROBLEMS,
    VSWR_THRESHOLD,
)

RUNBOOKS: dict[str, dict[str, Any]] = {
    "heartbeat-agent-restart": {
        "id": "heartbeat-agent-restart",
        "name": "Check heartbeat and restart management agent",
        "handles": ["Heartbeat"],
        "risk": "medium",
        "remote": True,
        "steps": [
            "ping -c 3 {network_element}",
            "restart management-agent",
        ],
        "rollback": ["restore management-agent previous state"],
        "params": {},
        "verify": {"metric": "heartbeat", "condition": "healthy"},
    },
    "loss-signal-radio-reset": {
        "id": "loss-signal-radio-reset",
        "name": "Read Rx power and reset radio port",
        "handles": ["Loss Of Signal"],
        "risk": "medium",
        "remote": True,
        "steps": [
            "read Rx power on {network_element}",
            "reset radio port {port}",
        ],
        "rollback": ["restore radio port {port} previous state"],
        "params": {"port": {"type": "integer", "min": 1, "max": 3}},
        "verify": {
            "metric": "rx_power_dbm",
            "condition": "gt",
            "value": RX_POWER_THRESHOLD_DBM,
        },
    },
    "vswr-power-reduction": {
        "id": "vswr-power-reduction",
        "name": "Read VSWR and reduce sector transmit power",
        "handles": ["VSWR Alarm"],
        "risk": "high",
        "remote": True,
        "steps": [
            "read VSWR on {network_element}, sector {sector}",
            "reduce Tx power by {reduction_db} dB on sector {sector}",
            "re-read VSWR on {network_element}, sector {sector}",
        ],
        "rollback": ["restore Tx power on sector {sector}"],
        "params": {
            "sector": {"type": "choice", "values": ["S1", "S2", "S3"]},
            "reduction_db": {
                "type": "integer",
                "min": 1,
                "max": MAX_TX_POWER_REDUCTION_DB,
            },
        },
        "verify": {
            "metric": "vswr",
            "condition": "lt",
            "value": VSWR_THRESHOLD,
        },
    },
    "transmission-route-reset": {
        "id": "transmission-route-reset",
        "name": "Reset interface and use backup route",
        "handles": ["Transmission Link Down"],
        "risk": "medium",
        "remote": True,
        "steps": [
            "reset interface {interface} on {network_element}",
            "switch {network_element} to backup route",
        ],
        "rollback": ["restore primary route on {network_element}"],
        "params": {
            "interface": {
                "type": "choice",
                "values": ["eth0", "eth1", "wan0"],
            },
        },
        "verify": {"metric": "transmission_link", "condition": "up"},
    },
    "cell-lock-unlock": {
        "id": "cell-lock-unlock",
        "name": "Lock and unlock affected cell",
        "handles": ["Cell Out Of Service"],
        "risk": "high",
        "remote": True,
        "steps": ["lock cell {sector}", "unlock cell {sector}"],
        "rollback": ["restore cell {sector} previous administrative state"],
        "params": {
            "sector": {"type": "choice", "values": ["S1", "S2", "S3"]},
        },
        "verify": {"metric": "cell_service", "condition": "available"},
    },
    "temperature-power-reduction": {
        "id": "temperature-power-reduction",
        "name": "Reduce Tx power and request field inspection",
        "handles": ["Temperature Alarm"],
        "risk": "high",
        "remote": True,
        "steps": [
            "reduce Tx power by {reduction_db} dB on {network_element}",
        ],
        "rollback": ["restore Tx power on {network_element}"],
        "params": {
            "reduction_db": {
                "type": "integer",
                "min": 1,
                "max": MAX_TX_POWER_REDUCTION_DB,
            },
        },
        "verify": {"metric": "temperature", "condition": "stable"},
        "dispatch_after": True,
    },
    "power-supply-dispatch": {
        "id": "power-supply-dispatch",
        "name": "Dispatch field engineer for power supply failure",
        "handles": ["Power Supply Failure"],
        "risk": "high",
        "remote": False,
        "steps": [],
        "rollback": [],
        "params": {},
        "verify": {"metric": "dispatch", "condition": "created"},
    },
    "battery-dispatch": {
        "id": "battery-dispatch",
        "name": "Dispatch field engineer for low battery",
        "handles": ["Battery Low"],
        "risk": "medium",
        "remote": False,
        "steps": [],
        "rollback": [],
        "params": {},
        "verify": {"metric": "dispatch", "condition": "created"},
    },
    "enclosure-dispatch": {
        "id": "enclosure-dispatch",
        "name": "Dispatch field engineer for enclosure alarm",
        "handles": ["Enclosure"],
        "risk": "low",
        "remote": False,
        "steps": [],
        "rollback": [],
        "params": {},
        "verify": {"metric": "dispatch", "condition": "created"},
    },
}


def runbooks_for_alarm(alarm_type: str) -> list[dict[str, Any]]:
    """Return the approved runbooks for a known alarm type."""
    if alarm_type not in SPECIFIC_PROBLEMS:
        return []
    return [
        runbook for runbook in RUNBOOKS.values()
        if alarm_type in runbook["handles"]
    ]


def validate_runbook_selection(
    runbook_id: str,
    alarm_type: str,
    params: dict[str, Any],
    network_element: str,
) -> dict[str, Any]:
    """Validate a runbook selection and its parameters before use."""
    runbook = RUNBOOKS.get(runbook_id)
    if runbook is None:
        raise ValueError(f"Unknown runbook: {runbook_id}")
    if (
        alarm_type not in SPECIFIC_PROBLEMS
        or alarm_type not in runbook["handles"]
    ):
        raise ValueError(
            f"Runbook {runbook_id} does not handle alarm type {alarm_type!r}"
        )
    if network_element not in NETWORK_ELEMENTS:
        raise ValueError(f"Unknown network element: {network_element}")
    if not isinstance(params, dict):
        raise ValueError("Runbook parameters must be an object")

    schema = runbook["params"]
    if set(params) != set(schema):
        allowed = ", ".join(sorted(schema)) or "(none)"
        raise ValueError(f"Parameters must be exactly: {allowed}")

    for name, spec in schema.items():
        value = params[name]
        if spec["type"] == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{name} must be an integer")
            if not spec["min"] <= value <= spec["max"]:
                raise ValueError(
                    f"{name} must be between {spec['min']} and {spec['max']}"
                )
        elif spec["type"] == "choice" and value not in spec["values"]:
            allowed = ", ".join(spec["values"])
            raise ValueError(f"{name} must be one of: {allowed}")

    return runbook


def render_commands(
    runbook: dict[str, Any],
    network_element: str,
    params: dict[str, Any],
    rollback: bool = False,
) -> list[str]:
    """Render only catalog-owned commands using validated values."""
    templates = runbook["rollback"] if rollback else runbook["steps"]
    return [
        command.format(network_element=network_element, **params)
        for command in templates
    ]
