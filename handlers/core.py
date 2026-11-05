"""
Базовые команды: /start, /link, /unlink, /me, /linked, /help, /status, /check.
"""

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message, BufferedInputFile

from db.database import (
    get_link, upsert_link, delete_link, delete_all_pending,
    get_pending_by_telegram,
)
from services.gdl_client import gdl, APIError
from services.formatting import h, country_code, country_flag, country_display_with_flag, record_rank, validate_username
from services.image_generator import (
    render_home_card_async, render_player_card_async, render_section_card_async
)

router = Router()

MAX_RECORDS_SHOWN = 10


def _player_text(user: dict, country_pos: int | None, hardest: list[dict]) -> str:
    country_display = country_display_with_flag(user.get("country"))
    ctop = f"#{country_pos}" if country_pos is not None else "-"

    lines = [
        f"Игрок: <b>{h(user.get('username', '?'))}</b>",
        f"Страна: {h(country_display)}",
        "",
        f"Мировой топ: <b>#{h(user.get('placement') or user.get('rank', '?'))}</b>",
        f"Очки Demonlist: <b>{h(user.get('points', 0))}</b> pts",
        f"Топ страны: <b>{ctop}</b>",
    ]
    if hardest:
        lines.append("<b>Топ-5 хардестов:</b>")
        for i, rec in enumerate(hardest, 1):
            lvl = rec.get("level") or {}
            name = h(lvl.get("name", "?"))
            place = lvl.get("placement", "?")
            lines.append(f"  {i}. {name} (#{place})")
    return "\n".join(lines)


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    args = (message.text or "").split(maxsplit=1)
    if len(args) > 1:
        param = args[1].strip()
        if param.startswith("p_") or param.startswith("player_"):
            from urllib.parse import unquote
            username = unquote(param.split("_", 1)[1])
            from handlers.info import cmd_player
            m = message.model_copy(update={"text": f"/player {username}"})
            await cmd_player(m)
            return
        elif param.startswith("l_") or param.startswith("level_"):
            from urllib.parse import unquote
            lvl_param = unquote(param.split("_", 1)[1])
            from handlers.info import cmd_level, _build_level_card
            if lvl_param.isdigit():
                card_res = await _build_level_card(int(lvl_param))
                if card_res:
                    text, kb, card_bio = card_res
                    if card_bio:
                        await message.answer_photo(
                            BufferedInputFile(card_bio.getvalue(), filename="level.png"),
                            caption=text,
                            reply_markup=kb,
                            parse_mode="HTML",
                        )
                        return
            m = message.model_copy(update={"text": f"/level {lvl_param}"})
            await cmd_level(m)
            return

    from handlers.menu import show_section_menu
    await show_section_menu(message, "home")


@router.message(Command("link"))
async def cmd_link(message: Message) -> None:
    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2 or not args[1].strip():
        await message.answer("Использование: /link <code>ник</code>", parse_mode="HTML")
        return

    username = args[1].strip()
    if not validate_username(username):
        await message.answer("Некорректный ник (допустимы латиница, цифры, _ и -, до 32 символов).")
        return

    await message.answer("Поиск игрока...")
    try:
        user = await gdl.get_user(username)
    except APIError:
        await message.answer("API Demonlist временно недоступен. Попробуйте позже.")
        return

    if user is None:
        await message.answer(f"Игрок <code>{h(username)}</code> не найден на GDL.", parse_mode="HTML")
        return

    await upsert_link(
        telegram_id=message.from_user.id,
        gdl_user_id=user["id"],
        gdl_username=user.get("username") or username,
    )
    await message.answer(
        f"Аккаунт успешно привязан: <b>{h(user.get('username', username))}</b> (ID: {user['id']})\n"
        f"Бот будет отслеживать статус новых рекордов.",
        parse_mode="HTML",
    )


