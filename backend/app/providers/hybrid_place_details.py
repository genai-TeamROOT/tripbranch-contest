"""places 캐시와 detailCommon2를 합쳐 장소 상세정보 1건을 만드는 Provider.

역할: INFO 상세 질의(`GetPlaceDetailTool`)가 쓰는 이름 기반 상세조회를, 외부 호출
1회로 끝낸다.

출처 분담:
  places 캐시 — 장소명·주소·운영시간·휴무일·주차·요금·안내처·편의시설
  detailCommon2 — overview·homepage (+ 축제의 tel)

detailCommon2가 실패하면 그 두 필드만 비우고 저장소 값으로 응답한다(PARTIAL).
이유는 `_common_details_or_empty()`에 적었다.

호출 수가 3회에서 1회로 준다. RealPlaceProvider.find_details_by_name()은
searchKeyword2로 이름을 맞추고(1) detailCommon2(2) + detailIntro2(3)를 부른다.
여기서는 이름 대조와 intro 값이 모두 저장소에 있어 detailCommon2만 남는다.

**D-054를 대체하는 경로다.** 그 결정은 "Supabase 캐시에는 INFO가 답할 데이터가
없다"가 전제였고, 당시 places의 동기화 대상은 operating_hours_raw/rest_date_raw
뿐이었다. D-056 이후 주차·요금이, D-060에서 안내처와 편의시설이 캐시에 들어와
question_type 전부를 덮게 됐다. overview/homepage만 detailCommon2에 남아 있어
그 1회를 부른다.
"""

from __future__ import annotations

import logging

from app.domain.models import PlaceCommonDetails, PlaceDetails, StoredPlaceDetail
from app.domain.operating_hours import resolve_operating_schedule
from app.errors import AppError
from app.providers.contracts import (
    ProviderResult,
    ProviderSource,
    ProviderStatus,
    provider_result,
)
from app.providers.protocols import PlaceCommonDetailsProvider
from app.repositories.protocols import (
    PlaceDetailsReadRepository,
    PlaceLocationRepository,
)

logger = logging.getLogger(__name__)

_PROVIDER_NAME = "hybrid_places"


class HybridPlaceDetailsProvider:
    """저장소에서 장소를 확정하고 detailCommon2로 서술 정보만 보탠다."""

    def __init__(
        self,
        location_repository: PlaceLocationRepository,
        details_repository: PlaceDetailsReadRepository,
        common_provider: PlaceCommonDetailsProvider,
    ) -> None:
        self._locations = location_repository
        self._details = details_repository
        self._common = common_provider

    async def find_details_by_name(
        self,
        name: str,
        region_code: str | None = None,
        district_code: str | None = None,
    ) -> ProviderResult[PlaceDetails]:
        """저장소에 정확히 일치하는 장소가 없으면 404를 던진다.

        region_code/district_code는 받기만 한다 — 저장소가 이미 종로구 한 지역만
        담고 있어 좁힐 대상이 없다. RealPlaceProvider와 시그니처를 맞춰 호출부가
        provider를 바꿔 끼울 수 있게 남긴다.
        """
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("name은 비어 있을 수 없습니다.")

        matches = await self._locations.find_active_places_by_name(normalized_name)
        if not matches:
            # RealPlaceProvider와 같은 코드·상태를 쓴다. GetPlaceDetailTool이 404를
            # no_data로 낮추므로 여기서 형태가 달라지면 장애로 잘못 보고된다.
            raise AppError(
                code="place_not_found",
                message=f"'{normalized_name}' 장소를 정확히 찾을 수 없어요.",
                status_code=404,
                details={"source": _PROVIDER_NAME},
            )

        content_id = matches[0].content_id
        # 무장애 정보를 함께 읽는다. INFO facility 질문("휠체어 들어갈 수 있어?")이
        # 이 경로로 오고, 저장소가 한 번의 조회로 함께 돌려준다(D-077).
        rows = await self._details.get_active_place_details(
            [content_id], include_barrier_free=True
        )
        row = rows.get(content_id)
        if row is None:
            # 이름 조회에는 걸렸는데 상세 행이 없다 — 두 조회 사이에 비활성화된
            # 경우다. 장애가 아니므로 같은 404 경로로 보낸다.
            raise AppError(
                code="place_not_found",
                message=f"'{normalized_name}' 장소를 정확히 찾을 수 없어요.",
                status_code=404,
                details={"source": _PROVIDER_NAME},
            )

        common, status = await self._common_details_or_empty(content_id)
        return provider_result(
            _to_place_details(row, common),
            source=ProviderSource.TOUR_API_PLACE,
            status=status,
            detail_fetched_at=row.detail_fetched_at,
        )

    async def _common_details_or_empty(
        self, content_id: str
    ) -> tuple[PlaceCommonDetails, ProviderStatus]:
        """detailCommon2가 실패해도 저장소 값만으로 상세를 낸다.

        detailCommon2가 실어 오는 값은 overview·homepage 둘뿐이고, 나머지(장소명·
        주소·운영시간·주차·요금·편의시설)는 이미 저장소 행에 있다. 그런데 이 호출
        하나가 실패해 예외가 올라가면 GetPlaceDetailTool이 UNAVAILABLE로 낮추고,
        routes/chat.py가 이미 만들어 둔 place_card를 응답에 싣지 않고 돌아간다 —
        일일 한도를 소진한 날 장소명·주소·사진까지 화면에서 통째로 사라지는 경로가
        이것이다. 받은 값은 내보내고 못 받은 두 필드만 비운다.

        **원인을 가리지 않고 삼킨다.** 일일 한도 소진이든 초당 한도든 타임아웃이든
        이 요청에서 할 수 있는 일이 같기 때문이다. 인증 실패·활용기간 만료 같은
        설정 문제는 부팅 검증(validate_provider_config)이 따로 잡는다. 대신 상태를
        PARTIAL로 낮추고 경고 로그를 남겨, 개요가 빠진 응답이 조용히 정상으로
        보이지 않게 한다.
        """
        try:
            result = await self._common.get_common_details(content_id)
        except AppError as exc:
            logger.warning(
                "detailCommon2 실패로 개요·홈페이지를 빼고 저장소 값만 응답합니다: "
                "place_id=%s code=%s",
                content_id,
                exc.code,
            )
            empty = PlaceCommonDetails(
                content_id=content_id,
                overview=None,
                homepage=None,
                telephone=None,
            )
            return empty, ProviderStatus.PARTIAL
        return result.data, ProviderStatus.SUCCESS


