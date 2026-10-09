"""Shared pytest fixtures: async SQLite test database + authenticated app."""

import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import session as db_session
from app.main import create_app
from app.models import Base


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


# ---------- Async test database (SQLite via aiosqlite) ----------
# Alembic migrations run against real Postgres in deployment; tests create
# the same schema via Base.metadata.create_all on a disposable SQLite file.


@pytest_asyncio.fixture()
async def db_engine(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture()
async def db_session_factory(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest_asyncio.fixture()
async def patched_session_factory(db_session_factory, monkeypatch):
    """Point db_session.get_session_factory at the test database.

    NOTE: get_session_factory() must return the sessionmaker synchronously,
    so the override is a plain function, not a coroutine.
    """
    monkeypatch.setattr(
        db_session, "get_session_factory", lambda: db_session_factory
    )
    return db_session_factory


@pytest_asyncio.fixture()
async def patched_db(db_session_factory, monkeypatch):
    """App wired to the test DB, authenticated as a default test user.

    All endpoints under test run authenticated unless a test overrides
    get_current_user (e.g. to simulate a second user or no token).
    """

    from app.core.security import get_current_user as _gcu
    from app.database.session import get_session as _get_session
    from app.models import User as UserModel

    async def _get_session_override():
        async with db_session_factory() as session:
            yield session

    app = create_app()
    app.dependency_overrides[_get_session] = _get_session_override

    # Default test user row (so FK targets exist in the test database).
    async with db_session_factory() as s:
        existing = (await s.execute(
            select(UserModel).where(UserModel.email == "test@voiceops.example")
        )).scalar_one_or_none()
        if existing is None:
            user = UserModel(email="test@voiceops.example", name="Test User",
                             password_hash="pbkdf2_sha256$200000$salt$abc")
            s.add(user)
            await s.commit()
            await s.refresh(user)
        else:
            user = existing
    test_user_id = str(user.id)

    async def _current_user_override(session=None):
        return SimpleNamespace(id=uuid.UUID(test_user_id),
                               email="test@voiceops.example", name="Test User")

    app.dependency_overrides[_gcu] = _current_user_override
    app.state.test_user_id = test_user_id

    yield app
