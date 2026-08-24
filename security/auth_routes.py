from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, ValidationError

from security.auth_login import LoginService
from security.auth_registration import RegistrationError, register_account
from security.auth_sessions import SessionService
from security.auth_throttle import LoginThrottle


class _LoginRequest(BaseModel):
    email: str
    password: str


class _RegistrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: str
    password: str
    confirm_password: str
    school_code: str


def _extract_bearer(authorization: str | None) -> str | None:
    if authorization is None:
        return None
    parts = authorization.strip().split()
    if len(parts) != 2 or parts[0].casefold() != "bearer":
        return None
    return parts[1] or None


def create_auth_router(db_path, throttle=None) -> APIRouter:
    router = APIRouter()
    limiter = throttle if throttle is not None else LoginThrottle()

    @router.post("/auth/register", status_code=201)
    def register(raw_payload: dict):
        try:
            payload = _RegistrationRequest.model_validate(raw_payload)
        except ValidationError:
            raise HTTPException(
                status_code=400,
                detail="registration_failed",
            ) from None

        try:
            email = register_account(
                db_path,
                full_name=payload.full_name,
                password=payload.password,
                confirm_password=payload.confirm_password,
                school_code=payload.school_code,
            )
        except RegistrationError as exc:
            raise HTTPException(status_code=400, detail=exc.detail) from None

        return {"status": "created", "email": email}

    @router.post("/auth/login")
    def login(payload: _LoginRequest, request: Request):
        client_ip = request.client.host if request.client is not None else "local-client"
        if not limiter.allow_attempt(payload.email, client_ip):
            raise HTTPException(status_code=429, detail="too_many_attempts")

        token = LoginService(db_path).authenticate(payload.email, payload.password)
        if token is None:
            limiter.record_failure(payload.email)
            raise HTTPException(status_code=401, detail="authentication_failed")

        limiter.record_success(payload.email)
        return {"access_token": token, "token_type": "bearer"}

    @router.get("/auth/me")
    def me(request: Request):
        token = _extract_bearer(request.headers.get("authorization"))
        if token is None:
            raise HTTPException(status_code=401, detail="authentication_required")

        service = SessionService(db_path)
        try:
            service.initialize()
            identity = service.resolve_session(token)
        finally:
            service.close()
        if identity is None:
            raise HTTPException(status_code=401, detail="authentication_required")

        return {
            "email": identity["email"],
            "role": identity["role"],
            "tenant_id": identity["tenant_id"],
        }

    @router.post("/auth/logout")
    def logout(request: Request):
        token = _extract_bearer(request.headers.get("authorization"))
        if token is None:
            raise HTTPException(status_code=401, detail="authentication_required")

        service = SessionService(db_path)
        try:
            service.initialize()
            identity = service.resolve_session(token)
            if identity is None:
                raise HTTPException(status_code=401, detail="authentication_required")
            service.revoke_session(token)
        finally:
            service.close()

        return {"status": "ok"}

    return router
