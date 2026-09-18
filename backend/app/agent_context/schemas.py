"""A가 조건을 전달하고 C가 추천 Context를 반환하는 Pydantic 계약."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class StrictModel(BaseModel):
    """계약에 정의하지 않은 필드는 수신하지 않는다."""

    model_config = ConfigDict(extra="forbid")


class UserConditions(StrictModel):
    current_location: str | None = None
    search_center: str | None = None
    place_types: list[str] = Field(default_factory=list)
    place_tags: list[str] = Field(default_factory=list)
    weather: Literal["rain", "snow", "hot", "cold", "good"] | None = None
    # NO_MENTION은 "날씨 언급이 없음", IGNORE는 "상관없다고 명시함"이다. 둘의 동작이
    # 반대라(전자는 조회, 후자는 생략) 한 값으로 겸할 수 없어 A가 분리했다.
    # C는 A보다 먼저 값을 받아들여야 한다 — Literal에 없으면 A 배포 시점에
    # ValidationError로 요청 전체가 깨진다.
    weather_intent: Literal["AVOID", "ENJOY", "NO_MENTION", "IGNORE"] | None = None
    transport: Literal["walk", "public", "car"] | None = None
    max_travel_time: int | None = Field(default=None, gt=0)
    time_available: int | None = Field(default=None, gt=0)
    environment: Literal["indoor", "outdoor", "any"] | None = None
    companion: (
        Literal["solo", "couple", "friend", "parent", "child", "pet"] | None
    ) = None
    budget: str | None = None
    exclude_tags: list[str] = Field(default_factory=list)
    special_requirements: list[str] = Field(default_factory=list)
    taste_query: str | None = None
    # 요구된 무장애 편의. 값이 있으면 C가 후보를 그 조건으로 좁힌다.
    #
    # 어휘는 아홉이고(app.domain.models.AccessibilityNeed) **여럿이면 전부
    # 만족**해야 한다. "유모차 끌고 갈 만한 곳"은 stroller_access와
    # infant_facilities로 온다 — 유모차를 끌고 들어갈 수 있는지와 수유실이 있는지는
    # 다른 질문이다.
    #
    # wheelchair_access와 stroller_access를 나눈 이유는 같은 원문에서 판정이
    # 갈리기 때문이다(계단은 휠체어만 막고, 흙길은 유모차에 더 불리하다).
    # wheelchair_rental·seating_available·low_floor_transit은 휠체어 사용자
    # 전용이 아니라 오래 걷기 힘든 동행에게 쓸모 있는 값이다.
    #
    # Literal로 조이지 않는 이유는 weather_intent와 같다. C가 A보다 먼저 값을
    # 받아들여야 A 배포 시점에 요청 전체가 깨지지 않는다. 대신 모르는 값은
    # 무시하고 warnings에 남긴다(nearby_place_details.py) — 조용히 버리면
    # 사용자가 요구한 조건이 사라진 것을 아무도 모른다.
    accessibility_needs: list[str] = Field(default_factory=list)

    @field_validator("current_location", "search_center", "budget", "taste_query")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("문자열 조건은 공백일 수 없습니다.")
        return normalized

    @field_validator(
        "place_types",
        "place_tags",
        "exclude_tags",
        "special_requirements",
        "accessibility_needs",
    )
    @classmethod
    def normalize_text_list(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("복수 조건에는 빈 문자열을 포함할 수 없습니다.")
        return normalized


class AgentContextRequest(StrictModel):
    request_id: str = Field(min_length=1)
    intent: Literal["RECOMMEND"]
    conditions: UserConditions
    # 사용자 발화 위치와 별도로, A가 검증한 기기 GPS를 좌표 객체로 전달한다.
    gps_location: Coordinates | None = None
    # 이미 소진된 후보 id(노출분 ∪ 거절분). C는 이 목록으로 추천 여부를 판정하지 않고
    # "수집 범위를 어디까지 넓힐지"에만 쓴다 — 제외 판정 자체는 여전히 D 몫이다
    # (a-c-context-contract-draft.md §2).
    #
    # 이 필드가 없으면 추가 추천이 성립하지 않는다. 장소 검색은 거리순 고정 정렬이라
    # 같은 조건으로 다시 부르면 같은 앞쪽 N건이 오고, D가 그걸 전부 제외해 0건이 된다.
    # C가 소진분을 알아야 그만큼 더 받아와서 새 후보를 채울 수 있다.
    excluded_place_ids: list[str] = Field(default_factory=list)
    # 같은 턴 안의 보충 조회임을 알리고, A가 첫 조회에서 확정한 검색 기준점을 그대로
    # 넘긴다. 값이 있으면 C는 **장소만** 다시 받는다 — 위치 해석·날씨·공휴일을
    # 건너뛴다.
    #
    # 건너뛰어도 되는 이유는 A가 그 결과를 어차피 버리기 때문이다.
    # `_merge_recommendation_context_places()`가 첫 배치에서 places만 갈아끼우고
    # 나머지는 그대로 두며, `merge_prepared()`도 첫 배치의 판정 기준을 재사용한다.
    # 즉 보충 1회마다 날씨(1) + 공휴일(1) + 위치 해석(3)을 계산해서 버리고 있었다.
    #
    # 좌표를 A가 넘기는 이유는 위치 해석까지 건너뛰기 위해서다. 그 3회가 제일 크고,
    # 같은 턴이라 기준점이 바뀔 일도 없다. 값이 없으면 기존과 동일하게 동작한다.
    resolved_search_center: Coordinates | None = None

    @field_validator("request_id")
    @classmethod
    def normalize_request_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("request_id는 공백일 수 없습니다.")
        return normalized

    @field_validator("excluded_place_ids")
    @classmethod
    def normalize_excluded_place_ids(cls, values: list[str]) -> list[str]:
        """공백 id는 거른다 — 중복은 그대로 두고 소비 측에서 frozenset으로 받는다."""
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("excluded_place_ids에는 빈 문자열을 포함할 수 없습니다.")
        return normalized


class Coordinates(StrictModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class ProviderMetadata(StrictModel):
    source: str = Field(min_length=1)
    status: Literal["success", "no_data", "partial", "unavailable"]
    retrieved_at: datetime

    @field_validator("source")
    @classmethod
    def normalize_source(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("source는 공백일 수 없습니다.")
        return normalized

    @field_validator("retrieved_at")
    @classmethod
    def validate_retrieved_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("retrieved_at은 timezone-aware datetime이어야 합니다.")
        return value


class ContextWarning(StrictModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)


class ContextError(StrictModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    retryable: bool


class Clarification(StrictModel):
    code: Literal[
        "location_required",
        "location_ambiguous",
        "place_required",
        # 행사 질의에서 지명이 없을 때. `place_required`와 갈라 두는 이유는 되묻는
        # 문장이 달라야 하기 때문이다 — "축제 추천해줘"에 "어떤 장소에 대해 알고
        # 싶으신가요?"라고 물으면 사용자가 묻지 않은 것을 되묻는 셈이 된다. 무엇이
        # 비었는지는 C가 알고 문장은 A가 정하는 지금 분담을 유지한다(TP-237).
        "event_place_required",
        "place_ambiguous",
    ]
    missing_fields: list[str] = Field(default_factory=list)
    candidates: list[str] = Field(default_factory=list)


def parse_candidate_names(raw: str) -> list[str]:
    """resolve_location.py가 "|" 구분 문자열로 준 되묻기 후보 이름을 리스트로 푼다.

    ToolError.details가 dict[str, str]라 리스트를 직접 못 담아 생긴 형식이다.
    빈 문자열(지오코딩 경로처럼 이름이 없는 경우)은 빈 리스트가 된다.
    """
    return [name for name in raw.split("|") if name]


class ResolvedLocation(StrictModel):
    # 사용자가 말한 문자열(수식어 제거 후). 근거 문장이 기준점을 부를 때 쓰는 값이라
    # 비어 있으면 안 된다 — resolved_name은 지오코딩으로 풀리면 도로명 주소가
    # 되므로(providers/geocoding.py) 표시용으로 쓸 수 없다.
    requested_query: str = Field(min_length=1)
    resolved_name: str
    # 이 좌표의 출처. "query"면 requested_query가 사용자가 말한 장소이고,
    # "device_gps"면 기기 위치라 부를 이름이 없다(requested_query는 자리표시자).
    source: Literal["query", "device_gps"]
    location: Coordinates
    address: str | None = None


class WeatherForecast(StrictModel):
    # D-051: C는 사실만 싣고 판정하지 않는다. 예전엔 여기 3단계 판정(condition)이
    # 있었지만, 사용자 의도(AVOID/ENJOY)를 반영하지 못해 D의
    # resolve_weather_condition()으로 이관하고 필드를 제거했다.
    forecast_for: datetime
    # 아래 세 필드가 판정 없는 날씨 사실이다. 기상청 코드(PTY/SKY)를 그대로 넘기지
    # 않고 C가 도메인 용어로 옮긴다 — 코드 체계가 D까지 새면 기상 API를 바꿀 때
    # D도 함께 고쳐야 한다.
    precipitation: (
        Literal["none", "rain", "snow", "sleet", "shower"] | None
    ) = None
    sky: Literal["clear", "cloudy", "overcast"] | None = None
    temperature_celsius: float | None = None

    @field_validator("forecast_for")
    @classmethod
    def validate_forecast_for(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("forecast_for는 timezone-aware datetime이어야 합니다.")
        return value


class PlaceCandidate(StrictModel):
    place_id: str
    name: str
    category: str
    # TourAPI 3단계 분류. category(대분류)만으로는 실내외를 가릴 수 없어 D가 판정에
    # 쓴다 — 관광지(12)에 고궁과 체험관이, 쇼핑(38)에 면세점과 시장이 함께 들어온다.
    lcls_systm1: str | None = None
    lcls_systm2: str | None = None
    lcls_systm3: str | None = None
    location: Coordinates
    operating_hours_raw: str | None = None
    rest_date_raw: str | None = None
    operating_schedule: dict[str, Any] | None = None
    # 무장애 조건이 있는 요청에서만 채워진다. 열쇠는 요구된 어휘
    # (`accessibility_needs`와 같은 값), 값은 `possible`·`partial` 중 하나다.
    #
    # **후보에 있다는 것이 온전히 가능하다는 뜻은 아니다.** 들어갈 수는 있는데
    # 못 가는 구역이 남는 곳(`partial`)도 후보로 남긴다 — 팔각정 하나 못 간다고
    # 추천에서 빼는 것은 과하기 때문이다. 대신 이 값을 올려 답변이 그 사실을
    # 말할 수 있게 한다. 값이 없으면 partial과 possible이 구분되지 않는다.
    #
    # `impossible`은 오지 않는다. RPC가 후보에서 이미 뺐다.
    #
    # 요구하지 않은 편의는 담지 않는다. 판정표가 있는 셋(휠체어·유모차·시각안내)
    # 밖의 어휘도 담지 않는다 — 나머지 여섯은 아직 원문 규칙으로 거르므로
    # "후보에 있다" 말고는 할 말이 없다.
    accessibility_verdicts: dict[str, str] | None = None


class HolidayInfo(StrictModel):
    date: str
    name: str


T = TypeVar("T")


class ContextValue(StrictModel, Generic[T]):
    status: Literal["success", "no_data", "partial", "unsupported", "unavailable"]
    data: T | None = None
    error: ContextError | None = None
    warnings: list[ContextWarning] = Field(default_factory=list)
    provider_metadata: list[ProviderMetadata] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_state(self) -> ContextValue[T]:
        if self.status in {"success", "partial"}:
            if self.data is None:
                raise ValueError(f"{self.status} Context에는 data가 필요합니다.")
            if self.error is not None:
                raise ValueError(f"{self.status} Context에는 error를 넣을 수 없습니다.")
        elif self.status == "no_data":
            if not _is_empty_data(self.data):
                raise ValueError("no_data Context에는 비어 있는 data만 허용됩니다.")
            if self.error is not None:
                raise ValueError("no_data Context에는 error를 넣을 수 없습니다.")
        else:
            if self.data is not None:
                raise ValueError(f"{self.status} Context에는 data를 넣을 수 없습니다.")
            if self.error is None:
                raise ValueError(f"{self.status} Context에는 error가 필요합니다.")
        return self


def _is_empty_data(value: object) -> bool:
    """목록형 no_data는 빈 컬렉션, 단건형 no_data는 null을 사용한다."""

    return value is None or (
        isinstance(value, (list, tuple, dict, set)) and not value
    )


class DistrictScope(StrictModel):
    """후보를 구 하나에서 통째로 모았다는 사실(D-119).

    구 이름을 정규화한 값이라 사용자가 "강남"이라고 해도 여기는 "강남구"다.
    """

    # TourAPI 법정동 코드의 시군구 부분(lDongSignguCd, 강남구 "680").
    district_code: str = Field(min_length=1)
    district_name: str = Field(min_length=1)


class RecommendationContext(StrictModel):
    # 반경 검색으로 후보를 **모은** 중심. 사용자가 있는 곳이 아니라 "이번 검색을
    # 어디를 중심으로 했는가"다. search_center → current_location → 기기 GPS 순으로
    # 정해진다(service.py::fetch_context).
    #
    # 후보를 **줄 세우는** 기준점은 여기가 아니라 user_location이다 — 거리·경로·근거
    # 문장은 사용자 위치에서 잰다(TP-112, domain/ranking_origin.py). 사용자 위치를
    # 모르는 요청에서만 이 값이 랭킹 기준점도 겸한다.
    location: ContextValue[ResolvedLocation] | None = None
    # 사용자가 있는 곳. 기준점(location)이 따로 잡혀도 버리지 않는다.
    # current_location(발화) → 기기 GPS 순으로 정해진다(service.py::_resolve_user_location).
    # location의 search_center → current_location → GPS와 같은 우선순위다 — 기준점만
    # 발화를 앞세우고 사용자 위치만 GPS를 앞세우면 한 요청 안에서 두 좌표가 서로 다른
    # 규칙으로 정해진다(TP-112).
    #
    # location과 같은 타입이라 D는 기준점에 쓰던 판정을 그대로 쓸 수 있다 — source가
    # "query"면 requested_query가 사용자가 말한 이름이고, "device_gps"면 부를 이름이
    # 없다(recommendation_pipeline.py::resolve_origin_name).
    user_location: ContextValue[ResolvedLocation] | None = None
    weather: ContextValue[WeatherForecast] | None = None
    places: ContextValue[list[PlaceCandidate]] | None = None
    holidays: ContextValue[list[HolidayInfo]] | None = None
    # 후보를 어디에서 모았는가. None이면 지금까지처럼 location을 중심으로 한 반경
    # 검색이고, 값이 있으면 그 구 전체에서 모은 것이다(D-119).
    #
    # **판정이 아니라 사실만 싣는다**(D-051). "거리 점수를 쓰지 마라"가 아니라
    # "이 후보들에는 기준점이 없다"는 사실이다. 구 단위 요청의 location은 구 이름을
    # 푼 대표점이라 후보 수집에도 정렬에도 쓰이지 않았다 — 그 좌표와의 거리는
    # 사용자가 말한 것과 아무 관계가 없다. 그것을 어떻게 채점에 반영할지는 D가
    # 정한다.
    district_scope: DistrictScope | None = None


class ResponseMetadata(StrictModel):
    rule_versions: dict[str, str] = Field(default_factory=dict)
    provider_metadata: list[ProviderMetadata] = Field(default_factory=list)


class AgentContextResponse(StrictModel):
    request_id: str = Field(min_length=1)
    intent: Literal["RECOMMEND"]
    contract_version: Literal["draft-v0"] = "draft-v0"
    status: Literal[
        "success",
        "partial",
        "no_data",
        "needs_clarification",
        "unsupported",
        "unavailable",
    ]
    context: RecommendationContext | None = None
    clarification: Clarification | None = None
    warnings: list[ContextWarning] = Field(default_factory=list)
    error: ContextError | None = None
    metadata: ResponseMetadata

    @field_validator("request_id")
    @classmethod
    def normalize_response_request_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("request_id는 공백일 수 없습니다.")
        return normalized

    @model_validator(mode="after")
    def validate_state(self) -> AgentContextResponse:
        if self.status in {"success", "partial"}:
            if self.context is None:
                raise ValueError(f"{self.status} 응답에는 context가 필요합니다.")
            if self.clarification is not None or self.error is not None:
                raise ValueError(
                    f"{self.status} 응답에는 clarification/error를 넣을 수 없습니다."
                )
        elif self.status == "needs_clarification":
            if self.context is not None or self.error is not None:
                raise ValueError(
                    "needs_clarification 응답에는 context/error를 넣을 수 없습니다."
                )
            if self.clarification is None:
                raise ValueError(
                    "needs_clarification 응답에는 clarification이 필요합니다."
                )
        elif self.status == "unsupported":
            if self.context is not None or self.clarification is not None:
                raise ValueError(
                    "unsupported 응답에는 context/clarification을 넣을 수 없습니다."
                )
            if self.error is None:
                raise ValueError("unsupported 응답에는 error가 필요합니다.")
        elif self.status == "unavailable":
            if self.clarification is not None:
                raise ValueError(
                    "unavailable 응답에는 clarification을 넣을 수 없습니다."
                )
            if self.error is None:
                raise ValueError("unavailable 응답에는 error가 필요합니다.")
        elif self.clarification is not None:
            raise ValueError("no_data 응답에는 clarification을 넣을 수 없습니다.")
        return self
