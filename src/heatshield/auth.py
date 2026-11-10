"""Password login, revocable bearer sessions, and role dependencies."""

import hashlib
import hmac
import secrets
from datetime import timedelta
from typing import Annotated
from uuid import uuid4

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .database import get_session, get_settings, utcnow
from .identity import User
from .settings import Settings

PASSWORD_ITERATIONS = 600_000


def hash_password(password: str) -> str:
    if not password or len(password) > 1024:
        raise ValueError("Password length must be between 1 and 1024 characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PASSWORD_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations_text, salt_text, digest_text = encoded.split("$")
        iterations = int(iterations_text)
        salt, expected = bytes.fromhex(salt_text), bytes.fromhex(digest_text)
        if (
            algorithm != "pbkdf2_sha256"
            or not PASSWORD_ITERATIONS <= iterations <= 2_000_000
            or len(salt) != 16
            or len(expected) != 32
            or len(password) > 1024
        ):
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


_DUMMY_HASH = hash_password(secrets.token_urlsafe(32))
_bearer = HTTPBearer(auto_error=False)
router = APIRouter(prefix="/api/v1/auth", tags=["Identity"])


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=1024)


class PublicUser(BaseModel):
    id: str
    email: str
    display_name: str
    role: str
    organization_id: str
    worker_id: str | None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: PublicUser


def public_user(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role,
        "organization_id": user.organization_id,
        "worker_id": user.worker_id,
    }


def _unauthorized() -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "Invalid or expired credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


def create_access_token(user: User, settings: Settings) -> str:
    now = utcnow()
    return jwt.encode(
        {
            "sub": user.id,
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
            "iat": now,
            "nbf": now,
            "exp": now + timedelta(minutes=settings.token_lifetime_minutes),
            "jti": str(uuid4()),
            "version": user.token_version,
        },
        settings.jwt_secret,
        algorithm="HS256",
    )


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> User:
    if credentials is None:
        raise _unauthorized()
    try:
        claims = jwt.decode(
            credentials.credentials,
            settings.jwt_secret,
            algorithms=["HS256"],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": ["sub", "iss", "aud", "iat", "nbf", "exp", "jti", "version"]},
        )
    except jwt.InvalidTokenError:
        raise _unauthorized() from None
    if (
        not isinstance(claims["sub"], str)
        or not claims["sub"]
        or not isinstance(claims["jti"], str)
        or not claims["jti"]
        or type(claims["version"]) is not int
    ):
        raise _unauthorized()
    user = session.get(User, claims["sub"])
    if user is None or not user.active or user.token_version != claims["version"]:
        raise _unauthorized()
    return user


def require_roles(*roles: str):
    def check_role(user: Annotated[User, Depends(get_current_user)]) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "This role cannot perform this action")
        return user

    return check_role


@router.post("/login", response_model=TokenResponse)
def login(
    request: LoginRequest,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
):
    user = session.scalar(select(User).where(User.email == request.email.strip().lower()))
    password_valid = verify_password(request.password, user.password_hash if user else _DUMMY_HASH)
    if user is None or not password_valid or not user.active:
        raise _unauthorized()
    return {"access_token": create_access_token(user, settings), "user": public_user(user)}


@router.get("/me", response_model=PublicUser)
def me(user: Annotated[User, Depends(get_current_user)]):
    return public_user(user)


@router.post("/logout")
def logout(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
):
    # Atomic increment revokes every previously issued token for this account.
    session.execute(
        update(User).where(User.id == user.id).values(token_version=User.token_version + 1)
    )
    session.flush()
    return {"detail": "Signed out"}
