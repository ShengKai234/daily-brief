import os
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from brief_db.config import database_url

LOCAL_OWNER_ID = UUID("00000000-0000-4000-8000-000000000001")
DEFAULT_TOPICS = (
    ("Stock market", "stock-market"),
    ("AI", "ai"),
    ("Information security", "information-security"),
    ("Weather", "weather"),
    ("Ruby and Python releases", "ruby-python-releases"),
    ("Travel news", "travel-news"),
)


async def seed():
    """Local seed: the owner profile (no email, no passkey) with default topics and one slot.

    Production creates nothing here: the owner is bootstrapped with `auth_admin bootstrap-owner`.
    """
    app_env = os.environ.get("APP_ENV")
    if app_env not in {"local", "production"}:
        raise ValueError("APP_ENV must be explicitly local or production")
    if app_env != "local":
        return
    engine = create_async_engine(database_url(), poolclass=NullPool, hide_parameters=True)
    try:
        async with engine.begin() as connection:
            owner = {"user_id": LOCAL_OWNER_ID}
            await connection.execute(
                text("""INSERT INTO profiles (user_id, display_name, status)
                    VALUES (:user_id, 'Local owner', 'active') ON CONFLICT DO NOTHING"""),
                owner,
            )
            await connection.execute(
                text(
                    "INSERT INTO agent_settings (user_id) VALUES (:user_id) ON CONFLICT DO NOTHING"
                ),
                owner,
            )
            for position, (title, key) in enumerate(DEFAULT_TOPICS):
                topic_id = await connection.scalar(
                    text("""INSERT INTO topics (id, owner_id, title, normalized_key)
                        VALUES (:id, :owner_id, :title, :key)
                        ON CONFLICT (owner_id, normalized_key) DO UPDATE
                        SET normalized_key = EXCLUDED.normalized_key RETURNING id"""),
                    {
                        "id": uuid5(NAMESPACE_URL, f"daily-brief:topic:{key}"),
                        "owner_id": LOCAL_OWNER_ID,
                        "title": title,
                        "key": key,
                    },
                )
                await connection.execute(
                    text("""INSERT INTO subscriptions (user_id, topic_id, position)
                        VALUES (:user_id, :topic_id, :position) ON CONFLICT DO NOTHING"""),
                    {"user_id": LOCAL_OWNER_ID, "topic_id": topic_id, "position": position},
                )
            await connection.execute(
                text("""INSERT INTO delivery_slots (id, user_id, local_time, position)
                    SELECT :id, :user_id, '07:00'::time, 1
                    WHERE NOT EXISTS (SELECT 1 FROM delivery_slots WHERE user_id = :user_id)"""),
                {"id": uuid5(NAMESPACE_URL, "daily-brief:local-slot"), "user_id": LOCAL_OWNER_ID},
            )
    finally:
        await engine.dispose()
