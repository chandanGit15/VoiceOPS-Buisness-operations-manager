"""Tests for OAuth token handling and the human-in-the-loop approval flow."""

import base64
import uuid

import pytest

from app.models import Approval
from app.services import approvals, google_oauth
from app.services.google_oauth import OAuthError, TokenCrypto
from app.tools.email_tools import SendEmailTool
from app.tools.registry import tools_for

pytestmark = pytest.mark.asyncio


# ---------- Token encryption ----------


def test_token_crypto_roundtrip():
    crypto = TokenCrypto(base64.urlsafe_b64encode(b"0123456789abcdef0123456789abcdef").decode())
    encrypted = crypto.encrypt("ya29.super-secret-token")
    assert encrypted != "ya29.super-secret-token"
    assert "secret" not in encrypted
    assert crypto.decrypt(encrypted) == "ya29.super-secret-token"


def test_token_crypto_fails_closed_on_wrong_key():
    key_a = base64.urlsafe_b64encode(b"a" * 32).decode()
    key_b = base64.urlsafe_b64encode(b"b" * 32).decode()
    crypto_a, crypto_b = TokenCrypto(key_a), TokenCrypto(key_b)
    encrypted = crypto_a.encrypt("token")
    with pytest.raises(OAuthError, match="key mismatch"):
        crypto_b.decrypt(encrypted)


def test_token_crypto_requires_key():
    with pytest.raises(OAuthError, match="TOKEN_ENCRYPTION_KEY"):
        TokenCrypto("")


# ---------- OAuth flow (mocked token endpoint) ----------


async def test_exchange_and_store_tokens_encrypts(db_session_factory, monkeypatch):
    captured = {}

    async def fake_token_request(payload):
        captured["payload"] = payload
        return {
            "access_token": "ya29.plaintext-access",
            "refresh_token": "1//plaintext-refresh",
            "expires_in": 3600,
            "scope": "https://www.googleapis.com/auth/calendar.readonly",
        }

    monkeypatch.setattr(google_oauth, "_token_request", fake_token_request)
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", base64.urlsafe_b64encode(b"k" * 32).decode())
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "client-secret")
    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        async with db_session_factory() as session:
            user = await persistence_user(session)
            tokens = await google_oauth.exchange_code_for_tokens("auth-code-123")
            record = await google_oauth.store_tokens(session, user, tokens)

            # Stored encrypted, not plaintext.
            assert "ya29.plaintext-access" not in record.encrypted_access_token
            assert "1//plaintext-refresh" not in record.encrypted_refresh_token

            # get_valid_access_token decrypts to the real token.
            token = await google_oauth.get_valid_access_token(session, user)
            assert token == "ya29.plaintext-access"
    finally:
        get_settings.cache_clear()


async def test_get_valid_access_token_refreshes_when_expired(db_session_factory, monkeypatch):
    from datetime import datetime, timedelta, timezone

    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", base64.urlsafe_b64encode(b"r" * 32).decode())
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        refreshed = {}

        async def fake_refresh(refresh_token):
            refreshed["refresh_token"] = refresh_token
            return {"access_token": "ya29.new-access", "expires_in": 3600, "scope": "x"}

        monkeypatch.setattr(google_oauth, "refresh_access_token", fake_refresh)

        async with db_session_factory() as session:
            user = await persistence_user(session)
            crypto = google_oauth.get_token_crypto()
            record = google_oauth.GoogleOAuthToken(
                user_id=user.id,
                encrypted_access_token=crypto.encrypt("ya29.old"),
                encrypted_refresh_token=crypto.encrypt("1//refresh"),
                expires_at=datetime.now(timezone.utc) - timedelta(seconds=10),
                scopes=[],
            )
            session.add(record)
            await session.commit()

            token = await google_oauth.get_valid_access_token(session, user)
            assert token == "ya29.new-access"  # refreshed automatically
            assert refreshed["refresh_token"] == "1//refresh"
    finally:
        get_settings.cache_clear()


async def test_get_valid_access_token_raises_when_not_connected(db_session_factory):
    async with db_session_factory() as session:
        user = await persistence_user(session)
        with pytest.raises(google_oauth.NotConnectedError):
            await google_oauth.get_valid_access_token(session, user)


async def persistence_user(session):
    from app.services import persistence

    return await persistence.get_or_create_default_user(session)


# ---------- Approval gating (the core security property) ----------


class StubGoogleContext:
    pass


