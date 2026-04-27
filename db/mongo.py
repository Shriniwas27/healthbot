"""
MongoDB Connection Manager
==========================
Uses Motor (async MongoDB driver) to manage a single shared client.
"""

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from utils.logger import get_logger
from config import get_settings

logger   = get_logger(__name__)
settings = get_settings()

_client: AsyncIOMotorClient | None = None
_db: AsyncIOMotorDatabase | None   = None


async def connect_db() -> None:
    global _client, _db

    logger.info("Connecting to MongoDB...")
    _client = AsyncIOMotorClient(
        settings.mongodb_uri,
        serverSelectionTimeoutMS=5000,
    )
    await _client.admin.command("ping")
    _db = _client[settings.mongodb_db_name]
    logger.info(f"MongoDB connected — database: '{settings.mongodb_db_name}'")
    await _ensure_indexes(_db)


async def close_db() -> None:
    global _client, _db
    if _client:
        _client.close()
        _client = None
        _db     = None
        logger.info("MongoDB connection closed.")


async def get_database() -> AsyncIOMotorDatabase:
    if _db is None:
        raise RuntimeError(
            "Database not initialised. "
            "Ensure connect_db() is called during FastAPI startup."
        )
    return _db


async def _ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    """Create indexes on first run (idempotent)."""
    try:
        # sessions
        await db["sessions"].create_index("session_id", unique=True)
        await db["sessions"].create_index("user_id")
        await db["sessions"].create_index("last_active")

        # appointments
        await db["appointments"].create_index("user_id")
        await db["appointments"].create_index("status")
        await db["appointments"].create_index("scheduled_at")
        await db["appointments"].create_index("reminder_sent")

        # users
        await db["users"].create_index("user_id", unique=True)
        await db["users"].create_index("email",   unique=True)
        await db["users"].create_index("phone")

        logger.info("MongoDB indexes verified.")
    except Exception as e:
        logger.warning(f"Index creation warning (non-fatal): {e}")