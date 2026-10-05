"""Unit tests for engineer-only simulated remediation."""

import os
import sys
from datetime import datetime, timedelta

import bcrypt
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from remediation import auth, executor, repository  # noqa: E402
from remediation.catalog import (  # noqa: E402
    RUNBOOKS,
    validate_runbook_selection,
)
from remediation.executor import SimulatedExecutor  # noqa: E402


@pytest.fixture
def engineer_session():
    return {
        "engineer_username": "operator",
        "engineer_role": "engineer",
        "engineer_last_activity": datetime.utcnow(),
    }


def test_require_engineer_rejects_missing_and_expired_sessions():
    with pytest.raises(PermissionError):
        auth.require_engineer({})

    expired = {
        "engineer_username": "operator",
        "engineer_role": "engineer",
        "engineer_last_activity": datetime.utcnow() - timedelta(minutes=31),
    }
    with pytest.raises(PermissionError, match="expired"):
        auth.require_engineer(expired)
    assert "engineer_username" not in expired


def test_require_engineer_refreshes_activity(engineer_session):
    now = datetime.utcnow()
    assert auth.require_engineer(engineer_session, now) == "operator"
    assert engineer_session["engineer_last_activity"] == now


def test_login_verifies_bcrypt_hash_and_establishes_session(monkeypatch):
    password_hash = bcrypt.hashpw(
        b"correct-horse-battery", bcrypt.gensalt()).decode()
    monkeypatch.setattr(
        auth.repository,
        "get_user_for_auth",
        lambda username: {
            "username": username,
            "password_hash": password_hash,
            "role": "engineer",
            "failed_attempts": 0,
            "locked_until": None,
        },
    )
    success_calls = []
    monkeypatch.setattr(
        auth.repository,
        "record_login_success",
        lambda username: success_calls.append(username))

    session = {}
    success, message = auth.login("operator", "correct-horse-battery", session)

    assert success
    assert message == "Logged in"
    assert session["engineer_role"] == "engineer"
    assert success_calls == ["operator"]


def test_login_locks_after_five_failed_attempts(monkeypatch):
    password_hash = bcrypt.hashpw(
        b"right-password", bcrypt.gensalt()
    ).decode()
    failures = {"count": 0, "locked_until": None}

    def get_user(_username):
        return {
            "username": "operator",
            "password_hash": password_hash,
            "role": "engineer",
            "failed_attempts": failures["count"],
            "locked_until": failures["locked_until"],
        }

    def record_failure(_username, max_attempts, lock_until):
        failures["count"] += 1
        if failures["count"] >= max_attempts:
            failures["locked_until"] = lock_until
        return failures["count"], failures["locked_until"]

    monkeypatch.setattr(auth.repository, "get_user_for_auth", get_user)
    monkeypatch.setattr(
        auth.repository, "record_failed_login", record_failure
    )
    session = {}
    now = datetime.utcnow()

    for _ in range(5):
        success, _message = auth.login(
            "operator", "wrong-password", session, now)
        assert not success

    success, message = auth.login("operator", "right-password", session, now)
    assert not success
    assert "locked" in message
    assert "engineer_username" not in session


@pytest.mark.parametrize(
    "runbook_id,alarm_type,params,element,match",
    [
        ("invented", "Heartbeat", {}, "BEN_KHAL2", "Unknown runbook"),
        (
            "heartbeat-agent-restart",
            "Battery Low",
            {},
            "BEN_KHAL2",
            "does not handle",
        ),
        (
            "loss-signal-radio-reset",
            "Loss Of Signal",
            {"port": 4},
            "BEN_KHAL2",
            "between",
        ),
    ],
)
def test_catalog_rejects_invalid_selections(
        runbook_id, alarm_type, params, element, match):
    with pytest.raises(ValueError, match=match):
        validate_runbook_selection(runbook_id, alarm_type, params, element)


def test_dry_run_executes_nothing_and_requires_engineer(engineer_session):
    simulated = SimulatedExecutor()
    runbook = RUNBOOKS["heartbeat-agent-restart"]

    result = simulated.dry_run(engineer_session, runbook, "BEN_KHAL2", {})

    assert result.success
    assert "no commands executed" in result.output
    assert not simulated._snapshots
    with pytest.raises(PermissionError):
        simulated.dry_run({}, runbook, "BEN_KHAL2", {})


def test_simulated_rollback_restores_previous_state(engineer_session):
    simulated = SimulatedExecutor()
    runbook = RUNBOOKS["vswr-power-reduction"]
    params = {"sector": "S1", "reduction_db": 2}

    simulated.execute(engineer_session, runbook, "BEN_KHAL2", params)
    result = simulated.rollback(
        engineer_session, runbook, "BEN_KHAL2", params
    )

    assert result.success
    assert "previous simulated state restored" in result.output
    assert not simulated._snapshots
    with pytest.raises(PermissionError):
        simulated.rollback({}, runbook, "BEN_KHAL2", params)


def test_non_remote_alarm_creates_dispatch_ticket(
        engineer_session, monkeypatch):
    calls = []
    monkeypatch.setattr(
        executor,
        "create_dispatch_ticket",
        lambda session, alarm_id, network_element, reason: (
            calls.append((alarm_id, network_element, reason)) or 42
        ),
    )

    ticket_id = executor.dispatch_non_remote_alarm(
        engineer_session, 17, "BEN_KHAL2", "Power Supply Failure"
    )

    assert ticket_id == 42
    assert calls == [
        (17, "BEN_KHAL2", "Non-remote alarm: Power Supply Failure")]
    with pytest.raises(PermissionError):
        executor.dispatch_non_remote_alarm(
            {}, 17, "BEN_KHAL2", "Power Supply Failure")


def test_repository_rejects_anonymous_dispatch_ticket_before_database(
        monkeypatch):
    monkeypatch.setattr(
        repository,
        "connect",
        lambda: pytest.fail(
            "database must not be reached before authorization"),
    )
    with pytest.raises(PermissionError):
        repository.create_dispatch_ticket({}, 17, "BEN_KHAL2", "test")
