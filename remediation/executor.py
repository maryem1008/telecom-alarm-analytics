"""Simulated command execution, verification, and rollback."""

from dataclasses import dataclass
from typing import Any, Protocol

from remediation.auth import require_engineer
from remediation.catalog import (
    RUNBOOKS,
    render_commands,
    validate_runbook_selection,
)
from remediation.repository import create_dispatch_ticket


@dataclass(frozen=True)
class ExecutionResult:
    """Result of simulated execution or verification."""

    success: bool
    output: str


class Executor(Protocol):
    """Interface for a future equipment executor."""

    def execute(
        self,
        session: Any,
        runbook: dict[str, Any],
        network_element: str,
        params: dict[str, Any],
    ) -> ExecutionResult:
        """Run an approved catalog runbook."""

    def dry_run(
        self,
        session: Any,
        runbook: dict[str, Any],
        network_element: str,
        params: dict[str, Any],
    ) -> ExecutionResult:
        """Render commands without running them."""

    def rollback(
        self,
        session: Any,
        runbook: dict[str, Any],
        network_element: str,
        params: dict[str, Any],
    ) -> ExecutionResult:
        """Restore the state recorded before a simulated execution."""


class SimulatedExecutor:
    """Return realistic-looking output without connecting to equipment."""

    def __init__(self) -> None:
        self._snapshots: dict[tuple[str, str], dict[str, Any]] = {}

    @staticmethod
    def _validate(
        runbook: dict[str, Any],
        network_element: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        canonical = RUNBOOKS.get(runbook.get("id"))
        if canonical is None:
            raise ValueError("Unknown runbook")
        return validate_runbook_selection(
            canonical["id"], canonical["handles"][0], params, network_element
        )

    def dry_run(
        self,
        session: Any,
        runbook: dict[str, Any],
        network_element: str,
        params: dict[str, Any],
    ) -> ExecutionResult:
        require_engineer(session)
        runbook = self._validate(runbook, network_element, params)
        if not runbook["remote"]:
            raise ValueError("Non-remote alarms must use a dispatch ticket")
        commands = render_commands(runbook, network_element, params)
        output = (
            "DRY RUN — no commands executed:\n"
            + "\n".join(commands)
        )
        return ExecutionResult(
            True,
            output,
        )

    def execute(
        self,
        session: Any,
        runbook: dict[str, Any],
        network_element: str,
        params: dict[str, Any],
    ) -> ExecutionResult:
        username = require_engineer(session)
        runbook = self._validate(runbook, network_element, params)
        if not runbook["remote"]:
            raise ValueError("Non-remote alarms must use a dispatch ticket")
        key = (network_element, runbook["id"])
        self._snapshots[key] = dict(params)
        commands = render_commands(runbook, network_element, params)
        output = [
            "SIMULATED EXECUTION — no network equipment was contacted.",
            f"Engineer: {username}",
        ]
        output.extend(
            f"$ {command}\nSIMULATED: completed successfully"
            for command in commands
        )
        return ExecutionResult(True, "\n".join(output))

    def verify(
        self,
        session: Any,
        runbook: dict[str, Any],
        network_element: str,
        params: dict[str, Any],
    ) -> ExecutionResult:
        require_engineer(session)
        runbook = self._validate(runbook, network_element, params)
        if not runbook["remote"]:
            raise ValueError("Non-remote alarms must use a dispatch ticket")
        metric = runbook["verify"]["metric"]
        condition = runbook["verify"]["condition"]
        if runbook["id"] == "vswr-power-reduction":
            threshold = runbook["verify"]["value"]
            return ExecutionResult(
                True,
                f"SIMULATED verification: VSWR 1.55 < threshold {threshold}.",
            )
        return ExecutionResult(
            True, f"SIMULATED verification: {metric} is {condition}.")

    def rollback(
        self,
        session: Any,
        runbook: dict[str, Any],
        network_element: str,
        params: dict[str, Any],
    ) -> ExecutionResult:
        require_engineer(session)
        runbook = self._validate(runbook, network_element, params)
        if not runbook["remote"]:
            raise ValueError("Non-remote alarms must use a dispatch ticket")
        key = (network_element, runbook["id"])
        if key not in self._snapshots:
            return ExecutionResult(
                False, "No simulated pre-execution snapshot is available.")
        commands = render_commands(
            runbook, network_element, params, rollback=True)
        del self._snapshots[key]
        return ExecutionResult(
            True,
            "SIMULATED ROLLBACK — previous simulated state restored:\n"
            + "\n".join(commands),
        )


def dispatch_non_remote_alarm(
    session: Any,
    alarm_id: int,
    network_element: str,
    alarm_type: str,
) -> int:
    """Create a field ticket instead of attempting a non-remote fix."""
    require_engineer(session)
    matching = [
        runbook for runbook in RUNBOOKS.values()
        if alarm_type in runbook["handles"] and not runbook["remote"]
    ]
    if not matching:
        raise ValueError(
            f"No non-remote runbook exists for alarm type {alarm_type!r}")
    validate_runbook_selection(
        matching[0]["id"], alarm_type, {}, network_element
    )
    return create_dispatch_ticket(
        session,
        alarm_id,
        network_element,
        f"Non-remote alarm: {alarm_type}",
    )
