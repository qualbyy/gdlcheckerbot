"""
Вспомогательные хендлеры: отмена и fallback для неизвестных команд.
"""

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

router = Router()


@router.message(Command("cancel"))
async def cmd_cancel(message: Message) -> None:
    await message.answer("Нечего отменять.")


@router.message()
async def fallback(message: Message) -> None:
    """Игнорируем обычный текст, подсказываем список команд только при неизвестной команде."""
    if message.text and message.text.startswith("/"):
        await message.answer("Неизвестная команда. Список: /help")