def _to_place_details(
    row: StoredPlaceDetail, common: PlaceCommonDetails
) -> PlaceDetails:
    return PlaceDetails(
        content_id=row.content_id,
        content_type_id=row.content_type_id,
        title=row.title,
        address=row.address,
        overview=common.overview,
        homepage=common.homepage,
        # 안내처가 먼저다. common의 tel은 축제(15)에만 채워지므로 대부분의 유형에서
        # 이 순서가 뒤바뀌어도 결과는 같지만, 축제는 저장소 쪽이 항상 비어 있어
        # 순서를 지켜야 tel이 살아난다.
        telephone=row.info_center_raw or common.telephone,
        operating_hours=row.operating_hours_raw,
        rest_date=row.rest_date_raw,
        raw_common={},
        # 저장소가 유형별 키를 한 컬럼으로 눌러 담아 원본 키를 복원할 수 없다.
        # 값은 아래 정규화 필드가 나르고, 소비 측도 raw_intro를 읽지 않는다(D-060).
        raw_intro={},
        provider=_PROVIDER_NAME,
        # 적재 배치가 저장한 파싱 결과를 쓰되 파서 버전이 다르면 원문을 다시
        # 읽는다(supabase_place_details.py와 동일).
        operating_schedule=resolve_operating_schedule(
            content_type_id=row.content_type_id,
            operating_hours=row.operating_hours_raw,
            rest_date=row.rest_date_raw,
            stored=row.operating_schedule_raw,
            stored_parser_version=row.operating_parser_version,
        ),
        parking=row.parking_info_raw,
        parking_fee=row.parking_fee_raw,
        fee=row.use_fee_raw,
        baby_carriage=row.baby_carriage_raw,
        pet=row.pet_raw,
        credit_card=row.credit_card_raw,
        restroom=row.restroom_raw,
        # 무장애 원문 15개는 합치거나 해석하지 않고 그대로 옮긴다. 어떤 값을 어떤
        # 계약 키로 낼지는 info_field_rules가 정한다 — provider가 미리 합치면
        # 원문 세 개(접근로·주출입구·승강기)를 잃어 되돌릴 수 없다.
        approach_route_raw=row.approach_route_raw,
        entrance_access_raw=row.entrance_access_raw,
        elevator_raw=row.elevator_raw,
        accessible_restroom_raw=row.accessible_restroom_raw,
        accessible_parking_raw=row.accessible_parking_raw,
        braille_block_raw=row.braille_block_raw,
        braille_promotion_raw=row.braille_promotion_raw,
        audio_guide_raw=row.audio_guide_raw,
        guide_dog_raw=row.guide_dog_raw,
        wheelchair_rental_raw=row.wheelchair_rental_raw,
        stroller_rental_raw=row.stroller_rental_raw,
        nursing_room_raw=row.nursing_room_raw,
        infant_family_etc_raw=row.infant_family_etc_raw,
        public_transport_raw=row.public_transport_raw,
        disability_etc_raw=row.disability_etc_raw,
        # INFO 상세 카드는 firstimage2(작은 썸네일, thumbnail_url)보다 firstimage
        # (원본 크기, first_image_url)를 우선한다 — 카드가 이미지를 크게 보여주는
        # 용도라 작은 썸네일을 확대하면 화질이 뭉개진다(2026-08-13 실사용 피드백).
        # first_image_url이 없는 20%가량의 장소만 thumbnail_url로 대체한다.
        thumbnail_url=row.first_image_url or row.thumbnail_url,
    )


__all__ = ["HybridPlaceDetailsProvider"]
