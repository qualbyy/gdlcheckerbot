"""
Новые функции roadmap:
- /progress (Личный трекер прогресса, дельта очков и ранга)
- /watchlist & /watch & /unwatch (Отслеживание конкретных демонов)
- /roulette (Demon Roulette челлендж)
- /predict (Калькулятор очков и ранга при прохождении уровня)
- /changes (Лог последних изменений и смещений в топе)
- /guild (Локальный рейтинг беседы/чата)
"""

import logging
import random as pyrandom
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest, TelegramAPIError
from aiogram.types import Message, CallbackQuery, BufferedInputFile, InlineKeyboardMarkup, InlineKeyboardButton

from db.database import (
    get_link, get_progress_snapshots, save_progress_snapshot,
    add_level_watcher, remove_level_watcher, get_level_watchers,
    add_guild_member, get_guild_members,
    get_list_changelog, sync_level_placements,
    get_player_subscriptions, get_level_subscriptions,
    add_player_subscription, remove_player_subscription,
    add_level_subscription, remove_level_subscription,
)
from services.gdl_client import gdl, APIError
from services.formatting import h, country_code, validate_username
from services.image_generator import render_section_card_async

logger = logging.getLogger(__name__)
router = Router()


# --- /progress ---

@router.message(Command("progress"))
async def cmd_progress(message: Message) -> None:
    link = await get_link(message.from_user.id)
    if not link:
        await message.answer("Аккаунт не привязан. Используйте /link <code>ник</code>", parse_mode="HTML")
        return

    await message.answer("Загрузка данных прогресса...")
    try:
        user = await gdl.get_user(link["gdl_username"])
    except APIError:
        await message.answer("API временно недоступен. Попробуйте позже.")
        return

    if not user:
        await message.answer("Профиль не найден.")
        return

    curr_points = float(user.get("points") or 0.0)
    curr_rank = int(user.get("placement") or user.get("rank") or 0)

    snapshots = await get_progress_snapshots(message.from_user.id, limit=5)
    await save_progress_snapshot(message.from_user.id, link["gdl_user_id"], curr_points, curr_rank)

    lines = [
        f"<b>Трекер прогресса: {h(link['gdl_username'])}</b>\n",
        f"Текущие очки: <b>{curr_points:g}</b>",
        f"Текущий ранг: <b>#{curr_rank}</b>\n",
    ]

    if snapshots:
        old = snapshots[0]
        pts_diff = curr_points - float(old["points"])
        rank_diff = int(old["rank"]) - curr_rank  # положительно если поднялся выше

        pts_sign = "+" if pts_diff > 0 else ""
        rank_sign = "+" if rank_diff > 0 else ""

        lines.append("<b>Изменения с прошлой проверки:</b>")
        lines.append(f"Очки: {pts_sign}{pts_diff:g}")
        lines.append(f"Ранг: {rank_sign}{rank_diff} поз. (было #{old['rank']})")
    else:
        lines.append("Это первая фиксация вашего прогресса. При последующих вызовах /progress бот покажет дельту очков и ранга.")

    card_bio = await render_section_card_async("ТРЕКЕР ПРОГРЕССА")
    await message.answer_photo(
        BufferedInputFile(card_bio.getvalue(), filename="progress.png"),
        caption="\n".join(lines),
        parse_mode="HTML",
    )


# --- /watchlist & /watch & /unwatch ---

@router.message(Command("watch"))
async def cmd_watch(message: Message) -> None:
    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Использование: /watch <code>название уровня</code>", parse_mode="HTML")
        return

    query = args[1].strip()
    await message.answer("Поиск уровня...")
    try:
        if query.isdigit():
            num = int(query)
            level = await gdl.get_level_at_position(num)
            if not level:
                level = await gdl.get_level_info(num)
        else:
            level = await gdl.search_level(query)
    except APIError:
        await message.answer("API временно недоступен.")
        return

    if not level or "id" not in level:
        await message.answer("Уровень не найден.")
        return

    added = await add_level_watcher(
        telegram_id=message.from_user.id,
        level_id=level["id"],
        level_name=level["name"],
        last_placement=level.get("placement"),
    )
    if added:
        await message.answer(
            f"Уровень <b>{h(level['name'])}</b> (#{level.get('placement', '?')}) добавлен в отслеживаемые.\n"
            "Бот уведомит вас при изменении его позиции в топе.",
            parse_mode="HTML",
        )
    else:
        await message.answer(f"Уровень <b>{h(level['name'])}</b> уже находится в вашем списке отслеживания.", parse_mode="HTML")


