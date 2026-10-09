"""User authentication endpoints (Part 8): register + login."""

import logging
import re

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, hash_password, verify_password
from app.database.session import get_session
from app.models import User

logger = logging.getLogger("app.api.auth_users")

router = APIRouter(prefix="/auth", tags=["auth"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class RegisterIn(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=8, max_length=128)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


@router.post("/register", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterIn, session: AsyncSession = Depends(get_session)) -> TokenOut:
    """Create an account and return an access token immediately."""
    if not _EMAIL_RE.match(payload.email):
        raise HTTPException(status_code=422, detail={
            "code": "validation_error", "message": "Invalid email address.",
        })

    existing = (await session.execute(
        select(User).where(User.email == payload.email.lower())
    )).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail={
            "code": "email_taken", "message": "An account with this email already exists.",
        })

    user = User(
        email=payload.email.lower(),
        name=payload.name.strip(),
        password_hash=hash_password(payload.password),
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    logger.info("user registered: %s", user.email)

    return TokenOut(
        access_token=create_access_token(user),
        user={"id": str(user.id), "email": user.email, "name": user.name},
    )


@router.post("/login", response_model=TokenOut)
async def login(payload: LoginIn, session: AsyncSession = Depends(get_session)) -> TokenOut:
    """Verify credentials and return an access token (generic error on failure)."""
    user = (await session.execute(
        select(User).where(User.email == payload.email.lower())
    )).scalar_one_or_none()

    # Same generic message for unknown email and wrong password (no enumeration).
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail={
            "code": "invalid_credentials", "message": "Invalid email or password.",
        })

    return TokenOut(
        access_token=create_access_token(user),
        user={"id": str(user.id), "email": user.email, "name": user.name},
    )
