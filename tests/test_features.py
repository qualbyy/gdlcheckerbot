"""
Тесты для новых функций из ROADMAP (Разделы 1 и 2):
/progress, /card, /watch, /unwatch, /watchlist, /roulette, /predict, /changes, /guild
"""

import os
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest
from unittest.mock import AsyncMock, patch

from aiogram.enums import ChatType
from aiogram.types import Chat, Message, User, PhotoSize

from db.database import init_db, upsert_link, close_db, remove_level_watcher
from handlers.features import (
    cmd_watch,
    cmd_unwatch,
    cmd_watchlist,
    cmd_roulette,
    cmd_changes,
    cmd_guild,
    cmd_predict,
    cmd_progress,
)
from services.gdl_client import gdl
from tests.mock_gdl_server import MockGDLServer, get_free_port


def make_message(text: str, user_id: int = 12345, username: str = "testuser", is_group: bool = False, has_photo: bool = False) -> Message:
    chat_type = ChatType.GROUP if is_group else ChatType.PRIVATE
    chat_id = -10012345 if is_group else user_id
    photo = [PhotoSize(file_id="p1", file_unique_id="u1", width=100, height=100)] if has_photo else None
    return Message(
        message_id=1,
        date=1700000000,
        chat=Chat(id=chat_id, type=chat_type),
        from_user=User(id=user_id, is_bot=False, first_name="Test", username=username),
        text=text,
        photo=photo,
    )


