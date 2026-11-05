"""
Тестирование хендлеров команд Telegram-бота.
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

TEST_DB_PATH = BASE_DIR / "test_handlers_bot.db"
os.environ["BOT_DB_PATH"] = str(TEST_DB_PATH)
os.environ["ADMIN_ID"] = "123456"

import db.database as db
from services.gdl_client import gdl
from tests.mock_gdl_server import MockGDLServer
from aiogram import Bot
from aiogram.types import Message, Chat, User
from datetime import datetime, timezone


def make_message(text: str, user_id: int = 123456, username: str = "testuser") -> Message:
    chat = Chat(id=user_id, type="private")
    from_user = User(id=user_id, is_bot=False, first_name="Test", username=username)
    return Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=chat,
        from_user=from_user,
        text=text,
    )


class TestHandlers(unittest.IsolatedAsyncioTestCase):
    server: MockGDLServer

    async def asyncSetUp(self):
        os.environ["BOT_DB_PATH"] = str(TEST_DB_PATH)
        await db.close_db()
        if TEST_DB_PATH.exists():
            TEST_DB_PATH.unlink()
        for ext in ("-wal", "-shm"):
            p = TEST_DB_PATH.with_name(TEST_DB_PATH.name + ext)
            if p.exists():
                p.unlink()
        await db.init_db()

        self.server = MockGDLServer()
        await self.server.start()

        gdl._base_url = self.server.url
        await gdl.start()

    async def asyncTearDown(self):
        await gdl.close()
        await self.server.stop()
        await db.close_db()
        if TEST_DB_PATH.exists():
            TEST_DB_PATH.unlink()
        for ext in ("-wal", "-shm"):
            p = TEST_DB_PATH.with_name(TEST_DB_PATH.name + ext)
            if p.exists():
                p.unlink()

    async def test_cmd_start(self):
        from handlers.core import cmd_start
        msg = make_message("/start")
        with patch.object(Message, "answer_photo", new_callable=AsyncMock) as mock_photo, \
             patch.object(Message, "answer", new_callable=AsyncMock) as mock_answer:
            await cmd_start(msg)
            self.assertTrue(mock_photo.called or mock_answer.called)

    async def test_cmd_link_and_me_and_unlink(self):
        from handlers.core import cmd_link, cmd_me, cmd_unlink, cmd_status

        # 1. /link Zoink
        msg_link = make_message("/link Zoink")
        with patch.object(Message, "answer", new_callable=AsyncMock) as mock_answer:
            await cmd_link(msg_link)
            self.assertTrue(any("привязан" in str(c).lower() for c in mock_answer.call_args_list))

        # 2. /me
        msg_me = make_message("/me")
        with patch.object(Message, "answer_photo", new_callable=AsyncMock) as mock_photo, \
             patch.object(Message, "answer", new_callable=AsyncMock) as mock_answer:
            await cmd_me(msg_me)
            self.assertTrue(mock_photo.called or any("Zoink" in str(c) for c in mock_answer.call_args_list))

        # 3. /status
        msg_status = make_message("/status")
        with patch.object(Message, "answer_photo", new_callable=AsyncMock) as mock_photo, \
             patch.object(Message, "answer", new_callable=AsyncMock) as mock_answer:
            await cmd_status(msg_status)
            self.assertTrue(mock_photo.called or any("Отслеживаемые" in str(c) or "Нет активных" in str(c) for c in mock_answer.call_args_list))

        # 4. /unlink
        msg_unlink = make_message("/unlink")
        with patch.object(Message, "answer", new_callable=AsyncMock) as mock_answer:
            await cmd_unlink(msg_unlink)
            self.assertIn("отвязан", mock_answer.call_args[0][0])

    async def test_cmd_player(self):
        from handlers.info import cmd_player
        msg = make_message("/player Zoink")
        with patch.object(Message, "answer_photo", new_callable=AsyncMock) as mock_photo, \
             patch.object(Message, "answer", new_callable=AsyncMock) as mock_answer:
            await cmd_player(msg)
            self.assertTrue(mock_photo.called or any("Zoink" in str(c) for c in mock_answer.call_args_list))

    async def test_cmd_level(self):
        from handlers.info import cmd_level
        msg = make_message("/level Tidal Wave")
        with patch.object(Message, "answer_photo", new_callable=AsyncMock) as mock_photo, \
             patch.object(Message, "answer", new_callable=AsyncMock) as mock_answer:
            await cmd_level(msg)
            self.assertTrue(mock_photo.called or any("Tidal Wave" in str(c) for c in mock_answer.call_args_list))

    async def test_cmd_top(self):
        from handlers.info import cmd_top
        msg = make_message("/top")
        with patch.object(Message, "answer_photo", new_callable=AsyncMock) as mock_photo, \
             patch.object(Message, "answer", new_callable=AsyncMock) as mock_answer:
            await cmd_top(msg)
            self.assertTrue(mock_photo.called or any("топ игроков" in str(c).lower() for c in mock_answer.call_args_list))

    async def test_cmd_topcountries(self):
        from handlers.info import cmd_topcountries
        msg = make_message("/topcountries")
        with patch.object(Message, "answer_photo", new_callable=AsyncMock) as mock_photo, \
             patch.object(Message, "answer", new_callable=AsyncMock) as mock_answer:
            await cmd_topcountries(msg)
            self.assertTrue(mock_photo.called or any("стран" in str(c).lower() for c in mock_answer.call_args_list))

    async def test_cmd_random(self):
        from handlers.info import cmd_random
        msg = make_message("/random 1-3")
        with patch.object(Message, "answer_photo", new_callable=AsyncMock) as mock_photo, \
             patch.object(Message, "answer", new_callable=AsyncMock) as mock_answer:
            await cmd_random(msg)
            self.assertTrue(mock_photo.called or any("Информация об уровне" in str(c) for c in mock_answer.call_args_list))

    async def test_cmd_hardest(self):
        from handlers.info import cmd_hardest
        msg = make_message("/hardest Zoink")
        with patch.object(Message, "answer_photo", new_callable=AsyncMock) as mock_photo, \
             patch.object(Message, "answer", new_callable=AsyncMock) as mock_answer:
            await cmd_hardest(msg)
            self.assertTrue(mock_photo.called or any("Топ-10 хардестов" in str(c) for c in mock_answer.call_args_list))

    async def test_cmd_compare(self):
        from handlers.info import cmd_compare
        msg = make_message("/compare Zoink, Trick")
        with patch.object(Message, "answer_photo", new_callable=AsyncMock) as mock_photo, \
             patch.object(Message, "answer", new_callable=AsyncMock) as mock_answer:
            await cmd_compare(msg)
            self.assertTrue(mock_photo.called or any("сравнение игроков" in str(c).lower() for c in mock_answer.call_args_list))

    async def test_cmd_admin_stats(self):
        from handlers.admin import cmd_stats
        msg = make_message("/stats", user_id=123456)
        with patch.object(Message, "answer", new_callable=AsyncMock) as mock_answer:
            await cmd_stats(msg)
            mock_answer.assert_called_once()

    async def test_cmd_start_deep_linking(self):
        from handlers.core import cmd_start
        msg_player = make_message("/start p_Zoink")
        with patch.object(Message, "answer_photo", new_callable=AsyncMock) as mock_photo, \
             patch.object(Message, "answer", new_callable=AsyncMock) as mock_answer:
            await cmd_start(msg_player)
            self.assertTrue(mock_photo.called or mock_answer.called)

        msg_level = make_message("/start l_101")
        with patch.object(Message, "answer_photo", new_callable=AsyncMock) as mock_photo, \
             patch.object(Message, "answer", new_callable=AsyncMock) as mock_answer:
            await cmd_start(msg_level)
            self.assertTrue(mock_photo.called or mock_answer.called)

    async def test_inline_search(self):
        from handlers.info import inline_search
        from aiogram.types import InlineQuery, InlineQueryResultArticle
        query = InlineQuery(
            id="q1",
            from_user=User(id=123, is_bot=False, first_name="User"),
            query="Zoink",
            offset="",
            chat_type="sender",
        )
        with patch.object(InlineQuery, "answer", new_callable=AsyncMock) as mock_answer:
            await inline_search(query)
            mock_answer.assert_called_once()
            results = mock_answer.call_args[0][0]
            self.assertTrue(len(results) > 0)
            self.assertIsInstance(results[0], InlineQueryResultArticle)
            self.assertIn("Zoink", results[0].title)
            self.assertNotIn("━━", results[0].input_message_content.message_text)
            self.assertIn("start=p_Zoink", results[0].reply_markup.inline_keyboard[0][0].url)

        query_lvl = InlineQuery(
            id="q2",
            from_user=User(id=123, is_bot=False, first_name="User"),
            query="Tidal",
            offset="",
            chat_type="sender",
        )
        with patch.object(InlineQuery, "answer", new_callable=AsyncMock) as mock_answer:
            await inline_search(query_lvl)
            mock_answer.assert_called_once()
            results = mock_answer.call_args[0][0]
            self.assertTrue(len(results) > 0)
            self.assertIsInstance(results[0], InlineQueryResultArticle)
            self.assertIn("start=l_", results[0].reply_markup.inline_keyboard[0][0].url)
            self.assertIn("Создатель:", results[0].input_message_content.message_text)

    async def test_cb_level_back(self):
        from handlers.info import cb_level_back
        from aiogram.types import CallbackQuery
        cb_msg = make_message("/dummy")
        cb = CallbackQuery(
            id="cb1",
            from_user=User(id=123, is_bot=False, first_name="User"),
            chat_instance="1",
            data="level_back:101",
            message=cb_msg,
        )
        with patch.object(Message, "edit_text", new_callable=AsyncMock) as mock_edit_text, \
             patch.object(Message, "edit_media", new_callable=AsyncMock) as mock_edit_media, \
             patch.object(Message, "answer_photo", new_callable=AsyncMock) as mock_answer_photo, \
             patch.object(CallbackQuery, "answer", new_callable=AsyncMock) as mock_cb_answer:
            await cb_level_back(cb)
            mock_cb_answer.assert_called_once()
            self.assertTrue(mock_edit_text.called or mock_edit_media.called or mock_answer_photo.called)


if __name__ == "__main__":
    unittest.main()
