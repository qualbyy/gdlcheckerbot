"""
Комплексный набор тестов для GDL Bot.
Запуск: .venv/bin/python tests/test_all.py
"""

import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

TEST_DB_PATH = BASE_DIR / "test_bot.db"
os.environ["BOT_DB_PATH"] = str(TEST_DB_PATH)

import db.database as db
from services.formatting import (
    h, safe_url, country_code, record_rank, fmt_date, validate_username
)
from services.gdl_client import GDLClient, APIError
from tests.mock_gdl_server import MockGDLServer
import tasks.checker as checker
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError


class TestFormatting(unittest.TestCase):
    def test_html_escape(self):
        self.assertEqual(h("<hello & world>"), "&lt;hello &amp; world&gt;")
        self.assertEqual(h(None), "")
        self.assertEqual(h(123), "123")

    def test_safe_url(self):
        self.assertEqual(safe_url("https://youtube.com/watch?v=123"), "https://youtube.com/watch?v=123")
        self.assertEqual(safe_url("http://example.com"), "http://example.com")
        self.assertIsNone(safe_url("javascript:alert(1)"))
        self.assertIsNone(safe_url("ftp://server.com"))
        self.assertIsNone(safe_url(None))

    def test_country_codes(self):
        self.assertEqual(country_code("Russia"), "RU")
        self.assertEqual(country_code("United-States"), "US")
        self.assertEqual(country_code("KR"), "KR")
        self.assertEqual(country_code(None), "")
        self.assertEqual(country_code("Atlantis"), "")

    def test_record_rank_safe(self):
        self.assertEqual(record_rank({"level": {"placement": 5}}), 5)
        self.assertEqual(record_rank({"level": {"placement": None}}), 999999)
        self.assertEqual(record_rank({"level": None}), 999999)
        self.assertEqual(record_rank({}), 999999)
        self.assertEqual(record_rank({"level": {"placement": "invalid"}}), 999999)

    def test_validate_username(self):
        self.assertTrue(validate_username("Zoink"))
        self.assertTrue(validate_username("Player_123-x"))
        self.assertFalse(validate_username(""))
        self.assertFalse(validate_username("a" * 35))
        self.assertFalse(validate_username("User$Name"))


class AsyncTestCase(unittest.IsolatedAsyncioTestCase):
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

    async def asyncTearDown(self):
        await db.close_db()
        if TEST_DB_PATH.exists():
            TEST_DB_PATH.unlink()
        for ext in ("-wal", "-shm"):
            p = TEST_DB_PATH.with_name(TEST_DB_PATH.name + ext)
            if p.exists():
                p.unlink()


class TestDatabase(AsyncTestCase):
    async def test_links_crud(self):
        await db.upsert_link(111, 1, "Zoink")
        link = await db.get_link(111)
        self.assertIsNotNone(link)
        self.assertEqual(link["gdl_username"], "Zoink")
        self.assertEqual(await db.count_links(), 1)

        await db.add_pending_record(111, 1, 501, "Tidal Wave", 100, "https://yt.com")
        pending = await db.get_pending_records(111, 1)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["level_name"], "Tidal Wave")

        await db.upsert_link(111, 2, "Trick")
        new_link = await db.get_link(111)
        self.assertEqual(new_link["gdl_username"], "Trick")
        pending_after = await db.get_pending_by_telegram(111)
        self.assertEqual(len(pending_after), 0)

        deleted = await db.delete_link(111)
        self.assertTrue(deleted)
        self.assertIsNone(await db.get_link(111))
        self.assertEqual(await db.count_links(), 0)


class TestGDLClient(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.server = MockGDLServer()
        await self.server.start()
        self.client = GDLClient(base_url=self.server.url)
        await self.client.start()

    async def asyncTearDown(self):
        await self.client.close()
        await self.server.stop()

    async def test_get_user(self):
        user = await self.client.get_user("Zoink")
        self.assertIsNotNone(user)
        self.assertEqual(user["username"], "Zoink")
        self.assertEqual(user["placement"], 1)

    async def test_get_level_info(self):
        lvl = await self.client.get_level_info(101)
        self.assertIsNotNone(lvl)
        self.assertEqual(lvl["name"], "Tidal Wave")
        self.assertEqual(lvl["creator"], "Onilink")

    async def test_search_level(self):
        lvl = await self.client.search_level("acheron")
        self.assertIsNotNone(lvl)
        self.assertEqual(lvl["name"], "Acheron")

    async def test_pagination_loop_detection(self):
        self.server.loop_records = True
        try:
            records = await self.client.get_records(1)
            self.assertTrue(len(records) > 0)
        finally:
            self.server.loop_records = False

    async def test_retry_on_429_and_500(self):
        self.server.fail_429_count = 1
        user = await self.client.get_user("Zoink")
        self.assertIsNotNone(user)

        self.server.fail_500_count = 1
        user500 = await self.client.get_user("Trick")
        self.assertIsNotNone(user500)


class TestCheckerAndBot(AsyncTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.server = MockGDLServer()
        await self.server.start()
        checker.gdl._base_url = self.server.url
        await checker.gdl.start()

    async def asyncTearDown(self):
        await checker.gdl.close()
        await self.server.stop()
        await super().asyncTearDown()

    async def test_checker_detects_waiting_and_notifies_on_accepted(self):
        mock_bot = AsyncMock(spec=Bot)
        mock_bot.send_message = AsyncMock(return_value=True)

        tg_id = 999001
        await db.upsert_link(tg_id, 2, "Trick")

        cache = {}
        await checker._check_user(mock_bot, tg_id, 2, "Trick", cache)

        pending = await db.get_pending_records(tg_id, 2)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["record_id"], 502)
        self.assertEqual(mock_bot.send_message.call_count, 0)

        for r in self.server.records:
            if r["id"] == 502:
                r["status"] = "accepted"

        cache = {}
        await checker._check_user(mock_bot, tg_id, 2, "Trick", cache)

        self.assertEqual(mock_bot.send_message.call_count, 1)
        sent_text = mock_bot.send_message.call_args[0][1]
        self.assertIn("Рекорд принят!", sent_text)
        self.assertIn("Acheron", sent_text)

        pending_after = await db.get_pending_records(tg_id, 2)
        self.assertEqual(len(pending_after), 0)

    async def test_checker_handles_blocked_bot(self):
        mock_bot = AsyncMock(spec=Bot)
        mock_bot.send_message.side_effect = TelegramForbiddenError(
            method=MagicMock(), message="Forbidden: bot was blocked by the user"
        )

        tg_id = 999002
        await db.upsert_link(tg_id, 2, "Trick")
        await db.add_pending_record(tg_id, 2, 502, "Acheron", 100, None)

        for r in self.server.records:
            if r["id"] == 502:
                r["status"] = "accepted"

        cache = {}
        await checker._check_user(mock_bot, tg_id, 2, "Trick", cache)

        link = await db.get_link(tg_id)
        self.assertIsNone(link)


if __name__ == "__main__":
    unittest.main()