@router.message(Command("unwatch"))
async def cmd_unwatch(message: Message) -> None:
    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Использование: /unwatch <code>название уровня</code>", parse_mode="HTML")
        return

    query = args[1].strip().lower()
    watchers = await get_level_watchers(message.from_user.id)
    matched = None
    if query.isdigit():
        q_id = int(query)
        matched = next((w for w in watchers if w["level_id"] == q_id or w.get("last_placement") == q_id), None)
    if not matched:
        matched = next((w for w in watchers if query in w["level_name"].lower()), None)

    if not matched:
        await message.answer("Данный уровень не найден в вашем списке отслеживания.")
        return

    await remove_level_watcher(message.from_user.id, matched["level_id"])
    await message.answer(f"Уровень <b>{h(matched['level_name'])}</b> удален из отслеживаемых.", parse_mode="HTML")


@router.message(Command("watchlist"))
async def cmd_watchlist(message: Message) -> None:
    watchers = await get_level_watchers(message.from_user.id)
    if not watchers:
        await message.answer(
            "Ваш список отслеживаемых демонов пуст.\n"
            "Добавить уровень можно командой /watch <code>название</code>",
            parse_mode="HTML",
        )
        return

    lines = ["<b>Отслеживаемые демоны:</b>\n"]
    for w in watchers:
        lines.append(f"- <b>{h(w['level_name'])}</b> (последняя позиция: #{w['last_placement'] or '?'})")
    lines.append("\nУдалить уровень: /unwatch <code>название</code>")
    card_bio = await render_section_card_async("ОТСЛЕЖИВАНИЕ")
    await message.answer_photo(
        BufferedInputFile(card_bio.getvalue(), filename="watchlist.png"),
        caption="\n".join(lines),
        parse_mode="HTML",
    )


# --- /roulette (По одному демону) ---

async def _generate_roulette_turn(pct: int, step: int = 1) -> tuple[str, InlineKeyboardMarkup]:
    try:
        total = await gdl.get_level_count()
    except APIError:
        total = 150
    if total < 5:
        total = 100

    rank = pyrandom.randint(1, min(total, 150))
    try:
        lvl = await gdl.get_level_at_position(rank)
        name = lvl["name"] if lvl else f"Demon #{rank}"
        points = lvl.get("points", "?") if lvl else "?"
    except Exception:
        name = f"Demon #{rank}"
        points = "?"

    text = (
        f"<b>DEMON ROULETTE CHALLENGE</b>\n\n"
        f"Текущая цель: <b>{pct}%</b>\n"
        f"Уровень: <b>{h(name)}</b> (Топ #{rank})\n"
        f"Очки за прохождение: <b>{h(points)}</b> pts\n\n"
        f"Пройдите <b>{pct}%</b> на этом демоне, чтобы перейти к следующему проценту!"
    )

    next_pct = pct + step
    btns = [
        [InlineKeyboardButton(text=f"Прошёл ({next_pct}%)", callback_data=f"roulette:next:{next_pct}:{step}")],
        [
            InlineKeyboardButton(text="Другой демон", callback_data=f"roulette:reroll:{pct}:{step}"),
            InlineKeyboardButton(text="Завершить", callback_data=f"roulette:stop:{pct}"),
        ]
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=btns)


@router.message(Command("roulette"))
async def cmd_roulette(message: Message) -> None:
    args = (message.text or "").split(maxsplit=1)
    step = 1
    if len(args) > 1 and args[1].strip().isdigit():
        step = max(1, min(10, int(args[1].strip())))

    text, kb = await _generate_roulette_turn(pct=1, step=step)
    card_bio = await render_section_card_async("ДЕМОН РУЛЕТКА")
    await message.answer_photo(
        BufferedInputFile(card_bio.getvalue(), filename="roulette.png"),
        caption=text,
        reply_markup=kb,
        parse_mode="HTML",
    )


