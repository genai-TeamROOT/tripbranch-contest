"""후보별 Concentration 후조회 계약과 서비스 상태 집계를 검증한다."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from pydantic import ValidationError

from app.agent_context.concentration_proxy import ConcentrationMappingCache
from app.agent_context.enrichment_schemas import (
    CandidateEnrichmentRequest,
    CandidateEnrichmentTarget,
)
from app.agent_context.enrichment_service import CandidateEnrichmentService
from app.concentration_policy import normalize_concentration
from app.config import settings
from app.domain.models import (
    ConcentrationForecast,
    ConcentrationResult,
    StoredPlaceLocation,
)
from app.errors import AppError, ProviderTimeoutError
from app.providers.concentration import FakeConcentrationProvider
from app.providers.contracts import (
    ProviderMetadata,
    ProviderResult,
    ProviderSource,
    ProviderStatus,
)
from app.recommendation_limits import MAX_RECOMMENDATION_CANDIDATE_LIMIT
from app.tools.concentration import GetConcentrationTool

RETRIEVED_AT = datetime(2026, 7, 28, 1, tzinfo=UTC)
REFERENCE_TIME = datetime(2026, 7, 29, 10, tzinfo=UTC)


def _target(index: int, *, name: str | None = None) -> CandidateEnrichmentTarget:
    return CandidateEnrichmentTarget(
        place_id=f"place-{index}",
        name=name or f"후보 {index}",
        latitude=37.57 + index / 1000,
        longitude=126.97 + index / 1000,
    )


def _request(*targets: CandidateEnrichmentTarget) -> CandidateEnrichmentRequest:
    return CandidateEnrichmentRequest(
        request_id="enrichment-request-1",
        candidates=list(targets),
        features=["concentration"],
    )


def _provider_result(
    name: str,
    *,
    status: ProviderStatus = ProviderStatus.SUCCESS,
    has_data: bool = True,
) -> ProviderResult[ConcentrationResult]:
    forecasts = (
        (
            ConcentrationForecast(
                place_name=name,
                forecast_date="20260729",
                concentration_rate=42.0,
                raw_data={"cnctrRate": 42.0},
            ),
        )
        if has_data
        else ()
    )
    return ProviderResult(
        data=ConcentrationResult(
            area_code="11",
            district_code="11110",
            requested_place_name=name,
            forecasts=forecasts,
            provider="test_concentration",
        ),
        metadata=ProviderMetadata(
            source=ProviderSource.TOUR_API_CONCENTRATION,
            status=status,
            retrieved_at=RETRIEVED_AT,
        ),
    )


def _district_only_places(count: int = 10) -> tuple[StoredPlaceLocation, ...]:
    """_target()이 만드는 후보에 대응하는 매핑. 구 코드만 담는다.

    집중률 조회는 구를 지정해야 하고, 구는 매핑된 장소에서 온다(D-095). 정식
    명칭을 비워 두면 조회 이름이 후보 이름으로 떨어져 이 파일의 기존 기대값이
    그대로 유지된다 - 이 테스트들이 보는 것은 결과 조립이지 이름 매칭이 아니다.
    """
    return tuple(
        StoredPlaceLocation(
            content_id=f"place-{index}",
            title=f"후보 {index}",
            address=None,
            latitude=37.57 + index / 1000,
            longitude=126.97 + index / 1000,
            district_code="110",
            concentration_name=None,
        )
        for index in range(1, count + 1)
    )


def _service(provider: _ScriptedConcentrationProvider) -> CandidateEnrichmentService:
    return CandidateEnrichmentService(
        GetConcentrationTool(provider),
        candidate_limit=5,
        clock=lambda: REFERENCE_TIME,
        mapping_cache=ConcentrationMappingCache(
            _StubMappingRepository(_district_only_places())
        ),
    )


class _ScriptedConcentrationProvider:
    """후보 이름별 결과나 오류를 반환하고 호출 인자를 기록한다."""

    def __init__(
        self,
        outcomes: dict[str, ProviderResult[ConcentrationResult] | AppError],
    ) -> None:
        self._outcomes = outcomes
        self.calls: list[tuple[str, str, str | None]] = []

    async def get_forecast(
        self,
        area_code: str,
        district_code: str,
        place_name: str | None = None,
    ) -> ProviderResult[ConcentrationResult]:
        self.calls.append((area_code, district_code, place_name))
        outcome = self._outcomes[str(place_name)]
        if isinstance(outcome, AppError):
            raise outcome
        return outcome


def test_request_enforces_system_limit_and_concentration_only() -> None:
    """Schema 절대 상한과 지원 feature를 계약 단계에서 거부한다."""

    limit = MAX_RECOMMENDATION_CANDIDATE_LIMIT
    valid = _request(*(_target(index) for index in range(1, limit + 1)))

    assert len(valid.candidates) == limit
    with pytest.raises(ValidationError):
        _request(*(_target(index) for index in range(1, limit + 2)))
    with pytest.raises(ValidationError):
        CandidateEnrichmentRequest(
            request_id="request-unsupported",
            candidates=[_target(1)],
            features=["weather"],
        )


@pytest.mark.asyncio
async def test_service_enforces_configured_candidate_limit() -> None:
    provider = _ScriptedConcentrationProvider({})
    request = _request(*(_target(index) for index in range(1, 7)))

    with pytest.raises(ValueError, match="최대 5개"):
        await _service(provider).enrich(request)

    assert provider.calls == []


@pytest.mark.asyncio
async def test_all_success_preserves_order_metadata_and_internal_region_codes() -> None:
    """네 후보 필드와 Provider metadata를 유지하고 종로구 코드로 조회한다."""

    first = _target(1, name="경복궁")
    second = _target(2, name="창덕궁")
    provider = _ScriptedConcentrationProvider(
        {
            "경복궁": _provider_result("경복궁"),
            "창덕궁": _provider_result("창덕궁"),
        }
    )

    response = await _service(provider).enrich(_request(first, second))

    assert response.status == "success"
    assert [candidate.place_id for candidate in response.candidates] == [
        first.place_id,
        second.place_id,
    ]
    assert response.candidates[0].latitude == first.latitude
    assert response.candidates[0].longitude == first.longitude
    assert response.candidates[0].concentration[0].concentration_rate == 42.0
    assert response.candidates[0].concentration[0].forecast_date == "2026-07-29"
    metadata = response.candidates[0].provider_metadata[0]
    assert metadata.source == "tour_api_concentration"
    assert metadata.status == "success"
    assert metadata.retrieved_at == RETRIEVED_AT
    assert provider.calls == [
        ("11", "11110", "경복궁"),
        ("11", "11110", "창덕궁"),
    ]


@pytest.mark.asyncio
async def test_service_keeps_only_today_and_prefers_matching_place_name() -> None:
    """여러 날짜·장소가 섞인 응답에서 오늘의 요청 장소 한 건만 반환한다."""

    provider_result = _provider_result("경복궁")
    provider_result = ProviderResult(
        data=ConcentrationResult(
            area_code="11",
            district_code="11110",
            requested_place_name="경복궁",
            forecasts=(
                ConcentrationForecast(
                    place_name="경복궁",
                    forecast_date="20260728",
                    concentration_rate=31.0,
                    raw_data={},
                ),
                ConcentrationForecast(
                    place_name="다른 장소",
                    forecast_date="2026-07-29",
                    concentration_rate=44.0,
                    raw_data={},
                ),
                ConcentrationForecast(
                    place_name="경복궁",
                    forecast_date="20260729",
                    concentration_rate=55.5,
                    raw_data={},
                ),
                ConcentrationForecast(
                    place_name="경복궁",
                    forecast_date="20260730",
                    concentration_rate=78.0,
                    raw_data={},
                ),
            ),
            provider="test_concentration",
        ),
        metadata=provider_result.metadata,
    )
    provider = _ScriptedConcentrationProvider({"경복궁": provider_result})

    response = await _service(provider).enrich(_request(_target(1, name="경복궁")))

    assert response.status == "success"
    assert response.candidates[0].concentration is not None
    assert len(response.candidates[0].concentration) == 1
    assert response.candidates[0].concentration[0].forecast_date == "2026-07-29"
    assert response.candidates[0].concentration[0].concentration_rate == 55.5
    assert response.candidates[0].concentration[0].concentration_level == "slightly_crowded"
    assert response.candidates[0].concentration[0].concentration_label == "다소 혼잡"


@pytest.mark.parametrize(
    ("rate", "expected_level", "expected_label"),
    [
        (0.0, "quiet", "한적함"),
        (19.9, "quiet", "한적함"),
        (20.0, "normal", "보통"),
        (49.9, "normal", "보통"),
        (50.0, "slightly_crowded", "다소 혼잡"),
        (69.9, "slightly_crowded", "다소 혼잡"),
        (70.0, "crowded", "혼잡"),
    ],
)
def test_normalize_concentration_uses_agreed_boundaries(
    rate: float,
    expected_level: str,
    expected_label: str,
) -> None:
    normalized = normalize_concentration(rate)

    assert normalized.level == expected_level
    assert normalized.label == expected_label


@pytest.mark.asyncio
async def test_service_returns_no_data_when_today_has_no_valid_rate() -> None:
    """오늘 항목이 없거나 숫자값이 유효하지 않으면 다른 날짜로 대체하지 않는다."""

    provider_result = _provider_result("경복궁")
    provider_result = ProviderResult(
        data=ConcentrationResult(
            area_code="11",
            district_code="11110",
            requested_place_name="경복궁",
            forecasts=(
                ConcentrationForecast(
                    place_name="경복궁",
                    forecast_date="20260728",
                    concentration_rate=42.0,
                    raw_data={},
                ),
                ConcentrationForecast(
                    place_name="경복궁",
                    forecast_date="20260729",
                    concentration_rate=-1.0,
                    raw_data={},
                ),
            ),
            provider="test_concentration",
        ),
        metadata=provider_result.metadata,
    )
    provider = _ScriptedConcentrationProvider({"경복궁": provider_result})

    response = await _service(provider).enrich(_request(_target(1, name="경복궁")))

    assert response.status == "no_data"
    assert response.candidates[0].status == "no_data"
    assert response.candidates[0].concentration == []


@pytest.mark.asyncio
async def test_mixed_candidate_outcomes_return_partial_without_removing_candidates() -> None:
    """성공·빈 결과·실패 후보가 섞여도 모든 후보를 그대로 반환한다."""

    provider = _ScriptedConcentrationProvider(
        {
            "성공": _provider_result("성공"),
            "빈 결과": _provider_result(
                "빈 결과",
                status=ProviderStatus.NO_DATA,
                has_data=False,
            ),
            "실패": ProviderTimeoutError("Concentration"),
        }
    )

    response = await _service(provider).enrich(
        _request(
            _target(1, name="성공"),
            _target(2, name="빈 결과"),
            _target(3, name="실패"),
        )
    )

    assert response.status == "partial"
    assert [candidate.status for candidate in response.candidates] == [
        "success",
        "no_data",
        "unavailable",
    ]
    assert len(response.candidates) == 3
    assert response.candidates[1].concentration == []
    assert response.candidates[1].provider_metadata[0].status == "no_data"
    assert response.candidates[2].concentration is None
    assert response.candidates[2].error is not None
    assert response.candidates[2].error.retryable is True


@pytest.mark.asyncio
async def test_all_no_data_returns_no_data_and_keeps_candidates() -> None:
    """모든 Provider 조회가 빈 결과여도 후보는 제거하지 않는다."""

    provider = _ScriptedConcentrationProvider(
        {
            "첫째": _provider_result(
                "첫째",
                status=ProviderStatus.NO_DATA,
                has_data=False,
            ),
            "둘째": _provider_result(
                "둘째",
                status=ProviderStatus.NO_DATA,
                has_data=False,
            ),
        }
    )

    response = await _service(provider).enrich(
        _request(_target(1, name="첫째"), _target(2, name="둘째"))
    )

    assert response.status == "no_data"
    assert len(response.candidates) == 2
    assert all(candidate.status == "no_data" for candidate in response.candidates)


@pytest.mark.asyncio
async def test_all_failures_return_unavailable_and_keep_candidates() -> None:
    """모든 호출이 실패하면 전체 unavailable과 후보별 오류를 반환한다."""

    provider = _ScriptedConcentrationProvider(
        {
            "첫째": ProviderTimeoutError("Concentration"),
            "둘째": ProviderTimeoutError("Concentration"),
        }
    )

    response = await _service(provider).enrich(
        _request(_target(1, name="첫째"), _target(2, name="둘째"))
    )

    assert response.status == "unavailable"
    assert len(response.candidates) == 2
    assert all(candidate.status == "unavailable" for candidate in response.candidates)
    assert all(candidate.error is not None for candidate in response.candidates)


@pytest.mark.asyncio
async def test_fake_provider_uses_the_common_concentration_contract() -> None:
    """실제 서비스가 Fake ConcentrationProvider 공통 계약으로 동작한다."""

    response = await CandidateEnrichmentService(
        GetConcentrationTool(FakeConcentrationProvider()),
        candidate_limit=5,
        # 구를 알아야 조회가 나간다(D-095). 프로덕션 배선과 같이 매핑을 넘긴다.
        mapping_cache=ConcentrationMappingCache(
            _StubMappingRepository(_district_only_places())
        ),
    ).enrich(_request(_target(1, name="경복궁")))

    assert response.status == "success"
    assert response.candidates[0].status == "success"
    assert response.candidates[0].provider_metadata[0].source == "fake_concentration"


@pytest.mark.asyncio
async def test_factory_wires_configured_concentration_provider() -> None:
    """Factory가 설정된 Provider를 Tool 경계 안에서 보강 서비스에 연결한다.

    place_id는 fake 저장소의 content_id를 쓴다 — 매핑에 없는 후보는 조회 자체를
    건너뛰므로(D-057 이관) 임의 id로는 Provider까지 도달하지 않는다.
    """

    from app.agent_context.factory import get_candidate_enrichment_service

    async with httpx.AsyncClient() as client:
        response = await get_candidate_enrichment_service(client).enrich(
            _request(
                CandidateEnrichmentTarget(
                    place_id="126508",  # fake 저장소의 경복궁
                    name="경복궁",
                    latitude=37.5788,
                    longitude=126.9770,
                )
            )
        )

    assert response.status == "success"
    assert response.candidates[0].provider_metadata[0].source == "fake_concentration"


@pytest.mark.asyncio
async def test_factory_uses_recommendation_result_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.agent_context.factory import get_candidate_enrichment_service

    monkeypatch.setattr(settings, "recommendation_result_limit", 1)

    async with httpx.AsyncClient() as client:
        service = get_candidate_enrichment_service(client)
        with pytest.raises(ValueError, match="최대 1개"):
            await service.enrich(_request(_target(1), _target(2)))


class _StubMappingRepository:
    """집중률 매핑 저장소 fake. 캐시가 읽는 목록만 돌려준다."""

    def __init__(self, places: tuple[StoredPlaceLocation, ...]) -> None:
        self._places = places

    async def find_concentration_mapped_places(self) -> tuple[StoredPlaceLocation, ...]:
        return self._places


def _mapped_place(
    content_id: str,
    *,
    title: str,
    concentration_name: str,
    search_keys: tuple[str, ...],
) -> StoredPlaceLocation:
    return StoredPlaceLocation(
        content_id=content_id,
        title=title,
        address=None,
        latitude=37.5739,
        longitude=126.9945,
        district_code="110",
        concentration_name=concentration_name,
        concentration_search_keys=search_keys,
    )


def _mapped_service(
    provider: _ScriptedConcentrationProvider,
    places: tuple[StoredPlaceLocation, ...],
) -> CandidateEnrichmentService:
    return CandidateEnrichmentService(
        GetConcentrationTool(provider),
        candidate_limit=5,
        clock=lambda: REFERENCE_TIME,
        mapping_cache=ConcentrationMappingCache(_StubMappingRepository(places)),
    )


@pytest.mark.asyncio
async def test_mapped_candidate_queries_by_search_key_and_matches_canonical_name() -> None:
    """조회는 검색어로, 대조는 정식 명칭으로 한다(D-057).

    후보 이름('종묘')을 tAtsNm에 그대로 넣던 기존 방식은 응답에 섞여 오는
    '종묘광장공원'과 구분하지 못한다. 검색어로 조회하고 매핑의 정식 명칭으로
    골라야 올바른 장소가 선택된다.
    """

    canonical = "종묘 [유네스코 세계유산]"
    provider = _ScriptedConcentrationProvider(
        {
            "종묘": ProviderResult(
                data=ConcentrationResult(
                    area_code="11",
                    district_code="11110",
                    requested_place_name="종묘",
                    forecasts=(
                        ConcentrationForecast(
                            place_name="종묘광장공원",
                            forecast_date="20260729",
                            concentration_rate=35.28,
                            raw_data={},
                        ),
                        ConcentrationForecast(
                            place_name=canonical,
                            forecast_date="20260729",
                            concentration_rate=61.97,
                            raw_data={},
                        ),
                    ),
                    provider="test_concentration",
                ),
                metadata=ProviderMetadata(
                    source=ProviderSource.TOUR_API_CONCENTRATION,
                    status=ProviderStatus.SUCCESS,
                    retrieved_at=RETRIEVED_AT,
                ),
            )
        }
    )
    service = _mapped_service(
        provider,
        (
            _mapped_place(
                "126510",
                title="종묘",
                concentration_name=canonical,
                search_keys=("종묘",),
            ),
        ),
    )

    response = await service.enrich(
        _request(
            CandidateEnrichmentTarget(
                place_id="126510",
                name="종묘",
                latitude=37.5739,
                longitude=126.9945,
            )
        )
    )

    assert provider.calls == [("11", "11110", "종묘")]
    candidate = response.candidates[0]
    assert candidate.status == "success"
    assert candidate.concentration is not None
    # 첫 행(종묘광장공원 35.28)이 아니라 정식 명칭의 값을 골라야 한다.
    assert candidate.concentration[0].place_name == canonical
    assert candidate.concentration[0].concentration_rate == 61.97


@pytest.mark.asyncio
async def test_unmapped_candidate_without_nearby_mapping_skips_provider_call() -> None:
    """매핑도 없고 인근 대체 장소도 반경 밖이면 호출하지 않고 no_data로 끝낸다.

    매핑은 집중률 API에 데이터가 있는 장소 목록이라, 후보 이름으로 조회해도 안
    나온다. 여기서는 유일한 매핑 장소가 0.588km 떨어져 있어 대체 조회 반경
    (0.5km) 밖이므로 빌려올 곳도 없다.
    """

    provider = _ScriptedConcentrationProvider({})
    service = _mapped_service(
        provider,
        (
            _mapped_place(
                "126510",
                title="종묘",
                concentration_name="종묘 [유네스코 세계유산]",
                search_keys=("종묘",),
            ),
        ),
    )

    response = await service.enrich(
        _request(
            CandidateEnrichmentTarget(
                place_id="999999",
                name="스타벅스 종로점",
                latitude=37.5700,
                longitude=126.9900,
            )
        )
    )

    assert provider.calls == []
    assert response.candidates[0].status == "no_data"
    assert response.candidates[0].concentration == []


@pytest.mark.asyncio
async def test_search_keys_are_tried_in_order_until_data_found() -> None:
    """앞 검색어가 0건이면 다음 검색어로 넘어간다(D-057)."""

    provider = _ScriptedConcentrationProvider(
        {
            # 실 Provider는 forecasts가 비면 NO_DATA를 낸다(concentration.py:133).
            "서울": _provider_result(
                "서울", status=ProviderStatus.NO_DATA, has_data=False
            ),
            "운현궁": _provider_result("서울 운현궁"),
        }
    )
    service = _mapped_service(
        provider,
        (
            _mapped_place(
                "126520",
                title="운현궁",
                concentration_name="서울 운현궁",
                search_keys=("서울", "운현궁"),
            ),
        ),
    )

    response = await service.enrich(
        _request(
            CandidateEnrichmentTarget(
                place_id="126520",
                name="운현궁",
                latitude=37.5745,
                longitude=126.9855,
            )
        )
    )

    assert [call[2] for call in provider.calls] == ["서울", "운현궁"]
    assert response.candidates[0].status == "success"


@pytest.mark.asyncio
async def test_unmapped_candidate_borrows_nearby_concentration_with_proxy_flag() -> None:
    """매핑 없는 후보는 인근 매핑 장소의 값을 빌리고 근사치임을 표시한다.

    활성 844건 중 매핑은 100건뿐이라, 빌려오지 않으면 다수 후보가 혼잡도 판정에서
    통째로 빠진다(안국역 2km 내 711건 중 매핑 70건). INFO 대체 조회와 같은 반경·
    시도 횟수를 쓴다 — 같은 사용자가 "여기 혼잡해?"와 "한산한 곳 추천해줘"에서
    다른 기준을 보면 곤란하다.
    """

    provider = _ScriptedConcentrationProvider(
        {"종묘": _provider_result("종묘 [유네스코 세계유산]")}
    )
    service = _mapped_service(
        provider,
        (
            _mapped_place(
                "126510",
                title="종묘",
                concentration_name="종묘 [유네스코 세계유산]",
                search_keys=("종묘",),
            ),
        ),
    )

    response = await service.enrich(
        _request(
            CandidateEnrichmentTarget(
                place_id="999999",
                name="이름없는 카페",
                # 매핑 장소에서 약 0.15km — 대체 조회 반경(0.5km) 안이다.
                latitude=37.5748,
                longitude=126.9955,
            )
        )
    )

    candidate = response.candidates[0]
    assert candidate.status == "success"
    assert candidate.concentration is not None
    forecast = candidate.concentration[0]
    assert forecast.concentration_rate == 42.0
    # 후보 본인의 값이 아니라는 사실이 반드시 드러나야 한다.
    assert forecast.is_proxy is True
    assert forecast.proxy_place_name == "종묘 [유네스코 세계유산]"
    assert forecast.proxy_distance_km is not None
    assert 0 < forecast.proxy_distance_km <= 0.5
    # 조회는 매핑의 검색어로 나간다 — 후보 이름을 tAtsNm에 넣지 않는다.
    assert [call[2] for call in provider.calls] == ["종묘"]


@pytest.mark.asyncio
async def test_mapped_candidate_is_not_marked_as_proxy() -> None:
    """자기 매핑으로 조회한 값은 근사치가 아니다."""

    provider = _ScriptedConcentrationProvider(
        {"종묘": _provider_result("종묘 [유네스코 세계유산]")}
    )
    service = _mapped_service(
        provider,
        (
            _mapped_place(
                "126510",
                title="종묘",
                concentration_name="종묘 [유네스코 세계유산]",
                search_keys=("종묘",),
            ),
        ),
    )

    response = await service.enrich(
        _request(
            CandidateEnrichmentTarget(
                place_id="126510",
                name="종묘",
                latitude=37.5739,
                longitude=126.9945,
            )
        )
    )

    forecast = response.candidates[0].concentration[0]
    assert forecast.is_proxy is False
    assert forecast.proxy_place_name is None
    assert forecast.proxy_distance_km is None


@pytest.mark.asyncio
async def test_candidates_sharing_a_proxy_place_query_it_once() -> None:
    """같은 인근 장소를 가리키는 후보들은 조회를 한 번만 나눠 쓴다.

    후보 5개가 각자 최대 3곳을 시도하면 한 요청에 최대 15회다. 집중률 API는
    오퍼레이션 단위 일일 한도가 있어(1,000회) 중복을 그대로 두면 안 된다.
    """

    provider = _ScriptedConcentrationProvider(
        {"종묘": _provider_result("종묘 [유네스코 세계유산]")}
    )
    service = _mapped_service(
        provider,
        (
            _mapped_place(
                "126510",
                title="종묘",
                concentration_name="종묘 [유네스코 세계유산]",
                search_keys=("종묘",),
            ),
        ),
    )

    response = await service.enrich(
        _request(
            CandidateEnrichmentTarget(
                place_id="999998",
                name="카페 A",
                latitude=37.5748,
                longitude=126.9955,
            ),
            CandidateEnrichmentTarget(
                place_id="999999",
                name="카페 B",
                latitude=37.5742,
                longitude=126.9950,
            ),
        )
    )

    assert response.status == "success"
    assert all(item.concentration[0].is_proxy for item in response.candidates)
    # 후보는 2건인데 외부 호출은 1회다.
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_proxy_flag_survives_into_the_response_d_receives() -> None:
    """C가 실은 플래그를 D가 그대로 받는다.

    D의 2차 Scoring은 CandidateEnrichmentResponse를 통째로 받고
    `result.concentration[0]`을 손에 쥔다. 값을 어떻게 쓸지는 D가 정하지만,
    판단 근거가 도달하는 것까지는 C가 보장한다.
    """

    provider = _ScriptedConcentrationProvider(
        {"종묘": _provider_result("종묘 [유네스코 세계유산]")}
    )
    service = _mapped_service(
        provider,
        (
            _mapped_place(
                "126510",
                title="종묘",
                concentration_name="종묘 [유네스코 세계유산]",
                search_keys=("종묘",),
            ),
        ),
    )

    response = await service.enrich(
        _request(
            CandidateEnrichmentTarget(
                place_id="999999",
                name="이름없는 카페",
                latitude=37.5748,
                longitude=126.9955,
            )
        )
    )

    # D가 하는 것과 같은 방식으로 꺼내 본다.
    by_place_id = {item.place_id: item for item in response.candidates}
    forecast = by_place_id["999999"].concentration[0]
    assert (forecast.is_proxy, forecast.proxy_place_name) == (
        True,
        "종묘 [유네스코 세계유산]",
    )
    assert forecast.concentration_level is not None


@pytest.mark.asyncio
async def test_lookup_uses_the_district_of_the_mapped_place() -> None:
    """조회 signguCd가 매핑 장소의 구에서 나온다(D-095).

    집중률 API는 signguCd로 엄격하게 거른다 — 중구 명동성당을 종로구로 물으면
    0건이 온다. 구를 종로구로 고정하던 동안에는 매핑이 전부 종로구라 값이 맞았고,
    다른 구 매핑이 들어오는 순간 틀린 값이 된다.
    """
    provider = _ScriptedConcentrationProvider({"덕수궁": _provider_result("덕수궁")})
    service = CandidateEnrichmentService(
        GetConcentrationTool(provider),
        candidate_limit=5,
        clock=lambda: REFERENCE_TIME,
        mapping_cache=ConcentrationMappingCache(
            _StubMappingRepository(
                (
                    StoredPlaceLocation(
                        content_id="place-1",
                        title="덕수궁",
                        address=None,
                        latitude=37.5658,
                        longitude=126.9751,
                        district_code="140",  # 중구
                        concentration_name="덕수궁",
                    ),
                )
            )
        ),
    )

    await service.enrich(_request(_target(1, name="덕수궁")))

    area_codes = {area for area, _, _ in provider.calls}
    signgu_codes = {signgu for _, signgu, _ in provider.calls}
    assert area_codes == {"11"}
    # 종로구(11110)가 아니라 중구(11140)로 나가야 한다.
    assert signgu_codes == {"11140"}


@pytest.mark.asyncio
async def test_jongno_only_mappings_keep_querying_jongno() -> None:
    """매핑이 종로구뿐이면 조회가 예전과 똑같이 나간다(D-095 회귀 방지).

    고정을 푸는 변경의 안전 조건이다. 이 카드만 머지된 시점에는 매핑이 전부
    종로구라, 상수를 쓰던 때와 나가는 값이 같아야 한다.
    """
    provider = _ScriptedConcentrationProvider({"경복궁": _provider_result("경복궁")})
    service = CandidateEnrichmentService(
        GetConcentrationTool(provider),
        candidate_limit=5,
        clock=lambda: REFERENCE_TIME,
        mapping_cache=ConcentrationMappingCache(
            _StubMappingRepository(
                (
                    StoredPlaceLocation(
                        content_id="place-1",
                        title="경복궁",
                        address=None,
                        latitude=37.5788,
                        longitude=126.9770,
                        district_code="110",
                        concentration_name="경복궁",
                    ),
                )
            )
        ),
    )

    response = await service.enrich(_request(_target(1, name="경복궁")))

    assert provider.calls == [("11", "11110", "경복궁")]
    assert response.candidates[0].status == "success"


@pytest.mark.asyncio
async def test_place_without_district_is_not_queried_as_jongno() -> None:
    """구를 모르는 장소는 조회하지 않는다(D-095).

    종로구로 대신 물으면 다른 구 장소는 언제나 0건이라, 틀린 조회가 "혼잡도 정보
    없음"과 구분되지 않고 조용히 섞인다. 호출을 아예 내보내지 않는 쪽을 택한다.
    """
    provider = _ScriptedConcentrationProvider({"덕수궁": _provider_result("덕수궁")})
    service = CandidateEnrichmentService(
        GetConcentrationTool(provider),
        candidate_limit=5,
        clock=lambda: REFERENCE_TIME,
        mapping_cache=ConcentrationMappingCache(
            _StubMappingRepository(
                (
                    StoredPlaceLocation(
                        content_id="place-1",
                        title="덕수궁",
                        address=None,
                        latitude=37.5658,
                        longitude=126.9751,
                        district_code=None,
                        concentration_name="덕수궁",
                    ),
                )
            )
        ),
    )

    response = await service.enrich(_request(_target(1, name="덕수궁")))

    assert provider.calls == []
    assert response.candidates[0].status == "no_data"
