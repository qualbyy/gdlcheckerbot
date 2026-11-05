"""
Точка входа. Запускает БД, API-клиент, бота и фоновый чекер.
"""

import asyncio
import logging
import os
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats
from dotenv import load_dotenv

from db.database import init_db, close_db
from services.gdl_client import gdl
from tasks.checker import checker_loop
from handlers import router
from middlewares.throttle import ThrottleMiddleware

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def setup_logging() -> None:
    """Логи только в stdout — ротацией занимается Docker (см. docker-compose.yml)."""
    fmt = logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    handler = logging.StreamHandler()
    handler.setFormatter(fmt)
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("aiogram").setLevel(logging.WARNING)


async def setup_bot_commands(bot: Bot) -> None:
    """Регистрирует подсказки команд для групп и личных сообщений."""
    # Команды для групповых чатов
    group_commands = [
        BotCommand(command="compare", description="Сравнить двух игроков Demonlist"),
        BotCommand(command="guild", description="Локальный топ участников чата"),
        BotCommand(command="player", description="Профиль игрока [ник]"),
        BotCommand(command="level", description="Информация об уровне [название/позиция]"),
        BotCommand(command="top", description="Топ-15 игроков Demonlist"),
        BotCommand(command="roulette", description="Демон-рулетка"),
        BotCommand(command="changes", description="Последние изменения топа"),
        BotCommand(command="subscriptions", description="Управление подписками группы"),
        BotCommand(command="sub", description="Подписаться на игрока или уровень"),
        BotCommand(command="help", description="Список всех доступных команд"),
    ]

    # Команды для личных сообщений
    private_commands = [
        BotCommand(command="menu", description="Главное интерактивное меню"),
        BotCommand(command="player", description="Профиль игрока [ник]"),
        BotCommand(command="level", description="Информация об уровне [название/позиция]"),
        BotCommand(command="compare", description="Сравнить двух игроков"),
        BotCommand(command="link", description="Привязать профиль Demonlist"),
        BotCommand(command="me", description="Мой профиль и статистика"),
        BotCommand(command="predict", description="Симуляция очков за уровень"),
        BotCommand(command="progress", description="Трекер личного прогресса"),
        BotCommand(command="subscriptions", description="Мои подписки на рекорды"),
        BotCommand(command="top", description="Топ-15 игроков мира"),
        BotCommand(command="changes", description="Последние изменения топа"),
        BotCommand(command="roulette", description="Демон-рулетка"),
        BotCommand(command="help", description="Помощь и список команд"),
    ]

    try:
        await bot.set_my_commands(group_commands, scope=BotCommandScopeAllGroupChats())
        await bot.set_my_commands(private_commands, scope=BotCommandScopeAllPrivateChats())
        logging.getLogger(__name__).info("Bot commands hints successfully registered for groups and private chats")
    except Exception as exc:
        logging.getLogger(__name__).warning("Failed to register bot commands hints: %s", exc)


async def main() -> None:
    setup_logging()
    logger = logging.getLogger(__name__)

    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN не задан в .env или окружении")

    await init_db()
    await gdl.start()

    proxy = os.getenv("TELEGRAM_PROXY")
    session = AiohttpSession(proxy=proxy) if proxy else None
    bot = Bot(token=token, session=session)
    dp = Dispatcher(storage=MemoryStorage())
    dp.message.middleware(ThrottleMiddleware(rate=1.5))
    dp.callback_query.middleware(ThrottleMiddleware(rate=0.8))
    dp.include_router(router)

    await setup_bot_commands(bot)

    checker_task: asyncio.Task | None = None
    try:
        checker_task = asyncio.create_task(checker_loop(bot), name="checker")
        logger.info("Bot started")
        while True:
            try:
                await dp.start_polling(bot, close_bot_session=False)
                break
            except (asyncio.CancelledError, KeyboardInterrupt):
                break
            except Exception as exc:
                logger.warning("Network/polling error (%s: %s). Reconnecting in 5 seconds...", type(exc).__name__, exc)
                await asyncio.sleep(5)
    finally:
        if checker_task:
            checker_task.cancel()
            try:
                await checker_task
            except asyncio.CancelledError:
                pass
        await gdl.close()
        await bot.session.close()
        await close_db()
        logger.info("Bot stopped cleanly")


if __name__ == "__main__":
    asyncio.run(main())
