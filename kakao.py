from __future__ import annotations

import asyncio
from typing import Any

import aiohttp


class KakaoError(RuntimeError):
    pass


class KakaoLocalClient:
    BASE_URL = "https://dapi.kakao.com/v2/local"
    DEFAULT_SEARCH_KINDS = ("한식", "중식", "일식", "양식", "아시아음식", "분식", "치킨", "피자")
    GENERAL_SEARCH_GROUP = "기타"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        headers = {"Authorization": f"KakaoAK {self.api_key}"}
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{self.BASE_URL}{path}", headers=headers, params=params) as response:
                if response.status != 200:
                    detail = (await response.text()).strip()
                    raise KakaoError(f"카카오 API 요청 실패 ({response.status}): {detail[:500]}")
                return await response.json()

    async def address_to_coordinate(self, address: str) -> dict[str, Any] | None:
        result = await self._get("/search/address.json", {"query": address})
        documents = result.get("documents", [])
        if not documents:
            return None
        document = documents[0]
        road = document.get("road_address") or {}
        return {
            "address": road.get("address_name") or document.get("address_name") or address,
            "latitude": float(document["y"]),
            "longitude": float(document["x"]),
        }

    @staticmethod
    def normalize_place(document: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": document["place_name"],
            "category": document.get("category_name", "음식점"),
            "address": document.get("road_address_name") or document.get("address_name") or "주소 정보 없음",
            "phone": document.get("phone") or "",
            "latitude": float(document["y"]),
            "longitude": float(document["x"]),
            "distance_meters": int(document["distance"]) if document.get("distance") else None,
            "kakao_place_id": document.get("id"),
            "kakao_place_url": document.get("place_url"),
            "category_group_code": document.get("category_group_code", ""),
            "category_group_name": document.get("category_group_name", ""),
            "memo": "",
        }

    async def _search_restaurant_pages(
        self,
        longitude: float,
        latitude: float,
        radius: int,
        kind: str | None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "x": longitude,
            "y": latitude,
            "radius": radius,
            "sort": "distance",
            "size": 15,
            "category_group_code": "FD6",
        }
        if kind:
            params["query"] = kind
            path = "/search/keyword.json"
        else:
            path = "/search/category.json"

        places: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for page in range(1, 4):
            result = await self._get(path, {**params, "page": page})
            for document in result.get("documents", []):
                if document.get("category_group_code") != "FD6":
                    continue
                place_id = str(document.get("id", ""))
                if place_id and place_id in seen_ids:
                    continue
                if place_id:
                    seen_ids.add(place_id)
                places.append(self.normalize_place(document))
            if result.get("meta", {}).get("is_end", True):
                break
        return places

    async def nearby_restaurants(self, longitude: float, latitude: float, radius: int, kind: str | None = None) -> list[dict[str, Any]]:
        if kind:
            return await self._search_restaurant_pages(longitude, latitude, radius, kind)

        groups = await self.nearby_restaurant_groups(longitude, latitude, radius)
        return self.merge_restaurant_groups(groups)

    async def nearby_restaurant_groups(self, longitude: float, latitude: float, radius: int) -> dict[str, list[dict[str, Any]]]:
        """Fetch each lunch category separately so callers can cache by group."""
        groups = await asyncio.gather(
            *(self._search_restaurant_pages(longitude, latitude, radius, search_kind) for search_kind in self.DEFAULT_SEARCH_KINDS),
            self._search_restaurant_pages(longitude, latitude, radius, None),
        )
        names = (*self.DEFAULT_SEARCH_KINDS, self.GENERAL_SEARCH_GROUP)
        return dict(zip(names, groups))

    @staticmethod
    def merge_restaurant_groups(groups: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
        places: list[dict[str, Any]] = []
        seen: set[str] = set()
        for group in groups.values():
            for place in group:
                key = str(place.get("kakao_place_id") or f"{place['name']}|{place.get('address', '')}")
                if key in seen:
                    continue
                seen.add(key)
                places.append(place)
        return places

    async def find_place(self, name: str, longitude: float, latitude: float, radius: int) -> dict[str, Any] | None:
        result = await self._get(
            "/search/keyword.json",
            {"query": name, "x": longitude, "y": latitude, "radius": radius, "sort": "distance", "size": 1},
        )
        documents = result.get("documents", [])
        return self.normalize_place(documents[0]) if documents else None
