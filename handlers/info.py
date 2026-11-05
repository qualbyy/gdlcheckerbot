"""
Команды информации: /player, /level, /levels, /top, /topcountries,
/random, /hardest, /compare + callback-пагинация и inline-поиск.
"""

import asyncio
import os
import random as pyrandom
from uuid import uuid4

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery, InlineQuery,
    InlineQueryResultArticle, InlineQueryResultPhoto, InputTextMessageContent,
    InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile,
    InputMediaPhoto,
)

from db.database import (
    get_link, add_level_watcher,
    is_subscribed_to_player, add_player_subscription, remove_player_subscription,
    is_subscribed_to_level, add_level_subscription, remove_level_subscription,
)
from services.gdl_client import gdl, APIError
from services.formatting import (
    h, safe_url, country_code, country_flag, country_display_with_flag,
    youtube_thumb, record_rank, fmt_date, validate_username
)
from services.image_generator import (
    render_player_card_async, render_level_card_async,
    render_section_card_async, render_compare_card_async,
)

router = Router()

LEVELS_PER_PAGE = 15
TOP_PER_PAGE = 15
COUNTRIES_PER_PAGE = 20
BOT_USERNAME = os.getenv("BOT_USERNAME", "gdlcheckerbot").lstrip("@")


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _url_btn(text: str, url: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, url=url)


async def _edit_or_answer(target: Message | CallbackQuery, text: str, **kwargs) -> None:
    if isinstance(target, CallbackQuery):
        if target.message:
            try:
                await target.message.edit_text(text, **kwargs)
            except Exception:
                pass
        await target.answer()
    else:
        await target.answer(text, **kwargs)


async def _edit_or_answer_with_photo(
    target: Message | CallbackQuery,
    photo_bio,
    text: str,
    filename: str = "card.png",
    reply_markup: InlineKeyboardMarkup | None = None,
    **kwargs,
) -> None:
    if isinstance(target, CallbackQuery):
        if target.message:
            try:
                if target.message.photo:
                    await target.message.edit_caption(
                        caption=text,
                        reply_markup=reply_markup,
                        parse_mode="HTML",
                        **kwargs,
                    )
                else:
                    await target.message.edit_media(
                        InputMediaPhoto(
                            media=BufferedInputFile(photo_bio.getvalue(), filename=filename),
                            caption=text,
                            parse_mode="HTML",
                        ),
                        reply_markup=reply_markup,
                        **kwargs,
                    )
            except Exception:
                pass
        await target.answer()
    else:
        await target.answer_photo(
            BufferedInputFile(photo_bio.getvalue(), filename=filename),
            caption=text,
            reply_markup=reply_markup,
            parse_mode="HTML",
            **kwargs,
        )


async def _is_group_admin(bot, chat_id: int, user_id: int) -> bool:
    """Проверка, является ли пользователь админом или создателем чата."""
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in ("creator", "administrator")
    except Exception:
        return False


async def _build_level_card(
    level_id: int,
    chat_id: int | None = None,
) -> tuple[str, InlineKeyboardMarkup | None, BytesIO | None] | None:
    try:
        info = await gdl.get_level_info(level_id)
    except APIError:
        return None
    if not info or not isinstance(info, dict):
        return None

    victors = await gdl.get_level_victors_count(level_id)
    list_pct = info.get("list_percent", 100)
    list_pct_line = f"Лист процент: {h(list_pct)}%\n" if list_pct and list_pct != 100 else ""

    verif = info.get("verification") or {}
    verif_name = h(verif.get("username") or "Не указан")
    video = safe_url(verif.get("video_url"))
    song = safe_url(info.get("song_url"))

    place_val = h(info.get('placement') or info.get('rank', '?'))
    text = (
        f"Название: <b>{h(info.get('name', '?'))}</b>\n"
        f"Создатель: {h(info.get('creator', '?'))}\n"
        f"Верификатор: {verif_name}\n\n"
        f"Позиция в топе: <b>#{place_val}</b>\n"
        f"Очки за прохождение: <b>{h(info.get('points', '?'))}</b> pts\n"
        f"{list_pct_line}"
        f"Количество викторов: <b>{h(victors)}</b>\n\n"
        f"ID уровня: <code>{h(info.get('ingame_id', '?'))}</code>\n"
        f"Версия игры: {h(info.get('game_version', '?'))}\n"
        f"Объектов: {h(info.get('objects', '?'))}\n"
        f"Длина: {h(info.get('length', '?'))} сек.\n"
        f"Добавлен: {fmt_date(info.get('date_created'))}"
    )

    is_sub = False
    if chat_id is not None:
        is_sub = await is_subscribed_to_level(chat_id, level_id)

    sub_btn_text = "Отписаться от уровня" if is_sub else "Следить за уровнем"
    sub_cb = f"sub_lvl_toggle:{level_id}"

    btns: list[list[InlineKeyboardButton]] = [
        [_btn("Рекорды", f"level_victors:{level_id}:0"), _btn("Сравнить со мной", f"level_compare_me:{level_id}")],
        [_btn(sub_btn_text, sub_cb)],
    ]
    media_row = []
    if video:
        media_row.append(_url_btn("Видео верификации", video))
    if song:
        media_row.append(_url_btn("Музыка", song))
    if media_row:
        btns.append(media_row)

    kb = InlineKeyboardMarkup(inline_keyboard=btns)

    card_bio = await render_level_card_async(
        name=info.get("name", "?"),
        rank=info.get("placement") or info.get("rank", "?"),
        points=info.get("points", 0),
        creator=info.get("creator", "Unknown"),
        verifier=verif_name,
        objects=info.get("objects", "-"),
        length=info.get("length", "-"),
        victors=victors if victors is not None else "-",
        list_percent=list_pct if list_pct else 100,
    )
    return text, kb, card_bio


