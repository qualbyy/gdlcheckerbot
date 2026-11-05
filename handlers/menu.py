"""
Главное интерактивное меню (Reply + Inline FSM).
"""

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, CallbackQuery,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
    InlineKeyboardMarkup, InlineKeyboardButton,
    BufferedInputFile, InputMediaPhoto,
)

from db.database import get_link
from handlers.admin import get_admin_id
from handlers.core import cmd_link, cmd_unlink, cmd_me, cmd_check, cmd_status, cmd_help
from handlers.info import (
    cmd_player, cmd_level, cmd_levels, cmd_top,
    cmd_topcountries, cmd_random, cmd_hardest, cmd_compare
)
from handlers.features import (
    cmd_watch, cmd_unwatch, cmd_watchlist,
    cmd_roulette, cmd_changes, cmd_guild,
    cmd_predict, cmd_progress, cmd_subscriptions
)
from handlers.admin import cmd_stats, cmd_broadcast
from services.image_generator import render_home_card_async, render_section_card_async

router = Router()


class ButtonInput(StatesGroup):
    value = State()
    comparison_second = State()
    broadcast_confirm = State()


MENU_LABELS = {
    "Игроки": "players",
    "Уровни": "levels",
    "Мой аккаунт": "account",
    "Топы": "tops",
    "Отслеживание": "tracking",
    "Инструменты": "tools",
    "Главное меню": "home",
    "Помощь": "help",
    "Админ": "admin",
    "Отмена": "home",
}

INPUT_PROMPTS = {
    "info": "Введите ник игрока, чтобы открыть его профиль.",
    "hardest": "Введите ник игрока, чтобы посмотреть его самые сложные прохождения.",
    "check": "Введите ник игрока для проверки рекордов.",
    "link": "Введите ник игрока, которого хотите привязать к своему Telegram-аккаунту.",
    "level": "Введите название уровня или его позицию в топе (например: Tidal Wave или 1).",
    "country": "Введите код или название страны (например: ru, kz или Russia).",
    "range": "Введите диапазон позиций для случайного уровня (например: 1-100).",
    "compare": "Введите ник первого игрока.",
    "post": "Введите текст рассылки.",
    "watch": "Введите название или позицию уровня для добавления в отслеживаемые:",
    "unwatch": "Введите название или ID уровня для удаления из отслеживаемых:",
    "predict": "Введите название уровня (и процент через запятую, например: Tidal Wave, 100):",
    "progress": "Введите ник игрока для просмотра трекера прогресса:",
}


def main_reply_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="Игроки"), KeyboardButton(text="Уровни")],
        [KeyboardButton(text="Мой аккаунт"), KeyboardButton(text="Топы")],
        [KeyboardButton(text="Отслеживание"), KeyboardButton(text="Инструменты")],
        [KeyboardButton(text="Главное меню"), KeyboardButton(text="Помощь")],
    ]
    if user_id == get_admin_id():
        rows.append([KeyboardButton(text="Админ")])
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        input_field_placeholder="Выберите раздел меню",
    )


def inline_ui_kb(rows: list[list[tuple[str, str]]], home: bool = True) -> InlineKeyboardMarkup:
    grid = [
        [InlineKeyboardButton(text=title, callback_data=f"ui:{act}") for title, act in r]
        for r in rows
    ]
    if home:
        grid.append([InlineKeyboardButton(text="Главное меню", callback_data="ui:home")])
    return InlineKeyboardMarkup(inline_keyboard=grid)


