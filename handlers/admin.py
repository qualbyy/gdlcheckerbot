"""
Админ-команды: /stats, /broadcast (/post).
"""

import asyncio
import logging
import os

from aiogram import Router, Bot
from aiogram.filters import Command
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter, TelegramAPIError
from aiogram.types import Message

from db.database import all_links, count_links, get_db, delete_link

logger = logging.getLogger(__name__)
router = Router()


def get_admin_id() -> int:
    try:
        return int(os.getenv("ADMIN_ID", "0"))
    except ValueError:
        return 0


def _is_admin(message: Message) -> bool:
    admin_id = get_admin_id()
    return bool(admin_id and message.from_user and message.from_user.id == admin_id)


async def _run_broadcast_task(bot: Bot, links: list, text: str, status_msg: Message) -> None:
    sent = 0
    failed = 0
    blocked_count = 0

    for link in links:
        tg_id = link["telegram_id"]
        try:
            await bot.send_message(tg_id, text)
            sent += 1
        except TelegramForbiddenError:
            blocked_count += 1
            await delete_link(tg_id)
        except TelegramRetryAfter as exc:
            await asyncio.sleep(min(exc.retry_after, 10))
            try:
                await bot.send_message(tg_id, text)
                sent += 1
            except TelegramForbiddenError:
                blocked_count += 1
                await delete_link(tg_id)
            except TelegramAPIError:
                failed += 1
        except TelegramAPIError:
            failed += 1
            logger.warning("Broadcast failed for %d", tg_id)

        await asyncio.sleep(0.05)

    try:
        await status_msg.edit_text(
            f"<b>Рассылка завершена</b>\n\n"
            f"Отправлено: {sent}\n"
            f"Заблокировали бота (удалены): {blocked_count}\n"
            f"Ошибок: {failed}",
            parse_mode="HTML",
        )
    except Exception:
        pass


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    if not _is_admin(message):
        return

    links_count = await count_links()

    async with get_db().execute("SELECT COUNT(*) FROM pending_records") as cur:
        row = await cur.fetchone()
        pending_count = row[0] if row else 0

    await message.answer(
        "<b>Статистика бота</b>\n\n"
        f"Привязано аккаунтов: <b>{links_count}</b>\n"
        f"Рекордов в отслеживании: <b>{pending_count}</b>",
        parse_mode="HTML",
    )


@router.message(Command("post", "broadcast"))
async def cmd_broadcast(message: Message, bot: Bot) -> None:
    if not _is_admin(message):
        return

    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2 or not args[1].strip():
        await message.answer("Использование: /post <code>текст</code>", parse_mode="HTML")
        return

    text = args[1].strip()
    if len(text) > 4096:
        await message.answer("Текст не должен превышать 4096 символов.")
        return

    links = await all_links()
    if not links:
        await message.answer("Нет привязанных пользователей.")
        return

    status_msg = await message.answer(f"Рассылка запущена в фоне ({len(links)} получателей)...")
    asyncio.create_task(_run_broadcast_task(bot, links, text, status_msg))