# ─── /player ──────────────────────────────────────────────────────────────────

@router.message(Command("player", "info"))
async def cmd_player(message: Message) -> None:
    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Использование: /player <code>ник</code>", parse_mode="HTML")
        return

    username = args[1].strip()
    if not validate_username(username):
        await message.answer("Некорректный ник.")
        return

    await message.answer("Загрузка профиля...")
    try:
        user = await gdl.get_user(username)
    except APIError:
        await message.answer("API временно недоступен. Попробуйте позже.")
        return

    if not user:
        await message.answer(f"Игрок <code>{h(username)}</code> не найден.", parse_mode="HTML")
        return

    try:
        records, country_pos = await asyncio.gather(
            gdl.get_records(user["id"]),
            gdl.get_country_position(user),
        )
    except APIError:
        records, country_pos = [], None

    completed = [r for r in records if r.get("status") == "accepted" and r.get("percent") == 100]
    waiting_c = sum(1 for r in records if r.get("status") == "waiting")
    hardest = sorted(completed, key=record_rank)[:5]

    country_display = country_display_with_flag(user.get("country"))
    ctop = f"#{country_pos}" if country_pos is not None else "-"

    lines = [
        f"Ник: <b>{h(user.get('username', '?'))}</b>",
        f"Страна: {h(country_display)}",
        "",
        f"Мировой топ: <b>#{h(user.get('placement') or user.get('rank', '?'))}</b>",
        f"Очки Demonlist: <b>{h(user.get('points', 0))}</b> pts",
        f"Топ страны: <b>{ctop}</b>",
        f"Всего комплишенов: <b>{len(completed)}</b>",
        f"В ожидании проверки: <b>{waiting_c}</b>",
    ]
    if hardest:
        lines += ["", "<b>Топ-5 хардестов:</b>"]
        for i, rec in enumerate(hardest, 1):
            lvl = rec.get("level") or {}
            video = safe_url(rec.get("video_url"))
            name = h(lvl.get("name", "?"))
            place = lvl.get("placement", "?")
            if video:
                lines.append(f'  {i}. <a href="{h(video)}">{name}</a> (#{place})')
            else:
                lines.append(f"  {i}. {name} (#{place})")

    hardest_titles = [f"{rec.get('level', {}).get('name', '?')} (#{rec.get('level', {}).get('placement', '?')})" for rec in hardest[:3]]
    card_bio = await render_player_card_async(
        username=user.get("username", username),
        rank=user.get("placement") or user.get("rank", "-"),
        points=user.get("points", 0),
        country=country_display,
        country_rank=country_pos,
        hardest=hardest_titles,
        completions_count=len(completed),
    )

    chat_id = message.chat.id
    target_uname = user.get("username", username)
    is_sub = await is_subscribed_to_player(chat_id, target_uname)
    sub_text = "Отписаться от игрока" if is_sub else "Следить за игроком"

    player_kb = InlineKeyboardMarkup(inline_keyboard=[
        [_btn("Сравнить со мной", f"player_compare_me:{user['id']}:{target_uname}")],
        [_btn(sub_text, f"sub_plr_toggle:{target_uname}")],
    ])

    await message.answer_photo(
        BufferedInputFile(card_bio.getvalue(), filename="player.png"),
        caption="\n".join(lines),
        reply_markup=player_kb,
        parse_mode="HTML",
    )


# ─── /level ───────────────────────────────────────────────────────────────────

@router.message(Command("level"))
async def cmd_level(message: Message) -> None:
    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "Использование:\n/level <code>название</code>\n/level <code>позиция</code>",
            parse_mode="HTML",
        )
        return

    query = args[1].strip()
    await message.answer("Поиск уровня...")

    try:
        if query.isdigit():
            pos = int(query)
            if pos < 1 or pos > 5000:
                await message.answer("Позиция должна быть в диапазоне от 1 до 5000.")
                return
            level_stub = await gdl.get_level_at_position(pos)
            level_id = level_stub["id"] if level_stub and "id" in level_stub else None
        else:
            level_stub = await gdl.search_level(query)
            level_id = level_stub["id"] if level_stub and "id" in level_stub else None
    except APIError:
        await message.answer("API временно недоступен. Попробуйте позже.")
        return

    if not level_id:
        await message.answer("Уровень не найден.")
        return

    result = await _build_level_card(level_id, chat_id=message.chat.id)
    if result is None:
        await message.answer("Не удалось загрузить данные об уровне.")
        return

    text, kb, card_bio = result
    if card_bio:
        await message.answer_photo(
            BufferedInputFile(card_bio.getvalue(), filename="level.png"),
            caption=text,
            reply_markup=kb,
            parse_mode="HTML",
        )
    else:
        await message.answer(text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)


# ─── /levels ──────────────────────────────────────────────────────────────────