@router.message(Command("unlink"))
async def cmd_unlink(message: Message) -> None:
    removed = await delete_link(message.from_user.id)
    await delete_all_pending(message.from_user.id)
    if removed:
        await message.answer("Аккаунт отвязан. Отслеживание остановлено.")
    else:
        await message.answer("Аккаунт не был привязан.")


@router.message(Command("linked"))
async def cmd_linked(message: Message) -> None:
    link = await get_link(message.from_user.id)
    if not link:
        await message.answer("Аккаунт не привязан. Используйте /link <code>ник</code>", parse_mode="HTML")
        return
    card_bio = await render_section_card_async("ПРИВЯЗКА АККАУНТА")
    await message.answer_photo(
        BufferedInputFile(card_bio.getvalue(), filename="linked.png"),
        caption=f"Привязан аккаунт: <b>{h(link['gdl_username'])}</b> (ID: {link['gdl_user_id']})",
        parse_mode="HTML",
    )


@router.message(Command("me"))
async def cmd_me(message: Message) -> None:
    link = await get_link(message.from_user.id)
    if not link:
        await message.answer("Аккаунт не привязан. Используйте /link <code>ник</code>", parse_mode="HTML")
        return

    await message.answer("Загрузка профиля...")
    try:
        user = await gdl.get_user_by_id(link["gdl_user_id"], username=link["gdl_username"])
    except APIError:
        await message.answer("API временно недоступен. Попробуйте позже.")
        return

    if not user:
        await message.answer("Профиль не найден на сервере Demonlist.")
        return

    try:
        records = await gdl.get_records(link["gdl_user_id"])
    except APIError:
        records = []

    completed = [r for r in records if r.get("status") == "accepted" and r.get("percent") == 100]
    waiting_count = sum(1 for r in records if r.get("status") == "waiting")
    hardest = sorted(completed, key=record_rank)[:5]

    try:
        country_pos = await gdl.get_country_position(user)
    except APIError:
        country_pos = None

    text = _player_text(user, country_pos, hardest)
    text += f"\n\nВ ожидании проверки: <b>{waiting_count}</b>"

    country_display = country_display_with_flag(user.get("country"))
    hardest_titles = [f"{rec.get('level', {}).get('name', '?')} (#{rec.get('level', {}).get('placement', '?')})" for rec in hardest[:3]]

    card_bio = await render_player_card_async(
        username=user.get("username", link["gdl_username"]),
        rank=user.get("placement") or user.get("rank", "-"),
        points=user.get("points", 0),
        country=country_display,
        country_rank=country_pos,
        hardest=hardest_titles,
        completions_count=len(completed),
    )
    await message.answer_photo(
        BufferedInputFile(card_bio.getvalue(), filename="me.png"),
        caption=text,
        parse_mode="HTML",
    )


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    link = await get_link(message.from_user.id)
    if not link:
        await message.answer("Аккаунт не привязан. Используйте /link <code>ник</code>", parse_mode="HTML")
        return

    pending = await get_pending_by_telegram(message.from_user.id)
    if not pending:
        await message.answer(
            f"Нет активных рекордов в ожидании для <b>{h(link['gdl_username'])}</b>.\n"
            "Проверка выполняется каждые ~90 секунд.",
            parse_mode="HTML",
        )
        return

    lines = [f"<b>Отслеживаемые рекорды ({h(link['gdl_username'])}):</b>\n"]
    for row in pending[:MAX_RECORDS_SHOWN]:
        lines.append(f"- {h(row['level_name'] or '?')} — {row['percent'] or '?'}%")
    if len(pending) > MAX_RECORDS_SHOWN:
        lines.append(f"\n<i>...и еще {len(pending) - MAX_RECORDS_SHOWN}</i>")
    card_bio = await render_section_card_async("СТАТУС МОНИТОРИНГА")
    await message.answer_photo(
        BufferedInputFile(card_bio.getvalue(), filename="status.png"),
        caption="\n".join(lines),
        parse_mode="HTML",
    )


