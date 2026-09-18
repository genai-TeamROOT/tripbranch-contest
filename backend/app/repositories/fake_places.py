"""Supabase 없이 동작하는 장소 저장소 fake.

역할: PLACE_PROVIDER=fake 환경에서도 장소명 해석과 INFO 집중률 경로, 추천 카드
조립이 끝까지 동작하도록 종로구 대표 장소 몇 곳을 메모리로 제공한다.
입력: 장소명(정확 일치, 공백 무시) 또는 content_id 목록.
출력: StoredPlaceLocation / StoredPlaceDetail 튜플.
호출 시점: providers.factory가 Supabase 설정이 없을 때 주입한다.

집중률 조회는 매핑된 장소명으로만 나간다(D-043). Fake 환경에 매핑 저장소가 없으면
INFO 혼잡도가 전부 no_data로 떨어져 개발 중 경로 확인이 불가능해진다. 좌표는
FakeGeocodingProvider의 _KNOWN_LOCATIONS와 같은 값을 쓴다 — 해석 경로가
달라져도 좌표가 흔들리지 않아야 한다.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from app.domain.models import PlacePhoto, StoredPlaceDetail, StoredPlaceLocation
from app.providers.contracts import ProviderSource

_FAKE_PLACES: tuple[StoredPlaceLocation, ...] = (
    StoredPlaceLocation(
        content_id="126508",
        title="경복궁",
        address="서울특별시 종로구 사직로 161",
        latitude=37.5788,
        longitude=126.9770,
        district_code="110",
        concentration_name="경복궁",
    ),
    StoredPlaceLocation(
        content_id="126509",
        title="창덕궁",
        address="서울특별시 종로구 율곡로 99",
        latitude=37.5826,
        longitude=126.9919,
        district_code="110",
        concentration_name="창덕궁과 후원 [유네스코 세계유산]",
    ),
    StoredPlaceLocation(
        content_id="126510",
        title="종묘",
        address="서울특별시 종로구 종로 157",
        latitude=37.5739,
        longitude=126.9945,
        district_code="110",
        concentration_name="종묘 [유네스코 세계유산]",
    ),
    StoredPlaceLocation(
        content_id="126537",
        title="북촌한옥마을",
        address="서울특별시 종로구 계동길 37",
        latitude=37.5826,
        longitude=126.9850,
        district_code="110",
        concentration_name="북촌한옥마을",
    ),
    # 매핑이 없는 장소도 하나 둔다 — 인근 대체 경로를 fake로 확인할 수 있어야 한다.
    StoredPlaceLocation(
        content_id="264337",
        title="쌈지길",
        address="서울특별시 종로구 인사동길 44",
        latitude=37.5740,
        longitude=126.9855,
        district_code="110",
        concentration_name=None,
    ),
)


_FAKE_DETAIL_FETCHED_AT = datetime(2026, 8, 10, 3, 0, tzinfo=UTC)

# content_id는 StubPlaceProvider가 내놓는 후보 id와 맞춘다 — fake 환경에서 추천
# 후보를 만드는 건 저 provider이므로, 다른 id를 두면 카드 조회가 전부 미조회로
# 떨어져 조립 경로가 한 줄도 실행되지 않는다.
#
# 값의 모양은 종로구 실측(2026-08-10)에서 그대로 가져왔다. 특히 주차는 비워두지
# 않는다 — 비워두면 normalize_parking()이 항상 UNKNOWN만 돌려주고, 판정 순서
# 규칙(`불가능`을 `가능`보다 먼저 본다)이 fake 환경에서 검증되지 않는다.
_FAKE_PLACE_DETAILS: tuple[StoredPlaceDetail, ...] = (
    StoredPlaceDetail(
        content_id="fake-museum-1",
        content_type_id="14",
        title="테스트 박물관",
        address="서울 종로구 어딘가",
        latitude=37.5735,
        longitude=126.9788,
        operating_hours_raw="09:00~18:00",
        rest_date_raw="매주 월요일",
        detail_fetch_status="success",
        detail_fetched_at=_FAKE_DETAIL_FETCHED_AT,
        source_modified_at=None,
        lcls_systm1="VE",
        lcls_systm2="VE07",
        lcls_systm3="VE070100",
        # `<br>` 정리와 note 보존을 함께 태우는 모양(실측: 한국미술관).
        parking_info_raw="가능<br>요금 (30분 1,500원)",
        parking_fee_raw=None,
        first_image_url="https://example.test/fake-museum-1.jpg",
        thumbnail_url="https://example.test/fake-museum-1-thumb.jpg",
    ),
    StoredPlaceDetail(
        content_id="fake-cafe-1",
        content_type_id="39",
        title="테스트 카페",
        address="서울 종로구 어딘가",
        latitude=37.5720,
        longitude=126.9850,
        operating_hours_raw="08:00~22:00",
        rest_date_raw="연중무휴",
        detail_fetch_status="success",
        detail_fetched_at=_FAKE_DETAIL_FETCHED_AT,
        source_modified_at=None,
        lcls_systm1="FD",
        lcls_systm2="FD05",
        lcls_systm3="FD050100",
        # 실측에서 가장 흔한 값이자 판정이 가장 쉽게 뒤집히는 값이다(313건).
        parking_info_raw="불가능",
        parking_fee_raw=None,
        # 이미지가 없는 장소가 실측 844건 중 169건(20%)이라 한 곳은 비워 둔다.
        first_image_url=None,
        thumbnail_url=None,
    ),
)


# 사진이 여러 장인 장소를 한 곳 둔다. 한 장뿐이면 상세 화면의 갤러리 분기가
# fake 환경에서 한 번도 실행되지 않고, 그러면 여러 장을 그리는 코드가 실 저장소를
# 붙이기 전까지 검증되지 않는다.
#
# fake-cafe-1은 키 자체를 두지 않는다 — 이미지가 없는 장소(실측 20%)를 대변하고,
# 그쪽 이미지 필드도 이미 None이다.
_FAKE_PLACE_PHOTOS: dict[str, tuple[PlacePhoto, ...]] = {
    "fake-museum-1": (
        PlacePhoto(
            content_id="fake-museum-1",
            photo_order=1,
            url="https://example.test/fake-museum-1.jpg",
            image_name="테스트 박물관 (1)",
        ),
        PlacePhoto(
            content_id="fake-museum-1",
            photo_order=2,
            url="https://example.test/fake-museum-1-2.jpg",
            image_name="테스트 박물관 (2)",
        ),
        PlacePhoto(
            content_id="fake-museum-1",
            photo_order=3,
            url="https://example.test/fake-museum-1-3.jpg",
            image_name="테스트 박물관 (3)",
        ),
    ),
}


def _key(value: str) -> str:
    return value.replace(" ", "").casefold()


class FakePlaceLocationRepository:
    """장소명 조회와 집중률 매핑 목록을 메모리로 돌려주는 fake 저장소."""

    # 응답 metadata에 실저장소로 보이면 안 된다(D-042).
    provider_source = ProviderSource.FAKE_PLACES

    def __init__(self, places: tuple[StoredPlaceLocation, ...] = _FAKE_PLACES) -> None:
        self._places = places

    async def find_active_places_by_name(
        self, name: str
    ) -> tuple[StoredPlaceLocation, ...]:
        # 실제 저장소와 같이 공백 차이는 표기 차이로 본다.
        wanted = _key(name.strip())
        if not wanted:
            return ()
        return tuple(place for place in self._places if _key(place.title) == wanted)

    async def find_concentration_mapped_places(self) -> tuple[StoredPlaceLocation, ...]:
        return tuple(place for place in self._places if place.concentration_name)


class FakePlaceDetailsRepository:
    """추천 카드 조립에 필요한 상세 행을 메모리로 돌려주는 fake 저장소."""

    # 응답 metadata에 실저장소로 보이면 안 된다(D-042).
    provider_source = ProviderSource.FAKE_PLACES

    def __init__(
        self, details: tuple[StoredPlaceDetail, ...] = _FAKE_PLACE_DETAILS
    ) -> None:
        self._details = {detail.content_id: detail for detail in details}

    async def get_active_place_details(
        self,
        content_ids: Sequence[str],
        *,
        include_barrier_free: bool = False,
    ) -> dict[str, StoredPlaceDetail]:
        # 실제 저장소와 같이 없는 id는 결과에서 빠진다 — 자리를 채워 주면 호출 측이
        # 미조회를 알아채지 못한다.
        return {
            content_id: self._details[content_id]
            for content_id in content_ids
            if content_id in self._details
        }


class FakePlacePhotoRepository:
    """장소 사진 목록을 메모리로 돌려주는 fake 저장소."""

    # 응답 metadata에 실저장소로 보이면 안 된다(D-042).
    provider_source = ProviderSource.FAKE_PLACES

    def __init__(
        self,
        photos: dict[str, tuple[PlacePhoto, ...]] | None = None,
    ) -> None:
        self._photos = dict(_FAKE_PLACE_PHOTOS if photos is None else photos)

    async def find_place_photos(
        self,
        content_ids: Sequence[str],
    ) -> dict[str, tuple[PlacePhoto, ...]]:
        # 실제 저장소와 같이 사진이 없는 id는 결과에서 빠진다.
        return {
            content_id: self._photos[content_id]
            for content_id in content_ids
            if content_id in self._photos
        }


__all__ = [
    "FakePlaceDetailsRepository",
    "FakePlaceLocationRepository",
    "FakePlacePhotoRepository",
]
