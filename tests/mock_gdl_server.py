"""
Mock Global Demonlist Server (aiohttp.web).
Симулирует реальный REST API demonlist.org для точного локального тестирования.
"""

import socket
from aiohttp import web
from typing import Any


def get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class MockGDLServer:
    def __init__(self, port: int | None = None):
        self.port = port or get_free_port()
        self.app = web.Application()
        self.runner: web.AppRunner | None = None
        self.site: web.TCPSite | None = None

        # Состояние для тестов
        self.fail_429_count = 0
        self.fail_500_count = 0
        self.loop_records = False

        self.users = [
            {"id": 1, "username": "Zoink", "points": 5000.5, "placement": 1, "country": "United-States"},
            {"id": 2, "username": "Trick", "points": 4500.0, "placement": 2, "country": "Russia"},
            {"id": 3, "username": "Doggie", "points": 3800.2, "placement": 3, "country": "United-States"},
        ]

        self.levels = [
            {
                "id": 101,
                "placement": 1,
                "name": "Tidal Wave",
                "creator": "Onilink",
                "points": 500.0,
                "list_percent": 100,
                "ingame_id": 95821034,
                "game_version": "2.1",
                "objects": 150000,
                "length": 120,
                "date_created": "2023-09-15T12:00:00Z",
                "verification": {"username": "Zoink", "video_url": "https://youtube.com/watch?v=tidalwave"},
                "song_url": "https://newgrounds.com/audio/listen/123456",
            },
            {
                "id": 102,
                "placement": 2,
                "name": "Acheron",
                "creator": "ryamer",
                "points": 450.0,
                "list_percent": 65,
                "ingame_id": 83451234,
                "game_version": "2.1",
                "objects": 95000,
                "length": 110,
                "date_created": "2022-08-20T12:00:00Z",
                "verification": {"username": "Zoink", "video_url": "https://youtube.com/watch?v=acheron"},
            },
            {
                "id": 103,
                "placement": 3,
                "name": "Silent Clubstep",
                "creator": "Sailent",
                "points": 400.0,
                "list_percent": 100,
                "ingame_id": 78912345,
                "game_version": "2.1",
                "objects": 60000,
                "length": 90,
                "date_created": "2022-07-10T12:00:00Z",
            },
        ]

        self.records = [
            {
                "id": 501,
                "user_id": 1,
                "status": "accepted",
                "percent": 100,
                "video_url": "https://youtube.com/watch?v=rec1",
                "level": {"id": 101, "name": "Tidal Wave", "placement": 1},
            },
            {
                "id": 502,
                "user_id": 2,
                "status": "waiting",
                "percent": 100,
                "video_url": "https://youtube.com/watch?v=rec2",
                "level": {"id": 102, "name": "Acheron", "placement": 2},
            },
        ]

        self.countries = [
            {"placement": 1, "title": "United-States", "points": 15000},
            {"placement": 2, "title": "Russia", "points": 12500},
            {"placement": 3, "title": "Germany", "points": 9000},
        ]

        self._setup_routes()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _setup_routes(self):
        self.app.router.add_get("/leaderboard/user/list", self.handle_users)
        self.app.router.add_get("/leaderboard/country/list", self.handle_countries)
        self.app.router.add_get("/level/classic/get", self.handle_level_get)
        self.app.router.add_get("/level/classic/list", self.handle_levels_list)
        self.app.router.add_get("/level/classic/record/list", self.handle_level_records)
        self.app.router.add_get("/user/record/list", self.handle_user_records)

    def _check_fault_injection(self):
        if self.fail_429_count > 0:
            self.fail_429_count -= 1
            return web.Response(status=429, headers={"Retry-After": "1"}, text="Too Many Requests")
        if self.fail_500_count > 0:
            self.fail_500_count -= 1
            return web.Response(status=500, text="Internal Server Error")
        return None

    async def handle_users(self, request: web.Request) -> web.Response:
        fault = self._check_fault_injection()
        if fault: return fault

        search = request.query.get("search", "").lower()
        country = request.query.get("country")
        offset = int(request.query.get("offset", 0))
        limit = int(request.query.get("limit", 25))

        res = self.users
        if search:
            res = [u for u in res if search in u["username"].lower()]
        if country:
            res = [u for u in res if u.get("country", "").lower() == country.lower()]

        page = res[offset : offset + limit]
        return web.json_response({"data": {"users": page}})

    async def handle_countries(self, request: web.Request) -> web.Response:
        fault = self._check_fault_injection()
        if fault: return fault
        return web.json_response({"data": {"countries": self.countries}})

    async def handle_level_get(self, request: web.Request) -> web.Response:
        fault = self._check_fault_injection()
        if fault: return fault

        level_id = int(request.query.get("id", 0))
        for lvl in self.levels:
            if lvl["id"] == level_id:
                return web.json_response({"data": lvl})
        return web.Response(status=404, text="Level not found")

    async def handle_levels_list(self, request: web.Request) -> web.Response:
        fault = self._check_fault_injection()
        if fault: return fault

        search = request.query.get("search", "").lower()
        offset = int(request.query.get("offset", 0))
        limit = int(request.query.get("limit", 15))

        res = self.levels
        if search:
            res = [l for l in res if search in l["name"].lower()]

        page = res[offset : offset + limit]
        return web.json_response({"data": {"levels": page}})

    async def handle_level_records(self, request: web.Request) -> web.Response:
        fault = self._check_fault_injection()
        if fault: return fault

        level_id = int(request.query.get("level_id", 0))
        offset = int(request.query.get("offset", 0))
        limit = int(request.query.get("limit", 15))

        recs = [r for r in self.records if (r.get("level") or {}).get("id") == level_id and r.get("status") == "accepted"]
        page = recs[offset : offset + limit]
        return web.json_response({"data": {"records": page, "completed_count": len(recs)}})

    async def handle_user_records(self, request: web.Request) -> web.Response:
        fault = self._check_fault_injection()
        if fault: return fault

        user_id = int(request.query.get("user_id", 0))
        offset = int(request.query.get("offset", 0))
        limit = int(request.query.get("limit", 50))

        if self.loop_records:
            return web.json_response({"data": {"records": self.records}})

        user_recs = [r for r in self.records if r.get("user_id") == user_id]
        page = user_recs[offset : offset + limit]
        return web.json_response({"data": {"records": page}})

    async def start(self):
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "127.0.0.1", self.port)
        await self.site.start()

    async def stop(self):
        if self.site:
            await self.site.stop()
        if self.runner:
            await self.runner.cleanup()