@router.message(Command("check"))
async def cmd_check(message: Message) -> None:
    args = (message.text or "").split(maxsplit=1)
    username: str | None = args[1].strip() if len(args) > 1 else None

    if not username:
        link = await get_link(message.from_user.id)
        if not link:
            await message.answer(
                "Использование: /check <code>ник</code>\nИли привяжите аккаунт через /link",
                parse_mode="HTML",
            )
            return
        gdl_user_id = link["gdl_user_id"]
        username = link["gdl_username"]
    else:
        if not validate_username(username):
            await message.answer("Некорректный ник.")
            return
        await message.answer("Поиск игрока...")
        try:
            user = await gdl.get_user(username)
        except APIError:
            await message.answer("API временно недоступен.")
            return
        if not user:
            await message.answer(f"Игрок <code>{h(username)}</code> не найден.", parse_mode="HTML")
            return
        gdl_user_id = user["id"]
        username = user.get("username") or username

    await message.answer(f"Проверка рекордов <b>{h(username)}</b>...", parse_mode="HTML")
    try:
        records = await gdl.get_records(gdl_user_id)
    except APIError:
        await message.answer("API временно недоступен. Попробуйте позже.")
        return

    if not records:
        await message.answer(f"У игрока <b>{h(username)}</b> нет рекордов на GDL.", parse_mode="HTML")
        return

    waiting = [r for r in records if r.get("status") == "waiting"]
    accepted = [r for r in records if r.get("status") == "accepted"]
    rejected = [r for r in records if r.get("status") == "rejected"]

    lines = [
        f"<b>Рекорды {h(username)}</b>\n",
        f"Ожидают: <b>{len(waiting)}</b>",
        f"Принято: <b>{len(accepted)}</b>",
        f"Отклонено: <b>{len(rejected)}</b>",
    ]
    if waiting:
        lines.append("\n<b>В очереди на проверку:</b>")
        for r in waiting[:MAX_RECORDS_SHOWN]:
            lvl = (r.get("level") or {}).get("name") or "?"
            pct = r.get("percent") or "?"
            lines.append(f"  - {h(lvl)} — {pct}%")
        if len(waiting) > MAX_RECORDS_SHOWN:
            lines.append(f"  <i>...и еще {len(waiting) - MAX_RECORDS_SHOWN}</i>")

    card_bio = await render_section_card_async("ПРОВЕРКА РЕКОРДОВ")
    await message.answer_photo(
        BufferedInputFile(card_bio.getvalue(), filename="check.png"),
        caption="\n".join(lines),
        parse_mode="HTML",
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    card_bio = await render_section_card_async("СПРАВКА И ПОМОЩЬ")
    text = (
        "<b>Global Demonlist Checker — Справка</b>\n\n"
        "<b>Аккаунт:</b>\n"
        "/link <code>ник</code> — привязать аккаунт GDL\n"
        "/unlink — отвязать • /linked — статус • /me — профиль\n"
        "/status — список отслеживаемых рекордов\n\n"
        "<b>Рекорды и игроки:</b>\n"
        "/player <code>ник</code> — карточка игрока\n"
        "/hardest <code>ник</code> — топ-10 хардестов\n"
        "/compare <code>ник1, ник2</code> — сравнение двух игроков\n"
        "/check [<code>ник</code>] — проверка статусов рекордов\n\n"
        "<b>Уровни и рейтинги:</b>\n"
        "/level <code>название</code> — карточка уровня\n"
        "/levels — список топа • /random — случайный уровень\n"
        "/top — топ игроков мира • /topcountries — рейтинг стран\n\n"
        "<b>Инструменты и мониторинг:</b>\n"
        "/progress — трекер очков и ранга • /predict — прогноз очков\n"
        "/watch — отслеживать уровень • /watchlist — список отслеживания\n"
        "/roulette — Demon Roulette • /changes — свежие изменения\n"
        "/guild — локальный рейтинг беседы • /menu — главное меню"
    )
    await message.answer_photo(
        BufferedInputFile(card_bio.getvalue(), filename="help.png"),
        caption=text,
        parse_mode="HTML",
    )
