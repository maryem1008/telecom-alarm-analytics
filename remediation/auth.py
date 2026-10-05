"""Engineer authentication and session authorization."""

import logging
from datetime import datetime, timedelta
from typing import Any

import bcrypt

from common.constants import (
    ENGINEER_LOCKOUT_MINUTES,
    ENGINEER_MAX_FAILED_LOGINS,
    ENGINEER_SESSION_TIMEOUT_MINUTES,
)
from remediation import repository

logger = logging.getLogger(__name__)


def require_engineer(session: Any, now: datetime | None = None) -> str:
    """Authorize and refresh an active engineer session."""
    now = now or datetime.utcnow()
    if not hasattr(session, "get"):
        raise PermissionError("Engineer login required")

    username = session.get("engineer_username")
    role = session.get("engineer_role")
    last_activity = session.get("engineer_last_activity")
    valid_session = (
        role == "engineer"
        and isinstance(username, str)
        and isinstance(last_activity, datetime)
    )
    if not valid_session:
        raise PermissionError("Engineer login required")

    timeout = timedelta(minutes=ENGINEER_SESSION_TIMEOUT_MINUTES)
    if now - last_activity >= timeout:
        for key in (
            "engineer_username",
            "engineer_role",
            "engineer_last_activity",
        ):
            session.pop(key, None)
        raise PermissionError("Engineer session expired; log in again")

    session["engineer_last_activity"] = now
    return username


def login(
    username: str,
    password: str,
    session: Any,
    now: datetime | None = None,
) -> tuple[bool, str]:
    """Authenticate an engineer and record success, failures, and lockouts."""
    now = now or datetime.utcnow()
    user = repository.get_user_for_auth(username)
    if user is None:
        logger.warning(
            "Engineer login failed for unknown username %r",
            username,
        )
        return False, "Invalid username or password"

    locked_until = user.get("locked_until")
    if locked_until is not None and locked_until > now:
        logger.warning("Engineer login blocked by lockout for %r", username)
        message = f"Account locked until {locked_until:%Y-%m-%d %H:%M:%S} UTC"
        return False, message
    if locked_until is not None:
        repository.reset_expired_lockout(username)

    valid_password = bcrypt.checkpw(
        password.encode("utf-8"), user["password_hash"].encode("utf-8")
    )
    if not valid_password:
        failed_attempts, new_locked_until = repository.record_failed_login(
            username,
            ENGINEER_MAX_FAILED_LOGINS,
            now + timedelta(minutes=ENGINEER_LOCKOUT_MINUTES),
        )
        logger.warning(
            "Engineer login failed for %r (attempt %s)",
            username,
            failed_attempts,
        )
        if new_locked_until is not None:
            logger.error(
                "Engineer account locked after failed logins: %r", username
            )
            message = (
                f"Account locked until "
                f"{new_locked_until:%Y-%m-%d %H:%M:%S} UTC"
            )
            return False, message
        return False, "Invalid username or password"

    repository.record_login_success(username)
    session["engineer_username"] = username
    session["engineer_role"] = user["role"]
    session["engineer_last_activity"] = now
    logger.warning("Engineer login succeeded for %r", username)
    return True, "Logged in"


def logout(session: Any) -> None:
    """Remove engineer credentials from the current Streamlit session."""
    username = session.get("engineer_username")
    if username:
        logger.warning("Engineer logged out: %r", username)
    for key in (
        "engineer_username",
        "engineer_role",
        "engineer_last_activity",
    ):
        session.pop(key, None)