async def _show_levels_page(target: Message | CallbackQuery, page: int) -> None:
    page = max(0, page)
    try:
        levels = await gdl.get_levels(offset=page * LEVELS_PER_PAGE, limit=LEVELS_PER_PAGE)
    except APIError:
        await _edit_or_answer(target, "API временно недоступен. Попробуйте позже.")
        return

    if not levels:
        await _edit_or_answer(target, "Больше уровней нет.", parse_mode="HTML")
        return

    lines = [f"<b>Топ уровней GDL</b> — страница {page + 1}\n"]
    for lvl in levels:
        placement = lvl.get("placement", "?")
        name = h(lvl.get("name", "?"))
        pts = h(lvl.get("points", "?"))
        url = f"https://demonlist.org/classic/{placement}"
        lines.append(f'<a href="{url}">#{h(placement)} {name}</a> — {pts} очков')

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(_btn("Назад", f"levels:{page - 1}"))
    nav.append(_btn(str(page + 1), "levels_page"))
    if len(levels) == LEVELS_PER_PAGE:
        nav.append(_btn("Вперед", f"levels:{page + 1}"))

    kb = InlineKeyboardMarkup(inline_keyboard=[nav])
    card_bio = await render_section_card_async("ТОП ДЕМОНОВ")
    await _edit_or_answer_with_photo(
        target, card_bio, "\n".join(lines), filename="levels.png", reply_markup=kb
    )


@router.message(Command("levels"))
async def cmd_levels(message: Message) -> None:
    await _show_levels_page(message, 0)


@router.callback_query(F.data.regexp(r"^levels:\d+$"))
async def cb_levels(callback: CallbackQuery) -> None:
    page = int(callback.data.split(":")[1])
    if page > 500:
        await callback.answer("Слишком далеко.", show_alert=True)
        return
    await _show_levels_page(callback, page)


@router.callback_query(F.data == "levels_page")
async def cb_levels_page_indicator(callback: CallbackQuery) -> None:
    await callback.answer()


# ─── Рекорды уровня ───────────────────────────────────────────────────────────

@router.callback_query(F.data.regexp(r"^level_victors:\d+:\d+$"))
async def cb_level_victors(callback: CallbackQuery) -> None:
    _, level_id_s, page_s = callback.data.split(":")
    level_id, page = int(level_id_s), int(page_s)
    if page > 200:
        await callback.answer("Слишком далеко.", show_alert=True)
        return

    try:
        records = await gdl.get_level_victors(level_id, offset=page * 15, limit=16)
    except APIError:
        await callback.answer("API временно недоступен.", show_alert=True)
        return

    if not records and page == 0:
        await callback.answer("Нет завершивших этот уровень.", show_alert=True)
        return

    lines = [f"<b>Рекорды уровня</b> — страница {page + 1}\n"]
    for i, rec in enumerate(records[:15], start=page * 15 + 1):
        player = (rec.get("user") or {}).get("username") or "?"
        pct = rec.get("percent", "?")
        video = safe_url(rec.get("video_url"))
        if video:
            lines.append(f'{i}. <a href="{h(video)}">{h(player)}</a> — {pct}%')
        else:
            lines.append(f"{i}. {h(player)} — {pct}%")

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(_btn("Назад", f"level_victors:{level_id}:{page - 1}"))
    nav.append(_btn("Карточка уровня", f"level_back:{level_id}"))
    if len(records) > 15:
        nav.append(_btn("Вперед", f"level_victors:{level_id}:{page + 1}"))

    kb = InlineKeyboardMarkup(inline_keyboard=[nav])
    if callback.message:
        try:
            await callback.message.edit_text(
                "\n".join(lines), reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True
            )
        except Exception:
            pass
    await callback.answer()


@router.callback_query(F.data.regexp(r"^level_back:\d+$"))
async def cb_level_back(callback: CallbackQuery) -> None:
    level_id = int(callback.data.split(":")[1])
    chat_id = callback.message.chat.id if callback.message else None
    result = await _build_level_card(level_id, chat_id=chat_id)
    if result and callback.message:
        text, kb, card_bio = result
        try:
            if card_bio and callback.message.photo:
                await callback.message.edit_media(
                    InputMediaPhoto(media=BufferedInputFile(card_bio.getvalue(), filename="level.png"), caption=text, parse_mode="HTML"),
                    reply_markup=kb,
                )
            else:
                await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)
        except Exception:
            if card_bio:
                await callback.message.answer_photo(
                    BufferedInputFile(card_bio.getvalue(), filename="level.png"),
                    caption=text,
                    reply_markup=kb,
                    parse_mode="HTML",
                )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^watch_level:\d+$"))
async def cb_watch_level(callback: CallbackQuery) -> None:
    level_id = int(callback.data.split(":")[1])
    try:
        info = await gdl.get_level_info(level_id)
    except APIError:
        await callback.answer("API временно недоступен.", show_alert=True)
        return
    if not info:
        await callback.answer("Уровень не найден.", show_alert=True)
        return

    added = await add_level_watcher(
        telegram_id=callback.from_user.id,
        level_id=level_id,
        level_name=info.get("name", "Unknown"),
        last_placement=info.get("placement"),
    )
    if added:
        await callback.answer("Уровень добавлен в отслеживаемые!", show_alert=True)
    else:
        await callback.answer("Уровень уже в вашем списке отслеживания.", show_alert=True)


