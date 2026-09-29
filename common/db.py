"""
Shared Postgres connection helper.

Reads connection settings from environment variables (POSTGRES_HOST,
POSTGRES_PORT, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD,
POSTGRES_SSLMODE). If a `.env` file exists at the project root, it is
loaded automatically — this is what lets `run_local.py` and every script
work the same way whether you're pointed at a Postgres you installed
yourself or a free hosted instance (Neon, Supabase, etc.), with no Docker
involved either way.

Retries briefly on connection failure so a script started a beat before
Postgres has finished accepting connections doesn't just crash.
"""

import os
import time

import psycopg2

try:
    from dotenv import load_dotenv
    _ROOT = os.path.join(os.path.dirname(__file__), "..")
    load_dotenv(os.path.join(_ROOT, ".env"))
except ImportError:
    pass  # python-dotenv is optional; env vars set another way still work


def get_pg_config():
    return {
        "host": os.environ.get("POSTGRES_HOST", "localhost"),
        "port": int(os.environ.get("POSTGRES_PORT", 5432)),
        "dbname": os.environ.get("POSTGRES_DB", "telecom"),
        "user": os.environ.get("POSTGRES_USER", "telecom"),
        "password": os.environ.get("POSTGRES_PASSWORD", "telecom"),
        "sslmode": os.environ.get("POSTGRES_SSLMODE", "prefer"),
    }


def connect(retries=5, delay_seconds=2, **overrides):
    """Connect to Postgres, retrying briefly if it isn't ready yet.

    Any of host/port/dbname/user/password/sslmode can be overridden via
    kwargs; everything else falls back to `get_pg_config()`.
    """
    config = get_pg_config()
    config.update({k: v for k, v in overrides.items() if v is not None})

    last_error = None
    for attempt in range(1, retries + 1):
        try:
            return psycopg2.connect(**config)
        except psycopg2.OperationalError as exc:
            last_error = exc
            if attempt < retries:
                print(f"  Postgres not ready yet (attempt {attempt}/{retries}) — retrying...")
                time.sleep(delay_seconds)
    raise ConnectionError(
        f"Could not connect to Postgres at {config['host']}:{config['port']}/{config['dbname']} "
        f"after {retries} attempts. Check your .env file. Original error: {last_error}"
    )


def jdbc_url_and_props():
    """Connection info for PySpark's JDBC reader/writer."""
    config = get_pg_config()
    url = f"jdbc:postgresql://{config['host']}:{config['port']}/{config['dbname']}"
    props = {
        "user": config["user"],
        "password": config["password"],
        "driver": "org.postgresql.Driver",
    }
    return url, props
