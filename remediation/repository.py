"""Postgres persistence for remediation data, using the shared connection."""

from datetime import datetime
from pathlib import Path
from typing import Any

from psycopg2.extras import Json

from common.db import connect


def apply_schema() -> None:
    """Apply the project schema so existing databases receive new tables."""
    schema_path = Path(__file__).resolve().parents[1] / "sql" / "schema.sql"
    schema_sql = schema_path.read_text(encoding="utf-8")
    conn = connect()
    try:
        with conn.cursor() as cursor:
            cursor.execute(schema_sql)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _fetchone(sql: str, params: tuple[Any, ...]
              = ()) -> tuple[Any, ...] | None:
    conn = connect()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            result = cursor.fetchone()
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def add_user(username: str, password_hash: str) -> None:
    """Create an engineer account."""
    conn = connect()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO users (username, password_hash, role)
                VALUES (%s, %s, 'engineer')
                """,
                (username, password_hash),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_user_for_auth(username: str) -> dict[str, Any] | None:
    """Fetch only the fields needed for engineer authentication."""
    row = _fetchone(
        "SELECT username, password_hash, role, failed_attempts, locked_until "
        "FROM users WHERE username = %s",
        (username,),
    )
    if row is None:
        return None
    return {
        "username": row[0],
        "password_hash": row[1],
        "role": row[2],
        "failed_attempts": row[3],
        "locked_until": row[4],
    }


def record_failed_login(
    username: str,
    max_attempts: int,
    lock_until: datetime,
) -> tuple[int, datetime | None]:
    """Atomically increment failures and set a lockout at the threshold."""
    row = _fetchone(
        """
        UPDATE users
        SET failed_attempts = failed_attempts + 1,
            locked_until = CASE
                WHEN failed_attempts + 1 >= %s THEN %s
                ELSE locked_until
            END
        WHERE username = %s
        RETURNING failed_attempts, locked_until
        """,
        (max_attempts, lock_until, username),
    )
    if row is None:
        return 0, None
    return row[0], row[1]


def record_login_success(username: str) -> None:
    """Reset lockout counters following successful authentication."""
    _execute(
        """
        UPDATE users
        SET failed_attempts = 0, locked_until = NULL
        WHERE username = %s
        """,
        (username,),
    )


def reset_expired_lockout(username: str) -> None:
    """Reset a lockout that has expired before the next authentication try."""
    _execute(
        "UPDATE users SET failed_attempts = 0, locked_until = NULL "
        "WHERE username = %s AND locked_until <= CURRENT_TIMESTAMP",
        (username,),
    )


def list_users() -> list[tuple[str, str, datetime]]:
    """List engineer identities without password hashes."""
    conn = connect()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT username, role, created_at FROM users "
                "ORDER BY username"
            )
            return cursor.fetchall()
    finally:
        conn.close()


def remove_user(username: str) -> bool:
    """Remove one engineer account."""
    conn = connect()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "DELETE FROM users WHERE username = %s", (username,))
            removed = cursor.rowcount > 0
        conn.commit()
        return removed
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _execute(sql: str, params: tuple[Any, ...] = ()) -> int:
    conn = connect()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            rowcount = cursor.rowcount
        conn.commit()
        return rowcount
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_open_alarms(session: Any, limit: int = 300) -> list[dict[str, Any]]:
    """List recent open alarms for the engineer remediation page."""
    from remediation.auth import require_engineer

    require_engineer(session)
    conn = connect()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT alarm_id, network_element, specific_problem, severity,
                       event_time
                FROM alarms
                WHERE clear_time IS NULL
                ORDER BY CASE severity
                    WHEN 'Critical' THEN 1 WHEN 'Major' THEN 2 ELSE 3
                END,
                         event_time DESC
                LIMIT %s
                """,
                (limit,),
            )
            return [
                {
                    "alarm_id": row[0],
                    "network_element": row[1],
                    "specific_problem": row[2],
                    "severity": row[3],
                    "event_time": row[4],
                }
                for row in cursor.fetchall()
            ]
    finally:
        conn.close()


def create_remediation_action(
    session: Any,
    alarm_id: int,
    network_element: str,
    runbook_id: str,
    params: dict[str, Any],
    mode: str,
    status: str,
    output: str,
) -> int:
    """Write an engineer-authorized remediation audit record."""
    from remediation.auth import require_engineer

    username = require_engineer(session)
    conn = connect()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO remediation_actions
                    (alarm_id, network_element, username, runbook_id, params,
                     ai_suggested, mode, status, output, started_at,
                     finished_at)
                VALUES (
                    %s, %s, %s, %s, %s, FALSE, %s, %s, %s,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                RETURNING action_id
                """,
                (alarm_id, network_element, username,
                 runbook_id, Json(params), mode, status, output),
            )
            action_id = cursor.fetchone()[0]
        conn.commit()
        return action_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def create_dispatch_ticket(
    session: Any,
    alarm_id: int,
    network_element: str,
    reason: str,
) -> int:
    """Create a dispatch ticket after engineer authorization."""
    from remediation.auth import require_engineer

    username = require_engineer(session)
    conn = connect()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO dispatch_tickets
                    (alarm_id, network_element, reason, created_by)
                VALUES (%s, %s, %s, %s)
                RETURNING ticket_id
                """,
                (alarm_id, network_element, reason, username),
            )
            ticket_id = cursor.fetchone()[0]
        conn.commit()
        return ticket_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def close_dispatch_ticket(session: Any, ticket_id: int) -> None:
    """Close a dispatch ticket only for an authenticated engineer."""
    from remediation.auth import require_engineer

    require_engineer(session)
    _execute(
        """
        UPDATE dispatch_tickets
        SET status = 'closed', closed_at = CURRENT_TIMESTAMP
        WHERE ticket_id = %s AND status = 'open'
        """, (ticket_id,), )


def clear_alarm(session: Any, alarm_id: int) -> None:
    """Mark a verified alarm cleared only for an authenticated engineer."""
    from remediation.auth import require_engineer

    require_engineer(session)
    _execute(
        "UPDATE alarms SET clear_time = CURRENT_TIMESTAMP "
        "WHERE alarm_id = %s AND clear_time IS NULL",
        (alarm_id,),
    )


def get_alarm(session: Any, alarm_id: int) -> dict[str, Any] | None:
    """Read the selected alarm for an authenticated engineer."""
    from remediation.auth import require_engineer

    require_engineer(session)
    row = _fetchone(
        """
        SELECT alarm_id, network_element, specific_problem, severity,
               event_time, clear_time
        FROM alarms WHERE alarm_id = %s
        """, (alarm_id,), )
    if row is None:
        return None
    return {
        "alarm_id": row[0],
        "network_element": row[1],
        "specific_problem": row[2],
        "severity": row[3],
        "event_time": row[4],
        "clear_time": row[5],
    }