@router.callback_query(F.data.regexp(r"^sub_lvl_toggle:\d+$"))
async def cb_sub_lvl_toggle(callback: CallbackQuery) -> None:
    level_id = int(callback.data.split(":")[1])
    chat = callback.message.chat if callback.message else None
    if not chat:
        await callback.answer("Ошибка контекста чата.", show_alert=True)
        return

    is_group = chat.type in ("group", "supergroup")

    # В группах и супергруппах управлять подписками могут только админы
    if is_group:
        is_admin = await _is_group_admin(callback.bot, chat.id, callback.from_user.id)
        if not is_admin:
            await callback.answer("Только администраторы могут настраивать подписки группы.", show_alert=True)
            return

    is_sub = await is_subscribed_to_level(chat.id, level_id)
    if is_sub:
        await remove_level_subscription(chat.id, level_id)
        msg_text = "Вы отписались от обновлений этого уровня в этой группе." if is_group else "Вы отписались от обновлений этого уровня."
        await callback.answer(msg_text, show_alert=True)
    else:
        try:
            info = await gdl.get_level_info(level_id)
            lvl_name = info.get("name", "Unknown") if info else f"ID {level_id}"
        except Exception:
            lvl_name = f"ID {level_id}"

        await add_level_subscription(
            chat_id=chat.id,
            chat_type=chat.type,
            level_id=level_id,
            level_name=lvl_name,
            added_by=callback.from_user.id,
        )
        msg_text = (
            f"Подписка оформлена! Группа получит уведомление при сдвиге в топе или новых рекордах уровня {lvl_name}."
            if is_group
            else f"Подписка оформлена! Вы будете получать уведомления в ЛС при сдвиге в топе или новых рекордах уровня {lvl_name}."
        )
        await callback.answer(msg_text, show_alert=True)

    # Обновляем кнопку в сообщении
    new_sub = not is_sub
    sub_btn_text = "Отписаться от уровня" if new_sub else "Следить за уровнем"
    if callback.message and callback.message.reply_markup:
        old_kb = callback.message.reply_markup.inline_keyboard
        new_rows = []
        for row in old_kb:
            new_row = []
            for b in row:
                if b.callback_data == callback.data:
                    new_row.append(_btn(sub_btn_text, callback.data))
                else:
                    new_row.append(b)
            new_rows.append(new_row)
        try:
            await callback.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=new_rows))
        except Exception:
            pass


@router.callback_query(F.data.regexp(r"^sub_plr_toggle:.+$"))
async def cb_sub_plr_toggle(callback: CallbackQuery) -> None:
    player_name = callback.data.split(":", 1)[1].strip()
    chat = callback.message.chat if callback.message else None
    if not chat:
        await callback.answer("Ошибка контекста чата.", show_alert=True)
        return

    is_group = chat.type in ("group", "supergroup")

    # В группах проверять права админа
    if is_group:
        is_admin = await _is_group_admin(callback.bot, chat.id, callback.from_user.id)
        if not is_admin:
            await callback.answer("Только администраторы могут настраивать подписки группы.", show_alert=True)
            return

    is_sub = await is_subscribed_to_player(chat.id, player_name)
    if is_sub:
        await remove_player_subscription(chat.id, player_name)
        msg_text = f"Вы отписались от игрока {player_name} в этой группе." if is_group else f"Вы отписались от игрока {player_name}."
        await callback.answer(msg_text, show_alert=True)
    else:
        await add_player_subscription(
            chat_id=chat.id,
            chat_type=chat.type,
            player_name=player_name,
            added_by=callback.from_user.id,
        )
        msg_text = (
            f"Подписка оформлена! Группа получит уведомление о новых рекордах игрока {player_name}."
            if is_group
            else f"Подписка оформлена! Вы будете получать уведомления в ЛС о новых рекордах игрока {player_name}."
        )
        await callback.answer(msg_text, show_alert=True)

    # Обновляем кнопку в сообщении
    new_sub = not is_sub
    sub_btn_text = "Отписаться от игрока" if new_sub else "Следить за игроком"
    if callback.message and callback.message.reply_markup:
        old_kb = callback.message.reply_markup.inline_keyboard
        new_rows = []
        for row in old_kb:
            new_row = []
            for b in row:
                if b.callback_data == callback.data:
                    new_row.append(_btn(sub_btn_text, callback.data))
                else:
                    new_row.append(b)
            new_rows.append(new_row)
        try:
            await callback.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=new_rows))
        except Exception:
            pass


@router.callback_query(F.data.regexp(r"^level_compare_me:\d+$"))
async def cb_level_compare_me(callback: CallbackQuery) -> None:
    level_id = int(callback.data.split(":")[1])
    link = await get_link(callback.from_user.id)
    if not link:
        await callback.answer("Сначала привяжите свой аккаунт через /link", show_alert=True)
        return

    try:
        records, info = await asyncio.gather(
            gdl.get_records(link["gdl_user_id"]),
            gdl.get_level_info(level_id),
        )
    except APIError:
        await callback.answer("API временно недоступен.", show_alert=True)
        return

    if not info:
        await callback.answer("Уровень не найден.", show_alert=True)
        return

    lvl_name = info.get("name", "?")
    user_rec = next((r for r in records if (r.get("level") or {}).get("id") == level_id), None)

    if user_rec and user_rec.get("status") == "accepted" and user_rec.get("percent") == 100:
        res = f"Вы прошли {lvl_name} на 100%!"
    elif user_rec:
        res = f"Ваш рекорд на {lvl_name}: {user_rec.get('percent', '?')}% ({user_rec.get('status', '?')})"
    else:
        res = f"У вас нет подтвержденных рекордов на {lvl_name}."

    await callback.answer(res, show_alert=True)


