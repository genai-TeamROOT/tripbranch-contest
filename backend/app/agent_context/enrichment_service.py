"""상위 추천 후보의 Concentration 정보를 후조회하는 C 서비스."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.agent_context.concentration_proxy import (
    ConcentrationMappingCache,
    select_nearest_mapped_places,
)
from app.agent_context.enrichment_schemas import (
    CandidateEnrichmentRequest,
    CandidateEnrichmentResponse,
    CandidateEnrichmentResult,
    CandidateEnrichmentStatus,
    CandidateEnrichmentTarget,
    ConcentrationForecastData,
    resolve_enrichment_status,
)
from app.agent_context.schemas import ContextError, ProviderMetadata
from app.concentration_policy import (
    CONCENTRATION_AREA_CODE,
    INFO_CONCENTRATION_FALLBACK_ATTEMPT_LIMIT,
    INFO_CONCENTRATION_FALLBACK_RADIUS_KM,
    concentration_signgu_code,
    is_valid_concentration_rate,
    normalize_concentration,
)
from app.domain.models import (
    ConcentrationForecast,
    ConcentrationResult,
    StoredPlaceLocation,
)
from app.geo import haversine_km
from app.providers.contracts import ProviderMetadata as ProviderMetadataData
from app.recommendation_limits import (
    MAX_RECOMMENDATION_CANDIDATE_LIMIT,
    MIN_RECOMMENDATION_LIMIT,
)
from app.tools.concentration import (
    ConcentrationQuery,
    ConcentrationToolResult,
    GetConcentrationTool,
)
from app.tools.contracts import ToolStatus

# 집중률 API의 종로구 행정 코드. INFO와 후보 보강이 같은 MVP 범위를 사용한다.
# 구는 더 이상 고정하지 않는다. 조회할 구는 대상 장소의 district_code에서 나오고,
# 광역 코드는 지원 구가 전부 서울이라 concentration_policy가 정한다(D-095).
_KST = ZoneInfo("Asia/Seoul")


class _ConcentrationLookupMemo:
    """요청 한 번 안에서 같은 장소를 두 번 조회하지 않게 한다.

    후보 5개는 서로 2km 안에 몰려 있고 대체 조회 반경은 0.5km라, 여러 후보가 같은
    인근 장소를 가리키는 일이 흔하다. 메모 없이 후보마다 최대 3곳을 시도하면 한
    요청에 최대 15회가 나가는데, 집중률 API는 오퍼레이션 단위 일일 한도가 있다.

    후보들이 asyncio.gather로 동시에 도는데, Task를 캐시하므로 먼저 도착한 쪽이
    만든 조회를 나머지가 함께 기다린다(같은 조회가 병렬로 두 번 나가지 않는다).
    """

    def __init__(self, tool: GetConcentrationTool) -> None:
        self._tool = tool
        self._tasks: dict[tuple[str, str], asyncio.Task[ConcentrationToolResult]] = {}

    async def lookup(
        self, *, search_keys: Sequence[str], canonical_name: str, signgu_code: str
    ) -> ConcentrationToolResult:
        # 구까지 키에 넣는다. 같은 이름이 구를 달리해 오면 서로 다른 조회다.
        key = (signgu_code, canonical_name)
        task = self._tasks.get(key)
        if task is None:
            task = asyncio.create_task(
                execute_concentration_by_search_keys(
                    self._tool,
                    search_keys=search_keys,
                    canonical_name=canonical_name,
                    signgu_code=signgu_code,
                )
            )
            self._tasks[key] = task
        return await task


class CandidateEnrichmentService:
    """D의 상위 후보를 받아 C의 Concentration Tool로 보강한다."""

    def __init__(
        self,
        concentration_tool: GetConcentrationTool,
        *,
        candidate_limit: int,
        clock: Callable[[], datetime] | None = None,
        mapping_cache: ConcentrationMappingCache | None = None,
    ) -> None:
        if not (
            MIN_RECOMMENDATION_LIMIT
            <= candidate_limit
            <= MAX_RECOMMENDATION_CANDIDATE_LIMIT
        ):
            raise ValueError(
                "candidate_limit은 "
                f"{MIN_RECOMMENDATION_LIMIT} 이상 "
                f"{MAX_RECOMMENDATION_CANDIDATE_LIMIT} 이하여야 합니다."
            )
        self._concentration_tool = concentration_tool
        self._candidate_limit = candidate_limit
        self._clock = clock or (lambda: datetime.now(_KST))
        # 없으면 후보 이름 원문으로 조회한다(Supabase 미설정 환경·기존 테스트 호환).
        self._mapping_cache = mapping_cache

    async def enrich(
        self,
        request: CandidateEnrichmentRequest,
    ) -> CandidateEnrichmentResponse:
        """후보 순서를 유지하며 Concentration 조회를 병렬 실행한다."""

        if len(request.candidates) > self._candidate_limit:
            raise ValueError(
                f"보강 후보는 최대 {self._candidate_limit}개까지 요청할 수 있습니다."
            )
        reference_date = _as_kst_date(self._clock())
        mapped_places = await self._mapped_places()
        mappings = (
            {place.content_id: place for place in mapped_places}
            if mapped_places is not None
            else None
        )
        memo = _ConcentrationLookupMemo(self._concentration_tool)
        candidates = await asyncio.gather(
            *(
                self._enrich_candidate(
                    candidate,
                    reference_date=reference_date,
                    mapping=mappings.get(candidate.place_id) if mappings is not None else None,
                    mapped_places=mapped_places,
                    memo=memo,
                )
                for candidate in request.candidates
            )
        )
        statuses: list[CandidateEnrichmentStatus] = [
            candidate.status for candidate in candidates
        ]
        return CandidateEnrichmentResponse(
            request_id=request.request_id,
            status=resolve_enrichment_status(statuses),
            candidates=candidates,
        )

    async def _mapped_places(self) -> tuple[StoredPlaceLocation, ...] | None:
        """집중률 매핑 장소 목록. 캐시가 없으면 None(기존 경로)."""

        if self._mapping_cache is None:
            return None
        return await self._mapping_cache.places()

    async def _enrich_candidate(
        self,
        candidate: CandidateEnrichmentTarget,
        *,
        reference_date: date,
        mapping: StoredPlaceLocation | None,
        mapped_places: tuple[StoredPlaceLocation, ...] | None,
        memo: _ConcentrationLookupMemo,
    ) -> CandidateEnrichmentResult:
        """후보 1건의 집중률을 조회한다.

        매핑이 있으면 조회는 검색어 목록으로, 대조는 정식 명칭으로 한다(D-057) —
        INFO 경로와 같은 방식이다. 후보 이름 원문을 그대로 `tAtsNm`에 넣던 기존
        방식은 공백이 든 이름에 항상 0건이 돌아오고(D-043), 이름이 정식 명칭과
        다르면 대조에서 탈락한다. 2026-08-09 기준 매핑 101건 중 원문으로 조회가
        통하는 건 67건뿐이었다.

        매핑이 없는 후보는 자기 이름으로 조회하지 않는다 — 매핑은 집중률 API에
        데이터가 있는 장소의 목록이라 없으면 조회해도 0건이다. 대신 INFO와 같은
        방식으로 인근 매핑 장소의 값을 빌려 `is_proxy=True`로 표시한다. 활성 844건
        중 매핑은 100건뿐이라, 빌려오지 않으면 다수 후보가 혼잡도 판정에서 통째로
        빠진다(안국역 2km 내 711건 중 매핑 70건, 최근접 15건 중 1건).
        """
        if mapped_places is not None and mapping is None:
            return await self._enrich_by_proxy(
                candidate,
                reference_date=reference_date,
                mapped_places=mapped_places,
                memo=memo,
            )

        canonical_name = (
            mapping.concentration_name
            if mapping is not None and mapping.concentration_name
            else candidate.name
        )
        search_keys = mapping.concentration_search_keys if mapping is not None else ()
        # 조회할 구는 매핑된 장소의 것을 쓴다. 구를 모르면 조회하지 않는다 - 종로구로
        # 대신 물으면 다른 구 장소는 언제나 0건이라 틀린 조회가 "정보 없음"으로 보인다.
        signgu_code = concentration_signgu_code(
            mapping.district_code if mapping is not None else None
        )
        if signgu_code is None:
            return CandidateEnrichmentResult(
                **candidate.model_dump(),
                status="no_data",
                concentration=[],
                error=None,
                provider_metadata=[],
            )
        tool_result = await memo.lookup(
            search_keys=search_keys,
            canonical_name=canonical_name,
            signgu_code=signgu_code,
        )
        if tool_result.status is ToolStatus.UNAVAILABLE:
            error = tool_result.error
            return CandidateEnrichmentResult(
                **candidate.model_dump(),
                status="unavailable",
                concentration=None,
                error=ContextError(
                    code=error.code if error else "unavailable",
                    message=(error.message if error else "집중률 정보를 가져오지 못했습니다."),
                    retryable=error.retryable if error else True,
                ),
                provider_metadata=[
                    _map_provider_metadata(metadata) for metadata in tool_result.provider_metadata
                ],
            )

        forecast = select_concentration_forecast(
            tool_result.concentration,
            candidate_name=canonical_name,
            reference_date=reference_date,
        )
        forecasts: list[ConcentrationForecastData] = []
        rate = forecast.concentration_rate if forecast is not None else None
        if forecast is not None and is_valid_concentration_rate(rate):
            normalized = normalize_concentration(rate)
            forecasts = [
                ConcentrationForecastData(
                    place_name=forecast.place_name,
                    forecast_date=reference_date.isoformat(),
                    concentration_rate=rate,
                    concentration_level=normalized.level,
                    concentration_label=normalized.label,
                )
            ]
        metadata = [_map_provider_metadata(item) for item in tool_result.provider_metadata]
        return CandidateEnrichmentResult(
            **candidate.model_dump(),
            status="success" if forecasts else "no_data",
            concentration=forecasts,
            error=None,
            provider_metadata=metadata,
        )

    async def _enrich_by_proxy(
        self,
        candidate: CandidateEnrichmentTarget,
        *,
        reference_date: date,
        mapped_places: tuple[StoredPlaceLocation, ...],
        memo: _ConcentrationLookupMemo,
    ) -> CandidateEnrichmentResult:
        """매핑 없는 후보에 인근 매핑 장소의 값을 빌려 채운다(INFO와 같은 방식).

        반경·시도 횟수는 INFO 대체 조회와 같은 값을 쓴다. 같은 사용자가 "여기
        혼잡해?"와 "한산한 곳 추천해줘"에서 다른 기준을 보면 곤란하다.

        가까운 순서로 시도하고 값이 나오는 첫 장소에서 멈춘다. 어느 장소에서
        빌렸는지와 거리를 함께 실어 보낸다 — 근사치를 얼마나 믿을지는 값을 쓰는
        쪽(D)이 정할 문제이고, C는 판단 근거만 제공한다.
        """
        proxy_places = select_nearest_mapped_places(
            mapped_places,
            latitude=candidate.latitude,
            longitude=candidate.longitude,
            radius_km=INFO_CONCENTRATION_FALLBACK_RADIUS_KM,
            limit=INFO_CONCENTRATION_FALLBACK_ATTEMPT_LIMIT,
        )
        attempted_metadata: list[ProviderMetadata] = []
        for proxy_place in proxy_places:
            if not proxy_place.concentration_name:
                continue
            # 구를 모르는 장소는 건너뛴다. 종로구로 대신 물으면 다른 구 장소는
            # 언제나 0건이라, 조회 실패가 "정보 없음"과 구분되지 않는다.
            proxy_signgu_code = concentration_signgu_code(proxy_place.district_code)
            if proxy_signgu_code is None:
                continue
            proxy_result = await memo.lookup(
                search_keys=proxy_place.concentration_search_keys,
                canonical_name=proxy_place.concentration_name,
                signgu_code=proxy_signgu_code,
            )
            attempted_metadata.extend(
                _map_provider_metadata(item)
                for item in proxy_result.provider_metadata
            )
            if proxy_result.status is ToolStatus.UNAVAILABLE:
                # 외부 장애는 다음 후보로 넘어가도 같은 결과다(INFO와 동일 판단).
                error = proxy_result.error
                return CandidateEnrichmentResult(
                    **candidate.model_dump(),
                    status="unavailable",
                    concentration=None,
                    error=ContextError(
                        code=error.code if error else "unavailable",
                        message=(
                            error.message
                            if error
                            else "집중률 정보를 가져오지 못했습니다."
                        ),
                        retryable=error.retryable if error else True,
                    ),
                    provider_metadata=attempted_metadata,
                )

            forecast = select_concentration_forecast(
                proxy_result.concentration,
                candidate_name=proxy_place.concentration_name,
                reference_date=reference_date,
            )
            rate = forecast.concentration_rate if forecast is not None else None
            if forecast is None or not is_valid_concentration_rate(rate):
                # 이 장소는 해당 날짜 예보가 없다 — 다음으로 가까운 곳을 시도한다.
                continue

            normalized = normalize_concentration(rate)
            return CandidateEnrichmentResult(
                **candidate.model_dump(),
                status="success",
                concentration=[
                    ConcentrationForecastData(
                        place_name=forecast.place_name,
                        forecast_date=reference_date.isoformat(),
                        concentration_rate=rate,
                        concentration_level=normalized.level,
                        concentration_label=normalized.label,
                        is_proxy=True,
                        proxy_place_name=forecast.place_name,
                        proxy_distance_km=round(
                            haversine_km(
                                candidate.latitude,
                                candidate.longitude,
                                proxy_place.latitude,
                                proxy_place.longitude,
                            ),
                            3,
                        ),
                    )
                ],
                error=None,
                provider_metadata=attempted_metadata,
            )

        return CandidateEnrichmentResult(
            **candidate.model_dump(),
            status="no_data",
            concentration=[],
            error=None,
            provider_metadata=attempted_metadata,
        )


async def execute_concentration_by_search_keys(
    concentration_tool: GetConcentrationTool,
    *,
    search_keys: Sequence[str],
    canonical_name: str,
    signgu_code: str,
) -> ConcentrationToolResult:
    """검색어를 순서대로 시도하고 결과가 나오면 멈춘다(D-057).

    tAtsNm은 공백이 든 값에 0건을 돌려주므로 정식 명칭을 그대로 못 쓰는 이름이 많다.
    검색어를 하나만 두면 '서울 동대문 닭한마리 골목'이 '닭한마리'로만 조회돼, 사용자가
    다른 표현으로 물으면 못 찾는다. 목록을 앞에서부터 시도해 폭을 넓힌다.

    1순위는 이관 전 단일 검색어와 같은 값이라 기존 동작이 그대로 유지된다. 뒤 토큰은
    앞에서 결과가 나오면 호출되지 않으므로 평상시 호출 수도 늘지 않는다.

    UNAVAILABLE은 즉시 반환한다 — 외부 장애는 다음 검색어로 바꿔도 같은 결과다.

    signgu_code는 대상 장소가 속한 구다. 집중률 API가 이 값으로 엄격하게 거르므로
    (중구 명동성당을 종로구로 물으면 0건) 장소마다 맞는 값을 받아야 한다. 호출자가
    concentration_signgu_code()로 바꿔 넘긴다 - 구를 모르면 조회 자체를 하지 않는다.

    INFO 경로(`service.py`)와 추천 후보 보강이 함께 쓴다. 두 경로가 같은 조회 규칙을
    갖도록 여기 한 곳에만 둔다.
    """
    candidates = [key for key in search_keys if key] or [canonical_name]
    result = None
    for candidate in candidates:
        result = await concentration_tool.execute(
            ConcentrationQuery(
                area_code=CONCENTRATION_AREA_CODE,
                district_code=signgu_code,
                place_name=candidate,
            )
        )
        if result.status is not ToolStatus.NO_DATA:
            return result
    assert result is not None  # candidates는 항상 하나 이상이다
    return result


def _map_provider_metadata(metadata: ProviderMetadataData) -> ProviderMetadata:
    """공통 Provider metadata를 A–C Pydantic 계약으로 옮긴다."""

    return ProviderMetadata(
        source=metadata.source.value,
        status=metadata.status.value,
        retrieved_at=metadata.retrieved_at,
    )


def _as_kst_date(value: datetime) -> date:
    """호출 시각을 한국 날짜로 바꿔 집중률 예측 기준일로 사용한다."""

    if value.tzinfo is None:
        return value.replace(tzinfo=_KST).date()
    return value.astimezone(_KST).date()


def parse_concentration_forecast_date(value: str | None) -> date | None:
    """Provider별 날짜 표기를 date로 통일한다."""
    if value is None:
        return None
    normalized = value.strip()
    try:
        if len(normalized) == 8 and normalized.isdigit():
            return datetime.strptime(normalized, "%Y%m%d").date()
        return date.fromisoformat(normalized)
    except ValueError:
        return None


def select_concentration_forecast(
    concentration: ConcentrationResult | None,
    *,
    candidate_name: str,
    reference_date: date,
) -> ConcentrationForecast | None:
    """오늘 날짜의 유효값 중 요청 후보와 이름이 같은 예측을 고른다.

    집중률 API의 tAtsNm은 부분 일치 검색이라 한 번에 여러 장소가 딸려 온다("종묘"로
    조회하면 "종묘광장공원"도 함께 온다). 이름이 안 맞는데 첫 예보로 폴백하면 엉뚱한
    장소의 혼잡도를 정상 응답처럼 답하게 되므로, 여러 장소가 섞여 왔는데 일치하는
    이름이 없으면 답을 포기한다. 틀린 값보다 "정보 없음"이 낫다.

    한 곳만 온 경우에는 표기가 달라도(예: 요청 "운현궁" ↔ 응답 "서울 운현궁") 그
    장소가 맞으므로 그대로 쓴다.
    """

    if concentration is None:
        return None
    forecasts = [
        forecast
        for forecast in concentration.forecasts
        if parse_concentration_forecast_date(forecast.forecast_date) == reference_date
        and is_valid_concentration_rate(forecast.concentration_rate)
    ]
    if not forecasts:
        return None
    normalized_name = candidate_name.strip()
    matched = next(
        (
            forecast
            for forecast in forecasts
            if forecast.place_name.strip() == normalized_name
        ),
        None,
    )
    if matched is not None:
        return matched
    if len({forecast.place_name.strip() for forecast in forecasts}) > 1:
        return None
    return forecasts[0]


def select_concentration_forecasts(
    concentration: ConcentrationResult | None,
    *,
    candidate_name: str,
    start_date: date,
    limit: int = 7,
) -> tuple[ConcentrationForecast, ...]:
    """방문 예정일을 포함해 최대 ``limit``일의 장소별 예측을 고른다.

    단일 날짜 선택과 같은 이름 대조 규칙을 날짜별로 적용한다. 부분 일치 API 응답에
    다른 관광지가 섞여도 차트에 잘못된 장소의 막대가 들어가지 않게 하기 위해서다.
    """

    if concentration is None or limit <= 0:
        return ()
    dates = sorted(
        {
            forecast_date
            for forecast in concentration.forecasts
            if (forecast_date := parse_concentration_forecast_date(forecast.forecast_date))
            is not None
            and forecast_date >= start_date
        }
    )
    selected: list[ConcentrationForecast] = []
    for forecast_date in dates:
        forecast = select_concentration_forecast(
            concentration,
            candidate_name=candidate_name,
            reference_date=forecast_date,
        )
        if forecast is not None:
            selected.append(forecast)
        if len(selected) == limit:
            break
    return tuple(selected)

__all__ = [
    "CandidateEnrichmentService",
    "parse_concentration_forecast_date",
    "select_concentration_forecast",
    "select_concentration_forecasts",
]
