"""D가 선정한 후보를 C가 후조회할 때 사용하는 독립 A–C 계약."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.agent_context.schemas import ContextError, ProviderMetadata, StrictModel
from app.concentration_policy import ConcentrationLabel, ConcentrationLevel
from app.recommendation_limits import MAX_RECOMMENDATION_CANDIDATE_LIMIT

CandidateEnrichmentStatus = Literal["success", "no_data", "unavailable"]
EnrichmentResponseStatus = Literal[
    "success",
    "partial",
    "no_data",
    "unavailable",
]


class CandidateEnrichmentTarget(StrictModel):
    """Concentration 보강을 요청할 추천 후보 한 건."""

    place_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)

    @field_validator("place_id", "name")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("후보 식별자와 이름은 공백일 수 없습니다.")
        return normalized


class CandidateEnrichmentRequest(StrictModel):
    """A가 D의 상위 후보를 받아 C에 전달하는 보강 요청."""

    request_id: str = Field(min_length=1)
    candidates: list[CandidateEnrichmentTarget] = Field(
        min_length=1,
        max_length=MAX_RECOMMENDATION_CANDIDATE_LIMIT,
    )
    features: list[Literal["concentration"]] = Field(min_length=1, max_length=1)

    @field_validator("request_id")
    @classmethod
    def normalize_request_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("request_id는 공백일 수 없습니다.")
        return normalized


class ConcentrationForecastData(StrictModel):
    """Provider 집중률 예측에서 A가 소비할 표준 필드."""

    place_name: str
    forecast_date: str | None = None
    concentration_rate: float = Field(ge=0)
    concentration_level: ConcentrationLevel
    concentration_label: ConcentrationLabel
    # 후보 본인의 값이 아니라 인근 매핑 장소에서 빌려온 값이라는 표시(D-036과 같은
    # 취지, INFO의 ConcentrationInfoResult.is_proxy와 같은 의미다). 활성 844건 중
    # 집중률 매핑은 100건뿐이라 매핑 없는 후보가 다수이고, 그런 후보는 이 값이
    # 없으면 혼잡도 판정에서 통째로 빠진다.
    #
    # 근사치는 후보 본인의 값과 신뢰도가 다르다. 점수에 어떻게 반영할지(D)와
    # 사용자에게 밝힐지(A)는 각 소유자가 정한다 — C는 사실만 실어 보낸다.
    is_proxy: bool = False
    # 값을 빌려온 실제 장소. is_proxy=False면 place_name과 같다.
    proxy_place_name: str | None = None
    # 후보에서 그 장소까지의 거리(km). 근사치를 얼마나 믿을지 판단하는 근거다.
    proxy_distance_km: float | None = Field(default=None, ge=0)


class CandidateEnrichmentResult(StrictModel):
    """후보 원본 식별정보와 Concentration 후조회 결과."""

    place_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    status: CandidateEnrichmentStatus
    concentration: list[ConcentrationForecastData] | None = None
    error: ContextError | None = None
    provider_metadata: list[ProviderMetadata] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_state(self) -> CandidateEnrichmentResult:
        if self.status == "success":
            if not self.concentration:
                raise ValueError("success 후보에는 집중률 데이터가 필요합니다.")
            if self.error is not None:
                raise ValueError("success 후보에는 error를 넣을 수 없습니다.")
        elif self.status == "no_data":
            if self.concentration != []:
                raise ValueError("no_data 후보의 집중률 데이터는 빈 목록이어야 합니다.")
            if self.error is not None:
                raise ValueError("no_data 후보에는 error를 넣을 수 없습니다.")
        else:
            if self.concentration is not None:
                raise ValueError("unavailable 후보에는 집중률 데이터를 넣을 수 없습니다.")
            if self.error is None:
                raise ValueError("unavailable 후보에는 error가 필요합니다.")
        return self


class CandidateEnrichmentResponse(StrictModel):
    """C가 후보 순서를 유지해 반환하는 Concentration 보강 응답."""

    request_id: str = Field(min_length=1)
    status: EnrichmentResponseStatus
    candidates: list[CandidateEnrichmentResult] = Field(
        min_length=1,
        max_length=MAX_RECOMMENDATION_CANDIDATE_LIMIT,
    )

    @model_validator(mode="after")
    def validate_overall_status(self) -> CandidateEnrichmentResponse:
        expected_status = resolve_enrichment_status(
            [candidate.status for candidate in self.candidates]
        )
        if self.status != expected_status:
            raise ValueError(
                f"후보 상태 조합에는 전체 상태 {expected_status!r}가 필요합니다."
            )
        return self


def resolve_enrichment_status(
    statuses: list[CandidateEnrichmentStatus],
) -> EnrichmentResponseStatus:
    """후보별 상태 조합을 보강 응답의 전체 상태로 축약한다."""

    if statuses and all(status == "success" for status in statuses):
        return "success"
    if statuses and all(status == "no_data" for status in statuses):
        return "no_data"
    if statuses and all(status == "unavailable" for status in statuses):
        return "unavailable"
    return "partial"


__all__ = [
    "CandidateEnrichmentRequest",
    "CandidateEnrichmentResponse",
    "CandidateEnrichmentResult",
    "CandidateEnrichmentStatus",
    "CandidateEnrichmentTarget",
    "ConcentrationForecastData",
    "EnrichmentResponseStatus",
]