async def show_section_menu(message: Message, section: str = "home") -> None:
    user_id = message.from_user.id if message.from_user else 0

    if section == "home":
        card_bio = await render_home_card_async()
        text = (
            "<b>Global Demonlist Checker</b>\n\n"
            "Профили игроков, уровни и уведомления о рекордах Demonlist.\n"
            "Выберите интересующий раздел:"
        )
        rows = [
            [("Игроки", "players"), ("Уровни", "levels")],
            [("Рейтинги", "tops"), ("Мой аккаунт", "account")],
            [("Отслеживание", "tracking"), ("Инструменты", "tools")],
            [("Справка и команды", "help")],
        ]
        if user_id == get_admin_id():
            rows.append([("Админ-панель", "admin")])
        home = False
    elif section == "players":
        card_bio = await render_section_card_async("Игроки")
        text = "<b>Игроки</b>\n\nКакую информацию хотите посмотреть?"
        rows = [
            [("Найти игрока", "ask:info"), ("Хардесты", "ask:hardest")],
            [("Сравнить игроков", "ask:compare"), ("Прогресс игрока", "ask:progress")],
            [("Проверить рекорды", "ask:check"), ("Топ игроков мира", "run:top")],
        ]
        home = True
    elif section == "levels":
        card_bio = await render_section_card_async("Уровни")
        text = "<b>Уровни</b>\n\nНайдите уровень или выберите случайный:"
        rows = [
            [("Найти уровень", "ask:level"), ("Список уровней", "run:levels")],
            [("Случайный уровень", "run:random"), ("Случайный из диапазона", "ask:range")],
            [("Отслеживать уровень", "ask:watch"), ("Изменения в топе", "run:changes")],
        ]
        home = True
    elif section == "tops":
        card_bio = await render_section_card_async("Рейтинги")
        text = "<b>Рейтинги</b>\n\nВыберите категорию топа:"
        rows = [
            [("Топ игроков мира", "run:top"), ("Рейтинг стран", "run:topcountries")],
            [("Игроки страны", "ask:country"), ("Изменения в топе", "run:changes")],
        ]
        home = True
    elif section == "tracking":
        card_bio = await render_section_card_async("Отслеживание")
        text = (
            "<b>Отслеживание демонов и игроков</b>\n\n"
            "Управление подписками на рекорды игроков и сдвиги уровней:"
        )
        rows = [
            [("Мои подписки", "run:subscriptions"), ("Отслеживать демон", "ask:watch")],
            [("Отслеживать уровень", "ask:level"), ("Проверить рекорды", "ask:check")],
        ]
        home = True
    elif section == "tools":
        card_bio = await render_section_card_async("Инструменты")
        text = (
            "<b>Инструменты и Челленджи</b>\n\n"
            "Дополнительные возможности бота:"
        )
        rows = [
            [("Demon Roulette", "run:roulette"), ("Симуляция очков", "ask:predict")],
            [("Изменения в топе", "run:changes"), ("Клановый лидерборд", "run:guild")],
        ]
        home = True
    elif section == "account":
        link = await get_link(user_id)
        card_bio = await render_section_card_async("Мой аккаунт")
        if link:
            text = (
                f"<b>Мой аккаунт</b>\n\n"
                f"Привязан игрок: <b>{link['gdl_username']}</b>\n"
                "Бот автоматически отслеживает новые waiting-рекорды и уведомляет о решениях."
            )
            rows = [
                [("Мой профиль", "run:me"), ("Мой прогресс", "run:progresslinked")],
                [("Мои хардесты", "run:hardestlinked"), ("Мои рекорды", "run:checklinked")],
                [("Мои отслеживаемые", "run:watchlist"), ("Симуляция прохождения", "ask:predict")],
                [("Сменить игрока", "ask:link"), ("Отвязать", "unlink")],
            ]
        else:
            text = (
                "<b>Мой аккаунт</b>\n\n"
                "Привяжите свой GDL-профиль, чтобы получать уведомления о рекордах и отслеживать прогресс."
            )
            rows = [[("Привязать игрока", "ask:link")]]
        home = True
    elif section == "admin":
        if user_id != get_admin_id():
            return
        card_bio = await render_section_card_async("Админ-панель")
        text = "<b>Панель администратора</b>"
        rows = [
            [("Статистика", "run:stats")],
            [("Рассылка", "ask:post")],
        ]
        home = True
    elif section == "help":
        card_bio = await render_section_card_async("Помощь")
        text = (
            "<b>Справка по командам</b>\n\n"
            "Используйте кнопки меню для быстрой навигации или текстовые команды.\n"
            "Полный список команд: /help"
        )
        rows = [
            [("Полная справка", "run:help")]
        ]
        home = True
    else:
        text = "Главное меню"
        card_bio = await render_home_card_async()
        rows = []
        home = False

    kb = inline_ui_kb(rows, home=home)
    is_bot_photo_msg = bool(
        message.photo
        and message.from_user
        and message.from_user.is_bot
    )

    if is_bot_photo_msg:
        try:
            await message.edit_media(
                InputMediaPhoto(
                    media=BufferedInputFile(card_bio.getvalue(), filename=f"{section}.png"),
                    caption=text,
                    parse_mode="HTML",
                ),
                reply_markup=kb,
            )
            return
        except Exception:
            pass

    await message.answer_photo(
        BufferedInputFile(card_bio.getvalue(), filename=f"{section}.png"),
        caption=text,
        reply_markup=kb,
        parse_mode="HTML",
    )


@router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    await show_section_menu(message, "home")


@router.message(F.text.in_(set(MENU_LABELS.keys())))
async def on_reply_menu_click(message: Message, state: FSMContext) -> None:
    await state.clear()
    section = MENU_LABELS.get(message.text or "", "home")
    await show_section_menu(message, section)


