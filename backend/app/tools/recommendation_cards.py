"""추천 카드 목록에 실을 장소 정보를 content_id 순서대로 조립하는 내부 Tool.

역할: A가 확정한 추천 순위(content_id 리스트)를 받아 카드가 표시할 썸네일·이름·
주차·카테고리·운영시간을 채운다. 순위 결정에는 관여하지 않는다 — 받은 순서를
그대로 유지해서 돌려준다.

**저장소가 돌려주는 행 순서를 믿지 않는다.** PostgREST의 `in.(...)`는 입력 순서를
보장하지 않아서, 조회 결과를 그대로 쓰면 A가 정한 순위가 조용히 뒤섞인다. 입력
순서로 다시 세운 뒤 반환한다.

조회되지 않은 content_id는 카드 목록에서 빠지되 `missing_content_ids`로 함께
돌려준다. 조용히 빼기만 하면 A가 기대한 개수와 어긋난 걸 알 방법이 없다.

DB 조회는 카드 N건에 1회다. 여기에 더해, **관광공사 이미지가 하나도 없는 장소에
한해서만** Google Places를 부른다(GOOGLE_PLACE_PHOTO_ENABLED가 켜졌을 때).
이미지가 있는 장소는 외부 호출이 전혀 없다 — 844건 중 나머지 7,223건이 그렇다.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace

from app.domain.models import StoredPlaceDetail
from app.domain.operating_hours import OperatingSchedule, resolve_operating_schedule
from app.domain.parking import ParkingAvailability, normalize_parking
from app.errors import AppError
from app.providers.google_place_photos import GooglePlaceCoverPhoto
from app.providers.protocols import GooglePlacePhotoProviderProtocol
from app.providers.tour_category_registry import (
    TourCategoryRegistry,
    get_tour_category_registry,
)
from app.repositories.protocols import PlaceDetailsReadRepository
from app.tools.contracts import ToolError, ToolStatus

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PhotoAttribution:
    """사진을 표시할 때 함께 내보내야 하는 출처.

    Google Maps Platform 정책은 사진에 작성자를 밝히고, 사용자가 원본 사진을
    Google 지도에서 볼 수 있게 하라고 요구한다. 그래서 이름만이 아니라 프로필
    링크와 원본 링크까지 화면까지 들고 간다.
    """

    author_name: str
    author_uri: str | None = None
    source_uri: str | None = None


@dataclass(frozen=True)
class RecommendationCard:
    """추천 카드 1건이 표시할 정보."""

    content_id: str
    # places.title은 NOT NULL이고 공백도 제약으로 막혀 있어 실제로는 항상 값이 있다.
    # 저장소가 빈 문자열을 None으로 바꿔 주므로 타입만 옵셔널로 둔다.
    name: str | None
    # thumbnail_url(firstimage2)이 없으면 first_image_url(firstimage)로 대체한다.
    # 둘 다 없는 장소가 실측 844건 중 169건(20%)이라 None을 정상 값으로 다룬다.
    thumbnail_url: str | None
    # 이 주소가 죽었을 때 쓸 대안은 fallback_thumbnail_url에 있다(아래).
    # TourAPI 신분류 중분류명(예: 한식, 역사유적지, 전시시설).
    category_label: str | None
    parking_status: ParkingAvailability
    parking_note: str | None
    operating_schedule: OperatingSchedule
    # COMPARE의 TRAVEL_TIME 실측 연결(2026-08-21)에서 추가. 조회 자체는 이미 하고
    # 있던 값(StoredPlaceDetail)을 그대로 옮기는 것뿐이라 추가 DB 호출은 없다.
    latitude: float | None = None
    longitude: float | None = None
    # thumbnail_url이 죽었을 때 대신 그릴 주소(firstimage). None이면 대안이 없다.
    #
    # **두 컬럼은 한쪽만 채워지지 않는다** — places 8,067건 중 thumbnail_url만 null인
    # 행이 0건이고, 둘 다 있거나(7,223) 둘 다 없다(844). 그래서 thumbnail_url의 `or`
    # 폴백은 null만 보는 한 발동한 적이 없다. 정작 실패는 다른 데서 온다: 주소는 남아
    # 있는데 관광공사 서버에서 파일이 사라진다. 아현시장(2751432)은 firstimage2가
    # 404인데 firstimage는 200이다(2026-09-05 실측, 무작위 60곳 중 1곳도 같은 상태라
    # 2% 안팎으로 보인다).
    #
    # 여기서 미리 확인해 고르지 않는 이유는 비용이다. 추천 한 번에 카드가 5장이니 매
    # 요청마다 외부 확인이 5~10건 붙고 그만큼 응답이 늦어진다 — 2%를 잡자고 100%를
    # 느리게 만드는 거래다. 두 주소를 다 넘기고 실패한 카드에서만 프론트가 갈아탄다.
    fallback_thumbnail_url: str | None = None
    # 썸네일이 Google Places에서 온 경우에만 채워진다. 관광공사 이미지에는 없다.
    #
    # **이 값이 있으면 화면에 반드시 표시해야 한다.** Google 정책이 사진을 보여줄
    # 때 작성자 표기를 요구한다. 표시할 수 없으면 사진도 쓰면 안 되므로, Provider가
    # 출처 없는 사진을 애초에 주지 않는다.
    photo_attribution: PhotoAttribution | None = None


@dataclass(frozen=True)
class RecommendationCardResult:
    status: ToolStatus
    cards: tuple[RecommendationCard, ...]
    missing_content_ids: tuple[str, ...]
    error: ToolError | None = None


class RecommendationCardTool:
    """content_id 목록을 카드 표시 정보로 채운다."""

    def __init__(
        self,
        repository: PlaceDetailsReadRepository,
        registry: TourCategoryRegistry | None = None,
        google_photo_provider: GooglePlacePhotoProviderProtocol | None = None,
    ) -> None:
        self._repository = repository
        self._registry = registry or get_tour_category_registry()
        # None이면 보강을 건너뛴다. 기능이 꺼져 있거나 키가 없으면 factory가
        # None을 준다 — 이 클래스는 왜 꺼졌는지 알 필요가 없다.
        self._google_photo_provider = google_photo_provider

    async def get_cards(
        self, content_ids: Sequence[str]
    ) -> RecommendationCardResult:
        # 중복 id가 섞여도 조회는 한 번만 하고, 카드도 첫 등장 순서로 한 번만 낸다.
        ordered_ids = list(dict.fromkeys(content_ids))
        if not ordered_ids:
            return RecommendationCardResult(
                status=ToolStatus.NO_DATA,
                cards=(),
                missing_content_ids=(),
            )

        try:
            rows = await self._repository.get_active_place_details(ordered_ids)
        except AppError as exc:
            # 부르는 쪽은 이 실패를 상태로만 받고 추천은 그대로 내보낸다. 화면에는
            # 그 턴의 썸네일이 통째로 빠진 채 자리표시 칩만 남는데, 여기서 남기지
            # 않으면 "이 장소들에 사진이 없다"와 구분할 수 없다 — 같은 장소가
            # 어떤 요청에는 사진이 나오고 어떤 요청에는 안 나오는 이유가 이것이다.
            logger.warning(
                "추천 카드 조회 실패 — 이 턴의 썸네일이 전부 빠진다 "
                "(요청=%d건, code=%s, retryable=%s)",
                len(ordered_ids),
                exc.code,
                exc.retryable,
            )
            return RecommendationCardResult(
                status=ToolStatus.UNAVAILABLE,
                cards=(),
                missing_content_ids=tuple(ordered_ids),
                error=ToolError(
                    code="unavailable",
                    message="추천 카드 정보를 불러오지 못했습니다.",
                    cause="upstream_error",
                    retryable=exc.retryable,
                ),
            )

        cards = tuple(
            self._to_card(rows[content_id])
            for content_id in ordered_ids
            if content_id in rows
        )
        cards = await self._fill_missing_thumbnails(cards, rows)
        missing = tuple(
            content_id for content_id in ordered_ids if content_id not in rows
        )
        if missing:
            # 위 실패와 다른 사건이다. 조회는 됐는데 그 행이 없는 것이라
            # 비활성이거나 아직 동기화되지 않은 장소다. 어느 id인지 남겨야
            # 데이터를 채울 대상을 고를 수 있다.
            logger.info(
                "추천 카드 일부 없음 — 사진 없이 나간다 (요청=%d건, 없음=%d건, id=%s)",
                len(ordered_ids),
                len(missing),
                ",".join(missing[:10]),
            )
        return RecommendationCardResult(
            status=_status(requested=len(ordered_ids), found=len(cards)),
            cards=cards,
            missing_content_ids=missing,
        )

    async def _fill_missing_thumbnails(
        self,
        cards: tuple[RecommendationCard, ...],
        rows: dict[str, StoredPlaceDetail],
    ) -> tuple[RecommendationCard, ...]:
        """관광공사 이미지가 없는 카드만 Google 사진으로 채운다.

        보강 대상이 없으면 호출도 없다. 대상이 있어도 한 장소라도 실패하면 그
        카드만 원래대로(자리표시) 남고 나머지는 그대로 나간다 — Provider가
        예외 대신 None을 주기로 한 계약이 여기서 쓰인다.

        여러 장소를 동시에 부른다. 추천 한 번에 카드가 5장이고 그중 이미지가
        없는 곳이 여럿일 수 있는데, 순서대로 부르면 그 지연이 그대로 쌓인다.
        """
        provider = self._google_photo_provider
        if provider is None:
            return cards

        targets = [
            index
            for index, card in enumerate(cards)
            if card.thumbnail_url is None and card.name
        ]
        if not targets:
            return cards

        async def fetch(card: RecommendationCard) -> GooglePlaceCoverPhoto | None:
            row = rows.get(card.content_id)
            return await provider.find_cover_photo(
                name=card.name or "",
                address=row.address if row else None,
                latitude=card.latitude,
                longitude=card.longitude,
            )

        photos = await asyncio.gather(
            *(fetch(cards[index]) for index in targets),
            return_exceptions=True,
        )

        filled = list(cards)
        succeeded = 0
        for index, photo in zip(targets, photos, strict=True):
            if isinstance(photo, BaseException):
                # Provider는 None으로 답하기로 했으므로 여기 들어오면 계약이
                # 깨진 것이다. 추천은 그대로 내보내되 원인을 남긴다.
                logger.warning(
                    "Google 사진 보강이 예외로 끝났습니다: place_id=%s error=%s",
                    cards[index].content_id,
                    photo,
                )
                continue
            if photo is None:
                continue
            # 대안 주소는 두지 않는다. Google이 준 주소는 한 개뿐이고, 원래
            # 비어 있던 관광공사 주소를 대안으로 넣으면 죽은 주소를 다시 부른다.
            # 출처는 사진과 한 몸이다 — 둘 중 하나만 실리는 경로를 만들지 않는다.
            filled[index] = replace(
                cards[index],
                thumbnail_url=photo.url,
                photo_attribution=PhotoAttribution(
                    author_name=photo.author_name,
                    author_uri=photo.author_uri,
                    source_uri=photo.google_maps_uri,
                ),
            )
            succeeded += 1

        if targets:
            logger.info(
                "Google 사진 보강: 대상=%d건 성공=%d건",
                len(targets),
                succeeded,
            )
        return tuple(filled)

    def _to_card(self, row: StoredPlaceDetail) -> RecommendationCard:
        parking = normalize_parking(row.parking_info_raw)
        thumbnail_url = row.thumbnail_url or row.first_image_url
        # 이미 primary로 나간 주소는 대안이 아니다. `row.thumbnail_url`이 아니라
        # **정해진 primary와** 견줘야 한다 — thumbnail_url이 비어 first_image_url이
        # primary로 올라온 장소에서 둘이 갈리고, 그대로 두면 같은 404를 두 번 부른다.
        # 두 컬럼이 같은 파일을 가리키는 장소도 있다(실측).
        fallback_thumbnail_url = (
            row.first_image_url if row.first_image_url != thumbnail_url else None
        )
        return RecommendationCard(
            content_id=row.content_id,
            name=row.title,
            thumbnail_url=thumbnail_url,
            fallback_thumbnail_url=fallback_thumbnail_url,
            category_label=self._category_label(row),
            parking_status=parking.availability,
            parking_note=parking.note,
            # 적재 배치가 저장한 파싱 결과를 쓰되 파서 버전이 다르면 원문을 다시
            # 읽는다(supabase_place_details.py와 동일). TourAPI를 직접 부르는 경로와
            # 같은 함수를 거치므로 두 경로의 결과가 갈리지 않는다.
            operating_schedule=resolve_operating_schedule(
                content_type_id=row.content_type_id,
                operating_hours=row.operating_hours_raw,
                rest_date=row.rest_date_raw,
                stored=row.operating_schedule_raw,
                stored_parser_version=row.operating_parser_version,
            ),
            latitude=row.latitude,
            longitude=row.longitude,
        )

    def _category_label(self, row: StoredPlaceDetail) -> str | None:
        """중분류명을 카드 라벨로 쓴다.

        대분류(`쇼핑`, `음식`)는 카드에서 변별력이 없고 소분류(`사후면세점`,
        `관광식당`)는 지나치게 세분화돼 있어 중분류(`면세점`, `한식`)를 택했다.

        소분류 코드로 먼저 찾는다 — 실측 활성 844건의 소분류 코드 84종이 기준
        데이터에 모두 있어(2026-08-10 확인) 이 경로에서 전부 해석된다. 중분류
        코드 조회는 소분류가 비어 있는 행을 위한 대비책이다.
        """
        if row.lcls_systm3:
            category = self._registry.get_by_small_code(row.lcls_systm3)
            if category is not None:
                return category.lcls_systm2_name
        if row.lcls_systm2:
            matches = self._registry.find_by_middle_code(row.lcls_systm2)
            if matches:
                return matches[0].lcls_systm2_name
        return None


def _status(*, requested: int, found: int) -> ToolStatus:
    if found == 0:
        return ToolStatus.NO_DATA
    if found < requested:
        return ToolStatus.PARTIAL
    return ToolStatus.SUCCESS


__all__ = [
    "PhotoAttribution",
    "RecommendationCard",
    "RecommendationCardResult",
    "RecommendationCardTool",
]
