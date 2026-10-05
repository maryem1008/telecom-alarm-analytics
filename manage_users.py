"""Manage engineer accounts; no public sign-up is provided."""

import argparse
import getpass
import sys

import bcrypt
import psycopg2

from remediation.repository import add_user, list_users, remove_user


def main() -> int:
    """Run the engineer account management CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    add_parser = commands.add_parser("add", help="Add an engineer account")
    add_parser.add_argument("username")
    commands.add_parser("list", help="List engineer accounts")
    remove_parser = commands.add_parser(
        "remove", help="Remove an engineer account")
    remove_parser.add_argument("username")
    args = parser.parse_args()

    if args.command == "add":
        password = getpass.getpass("Password: ")
        confirmation = getpass.getpass("Confirm password: ")
        if len(password) < 12:
            parser.error("Password must contain at least 12 characters")
        if password != confirmation:
            parser.error("Passwords do not match")
        password_hash = bcrypt.hashpw(password.encode(
            "utf-8"), bcrypt.gensalt()).decode("utf-8")
        try:
            add_user(args.username, password_hash)
        except psycopg2.errors.UniqueViolation:
            print(
                f"Engineer {args.username!r} already exists.",
                file=sys.stderr,
            )
            return 1
        print(f"Engineer {args.username!r} created.")
    elif args.command == "list":
        for username, role, created_at in list_users():
            print(f"{username}\t{role}\t{created_at}")
    elif args.command == "remove":
        if remove_user(args.username):
            print(f"Engineer {args.username!r} removed.")
        else:
            print(f"Engineer {args.username!r} was not found.",
                  file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