@router.callback_query(F.data.regexp(r"^roulette:next:(\d+):(\d+)$"))
async def cb_roulette_next(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    pct = int(parts[2])
    step = int(parts[3])

    if pct > 100:
        win_text = (
            "<b>ПОЗДРАВЛЯЕМ!</b>\n\n"
            "Вы успешно завершили <b>Demon Roulette Challenge</b> на 100%!\n"
            "Вы настоящий топ-виктор!"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Новая рулетка", callback_data=f"roulette:start:1:{step}")]
        ])
        if callback.message and callback.message.photo:
            await callback.message.edit_caption(caption=win_text, reply_markup=kb, parse_mode="HTML")
        elif callback.message:
            await callback.message.edit_text(win_text, reply_markup=kb, parse_mode="HTML")
        await callback.answer("100%! Поздравляем!")
        return

    text, kb = await _generate_roulette_turn(pct=pct, step=step)
    if callback.message and callback.message.photo:
        await callback.message.edit_caption(caption=text, reply_markup=kb, parse_mode="HTML")
    elif callback.message:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer(f"Цель: {pct}%")


@router.callback_query(F.data.regexp(r"^roulette:reroll:(\d+):(\d+)$"))
async def cb_roulette_reroll(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    pct = int(parts[2])
    step = int(parts[3])

    text, kb = await _generate_roulette_turn(pct=pct, step=step)
    if callback.message and callback.message.photo:
        await callback.message.edit_caption(caption=text, reply_markup=kb, parse_mode="HTML")
    elif callback.message:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer("Демон заменен")


@router.callback_query(F.data.regexp(r"^roulette:stop:(\d+)$"))
async def cb_roulette_stop(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    pct = int(parts[2])
    score = max(0, pct - 1)

    text = (
        f"<b>Рулетка завершена!</b>\n\n"
        f"Ваш итоговый результат: <b>{score}%</b>\n\n"
        f"Хотите попробовать еще раз?"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Новая рулетка", callback_data="roulette:start:1:1")]
    ])
    if callback.message and callback.message.photo:
        await callback.message.edit_caption(caption=text, reply_markup=kb, parse_mode="HTML")
    elif callback.message:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer("Рулетка завершена")


@router.callback_query(F.data.regexp(r"^roulette:start:(\d+):(\d+)$"))
async def cb_roulette_start(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    pct = int(parts[2])
    step = int(parts[3])

    text, kb = await _generate_roulette_turn(pct=pct, step=step)
    if callback.message and callback.message.photo:
        await callback.message.edit_caption(caption=text, reply_markup=kb, parse_mode="HTML")
    elif callback.message:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer("Новая игра начата!")


# --- /predict ---

@router.message(Command("predict"))
async def cmd_predict(message: Message) -> None:
    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Использование: /predict <code>название уровня или позиция</code>", parse_mode="HTML")
        return

    link = await get_link(message.from_user.id)
    if not link:
        await message.answer("Сначала привяжите свой аккаунт через /link <code>ник</code>", parse_mode="HTML")
        return

    raw_query = args[1].strip()
    target_pct = 100
    if "," in raw_query:
        parts = [p.strip() for p in raw_query.split(",", 1)]
        query = parts[0]
        pct_candidate = parts[1].replace("%", "").strip()
        if pct_candidate.isdigit():
            target_pct = max(1, min(100, int(pct_candidate)))
    else:
        query = raw_query

    await message.answer("Расчет предполагаемых очков...")

    try:
        user = await gdl.get_user(link["gdl_username"])
    except APIError:
        await message.answer("API временно недоступен.")
        return

    if not user:
        await message.answer("Не удалось загрузить данные вашего профиля.")
        return

    try:
        if query.isdigit():
            num = int(query)
            level = await gdl.get_level_at_position(num)
            if not level:
                level = await gdl.get_level_info(num)
        else:
            level = await gdl.search_level(query)
    except APIError:
        await message.answer("API временно недоступен.")
        return

    if not level:
        await message.answer("Уровень не найден.")
        return

    curr_points = float(user.get("points") or 0.0)
    curr_rank = int(user.get("placement") or user.get("rank") or 0)
    full_points = float(level.get("points") or 0.0)
    lvl_points = (full_points * target_pct / 100.0) if target_pct < 100 else full_points
    lvl_name = level.get("name", "Unknown")
    lvl_placement = level.get("placement", "?")

    new_points = curr_points + lvl_points

    # Оцениваем новый ранг по лидерборду
    predicted_rank = curr_rank
    try:
        top_players = await gdl.get_leaderboard(offset=max(0, curr_rank - 25), limit=50)
        for p in top_players:
            if new_points >= float(p.get("points") or 0.0):
                predicted_rank = min(predicted_rank, int(p.get("placement") or curr_rank))
                break
    except Exception:
        pass

    rank_gain = max(0, curr_rank - predicted_rank)
    gain_text = f"+{rank_gain} позиций" if rank_gain > 0 else "без изменения ранга"
    player_name = user.get("name") or link["gdl_username"] or "Игрок"

    text = (
        f"<b>Симуляция очков для игрока {h(player_name)}</b>\n\n"
        f"Уровень: <b>{h(lvl_name)}</b> (#{lvl_placement})\n"
        f"Очки за уровень: <b>+{lvl_points:g}</b>\n"
        f"Текущие очки: {curr_points:g} -> <b>{new_points:g}</b>\n"
        f"Текущий ранг: #{curr_rank} -> <b>#{predicted_rank}</b> ({gain_text})"
    )
    card_bio = await render_section_card_async("СИМУЛЯЦИЯ ОЧКОВ")
    await message.answer_photo(
        BufferedInputFile(card_bio.getvalue(), filename="predict.png"),
        caption=text,
        parse_mode="HTML",
    )


# --- /changes ---

@router.message(Command("changes"))
async def cmd_changes(message: Message) -> None:
    await message.answer("Загрузка последних обновлений списка...")
    try:
        levels = await gdl.get_levels(offset=0, limit=150)
    except APIError:
        levels = []

    if levels:
        try:
            await sync_level_placements(levels)
        except Exception:
            pass

    changelog = await get_list_changelog(limit=12)

    lines = ["<b>Ченджлог обновлений Demonlist:</b>\n"]
    if changelog:
        for entry in changelog:
            ctype = entry["change_type"]
            name = entry["level_name"]
            new_p = entry["new_placement"]
            old_p = entry["old_placement"]

            if ctype == "added":
                lines.append(f"+ <b>{h(name)}</b> добавлен на <b>#{new_p}</b>")
            elif ctype == "moved":
                diff = old_p - new_p
                if diff > 0:
                    dir_str = f"поднялся на +{diff}"
                else:
                    dir_str = f"опустился на {diff}"
                lines.append(f"• <b>{h(name)}</b>: #{old_p} -> <b>#{new_p}</b> ({dir_str})")
            elif ctype == "removed":
                lines.append(f"- <b>{h(name)}</b> удален из топа (был #{old_p})")
            else:
                lines.append(f"• <b>{h(name)}</b>: #{old_p} -> #{new_p}")
    elif levels:
        # Если база еще только инициализирована и нет зафиксированных смещений,
        # отображаем последние добавленные уровни по дате создания
        with_dates = [l for l in levels if l.get("date_created")]
        with_dates.sort(key=lambda x: str(x.get("date_created")), reverse=True)
        recent = with_dates[:10] if with_dates else levels[:10]

        lines.append("<i>Последние добавленные в список уровни:</i>\n")
        for l in recent:
            p = l.get("placement", "?")
            name = l.get("name", "Unknown")
            creator = l.get("creator") or (l.get("holder") or {}).get("name") or "Unknown"
            dt = str(l.get("date_created", ""))[:10]
            dt_str = f" ({dt})" if dt else ""
            lines.append(f"+ <b>{h(name)}</b> (Топ #{p}) by {h(creator)}{dt_str}")
    else:
        await message.answer("Не удалось загрузить данные Demonlist. Попробуйте позже.")
        return

    lines.append("\nДля подробного списка уровней используйте /levels")
    card_bio = await render_section_card_async("ОБНОВЛЕНИЯ ТОПА")
    await message.answer_photo(
        BufferedInputFile(card_bio.getvalue(), filename="changes.png"),
        caption="\n".join(lines),
        parse_mode="HTML",
    )


# --- /guild ---

@router.message(Command("guild"))
async def cmd_guild(message: Message) -> None:
    if message.chat.type == "private":
        await message.answer("Команда /guild работает в групповых чатах для создания локального рейтинга беседы.")
        return

    # Регистрируем отправителя в гильдии чата
    await add_guild_member(message.chat.id, message.from_user.id)

    members = await get_guild_members(message.chat.id)
    if not members:
        await message.answer(
            "В этом чате пока нет привязанных участников GDL.\n"
            "Каждый участник может привязать аккаунт через /link в ЛС с ботом.",
            parse_mode="HTML",
        )
        return

    await message.answer(f"Формирование рейтинга беседы ({len(members)} участников)...")

    # Собираем актуальные данные
    scores = []
    for m in members:
        try:
            u = await gdl.get_user(m["gdl_username"])
            pts = float(u.get("points") or 0.0) if u else 0.0
            rank = int(u.get("placement") or 0) if u else 0
        except Exception:
            pts = 0.0
            rank = 0
        scores.append((m["gdl_username"], pts, rank))

    scores.sort(key=lambda x: x[1], reverse=True)

    lines = [f"<b>Рейтинг участников чата:</b>\n"]
    for idx, (uname, pts, rnk) in enumerate(scores, 1):
        lines.append(f"{idx}. <b>{h(uname)}</b> — {pts:g} pts (Мировой ранг: #{rnk or '-'})")

    card_bio = await render_section_card_async("КЛАНОВЫЙ ТОП")
    await message.answer_photo(
        BufferedInputFile(card_bio.getvalue(), filename="guild.png"),
        caption="\n".join(lines),
        parse_mode="HTML",
    )


# --- /subscriptions & /sub & /unsub ---

async def _is_admin(bot, chat_id: int, user_id: int) -> bool:
    try:
        m = await bot.get_chat_member(chat_id, user_id)
        return m.status in ("creator", "administrator")
    except (TelegramForbiddenError, TelegramBadRequest):
        return False
    except TelegramAPIError as exc:
        logger.warning("get_chat_member(%d, %d) API error: %s", chat_id, user_id, exc)
        return False
    except Exception as exc:
        logger.error("get_chat_member(%d, %d) unexpected error: %s", chat_id, user_id, exc)
        return False


async def _send_subscriptions_list(
    target: Message | CallbackQuery,
    chat_id: int,
    is_group: bool,
) -> None:
    p_subs = await get_player_subscriptions(chat_id)
    l_subs = await get_level_subscriptions(chat_id)

    title = "Подписки этой группы" if is_group else "Ваши активные подписки"
    lines = [f"<b>{title}:</b>\n"]

    kb_rows = []

    if p_subs:
        lines.append("<b>Отслеживаемые игроки:</b>")
        for s in p_subs:
            pname = s["player_name"]
            lines.append(f"• <b>{h(pname)}</b>")
            kb_rows.append([InlineKeyboardButton(text=f"Отписаться от {pname[:15]}", callback_data=f"unsub_p:{pname}")])
        lines.append("")

    if l_subs:
        lines.append("<b>Отслеживаемые уровни:</b>")
        for s in l_subs:
            lname = s["level_name"]
            lid = s["level_id"]
            lines.append(f"• <b>{h(lname)}</b> (ID: {lid})")
            kb_rows.append([InlineKeyboardButton(text=f"Отписаться от {lname[:15]}", callback_data=f"unsub_l:{lid}")])
        lines.append("")

    if not p_subs and not l_subs:
        lines.append("У вас пока нет активных подписок.")
        lines.append("Вы можете подписаться кнопкой «Следить» прямо в профиле игрока / карточке уровня,")
        lines.append("или командами:")
        lines.append("<code>/sub player ник</code>")
        lines.append("<code>/sub level название</code>")

    reply_kb = InlineKeyboardMarkup(inline_keyboard=kb_rows) if kb_rows else None
    card_bio = await render_section_card_async("ПОДПИСКИ")

    if isinstance(target, CallbackQuery) and target.message:
        if target.message.photo:
            try:
                await target.message.edit_caption(
                    caption="\n".join(lines),
                    reply_markup=reply_kb,
                    parse_mode="HTML",
                )
                return
            except Exception:
                pass
        await target.message.answer_photo(
            BufferedInputFile(card_bio.getvalue(), filename="subs.png"),
            caption="\n".join(lines),
            reply_markup=reply_kb,
            parse_mode="HTML",
        )
    else:
        await target.answer_photo(
            BufferedInputFile(card_bio.getvalue(), filename="subs.png"),
            caption="\n".join(lines),
            reply_markup=reply_kb,
            parse_mode="HTML",
        )


@router.message(Command("subscriptions", "subs"))
async def cmd_subscriptions(message: Message) -> None:
    chat_id = message.chat.id
    is_group = message.chat.type in ("group", "supergroup")

    if is_group:
        admin = await _is_admin(message.bot, chat_id, message.from_user.id)
        if not admin:
            await message.answer("Управлять подписками группы могут только администраторы.")
            return

    await _send_subscriptions_list(message, chat_id, is_group)


@router.message(Command("sub"))
async def cmd_sub(message: Message) -> None:
    chat_id = message.chat.id
    is_group = message.chat.type in ("group", "supergroup")
    if is_group:
        admin = await _is_admin(message.bot, chat_id, message.from_user.id)
        if not admin:
            await message.answer("Только администраторы могут настраивать подписки группы.")
            return

    args = (message.text or "").split(maxsplit=2)
    if len(args) < 3:
        await message.answer(
            "Использование:\n"
            "<code>/sub player ник</code> — подписаться на рекорды игрока\n"
            "<code>/sub level название или позиция</code> — подписаться на уровень",
            parse_mode="HTML",
        )
        return

    sub_type, target = args[1].lower(), args[2].strip()

    if sub_type in ("player", "p", "игрок"):
        try:
            user = await gdl.get_user(target)
        except APIError:
            await message.answer("API временно недоступен.")
            return

        if not user:
            await message.answer(f"Игрок <code>{h(target)}</code> не найден.", parse_mode="HTML")
            return

        pname = user.get("username", target)
        added = await add_player_subscription(
            chat_id=chat_id,
            chat_type=message.chat.type,
            player_name=pname,
            added_by=message.from_user.id,
        )
        if added:
            await message.answer(f"Чат успешно подписан на рекорды игрока <b>{h(pname)}</b>!", parse_mode="HTML")
        else:
            await message.answer(f"Чат уже подписан на игрока <b>{h(pname)}</b>.", parse_mode="HTML")

    elif sub_type in ("level", "lvl", "l", "уровень"):
        try:
            if target.isdigit():
                pos = int(target)
                lvl_stub = await gdl.get_level_at_position(pos)
                level_id = lvl_stub["id"] if lvl_stub and "id" in lvl_stub else None
            else:
                lvl_stub = await gdl.search_level(target)
                level_id = lvl_stub["id"] if lvl_stub and "id" in lvl_stub else None

            if not level_id:
                await message.answer("Уровень не найден.")
                return

            info = await gdl.get_level_info(level_id)
            lvl_name = info.get("name", target) if info else target
        except APIError:
            await message.answer("API временно недоступен.")
            return

        added = await add_level_subscription(
            chat_id=chat_id,
            chat_type=message.chat.type,
            level_id=level_id,
            level_name=lvl_name,
            added_by=message.from_user.id,
        )
        if added:
            await message.answer(f"Чат успешно подписан на изменения уровня <b>{h(lvl_name)}</b>!", parse_mode="HTML")
        else:
            await message.answer(f"Чат уже подписан на уровень <b>{h(lvl_name)}</b>.", parse_mode="HTML")
    else:
        await message.answer("Неверный тип подписки. Используйте <code>player</code> или <code>level</code>.", parse_mode="HTML")


@router.callback_query(F.data.regexp(r"^unsub_p:.+$"))
async def cb_unsub_p(callback: CallbackQuery) -> None:
    pname = callback.data.split(":", 1)[1].strip()
    chat = callback.message.chat if callback.message else None
    if not chat:
        return

    if chat.type in ("group", "supergroup"):
        admin = await _is_admin(callback.bot, chat.id, callback.from_user.id)
        if not admin:
            await callback.answer("Только администраторы могут изменять подписки группы.", show_alert=True)
            return

    await remove_player_subscription(chat.id, pname)
    await callback.answer(f"Вы отписались от игрока {pname}.", show_alert=True)
    await _send_subscriptions_list(callback, chat.id, chat.type in ("group", "supergroup"))


@router.callback_query(F.data.regexp(r"^unsub_l:\d+$"))
async def cb_unsub_l(callback: CallbackQuery) -> None:
    lid = int(callback.data.split(":", 1)[1])
    chat = callback.message.chat if callback.message else None
    if not chat:
        return

    if chat.type in ("group", "supergroup"):
        admin = await _is_admin(callback.bot, chat.id, callback.from_user.id)
        if not admin:
            await callback.answer("Только администраторы могут изменять подписки группы.", show_alert=True)
            return

    await remove_level_subscription(chat.id, lid)
    await callback.answer("Вы отписались от этого уровня.", show_alert=True)
    await _send_subscriptions_list(callback, chat.id, chat.type in ("group", "supergroup"))