@router.callback_query(F.data.regexp(r"^player_compare_me:\d+(?::.*)?$"))
async def cb_player_compare_me(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    target_id = int(parts[1])
    target_uname = parts[2] if len(parts) > 2 else None

    link = await get_link(callback.from_user.id)
    if not link:
        await callback.answer("Сначала привяжите свой аккаунт через /link", show_alert=True)
        return

    if link["gdl_user_id"] == target_id:
        await callback.answer("Это ваш собственный профиль.", show_alert=True)
        return

    await callback.answer()

    # Если имя не передано в callback, пробуем узнать его через get_user_by_id
    if not target_uname:
        try:
            target_obj = await gdl.get_user_by_id(target_id)
            target_uname = target_obj.get("username") if target_obj else None
        except Exception:
            target_uname = None

    if not target_uname:
        await callback.answer("Не удалось определить имя игрока.", show_alert=True)
        return

    u1_name = link["gdl_username"]
    u2_name = target_uname

    msg = callback.message
    if msg:
        status_msg = await msg.answer(f"Сравнение <b>{h(u1_name)}</b> и <b>{h(u2_name)}</b>...", parse_mode="HTML")
        await _send_compare_result(status_msg, u1_name, u2_name)
    else:
        await callback.bot.send_message(
            callback.from_user.id,
            f"Сравнение <b>{h(u1_name)}</b> и <b>{h(u2_name)}</b>...",
            parse_mode="HTML",
        )


@router.callback_query(F.data.regexp(r"^player_card:.+$"))
async def cb_player_card(callback: CallbackQuery) -> None:
    uname = callback.data.split(":", 1)[1]
    await callback.answer("Генерация карточки...")
    try:
        user = await gdl.get_user(uname)
    except APIError:
        await callback.message.answer("API временно недоступен.")
        return

    if not user:
        await callback.message.answer("Игрок не найден.")
        return

    try:
        records = await gdl.get_records(user["id"])
    except APIError:
        records = []

    completed = [r for r in records if r.get("status") == "accepted" and r.get("percent") == 100]
    hardest_list = []
    for r in sorted(completed, key=lambda x: (x.get("level") or {}).get("placement", 99999))[:3]:
        lvl = r.get("level") or {}
        hardest_list.append(f"{lvl.get('name', '?')} (#{lvl.get('placement', '?')})")

    cc = country_code(user.get("country"))
    country_str = country_display_with_flag(user.get("country"))

    card_bio = await render_player_card_async(
        username=user.get("username", uname),
        rank=user.get("placement") or user.get("rank") or "-",
        points=user.get("points") or "0",
        country=country_str,
        hardest=hardest_list,
    )

    photo_file = BufferedInputFile(card_bio.getvalue(), filename=f"card_{uname}.png")
    await callback.message.answer_photo(photo_file, caption=f"Карточка игрока {h(user.get('username', uname))}", parse_mode="HTML")


# ─── /top ─────────────────────────────────────────────────────────────────────

async def _show_top_page(target: Message | CallbackQuery, page: int) -> None:
    try:
        players = await gdl.get_leaderboard(offset=page * TOP_PER_PAGE, limit=TOP_PER_PAGE + 1)
    except APIError:
        await _edit_or_answer(target, "API временно недоступен. Попробуйте позже.")
        return

    has_next = len(players) > TOP_PER_PAGE
    players = players[:TOP_PER_PAGE]

    if not players:
        await _edit_or_answer(target, "Больше игроков нет.")
        return

    lines = [f"<b>Топ игроков GDL</b> — страница {page + 1}\n"]
    for i, p in enumerate(players, start=page * TOP_PER_PAGE + 1):
        flag = country_flag(p.get("country"))
        flag_prefix = f"{flag} " if flag else ""
        name = h(p.get("username", "?"))
        pts = h(p.get("points", 0))
        lines.append(f"{i}. {flag_prefix}<b>{name}</b> — {pts} pts")

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(_btn("Назад", f"top:{page - 1}"))
    nav.append(_btn(str(page + 1), "top_page"))
    if has_next:
        nav.append(_btn("Вперед", f"top:{page + 1}"))

    kb = InlineKeyboardMarkup(inline_keyboard=[nav])
    card_bio = await render_section_card_async("ТОП ИГРОКОВ")
    await _edit_or_answer_with_photo(target, card_bio, "\n".join(lines), filename="top.png", reply_markup=kb)


@router.message(Command("top"))
async def cmd_top(message: Message) -> None:
    await _show_top_page(message, 0)


@router.callback_query(F.data.regexp(r"^top:\d+$"))
async def cb_top(callback: CallbackQuery) -> None:
    page = int(callback.data.split(":")[1])
    if page > 200:
        await callback.answer("Слишком далеко.", show_alert=True)
        return
    await _show_top_page(callback, page)


@router.callback_query(F.data == "top_page")
async def cb_top_page_indicator(callback: CallbackQuery) -> None:
    await callback.answer()


# ─── /topcountries ────────────────────────────────────────────────────────────

async def _show_countries_page(target: Message | CallbackQuery, page: int) -> None:
    try:
        countries = await gdl.get_top_countries(offset=page * COUNTRIES_PER_PAGE, limit=COUNTRIES_PER_PAGE)
    except APIError:
        await _edit_or_answer(target, "API временно недоступен. Попробуйте позже.")
        return

    if not countries:
        if isinstance(target, CallbackQuery):
            await target.answer("Больше стран нет.", show_alert=True)
        else:
            await target.answer("Список стран пуст.")
        return

    lines = [f"<b>Рейтинг стран GDL</b> — страница {page + 1}\n"]
    for c in countries:
        c_title = c.get("title") or "Неизвестно"
        name = h(c_title.replace("-", " ").title())
        flag = country_flag(c_title)
        flag_prefix = f"{flag} " if flag else ""
        place = c.get("placement", "?")
        pts = h(c.get("points", 0))
        lines.append(f"#{place} {flag_prefix}{name} — {pts} очков")

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(_btn("Назад", f"countries:{page - 1}"))
    nav.append(_btn(str(page + 1), "countries_page"))
    if len(countries) == COUNTRIES_PER_PAGE:
        nav.append(_btn("Вперед", f"countries:{page + 1}"))

    kb = InlineKeyboardMarkup(inline_keyboard=[nav])
    card_bio = await render_section_card_async("ТОП СТРАН")
    await _edit_or_answer_with_photo(target, card_bio, "\n".join(lines), filename="countries.png", reply_markup=kb)


@router.message(Command("topcountries"))
async def cmd_topcountries(message: Message) -> None:
    await _show_countries_page(message, 0)


@router.callback_query(F.data.regexp(r"^countries:\d+$"))
async def cb_countries(callback: CallbackQuery) -> None:
    page = int(callback.data.split(":")[1])
    if page > 50:
        await callback.answer("Слишком далеко.", show_alert=True)
        return
    await _show_countries_page(callback, page)


@router.callback_query(F.data == "countries_page")
async def cb_countries_page_indicator(callback: CallbackQuery) -> None:
    await callback.answer()


# ─── /random ──────────────────────────────────────────────────────────────────

@router.message(Command("random"))
async def cmd_random(message: Message) -> None:
    args = (message.text or "").split(maxsplit=1)
    min_pos, max_pos = 1, 0

    if len(args) > 1:
        raw = args[1].strip()
        if "-" in raw:
            parts = raw.split("-", 1)
            try:
                min_pos, max_pos = int(parts[0]), int(parts[1])
            except ValueError:
                await message.answer("Использование: /random или /random <code>1-100</code>", parse_mode="HTML")
                return
        elif raw.isdigit():
            max_pos = int(raw)
        else:
            await message.answer("Использование: /random или /random <code>1-100</code>", parse_mode="HTML")
            return

    if min_pos < 1 or (max_pos != 0 and max_pos < min_pos):
        await message.answer("Указан неверный диапазон.")
        return

    try:
        total = await gdl.get_level_count()
    except APIError:
        await message.answer("API временно недоступен. Попробуйте позже.")
        return

    if total == 0:
        await message.answer("Список уровней пуст.")
        return

    if max_pos == 0:
        max_pos = total

    if min_pos > total:
        await message.answer(f"Диапазон вне рамок топа. Последний уровень: #{total}")
        return

    max_pos = min(max_pos, total)
    position = pyrandom.randint(min_pos, max_pos)

    await message.answer(f"Выбор случайного уровня из диапазона #{min_pos}–#{max_pos}...")
    try:
        level_stub = await gdl.get_level_at_position(position)
    except APIError:
        await message.answer("API временно недоступен. Попробуйте позже.")
        return

    if not level_stub or "id" not in level_stub:
        await message.answer("Уровень не найден.")
        return

    result = await _build_level_card(level_stub["id"])
    if not result:
        await message.answer("Не удалось загрузить данные об уровне.")
        return

    text, kb, card_bio = result
    if card_bio:
        await message.answer_photo(
            BufferedInputFile(card_bio.getvalue(), filename="level.png"),
            caption=text,
            reply_markup=kb,
            parse_mode="HTML",
        )
    else:
        await message.answer(text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)


# ─── /hardest ─────────────────────────────────────────────────────────────────

@router.message(Command("hardest"))
async def cmd_hardest(message: Message) -> None:
    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Использование: /hardest <code>ник</code>", parse_mode="HTML")
        return

    username = args[1].strip()
    if not validate_username(username):
        await message.answer("Некорректный ник.")
        return

    await message.answer(f"Загрузка рекордов <b>{h(username)}</b>...", parse_mode="HTML")
    try:
        user = await gdl.get_user(username)
    except APIError:
        await message.answer("API временно недоступен. Попробуйте позже.")
        return

    if not user:
        await message.answer(f"Игрок <code>{h(username)}</code> не найден.", parse_mode="HTML")
        return

    try:
        records = await gdl.get_records(user["id"])
    except APIError:
        await message.answer("Не удалось получить рекорды.")
        return

    completed = [r for r in records if r.get("status") == "accepted" and r.get("percent") == 100]
    hardest = sorted(completed, key=record_rank)[:10]

    lines = [
        "<b>Топ-10 хардестов</b>\n",
        f"Игрок: <b>{h(user.get('username', username))}</b>\n",
    ]
    if not hardest:
        lines.append("У игрока пока нет принятых комплишенов.")
    else:
        for i, rec in enumerate(hardest, 1):
            lvl = rec.get("level") or {}
            name = h(lvl.get("name", "?"))
            place = lvl.get("placement", "?")
            video = safe_url(rec.get("video_url"))
            if video:
                lines.append(f'{i}. <a href="{h(video)}">{name}</a> (Топ #{place})')
            else:
                lines.append(f"{i}. {name} (Топ #{place})")

    card_bio = await render_section_card_async("ХАРДЕСТЫ")
    await message.answer_photo(
        BufferedInputFile(card_bio.getvalue(), filename="hardest.png"),
        caption="\n".join(lines),
        parse_mode="HTML",
    )


# ─── /compare ─────────────────────────────────────────────────────────────────

async def _send_compare_result(
    target_msg: Message,
    u1_name: str,
    u2_name: str,
) -> None:
    """Выполняет сравнение двух игроков и отправляет отдельное сообщение с карточкой 1:1."""
    try:
        user1, user2 = await asyncio.gather(gdl.get_user(u1_name), gdl.get_user(u2_name))
    except APIError:
        await target_msg.answer("API временно недоступен. Попробуйте позже.")
        return

    if not user1:
        await target_msg.answer(f"Игрок <code>{h(u1_name)}</code> не найден.", parse_mode="HTML")
        return
    if not user2:
        await target_msg.answer(f"Игрок <code>{h(u2_name)}</code> не найден.", parse_mode="HTML")
        return

    try:
        records1, records2, pos1, pos2 = await asyncio.gather(
            gdl.get_records(user1["id"]),
            gdl.get_records(user2["id"]),
            gdl.get_country_position(user1),
            gdl.get_country_position(user2),
        )
    except APIError:
        records1 = records2 = []
        pos1 = pos2 = None

    def _comp(recs: list) -> list:
        return [r for r in recs if r.get("status") == "accepted" and r.get("percent") == 100]

    comp1 = _comp(records1)
    comp2 = _comp(records2)

    h1 = sorted(comp1, key=record_rank)
    h2 = sorted(comp2, key=record_rank)

    h1_top = h1[0].get("level", {}) if h1 else {}
    h2_top = h2[0].get("level", {}) if h2 else {}

    h1_name = h1_top.get("name", "-")
    h2_name = h2_top.get("name", "-")

    h1_place = h1_top.get("placement", 9999)
    h2_place = h2_top.get("placement", 9999)

    r1 = int(user1.get("placement") or user1.get("rank") or 99999)
    r2 = int(user2.get("placement") or user2.get("rank") or 99999)

    pts1 = float(user1.get("points") or 0)
    pts2 = float(user2.get("points") or 0)

    c1_cnt = len(comp1)
    c2_cnt = len(comp2)

    # Подсчет счета (кто победил по метрикам, при равенстве очко обоим)
    p1_score = 0
    p2_score = 0

    # 1. Ранг в мире
    if r1 < r2:
        p1_score += 1
    elif r2 < r1:
        p2_score += 1
    else:
        p1_score += 1
        p2_score += 1

    # 2. Очки Demonlist
    if pts1 > pts2:
        p1_score += 1
    elif pts2 > pts1:
        p2_score += 1
    else:
        p1_score += 1
        p2_score += 1

    # 3. Сложнейший демон
    if h1_place < h2_place:
        p1_score += 1
    elif h2_place < h1_place:
        p2_score += 1
    else:
        p1_score += 1
        p2_score += 1

    # 4. Пройденные демоны
    if c1_cnt > c2_cnt:
        p1_score += 1
    elif c2_cnt > c1_cnt:
        p2_score += 1
    else:
        p1_score += 1
        p2_score += 1

    p1_real_name = user1.get("username", u1_name)
    p2_real_name = user2.get("username", u2_name)

    card_bio = await render_compare_card_async(
        p1_name=p1_real_name,
        p1_rank=user1.get("placement") or user1.get("rank") or "-",
        p1_points=f"{pts1:g}",
        p1_hardest=h1_name if h1_name != "-" else "Нет",
        p1_victors=c1_cnt,
        p2_name=p2_real_name,
        p2_rank=user2.get("placement") or user2.get("rank") or "-",
        p2_points=f"{pts2:g}",
        p2_hardest=h2_name if h2_name != "-" else "Нет",
        p2_victors=c2_cnt,
        p1_score=p1_score,
        p2_score=p2_score,
    )

    c1_display = country_display_with_flag(user1.get("country"))
    c2_display = country_display_with_flag(user2.get("country"))

    lines = [
        f"<b>Сравнение игроков: {h(p1_real_name)} vs {h(p2_real_name)}</b>\n",
        f"<b>{h(p1_real_name)}</b> {c1_display}:",
        f"• Ранг в мире: #{h(user1.get('placement') or user1.get('rank', '-'))}",
        f"• Очки: {pts1:g} pts",
        f"• Хардест: {h(h1_name)} (#{h1_place if h1_place != 9999 else '-'})\n",
        f"<b>{h(p2_real_name)}</b> {c2_display}:",
        f"• Ранг в мире: #{h(user2.get('placement') or user2.get('rank', '-'))}",
        f"• Очки: {pts2:g} pts",
        f"• Хардест: {h(h2_name)} (#{h2_place if h2_place != 9999 else '-'})\n",
        f"<b>Счет раундов: {p1_score} : {p2_score}</b>",
    ]

    if p1_score > p2_score:
        lines.append(f"Победитель: <b>{h(p1_real_name)}</b>")
    elif p2_score > p1_score:
        lines.append(f"Победитель: <b>{h(p2_real_name)}</b>")
    else:
        lines.append("Боевая ничья!")

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            _btn(f"Профиль {p1_real_name[:12]}", f"open_profile:{p1_real_name}"),
            _btn(f"Профиль {p2_real_name[:12]}", f"open_profile:{p2_real_name}"),
        ]
    ])

    await target_msg.answer_photo(
        BufferedInputFile(card_bio.getvalue(), filename="compare.png"),
        caption="\n".join(lines),
        reply_markup=kb,
        parse_mode="HTML",
    )


