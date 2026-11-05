import asyncio
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ["BOT_DB_PATH"] = ":memory:"
os.environ["GDL_API_BASE_URL"] = "http://127.0.0.1:8765/api/v1"

import db.database as db
from handlers.features import cmd_subscriptions, cmd_sub
from handlers.info import cmd_compare


class TestNewFeatures(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await db.init_db()

    async def asyncTearDown(self):
        await db.close_db()

    async def test_subscription_db_methods(self):
        # 1. Player subscription
        added = await db.add_player_subscription(12345, "private", "Zoink", 12345)
        self.assertTrue(added)
        # Duplicate ignore
        added_again = await db.add_player_subscription(12345, "private", "Zoink", 12345)
        self.assertFalse(added_again)

        is_sub = await db.is_subscribed_to_player(12345, "zoink")
        self.assertTrue(is_sub)

        subs = await db.get_player_subscriptions(12345)
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0]["player_name"], "Zoink")

        removed = await db.remove_player_subscription(12345, "Zoink")
        self.assertTrue(removed)
        self.assertFalse(await db.is_subscribed_to_player(12345, "Zoink"))

        # 2. Level subscription
        lvl_added = await db.add_level_subscription(999, "group", 101, "Tidal Wave", 12345)
        self.assertTrue(lvl_added)
        self.assertTrue(await db.is_subscribed_to_level(999, 101))

        lvl_subs = await db.get_level_subscriptions(999)
        self.assertEqual(len(lvl_subs), 1)
        self.assertEqual(lvl_subs[0]["level_name"], "Tidal Wave")

        lvl_removed = await db.remove_level_subscription(999, 101)
        self.assertTrue(lvl_removed)
        self.assertFalse(await db.is_subscribed_to_level(999, 101))

    async def test_cmd_compare_flow(self):
        msg = MagicMock()
        msg.text = "/compare Zoink, Trick"
        msg.reply_to_message = None
        msg.answer = AsyncMock()
        msg.answer_photo = AsyncMock()

        # Mock gdl
        with patch("handlers.info.gdl") as mock_gdl:
            mock_gdl.get_user = AsyncMock(side_effect=lambda name: {
                "id": 1 if name == "Zoink" else 2,
                "username": name,
                "placement": 1 if name == "Zoink" else 2,
                "points": 5000 if name == "Zoink" else 4500,
                "country": "US" if name == "Zoink" else "RU",
            })
            mock_gdl.get_records = AsyncMock(return_value=[
                {"status": "accepted", "percent": 100, "level": {"name": "Acheron", "placement": 3}}
            ])
            mock_gdl.get_country_position = AsyncMock(return_value=1)

            await cmd_compare(msg)

            msg.answer_photo.assert_called_once()
            args, kwargs = msg.answer_photo.call_args
            caption = kwargs.get("caption", "")
            self.assertIn("Сравнение игроков", caption)
            self.assertIn("Zoink", caption)
            self.assertIn("Trick", caption)

    async def test_cmd_subscriptions_view(self):
        msg = MagicMock()
        msg.chat.id = 555
        msg.chat.type = "private"
        msg.from_user.id = 555
        msg.answer_photo = AsyncMock()
        msg.answer = AsyncMock()

        await db.add_player_subscription(555, "private", "Zoink", 555)
        await cmd_subscriptions(msg)

        msg.answer_photo.assert_called_once()
        args, kwargs = msg.answer_photo.call_args
        caption = kwargs.get("caption", "")
        self.assertIn("Zoink", caption)
        self.assertIn("Ваши активные подписки", caption)


if __name__ == "__main__":
    unittest.main()
