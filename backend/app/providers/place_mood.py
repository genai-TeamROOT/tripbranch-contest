"""장소 사진의 분위기로 후보를 재는 Provider.

역할: 두 경로를 한 곳에서 다룬다.

  발화 경로  "조용하고 오래된 데"  → 미리 계산된 축 점수를 읽어 온다.
             임베딩 모델이 필요 없다. 축 점수는 적재 때 계산해 뒀다.
  사진 경로  사용자가 올린 사진    → 임베딩해 닮은 장소를 찾는다.
             SigLIP이 있어야 한다.

두 경로를 한 Provider에 둔 이유는 같은 테이블(place_mood_vectors)을 보고 같은
축 이름을 쓰기 때문이다. 인코더가 없는 환경에서도 발화 경로는 돌아간다 —
`search_by_photo`만 인코더를 요구하고, `describe`는 저장소만 있으면 된다.

**축 점수는 정렬에만 쓴다.** 부호를 임계값으로 삼지 않는다. 세월 축은 종로
631곳 중 양수가 24곳뿐이라 "0보다 크면 새것"이 성립하지 않는다(D-087).
축 키는 영문이고 부호는 `+` 쪽을 가리킨다 — calm이 양수면 조용한 쪽이다.

**warm_toned를 사용자에게 "따뜻한 곳"이라고 부르지 않는다.** 한국어에서 그 말은
대부분 아늑하다는 뜻인데, 이 축은 사진의 색이 주황·갈색 계열인가 파랑·회색
계열인가만 잰다.

**calm은 사람 수가 아니라 화면 속 물건의 밀도다.** TourAPI 사진에는 사람이
거의 찍혀 있지 않은데도 인사동·광장시장이 북적임 쪽으로 나온다. 지금 붐비는지는
서울시 실시간 인구 데이터가 따로 알려주므로, 이 축으로 차분한 곳을 고른 뒤
혼잡도로 걷어내는 순서가 맞다.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Protocol

from app.domain.models import PlaceMoodMatch, PlaceMoodProfile
from app.observability.langfuse_tracing import observe_step, record_score
from app.providers.contracts import (
    ProviderResult,
    ProviderSource,
    ProviderStatus,
    provider_result,
)
from app.repositories.protocols import PlaceMoodRepository

logger = logging.getLogger(__name__)

# 사진 검색이 돌려줄 장소 수. 추천 카드가 3~5장이라 10이면 뒤에서 걸러낼 여유가
# 있고, 늘리면 응답만 커진다.
DEFAULT_MATCH_COUNT = 10

# 유사도 컷. 0.0은 **실측 전 임시값이다.** 축 점수 쪽은 사람 정답표 77곳으로
# AUC를 쟀지만(D-087), 사진끼리의 "이 정도면 닮았다" 경계는 표본이 없다.
# 재기 전까지 필터를 걸지 않고 순위만 쓴다 — 근거 없는 컷을 넣으면 왜 그 값인지
# 아무도 설명할 수 없는 숫자가 코드에 남는다.
DEFAULT_MIN_SIMILARITY = 0.0

# 사진 한 장으로 만든 벡터는 그 한 장에 좌우된다. 간판만 찍힌 사진이 장소를
# 대표하게 되므로, 품질이 중요한 자리에서는 이 값으로 걸러낸다. 종로 631곳 중
# 170곳(27%)이 사진 한 장뿐이고 대부분 쇼핑이다.
RELIABLE_PHOTO_COUNT = 2


def _search_summary(
    matches: Sequence[PlaceMoodMatch],
    *,
    candidate_count: int | None,
    min_similarity: float,
    match_count: int,
    mean_center: bool = False,
    axis_weight: float = 1.0,
) -> dict[str, object]:
    """사진 검색 한 번을 span에 실을 집계로 접는다.

    **유사도 분포가 이 요약의 존재 이유다.** 컷값을 아직 못 정했는데, 정하려면
    실제 요청에서 값이 어디에 몰리는지가 먼저 있어야 한다. 사진 자체도 사용자가
    올린 것이라 싣지 않는다 — 여기서 알고 싶은 건 몇 곳이 걸렸고 유사도가
    어디에 있나다.

    `mean_center`를 함께 싣는 이유는 그것이 유사도의 눈금을 바꾸기 때문이다
    (D-115). 켠 요청과 끈 요청의 값을 한 눈금으로 보면 컷을 엉뚱하게 잡는다.

    `axis_weight`도 같은 이유로 싣는다 — 1.0이 아니면 유사도가 정렬 기준이
    아니게 되므로(축 순위와 섞어 정렬한다), 그 요청의 유사도 분포는 순위와
    무관한 값이다(TP-206).
    """
    sims = [match.similarity for match in matches]
    single_photo = sum(
        1 for match in matches if match.profile.photo_count < RELIABLE_PHOTO_COUNT
    )
    return {
        "candidates": candidate_count,
        "mean_center": mean_center,
        "axis_weight": axis_weight,
        "matched": len(sims),
        "similarity_max": round(sims[0], 4) if sims else None,
        "similarity_min": round(sims[-1], 4) if sims else None,
        "similarity_mean": round(sum(sims) / len(sims), 4) if sims else None,
        # 결과 중 사진 한 장짜리가 몇이나 되나. 높으면 순위가 대표성 없는 사진에
        # 끌려간 것일 수 있다.
        "single_photo_hits": single_photo,
        "min_similarity": min_similarity,
        "match_count": match_count,
    }


class PlaceMoodEncoder(Protocol):
    """올린 사진을 적재 때와 같은 벡터 공간으로 인코딩하는 계약.

    적재는 `google/siglip2-base-patch16-224`(768차원)로 길이 1 정규화 상태에서
    했다. 다른 모델로 인코딩하면 좌표계가 달라 유사도가 뜻을 잃는다 — 숫자는
    나오지만 아무 의미가 없어서, 틀린 줄도 모르고 쓰게 된다.
    """

    def encode_image(self, image_bytes: bytes) -> Sequence[float]: ...


class PlaceMoodProvider:
    """분위기 축 조회와 사진 최근접 검색을 한 곳에서 다룬다."""

    def __init__(
        self,
        repository: PlaceMoodRepository,
        encoder: PlaceMoodEncoder | None = None,
        *,
        match_count: int = DEFAULT_MATCH_COUNT,
        min_similarity: float = DEFAULT_MIN_SIMILARITY,
        mean_center: bool = False,
        axis_weight: float = 1.0,
    ) -> None:
        self._repository = repository
        # 인코더가 None이어도 발화 경로는 돌아간다. SigLIP이 선택 의존성이라
        # 안 깔린 환경에서도 축 정렬은 쓸 수 있어야 한다.
        self._encoder = encoder
        self._match_count = match_count
        self._min_similarity = min_similarity
        # 조회할 때 전체 평균을 뺄지(D-115). 사진 경로에만 걸린다 — 축 점수는
        # 이미 계산돼 저장돼 있고, 축 점수는 방향과의 내적이라 중심을 빼면
        # 값의 의미가 달라진다.
        self._mean_center = mean_center
        # 유사도와 축을 섞는 비율(TP-206). 1.0이면 축을 쓰지 않는다.
        self._axis_weight = axis_weight

    @property
    def photo_search_available(self) -> bool:
        """사진 경로를 쓸 수 있는지. 인코더가 없으면 False다."""
        return self._encoder is not None

    async def describe(
        self,
        content_ids: Sequence[str],
    ) -> ProviderResult[dict[str, PlaceMoodProfile]]:
        """후보들의 분위기 축 점수를 읽는다.

        **결측이 정상이다.** 사진 임베딩은 종로구까지만 적재돼 있어 다른 구의
        후보는 여기서 빠진다. 호출부는 없는 것을 없는 대로 다뤄야 하고, 0점으로
        채우면 사진이 없는 장소가 "분위기가 안 맞는 곳"으로 잘못 밀린다.
        """
        if not content_ids:
            return provider_result(
                {},
                source=ProviderSource.SUPABASE_PLACE_MOOD,
                status=ProviderStatus.NO_DATA,
            )

        with observe_step("place_mood_describe", kind="retriever") as step:
            profiles = await self._repository.find_mood_profiles(content_ids)
            try:
                unique = len(dict.fromkeys(content_ids))
                summary = {
                    "candidates": unique,
                    "matched": len(profiles),
                    # 후보 중 몇 곳이 분위기 벡터를 갖고 있나. 이 값이 낮으면
                    # 축 정렬이 후보 일부에만 걸린다는 뜻이다 — 적재 범위를
                    # 넓혀야 하는 시점을 여기 숫자로 안다.
                    "coverage": round(len(profiles) / unique, 3) if unique else None,
                    "single_photo": sum(
                        1
                        for profile in profiles.values()
                        if profile.photo_count < RELIABLE_PHOTO_COUNT
                    ),
                }
                step.record(output=summary)
                if summary["coverage"] is not None:
                    record_score("place_mood_coverage", float(summary["coverage"]))
            except Exception:
                logger.warning(
                    "분위기 축 조회 관측 요약 실패(응답 흐름에는 영향 없음)",
                    exc_info=True,
                )

        return provider_result(
            profiles,
            source=ProviderSource.SUPABASE_PLACE_MOOD,
            status=(
                ProviderStatus.SUCCESS if profiles else ProviderStatus.NO_DATA
            ),
        )

    async def first_photo_urls(self, content_ids: Sequence[str]) -> dict[str, str]:
        """비교에 쓴 첫 사진의 주소. 결과 카드에 보여줄 값이다.

        조회 실패를 검색 실패로 만들지 않는다 — 사진이 안 보이는 것과 결과가
        안 나오는 것은 무게가 다르다.
        """
        if not content_ids:
            return {}
        try:
            return await self._repository.find_first_photo_urls(content_ids)
        except Exception:
            logger.warning("비교 사진 주소를 읽지 못했습니다.", exc_info=True)
            return {}

    async def search_by_photo(
        self,
        image_bytes: bytes,
        candidate_content_ids: Sequence[str] | None = None,
        *,
        latitude: float | None = None,
        longitude: float | None = None,
        radius_km: float | None = None,
        match_count: int | None = None,
    ) -> ProviderResult[tuple[PlaceMoodMatch, ...]]:
        """올린 사진과 분위기가 닮은 장소를 찾는다.

        **좌표와 반경으로 부르는 것이 기본이다.** 후보 목록으로 부르면 그 목록을
        만드는 데 TourAPI 상세 조회가 붙어 최대 20곳이 되는데, 반경으로 좁히면
        그 안 전부를 줄 세운다 — 사진 유사도는 DB 안에서 끝나 사실상 공짜다.

        인코더가 없으면 조회하지 않고 NO_DATA로 끝낸다. **빈 벡터로 흉내내지
        않는다** — 0으로 채운 벡터를 넘기면 유사도가 전부 0이 되어 아무 장소나
        순서대로 돌아오고, 그게 추천으로 나가면 틀린 줄도 모른다(D-042).
        """
        if not image_bytes:
            return provider_result(
                (),
                source=ProviderSource.SUPABASE_PLACE_MOOD,
                status=ProviderStatus.NO_DATA,
            )
        if self._encoder is None:
            logger.warning(
                "사진 분위기 검색을 요청받았지만 SigLIP 인코더가 없어 건너뜁니다."
            )
            return provider_result(
                (),
                source=ProviderSource.SUPABASE_PLACE_MOOD,
                status=ProviderStatus.NO_DATA,
            )

        with observe_step("place_mood_photo_search", kind="retriever") as step:
            # 열 수 없는 사진은 UnreadableImageError로 올라온다. 여기서 잡지
            # 않는다 — 호출부가 사용자 입력 문제로 다뤄야 하고, 빈 결과로
            # 바꾸면 "안 닮았다"와 구분되지 않는다.
            embedding = self._encoder.encode_image(image_bytes)
            matches = await self._repository.search_place_mood(
                embedding,
                candidate_content_ids,
                match_count=match_count or self._match_count,
                min_similarity=self._min_similarity,
                latitude=latitude,
                longitude=longitude,
                radius_km=radius_km,
                mean_center=self._mean_center,
                axis_weight=self._axis_weight,
            )
            try:
                step.record(
                    output=_search_summary(
                        matches,
                        candidate_count=(
                            None
                            if candidate_content_ids is None
                            else len(dict.fromkeys(candidate_content_ids))
                        ),
                        min_similarity=self._min_similarity,
                        match_count=match_count or self._match_count,
                        mean_center=self._mean_center,
                        axis_weight=self._axis_weight,
                    )
                )
            except Exception:
                logger.warning(
                    "사진 분위기 검색 관측 요약 실패(응답 흐름에는 영향 없음)",
                    exc_info=True,
                )

        return provider_result(
            matches,
            source=ProviderSource.SUPABASE_PLACE_MOOD,
            status=(
                ProviderStatus.SUCCESS if matches else ProviderStatus.NO_DATA
            ),
        )
