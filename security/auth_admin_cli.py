import argparse
import getpass
import sqlite3
import sys

from security.auth_passwords import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    hash_password,
)
from security.auth_store import SecurityStore


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)

    tenant_parser = commands.add_parser("create-tenant", allow_abbrev=False)
    tenant_parser.add_argument("--db", required=True)
    tenant_parser.add_argument("--id", required=True)
    tenant_parser.add_argument("--name", required=True)

    user_parser = commands.add_parser("create-user", allow_abbrev=False)
    user_parser.add_argument("--db", required=True)
    user_parser.add_argument("--email", required=True)
    user_parser.add_argument("--role", required=True)
    user_parser.add_argument("--tenant", required=True)

    return parser


def _create_tenant(args) -> int:
    try:
        store = SecurityStore(args.db)
        try:
            store.initialize()
            store.create_tenant(args.id, args.name)
        finally:
            store.close()
    except (ValueError, sqlite3.Error):
        print("tenant could not be created", file=sys.stderr)
        return 1

    print("tenant created")
    return 0


def _create_user(args) -> int:
    try:
        password = getpass.getpass("Password:")
        confirmation = getpass.getpass("Confirm password:")
    except (EOFError, KeyboardInterrupt):
        print("password input cancelled", file=sys.stderr)
        return 1

    if password != confirmation:
        print("password confirmation did not match", file=sys.stderr)
        return 1
    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        print("password length is invalid", file=sys.stderr)
        return 1

    password_hash = hash_password(password)
    try:
        store = SecurityStore(args.db)
        try:
            store.initialize()
            store.create_user(
                email=args.email,
                password_hash=password_hash,
                role=args.role,
                tenant_id=args.tenant,
            )
        finally:
            store.close()
    except (ValueError, sqlite3.Error):
        print("user could not be created", file=sys.stderr)
        return 1

    print("user created")
    return 0


def main(argv=None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if any(
        argument == "--password" or argument.startswith("--password=")
        for argument in arguments
    ):
        print("password must be entered interactively", file=sys.stderr)
        return 2

    args = _build_parser().parse_args(arguments)
    if args.command == "create-tenant":
        return _create_tenant(args)
    return _create_user(args)


if __name__ == "__main__":
    raise SystemExit(main())
