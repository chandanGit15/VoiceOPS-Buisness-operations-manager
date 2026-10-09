"""
Development seed script.

Creates the dev user, a sample conversation with messages, a few tasks,
and long-term memories so the UI has data immediately.

Usage (from backend/):
    python scripts/seed_db.py          # requires a migrated database
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # import `app`

from app.core.logging import configure_logging  # noqa: E402
from app.database.session import get_session_factory  # noqa: E402
from app.services import persistence, task_store  # noqa: E402


async def seed() -> None:
    configure_logging()
    factory = get_session_factory()

    async with factory() as session:
        user = await persistence.get_or_create_default_user(session)
        print(f"✓ dev user ready: {user.email}")

        conversation = await persistence.create_conversation(
            session, user.id, "Welcome to VoiceOps"
        )
        await persistence.append_message(
            session, conversation.id, "user", "Hello VoiceOps!"
        )
        await persistence.append_message(
            session, conversation.id, "model",
            "Hi! I can check the weather, search the web, and manage tasks. "
            "This conversation is stored in PostgreSQL.",
        )
        print(f"✓ sample conversation created: {conversation.id}")

        await persistence.add_memory(
            session, user_id=user.id, memory_type="preference",
            content="Prefers concise, voice-friendly answers without markdown.",
        )
        await persistence.add_memory(
            session, user_id=user.id, memory_type="fact",
            content="Lives in Bengaluru and cares about rain forecasts.",
        )
        print("✓ long-term memories added")

        await task_store.create_task(
            session, user_id=user.id, title="Explore the VoiceOps dashboard",
            description="Try the chat, tasks, and agent activity panels.",
            priority=2,
        )
        await task_store.create_task(
            session, user_id=user.id, title="Add GEMINI_API_KEY to .env",
            description="Required for real agent responses.",
            priority=1,
        )
        print("✓ sample tasks created")

    print("\nSeed complete. Start the backend and open the dashboard.")


if __name__ == "__main__":
    asyncio.run(seed())