@router.message(Command("compare", "versus", "duel"))
async def cmd_compare(message: Message) -> None:
    args = (message.text or "").split(maxsplit=1)
    u1_name = None
    u2_name = None

    if len(args) >= 2:
        raw = args[1].strip()
        if "," in raw:
            parts = raw.split(",", 1)
            u1_name, u2_name = parts[0].strip(), parts[1].strip()
        else:
            parts = raw.split(maxsplit=1)
            if len(parts) >= 2:
                u1_name, u2_name = parts[0].strip(), parts[1].strip()
            else:
                u1_name = parts[0].strip()

    # Если в группе вызвали через ответ (reply) на сообщение другого пользователя
    if (not u2_name or not u1_name) and message.reply_to_message and message.reply_to_message.from_user:
        caller_link = await get_link(message.from_user.id)
        target_link = await get_link(message.reply_to_message.from_user.id)
        if caller_link and target_link:
            u1_name = caller_link["gdl_username"]
            u2_name = target_link["gdl_username"]
        elif u1_name and target_link:
            u2_name = target_link["gdl_username"]
        elif caller_link and not u1_name:
            # первый игрок - вызвавший, но второго нет
            pass

    if not u1_name or not u2_name:
        await message.answer(
            "Использование:\n"
            "/compare <code>ник1</code>, <code>ник2</code>\n"
            "/compare <code>ник1</code> <code>ник2</code>\n"
            "Или ответьте командой /compare на сообщение участника в группе.",
            parse_mode="HTML",
        )
        return

    await message.answer(f"Сравнение <b>{h(u1_name)}</b> и <b>{h(u2_name)}</b>...", parse_mode="HTML")
    try:
        async with asyncio.timeout(25):
            await _send_compare_result(message, u1_name, u2_name)
    except TimeoutError:
        await message.answer("Сравнение заняло слишком много времени. Попробуйте позже.")


