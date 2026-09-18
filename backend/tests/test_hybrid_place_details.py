"""places 캐시 + detailCommon2 하이브리드 상세조회 Provider 테스트."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import pytest

from app.agent_context.info_field_rules import extract_info_fields
from app.domain.models import (
    PlaceCommonDetails,
    StoredPlaceDetail,
    StoredPlaceLocation,
)
from app.errors import AppError, ProviderTimeoutError, ProviderUnavailableError
from app.providers.contracts import (
    ProviderResult,
    ProviderSource,
    ProviderStatus,
    provider_result,
)
from app.providers.hybrid_place_details import HybridPlaceDetailsProvider
from app.tools.contracts import ToolStatus
from app.tools.place_detail import GetPlaceDetailTool, PlaceDetailQuery

_FETCHED_AT = datetime(2026, 8, 10, 3, 0, tzinfo=UTC)


def _location(content_id: str = "126508") -> StoredPlaceLocation:
    return StoredPlaceLocation(
        content_id=content_id,
        title="경복궁",
        address="서울특별시 종로구 사직로 161",
        latitude=37.5788,
        longitude=126.9770,
    )


def _row(**overrides: object) -> StoredPlaceDetail:
    base: dict[str, object] = {
        "content_id": "126508",
        "content_type_id": "12",
        "title": "경복궁",
        "address": "서울특별시 종로구 사직로 161",
        "operating_hours_raw": "09:00~18:00",
        "rest_date_raw": "매주 화요일",
        "detail_fetch_status": "success",
        "detail_fetched_at": _FETCHED_AT,
        "source_modified_at": None,
        "lcls_systm1": "HS",
        "lcls_systm2": "HS01",
        "lcls_systm3": "HS010100",
        "parking_info_raw": "가능 (승용차 240대 / 버스 50대)",
        "parking_fee_raw": None,
        "use_fee_raw": "어른 3,000원",
        "info_center_raw": "02-3700-3900",
        "baby_carriage_raw": "없음",
        "pet_raw": "불가",
        "credit_card_raw": "가능",
        "restroom_raw": "있음",
        "first_image_url": "https://example.test/first.jpg",
        "thumbnail_url": "https://example.test/thumb.jpg",
    }
    base.update(overrides)
    return StoredPlaceDetail(**base)  # type: ignore[arg-type]


class _Locations:
    def __init__(self, matches: tuple[StoredPlaceLocation, ...]) -> None:
        self._matches = matches
        self.queries: list[str] = []

    async def find_active_places_by_name(
        self, name: str
    ) -> tuple[StoredPlaceLocation, ...]:
        self.queries.append(name)
        return self._matches


class _Details:
    def __init__(self, rows: dict[str, StoredPlaceDetail]) -> None:
        self._rows = rows
        # provider가 무장애 정보를 요청했는지 기록한다. 요청하지 않으면 저장소가
        # 그 값을 읽어오지 않아 INFO facility 질문이 답할 근거를 잃는다.
        self.barrier_free_requested: list[bool] = []

    async def get_active_place_details(
        self,
        content_ids: Sequence[str],
        *,
        include_barrier_free: bool = False,
    ) -> dict[str, StoredPlaceDetail]:
        self.barrier_free_requested.append(include_barrier_free)
        return {
            content_id: self._rows[content_id]
            for content_id in content_ids
            if content_id in self._rows
        }


class _Common:
    def __init__(
        self,
        overview: str | None = "조선왕조 제일의 법궁이다.",
        homepage: str | None = "https://royal.khs.go.kr/",
        telephone: str | None = None,
    ) -> None:
        self._details = PlaceCommonDetails(
            content_id="126508",
            overview=overview,
            homepage=homepage,
            telephone=telephone,
        )
        self.calls: list[str] = []

    async def get_common_details(
        self, content_id: str
    ) -> ProviderResult[PlaceCommonDetails]:
        self.calls.append(content_id)
        return provider_result(
            self._details,
            source=ProviderSource.TOUR_API_PLACE,
            status=ProviderStatus.SUCCESS,
        )


def _provider(
    *,
    matches: tuple[StoredPlaceLocation, ...] = (_location(),),
    rows: dict[str, StoredPlaceDetail] | None = None,
    common: _Common | None = None,
) -> tuple[HybridPlaceDetailsProvider, _Common]:
    common_provider = common or _Common()
    provider = HybridPlaceDetailsProvider(
        location_repository=_Locations(matches),
        details_repository=_Details(rows if rows is not None else {"126508": _row()}),
        common_provider=common_provider,
    )
    return provider, common_provider


@pytest.mark.asyncio
async def test_외부_호출은_detailCommon2_한_번뿐이다() -> None:
    """이 provider의 존재 이유다.

    TourAPI 직접 경로는 searchKeyword2 + detailCommon2 + detailIntro2로 3회를 쓴다.
    이름 대조와 intro 값이 모두 저장소에 있어 여기서는 1회로 끝난다.
    """
    provider, common = _provider()

    await provider.find_details_by_name("경복궁")

    assert common.calls == ["126508"]


@pytest.mark.asyncio
async def test_캐시와_common을_합쳐_채운다() -> None:
    provider, _ = _provider()

    details = (await provider.find_details_by_name("경복궁")).data

    # 저장소에서 온 값
    assert details.title == "경복궁"
    assert details.address == "서울특별시 종로구 사직로 161"
    assert details.operating_hours == "09:00~18:00"
    assert details.parking == "가능 (승용차 240대 / 버스 50대)"
    assert details.fee == "어른 3,000원"
    assert details.telephone == "02-3700-3900"
    # detailCommon2에서 온 값
    assert details.overview == "조선왕조 제일의 법궁이다."
    assert details.homepage == "https://royal.khs.go.kr/"


@pytest.mark.asyncio
async def test_운영시간을_원문에서_다시_정규화한다() -> None:
    provider, _ = _provider()

    details = (await provider.find_details_by_name("경복궁")).data

    assert details.operating_schedule is not None
    assert details.operating_schedule.cleaned_operating_hours == "09:00~18:00"


@pytest.mark.asyncio
async def test_안내처가_common의_tel보다_우선한다() -> None:
    """대부분의 유형에서 tel은 비어 있지만, 둘 다 있으면 안내처가 정확하다."""
    provider, _ = _provider(common=_Common(telephone="02-000-0000"))

    details = (await provider.find_details_by_name("경복궁")).data

    assert details.telephone == "02-3700-3900"


@pytest.mark.asyncio
async def test_안내처가_비면_common의_tel로_떨어진다() -> None:
    """축제(15)가 이 경로다 — infocenter 계열이 없고 tel만 채워진다."""
    provider, _ = _provider(
        rows={"126508": _row(info_center_raw=None)},
        common=_Common(telephone="02-3210-1645"),
    )

    details = (await provider.find_details_by_name("경복궁")).data

    assert details.telephone == "02-3210-1645"


@pytest.mark.asyncio
async def test_둘_다_없으면_전화번호는_None이다() -> None:
    """info_center_raw 적재 전 현재 상태다. 없는 값을 지어내지 않는다."""
    provider, _ = _provider(
        rows={"126508": _row(info_center_raw=None)}, common=_Common()
    )

    details = (await provider.find_details_by_name("경복궁")).data

    assert details.telephone is None


@pytest.mark.asyncio
async def test_주차_요금이_소비_측까지_도달한다() -> None:
    provider, _ = _provider()

    details = (await provider.find_details_by_name("경복궁")).data

    assert extract_info_fields("parking", details) == {
        "parking": "가능 (승용차 240대 / 버스 50대)"
    }
    assert extract_info_fields("fee", details) == {"fee": "어른 3,000원"}
    assert extract_info_fields("general_info", details) == {
        "overview": "조선왕조 제일의 법궁이다.",
        "homepage": "https://royal.khs.go.kr/",
    }


@pytest.mark.asyncio
async def test_편의시설도_캐시에서_답한다() -> None:
    """D-060에서 chk* 컬럼을 추가해 tour_api와 답할 수 있는 질문이 같아졌다.

    이게 성립해야 INFO 출처를 고르는 설정을 없앤 근거가 유지된다.
    """
    provider, _ = _provider()

    details = (await provider.find_details_by_name("경복궁")).data

    assert extract_info_fields("facility", details) == {
        "baby_carriage": "없음",
        "pet": "불가",
        "credit_card": "가능",
        "restroom": "있음",
    }


@pytest.mark.asyncio
async def test_카드_이미지는_원본_크기를_우선한다() -> None:
    """firstimage2(작은 썸네일)보다 firstimage(원본 크기)를 우선 노출한다.

    INFO 상세 카드가 이미지를 크게 보여주는 용도라 작은 썸네일을 확대하면
    화질이 뭉개진다(2026-08-13 실사용 피드백).
    """
    provider, _ = _provider()

    details = (await provider.find_details_by_name("경복궁")).data

    assert details.thumbnail_url == "https://example.test/first.jpg"


@pytest.mark.asyncio
async def test_원본_이미지가_없으면_작은_썸네일로_대체한다() -> None:
    provider, _ = _provider(
        rows={"126508": _row(first_image_url=None)},
    )

    details = (await provider.find_details_by_name("경복궁")).data

    assert details.thumbnail_url == "https://example.test/thumb.jpg"


@pytest.mark.asyncio
async def test_이름이_없으면_404를_던진다() -> None:
    """RealPlaceProvider와 같은 코드·상태여야 Tool이 no_data로 낮춘다."""
    provider, _ = _provider(matches=())

    with pytest.raises(AppError) as exc_info:
        await provider.find_details_by_name("없는장소")

    assert exc_info.value.status_code == 404
    assert exc_info.value.code == "place_not_found"


@pytest.mark.asyncio
async def test_상세_행이_없어도_404다() -> None:
    """이름 조회와 상세 조회 사이에 비활성화된 경우다. 장애가 아니다."""
    provider, _ = _provider(rows={})

    with pytest.raises(AppError) as exc_info:
        await provider.find_details_by_name("경복궁")

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_Tool이_404를_no_data로_낮춘다() -> None:
    """계약이 실제로 맞물리는지 Tool까지 태워 확인한다."""
    provider, _ = _provider(matches=())
    tool = GetPlaceDetailTool(provider)

    result = await tool.execute(PlaceDetailQuery(place_name="없는장소"))

    assert result.status is ToolStatus.NO_DATA
    assert result.details is None


@pytest.mark.asyncio
async def test_Tool이_상세를_그대로_전달한다() -> None:
    provider, _ = _provider()
    tool = GetPlaceDetailTool(provider)

    result = await tool.execute(PlaceDetailQuery(place_name="경복궁"))

    assert result.status is ToolStatus.SUCCESS
    assert result.details is not None
    assert result.details.parking == "가능 (승용차 240대 / 버스 50대)"


@pytest.mark.asyncio
async def test_무장애_정보를_저장소에_요청한다() -> None:
    """요청하지 않으면 저장소가 무장애 값을 읽어오지 않는다.

    INFO facility 질문이 답할 근거를 잃는데, 값이 비어 있을 뿐 오류가 나지 않아
    화면에서는 "그 장소에 정보가 없다"로 보인다.
    """
    details_repository = _Details({"126508": _row()})
    provider = HybridPlaceDetailsProvider(
        location_repository=_Locations((_location(),)),
        details_repository=details_repository,
        common_provider=_Common(),
    )

    await provider.find_details_by_name("경복궁")

    assert details_repository.barrier_free_requested == [True]


@pytest.mark.asyncio
async def test_무장애_원문이_facility_응답까지_도달한다() -> None:
    """저장소 → PlaceDetails → 계약 키로 이어지는 배선을 못 박는다.

    값을 비워 둔 채 테스트하면 배선이 끊어져도 통과한다. 실제 문장을 넣는다.
    """
    provider, _ = _provider(
        rows={
            "126508": _row(
                approach_route_raw="출입구까지 경사로가 설치되어 있음",
                entrance_access_raw="주출입구는 턱이 없어 휠체어 접근 가능함",
                elevator_raw="엘리베이터 있음",
                accessible_restroom_raw="장애인 화장실 있음",
                accessible_parking_raw="장애인 주차장 있음(9면)",
                wheelchair_rental_raw="대여가능",
                stroller_rental_raw="유모차 대여 가능함(4대)",
                nursing_room_raw="수유실 있음(흥례문)",
                guide_dog_raw="동반가능",
                braille_block_raw="점자블록 있음",
                braille_promotion_raw="점자 안내물 있음",
                audio_guide_raw="음성 안내 있음",
                public_transport_raw="저상버스 운행 : 모든 버스",
                infant_family_etc_raw="기저귀교환대 있음",
                disability_etc_raw="휠체어 관람경로가 표시되어 있음",
            )
        }
    )

    details = (await provider.find_details_by_name("경복궁")).data
    fields = extract_info_fields("facility", details)

    # 접근로·주출입구·승강기는 한 키로 합쳐 나간다.
    assert fields["wheelchair_access"] == (
        "출입구까지 경사로가 설치되어 있음"
        " / 주출입구는 턱이 없어 휠체어 접근 가능함"
        " / 엘리베이터 있음"
    )
    # 이름과 달리 출입이 아니라 대여다.
    assert fields["wheelchair_rental"] == "대여가능"
    # 일반 화장실과 장애인 화장실은 뜻이 달라 둘 다 나간다.
    assert fields["restroom"] == "있음"
    assert fields["accessible_restroom"] == "장애인 화장실 있음"
    assert fields["accessible_parking"] == "장애인 주차장 있음(9면)"
    assert fields["stroller_rental"] == "유모차 대여 가능함(4대)"
    assert fields["nursing_room"] == "수유실 있음(흥례문)"
    assert fields["guide_dog"] == "동반가능"
    assert fields["braille_block"] == "점자블록 있음"
    assert fields["braille_promotion"] == "점자 안내물 있음"
    assert fields["audio_guide"] == "음성 안내 있음"
    assert fields["public_transport"] == "저상버스 운행 : 모든 버스"
    assert fields["infant_family_etc"] == "기저귀교환대 있음"
    assert fields["disability_etc"] == "휠체어 관람경로가 표시되어 있음"


@pytest.mark.asyncio
async def test_무장애_정보가_없는_장소는_키가_생기지_않는다() -> None:
    """places 행의 81%가 이 경우다. 없는 값을 지어내지 않는다."""
    provider, _ = _provider()

    details = (await provider.find_details_by_name("경복궁")).data
    fields = extract_info_fields("facility", details)

    assert "wheelchair_access" not in fields
    assert "accessible_restroom" not in fields
    # 기존 편의시설 답변은 그대로다.
    assert fields["restroom"] == "있음"
    assert fields["pet"] == "불가"


class _FailingCommon:
    """detailCommon2 호출이 실패하는 provider.

    일일 한도 소진(returnReasonCode=22)을 본떴지만, 이 provider는 원인을 가리지
    않고 삼키므로 어떤 AppError를 넣어도 결과가 같아야 한다.
    """

    def __init__(self, error: AppError | None = None) -> None:
        self._error = error or ProviderUnavailableError(
            "TourAPI", detail="returnReasonCode=22, errMsg=SERVICE ERROR"
        )
        self.calls: list[str] = []

    async def get_common_details(
        self, content_id: str
    ) -> ProviderResult[PlaceCommonDetails]:
        self.calls.append(content_id)
        raise self._error


@pytest.mark.asyncio
async def test_detailCommon2가_실패해도_저장소_값으로_답한다() -> None:
    """개요 하나 때문에 나머지 전부를 버리지 않는다.

    detailCommon2가 실어 오는 건 overview·homepage 둘뿐이다. 여기서 예외를 올리면
    상세가 unavailable이 되고, routes/chat.py가 이미 만들어 둔 place_card를 응답에
    싣지 않아 장소명·주소·사진까지 화면에서 사라진다.
    """
    provider, _ = _provider(common=_FailingCommon())

    details = (await provider.find_details_by_name("경복궁")).data

    # 저장소에서 온 값은 그대로 남는다.
    assert details.title == "경복궁"
    assert details.address == "서울특별시 종로구 사직로 161"
    assert details.operating_hours == "09:00~18:00"
    assert details.parking == "가능 (승용차 240대 / 버스 50대)"
    assert details.fee == "어른 3,000원"
    assert details.telephone == "02-3700-3900"
    assert details.thumbnail_url == "https://example.test/first.jpg"
    # 못 받은 두 필드만 빈다. 문구를 지어내지 않는다.
    assert details.overview is None
    assert details.homepage is None


@pytest.mark.asyncio
async def test_detailCommon2_실패는_PARTIAL로_남는다() -> None:
    """개요가 빠진 응답이 조용히 정상으로 보이면 안 된다."""
    provider, _ = _provider(common=_FailingCommon())

    result = await provider.find_details_by_name("경복궁")

    assert result.metadata.status is ProviderStatus.PARTIAL


@pytest.mark.asyncio
async def test_타임아웃도_같은_방식으로_삼킨다() -> None:
    """원인을 가리지 않는다 — 이 요청에서 할 수 있는 일이 같기 때문이다."""
    provider, _ = _provider(common=_FailingCommon(ProviderTimeoutError("TourAPI")))

    result = await provider.find_details_by_name("경복궁")

    assert result.metadata.status is ProviderStatus.PARTIAL
    assert result.data.parking == "가능 (승용차 240대 / 버스 50대)"


@pytest.mark.asyncio
async def test_Tool이_common_실패를_unavailable로_올리지_않는다() -> None:
    """UNAVAILABLE이 되는 순간 호출부가 카드를 통째로 버린다(routes/chat.py)."""
    provider, _ = _provider(common=_FailingCommon())
    tool = GetPlaceDetailTool(provider)

    result = await tool.execute(PlaceDetailQuery(place_name="경복궁"))

    assert result.status is ToolStatus.SUCCESS
    assert result.details is not None
    assert result.details.parking == "가능 (승용차 240대 / 버스 50대)"


@pytest.mark.asyncio
async def test_저장소에_없는_장소는_common을_부르지도_않는다() -> None:
    """404는 그대로 404다 — 삼키는 대상은 detailCommon2 실패뿐이다."""
    common = _FailingCommon()
    provider, _ = _provider(matches=(), common=common)

    with pytest.raises(AppError) as exc_info:
        await provider.find_details_by_name("없는장소")

    assert exc_info.value.status_code == 404
    assert common.calls == []