@router.callback_query(F.data.startswith("ui:"))
async def on_ui_callback(callback: CallbackQuery, state: FSMContext) -> None:
    action = (callback.data or "")[3:]
    user_id = callback.from_user.id
    msg = callback.message

    if not isinstance(msg, Message):
        await callback.answer()
        return

    if action in {"admin", "ask:post", "run:stats"} and user_id != get_admin_id():
        await callback.answer("Доступ запрещен.", show_alert=True)
        return

    await callback.answer()

    if action in {"home", "players", "levels", "tops", "account", "tracking", "tools", "help", "admin"}:
        await state.clear()
        await show_section_menu(msg, action)
    elif action.startswith("ask:"):
        task = action[4:]
        if task not in INPUT_PROMPTS:
            return
        await state.clear()
        await state.set_state(ButtonInput.value)
        await state.update_data(action=task)
        await msg.answer(
            INPUT_PROMPTS[task],
            reply_markup=inline_ui_kb([[("Отмена", "home")]]),
        )
    elif action.startswith("run:"):
        task = action[4:]
        await state.clear()
        if task == "levels":
            await cmd_levels(msg)
        elif task == "random":
            await cmd_random(msg)
        elif task == "top":
            await cmd_top(msg)
        elif task == "topcountries":
            await cmd_topcountries(msg)
        elif task == "me":
            await cmd_me(msg)
        elif task == "stats":
            await cmd_stats(msg)
        elif task == "watchlist":
            await cmd_watchlist(msg)
        elif task == "changes":
            await cmd_changes(msg)
        elif task == "subscriptions":
            await cmd_subscriptions(msg)
        elif task == "roulette":
            await cmd_roulette(msg)
        elif task == "guild":
            await cmd_guild(msg)
        elif task == "progresslinked":
            link = await get_link(user_id)
            if not link:
                await show_section_menu(msg, "account")
                return
            await cmd_progress(msg)
        elif task in {"hardestlinked", "checklinked"}:
            link = await get_link(user_id)
            if not link:
                await show_section_menu(msg, "account")
                return
            target_msg = msg.model_copy(update={"text": f"/{'hardest' if task == 'hardestlinked' else 'check'} {link['gdl_username']}"})
            if task == "hardestlinked":
                await cmd_hardest(target_msg)
            else:
                await cmd_check(target_msg)
        elif task == "help":
            await cmd_help(msg)
    elif action == "unlink":
        link = await get_link(user_id)
        if not link:
            await show_section_menu(msg, "account")
            return
        await state.clear()
        await msg.answer(
            f"Отвязать игрока <b>{link['gdl_username']}</b>?",
            reply_markup=inline_ui_kb([[("Да, отвязать", "unlink_confirm"), ("Отмена", "account")]]),
            parse_mode="HTML",
        )
    elif action == "unlink_confirm":
        await state.clear()
        await cmd_unlink(msg)
        await show_section_menu(msg, "account")


@router.message(ButtonInput.value, F.text)
async def on_input_value(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    action = data.get("action")
    raw = (message.text or "").strip()

    if raw in MENU_LABELS or raw.startswith("/"):
        await state.clear()
        return

    if action == "info":
        await state.clear()
        m = message.model_copy(update={"text": f"/player {raw}"})
        await cmd_player(m)
    elif action == "hardest":
        await state.clear()
        m = message.model_copy(update={"text": f"/hardest {raw}"})
        await cmd_hardest(m)
    elif action == "check":
        await state.clear()
        m = message.model_copy(update={"text": f"/check {raw}"})
        await cmd_check(m)
    elif action == "link":
        await state.clear()
        m = message.model_copy(update={"text": f"/link {raw}"})
        await cmd_link(m)
    elif action == "level":
        await state.clear()
        m = message.model_copy(update={"text": f"/level {raw}"})
        await cmd_level(m)
    elif action == "range":
        await state.clear()
        m = message.model_copy(update={"text": f"/random {raw}"})
        await cmd_random(m)
    elif action == "watch":
        await state.clear()
        m = message.model_copy(update={"text": f"/watch {raw}"})
        await cmd_watch(m)
    elif action == "unwatch":
        await state.clear()
        m = message.model_copy(update={"text": f"/unwatch {raw}"})
        await cmd_unwatch(m)
    elif action == "predict":
        await state.clear()
        m = message.model_copy(update={"text": f"/predict {raw}"})
        await cmd_predict(m)
    elif action == "progress":
        await state.clear()
        m = message.model_copy(update={"text": f"/progress {raw}"})
        await cmd_progress(m)
    elif action == "compare":
        await state.update_data(first_user=raw)
        await state.set_state(ButtonInput.comparison_second)
        await message.answer("Введите ник второго игрока для сравнения:")
    elif action == "post":
        if message.from_user and message.from_user.id == get_admin_id():
            await state.clear()
            m = message.model_copy(update={"text": f"/post {raw}"})
            await cmd_broadcast(m, message.bot)


@router.message(ButtonInput.comparison_second, F.text)
async def on_input_compare_second(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    first = data.get("first_user", "")
    second = (message.text or "").strip()
    await state.clear()
    m = message.model_copy(update={"text": f"/compare {first}, {second}"})
    await cmd_compare(m)
