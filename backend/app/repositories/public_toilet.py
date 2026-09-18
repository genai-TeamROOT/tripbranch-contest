"""Supabase의 공중화장실 위치 저장소.

서울시 API가 구·좌표 필터를 지원하지 않아 매 요청 전량(4,447건)을 받을 수 없다.
동기화 스크립트가 적재해두고, 조회는 좌표 바운딩 박스로 1차로 추린다. 정확한
거리 계산과 정렬은 이 저장소가 아니라 호출부(파이썬 ``haversine_km``)가 한다 —
공영주차장이 확립한 방식과 같다. PostGIS·RPC를 새로 들이지 않는 이유다.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

import httpx

from app.domain.models import PublicToilet
from app.repositories.supabase_places import SupabaseRepositoryError

# 바운딩 박스를 만들 때 쓰는 위도 1도의 거리. 경도 1도는 위도에 따라 짧아지므로
# cos(위도)로 나눠 보정한다.
_KM_PER_LATITUDE_DEGREE = 111.0

_SELECT_COLUMNS = (
    "toilet_id,name,address_new,address_old,latitude,longitude,district,tel,"
    "open_type,open_hours_raw,restroom_status,accessible_status,amenities,"
    "safety_signs,location_type,manager"
)


class SupabasePublicToiletRepository:
    def __init__(
        self,
        supabase_url: str,
        secret_key: str,
        client: httpx.AsyncClient,
        timeout_seconds: float = 10.0,
    ) -> None:
        normalized_url = supabase_url.strip().rstrip("/")
        if not normalized_url:
            raise ValueError("supabase_url이 필요합니다.")
        if not secret_key.strip():
            raise ValueError("secret_key가 필요합니다.")
        self._rest_url = f"{normalized_url}/rest/v1"
        self._secret_key = secret_key
        self._client = client
        self._timeout_seconds = timeout_seconds

    def _headers(self, prefer: str | None = None) -> dict[str, str]:
        headers = {"apikey": self._secret_key, "Content-Type": "application/json"}
        if prefer is not None:
            headers["Prefer"] = prefer
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        json: object | None = None,
        prefer: str | None = None,
    ) -> httpx.Response:
        try:
            response = await self._client.request(
                method,
                self._rest_url + path,
                params=params,
                json=json,
                headers=self._headers(prefer),
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            return response
        except httpx.TimeoutException:
            raise SupabaseRepositoryError("public toilet request timeout") from None
        except httpx.HTTPStatusError as exc:
            raise SupabaseRepositoryError(
                f"public toilet HTTP {exc.response.status_code}"
            ) from None
        except httpx.HTTPError:
            raise SupabaseRepositoryError("public toilet request failed") from None

    async def find_near(
        self, latitude: float, longitude: float, *, radius_km: float, limit: int
    ) -> tuple[PublicToilet, ...]:
        """좌표 주변 화장실을 바운딩 박스로 추려 온다.

        박스는 원보다 넓으므로(모서리가 반지름보다 멀다) 호출부가 정확한 거리로
        한 번 더 걸러야 한다. ``limit``은 박스 안에서 가져올 상한이라 최종 노출
        건수보다 넉넉하게 줘야 가까운 곳을 놓치지 않는다.
        """

        latitude_delta = radius_km / _KM_PER_LATITUDE_DEGREE
        # 위도가 높아질수록 경도 1도의 실제 거리가 짧아진다. 서울(37.5도)에서는
        # 약 0.79배라 보정 없이 쓰면 동서 방향 범위가 좁아진다.
        cosine = math.cos(math.radians(latitude))
        longitude_delta = radius_km / (_KM_PER_LATITUDE_DEGREE * max(cosine, 0.01))
        response = await self._request(
            "GET",
            "/public_toilets",
            params={
                "select": _SELECT_COLUMNS,
                "latitude": f"gte.{latitude - latitude_delta}",
                "longitude": f"gte.{longitude - longitude_delta}",
                # PostgREST는 같은 컬럼에 두 조건을 줄 때 and 구문을 쓴다.
                "and": (
                    f"(latitude.lte.{latitude + latitude_delta},"
                    f"longitude.lte.{longitude + longitude_delta})"
                ),
                "limit": str(limit),
            },
        )
        try:
            rows = response.json()
        except ValueError:
            raise SupabaseRepositoryError("invalid public toilet response") from None
        if not isinstance(rows, list):
            raise SupabaseRepositoryError("invalid public toilet response")
        return tuple(_to_toilet(row) for row in rows if _has_coordinates(row))

    async def upsert_toilets(self, toilets: Sequence[PublicToilet]) -> None:
        rows = [
            {
                "toilet_id": toilet.toilet_id,
                "name": toilet.name,
                "address_new": toilet.address_new,
                "address_old": toilet.address_old,
                "latitude": toilet.latitude,
                "longitude": toilet.longitude,
                "district": toilet.district,
                "tel": toilet.tel,
                "open_type": toilet.open_type,
                "open_hours_raw": toilet.open_hours_raw,
                "restroom_status": toilet.restroom_status,
                "accessible_status": toilet.accessible_status,
                "amenities": toilet.amenities,
                "safety_signs": toilet.safety_signs,
                "location_type": toilet.location_type,
                "manager": toilet.manager,
            }
            for toilet in toilets
        ]
        if not rows:
            return
        await self._request(
            "POST",
            "/public_toilets",
            params={"on_conflict": "toilet_id"},
            json=rows,
            prefer="resolution=merge-duplicates,return=minimal",
        )


def _has_coordinates(row: object) -> bool:
    return (
        isinstance(row, Mapping)
        and _optional_float(row.get("latitude")) is not None
        and _optional_float(row.get("longitude")) is not None
    )


def _to_toilet(row: object) -> PublicToilet:
    if not isinstance(row, Mapping):
        raise SupabaseRepositoryError("invalid public toilet row")
    toilet_id = row.get("toilet_id")
    if not isinstance(toilet_id, str) or not toilet_id.strip():
        raise SupabaseRepositoryError("public toilet row missing id")
    latitude = _optional_float(row.get("latitude"))
    longitude = _optional_float(row.get("longitude"))
    if latitude is None or longitude is None:
        raise SupabaseRepositoryError("public toilet row missing coordinates")
    return PublicToilet(
        toilet_id=toilet_id.strip(),
        name=str(row.get("name") or "공중화장실"),
        address_new=_optional_text(row.get("address_new")),
        address_old=_optional_text(row.get("address_old")),
        latitude=latitude,
        longitude=longitude,
        district=_optional_text(row.get("district")),
        tel=_optional_text(row.get("tel")),
        open_type=_optional_text(row.get("open_type")),
        open_hours_raw=_optional_text(row.get("open_hours_raw")),
        restroom_status=_optional_text(row.get("restroom_status")),
        accessible_status=_optional_text(row.get("accessible_status")),
        amenities=_optional_text(row.get("amenities")),
        safety_signs=_optional_text(row.get("safety_signs")),
        location_type=_optional_text(row.get("location_type")),
        manager=_optional_text(row.get("manager")),
    )


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_float(value: object) -> float | None:
    try:
        return float(str(value)) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


__all__ = ["SupabasePublicToiletRepository"]
