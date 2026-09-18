"""INFO 응답의 장소 카드(PlaceCard) 계약 테스트.

카드는 질문 유형과 무관하게 채우고 status 판정에는 관여하지 않는다. 두 값이 섞이면
"주차 정보는 없어요" 같은 안내의 근거가 사라진다.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.agent_context.info_schemas import (
    InfoContextRequest,
    InfoContextResponse,
    InfoQuestionType,
    PlaceInfoResult,
)
from app.agent_context.service import ContextService, ContextTools
from app.domain.models import PlaceDetails, PlacePhoto, StoredPlaceLocation
from app.errors import ProviderUnavailableError
from app.providers.concentration import FakeConcentrationProvider
from app.providers.contracts import (
    ProviderResult,
    ProviderSource,
    ProviderStatus,
    provider_result,
)
from app.providers.geocoding import FakeGeocodingProvider
from app.providers.holiday import FakeHolidayProvider
from app.providers.hybrid_place_details import HybridPlaceDetailsProvider
from app.providers.stub import FakePlaceProvider, FakeWeatherProvider
from app.repositories.fake_places import (
    FakePlaceDetailsRepository,
    FakePlaceLocationRepository,
    FakePlacePhotoRepository,
)
from app.tools.concentration import GetConcentrationTool
from app.tools.holiday import GetHolidaysTool
from app.tools.nearby_place_details import NearbyPlaceDetailsTool
from app.tools.place_detail import GetPlaceDetailTool
from app.tools.resolve_location import ResolveLocationTool
from app.tools.weather_forecast import GetWeatherForecastTool

KST = ZoneInfo("Asia/Seoul")


def _details(**overrides: object) -> PlaceDetails:
    base: dict[str, object] = {
        "content_id": "126508",
        "content_type_id": "12",
        "title": "경복궁",
        "address": "서울특별시 종로구 사직로 161",
        "overview": None,
        "homepage": None,
        "telephone": None,
        "operating_hours": None,
        "rest_date": None,
        "raw_common": {},
        "raw_intro": {},
        "provider": "hybrid_places",
        "thumbnail_url": "https://example.test/thumb.jpg",
    }
    base.update(overrides)
    return PlaceDetails(**base)  # type: ignore[arg-type]


class _Provider(FakePlaceProvider):
    def __init__(self, details: PlaceDetails) -> None:
        self._details = details

    async def find_details_by_name(
        self,
        name: str,
        region_code: str | None = None,
        district_code: str | None = None,
    ) -> ProviderResult[PlaceDetails]:
        return provider_result(
            self._details,
            source=ProviderSource.TOUR_API_PLACE,
            status=ProviderStatus.SUCCESS,
        )


def _photo(order: int) -> PlacePhoto:
    return PlacePhoto(
        content_id="126508",
        photo_order=order,
        url=f"https://tong.visitkorea.or.kr/126508-{order}.jpg",
        image_name=f"경복궁 ({order})",
    )


class _FailingPhotoRepository:
    """사진 조회가 실패하는 저장소. 상세 정보 전체를 잃지 않는지 보기 위한 것이다."""

    async def find_place_photos(self, content_ids):  # noqa: ANN001, ANN201
        raise RuntimeError("supabase unreachable")


def _service(details: PlaceDetails, photos: object | None = None) -> ContextService:
    search_provider = FakePlaceProvider()
    return ContextService(
        ContextTools(
            location=ResolveLocationTool(
                FakeGeocodingProvider(),
                place_repository=FakePlaceLocationRepository(),
            ),
            places=NearbyPlaceDetailsTool(search_provider, search_provider),
            weather=GetWeatherForecastTool(FakeWeatherProvider()),
            holidays=GetHolidaysTool(FakeHolidayProvider()),
            concentration=GetConcentrationTool(FakeConcentrationProvider()),
            place_detail=GetPlaceDetailTool(_Provider(details)),
            place_photos=photos,
        ),
        candidate_limit=10,
        clock=lambda: datetime.now(KST),
    )


def _request(question_type: InfoQuestionType) -> InfoContextRequest:
    return InfoContextRequest(
        request_id="request-info-card-1",
        place_name="경복궁",
        place_context="explicit",
        question_type=question_type,
    )


def _result(response: InfoContextResponse) -> PlaceInfoResult:
    assert isinstance(response.result, PlaceInfoResult)
    return response.result


@pytest.mark.asyncio
async def test_질문_유형과_무관하게_전체를_채운다() -> None:
    """주차를 물어도 카드에는 운영시간·요금·개요가 함께 담긴다."""
    service = _service(
        _details(
            parking="가능 (240대)",
            fee="어른 3,000원",
            overview="조선왕조 제일의 법궁이다.",
            operating_hours="09:00~18:00",
        )
    )

    response = await service.fetch_info_context(_request("parking"))

    card = _result(response).place_card
    assert card is not None
    assert card.parking == "가능 (240대)"
    assert card.fee == "어른 3,000원"
    assert card.overview == "조선왕조 제일의 법궁이다."
    assert card.operating_hours == "09:00~18:00"
    assert card.place_name == "경복궁"


@pytest.mark.asyncio
async def test_카드가_차도_no_data_판정은_그대로다() -> None:
    """이 테스트가 이 설계의 전부다.

    카드를 fields에 합치면 overview가 거의 항상 있어 fields가 비지 않고,
    "경복궁 요금 정보는 없어요"가 영영 나오지 않는다.

    질문 유형이 parking이 아니라 fee인 이유는 D-099다 — 주차는 상세정보에
    parking/parking_fee가 없으면 주변 공영주차장 현황으로 대체하므로
    PlaceInfoResult로 돌아오지 않는다(그 경로는
    test_info_realtime_commercial.py가 본다). 여기서 보려는 것은 대체 경로가
    없는 질문 유형에서 카드와 fields가 서로 다른 값으로 남는지다.
    """
    service = _service(
        _details(overview="조선왕조 제일의 법궁이다.", operating_hours="09:00~18:00")
    )

    response = await service.fetch_info_context(_request("fee"))

    result = _result(response)
    # 물어본 요금 정보가 없으므로 no_data다.
    assert response.status == "no_data"
    assert result.status == "no_data"
    assert result.fields == {}
    # 그래도 카드는 채워져 펼칠 수 있다.
    assert result.place_card is not None
    assert result.place_card.overview == "조선왕조 제일의 법궁이다."


@pytest.mark.asyncio
async def test_fields와_카드가_같은_정제_결과를_쓴다() -> None:
    """HTML 정리가 두 곳에서 갈리면 같은 값이 다르게 보인다."""
    service = _service(_details(parking="가능<br>요금 (30분 1,500원)"))

    response = await service.fetch_info_context(_request("parking"))

    result = _result(response)
    assert result.fields["parking"] == "가능 요금 (30분 1,500원)"
    assert result.place_card is not None
    assert result.place_card.parking == result.fields["parking"]


@pytest.mark.asyncio
async def test_없는_값은_None으로_두고_문구를_지어내지_않는다() -> None:
    service = _service(_details(parking="가능"))

    response = await service.fetch_info_context(_request("parking"))

    card = _result(response).place_card
    assert card is not None
    assert card.overview is None
    assert card.homepage is None
    assert card.restroom is None


@pytest.mark.asyncio
async def test_편의시설은_네_항목으로_따로_담는다() -> None:
    """하나로 합치면 어느 항목이 빠졌는지 구분되지 않는다."""
    service = _service(
        _details(baby_carriage="없음", credit_card="가능", restroom="있음")
    )

    response = await service.fetch_info_context(_request("facility"))

    card = _result(response).place_card
    assert card is not None
    assert card.baby_carriage == "없음"
    assert card.credit_card == "가능"
    assert card.restroom == "있음"
    # 값이 없는 항목은 None이며, "없다고 답한" 없음과 구분된다.
    assert card.pet is None


@pytest.mark.asyncio
async def test_썸네일이_없으면_None이다() -> None:
    """실측 844건 중 169건(20%)이 여기 해당한다. 소비 측이 이미지 영역을 숨긴다."""
    service = _service(_details(parking="가능", thumbnail_url=None))

    response = await service.fetch_info_context(_request("parking"))

    card = _result(response).place_card
    assert card is not None
    assert card.thumbnail_url is None


@pytest.mark.asyncio
async def test_상세조회를_하지_않는_경로는_카드가_없다() -> None:
    """location_info는 상세 조회 전에 응답한다 — 카드를 지어내지 않는다."""
    service = _service(_details(parking="가능"))

    response = await service.fetch_info_context(_request("location_info"))

    assert _result(response).place_card is None


@pytest.mark.asyncio
async def test_사진이_여러_장이면_순서대로_카드에_담긴다() -> None:
    """첫 번째가 대표 사진이다 — 저장소가 준 순서를 바꾸지 않는다."""
    service = _service(
        _details(parking="가능"),
        FakePlacePhotoRepository({"126508": (_photo(1), _photo(2), _photo(3))}),
    )

    response = await service.fetch_info_context(_request("parking"))

    card = _result(response).place_card
    assert card is not None
    assert [photo.url for photo in card.photos] == [
        "https://tong.visitkorea.or.kr/126508-1.jpg",
        "https://tong.visitkorea.or.kr/126508-2.jpg",
        "https://tong.visitkorea.or.kr/126508-3.jpg",
    ]
    assert card.photos[0].image_name == "경복궁 (1)"


@pytest.mark.asyncio
async def test_사진이_없어도_대표_이미지는_그대로_남는다() -> None:
    """사진 목록이 비는 장소가 대부분이다. 목록만 보고 그리면 보이던 사진이 사라진다."""
    service = _service(_details(parking="가능"), FakePlacePhotoRepository({}))

    response = await service.fetch_info_context(_request("parking"))

    card = _result(response).place_card
    assert card is not None
    assert card.photos == []
    assert card.thumbnail_url == "https://example.test/thumb.jpg"


@pytest.mark.asyncio
async def test_사진_저장소가_없으면_기존_경로_그대로다() -> None:
    """저장소를 안 주는 호출부(기존 테스트 포함)가 그대로 돌아야 한다."""
    service = _service(_details(parking="가능"))

    response = await service.fetch_info_context(_request("parking"))

    card = _result(response).place_card
    assert card is not None
    assert card.photos == []


@pytest.mark.asyncio
async def test_사진_조회가_실패해도_상세_정보는_나온다() -> None:
    """사진이 안 보이는 것과 상세 정보가 통째로 없는 것은 무게가 다르다."""
    service = _service(_details(parking="가능 (240대)"), _FailingPhotoRepository())

    response = await service.fetch_info_context(_request("parking"))

    result = _result(response)
    assert response.status == "success"
    assert result.fields["parking"] == "가능 (240대)"
    assert result.place_card is not None
    assert result.place_card.photos == []


class TestCardBarrierFree:
    """상세 카드의 무장애 구획(D-077).

    카드는 답변(fields)과 목적이 달라 항목 구성도 다르다 — 접근로·주출입구는
    단차 서술이라 카드에서 빼고, 흩어져 있는 값 셋(시각 안내·수유/기저귀·좌석)은
    한 줄로 합친다. 답변 경로의 wheelchair_access는 그대로 둔다.
    """

    @pytest.mark.asyncio
    async def test_아홉_항목을_채운다(self) -> None:
        service = _service(
            _details(
                accessible_restroom_raw="장애인 화장실 있음(1층)",
                accessible_parking_raw="장애인 주차구역 2면",
                elevator_raw="엘리베이터 있음",
                braille_block_raw="점자블록 있음",
                wheelchair_rental_raw="대여가능(2대, 안내데스크)",
                nursing_room_raw="수유실 있음(2층)",
                disability_etc_raw="의자식 테이블 있음",
                stroller_rental_raw="대여가능(10대)",
                guide_dog_raw="보조견 동반 가능함",
            )
        )

        response = await service.fetch_info_context(_request("general_info"))

        card = _result(response).place_card
        assert card is not None
        assert card.accessible_restroom == "장애인 화장실 있음(1층)"
        assert card.accessible_parking == "장애인 주차구역 2면"
        assert card.elevator == "엘리베이터 있음"
        assert card.visual_guide == "점자블록 있음"
        assert card.wheelchair_rental == "대여가능(2대, 안내데스크)"
        assert card.nursing_room == "수유실 있음(2층)"
        assert card.seating == "의자식 테이블 있음"
        assert card.stroller_rental == "대여가능(10대)"
        assert card.guide_dog == "보조견 동반 가능함"

    @pytest.mark.asyncio
    async def test_무장애_정보가_없으면_전부_None이다(self) -> None:
        """전체 8,060곳 중 무장애 원문이 있는 곳은 1,229곳(15%)뿐이다.

        빈 값을 "없음"으로 채우면 안 된다 — 이 데이터는 있으면 적고 없으면
        비우는 식이라, 없다고 답한 값은 장애인 화장실 4건뿐이다.
        """
        service = _service(_details(operating_hours="09:00~18:00"))

        response = await service.fetch_info_context(_request("general_info"))

        card = _result(response).place_card
        assert card is not None
        assert card.accessible_restroom is None
        assert card.elevator is None
        assert card.visual_guide is None
        assert card.nursing_room is None
        assert card.seating is None
        assert card.guide_dog is None

    @pytest.mark.asyncio
    async def test_시각_안내_셋을_한_줄로_잇는다(self) -> None:
        """개별 채움률이 6~17%라 따로 두면 세 줄 중 두 줄이 늘 빈다."""
        service = _service(
            _details(
                braille_block_raw="점자블록 있음",
                braille_promotion_raw="점자 안내책자 있음",
                audio_guide_raw="음성안내기 대여 가능",
            )
        )

        response = await service.fetch_info_context(_request("general_info"))

        card = _result(response).place_card
        assert card is not None
        assert card.visual_guide == "점자블록 있음 / 점자 안내책자 있음 / 음성안내기 대여 가능"

    @pytest.mark.asyncio
    async def test_기저귀교환대는_수유실_줄에_함께_낸다(self) -> None:
        """기저귀 원문이 수유실이 아니라 영유아·가족 편의 필드에 들어 있다.

        70건이 이 필드에서만 기저귀를 말하고 그중 48건은 수유실 값이 없다 —
        수유실만 보면 그 48곳에서 기저귀 갈 곳이 사라진다.
        """
        service = _service(_details(infant_family_etc_raw="기저귀교환대 있음"))

        response = await service.fetch_info_context(_request("general_info"))

        card = _result(response).place_card
        assert card is not None
        assert card.nursing_room == "기저귀교환대 있음"

    @pytest.mark.asyncio
    async def test_기저귀를_말하지_않는_영유아_값은_빼고_수유실만_낸다(self) -> None:
        """"수유·기저귀" 줄에 유아용 식기가 붙으면 라벨과 값이 어긋난다."""
        service = _service(
            _details(
                nursing_room_raw="수유실 있음(2층)",
                infant_family_etc_raw="유아용식기 있음",
            )
        )

        response = await service.fetch_info_context(_request("general_info"))

        card = _result(response).place_card
        assert card is not None
        assert card.nursing_room == "수유실 있음(2층)"

    @pytest.mark.asyncio
    async def test_좌석을_말하지_않는_기타_값은_카드에_싣지_않는다(self) -> None:
        """장애인 편의 기타에는 단차 서술이 섞여 있다(231건 중 19건).

        통째로 실으면 카드에서 빼기로 한 축이 되돌아온다.
        """
        service = _service(
            _details(disability_etc_raw="공연장까지 이동하는 경로에 2~3단 정도의 계단이 있음")
        )

        response = await service.fetch_info_context(_request("general_info"))

        card = _result(response).place_card
        assert card is not None
        assert card.seating is None

    @pytest.mark.asyncio
    async def test_유모차는_무장애_값이_기존_값을_대체한다(self) -> None:
        """둘 다 있는 34곳 중 21곳(62%)에서 두 원문이 서로 반대다.

        서울공예박물관은 detailIntro2가 "없음", 무장애가 "대여가능(10대)"이라
        함께 내면 카드에 모순된 두 줄이 나란히 보인다.
        """
        service = _service(
            _details(baby_carriage="없음", stroller_rental_raw="대여가능(10대)")
        )

        response = await service.fetch_info_context(_request("general_info"))

        card = _result(response).place_card
        assert card is not None
        assert card.stroller_rental == "대여가능(10대)"
        assert card.baby_carriage is None

    @pytest.mark.asyncio
    async def test_무장애_값이_없으면_기존_유모차_값이_남는다(self) -> None:
        """무장애 정보가 없는 장소가 대부분이라 이 경로가 기본이다."""
        service = _service(_details(baby_carriage="가능"))

        response = await service.fetch_info_context(_request("general_info"))

        card = _result(response).place_card
        assert card is not None
        assert card.baby_carriage == "가능"
        assert card.stroller_rental is None


class _FailingCommonProvider:
    """detailCommon2만 죽은 상태. 일일 한도를 소진한 날이 이 모양이다."""

    async def get_common_details(self, content_id: str):  # noqa: ANN201
        raise ProviderUnavailableError(
            "TourAPI", detail="returnReasonCode=22, errMsg=SERVICE ERROR"
        )


def _service_with_failing_common() -> ContextService:
    """저장소와 사진은 살아 있고 detailCommon2만 실패하는 상태를 실 provider로 조립한다.

    여기서 fake로 두는 것은 저장소와 detailCommon2뿐이고, 상세 조립은
    HybridPlaceDetailsProvider가 그대로 한다 — 사진이 응답까지 실제로 실리는지
    보려면 그 경로가 통째로 돌아야 한다.
    """
    locations = FakePlaceLocationRepository(
        (
            StoredPlaceLocation(
                content_id="fake-museum-1",
                title="테스트 박물관",
                address="서울 종로구 어딘가",
                latitude=37.5735,
                longitude=126.9788,
                district_code="110",
            ),
        )
    )
    search_provider = FakePlaceProvider()
    return ContextService(
        ContextTools(
            location=ResolveLocationTool(
                FakeGeocodingProvider(),
                place_repository=locations,
            ),
            places=NearbyPlaceDetailsTool(search_provider, search_provider),
            weather=GetWeatherForecastTool(FakeWeatherProvider()),
            holidays=GetHolidaysTool(FakeHolidayProvider()),
            concentration=GetConcentrationTool(FakeConcentrationProvider()),
            place_detail=GetPlaceDetailTool(
                HybridPlaceDetailsProvider(
                    location_repository=locations,
                    details_repository=FakePlaceDetailsRepository(),
                    common_provider=_FailingCommonProvider(),
                )
            ),
            place_photos=FakePlacePhotoRepository(),
        ),
        candidate_limit=10,
        clock=lambda: datetime.now(KST),
    )


@pytest.mark.asyncio
async def test_detailCommon2가_실패해도_사진은_카드에_남는다() -> None:
    """사진은 detailCommon2와 출처가 다르다 — 저장소에서 오므로 한도와 무관하다.

    그런데도 안 보이던 이유는 상세 실패가 응답을 조기에 끝내 사진 조회가 한 번도
    실행되지 않았기 때문이다. 상세를 저장소 값으로 끝까지 조립해야 이 경로가 돈다.
    """
    service = _service_with_failing_common()

    response = await service.fetch_info_context(
        InfoContextRequest(
            request_id="request-info-card-common-fail",
            place_name="테스트 박물관",
            place_context="explicit",
            question_type="parking",
        )
    )

    assert response.status == "success"
    card = _result(response).place_card
    assert card is not None
    assert [photo.url for photo in card.photos] == [
        "https://example.test/fake-museum-1.jpg",
        "https://example.test/fake-museum-1-2.jpg",
        "https://example.test/fake-museum-1-3.jpg",
    ]


@pytest.mark.asyncio
async def test_detailCommon2가_실패해도_저장소_값은_카드에_남는다() -> None:
    """빠지는 건 개요·홈페이지 둘뿐이다."""
    service = _service_with_failing_common()

    response = await service.fetch_info_context(
        InfoContextRequest(
            request_id="request-info-card-common-fail-2",
            place_name="테스트 박물관",
            place_context="explicit",
            question_type="parking",
        )
    )

    result = _result(response)
    assert result.fields["parking"] == "가능 요금 (30분 1,500원)"
    card = result.place_card
    assert card is not None
    assert card.place_name == "테스트 박물관"
    assert card.operating_hours == "09:00~18:00"
    assert card.rest_date == "매주 월요일"
    assert card.thumbnail_url == "https://example.test/fake-museum-1.jpg"
    assert card.overview is None
    assert card.homepage is None