@router.callback_query(F.data.regexp(r"^open_profile:.+$"))
async def cb_open_profile(callback: CallbackQuery) -> None:
    uname = callback.data.split(":", 1)[1].strip()
    msg = callback.message.model_copy(update={"text": f"/player {uname}"})
    await cmd_player(msg)
    await callback.answer()


# ─── Inline query ─────────────────────────────────────────────────────────────

@router.inline_query()
async def inline_search(query: InlineQuery) -> None:
    text = query.query.strip()
    if not text or len(text) < 2:
        await query.answer([], cache_time=1, is_personal=True)
        return

    results = []

    async def _player_result():
        if not validate_username(text):
            return None
        try:
            user = await gdl.get_user(text)
        except APIError:
            return None
        if not user:
            return None

        uname = user.get("username", text)
        pts = user.get("points", 0)
        rank = user.get("placement") or user.get("rank", "?")
        country_display = country_display_with_flag(user.get("country"))

        try:
            records, country_pos = await asyncio.gather(
                gdl.get_records(user["id"]),
                gdl.get_country_position(user),
            )
        except Exception:
            records, country_pos = [], None

        completed = [r for r in records if r.get("status") == "accepted" and r.get("percent") == 100]
        hardest = sorted(completed, key=record_rank)[:5]
        ctop = f"#{country_pos}" if country_pos is not None else "-"

        lines = [
            f"Ник: <b>{h(uname)}</b>",
            f"Страна: {h(country_display)}",
            "",
            f"Мировой топ: <b>#{h(rank)}</b>",
            f"Очки Demonlist: <b>{h(pts)}</b> pts",
            f"Топ страны: <b>{ctop}</b>",
            f"Всего комплишенов: <b>{len(completed)}</b>",
        ]
        if hardest:
            lines += ["", "<b>Топ-5 хардестов:</b>"]
            for i, rec in enumerate(hardest, 1):
                lvl = rec.get("level") or {}
                name = h(lvl.get("name", "?"))
                place = lvl.get("placement", "?")
                lines.append(f"  {i}. {name} (#{place})")

        from urllib.parse import quote
        deep_link = f"https://t.me/{BOT_USERNAME}?start=p_{quote(uname)}"

        return InlineQueryResultArticle(
            id=f"player_{uuid4()}",
            title=f"Игрок: {uname} (#{rank})",
            description=f"Очки: {pts} pts | Топ: #{rank} | {country_display}",
            thumbnail_url="https://demonlist.org/img/favicon.ico",
            input_message_content=InputTextMessageContent(
                message_text="\n".join(lines),
                parse_mode="HTML",
                disable_web_page_preview=True,
            ),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Открыть в боте", url=deep_link)]
            ]),
        )

    async def _level_results():
        try:
            levels = await gdl.search_levels(text, limit=5)
        except APIError:
            return []

        async def _fetch_one(lvl: dict):
            lvl_id = lvl.get("id")
            name = lvl.get("name", "?")
            place = lvl.get("placement", "?")
            pts = lvl.get("points", "?")
            lp = lvl.get("list_percent", 100)

            creator = lvl.get("creator") or (lvl.get("holder") or {}).get("name") or "Unknown"
            lines = [
                f"Название: <b>{h(name)}</b>",
                f"Создатель: {h(creator)}",
                "",
                f"Позиция в топе: <b>#{h(place)}</b>",
                f"Очки за прохождение: <b>{h(pts)}</b> pts",
            ]
            if lp and lp != 100:
                lines.append(f"Лист процент: {h(lp)}%")
            lvl_text = "\n".join(lines)

            deep_link = f"https://t.me/{BOT_USERNAME}?start=l_{lvl_id}" if lvl_id else f"https://t.me/{BOT_USERNAME}"
            return InlineQueryResultArticle(
                id=f"level_{uuid4()}",
                title=f"#{place} {name}",
                description=f"Очки: {pts} pts | Лист: {lp}%",
                thumbnail_url="https://demonlist.org/img/favicon.ico",
                input_message_content=InputTextMessageContent(
                    message_text=lvl_text,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                ),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="Открыть в боте", url=deep_link)]
                ]),
            )

        tasks = [_fetch_one(lvl) for lvl in levels]
        items = await asyncio.gather(*tasks)
        return [item for item in items if item is not None]

    player_res, level_res = await asyncio.gather(_player_result(), _level_results())
    if player_res:
        results.append(player_res)
    results.extend(level_res)

    try:
        await query.answer(results[:10], cache_time=30, is_personal=True)
    except Exception:
        pass
