"""
Background checker — безопасный фоновый таск проверки рекордов.
"""

import asyncio
import logging
import random
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter

from db.database import (
    all_links,
    get_pending_records,
    add_pending_record,
    delete_pending_record,
    delete_link,
    delete_all_pending,
    all_watched_levels,
    get_users_watching_level,
    update_level_placement,
    sync_level_placements,
    get_unique_tracked_players,
    get_all_player_subscribers,
    get_player_record_hash,
    update_player_record_hash,
    get_all_level_subscribers,
)
from services.gdl_client import gdl, APIError
from services.formatting import h, safe_url
from services.image_generator import render_player_card, render_level_card

logger = logging.getLogger(__name__)

CHECK_INTERVAL = 90
USER_PAUSE = 0.4


async def _notify(bot: Bot, telegram_id: int, text: str) -> str:
    try:
        await bot.send_message(
            telegram_id, text, parse_mode="HTML", disable_web_page_preview=True
        )
        return "sent"
    except TelegramForbiddenError:
        logger.info("User %d blocked the bot", telegram_id)
        return "blocked"
    except TelegramRetryAfter as exc:
        logger.warning("Telegram rate limit, sleeping %ds", exc.retry_after)
        await asyncio.sleep(exc.retry_after)
        try:
            await bot.send_message(
                telegram_id, text, parse_mode="HTML", disable_web_page_preview=True
            )
            return "sent"
        except TelegramForbiddenError:
            return "blocked"
        except Exception:
            return "failed"
    except Exception as exc:
        logger.error("Failed to notify %d: %s", telegram_id, exc)
        return "failed"


async def _check_user(
    bot: Bot,
    telegram_id: int,
    gdl_user_id: int,
    gdl_username: str,
    records_cache: dict[int, list[dict]],
) -> None:
    if gdl_user_id in records_cache:
        records = records_cache[gdl_user_id]
    else:
        try:
            records = await gdl.get_records(gdl_user_id)
            records_cache[gdl_user_id] = records
        except APIError as exc:
            logger.warning("API error for user %d: %s", gdl_user_id, exc)
            return

    current_by_id = {str(r["id"]): r for r in records if isinstance(r, dict) and "id" in r}

    pending = await get_pending_records(telegram_id, gdl_user_id)
    for row in pending:
        record_id = str(row["record_id"])
        current = current_by_id.get(record_id)

        if current is None:
            await delete_pending_record(telegram_id, row["record_id"])
            continue

        status = current.get("status")
        if status == "waiting":
            continue

        level = current.get("level") or {}
        level_name = level.get("name") or row["level_name"] or "Неизвестный уровень"
        percent = current.get("percent") if current.get("percent") is not None else (row["percent"] or "?")
        video = safe_url(current.get("video_url") or row["video_url"])

        status_text = "Рекорд принят!" if status == "accepted" else "Рекорд отклонён."

        text = (
            f"<b>{status_text}</b>\n\n"
            f"Игрок: <b>{h(gdl_username)}</b>\n"
            f"Уровень: <b>{h(level_name)}</b> — {percent}%"
        )
        if video:
            text += f'\nВидео: <a href="{h(video)}">Ссылка на видео</a>'

        result = await _notify(bot, telegram_id, text)
        if result == "sent":
            await delete_pending_record(telegram_id, row["record_id"])
        elif result == "blocked":
            await delete_link(telegram_id)
            await delete_all_pending(telegram_id)
            return

    for record in records:
        if not isinstance(record, dict) or record.get("status") != "waiting":
            continue
        level = record.get("level") or {}
        await add_pending_record(
            telegram_id=telegram_id,
            gdl_user_id=gdl_user_id,
            record_id=record["id"],
            level_name=level.get("name"),
            percent=record.get("percent"),
            video_url=record.get("video_url"),
        )


