import json
import sqlite3
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from security.auth_passwords import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    hash_password,
)
from security.auth_store import SecurityStore


ENROLLMENT_CODES = {
    "ANS-A-S": ("school-a", "student"),
    "ANS-A-T": ("school-a", "teacher"),
    "FS-B-S": ("school-b", "student"),
    "FS-B-T": ("school-b", "teacher"),
}

_SEED_PATH = Path(__file__).resolve().parent.parent / "data" / "seed.json"
_PERSON_TYPE_ORDER = {"student": 0, "teacher": 1}


class RegistrationError(Exception):
    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


@dataclass(frozen=True)
class _RosterPerson:
    person_type: str
    erp_id: int
    canonical_name: str
    tenant_id: str


def normalize_full_name(full_name: str) -> str:
    return " ".join(full_name.strip().split()).casefold()


def _email_base(canonical_name: str) -> str:
    parts = []
    current = []
    for character in canonical_name.casefold():
        if character.isalnum():
            current.append(character)
        elif current:
            parts.append("".join(current))
            current = []
    if current:
        parts.append("".join(current))
    return ".".join(parts)


@lru_cache(maxsize=1)
def _load_roster() -> tuple[_RosterPerson, ...]:
    try:
        seed = json.loads(_SEED_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return ()

    roster = []
    for collection_name, person_type in (
        ("students", "student"),
        ("teachers", "teacher"),
    ):
        people = seed.get(collection_name)
        if not isinstance(people, list):
            return ()
        for raw_person in people:
            if not isinstance(raw_person, dict):
                continue
            erp_id = raw_person.get("id")
            canonical_name = raw_person.get("name")
            tenant_id = raw_person.get("schoolId")
            if (
                not isinstance(erp_id, int)
                or not isinstance(canonical_name, str)
                or not canonical_name.strip()
                or not isinstance(tenant_id, str)
                or not tenant_id.strip()
            ):
                continue
            roster.append(
                _RosterPerson(
                    person_type=person_type,
                    erp_id=erp_id,
                    canonical_name=canonical_name,
                    tenant_id=tenant_id,
                )
            )
    return tuple(roster)


def _resolve_person(
    roster: tuple[_RosterPerson, ...],
    full_name: str,
    tenant_id: str,
    role: str,
) -> _RosterPerson:
    normalized_name = normalize_full_name(full_name)
    matches = [
        person
        for person in roster
        if person.person_type == role
        and person.tenant_id == tenant_id
        and normalize_full_name(person.canonical_name) == normalized_name
    ]
    if len(matches) != 1:
        raise RegistrationError("registration_failed")
    return matches[0]


def deterministic_email(
    person: _RosterPerson,
    roster: tuple[_RosterPerson, ...],
) -> str:
    base = _email_base(person.canonical_name)
    if not base:
        raise RegistrationError("registration_failed")

    collision_group = sorted(
        (
            candidate
            for candidate in roster
            if candidate.tenant_id == person.tenant_id
            and _email_base(candidate.canonical_name) == base
        ),
        key=lambda candidate: (
            _PERSON_TYPE_ORDER[candidate.person_type],
            candidate.erp_id,
        ),
    )
    try:
        position = collision_group.index(person) + 1
    except ValueError:
        raise RegistrationError("registration_failed") from None

    suffix = "" if position == 1 else f".{position}"
    return f"{base}{suffix}@{person.tenant_id}.edu"


def generated_roster_identities() -> tuple[dict[str, str | int], ...]:
    """Return deterministic identity metadata for the immutable ERP roster."""
    roster = _load_roster()
    return tuple(
        {
            "email": deterministic_email(person, roster),
            "role": person.person_type,
            "tenant_id": person.tenant_id,
            "erp_person_id": person.erp_id,
        }
        for person in roster
    )


def register_account(
    db_path,
    *,
    full_name: str,
    password: str,
    confirm_password: str,
    school_code: str,
) -> str:
    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        raise RegistrationError("password_policy_failed")
    if password != confirm_password:
        raise RegistrationError("password_confirmation_failed")

    enrollment = ENROLLMENT_CODES.get(school_code)
    if enrollment is None:
        raise RegistrationError("registration_failed")
    tenant_id, role = enrollment

    roster = _load_roster()
    person = _resolve_person(roster, full_name, tenant_id, role)
    email = deterministic_email(person, roster)

    try:
        store = SecurityStore(db_path)
    except sqlite3.Error:
        raise RegistrationError("registration_failed") from None
    try:
        store.initialize()
        tenant = store.get_tenant(tenant_id)
        if tenant is None or tenant["status"] != "active":
            raise RegistrationError("registration_failed")
        if store.get_user_by_email(email) is not None:
            raise RegistrationError("registration_failed")

        password_hash = hash_password(password)
        store.create_user(
            email=email,
            password_hash=password_hash,
            role=role,
            tenant_id=tenant_id,
        )
    except RegistrationError:
        raise
    except (ValueError, sqlite3.Error):
        raise RegistrationError("registration_failed") from None
    finally:
        store.close()

    return email