async def test_send_email_tool_does_not_send_without_approval(db_session_factory, monkeypatch):
    """send_email tool must create a pending approval, NEVER call Gmail."""

    async with db_session_factory() as session:
        user = await persistence_user(session)
        sent = {"called": False}

        class StubGmail:
            async def send_email(self, **kwargs):
                sent["called"] = True
                return {"message_id": "should-not-happen"}

        tool = SendEmailTool(trace=None)
        tool.ctx = {"session": session, "user": user}
        monkeypatch.setattr(type(tool), "gmail", property(lambda self: StubGmail()))

        result = await tool.run(to="rahul@example.com", subject="Meeting confirmed", body="See you then.")

        assert sent["called"] is False                     # Gmail never called
        assert result["awaiting_approval"] is True          # gated instead
        assert result["risk_level"] == "high"

        pending = await approvals.list_approvals(session, user.id, status="pending")
        assert len(pending) == 1
        assert pending[0].tool_name == "send_email"
        assert pending[0].payload["to"] == "rahul@example.com"


async def test_resolving_approval_executes_real_tool(db_session_factory, monkeypatch):

    async with db_session_factory() as session:
        user = await persistence_user(session)
        send_calls = []

        class StubGmail:
            async def send_email(self, **kwargs):
                send_calls.append(kwargs)
                return {"message_id": "msg-123"}

        tool = SendEmailTool(trace=None)
        tool.ctx = {"session": session, "user": user}
        monkeypatch.setattr(type(tool), "gmail", property(lambda self: StubGmail()))

        created = await tool.run(to="rahul@example.com", subject="Hi", body="Body")

        # The approval resolver creates a FRESH tool instance; patch at the
        # class level so the execution path also hits the stub.
        monkeypatch.setattr(SendEmailTool, "gmail", property(lambda self: StubGmail()))

        outcome = await approvals.resolve_approval(
            session, user, uuid.UUID(created["approval_id"]),
            decision="approved", confirmed=True,
        )

        assert outcome["executed"] is True
        assert send_calls == [{"to": "rahul@example.com", "subject": "Hi", "body": "Body", "draft_id": None}]

        approval = await approvals.get_approval(session, uuid.UUID(created["approval_id"]))
        assert approval.status == "executed"

        audit = await approvals.list_audit(session, user.id)
        events = [a.event for a in audit]
        assert "approval.created" in events and "approval.executed" in events


async def test_high_risk_requires_explicit_confirmation(db_session_factory, monkeypatch):

    async with db_session_factory() as session:
        user = await persistence_user(session)
        tool = SendEmailTool(trace=None)
        tool.ctx = {"session": session, "user": user}
        monkeypatch.setattr(
            type(tool), "gmail",
            property(lambda self: (_ for _ in ()).throw(AssertionError("must not be called"))),
        )
        created = await tool.run(to="a@b.com", subject="s", body="b")

        with pytest.raises(approvals.ApprovalError, match="explicit confirmation"):
            await approvals.resolve_approval(
                session, user, uuid.UUID(created["approval_id"]),
                decision="approved", confirmed=False,  # missing confirmation
            )


async def test_rejection_does_not_execute(db_session_factory, monkeypatch):

    async with db_session_factory() as session:
        user = await persistence_user(session)
        tool = SendEmailTool(trace=None)
        tool.ctx = {"session": session, "user": user}
        monkeypatch.setattr(
            type(tool), "gmail",
            property(lambda self: (_ for _ in ()).throw(AssertionError("must not be called"))),
        )
        created = await tool.run(to="a@b.com", subject="s", body="b")

        outcome = await approvals.resolve_approval(
            session, user, uuid.UUID(created["approval_id"]), decision="rejected"
        )
        assert outcome["executed"] is False
        approval = await approvals.get_approval(session, uuid.UUID(created["approval_id"]))
        assert approval.status == "rejected"


async def test_approval_cannot_be_resolved_twice(db_session_factory):

    async with db_session_factory() as session:
        user = await persistence_user(session)
        approval = Approval(
            user_id=user.id, action="email.send_email", tool_name="send_email",
            payload={}, risk_level="medium", summary="x",
        )
        session.add(approval)
        await session.commit()

        await approvals.resolve_approval(session, user, approval.id, decision="rejected")
        with pytest.raises(approvals.ApprovalError, match="already"):
            await approvals.resolve_approval(session, user, approval.id, decision="rejected")


def test_registry_contains_new_agents():
    assert set(tools_for("calendar")) == {
        "get_events", "check_availability", "create_event", "update_event", "delete_event",
    }
    assert set(tools_for("email")) == {
        "search_emails", "read_email", "draft_email", "send_email",
    }
