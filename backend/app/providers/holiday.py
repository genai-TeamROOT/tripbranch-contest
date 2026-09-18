"""한국천문연구원 특일 정보(공휴일) Provider의 Fake/Real 구현."""

from __future__ import annotations

import logging
from xml.etree import ElementTree

import httpx

from app.domain.models import HolidayEntry, HolidayResult
from app.errors import ProviderTimeoutError, ProviderUnavailableError
from app.providers.contracts import (
    ProviderResult,
    ProviderSource,
    ProviderStatus,
    provider_result,
)
from app.providers.upstream_errors import upstream_error_detail

logger = logging.getLogger(__name__)

_HOLIDAY_URL = (
    "https://apis.data.go.kr/B090041/openapi/service/SpcdeInfoService/"
    "getRestDeInfo"
)


def _parse_optional_int(value: str | None) -> int | None:
    try:
        return int(value) if value else None
    except ValueError:
        return None


def map_holiday_xml(xml_text: str, *, year: int, month: int | None) -> HolidayResult:
    """XML 응답을 외부 필드명과 독립적인 공통 모델로 변환한다."""
    root = ElementTree.fromstring(xml_text)
    result_code = root.findtext(".//header/resultCode", default="")
    if result_code not in {"", "00", "0000"}:
        result_message = root.findtext(".//header/resultMsg", default="")
        raise ProviderUnavailableError(
            "KASI Holiday", detail=f"{result_code}: {result_message}"
        )

    entries: list[HolidayEntry] = []
    for node in root.findall(".//items/item"):
        raw: dict[str, object] = {
            child.tag: child.text or "" for child in list(node)
        }
        date = str(raw.get("locdate", ""))
        name = str(raw.get("dateName", ""))
        if not date or not name:
            continue
        entries.append(
            HolidayEntry(
                date=date,
                name=name,
                kind=str(raw["dateKind"]) if raw.get("dateKind") else None,
                sequence=_parse_optional_int(str(raw.get("seq", ""))),
                is_holiday=str(raw.get("isHoliday", "")).upper() == "Y",
                raw_data=raw,
            )
        )

    return HolidayResult(
        year=year,
        month=month,
        entries=tuple(entries),
        provider="kasi_holiday",
    )


class FakeHolidayProvider:
    """실제 응답과 같은 공휴일 플래그를 가진 고정 목록을 반환한다."""

    _ENTRIES = (
        HolidayEntry("20260301", "삼일절", "02", 1, True, {"isHoliday": "Y"}),
        HolidayEntry("20260505", "어린이날", "02", 1, True, {"isHoliday": "Y"}),
    )

    async def get_holidays(
        self, year: int, month: int | None = None
    ) -> ProviderResult[HolidayResult]:
        _validate_date_scope(year, month)
        entries = tuple(
            entry
            for entry in self._ENTRIES
            if int(entry.date[:4]) == year
            and (month is None or int(entry.date[4:6]) == month)
        )
        return provider_result(
            HolidayResult(year, month, entries, "fake_holiday"),
            source=ProviderSource.FAKE_HOLIDAY,
            status=ProviderStatus.SUCCESS if entries else ProviderStatus.NO_DATA,
        )


def _validate_date_scope(year: int, month: int | None) -> None:
    if not 1 <= year <= 9999:
        raise ValueError("year는 1~9999 범위여야 합니다.")
    if month is not None and not 1 <= month <= 12:
        raise ValueError("month는 1~12 범위여야 합니다.")


class RealHolidayProvider:
    """SpcdeInfoService getRestDeInfo를 사용하는 실제 공휴일 Provider."""

    def __init__(
        self,
        api_key: str,
        client: httpx.AsyncClient,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._api_key = api_key
        self._client = client
        self._timeout_seconds = timeout_seconds

    async def get_holidays(
        self, year: int, month: int | None = None
    ) -> ProviderResult[HolidayResult]:
        _validate_date_scope(year, month)
        params = {
            "pageNo": "1",
            "numOfRows": "100",
            "solYear": f"{year:04d}",
        }
        if month is not None:
            params["solMonth"] = f"{month:02d}"
        request_params = {"serviceKey": self._api_key, **params}

        try:
            response = await self._client.get(
                _HOLIDAY_URL,
                params=request_params,
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            result = map_holiday_xml(response.text, year=year, month=month)
            return provider_result(
                result,
                source=ProviderSource.KASI_HOLIDAY,
                status=(
                    ProviderStatus.SUCCESS
                    if result.entries
                    else ProviderStatus.NO_DATA
                ),
            )
        except httpx.TimeoutException:
            # httpx 예외 문자열에는 ServiceKey가 포함된 전체 URL이 들어갈 수 있다.
            request_params.clear()
            response = None
            logger.error("KASI Holiday 호출 타임아웃 (year=%s, month=%s)", year, month)
            raise ProviderTimeoutError("KASI Holiday") from None
        except ProviderUnavailableError as exc:
            # map_holiday_xml이 resultCode를 보고 던진 경우 — detail에 사유가 있다.
            logger.error(
                "KASI Holiday 응답 오류 (%s, year=%s, month=%s)",
                exc.details,
                year,
                month,
            )
            raise
        except httpx.HTTPStatusError as exc:
            detail = f"HTTP {exc.response.status_code}, {upstream_error_detail(exc.response)}"
            request_params.clear()
            response = None
            logger.error(
                "KASI Holiday 호출 실패 (%s, year=%s, month=%s)", detail, year, month
            )
            raise ProviderUnavailableError("KASI Holiday", detail=detail) from None
        except (httpx.HTTPError, ElementTree.ParseError) as exc:
            request_params.clear()
            response = None
            logger.error(
                "KASI Holiday 호출 실패 (%s, year=%s, month=%s)",
                type(exc).__name__,
                year,
                month,
            )
            raise ProviderUnavailableError("KASI Holiday") from None
