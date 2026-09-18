"""Supabase의 공영주차장 좌표 카탈로그 저장소.

실시간 주차 대수는 저장하지 않는다. 주소를 매 요청 지오코딩하지 않기 위해 정적
좌표·기본 속성만 보관하고, 잔여 대수와 기준 시각은 GetParkingInfo에서 매번 읽는다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import httpx

from app.domain.models import StoredMunicipalParkingLot
from app.repositories.supabase_places import SupabaseRepositoryError


class SupabaseMunicipalParkingRepository:
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
            raise SupabaseRepositoryError("municipal parking request timeout") from None
        except httpx.HTTPStatusError as exc:
            raise SupabaseRepositoryError(
                f"municipal parking HTTP {exc.response.status_code}"
            ) from None
        except httpx.HTTPError:
            raise SupabaseRepositoryError("municipal parking request failed") from None

    async def find_by_codes(
        self, codes: Sequence[str]
    ) -> dict[str, StoredMunicipalParkingLot]:
        unique_codes = list(dict.fromkeys(code.strip() for code in codes if code.strip()))
        if not unique_codes:
            return {}
        response = await self._request(
            "GET",
            "/municipal_parking_lots",
            params={
                "select": "parking_code,name,address,district,latitude,longitude,capacity,paid",
                "parking_code": f"in.({','.join(unique_codes)})",
            },
        )
        try:
            rows = response.json()
        except ValueError:
            raise SupabaseRepositoryError("invalid municipal parking response") from None
        if not isinstance(rows, list):
            raise SupabaseRepositoryError("invalid municipal parking response")
        lots: dict[str, StoredMunicipalParkingLot] = {}
        for row in rows:
            if not isinstance(row, Mapping) or not isinstance(row.get("parking_code"), str):
                raise SupabaseRepositoryError("municipal parking row missing code")
            code = row["parking_code"].strip()
            if not code:
                raise SupabaseRepositoryError("municipal parking row missing code")
            lots[code] = StoredMunicipalParkingLot(
                code=code,
                name=str(row.get("name") or "공영주차장"),
                address=_optional_text(row.get("address")),
                district=_optional_text(row.get("district")),
                latitude=_optional_float(row.get("latitude")),
                longitude=_optional_float(row.get("longitude")),
                capacity=_optional_int(row.get("capacity")),
                paid=_optional_bool(row.get("paid")),
            )
        return lots

    async def upsert_lots(self, lots: Sequence[StoredMunicipalParkingLot]) -> None:
        rows = [
            {
                "parking_code": lot.code,
                "name": lot.name,
                "address": lot.address,
                "district": lot.district,
                "latitude": lot.latitude,
                "longitude": lot.longitude,
                "capacity": lot.capacity,
                "paid": lot.paid,
            }
            for lot in lots
        ]
        if not rows:
            return
        await self._request(
            "POST",
            "/municipal_parking_lots",
            params={"on_conflict": "parking_code"},
            json=rows,
            prefer="resolution=merge-duplicates,return=minimal",
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


def _optional_int(value: object) -> int | None:
    try:
        return int(float(str(value))) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _optional_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


__all__ = ["SupabaseMunicipalParkingRepository"]