class TestRoadmapFeatures(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db_path = f"/tmp/test_features_{get_free_port()}.db"
        os.environ["BOT_DB_PATH"] = self.db_path
        self.port = get_free_port()
        os.environ["GDL_API_URL"] = f"http://127.0.0.1:{self.port}"
        self.server = MockGDLServer(port=self.port)
        await self.server.start()
        gdl._base_url = f"http://127.0.0.1:{self.port}"
        await gdl.start()
        await close_db()
        await init_db()

    async def asyncTearDown(self):
        await gdl.close()
        await close_db()
        await self.server.stop()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    async def test_cmd_watch_and_watchlist(self):
        msg_watch = make_message("/watch 101")
        with patch.object(Message, "answer", new_callable=AsyncMock) as mock_ans:
            await cmd_watch(msg_watch)
            self.assertTrue(mock_ans.called)
            args = mock_ans.call_args[0][0]
            self.assertIn("добавлен в отслеживаемые", args)

        msg_list = make_message("/watchlist")
        with patch.object(Message, "answer_photo", new_callable=AsyncMock) as mock_photo, \
             patch.object(Message, "answer", new_callable=AsyncMock) as mock_ans:
            await cmd_watchlist(msg_list)
            self.assertTrue(mock_photo.called or mock_ans.called)
            caption = mock_photo.call_args.kwargs.get("caption") if mock_photo.called else mock_ans.call_args[0][0]
            self.assertIn("Отслеживаемые демоны", caption)

        msg_unwatch = make_message("/unwatch Tidal Wave")
        with patch.object(Message, "answer", new_callable=AsyncMock) as mock_ans:
            await cmd_unwatch(msg_unwatch)
            self.assertTrue(mock_ans.called)
            args = mock_ans.call_args[0][0]
            self.assertIn("удален из отслеживаемых", args)

    async def test_cmd_roulette(self):
        msg = make_message("/roulette 1-10")
        with patch.object(Message, "answer_photo", new_callable=AsyncMock) as mock_photo, \
             patch.object(Message, "answer", new_callable=AsyncMock) as mock_ans:
            await cmd_roulette(msg)
            self.assertTrue(mock_photo.called or mock_ans.called)
            caption = mock_photo.call_args.kwargs.get("caption") if mock_photo.called else mock_ans.call_args[0][0]
            self.assertIn("DEMON ROULETTE", caption.upper())

    async def test_cmd_changes(self):
        msg = make_message("/changes")
        with patch.object(Message, "answer_photo", new_callable=AsyncMock) as mock_photo, \
             patch.object(Message, "answer", new_callable=AsyncMock) as mock_ans:
            await cmd_changes(msg)
            self.assertTrue(mock_photo.called or mock_ans.called)
            caption = mock_photo.call_args.kwargs.get("caption") if mock_photo.called else mock_ans.call_args[0][0]
            self.assertIn("Ченджлог обновлений Demonlist", caption)

    async def test_cmd_guild(self):
        await upsert_link(telegram_id=12345, gdl_user_id=1, gdl_username="Zoink")
        msg = make_message("/guild", is_group=True)
        with patch.object(Message, "answer_photo", new_callable=AsyncMock) as mock_photo, \
             patch.object(Message, "answer", new_callable=AsyncMock) as mock_ans:
            await cmd_guild(msg)
            self.assertTrue(mock_photo.called or mock_ans.called)
            caption = mock_photo.call_args.kwargs.get("caption") if mock_photo.called else mock_ans.call_args[0][0]
            self.assertIn("Рейтинг участников чата", caption)
            self.assertIn("Zoink", caption)

    async def test_cmd_predict(self):
        await upsert_link(telegram_id=12345, gdl_user_id=1, gdl_username="Zoink")
        msg = make_message("/predict 101")
        with patch.object(Message, "answer_photo", new_callable=AsyncMock) as mock_photo, \
             patch.object(Message, "answer", new_callable=AsyncMock) as mock_ans:
            await cmd_predict(msg)
            self.assertTrue(mock_photo.called or mock_ans.called)
            caption = mock_photo.call_args.kwargs.get("caption") if mock_photo.called else mock_ans.call_args[0][0]
            self.assertIn("Симуляция очков для игрока", caption)
            self.assertIn("Zoink", caption)

    async def test_cmd_progress(self):
        await upsert_link(telegram_id=12345, gdl_user_id=1, gdl_username="Zoink")
        msg = make_message("/progress")
        with patch.object(Message, "answer_photo", new_callable=AsyncMock) as mock_photo, \
             patch.object(Message, "answer", new_callable=AsyncMock) as mock_ans:
            await cmd_progress(msg)
            self.assertTrue(mock_photo.called or mock_ans.called)
            caption = mock_photo.call_args.kwargs.get("caption") if mock_photo.called else mock_ans.call_args[0][0]
            self.assertIn("Трекер прогресса", caption)

    async def test_roulette_callbacks(self):
        from handlers.features import cb_roulette_next, cb_roulette_reroll, cb_roulette_stop
        from aiogram.types import CallbackQuery

        cb_msg = make_message("roulette", has_photo=True)

        # 1. next
        cb_next = CallbackQuery(
            id="c1",
            from_user=User(id=12345, is_bot=False, first_name="User"),
            chat_instance="ci",
            data="roulette:next:5:1",
            message=cb_msg,
        )
        with patch.object(Message, "edit_caption", new_callable=AsyncMock) as mock_edit, \
             patch.object(CallbackQuery, "answer", new_callable=AsyncMock) as mock_ans:
            await cb_roulette_next(cb_next)
            self.assertTrue(mock_edit.called)
            self.assertIn("5%", mock_edit.call_args.kwargs.get("caption", ""))

        # 2. reroll
        cb_reroll = CallbackQuery(
            id="c2",
            from_user=User(id=12345, is_bot=False, first_name="User"),
            chat_instance="ci",
            data="roulette:reroll:5:1",
            message=cb_msg,
        )
        with patch.object(Message, "edit_caption", new_callable=AsyncMock) as mock_edit, \
             patch.object(CallbackQuery, "answer", new_callable=AsyncMock) as mock_ans:
            await cb_roulette_reroll(cb_reroll)
            self.assertTrue(mock_edit.called)

        # 3. stop
        cb_stop = CallbackQuery(
            id="c3",
            from_user=User(id=12345, is_bot=False, first_name="User"),
            chat_instance="ci",
            data="roulette:stop:5",
            message=cb_msg,
        )
        with patch.object(Message, "edit_caption", new_callable=AsyncMock) as mock_edit, \
             patch.object(CallbackQuery, "answer", new_callable=AsyncMock) as mock_ans:
            await cb_roulette_stop(cb_stop)
            self.assertTrue(mock_edit.called)
            self.assertIn("4%", mock_edit.call_args.kwargs.get("caption", ""))


if __name__ == "__main__":
    unittest.main()
