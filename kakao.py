from __future__ import annotations

from typing import Any

import aiohttp


class KakaoError(RuntimeError):
    pass


class KakaoLocalClient:
    BASE_URL = "https://dapi.kakao.com/v2/local"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        headers = {"Authorization": f"KakaoAK {self.api_key}"}
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{self.BASE_URL}{path}", headers=headers, params=params) as response:
                if response.status != 200:
                    raise KakaoError(f"카카오 API 요청 실패 ({response.status})")
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
            "memo": "",
        }

    async def nearby_restaurants(self, longitude: float, latitude: float, radius: int, kind: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"x": longitude, "y": latitude, "radius": radius, "sort": "distance", "size": 15}
        if kind:
            params["query"] = f"{kind} 음식점"
            result = await self._get("/search/keyword.json", params)
        else:
            params["category_group_code"] = "FD6"
            result = await self._get("/search/category.json", params)
        return [self.normalize_place(document) for document in result.get("documents", [])]

    async def find_place(self, name: str, longitude: float, latitude: float, radius: int) -> dict[str, Any] | None:
        result = await self._get(
            "/search/keyword.json",
            {"query": name, "x": longitude, "y": latitude, "radius": radius, "sort": "distance", "size": 1},
        )
        documents = result.get("documents", [])
        return self.normalize_place(documents[0]) if documents else None

