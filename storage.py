from __future__ import annotations

import asyncio
import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _distance_meters(first: dict[str, Any], second: dict[str, Any]) -> float:
    lat1, lon1 = math.radians(float(first["latitude"])), math.radians(float(first["longitude"]))
    lat2, lon2 = math.radians(float(second["latitude"])), math.radians(float(second["longitude"]))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    value = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6_371_000 * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))


def place_key(place: dict[str, Any]) -> str:
    return str(place.get("kakao_place_id") or f"{place.get('name', '').strip().casefold()}|{place.get('address', '').strip().casefold()}")


class SqliteStore:
    """SQLite-backed state store for a single bot process."""

    def __init__(self, path: str | Path = "data/jeommechu.db", legacy_path: str | Path = "data/state.json") -> None:
        self.path = Path(path)
        self.legacy_path = Path(legacy_path)
        self._lock = asyncio.Lock()
        self._connection: sqlite3.Connection | None = None

    def _db(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("저장소가 아직 초기화되지 않았습니다.")
        return self._connection

    async def load(self) -> None:
        async with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            is_new = not self.path.exists()
            self._connection = sqlite3.connect(self.path)
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._create_schema()
            if is_new and self.legacy_path.exists():
                self._migrate_legacy_json()

    def _create_schema(self) -> None:
        self._db().executescript(
            """
            CREATE TABLE IF NOT EXISTS companies (
                guild_id INTEGER PRIMARY KEY,
                data_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS meal_ticket_restaurants (
                id TEXT PRIMARY KEY,
                guild_id INTEGER NOT NULL,
                name_key TEXT NOT NULL,
                address_key TEXT NOT NULL,
                data_json TEXT NOT NULL,
                UNIQUE (guild_id, name_key, address_key)
            );
            CREATE TABLE IF NOT EXISTS nearby_searches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                radius INTEGER NOT NULL,
                kind TEXT NOT NULL DEFAULT '',
                fetched_at TEXT NOT NULL,
                UNIQUE (guild_id, radius, kind)
            );
            CREATE TABLE IF NOT EXISTS nearby_places (
                search_id INTEGER NOT NULL REFERENCES nearby_searches(id) ON DELETE CASCADE,
                position INTEGER NOT NULL,
                place_key TEXT NOT NULL,
                data_json TEXT NOT NULL,
                PRIMARY KEY (search_id, place_key)
            );
            CREATE TABLE IF NOT EXISTS recommendation_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                source TEXT NOT NULL,
                place_key TEXT NOT NULL,
                recommended_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_recommendation_history_recent
                ON recommendation_history(guild_id, source, recommended_at DESC);
            """
        )
        self._db().commit()

    def _migrate_legacy_json(self) -> None:
        try:
            legacy = json.loads(self.legacy_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise RuntimeError(f"기존 저장 파일을 읽을 수 없습니다: {self.legacy_path}") from exc
        with self._db():
            for guild_id_text, guild in legacy.get("guilds", {}).items():
                guild_id = int(guild_id_text)
                company = guild.get("company")
                if company:
                    self._db().execute(
                        "INSERT OR REPLACE INTO companies(guild_id, data_json) VALUES (?, ?)",
                        (guild_id, json.dumps(company, ensure_ascii=False)),
                    )
                for place in guild.get("meal_ticket_restaurants", []):
                    self._insert_restaurant(guild_id, place)
                cache = guild.get("nearby_cache") or {}
                if cache.get("places"):
                    self._replace_nearby_cache(guild_id, int(cache.get("radius", 700)), "", cache["places"], cache.get("fetched_at"))

    def _insert_restaurant(self, guild_id: int, place: dict[str, Any]) -> None:
        self._db().execute(
            "INSERT INTO meal_ticket_restaurants(id, guild_id, name_key, address_key, data_json) VALUES (?, ?, ?, ?, ?)",
            (
                place["id"], guild_id, place["name"].strip().casefold(),
                place.get("address", "").strip().casefold(), json.dumps(place, ensure_ascii=False),
            ),
        )

    async def get_guild(self, guild_id: int) -> dict[str, Any]:
        async with self._lock:
            company_row = self._db().execute("SELECT data_json FROM companies WHERE guild_id = ?", (guild_id,)).fetchone()
            restaurants = self._restaurant_rows(guild_id)
            return {
                "company": json.loads(company_row["data_json"]) if company_row else None,
                "meal_ticket_restaurants": restaurants,
            }

    async def set_company(self, guild_id: int, company: dict[str, Any]) -> bool:
        """Save company settings and invalidate location data when coordinates/radius changed."""
        async with self._lock:
            row = self._db().execute("SELECT data_json FROM companies WHERE guild_id = ?", (guild_id,)).fetchone()
            previous = json.loads(row["data_json"]) if row else None
            changed = previous is None or _distance_meters(previous, company) >= 20 or int(previous.get("default_radius", 700)) != int(company["default_radius"])
            with self._db():
                if changed:
                    self._db().execute("DELETE FROM nearby_searches WHERE guild_id = ?", (guild_id,))
                    self._db().execute("DELETE FROM recommendation_history WHERE guild_id = ? AND source = 'nearby'", (guild_id,))
                self._db().execute(
                    "INSERT INTO companies(guild_id, data_json) VALUES (?, ?) ON CONFLICT(guild_id) DO UPDATE SET data_json = excluded.data_json",
                    (guild_id, json.dumps(company, ensure_ascii=False)),
                )
            return changed

    def _restaurant_rows(self, guild_id: int) -> list[dict[str, Any]]:
        rows = self._db().execute(
            "SELECT data_json FROM meal_ticket_restaurants WHERE guild_id = ? ORDER BY rowid", (guild_id,)
        ).fetchall()
        return [json.loads(row["data_json"]) for row in rows]

    async def restaurants(self, guild_id: int) -> list[dict[str, Any]]:
        async with self._lock:
            return self._restaurant_rows(guild_id)

    async def add_restaurant(self, guild_id: int, restaurant: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            restaurant = {**restaurant, "id": uuid4().hex, "created_at": now_iso(), "updated_at": now_iso()}
            try:
                with self._db():
                    self._insert_restaurant(guild_id, restaurant)
            except sqlite3.IntegrityError as exc:
                raise ValueError("이미 같은 이름과 주소로 등록된 식당입니다.") from exc
            return dict(restaurant)

    async def update_restaurant(self, guild_id: int, name: str, changes: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            row = self._db().execute(
                "SELECT id, data_json FROM meal_ticket_restaurants WHERE guild_id = ? AND name_key = ? LIMIT 1",
                (guild_id, name.casefold()),
            ).fetchone()
            if row is None:
                raise KeyError(name)
            place = json.loads(row["data_json"])
            place.update({key: value for key, value in changes.items() if value is not None})
            place["updated_at"] = now_iso()
            try:
                with self._db():
                    self._db().execute(
                        "UPDATE meal_ticket_restaurants SET name_key = ?, address_key = ?, data_json = ? WHERE id = ?",
                        (place["name"].strip().casefold(), place.get("address", "").strip().casefold(), json.dumps(place, ensure_ascii=False), row["id"]),
                    )
            except sqlite3.IntegrityError as exc:
                raise ValueError("이미 같은 이름과 주소로 등록된 식당입니다.") from exc
            return place

    async def delete_restaurant(self, guild_id: int, name: str) -> dict[str, Any]:
        async with self._lock:
            row = self._db().execute(
                "SELECT id, data_json FROM meal_ticket_restaurants WHERE guild_id = ? AND name_key = ? LIMIT 1",
                (guild_id, name.casefold()),
            ).fetchone()
            if row is None:
                raise KeyError(name)
            with self._db():
                self._db().execute("DELETE FROM meal_ticket_restaurants WHERE id = ?", (row["id"],))
            return json.loads(row["data_json"])

    async def get_nearby_cache(
        self,
        guild_id: int,
        radius: int,
        kind: str | None = None,
        max_age_hours: int = 6,
        company: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Return this guild's cache or a fresh cache from the same company location."""
        async with self._lock:
            cutoff = datetime.now(timezone.utc).astimezone() - timedelta(hours=max_age_hours)
            rows = self._db().execute(
                """
                SELECT searches.id, searches.guild_id, searches.fetched_at, companies.data_json AS company_json
                FROM nearby_searches AS searches
                LEFT JOIN companies ON companies.guild_id = searches.guild_id
                WHERE searches.radius = ? AND searches.kind = ?
                ORDER BY CASE WHEN searches.guild_id = ? THEN 0 ELSE 1 END, searches.fetched_at DESC
                """,
                (radius, kind or "", guild_id),
            ).fetchall()
            for row in rows:
                if datetime.fromisoformat(row["fetched_at"]) < cutoff:
                    continue
                if row["guild_id"] != guild_id:
                    if company is None or row["company_json"] is None:
                        continue
                    cached_company = json.loads(row["company_json"])
                    if _distance_meters(company, cached_company) >= 20:
                        continue
                place_rows = self._db().execute(
                    "SELECT data_json FROM nearby_places WHERE search_id = ? ORDER BY position", (row["id"],)
                ).fetchall()
                return [json.loads(item["data_json"]) for item in place_rows]
            return []

    def _replace_nearby_cache(self, guild_id: int, radius: int, kind: str, places: list[dict[str, Any]], fetched_at: str | None = None) -> None:
        self._db().execute("DELETE FROM nearby_searches WHERE guild_id = ? AND radius = ? AND kind = ?", (guild_id, radius, kind))
        cursor = self._db().execute(
            "INSERT INTO nearby_searches(guild_id, radius, kind, fetched_at) VALUES (?, ?, ?, ?)",
            (guild_id, radius, kind, fetched_at or now_iso()),
        )
        search_id = cursor.lastrowid
        self._db().executemany(
            "INSERT OR IGNORE INTO nearby_places(search_id, position, place_key, data_json) VALUES (?, ?, ?, ?)",
            [(search_id, index, place_key(place), json.dumps(place, ensure_ascii=False)) for index, place in enumerate(places)],
        )

    async def set_nearby_cache(self, guild_id: int, radius: int, places: list[dict[str, Any]], kind: str | None = None) -> None:
        async with self._lock:
            with self._db():
                self._replace_nearby_cache(guild_id, radius, kind or "", places)

    async def recent_recommendations(self, guild_id: int, source: str, limit: int = 10) -> set[str]:
        async with self._lock:
            rows = self._db().execute(
                "SELECT place_key FROM recommendation_history WHERE guild_id = ? AND source = ? ORDER BY id DESC LIMIT ?",
                (guild_id, source, limit),
            ).fetchall()
            return {row["place_key"] for row in rows}

    async def record_recommendation(self, guild_id: int, source: str, place: dict[str, Any]) -> None:
        async with self._lock:
            with self._db():
                self._db().execute(
                    "INSERT INTO recommendation_history(guild_id, source, place_key, recommended_at) VALUES (?, ?, ?, ?)",
                    (guild_id, source, place_key(place), now_iso()),
                )
