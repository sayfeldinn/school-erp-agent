from security.auth_sessions import SessionService


def resolve_authenticated_identity(
    authorization: str | None,
    db_path,
) -> dict[str, str] | None:
    if authorization is None:
        return None

    parts = authorization.strip().split()
    if len(parts) != 2 or parts[0].casefold() != "bearer":
        return None
    token = parts[1]
    if not token:
        return None

    service = SessionService(db_path)
    try:
        service.initialize()
        identity = service.resolve_session(token)
    finally:
        service.close()
    if identity is None:
        return None

    email = identity.get("email")
    role = identity.get("role")
    tenant_id = identity.get("tenant_id")
    if not all(isinstance(value, str) and value.strip() for value in (email, role, tenant_id)):
        return None

    return {
        "school": tenant_id.strip(),
        "role": role.strip(),
        "user": email.strip(),
    }
