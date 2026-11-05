"""
Database layer — единое соединение SQLite (singleton) через aiosqlite.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)

_db: aiosqlite.Connection | None = None
_write_lock: asyncio.Lock | None = None
_write_loop: asyncio.AbstractEventLoop | None = None


def _get_write_lock() -> asyncio.Lock:
    global _write_lock, _write_loop
    loop = asyncio.get_running_loop()
    if _write_lock is None or _write_loop != loop:
        _write_loop = loop
        _write_lock = asyncio.Lock()
    return _write_lock


def get_db_path() -> Path:
    env_path = os.getenv("BOT_DB_PATH")
    if env_path:
        return Path(env_path)
    return Path(__file__).resolve().parent.parent / "bot.db"


def get_db() -> aiosqlite.Connection:
    if _db is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _db


async def init_db() -> None:
    global _db
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _db = await aiosqlite.connect(db_path)
    _db.row_factory = aiosqlite.Row
    await _db.execute("PRAGMA journal_mode=WAL")
    await _db.execute("PRAGMA foreign_keys=ON")
    await _db.execute("PRAGMA synchronous=NORMAL")
    await _db.execute("PRAGMA busy_timeout=5000")
    await _db.executescript("""
        CREATE TABLE IF NOT EXISTS links (
            telegram_id  INTEGER PRIMARY KEY,
            gdl_user_id  INTEGER NOT NULL,
            gdl_username TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS pending_records (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id  INTEGER NOT NULL REFERENCES links(telegram_id) ON DELETE CASCADE,
            gdl_user_id  INTEGER NOT NULL,
            record_id    INTEGER NOT NULL,
            level_name   TEXT,
            percent      INTEGER,
            video_url    TEXT,
            added_at     TEXT    NOT NULL DEFAULT (datetime('now')),
            UNIQUE(telegram_id, record_id)
        );

        CREATE TABLE IF NOT EXISTS user_progress_snapshots (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id  INTEGER NOT NULL,
            gdl_user_id  INTEGER NOT NULL,
            points       REAL    NOT NULL,
            rank         INTEGER NOT NULL,
            recorded_at  TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS level_watchers (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id  INTEGER NOT NULL,
            level_id     INTEGER NOT NULL,
            level_name   TEXT    NOT NULL,
            last_placement INTEGER,
            added_at     TEXT    NOT NULL DEFAULT (datetime('now')),
            UNIQUE(telegram_id, level_id)
        );

        CREATE TABLE IF NOT EXISTS guild_members (
            chat_id      INTEGER NOT NULL,
            telegram_id  INTEGER NOT NULL,
            joined_at    TEXT    NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (chat_id, telegram_id)
        );

        CREATE TABLE IF NOT EXISTS level_placements (
            level_id       INTEGER PRIMARY KEY,
            level_name     TEXT    NOT NULL,
            placement      INTEGER NOT NULL,
            updated_at     TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS list_changelog (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            level_id       INTEGER NOT NULL,
            level_name     TEXT    NOT NULL,
            change_type    TEXT    NOT NULL, -- 'added', 'moved', 'removed'
            old_placement  INTEGER,
            new_placement  INTEGER,
            recorded_at    TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS player_subscriptions (
            chat_id        INTEGER NOT NULL,
            chat_type      TEXT    NOT NULL, -- 'private', 'group', 'supergroup'
            player_name    TEXT    NOT NULL,
            added_by       INTEGER NOT NULL,
            created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (chat_id, player_name)
        );

        CREATE TABLE IF NOT EXISTS level_subscriptions (
            chat_id        INTEGER NOT NULL,
            chat_type      TEXT    NOT NULL, -- 'private', 'group', 'supergroup'
            level_id       INTEGER NOT NULL,
            level_name     TEXT    NOT NULL,
            added_by       INTEGER NOT NULL,
            created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (chat_id, level_id)
        );

        CREATE TABLE IF NOT EXISTS player_record_hashes (
            player_name    TEXT PRIMARY KEY,
            last_record_id INTEGER,
            records_count  INTEGER,
            updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_pending_user ON pending_records(gdl_user_id);
        CREATE INDEX IF NOT EXISTS idx_pending_tg ON pending_records(telegram_id);
        CREATE INDEX IF NOT EXISTS idx_progress_tg ON user_progress_snapshots(telegram_id);
        CREATE INDEX IF NOT EXISTS idx_watchers_lvl ON level_watchers(level_id);
        CREATE INDEX IF NOT EXISTS idx_changelog_date ON list_changelog(recorded_at DESC);
        CREATE INDEX IF NOT EXISTS idx_player_sub_name ON player_subscriptions(player_name);
        CREATE INDEX IF NOT EXISTS idx_level_sub_id ON level_subscriptions(level_id);
    """)
    await _db.commit()
    logger.info("Database ready: %s", db_path)


async def close_db() -> None:
    global _db
    if _db is not None:
        await _db.close()
        _db = None


# --- Links ---

async def get_link(telegram_id: int) -> aiosqlite.Row | None:
    async with get_db().execute(
        "SELECT * FROM links WHERE telegram_id = ?", (telegram_id,)
    ) as cur:
        return await cur.fetchone()


async def _delete_all_pending_unlocked(telegram_id: int) -> None:
    await get_db().execute(
        "DELETE FROM pending_records WHERE telegram_id = ?", (telegram_id,)
    )


async def upsert_link(telegram_id: int, gdl_user_id: int, gdl_username: str) -> None:
    async with _get_write_lock():
        current = await get_link(telegram_id)
        if current and current["gdl_user_id"] != gdl_user_id:
            await _delete_all_pending_unlocked(telegram_id)

        await get_db().execute(
            """INSERT INTO links(telegram_id, gdl_user_id, gdl_username)
               VALUES(?, ?, ?)
               ON CONFLICT(telegram_id) DO UPDATE SET
                   gdl_user_id  = excluded.gdl_user_id,
                   gdl_username = excluded.gdl_username""",
            (telegram_id, gdl_user_id, gdl_username),
        )
        await get_db().commit()


async def delete_link(telegram_id: int) -> bool:
    async with _get_write_lock():
        await _delete_all_pending_unlocked(telegram_id)
        cur = await get_db().execute(
            "DELETE FROM links WHERE telegram_id = ?", (telegram_id,)
        )
        await get_db().commit()
        return cur.rowcount > 0


async def all_links() -> list[aiosqlite.Row]:
    async with get_db().execute("SELECT * FROM links") as cur:
        return await cur.fetchall()


async def count_links() -> int:
    async with get_db().execute("SELECT COUNT(*) FROM links") as cur:
        row = await cur.fetchone()
        return row[0] if row else 0


# --- Pending Records ---

async def add_pending_record(
    telegram_id: int,
    gdl_user_id: int,
    record_id: int,
    level_name: str | None,
    percent: int | None,
    video_url: str | None,
) -> None:
    async with _get_write_lock():
        await get_db().execute(
            """INSERT OR IGNORE INTO pending_records
               (telegram_id, gdl_user_id, record_id, level_name, percent, video_url)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (telegram_id, gdl_user_id, record_id, level_name, percent, video_url),
        )
        await get_db().commit()


async def get_pending_records(telegram_id: int, gdl_user_id: int) -> list[aiosqlite.Row]:
    async with get_db().execute(
        "SELECT * FROM pending_records WHERE telegram_id = ? AND gdl_user_id = ?",
        (telegram_id, gdl_user_id),
    ) as cur:
        return await cur.fetchall()


async def get_pending_by_telegram(telegram_id: int) -> list[aiosqlite.Row]:
    async with get_db().execute(
        "SELECT * FROM pending_records WHERE telegram_id = ? ORDER BY added_at DESC",
        (telegram_id,),
    ) as cur:
        return await cur.fetchall()


async def delete_pending_record(telegram_id: int, record_id: int) -> None:
    async with _get_write_lock():
        await get_db().execute(
            "DELETE FROM pending_records WHERE telegram_id = ? AND record_id = ?",
            (telegram_id, record_id),
        )
        await get_db().commit()


async def delete_all_pending(telegram_id: int) -> None:
    async with _get_write_lock():
        await _delete_all_pending_unlocked(telegram_id)
        await get_db().commit()


# --- Progress Snapshots ---

async def save_progress_snapshot(telegram_id: int, gdl_user_id: int, points: float, rank: int) -> None:
    async with _get_write_lock():
        await get_db().execute(
            """INSERT INTO user_progress_snapshots (telegram_id, gdl_user_id, points, rank)
               VALUES (?, ?, ?, ?)""",
            (telegram_id, gdl_user_id, points, rank),
        )
        await get_db().commit()


async def get_progress_snapshots(telegram_id: int, limit: int = 10) -> list[aiosqlite.Row]:
    async with get_db().execute(
        """SELECT * FROM user_progress_snapshots 
           WHERE telegram_id = ? 
           ORDER BY recorded_at DESC LIMIT ?""",
        (telegram_id, limit),
    ) as cur:
        return await cur.fetchall()


# --- Level Watchers ---

async def add_level_watcher(telegram_id: int, level_id: int, level_name: str, last_placement: int | None) -> bool:
    async with _get_write_lock():
        cur = await get_db().execute(
            """INSERT OR IGNORE INTO level_watchers (telegram_id, level_id, level_name, last_placement)
               VALUES (?, ?, ?, ?)""",
            (telegram_id, level_id, level_name, last_placement),
        )
        await get_db().commit()
        return cur.rowcount > 0


async def remove_level_watcher(telegram_id: int, level_id: int) -> bool:
    async with _get_write_lock():
        cur = await get_db().execute(
            "DELETE FROM level_watchers WHERE telegram_id = ? AND level_id = ?",
            (telegram_id, level_id),
        )
        await get_db().commit()
        return cur.rowcount > 0


async def get_level_watchers(telegram_id: int) -> list[aiosqlite.Row]:
    async with get_db().execute(
        "SELECT * FROM level_watchers WHERE telegram_id = ? ORDER BY added_at DESC",
        (telegram_id,),
    ) as cur:
        return await cur.fetchall()


async def all_watched_levels() -> list[aiosqlite.Row]:
    async with get_db().execute(
        "SELECT DISTINCT level_id, level_name, last_placement FROM level_watchers"
    ) as cur:
        return await cur.fetchall()


async def get_users_watching_level(level_id: int) -> list[aiosqlite.Row]:
    async with get_db().execute(
        "SELECT telegram_id, last_placement FROM level_watchers WHERE level_id = ?",
        (level_id,),
    ) as cur:
        return await cur.fetchall()


async def update_level_placement(level_id: int, new_placement: int) -> None:
    async with _get_write_lock():
        await get_db().execute(
            "UPDATE level_watchers SET last_placement = ? WHERE level_id = ?",
            (new_placement, level_id),
        )
        await get_db().commit()


# --- Guild Members ---

async def add_guild_member(chat_id: int, telegram_id: int) -> None:
    async with _get_write_lock():
        await get_db().execute(
            """INSERT OR IGNORE INTO guild_members (chat_id, telegram_id)
               VALUES (?, ?)""",
            (chat_id, telegram_id),
        )
        await get_db().commit()


async def get_guild_members(chat_id: int) -> list[aiosqlite.Row]:
    async with get_db().execute(
        """SELECT gm.telegram_id, l.gdl_user_id, l.gdl_username
           FROM guild_members gm
           JOIN links l ON gm.telegram_id = l.telegram_id
           WHERE gm.chat_id = ?""",
        (chat_id,),
    ) as cur:
        return await cur.fetchall()


# --- Demonlist Changelog ---

async def _record_list_change_unlocked(
    level_id: int,
    level_name: str,
    change_type: str,
    old_placement: int | None,
    new_placement: int | None,
) -> None:
    await get_db().execute(
        """INSERT INTO list_changelog (level_id, level_name, change_type, old_placement, new_placement)
           VALUES (?, ?, ?, ?, ?)""",
        (level_id, level_name, change_type, old_placement, new_placement),
    )


async def record_list_change(
    level_id: int,
    level_name: str,
    change_type: str,
    old_placement: int | None,
    new_placement: int | None,
) -> None:
    async with _get_write_lock():
        await _record_list_change_unlocked(level_id, level_name, change_type, old_placement, new_placement)
        await get_db().commit()


async def get_list_changelog(limit: int = 15) -> list[aiosqlite.Row]:
    async with get_db().execute(
        """SELECT * FROM list_changelog
           ORDER BY id DESC LIMIT ?""",
        (limit,),
    ) as cur:
        return await cur.fetchall()


async def sync_level_placements(levels_list: list[dict]) -> list[dict]:
    """
    Сверяет переданный список уровней с базой level_placements.
    Если уровень новый или изменил позицию, записывает запись в list_changelog.
    Возвращает список зафиксированных изменений в текущей итерации.
    """
    if not levels_list:
        return []

    async with _get_write_lock():
        db = get_db()
        async with db.execute("SELECT level_id, level_name, placement FROM level_placements") as cur:
            rows = await cur.fetchall()

        stored_map = {row["level_id"]: (row["level_name"], row["placement"]) for row in rows}
        is_initial_sync = (len(stored_map) == 0)

        detected_changes: list[dict] = []

        for item in levels_list:
            lvl_id = item.get("id")
            lvl_name = item.get("name") or "Unknown"
            placement = item.get("placement")
            if lvl_id is None or placement is None:
                continue

            if lvl_id not in stored_map:
                # Новый уровень
                if not is_initial_sync:
                    await _record_list_change_unlocked(
                        level_id=lvl_id,
                        level_name=lvl_name,
                        change_type="added",
                        old_placement=None,
                        new_placement=placement,
                    )
                    detected_changes.append({
                        "level_id": lvl_id,
                        "level_name": lvl_name,
                        "change_type": "added",
                        "old_placement": None,
                        "new_placement": placement,
                    })
                await db.execute(
                    """INSERT OR REPLACE INTO level_placements (level_id, level_name, placement, updated_at)
                       VALUES (?, ?, ?, datetime('now'))""",
                    (lvl_id, lvl_name, placement),
                )
            else:
                prev_name, prev_placement = stored_map[lvl_id]
                if prev_placement != placement:
                    await _record_list_change_unlocked(
                        level_id=lvl_id,
                        level_name=lvl_name,
                        change_type="moved",
                        old_placement=prev_placement,
                        new_placement=placement,
                    )
                    detected_changes.append({
                        "level_id": lvl_id,
                        "level_name": lvl_name,
                        "change_type": "moved",
                        "old_placement": prev_placement,
                        "new_placement": placement,
                    })
                    await db.execute(
                        """UPDATE level_placements
                           SET placement = ?, level_name = ?, updated_at = datetime('now')
                           WHERE level_id = ?""",
                        (placement, lvl_name, lvl_id),
                    )

        await db.commit()
        return detected_changes


# --- Subscriptions (Players & Levels) ---

async def add_player_subscription(
    chat_id: int,
    chat_type: str,
    player_name: str,
    added_by: int,
) -> bool:
    """Подписаться на игрока. Возвращает True, если подписка новая."""
    norm_name = player_name.strip()
    async with _get_write_lock():
        db = get_db()
        cur = await db.execute(
            """INSERT OR IGNORE INTO player_subscriptions (chat_id, chat_type, player_name, added_by)
               VALUES (?, ?, ?, ?)""",
            (chat_id, chat_type, norm_name, added_by),
        )
        await db.commit()
        return cur.rowcount > 0


async def remove_player_subscription(chat_id: int, player_name: str) -> bool:
    """Отписаться от игрока."""
    norm_name = player_name.strip()
    async with _get_write_lock():
        db = get_db()
        cur = await db.execute(
            "DELETE FROM player_subscriptions WHERE chat_id = ? AND LOWER(player_name) = LOWER(?)",
            (chat_id, norm_name),
        )
        await db.commit()
        return cur.rowcount > 0


async def is_subscribed_to_player(chat_id: int, player_name: str) -> bool:
    """Проверить, подписан ли чат на игрока."""
    norm_name = player_name.strip()
    async with get_db().execute(
        "SELECT 1 FROM player_subscriptions WHERE chat_id = ? AND LOWER(player_name) = LOWER(?)",
        (chat_id, norm_name),
    ) as cur:
        return await cur.fetchone() is not None


async def get_player_subscriptions(chat_id: int) -> list[aiosqlite.Row]:
    """Получить список подписок чата на игроков."""
    async with get_db().execute(
        "SELECT * FROM player_subscriptions WHERE chat_id = ? ORDER BY created_at DESC",
        (chat_id,),
    ) as cur:
        return await cur.fetchall()


async def get_all_player_subscribers(player_name: str) -> list[aiosqlite.Row]:
    """Получить все чаты, подписанные на данного игрока."""
    async with get_db().execute(
        "SELECT * FROM player_subscriptions WHERE LOWER(player_name) = LOWER(?)",
        (player_name.strip(),),
    ) as cur:
        return await cur.fetchall()


async def get_unique_tracked_players() -> list[str]:
    """Получить уникальный список отслеживаемых игроков со всего бота."""
    async with get_db().execute(
        "SELECT DISTINCT player_name FROM player_subscriptions"
    ) as cur:
        rows = await cur.fetchall()
        return [row[0] for row in rows]


async def add_level_subscription(
    chat_id: int,
    chat_type: str,
    level_id: int,
    level_name: str,
    added_by: int,
) -> bool:
    """Подписаться на уровень. Возвращает True, если подписка новая."""
    async with _get_write_lock():
        db = get_db()
        cur = await db.execute(
            """INSERT OR IGNORE INTO level_subscriptions (chat_id, chat_type, level_id, level_name, added_by)
               VALUES (?, ?, ?, ?, ?)""",
            (chat_id, chat_type, level_id, level_name.strip(), added_by),
        )
        await db.commit()
        return cur.rowcount > 0


async def remove_level_subscription(chat_id: int, level_id: int) -> bool:
    """Отписаться от уровня."""
    async with _get_write_lock():
        db = get_db()
        cur = await db.execute(
            "DELETE FROM level_subscriptions WHERE chat_id = ? AND level_id = ?",
            (chat_id, level_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def is_subscribed_to_level(chat_id: int, level_id: int) -> bool:
    """Проверить, подписан ли чат на уровень."""
    async with get_db().execute(
        "SELECT 1 FROM level_subscriptions WHERE chat_id = ? AND level_id = ?",
        (chat_id, level_id),
    ) as cur:
        return await cur.fetchone() is not None


async def get_level_subscriptions(chat_id: int) -> list[aiosqlite.Row]:
    """Получить список подписок чата на уровни."""
    async with get_db().execute(
        "SELECT * FROM level_subscriptions WHERE chat_id = ? ORDER BY created_at DESC",
        (chat_id,),
    ) as cur:
        return await cur.fetchall()


async def get_all_level_subscribers(level_id: int) -> list[aiosqlite.Row]:
    """Получить все чаты, подписанные на данный уровень."""
    async with get_db().execute(
        "SELECT * FROM level_subscriptions WHERE level_id = ?",
        (level_id,),
    ) as cur:
        return await cur.fetchall()


async def get_player_record_hash(player_name: str) -> aiosqlite.Row | None:
    async with get_db().execute(
        "SELECT * FROM player_record_hashes WHERE LOWER(player_name) = LOWER(?)",
        (player_name.strip(),),
    ) as cur:
        return await cur.fetchone()


async def update_player_record_hash(
    player_name: str, last_record_id: int | None, records_count: int
) -> None:
    async with _get_write_lock():
        db = get_db()
        await db.execute(
            """INSERT OR REPLACE INTO player_record_hashes (player_name, last_record_id, records_count, updated_at)
               VALUES (?, ?, ?, datetime('now'))""",
            (player_name.strip(), last_record_id, records_count),
        )
        await db.commit()


