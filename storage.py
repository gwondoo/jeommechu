from __future__ import annotations

import asyncio
import json
import os
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class JsonStore:
    """Small, atomic JSON store. One bot process is assumed."""

    def __init__(self, path: str | Path = "data/state.json") -> None:
        self.path = Path(path)
        self._lock = asyncio.Lock()
        self._data: dict[str, Any] = {"version": 1, "guilds": {}}

    async def load(self) -> None:
        async with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists():
                try:
                    self._data = json.loads(self.path.read_text(encoding="utf-8"))
                except json.JSONDecodeError as exc:
                    raise RuntimeError(f"저장 파일을 읽을 수 없습니다: {self.path}") from exc
            else:
                self._write_unlocked()

    def _write_unlocked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix="state-", suffix=".json", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as temp:
                json.dump(self._data, temp, ensure_ascii=False, indent=2)
                temp.write("\n")
                temp.flush()
                os.fsync(temp.fileno())
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def _guild_unlocked(self, guild_id: int) -> dict[str, Any]:
        guilds = self._data.setdefault("guilds", {})
        return guilds.setdefault(
            str(guild_id),
            {"company": None, "meal_ticket_restaurants": [], "nearby_cache": {}},
        )

    async def get_guild(self, guild_id: int) -> dict[str, Any]:
        async with self._lock:
            return deepcopy(self._guild_unlocked(guild_id))

    async def set_company(self, guild_id: int, company: dict[str, Any]) -> None:
        async with self._lock:
            self._guild_unlocked(guild_id)["company"] = company
            self._write_unlocked()

    async def restaurants(self, guild_id: int) -> list[dict[str, Any]]:
        async with self._lock:
            return deepcopy(self._guild_unlocked(guild_id)["meal_ticket_restaurants"])

    async def add_restaurant(self, guild_id: int, restaurant: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            restaurants = self._guild_unlocked(guild_id)["meal_ticket_restaurants"]
            key = (restaurant["name"].strip().casefold(), restaurant.get("address", "").strip().casefold())
            if any((r["name"].strip().casefold(), r.get("address", "").strip().casefold()) == key for r in restaurants):
                raise ValueError("이미 같은 이름과 주소로 등록된 식당입니다.")
            restaurant = {**restaurant, "id": uuid4().hex, "created_at": now_iso(), "updated_at": now_iso()}
            restaurants.append(restaurant)
            self._write_unlocked()
            return deepcopy(restaurant)

    async def update_restaurant(self, guild_id: int, name: str, changes: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            restaurants = self._guild_unlocked(guild_id)["meal_ticket_restaurants"]
            target = next((r for r in restaurants if r["name"].casefold() == name.casefold()), None)
            if target is None:
                raise KeyError(name)
            target.update({key: value for key, value in changes.items() if value is not None})
            target["updated_at"] = now_iso()
            self._write_unlocked()
            return deepcopy(target)

    async def delete_restaurant(self, guild_id: int, name: str) -> dict[str, Any]:
        async with self._lock:
            restaurants = self._guild_unlocked(guild_id)["meal_ticket_restaurants"]
            for index, restaurant in enumerate(restaurants):
                if restaurant["name"].casefold() == name.casefold():
                    removed = restaurants.pop(index)
                    self._write_unlocked()
                    return deepcopy(removed)
            raise KeyError(name)

    async def get_nearby_cache(self, guild_id: int, radius: int) -> list[dict[str, Any]]:
        async with self._lock:
            cache = self._guild_unlocked(guild_id).get("nearby_cache", {})
            if cache.get("radius") == radius:
                return deepcopy(cache.get("places", []))
            return []

    async def set_nearby_cache(self, guild_id: int, radius: int, places: list[dict[str, Any]]) -> None:
        async with self._lock:
            self._guild_unlocked(guild_id)["nearby_cache"] = {
                "radius": radius,
                "fetched_at": now_iso(),
                "places": places,
            }
            self._write_unlocked()

