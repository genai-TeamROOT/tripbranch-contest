"""공영주차장 카탈로그 테스트 더블."""

from __future__ import annotations

from collections.abc import Sequence

from app.domain.models import StoredMunicipalParkingLot


class FakeMunicipalParkingCatalogRepository:
    def __init__(self, lots: Sequence[StoredMunicipalParkingLot] = ()) -> None:
        self._lots = {lot.code: lot for lot in lots}

    async def find_by_codes(
        self, codes: Sequence[str]
    ) -> dict[str, StoredMunicipalParkingLot]:
        return {code: self._lots[code] for code in codes if code in self._lots}

    async def upsert_lots(self, lots: Sequence[StoredMunicipalParkingLot]) -> None:
        self._lots.update({lot.code: lot for lot in lots})


__all__ = ["FakeMunicipalParkingCatalogRepository"]