async def checker_loop(bot: Bot) -> None:
    logger.info("Checker started (interval=%ds)", CHECK_INTERVAL)

    while True:
        jitter = random.uniform(-0.1, 0.1) * CHECK_INTERVAL
        await asyncio.sleep(CHECK_INTERVAL + jitter)

        links = await all_links()
        if not links:
            continue

        records_cache: dict[int, list[dict]] = {}

        for link in links:
            try:
                await _check_user(
                    bot=bot,
                    telegram_id=link["telegram_id"],
                    gdl_user_id=link["gdl_user_id"],
                    gdl_username=link["gdl_username"],
                    records_cache=records_cache,
                )
            except Exception:
                logger.exception(
                    "Unexpected error checking user %d (tg: %d)",
                    link["gdl_user_id"], link["telegram_id"],
                )

            await asyncio.sleep(USER_PAUSE)

        # Check watched levels for placement changes
        try:
            top_map: dict[int, dict] = {}
            # Sync full list placements periodically to maintain changelog
            try:
                top_levels = await gdl.get_levels(limit=150)
                if top_levels:
                    top_map = {
                        lvl["id"]: lvl for lvl in top_levels
                        if isinstance(lvl, dict) and "id" in lvl
                    }
                    detected_changes = await sync_level_placements(top_levels)
                    # Если были перемещения уровней, уведомляем подписчиков этих уровней
                    for ch in detected_changes:
                        lid = ch["level_id"]
                        subscribers = await get_all_level_subscribers(lid)
                        if subscribers:
                            lvl_name = ch["level_name"]
                            ctype = ch["change_type"]
                            old_p = ch.get("old_placement")
                            new_p = ch.get("new_placement")
                            if ctype == "added":
                                msg_txt = f"<b>Новый уровень в Demonlist!</b>\n\nУровень <b>{h(lvl_name)}</b> добавлен на <b>#{new_p}</b> место."
                            elif ctype == "moved":
                                dir_str = "поднялся" if (old_p and new_p and new_p < old_p) else "опустился"
                                msg_txt = f"<b>Смещение уровня в Demonlist!</b>\n\nУровень <b>{h(lvl_name)}</b> {dir_str} с #{old_p} на <b>#{new_p}</b>."
                            else:
                                msg_txt = f"<b>Изменение уровня:</b> {h(lvl_name)}"

                            for s in subscribers:
                                await _notify(bot, s["chat_id"], msg_txt)
                                await asyncio.sleep(0.1)
            except Exception as sync_exc:
                logger.debug("Failed to sync placements for changelog: %s", sync_exc)

            watched_levels = await all_watched_levels()
            for w in watched_levels:
                lid = w["level_id"]
                last_place = w["last_placement"]
                try:
                    lvl_data = top_map.get(lid)
                    if not lvl_data:
                        lvl_data = await gdl.get_level_info(lid)
                        await asyncio.sleep(USER_PAUSE)

                    if lvl_data:
                        new_place = lvl_data.get("placement")
                        lvl_name = lvl_data.get("name") or f"ID {lid}"
                        if new_place is not None and last_place is not None and new_place != last_place:
                            change_dir = "поднялся" if new_place < last_place else "опустился"
                            watchers = await get_users_watching_level(lid)
                            notify_text = (
                                f"<b>Изменение позиции в Demonlist!</b>\n\n"
                                f"Уровень: <b>{h(lvl_name)}</b>\n"
                                f"Позиция: #{last_place} -> #{new_place} ({change_dir})"
                            )
                            for uid in watchers:
                                await _notify(bot, uid, notify_text)
                                await asyncio.sleep(0.1)
                            await update_level_placement(lid, new_place)
                        elif last_place is None and new_place is not None:
                            await update_level_placement(lid, new_place)
                except Exception as exc:
                    logger.debug("Failed to check watched level %d: %s", lid, exc)
        except Exception as exc:
            logger.error("Error checking watched levels: %s", exc)

        # Check subscribed players for new records
        try:
            tracked_players = await get_unique_tracked_players()
            for player_name in tracked_players:
                try:
                    u_data = await gdl.get_user(player_name)
                    if not u_data:
                        continue
                    recs = await gdl.get_records(u_data["id"])
                    accepted = [r for r in recs if r.get("status") == "accepted"]

                    prev_state = await get_player_record_hash(player_name)
                    curr_count = len(accepted)
                    curr_last_id = accepted[0]["id"] if accepted else 0

                    if prev_state is None:
                        # Первичная фиксация состояния, чтобы не спамить старыми рекордами
                        await update_player_record_hash(player_name, curr_last_id, curr_count)
                    else:
                        prev_count = prev_state["records_count"]
                        if curr_count > prev_count:
                            # Есть новые подтвержденные рекорды
                            diff_count = curr_count - prev_count
                            new_records = accepted[:diff_count]
                            subscribers = await get_all_player_subscribers(player_name)

                            for r in new_records:
                                lvl = r.get("level") or {}
                                lvl_name = lvl.get("name", "Unknown")
                                lvl_place = lvl.get("placement", "?")
                                pct = r.get("percent", 100)
                                v_url = safe_url(r.get("video_url"))

                                notify_txt = (
                                    f"<b>Новый рекорд игрока {h(player_name)}!</b>\n\n"
                                    f"Уровень: <b>{h(lvl_name)}</b> (#{lvl_place})\n"
                                    f"Прогресс: <b>{pct}%</b>"
                                )
                                if v_url:
                                    notify_txt += f'\nВидео: <a href="{h(v_url)}">Смотреть пруф</a>'

                                for s in subscribers:
                                    await _notify(bot, s["chat_id"], notify_txt)
                                    await asyncio.sleep(0.1)

                            await update_player_record_hash(player_name, curr_last_id, curr_count)
                except Exception as p_exc:
                    logger.debug("Failed to check records for tracked player %s: %s", player_name, p_exc)
                await asyncio.sleep(USER_PAUSE)
        except Exception as exc:
            logger.error("Error checking tracked player subscriptions: %s", exc)

