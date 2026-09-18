"""TourAPI 기반 좌표·키워드 검색과 장소 상세정보 Provider."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from app.domain.models import (
    PlaceCategoryFilter,
    PlaceCommonDetails,
    PlaceDetails,
    PlaceOperatingDetails,
    PlacePhoto,
    TourPlacePage,
    TourPlaceRecord,
)
from app.domain.operating_hours import normalize_operating_schedule
from app.errors import AppError, ProviderUnavailableError
from app.place_search_policy import (
    DEFAULT_PLACE_PROVIDER_RESULT_LIMIT,
    MAX_PLACE_PROVIDER_ROWS,
    MAX_PLACE_SEARCH_RADIUS_KM,
)
from app.providers.contracts import (
    ProviderResult,
    ProviderSource,
    ProviderStatus,
    provider_result,
)
from app.providers.mappers import map_tour_api_response
from app.providers.tour_api_client import (
    TourApiClient,
    first_item,
    first_text,
    response_body,
    response_items,
)
from app.providers.tour_intro_keys import (
    BABY_CARRIAGE_KEYS,
    CREDIT_CARD_KEYS,
    DISCOUNT_INFO_KEYS,
    INFO_CENTER_KEYS,
    OPERATING_HOURS_KEYS,
    PARKING_FEE_KEYS,
    PARKING_KEYS,
    PET_KEYS,
    REST_DATE_KEYS,
    RESTROOM_KEYS,
    USE_FEE_KEYS,
)
from app.schemas import PlaceCandidate
from app.service_area import SUPPORTED_DISTRICT_CODES

logger = logging.getLogger(__name__)

_BASE_URL = "https://apis.data.go.kr/B551011/KorService2"
_AREA_BASED_LIST_PATH = "/areaBasedList2"
_LOCATION_BASED_LIST_PATH = "/locationBasedList2"
_SEARCH_KEYWORD_PATH = "/searchKeyword2"
_DETAIL_COMMON_PATH = "/detailCommon2"
_DETAIL_INTRO_PATH = "/detailIntro2"
_DETAIL_IMAGE_PATH = "/detailImage2"

# 목록 조회 numOfRows 상한. TourAPI가 실제로 받아주는 값이다.
_MAX_LIST_ROWS = 1000
_TOUR_API_TIMEZONE = ZoneInfo("Asia/Seoul")


def _required_text(item: Mapping[str, object], key: str) -> str:
    value = item.get(key)
    if value is None or not str(value).strip():
        raise ProviderUnavailableError(
            "TourAPI", detail=f"areaBasedList2 item missing {key}"
        )
    return str(value).strip()


def _optional_float(item: Mapping[str, object], key: str) -> float | None:
    value = item.get(key)
    if value in (None, ""):
        return None
    try:
        return float(str(value))
    except ValueError:
        raise ProviderUnavailableError(
            "TourAPI", detail=f"areaBasedList2 item has invalid {key}"
        ) from None


def _optional_modified_at(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        return datetime.strptime(str(value), "%Y%m%d%H%M%S").replace(
            tzinfo=_TOUR_API_TIMEZONE
        )
    except ValueError:
        raise ProviderUnavailableError(
            "TourAPI", detail="areaBasedList2 item has invalid modifiedtime"
        ) from None


def _non_negative_int(value: object, field: str) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        raise ProviderUnavailableError(
            "TourAPI", detail=f"areaBasedList2 response has invalid {field}"
        ) from None
    if parsed < 0:
        raise ProviderUnavailableError(
            "TourAPI", detail=f"areaBasedList2 response has invalid {field}"
        )
    return parsed


def _map_area_place(
    item: Mapping[str, object],
    *,
    requested_area_code: str,
    requested_district_code: str,
) -> TourPlaceRecord:
    address_parts = (item.get("addr1"), item.get("addr2"))
    address = " ".join(str(part).strip() for part in address_parts if part).strip() or None
    return TourPlaceRecord(
        content_id=_required_text(item, "contentid"),
        content_type_id=_required_text(item, "contenttypeid"),
        title=_required_text(item, "title"),
        address=address,
        latitude=_optional_float(item, "mapy"),
        longitude=_optional_float(item, "mapx"),
        area_code=requested_area_code,
        district_code=requested_district_code,
        lcls_systm1=first_text(item, ("lclsSystm1",)),
        lcls_systm2=first_text(item, ("lclsSystm2",)),
        lcls_systm3=first_text(item, ("lclsSystm3",)),
        source_modified_at=_optional_modified_at(item.get("modifiedtime")),
        first_image_url=first_text(item, ("firstimage",)),
        thumbnail_url=first_text(item, ("firstimage2",)),
    )


def _to_place_photos(
    items: tuple[Mapping[str, object], ...], content_id: str
) -> tuple[PlacePhoto, ...]:
    """detailImage2 항목을 사진 값으로 옮긴다. 주소가 없는 항목은 건너뛴다.

    **photo_order는 응답이 온 순서다.** `serialnum`은 관광공사의 사진 식별자라
    장소 안의 순번이 아니고, 값이 비어 있는 항목도 있다. 적재분
    (place_image_embeddings)도 같은 규칙으로 번호를 매겼기 때문에 두 출처의
    순서가 같은 뜻을 갖는다.

    `originimgurl`은 원본, `smallimageurl`은 축소본이다. 화면이 크게 쓰므로
    원본을 택하고, 없는 항목은 뺀다 — 축소본으로 대체하면 같은 갤러리 안에서
    화질이 들쭉날쭉해진다.
    """
    photos: list[PlacePhoto] = []
    for item in items:
        url = first_text(item, ("originimgurl",))
        if not url:
            continue
        photos.append(
            PlacePhoto(
                content_id=content_id,
                photo_order=len(photos) + 1,
                url=url,
                image_name=first_text(item, ("imgname",)),
            )
        )
    return tuple(photos)


class RealPlaceProvider:
    def __init__(
        self,
        api_key: str,
        client: httpx.AsyncClient,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._api_key = api_key
        self._client = client
        self._timeout_seconds = timeout_seconds
        self._api = TourApiClient(
            api_key,
            client,
            base_url=_BASE_URL,
            timeout_seconds=timeout_seconds,
        )

    def _base_params(self) -> dict[str, object]:
        return self._api.base_params()

    async def _request_json(
        self, path: str, params: dict[str, object]
    ) -> dict[str, object]:
        return await self._api.request_json(path, params)

    async def search_places(
        self,
        latitude: float,
        longitude: float,
        preferred_categories: list[str],
        search_radius_km: float,
        region_code: str | None = None,
        district_code: str | None = None,
        category_filter: PlaceCategoryFilter | None = None,
        limit: int = DEFAULT_PLACE_PROVIDER_RESULT_LIMIT,
    ) -> ProviderResult[list[PlaceCandidate]]:
        radius_m = min(
            int(search_radius_km * 1000),
            int(MAX_PLACE_SEARCH_RADIUS_KM * 1000),
        )
        params = {
            **self._base_params(),
            "mapX": longitude,
            "mapY": latitude,
            "radius": radius_m,
            "arrange": "E",
            "numOfRows": max(1, min(limit, MAX_PLACE_PROVIDER_ROWS)),
            "pageNo": 1,
        }
        if district_code and not region_code:
            raise ValueError("district_code 사용 시 region_code가 필요합니다.")
        if region_code:
            params["lDongRegnCd"] = region_code
        # district_code를 요청에 실으면 그 구 밖의 후보가 반경 안에 있어도 잘린다.
        # 지원 구가 여럿이면 구마다 호출해야 해서 호출 수가 구 수만큼 늘어난다.
        # 그래서 요청은 시도까지만 좁히고, 지원 구 판정은 응답으로 한다(D-025).
        if district_code:
            params["lDongSignguCd"] = district_code
        if category_filter is not None:
            optional_filters = {
                "contentTypeId": category_filter.content_type_id,
                "lclsSystm1": category_filter.lcls_systm1,
                "lclsSystm2": category_filter.lcls_systm2,
                "lclsSystm3": category_filter.lcls_systm3,
            }
            params.update(
                {
                    key: value
                    for key, value in optional_filters.items()
                    if value is not None
                }
            )
        payload = await self._request_json(_LOCATION_BASED_LIST_PATH, params)
        candidates = map_tour_api_response(
            payload, allowed_district_codes=SUPPORTED_DISTRICT_CODES
        )
        return provider_result(
            candidates,
            source=ProviderSource.TOUR_API_PLACE,
            status=ProviderStatus.SUCCESS if candidates else ProviderStatus.NO_DATA,
        )

    async def list_places_by_area(
        self,
        area_code: str,
        district_code: str,
        page_no: int,
        num_of_rows: int = 100,
    ) -> TourPlacePage:
        normalized_area_code = area_code.strip()
        normalized_district_code = district_code.strip()
        if not normalized_area_code or not normalized_district_code:
            raise ValueError("area_code와 district_code가 필요합니다.")
        if page_no < 1:
            raise ValueError("page_no는 1 이상이어야 합니다.")
        # TourAPI 목록 조회는 numOfRows 1000까지 그대로 반환한다(2026-08-08 확인).
        # 상한을 100으로 두면 종로구 전량 스냅샷에 9회가 필요한데, areaBasedList2는
        # 오퍼레이션 단위 일일 한도가 있어 호출 수를 줄일 수 있어야 한다.
        if not 1 <= num_of_rows <= _MAX_LIST_ROWS:
            raise ValueError(f"num_of_rows는 1 이상 {_MAX_LIST_ROWS} 이하여야 합니다.")

        payload = await self._request_json(
            _AREA_BASED_LIST_PATH,
            {
                **self._base_params(),
                "lDongRegnCd": normalized_area_code,
                "lDongSignguCd": normalized_district_code,
                "arrange": "A",
                "pageNo": page_no,
                "numOfRows": num_of_rows,
            },
        )
        body = response_body(payload)
        return TourPlacePage(
            page_no=_non_negative_int(body.get("pageNo"), "pageNo"),
            num_of_rows=_non_negative_int(body.get("numOfRows"), "numOfRows"),
            total_count=_non_negative_int(body.get("totalCount"), "totalCount"),
            places=tuple(
                _map_area_place(
                    item,
                    requested_area_code=normalized_area_code,
                    requested_district_code=normalized_district_code,
                )
                for item in response_items(payload)
            ),
        )

    async def get_operating_details(
        self,
        content_id: str,
        content_type_id: str,
    ) -> PlaceOperatingDetails:
        normalized_content_id = content_id.strip()
        normalized_content_type_id = content_type_id.strip()
        if not normalized_content_id or not normalized_content_type_id:
            raise ValueError("content_id와 content_type_id가 필요합니다.")

        payload = await self._request_json(
            _DETAIL_INTRO_PATH,
            {
                **self._base_params(),
                "contentId": normalized_content_id,
                "contentTypeId": normalized_content_type_id,
            },
        )
        intro = first_item(payload)
        return PlaceOperatingDetails(
            content_id=normalized_content_id,
            content_type_id=normalized_content_type_id,
            operating_hours_raw=first_text(intro, OPERATING_HOURS_KEYS),
            rest_date_raw=first_text(intro, REST_DATE_KEYS),
            parking_info_raw=first_text(intro, PARKING_KEYS),
            parking_fee_raw=first_text(intro, PARKING_FEE_KEYS),
            use_fee_raw=first_text(intro, USE_FEE_KEYS),
            discount_info_raw=first_text(intro, DISCOUNT_INFO_KEYS),
            info_center_raw=first_text(intro, INFO_CENTER_KEYS),
            baby_carriage_raw=first_text(intro, BABY_CARRIAGE_KEYS),
            pet_raw=first_text(intro, PET_KEYS),
            credit_card_raw=first_text(intro, CREDIT_CARD_KEYS),
            restroom_raw=first_text(intro, RESTROOM_KEYS),
        )

    async def search_by_keyword(
        self,
        keyword: str,
        region_code: str | None = None,
        district_code: str | None = None,
        limit: int = DEFAULT_PLACE_PROVIDER_RESULT_LIMIT,
    ) -> ProviderResult[list[PlaceCandidate]]:
        normalized_keyword = keyword.strip()
        if not normalized_keyword:
            raise ValueError("keyword는 비어 있을 수 없습니다.")
        if district_code and not region_code:
            raise ValueError("district_code 사용 시 region_code가 필요합니다.")

        params = {
            **self._base_params(),
            "keyword": normalized_keyword,
            "arrange": "A",
            "numOfRows": max(1, min(limit, MAX_PLACE_PROVIDER_ROWS)),
            "pageNo": 1,
        }
        if region_code:
            params["lDongRegnCd"] = region_code
        if district_code:
            params["lDongSignguCd"] = district_code

        payload = await self._request_json(_SEARCH_KEYWORD_PATH, params)
        candidates = map_tour_api_response(
            payload, allowed_district_codes=SUPPORTED_DISTRICT_CODES
        )
        return provider_result(
            candidates,
            source=ProviderSource.TOUR_API_PLACE,
            status=ProviderStatus.SUCCESS if candidates else ProviderStatus.NO_DATA,
        )

    async def get_common_details(
        self, content_id: str
    ) -> ProviderResult[PlaceCommonDetails]:
        """detailCommon2만 호출한다. contentTypeId가 필요 없는 유일한 상세 엔드포인트다.

        운영시간·주차·요금은 detailIntro2 쪽이라 여기서 얻을 수 없다. places 캐시가
        그 값을 이미 들고 있는 경로(HybridPlaceDetailsProvider)가 호출 1회로 나머지를
        채우려고 쓴다.
        """
        normalized_content_id = content_id.strip()
        if not normalized_content_id:
            raise ValueError("content_id가 필요합니다.")

        payload = await self._request_json(
            _DETAIL_COMMON_PATH,
            {**self._base_params(), "contentId": normalized_content_id},
        )
        common = first_item(payload)
        details = PlaceCommonDetails(
            content_id=normalized_content_id,
            overview=first_text(common, ("overview",)),
            homepage=first_text(common, ("homepage",)),
            telephone=first_text(common, ("tel",)),
        )
        has_data = any((details.overview, details.homepage, details.telephone))
        return provider_result(
            details,
            source=ProviderSource.TOUR_API_PLACE,
            status=ProviderStatus.SUCCESS if has_data else ProviderStatus.NO_DATA,
        )

    async def get_place_images(
        self, content_id: str, limit: int
    ) -> ProviderResult[tuple[PlacePhoto, ...]]:
        """detailImage2로 장소 사진 목록을 받는다.

        상세 화면이 여러 장을 보여주는 데 쓴다. `places` 캐시가 나르는 대표 이미지
        (firstimage)와 다른 것으로, 관광공사가 장소마다 따로 등록해 둔 사진들이다.

        **오퍼레이션 단위로 일일 1,000회 한도가 걸려 있다.** 소진 판정과 호출을
        멈추는 일은 이 provider가 아니라 호출부(HybridPlacePhotoProvider)가 한다 —
        여기서 멈추면 적재 스크립트처럼 다른 정책이 필요한 호출부까지 같은 규칙에
        묶인다.

        `imageYN=Y`는 "장소 사진"을 뜻한다. N으로 주면 음식점 메뉴판 같은 다른
        분류가 돌아온다.

        몇 장까지 받을지는 호출부가 정한다. 화면에 몇 장을 쓸지는 표시 정책이고
        이 provider가 알 일이 아니다.
        """
        normalized_content_id = content_id.strip()
        if not normalized_content_id:
            raise ValueError("content_id가 필요합니다.")
        if limit < 1:
            raise ValueError("limit은 1 이상이어야 합니다.")

        payload = await self._request_json(
            _DETAIL_IMAGE_PATH,
            {
                **self._base_params(),
                "contentId": normalized_content_id,
                "imageYN": "Y",
                "numOfRows": limit,
                "pageNo": 1,
            },
        )
        photos = _to_place_photos(response_items(payload), normalized_content_id)
        return provider_result(
            photos,
            source=ProviderSource.TOUR_API_PLACE,
            status=ProviderStatus.SUCCESS if photos else ProviderStatus.NO_DATA,
        )

    async def get_details(
        self, content_id: str, content_type_id: str
    ) -> ProviderResult[PlaceDetails]:
        if not content_id or not content_type_id:
            raise ValueError("content_id와 content_type_id가 필요합니다.")

        common_payload = await self._request_json(
            _DETAIL_COMMON_PATH,
            {**self._base_params(), "contentId": content_id},
        )
        intro_payload = await self._request_json(
            _DETAIL_INTRO_PATH,
            {
                **self._base_params(),
                "contentId": content_id,
                "contentTypeId": content_type_id,
            },
        )
        common = first_item(common_payload)
        intro = first_item(intro_payload)
        address_parts = [common.get("addr1"), common.get("addr2")]
        address = " ".join(str(part) for part in address_parts if part) or None

        operating_hours = first_text(intro, OPERATING_HOURS_KEYS)
        rest_date = first_text(intro, REST_DATE_KEYS)
        details = PlaceDetails(
            content_id=content_id,
            content_type_id=content_type_id,
            title=first_text(common, ("title",)),
            address=address,
            overview=first_text(common, ("overview",)),
            homepage=first_text(common, ("homepage",)),
            # common의 tel은 축제(15)에만 채워진다. 나머지 유형은 intro의 안내처가
            # 실제 출처라 유형별 키를 모두 훑는다 — 예전에는 `infocenter` 하나만 봐서
            # 문화시설·숙박·쇼핑·음식점의 전화번호가 누락됐다.
            telephone=(
                first_text(common, ("tel",)) or first_text(intro, INFO_CENTER_KEYS)
            ),
            # 주차·요금도 유형별 키를 여기서 정규화한다. 소비 측이 raw_intro에서
            # 원본 키를 다시 찾지 않도록 동기화 경로와 같은 상수를 쓴다.
            parking=first_text(intro, PARKING_KEYS),
            parking_fee=first_text(intro, PARKING_FEE_KEYS),
            fee=first_text(intro, USE_FEE_KEYS),
            baby_carriage=first_text(intro, BABY_CARRIAGE_KEYS),
            pet=first_text(intro, PET_KEYS),
            credit_card=first_text(intro, CREDIT_CARD_KEYS),
            restroom=first_text(intro, RESTROOM_KEYS),
            # 목록 동기화가 places에 담는 값과 같은 키다(firstimage2 → firstimage).
            thumbnail_url=first_text(common, ("firstimage2", "firstimage")),
            operating_hours=operating_hours,
            rest_date=rest_date,
            raw_common=common,
            raw_intro=intro,
            provider="tour_api",
            operating_schedule=normalize_operating_schedule(
                content_type_id=content_type_id,
                operating_hours=operating_hours,
                rest_date=rest_date,
            ),
        )
        has_data = any(
            (
                details.title,
                details.address,
                details.overview,
                details.homepage,
                details.telephone,
                details.operating_hours,
                details.rest_date,
                details.raw_common,
                details.raw_intro,
            )
        )
        return provider_result(
            details,
            source=ProviderSource.TOUR_API_PLACE,
            status=ProviderStatus.SUCCESS if has_data else ProviderStatus.NO_DATA,
        )

    async def find_details_by_name(
        self,
        name: str,
        region_code: str | None = None,
        district_code: str | None = None,
    ) -> ProviderResult[PlaceDetails]:
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("name은 비어 있을 수 없습니다.")

        candidates = (await self.search_by_keyword(
            normalized_name,
            region_code=region_code,
            district_code=district_code,
            limit=100,
        )).data
        exact = next(
            (
                candidate
                for candidate in candidates
                if candidate.name.strip().casefold() == normalized_name.casefold()
            ),
            None,
        )
        if exact is None:
            raise AppError(
                code="place_not_found",
                message=f"'{normalized_name}' 장소를 정확히 찾을 수 없어요.",
                status_code=404,
                details={"candidate_names": [item.name for item in candidates[:5]]},
            )
        if not exact.content_type_id:
            raise ProviderUnavailableError(
                "TourAPI", detail="matched place has no contentTypeId"
            )
        return await self.get_details(exact.place_id, exact.content_type_id)
