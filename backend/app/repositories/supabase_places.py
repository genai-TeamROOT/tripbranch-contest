"""Supabase PostgREST를 사용하는 장소 동기화 저장소."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import TypeVar
from uuid import UUID

import httpx

from app.domain.models import (
    AccessibilityNeed,
    AccessibilityVerdict,
    BarrierFreePlaceRow,
    DistrictPlaceRow,
    PlaceBarrierFreeDetails,
    PlaceCategoryFilter,
    PlaceEvidenceMatch,
    PlaceEvidenceSnippet,
    PlaceMoodMatch,
    PlaceMoodProfile,
    PlacePhoto,
    StoredPlaceDetail,
    StoredPlaceLocation,
    StoredPlaceState,
    TourPlaceRecord,
)
from app.errors import AppError
from app.place_search_policy import PLACE_SEARCH_LDONG_REGION_CODE
from app.providers.tour_category_registry import TourCategoryRegistry, get_tour_category_registry
from app.service_area import SUPPORTED_DISTRICT_CODES, is_plausible_seoul_coordinate

# search_place_evidence RPC가 강제하는 후보 상한. 넘으면 RPC가 즉시 에러를
# 던진다 — 여기서 미리 막아 왕복 한 번을 아끼고, 실패 지점을 호출부 가까이 둔다.
_MAX_EVIDENCE_CANDIDATES = 500

# search_place_mood도 같은 상한을 둔다. 이유는 다르다 — place_mood_vectors는
# 장소당 한 행이라 종로구만 적재한 지금 631행이고 전체를 훑어도 빠르지만,
# 후보 배열로 좁히면 HNSW 인덱스를 못 타고 순차 스캔이 된다.
_MAX_MOOD_CANDIDATES = 500

# 발화 경로가 한 번에 읽는 장소 수. PostgREST의 in 필터는 URL에 그대로 실려서
# 너무 길면 요청줄 길이 제한에 걸린다. content_id가 7자리라 200건이면 1.6KB다.
_MOOD_PROFILE_CHUNK_SIZE = 200

# 구 단위 후보 조회가 한 번에 받는 행 수. 가장 큰 구가 1,133곳(강남구)이라 두
# 페이지면 끝난다. PostgREST 기본 상한이 1,000이라 그보다 크게 잡아도 소용없다.
_DISTRICT_PAGE_SIZE = 1000

# 구 단위 후보 한 건에 필요한 컬럼. `DistrictPlaceRow`가 채우는 자리와 같다.
_DISTRICT_PLACE_COLUMNS = ",".join(
    (
        "content_id",
        "title",
        "address",
        "latitude",
        "longitude",
        "content_type_id",
        "lcls_systm1",
        "lcls_systm2",
        "lcls_systm3",
        "first_image_url",
    )
)

# 상세 화면에 보여줄 사진 수 상한. 지금 가장 많은 장소가 9장이라(국립중앙박물관·
# 딜쿠샤 등, 2026-08-31 실측) 실제로 잘리는 장소는 없지만, 적재가 구 단위로
# 계속 진행 중이라 상한 없이 두지 않는다.
#
# **번호가 아니라 순서로 자른다.** photo_order에 빈 번호가 있는 장소가 5,465곳 중
# 7곳 있다(김희수아트센터 [1,2,3,4,5,6,8,12], 딜쿠샤 [1,2,3,4,5,7,9,10,11]).
# `photo_order <= 10` 같은 조회 필터로 자르면 8장짜리 장소에서 12번 사진이 빠져
# 7장만 나온다 — 앞에서 열 장을 고르는 것과 번호가 열 이하인 것을 고르는 것은
# 다른 일이다.
_PLACE_PHOTO_LIMIT = 10

# 장소 하나에서 읽어 올 행 수의 상한. 위 상한은 순서로 자르므로 자르기 전
# 행 수를 여기서 막는다. 지금 최대 장수가 9, 최대 번호가 12라 20이면 넉넉하다.
_PLACE_PHOTO_ROW_BUDGET = 20

# 사진 조회가 한 번에 읽는 장소 수. 장소당 최대 _PLACE_PHOTO_ROW_BUDGET행이라
# 40곳이면 800행으로 _READ_PAGE_SIZE 안에 들어간다.
_PLACE_PHOTO_CHUNK_SIZE = 40

# PostgREST의 in 필터 값. 지원 구가 늘면 SUPPORTED_DISTRICT_CODES만 고치면 된다.
_SUPPORTED_DISTRICT_FILTER = f"in.({','.join(sorted(SUPPORTED_DISTRICT_CODES))})"

_READ_PAGE_SIZE = 1000
_UPSERT_CHUNK_SIZE = 100
_PLACE_SUMMARY_COLUMNS = ",".join(
    (
        "area_code",
        "district_code",
        "is_active",
        "detail_fetch_status",
        "operating_parse_status",
        "operating_parser_version",
        "detail_fetched_at",
        # Ops의 구별 카테고리 현황은 같은 전량 조회를 재사용한다. 원본 행은 응답에
        # 내보내지 않고 대·중·소분류별 건수와 예시 두 개만 조립한다.
        "title",
        "lcls_systm1",
        "lcls_systm2",
        "lcls_systm3",
    )
)
_STATE_COLUMNS = ",".join(
    (
        "content_id",
        "source_modified_at",
        "detail_fetched_at",
        "detail_fetch_status",
        "operating_parser_version",
        "operating_hours_raw",
        "rest_date_raw",
        "is_active",
        "inactive_reason",
    )
)
_LOCATION_COLUMNS = (
    "content_id,title,address,latitude,longitude,district_code,"
    "place_concentration_mappings(primary_concentration_name,concentration_search_keys)"
)

# 제목 조회 한 번에 받아올 행 상한. 필터 여러 개를 or=로 함께 던지므로 사다리
# 시절의 2보다 넉넉해야 앞선 필터의 행이 잘리지 않는다.
_TITLE_QUERY_ROW_LIMIT = 50
# 우선순위로 고른 뒤 호출자에게 넘길 상한. 사다리 시절 limit=2와 같은 값이고,
# 2건이면 호출자가 모호하다고 판정한다(`_lookup_stored_place`).
_TITLE_MATCH_LIMIT = 2

# 별칭 조회는 매핑이 있는 장소로만 좁혀야 해서 inner join이 필요하다.
_LOCATION_COLUMNS_INNER = _LOCATION_COLUMNS.replace(
    "place_concentration_mappings(", "place_concentration_mappings!inner("
)

_DETAIL_COLUMNS = ",".join(
    (
        "content_id",
        "content_type_id",
        "title",
        "address",
        # COMPARE TRAVEL_TIME 실측 연결(2026-08-21)이 쓴다.
        "latitude",
        "longitude",
        "operating_hours_raw",
        "rest_date_raw",
        # 적재 배치가 넣어둔 파싱 결과와 그때의 파서 버전. 읽기 경로가 원문을
        # 매번 다시 파싱하지 않게 한다(`resolve_operating_schedule()`). 버전이
        # 지금 파서와 다르면 이 값을 믿지 않고 원문을 다시 읽는다.
        "operating_schedule",
        "operating_parser_version",
        "detail_fetch_status",
        "detail_fetched_at",
        "source_modified_at",
        # 추천 카드용 컬럼(D-056). 분류 코드는 카테고리 라벨, 주차·이미지는 배지와
        # 썸네일에 쓴다. 상세 배치 조회는 이미 장소당 1행이라 컬럼을 늘려도
        # 요청 수는 그대로다.
        "lcls_systm1",
        "lcls_systm2",
        "lcls_systm3",
        "parking_info_raw",
        "parking_fee_raw",
        "first_image_url",
        "thumbnail_url",
        # INFO 상세 질의(요금·전화번호)를 캐시만으로 답하기 위한 컬럼.
        "use_fee_raw",
        "info_center_raw",
        "baby_carriage_raw",
        "pet_raw",
        "credit_card_raw",
        "restroom_raw",
    )
)
# place_barrier_free에서 함께 읽을 컬럼(D-077). places와 1:1이라 PostgREST 임베드로
# 한 번에 읽는다 — 왕복이 늘지 않는다. 무장애 행이 없는 장소는 임베드 자리가 null로
# 온다.
_BARRIER_FREE_COLUMNS = ",".join(
    (
        "approach_route_raw",
        "entrance_access_raw",
        "elevator_raw",
        "accessible_restroom_raw",
        "accessible_parking_raw",
        "braille_block_raw",
        "braille_promotion_raw",
        "audio_guide_raw",
        "guide_dog_raw",
        "wheelchair_rental_raw",
        "stroller_rental_raw",
        "nursing_room_raw",
        "infant_family_etc_raw",
        "public_transport_raw",
        "disability_etc_raw",
    )
)
_BARRIER_FREE_FIELDS = tuple(_BARRIER_FREE_COLUMNS.split(","))
_VALID_RUN_STATUSES = {"success", "partial_failure", "failed"}
_VALID_PARSE_STATUSES = {"parsed", "partial", "unknown", "assumed"}
# 사후면세점은 세금 환급이 가능한 일반 매장까지 대량 포함한다. 단순 가나다순 예시만
# 보이면 면세점 성격으로 오해하기 쉬워, 대표적인 생활 쇼핑 브랜드를 먼저 보여준다.
_CATEGORY_EXAMPLE_PREFIXES: dict[str, tuple[str, ...]] = {
    "SH040300": ("다이소", "올리브영"),
}
T = TypeVar("T")


class SupabaseRepositoryError(AppError):
    """Supabase 요청이나 응답이 저장소 계약을 만족하지 못한 경우."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            code="supabase_repository_error",
            message="장소 데이터 저장 중 문제가 발생했어요.",
            status_code=502,
            retryable=True,
            provider="supabase",
            details={"upstream_detail": detail},
        )


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _parse_datetime(value: object, field: str) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise SupabaseRepositoryError(f"invalid {field}") from None


def _barrier_free_fields(embedded: object) -> dict[str, str | None]:
    """임베드로 온 place_barrier_free 행을 StoredPlaceDetail 인자로 바꾼다.

    무장애 목록에 없는 장소는 행 자체가 없어 임베드 자리가 null로 온다(D-077).
    그때는 15개 필드를 전부 None으로 둔다 — 조회하지 않은 것과 값이 없는 것을
    구분하지 않는다. 어느 쪽이든 소비 측이 답할 값이 없다는 뜻은 같다.
    """
    row = embedded if isinstance(embedded, Mapping) else {}
    return {field: _optional_text(row.get(field)) for field in _BARRIER_FREE_FIELDS}


def _map_place_locations(
    rows: list[object], *, fallback_title: str
) -> tuple[StoredPlaceLocation, ...]:
    """places 조회 행을 StoredPlaceLocation으로 옮긴다.

    좌표가 없거나 서울 언저리 밖인 행은 버린다. **원본 데이터에 깨진 좌표가 실재한다** —
    활성 8,007곳 중 12건이고 그중 10건이 (19.69, 117.99)라는 같은 값이다(남중국해).
    구 단위 후보 조회는 이미 같은 검사를 하는데(_map_district_place_row) 이름 조회에는
    없어서, "계남근린공원"처럼 깨진 행과 정상 행이 함께 있는 이름이 "2건이니 애매하다"로
    판정됐다. 두 행의 주소가 같아 자치구를 붙여도 선택지가 하나로 합쳐지고, 눌러도
    제자리였다(2026-09-11).

    한 건 때문에 이름 해석을 통째로 실패시키지는 않는다 — 그 행만 버리고 나머지로 간다.
    """
    locations: list[StoredPlaceLocation] = []
    for raw in rows:
        if not isinstance(raw, Mapping) or not raw.get("content_id"):
            raise SupabaseRepositoryError("place location missing content_id")
        try:
            latitude = float(raw["latitude"])
            longitude = float(raw["longitude"])
        except (KeyError, TypeError, ValueError):
            continue
        if not is_plausible_seoul_coordinate(latitude, longitude):
            continue
        # places ↔ place_concentration_mappings는 1:1(FK가 PK)이라 PostgREST가 단일
        # 객체로 내려준다. 관계 형태가 바뀌어 배열로 올 경우도 함께 받는다.
        mapping = raw.get("place_concentration_mappings")
        if isinstance(mapping, list):
            mapping = mapping[0] if mapping else None
        concentration_name = (
            _optional_text(mapping.get("primary_concentration_name"))
            if isinstance(mapping, Mapping)
            else None
        )
        # tAtsNm은 공백이 든 값에 0건을 돌려주므로 조회용 검색어를 따로 둔다.
        # 목록으로 받아 앞에서부터 시도한다(D-057).
        concentration_search_keys = _search_keys(
            mapping.get("concentration_search_keys") if isinstance(mapping, Mapping) else None
        )
        locations.append(
            StoredPlaceLocation(
                content_id=str(raw["content_id"]),
                title=str(raw.get("title") or fallback_title),
                address=_optional_text(raw.get("address")),
                latitude=latitude,
                longitude=longitude,
                # 집중률 조회는 구를 지정해야 한다. 이 값이 비면 그 장소로는
                # 조회하지 않는다 - 종로구로 대신 묻지 않는다.
                district_code=_optional_text(raw.get("district_code")),
                concentration_name=concentration_name,
                concentration_search_keys=concentration_search_keys,
            )
        )
    return tuple(locations)


# TourAPI가 국가지정문화재류 제목에 규칙적으로 붙이는 지역 접두사. 사용자는
# "명동성당"이라고 하는데 저장소 제목은 "서울 명동성당"이다(활성 26곳).
_TITLE_REGION_PREFIX = "서울 "


def _title_filters(name: str) -> list[str]:
    """장소명 조회에 쓸 PostgREST 필터를 좁은 것부터 나열한다.

    부분 일치로 넓히지 않는다 - "종묘*"로 찾으면 종묘광장공원·종묘대제까지 걸려
    엉뚱한 장소가 검색 중심이 된다. 이름 앞뒤에 **규칙적으로** 붙는 수식만
    허용한다. PostgREST는 ilike의 `*`를 `%`로 바꿔 주므로 인코딩 문제가 없다.

    순서가 곧 우선순위다(D-043). 한 번에 조회하고 이 순서로 골라내므로, 필터를
    더할 때는 넣는 자리가 그대로 우선순위가 된다.
    """
    prefixed = not name.startswith(_TITLE_REGION_PREFIX)
    collapsed = "*".join(name.split()) if any(c.isspace() for c in name) else None

    filters = [f"eq.{name}"]
    # "명동성당" → "서울 명동성당". 와일드카드가 없어 사실상 정확 일치라,
    # "종로"로 찾아도 "서울 종로 낙지볶음 골목"에는 걸리지 않는다. 접두사를 뗀
    # 이름과 같은 제목의 다른 활성 장소는 없다(26곳 전수, 2026-08-24 확인).
    # 정확 일치를 먼저 보므로 나중에 접두사 없는 동명 장소가 적재되면 그쪽이 이긴다.
    if prefixed:
        filters.append(f"ilike.{_TITLE_REGION_PREFIX}{name}")
    if collapsed is not None:
        # "북촌 한옥마을" → "북촌한옥마을"
        filters.append(f"ilike.{collapsed}")
        # "명동 성당" → "서울 명동성당". 두 수식이 함께 걸린 제목이 실제로 있어
        # 각각만으로는 못 찾는다.
        if prefixed:
            filters.append(f"ilike.{_TITLE_REGION_PREFIX}{collapsed}")
    # "종묘" → "종묘 [유네스코 세계유산]", "세검정 터" → "세검정 터 (구 세검정)"
    #
    # 괄호는 공백을 두는 표기와 붙이는 표기가 모두 있다. 공백 있는 쪽만 보면
    # "조계사"로 "조계사(서울)"을 못 찾는다 — 활성 2,761건 중 붙여 쓴 제목이 47건으로
    # 띄어 쓴 14건보다 세 배 넘게 많다(2026-08-25 실측). 동대문디자인플라자(DDP)·
    # 남산공원(서울)·대원군별장(석파정)처럼 사람이 괄호 없이 부르는 이름들이다.
    #
    # 와일드카드가 여는 괄호 뒤에만 있어 부분 일치로 넓어지지 않는다 — "조계사"가
    # "조계사터"나 "조계사길"에는 걸리지 않는다.
    filters.extend([f"ilike.{name} [*", f"ilike.{name} (*", f"ilike.{name}(*"])
    return filters


def _quote_filter_value(value: str) -> str:
    """or= 안에 들어갈 값을 큰따옴표로 감싼다.

    감싸지 않으면 쉼표가 든 제목("꽃,밥에피다" 등 활성 5건)이 필터 구분자로 읽혀
    PGRST100으로 깨진다. 괄호도 마찬가지다. 따옴표 안에서도 ilike의 `*`는 그대로
    와일드카드로 동작한다(2026-08-24 실측).
    """
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _title_or_filter(title_filters: Sequence[str]) -> str:
    """제목 필터 여러 개를 or= 하나로 합친다.

    사다리를 한 칸씩 따로 던지면 저장소에 없는 이름에서 왕복이 필터 수만큼 쌓인다
    ("안국역" 기준 4회, 공백이 든 이름은 5회). 한 번에 던지고 우선순위는 받은 뒤
    파이썬에서 가린다.
    """
    conditions = []
    for title_filter in title_filters:
        operator, _, value = title_filter.partition(".")
        conditions.append(f"title.{operator}.{_quote_filter_value(value)}")
    return f"({','.join(conditions)})"


def _title_filter_matcher(title_filter: str) -> Callable[[str], bool]:
    """제목이 이 필터에 걸리는지 판정한다. 받은 행을 우선순위로 가를 때 쓴다.

    fnmatch를 쓰지 않는다 - 대괄호를 문자 클래스로 읽어 "종묘 [*"가 엉뚱하게
    동작한다. 저장소 제목에 대괄호가 실제로 있다(활성 22건).
    """
    operator, _, value = title_filter.partition(".")
    if operator == "eq":
        return lambda title: title == value
    # ilike: `*`만 와일드카드이고 나머지는 글자 그대로다.
    pattern = "".join(".*" if part == "*" else re.escape(part) for part in re.split(r"(\*)", value))
    compiled = re.compile(f"^{pattern}$", re.IGNORECASE)
    return lambda title: compiled.match(title) is not None


def _select_by_filter_priority(
    rows: Sequence[object], title_filters: Sequence[str]
) -> list[object]:
    """섞여 온 행에서 가장 앞선 필터에 걸린 것만 남긴다.

    사다리를 한 칸씩 던지던 시절의 동작을 그대로 재현한다 - 먼저 걸린 칸에서
    멈췄으므로, 뒤 칸에만 걸리는 행은 애초에 보이지 않았다. 여기서 뒤 칸 행까지
    함께 돌려주면 호출자가 후보 2건 이상으로 보고 되묻기로 새 버린다
    (`_lookup_stored_place`의 `len(matches) > 1` 판정).

    같은 이유로 2건에서 자른다. 그때 limit이 2였고, 그 상한이 곧 "이 칸에서
    후보가 여럿이면 모호하다"는 신호였다.
    """
    matchers = [_title_filter_matcher(title_filter) for title_filter in title_filters]
    best_rank: int | None = None
    selected: list[object] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        title = row.get("title")
        if not isinstance(title, str):
            continue
        rank = next((index for index, matches in enumerate(matchers) if matches(title)), None)
        if rank is None:
            continue
        if best_rank is None or rank < best_rank:
            best_rank, selected = rank, [row]
        elif rank == best_rank:
            selected.append(row)
    return selected[:_TITLE_MATCH_LIMIT]


def _optional_text(value: object) -> str | None:
    return str(value) if value is not None else None


def _optional_float(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _search_keys(value: object) -> tuple[str, ...]:
    """집중률 검색어 목록을 순서 그대로 읽는다(D-057).

    공백이 든 값은 tAtsNm에 넣으면 무엇을 넣든 0건이 돌아오므로 여기서 버린다.
    DB 제약이 같은 것을 막고 있지만, 저장소를 거치지 않고 들어온 값이나 제약이
    없던 시절의 행이 조용히 0건 조회를 만들지 않도록 읽는 쪽에서도 막는다.
    """
    if not isinstance(value, list):
        return ()
    keys: list[str] = []
    for item in value:
        if item is None:
            continue
        text = str(item).strip()
        if text and not any(character.isspace() for character in text):
            keys.append(text)
    return tuple(keys)


def _chunks(values: Sequence[T], size: int) -> list[Sequence[T]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


class SupabasePlaceRepository:
    def __init__(
        self,
        supabase_url: str,
        secret_key: str,
        client: httpx.AsyncClient,
        timeout_seconds: float = 10.0,
    ) -> None:
        normalized_url = supabase_url.strip().rstrip("/")
        if not normalized_url:
            raise ValueError("supabase_url이 필요합니다.")
        if not secret_key.strip():
            raise ValueError("secret_key가 필요합니다.")
        self._rest_url = f"{normalized_url}/rest/v1"
        self._secret_key = secret_key
        self._client = client
        self._timeout_seconds = timeout_seconds

    def _headers(self, prefer: str | None = None) -> dict[str, str]:
        headers = {
            "apikey": self._secret_key,
            "Content-Type": "application/json",
        }
        if prefer is not None:
            headers["Prefer"] = prefer
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        json: object | None = None,
        prefer: str | None = None,
    ) -> httpx.Response:
        try:
            response = await self._client.request(
                method,
                self._rest_url + path,
                params=params,
                json=json,
                headers=self._headers(prefer),
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            return response
        except httpx.TimeoutException:
            response = None
            raise SupabaseRepositoryError("request timeout") from None
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            detail = f"HTTP {status_code}"
            try:
                error_payload = exc.response.json()
                if isinstance(error_payload, Mapping):
                    code = str(error_payload.get("code", "")).strip()
                    message = str(error_payload.get("message", "")).strip()
                    safe_parts = [part for part in (code, message) if part]
                    if safe_parts:
                        detail = f"{detail}: {' - '.join(safe_parts)}"
            except ValueError:
                pass
            response = None
            exc = None
            raise SupabaseRepositoryError(detail) from None
        except httpx.HTTPError:
            response = None
            raise SupabaseRepositoryError("request failed") from None

    @staticmethod
    def _json(response: httpx.Response) -> object:
        try:
            return response.json()
        except ValueError:
            raise SupabaseRepositoryError("non-JSON response") from None

    async def find_preference_insights(self, content_id: str) -> list[dict[str, object]]:
        """상세 카드용 태그 집계와 대표 후기 근거를 한 장소 단위로 읽는다."""
        tag_response, evidence_response = await asyncio.gather(
            self._request(
                "GET",
                "/place_preference_tags",
                params={
                    "select": (
                        "preference_code,preference_label,display_rank,mention_count,"
                        "positive_document_count,negative_document_count"
                    ),
                    "content_id": f"eq.{content_id}",
                    "order": "display_rank.asc",
                    "limit": "5",
                },
            ),
            self._request(
                "GET",
                "/place_preference_evidence",
                params={
                    "select": (
                        "preference_code,polarity,evidence_rank,evidence_text,"
                        "source_type,source_url"
                    ),
                    "content_id": f"eq.{content_id}",
                    "order": "preference_code.asc,polarity.asc,evidence_rank.asc",
                    "limit": "30",
                },
            ),
        )
        tags = self._json(tag_response)
        evidence_rows = self._json(evidence_response)
        if not isinstance(tags, list) or not isinstance(evidence_rows, list):
            raise SupabaseRepositoryError("invalid place preference insight response")

        evidence_by_code: dict[str, list[dict[str, object]]] = {}
        for row in evidence_rows:
            if not isinstance(row, Mapping):
                raise SupabaseRepositoryError("invalid place preference evidence row")
            code = str(row.get("preference_code") or "")
            if code:
                evidence_by_code.setdefault(code, []).append(
                    {
                        "polarity": str(row.get("polarity") or "mixed"),
                        "text": str(row.get("evidence_text") or ""),
                        "source_type": str(row.get("source_type") or ""),
                        "source_url": (str(row["source_url"]) if row.get("source_url") else None),
                    }
                )
        insights: list[dict[str, object]] = []
        for row in tags:
            if not isinstance(row, Mapping):
                raise SupabaseRepositoryError("invalid place preference tag row")
            code = str(row.get("preference_code") or "")
            if not code:
                continue
            insights.append(
                {
                    "code": code,
                    "label": str(row.get("preference_label") or ""),
                    "mention_count": int(row.get("mention_count") or 0),
                    "positive_document_count": int(row.get("positive_document_count") or 0),
                    "negative_document_count": int(row.get("negative_document_count") or 0),
                    "evidence": evidence_by_code.get(code, []),
                }
            )
        return insights

    async def find_preference_tags(
        self,
        content_ids: Sequence[str],
    ) -> dict[str, tuple[dict[str, object], ...]]:
        """추천 카드에 표시할 장소별 상위 취향 태그를 읽는다."""
        unique_ids = list(dict.fromkeys(content_ids))
        if not unique_ids:
            return {}

        grouped: dict[str, list[dict[str, object]]] = {}
        for start in range(0, len(unique_ids), _MOOD_PROFILE_CHUNK_SIZE):
            chunk = unique_ids[start : start + _MOOD_PROFILE_CHUNK_SIZE]
            response = await self._request(
                "GET",
                "/place_preference_tags",
                params={
                    "select": (
                        "content_id,preference_code,preference_label,display_rank,"
                        # 채점이 쓰는 값이다(scoring.py::match_preference_tags).
                        # confidence는 적재가 이미 계산해 둔 0~1 강도이고,
                        # 긍정·부정 문서 수는 부정이 우세한 태그를 가려내는 데 쓴다.
                        "mention_count,positive_document_count,negative_document_count,confidence"
                    ),
                    "content_id": "in.(" + ",".join(chunk) + ")",
                    "order": "content_id.asc,display_rank.asc",
                    # 요청 취향 태그가 기존 상위 5개 밖에 있을 수도 있다. 후보별
                    # 전체 태그를 읽고 호출부에서 요청 태그 우선으로 다시 정렬한다.
                    "limit": str(len(chunk) * 33),
                },
            )
            payload = self._json(response)
            if not isinstance(payload, list):
                raise SupabaseRepositoryError("invalid place_preference_tags response")
            for row in payload:
                if not isinstance(row, Mapping):
                    raise SupabaseRepositoryError("invalid place_preference_tags row")
                content_id = str(row.get("content_id") or "")
                if not content_id:
                    raise SupabaseRepositoryError("preference tag missing content_id")
                grouped.setdefault(content_id, []).append(dict(row))
        return {content_id: tuple(rows) for content_id, rows in grouped.items()}

    async def search_place_evidence(
        self,
        query_embedding: Sequence[float],
        candidate_content_ids: Sequence[str],
        *,
        match_count: int,
        min_similarity: float,
    ) -> tuple[PlaceEvidenceMatch, ...]:
        """취향 질의 임베딩으로 후보 범위 안에서만 근거 문장을 찾는다.

        후보를 좁히지 않으면 RPC가 전체 행을 훑어 수 초가 걸리므로(2026-08-18
        실측: 200~400건 100~300ms, 844건 6~9초) 상한을 여기서 먼저 막는다.
        """
        unique_ids = list(dict.fromkeys(candidate_content_ids))
        if not unique_ids:
            return ()
        if len(unique_ids) > _MAX_EVIDENCE_CANDIDATES:
            raise SupabaseRepositoryError(
                f"후보 content_id가 {len(unique_ids)}건입니다. "
                f"{_MAX_EVIDENCE_CANDIDATES}건 이하로 좁혀서 호출하세요."
            )

        response = await self._request(
            "POST",
            "/rpc/search_place_evidence",
            json={
                "p_query_embedding": list(query_embedding),
                "p_candidate_content_ids": unique_ids,
                "p_match_count": match_count,
                "p_min_similarity": min_similarity,
            },
        )
        payload = self._json(response)
        if not isinstance(payload, list):
            raise SupabaseRepositoryError("invalid search_place_evidence response")
        return tuple(_to_evidence_match(row) for row in payload)

    async def find_mood_profiles(
        self,
        content_ids: Sequence[str],
    ) -> dict[str, PlaceMoodProfile]:
        """미리 계산된 분위기 축 점수를 content_id로 읽는다.

        발화 경로가 쓴다. 축 점수는 적재 때 계산해 뒀으므로 여기서는 벡터 연산도
        임베딩 모델도 필요 없다 — 단순 조회다.

        **분위기 벡터가 없는 장소가 정상이다.** 사진 임베딩은 종로구까지만
        적재돼 있어(2026-08-26 기준 631곳), 나머지 구의 후보는 여기서 빠진다.
        호출부는 결측을 결측으로 다뤄야 하고, 0점으로 채우면 사진이 없는 장소가
        "분위기가 안 맞는 곳"으로 잘못 밀린다.
        """
        unique_ids = list(dict.fromkeys(content_ids))
        if not unique_ids:
            return {}

        profiles: dict[str, PlaceMoodProfile] = {}
        for start in range(0, len(unique_ids), _MOOD_PROFILE_CHUNK_SIZE):
            chunk = unique_ids[start : start + _MOOD_PROFILE_CHUNK_SIZE]
            response = await self._request(
                "GET",
                "/place_mood_vectors",
                params={
                    # embedding은 안 읽는다. 768개 float을 장소마다 실어 오면
                    # 응답이 수 MB가 되는데, 발화 경로는 축 점수만 쓴다.
                    "select": "content_id,axis_scores,photo_count",
                    "content_id": "in.(" + ",".join(chunk) + ")",
                    "limit": str(_MOOD_PROFILE_CHUNK_SIZE),
                },
            )
            payload = self._json(response)
            if not isinstance(payload, list):
                raise SupabaseRepositoryError("invalid place_mood_vectors response")
            for row in payload:
                profile = _to_mood_profile(row)
                profiles[profile.content_id] = profile
        return profiles

    async def search_place_mood(
        self,
        query_embedding: Sequence[float],
        candidate_content_ids: Sequence[str] | None,
        *,
        match_count: int,
        min_similarity: float,
        latitude: float | None = None,
        longitude: float | None = None,
        radius_km: float | None = None,
        mean_center: bool = False,
        axis_weight: float = 1.0,
    ) -> tuple[PlaceMoodMatch, ...]:
        """올린 사진의 벡터로 분위기가 닮은 장소를 찾는다.

        **좌표와 반경을 주면 DB가 직접 좁힌다.** 후보 목록을 만들어 넘기려면
        TourAPI 상세 조회를 거쳐야 해 최대 20곳인데, 반경으로 좁히면 그 안
        전부를 줄 세운다. 사진 유사도는 DB 안에서 끝나 사실상 공짜다.

        둘 다 주면 교집합이고, 좌표만 주면 반경, 후보만 주면 그 목록이다.

        `candidate_content_ids`가 None이면 적재된 전체에서 찾는다. 빈 배열은
        None과 다르게 다뤄 빈 결과를 돌려준다 — 후보를 좁히려다 전부 걸러진
        호출이 전체 검색으로 둔갑하면, 지역 필터를 통과하지 못한 장소가 추천에
        섞인다.

        질의 벡터는 적재와 **같은 모델·같은 정규화**여야 한다. 적재는
        google/siglip2-base-patch16-224로 길이 1 정규화 상태에서 했다.

        `mean_center`를 켜면 질의와 장소 벡터에서 각각 전체 평균을 빼고
        비교한다(D-115). **돌아오는 similarity의 눈금이 달라진다** — 공통
        성분이 빠져 값이 전반적으로 낮아지므로, 켠 결과와 끈 결과의 숫자를
        직접 견주면 안 된다. 순위만 쓴다.

        `axis_weight`가 1.0보다 작으면 분위기 축을 순위에 섞는다(TP-206).
        **이때 돌아오는 similarity는 정렬 기준이 아니다** — 정렬은 유사도 순위와
        축 순위를 섞은 값으로 하고, similarity는 참고로 실어 보낸다. 값이 큰
        쪽이 위에 있다고 가정하면 안 된다.
        """
        payload_ids: list[str] | None = None
        if candidate_content_ids is not None:
            payload_ids = list(dict.fromkeys(candidate_content_ids))
            if not payload_ids:
                return ()
            if len(payload_ids) > _MAX_MOOD_CANDIDATES:
                raise SupabaseRepositoryError(
                    f"후보 content_id가 {len(payload_ids)}건입니다. "
                    f"{_MAX_MOOD_CANDIDATES}건 이하로 좁혀서 호출하세요."
                )

        response = await self._request(
            "POST",
            "/rpc/search_place_mood",
            json={
                "p_query_embedding": list(query_embedding),
                "p_candidate_content_ids": payload_ids,
                "p_match_count": match_count,
                "p_min_similarity": min_similarity,
                "p_latitude": latitude,
                "p_longitude": longitude,
                "p_radius_km": radius_km,
                "p_mean_center": mean_center,
                "p_axis_weight": axis_weight,
            },
        )
        payload = self._json(response)
        if not isinstance(payload, list):
            raise SupabaseRepositoryError("invalid search_place_mood response")
        return tuple(_to_mood_match(row) for row in payload)

    async def search_places_barrier_free(
        self,
        *,
        latitude: float,
        longitude: float,
        radius_km: float,
        needs: Sequence[AccessibilityNeed],
        category_filter: PlaceCategoryFilter | None = None,
        limit: int,
    ) -> tuple[BarrierFreePlaceRow, ...]:
        """무장애 편의를 요구한 요청의 후보를 반경 안에서 거리순으로 찾는다.

        **무장애 조건이 없는 요청은 이 메서드를 부르지 않는다.** 후보 출처가
        TourAPI 실시간 조회에서 저장소 스냅샷으로 바뀌므로, 무장애 요청에만
        한정한다. 빈 `needs`로 부르면 조건 없는 전체 반경 검색이 되는데 그건
        호출부가 의도한 적 없는 동작이라, RPC가 예외를 던지기 전에 여기서 막는다.

        `needs`가 여럿이면 **전부 만족**하는 장소만 돌아온다. "유모차 끌고 갈 만한
        곳"이 STROLLER_ACCESS + INFANT_FACILITIES로 오는데, 둘 다 필요하다고 말한
        것이기 때문이다.

        어느 컬럼을 읽고 무엇을 있다고 볼지는 RPC의 판정 블록이 정한다. 여기서
        같은 판정을 다시 쓰지 않는다 — 두 곳에 두면 한쪽만 바뀌었을 때 결과가
        갈리고, 그건 오류 없이 결과만 틀리는 실패다.
        """
        unique_needs = list(dict.fromkeys(needs))
        if not unique_needs:
            raise ValueError(
                "needs가 비어 있습니다. 무장애 조건이 없으면 이 메서드를 부르지 않습니다."
            )
        if limit <= 0:
            raise ValueError("limit은 0보다 커야 합니다.")

        response = await self._request(
            "POST",
            "/rpc/search_places_barrier_free",
            json={
                "p_latitude": latitude,
                "p_longitude": longitude,
                "p_radius_km": radius_km,
                "p_needs": [need.value for need in unique_needs],
                "p_content_type_id": (category_filter.content_type_id if category_filter else None),
                "p_lcls_systm1": category_filter.lcls_systm1 if category_filter else None,
                "p_lcls_systm2": category_filter.lcls_systm2 if category_filter else None,
                "p_lcls_systm3": category_filter.lcls_systm3 if category_filter else None,
                "p_limit": limit,
            },
        )
        payload = self._json(response)
        if not isinstance(payload, list):
            raise SupabaseRepositoryError("invalid search_places_barrier_free response")
        return tuple(_to_barrier_free_place_row(row) for row in payload)

    async def list_active_places_in_district(
        self, district_code: str
    ) -> tuple[DistrictPlaceRow, ...]:
        """그 구의 활성 장소를 전부 읽는다(D-119).

        **개수를 자르지 않는다.** 몇 곳을 쓸지는 `agent_context.district_selection`이
        정한다. 여기서 앞의 N곳만 주면 그 자름의 순서가 결과를 정해버리는데, 구
        단위에는 의미 있는 순서가 없다 — 거리 기준점이 없고, 저장소 기본 순서는
        content_id다.

        한 구가 최대 1,133곳이라(강남구, 2026-09-01) 한 번에 다 오지 않을 수 있어
        페이지를 이어 받는다.

        좌표가 말이 안 되는 행은 여기서 뺀다. 전 구에 12건 있고, 반경 검색에서는
        어떤 중심점에서도 안 걸려 드러나지 않던 것들이다 — 구 전량 조회가 그 전제를
        깨는 첫 경로다. 판정을 폴리곤이 아니라 사각형으로 하는 이유는
        `service_area.SEOUL_MIN_LATITUDE` 주석에 있다.
        """
        normalized_code = district_code.strip()
        if not normalized_code:
            raise ValueError("district_code가 필요합니다.")

        rows: list[DistrictPlaceRow] = []
        offset = 0
        while True:
            response = await self._request(
                "GET",
                "/places",
                params={
                    "select": _DISTRICT_PLACE_COLUMNS,
                    "district_code": f"eq.{normalized_code}",
                    "is_active": "eq.true",
                    "order": "content_id.asc",
                    "limit": str(_DISTRICT_PAGE_SIZE),
                    "offset": str(offset),
                },
            )
            payload = self._json(response)
            if not isinstance(payload, list):
                raise SupabaseRepositoryError("invalid places response")
            for row in payload:
                mapped = _to_district_place_row(row)
                if mapped is not None:
                    rows.append(mapped)
            if len(payload) < _DISTRICT_PAGE_SIZE:
                return tuple(rows)
            offset += _DISTRICT_PAGE_SIZE

    async def find_first_photo_urls(
        self,
        content_ids: Sequence[str],
    ) -> dict[str, str]:
        """장소별 첫 사진의 관광공사 원본 주소.

        **비교에 실제로 쓴 사진이다.** `places.first_image_url`과 다르다 —
        2,008곳 중 1,163곳이 서로 다른 주소다(2026-08-27 실측). 대표 이미지를
        보여주면 "우리가 비교한 사진"이 아닌 것을 보여주는 셈이고, 사용자가
        분위기가 맞는지 확인하려는 화면에서 그건 틀린 근거가 된다.

        `photo_order = 1`만 읽는다. detailImage2가 준 순서 그대로이고 관광공사가
        대표성 높은 사진을 앞에 주므로 1이 가장 대표적이다.
        """
        unique_ids = list(dict.fromkeys(content_ids))
        if not unique_ids:
            return {}

        urls: dict[str, str] = {}
        for start in range(0, len(unique_ids), _MOOD_PROFILE_CHUNK_SIZE):
            chunk = unique_ids[start : start + _MOOD_PROFILE_CHUNK_SIZE]
            response = await self._request(
                "GET",
                "/place_image_embeddings",
                params={
                    "select": "content_id,origin_url",
                    "content_id": "in.(" + ",".join(chunk) + ")",
                    "photo_order": "eq.1",
                    "limit": str(_MOOD_PROFILE_CHUNK_SIZE),
                },
            )
            payload = self._json(response)
            if not isinstance(payload, list):
                raise SupabaseRepositoryError("invalid place_image_embeddings response")
            for row in payload:
                if isinstance(row, Mapping) and row.get("origin_url"):
                    urls[str(row["content_id"])] = str(row["origin_url"])
        return urls

    async def find_place_photos(
        self,
        content_ids: Sequence[str],
    ) -> dict[str, tuple[PlacePhoto, ...]]:
        """장소별 사진 목록. 상세 화면이 여러 장을 보여주기 위해 읽는다.

        ``find_first_photo_urls``와 같은 테이블을 읽지만 쓰임이 다르다. 그쪽은
        "우리가 비교에 쓴 사진"을 사진 검색 결과에 근거로 붙이는 용도라 첫 장만
        읽는다. 이쪽은 장소를 둘러보라고 보여주는 용도라 여러 장을 읽는다.

        ``photo_order`` 오름차순으로 돌려준다 — 관광공사가 대표성 높은 사진을
        앞에 주므로 이 순서가 곧 보여줄 순서다.

        장소당 앞에서 ``_PLACE_PHOTO_LIMIT``장까지만 남긴다. 번호가 그 이하인
        것을 고르는 게 아니라 정렬한 뒤 앞에서 세는 것이다 — 빈 번호가 있는
        장소에서 두 판정이 갈린다(상수 주석 참고).

        사진이 없는 장소는 **키 자체가 없다.** 빈 튜플로 자리를 채워 주면 호출
        측이 "사진이 없는 장소"와 "조회하지 않은 장소"를 구분하지 못한다.
        """
        unique_ids = list(dict.fromkeys(content_ids))
        if not unique_ids:
            return {}

        photos: dict[str, list[PlacePhoto]] = {}
        for start in range(0, len(unique_ids), _PLACE_PHOTO_CHUNK_SIZE):
            chunk = unique_ids[start : start + _PLACE_PHOTO_CHUNK_SIZE]
            response = await self._request(
                "GET",
                "/place_image_embeddings",
                params={
                    "select": "content_id,photo_order,origin_url,image_name",
                    "content_id": "in.(" + ",".join(chunk) + ")",
                    "order": "content_id.asc,photo_order.asc",
                    "limit": str(len(chunk) * _PLACE_PHOTO_ROW_BUDGET),
                },
            )
            payload = self._json(response)
            if not isinstance(payload, list):
                raise SupabaseRepositoryError("invalid place_image_embeddings response")
            for row in payload:
                photo = _to_place_photo(row)
                if photo is not None:
                    photos.setdefault(photo.content_id, []).append(photo)
        return {
            content_id: tuple(
                sorted(rows, key=lambda photo: photo.photo_order)[:_PLACE_PHOTO_LIMIT]
            )
            for content_id, rows in photos.items()
        }

    async def create_sync_run(self, area_code: str, district_code: str) -> UUID:
        response = await self._request(
            "POST",
            "/place_sync_runs",
            json={"area_code": area_code, "district_code": district_code},
            prefer="return=representation",
        )
        payload = self._json(response)
        if not isinstance(payload, list) or len(payload) != 1:
            raise SupabaseRepositoryError("invalid sync run insert response")
        row = payload[0]
        if not isinstance(row, Mapping) or not row.get("id"):
            raise SupabaseRepositoryError("sync run response missing id")
        try:
            return UUID(str(row["id"]))
        except ValueError:
            raise SupabaseRepositoryError("invalid sync run id") from None

    async def try_acquire_sync_lock(
        self,
        area_code: str,
        district_code: str,
        sync_run_id: UUID,
        lock_ttl: str = "2 hours",
    ) -> bool:
        response = await self._request(
            "POST",
            "/rpc/try_acquire_place_sync_lock",
            json={
                "p_area_code": area_code,
                "p_district_code": district_code,
                "p_sync_run_id": str(sync_run_id),
                "p_lock_ttl": lock_ttl,
            },
        )
        payload = self._json(response)
        if not isinstance(payload, bool):
            raise SupabaseRepositoryError("invalid acquire lock response")
        return payload

    async def release_sync_lock(
        self,
        area_code: str,
        district_code: str,
        sync_run_id: UUID,
    ) -> bool:
        response = await self._request(
            "POST",
            "/rpc/release_place_sync_lock",
            json={
                "p_area_code": area_code,
                "p_district_code": district_code,
                "p_sync_run_id": str(sync_run_id),
            },
        )
        payload = self._json(response)
        if not isinstance(payload, bool):
            raise SupabaseRepositoryError("invalid release lock response")
        return payload

    async def abandon_sync_run(
        self,
        sync_run_id: UUID,
        *,
        reason: str,
        completed_at: datetime,
    ) -> bool:
        """running으로 남은 실행을 failed로 마감한다. 이미 끝난 실행은 건드리지 않는다.

        잠금을 손으로 풀 때 함께 쓴다. 잠금만 지우고 실행을 running으로 두면 이력이
        영영 "진행 중"으로 남아, 나중에 통계와 화면이 실제와 어긋난다(2026-08-29
        강동구 사고).

        카운터는 손대지 않는다 — 어디까지 처리했는지는 죽은 프로세스만 알고, 여기서
        0이나 임의 값으로 덮으면 실제로 적재된 행과 어긋난 기록이 남는다.
        status=eq.running 조건을 함께 걸어, 그 사이 정상 종료된 실행을 덮어쓰지
        않는다.
        """
        response = await self._request(
            "PATCH",
            "/place_sync_runs",
            params={"id": f"eq.{sync_run_id}", "status": "eq.running"},
            json={
                "status": "failed",
                "completed_at": _iso(completed_at),
                "error_summary": {"SYNC_LOCK_RELEASED": 1, "note": reason},
            },
            prefer="return=representation",
        )
        payload = self._json(response)
        return isinstance(payload, list) and len(payload) > 0

    async def delete_sync_lock(
        self,
        area_code: str,
        district_code: str,
        sync_run_id: UUID,
    ) -> bool:
        """잠금을 지운다. sync_run_id가 일치할 때만 지운다.

        RPC(`release_place_sync_lock`)는 동기화 파이프라인이 자기 잠금을 정상
        반납할 때 쓰는 경로라 그대로 두고, 손으로 푸는 경로는 여기 따로 둔다.
        소유자를 함께 대조하는 이유는 그 사이 잠금이 만료되고 **다른 실행이 새로
        잡았을 수 있어서다** — 그때 지우면 살아 있는 동기화의 잠금을 뺏는다.
        """
        response = await self._request(
            "DELETE",
            "/place_sync_locks",
            params={
                "area_code": f"eq.{area_code}",
                "district_code": f"eq.{district_code}",
                "sync_run_id": f"eq.{sync_run_id}",
            },
            prefer="return=representation",
        )
        payload = self._json(response)
        return isinstance(payload, list) and len(payload) > 0

    async def get_region_place_states(
        self,
        area_code: str,
        district_code: str,
    ) -> dict[str, StoredPlaceState]:
        rows: list[object] = []
        offset = 0
        while True:
            response = await self._request(
                "GET",
                "/places",
                params={
                    "select": _STATE_COLUMNS,
                    "area_code": f"eq.{area_code}",
                    "district_code": f"eq.{district_code}",
                    "order": "content_id.asc",
                    "limit": str(_READ_PAGE_SIZE),
                    "offset": str(offset),
                },
            )
            page = self._json(response)
            if not isinstance(page, list):
                raise SupabaseRepositoryError("invalid place states response")
            rows.extend(page)
            if len(page) < _READ_PAGE_SIZE:
                break
            offset += _READ_PAGE_SIZE

        states: dict[str, StoredPlaceState] = {}
        for raw in rows:
            if not isinstance(raw, Mapping) or not raw.get("content_id"):
                raise SupabaseRepositoryError("place state missing content_id")
            content_id = str(raw["content_id"])
            states[content_id] = StoredPlaceState(
                content_id=content_id,
                source_modified_at=_parse_datetime(
                    raw.get("source_modified_at"), "source_modified_at"
                ),
                detail_fetched_at=_parse_datetime(
                    raw.get("detail_fetched_at"), "detail_fetched_at"
                ),
                detail_fetch_status=str(raw.get("detail_fetch_status", "")),
                operating_parser_version=str(raw.get("operating_parser_version", "")),
                operating_hours_raw=(
                    str(raw["operating_hours_raw"])
                    if raw.get("operating_hours_raw") is not None
                    else None
                ),
                rest_date_raw=(
                    str(raw["rest_date_raw"]) if raw.get("rest_date_raw") is not None else None
                ),
                is_active=bool(raw.get("is_active")),
                inactive_reason=(
                    str(raw["inactive_reason"]) if raw.get("inactive_reason") is not None else None
                ),
            )
        return states

    async def find_active_places_by_name(self, name: str) -> tuple[StoredPlaceLocation, ...]:
        """TourAPI 기준 장소명을 정확히 일치시켜 검색 중심 좌표를 읽는다.

        장소명 검색은 지오코딩보다 먼저 수행한다. 좌표가 없는 행은 검색 중심점으로
        사용할 수 없으므로 반환하지 않는다. 별칭·부분 일치 정책은 상위 Tool이
        별도 경로로 확장할 수 있도록 이 Repository는 정확 일치만 담당한다.

        다만 공백 유무와 괄호 부기는 표기 차이로 본다. 지역 검색은 "북촌 한옥마을"을
        주는데 저장소에는 "북촌한옥마을"로 들어 있고, 사용자는 "종묘"라고 하는데
        저장소 제목은 "종묘 [유네스코 세계유산]"이다. 정확 일치만 보면 모두 놓친다.
        """
        normalized_name = name.strip()
        if not normalized_name:
            return ()
        title_filters = _title_filters(normalized_name)
        rows = _select_by_filter_priority(
            await self._query_places_by_title(title_filters), title_filters
        )
        if rows:
            return _map_place_locations(rows, fallback_title=normalized_name)
        # 제목으로 못 찾으면 사람이 지정한 별칭을 본다. "창덕궁"은 저장소 제목이
        # "창덕궁과 후원 [유네스코 세계유산]"이라 어떤 제목 규칙으로도 닿지 않는다.
        rows = await self._query_places_by_alias(normalized_name)
        return _map_place_locations(rows, fallback_title=normalized_name)

    async def _query_places_by_alias(self, alias: str) -> list[object]:
        """매핑 별칭으로 장소를 찾는다. 별칭이 없는 장소는 조인에서 빠진다.

        지원 지역(area_code/district_code)을 명시적으로 걸어둔다. 이 메서드가
        읽는 결과는 호출자(resolve_location)가 지역 경계 검사를 건너뛰는
        경로라(D-044, "저장소에서 해석된 장소는 이미 지원 구로 등록된 것") —
        지원하지 않는 구의 데이터가 섞이는 순간 그 전제가 깨진다. RAG 실험 등으로
        다른 구 장소가 이 테이블에 들어와도 실제 서비스 동작이 조용히 바뀌지
        않도록 여기서 필터로 막아둔다.
        """
        response = await self._request(
            "GET",
            "/places",
            params={
                "select": _LOCATION_COLUMNS_INNER,
                "is_active": "eq.true",
                "area_code": f"eq.{PLACE_SEARCH_LDONG_REGION_CODE}",
                "district_code": _SUPPORTED_DISTRICT_FILTER,
                "place_concentration_mappings.concentration_aliases": f"cs.{{{alias}}}",
                "limit": "2",
            },
        )
        rows = self._json(response)
        if not isinstance(rows, list):
            raise SupabaseRepositoryError("invalid place location response")
        return rows

    async def _query_places_by_title(self, title_filters: Sequence[str]) -> list[object]:
        """제목 필터 전부를 or= 하나로 던진다. 우선순위 판정은 호출자가 한다.

        limit이 사다리 시절의 2가 아닌 이유: 그때는 한 칸이 자기 필터에 걸린 행만
        받았지만, 지금은 여러 칸의 결과가 섞여 온다. 2로 자르면 뒤쪽 칸의 행이
        앞자리를 차지해 정작 정확 일치를 못 받는 일이 생긴다. 넉넉히 받아
        `_select_by_filter_priority`가 고르고, 거기서 다시 2건으로 줄인다.
        """
        # _query_places_by_alias와 같은 이유로 지원 지역을 명시적으로 건다.
        response = await self._request(
            "GET",
            "/places",
            params={
                "select": _LOCATION_COLUMNS,
                "or": _title_or_filter(title_filters),
                "is_active": "eq.true",
                "area_code": f"eq.{PLACE_SEARCH_LDONG_REGION_CODE}",
                "district_code": _SUPPORTED_DISTRICT_FILTER,
                # 한 칸에 이만큼 걸리면 어차피 모호해서 되묻기로 간다.
                "limit": str(_TITLE_QUERY_ROW_LIMIT),
                # 상한에 걸릴 때 어떤 행이 잘리는지가 호출마다 달라지지 않게 한다.
                "order": "content_id",
            },
        )
        rows = self._json(response)
        if not isinstance(rows, list):
            raise SupabaseRepositoryError("invalid place location response")
        return rows

    async def find_concentration_mapped_places(self) -> tuple[StoredPlaceLocation, ...]:
        """집중률 매핑이 있는 활성 장소를 좌표와 함께 모두 읽는다.

        매핑에 있는 장소는 집중률 API에 데이터가 존재한다는 뜻이다. INFO 질의에서
        대상 장소의 직접 데이터가 없을 때, 여기서 가장 가까운 곳을 대체 기준으로
        쓰면 "가까운 곳을 골랐는데 집중률이 없더라"를 구조적으로 피할 수 있다.
        PostgREST가 거리 정렬을 지원하지 않아 전체를 읽고 호출자가 계산한다.

        페이지로 나눠 읽는다. 매핑이 101건인 지금은 첫 요청 한 번으로 끝나지만,
        한 번만 읽으면 상한을 넘는 순간 오류 없이 뒷부분이 잘린다 — 대체 후보가
        조용히 사라져 "가까운 곳이 있는데 no_data"가 된다. 확장 구 매핑을
        채우면(TP-136) 건수가 늘어난다.
        """
        rows: list[object] = []
        offset = 0
        while True:
            response = await self._request(
                "GET",
                "/places",
                params={
                    "select": _LOCATION_COLUMNS,
                    "is_active": "eq.true",
                    # 내부 조인으로 매핑이 있는 장소만 남긴다.
                    "place_concentration_mappings": "not.is.null",
                    # 정렬이 없으면 페이지마다 순서가 달라져 같은 행이 두 번 오거나
                    # 아예 빠질 수 있다.
                    "order": "content_id.asc",
                    "limit": str(_READ_PAGE_SIZE),
                    "offset": str(offset),
                },
            )
            page = self._json(response)
            if not isinstance(page, list):
                raise SupabaseRepositoryError("invalid concentration mapping response")
            rows.extend(page)
            if len(page) < _READ_PAGE_SIZE:
                break
            offset += _READ_PAGE_SIZE

        return tuple(
            location
            for location in _map_place_locations(rows, fallback_title="")
            if location.concentration_name
        )

    async def get_active_place_details(
        self,
        content_ids: Sequence[str],
        *,
        include_barrier_free: bool = False,
    ) -> dict[str, StoredPlaceDetail]:
        """활성 상태인 장소들의 상세·운영정보를 content_id 기준으로 한 번에 읽는다.

        조회되지 않은 content_id는 결과에 포함되지 않는다. content_id가 URL 길이
        제한을 넘지 않도록 청크로 나눠 요청한다.

        include_barrier_free가 참이면 place_barrier_free를 임베드로 함께 읽는다.
        기본값이 거짓인 이유는 이 메서드를 부르는 세 곳 중 둘(추천 카드 조립,
        추천 후보 상세)이 무장애 값을 읽지 않기 때문이다 — 항상 붙이면 그 두 경로가
        쓰지도 않는 데이터를 매 요청마다 받아온다.
        """
        if not content_ids:
            return {}

        select = _DETAIL_COLUMNS
        if include_barrier_free:
            select = f"{select},place_barrier_free({_BARRIER_FREE_COLUMNS})"

        details: dict[str, StoredPlaceDetail] = {}
        for chunk in _chunks(list(content_ids), _UPSERT_CHUNK_SIZE):
            quoted_ids = ",".join(f'"{content_id}"' for content_id in chunk)
            response = await self._request(
                "GET",
                "/places",
                params={
                    "select": select,
                    "content_id": f"in.({quoted_ids})",
                    "is_active": "eq.true",
                },
            )
            rows = self._json(response)
            if not isinstance(rows, list):
                raise SupabaseRepositoryError("invalid place details response")
            for raw in rows:
                if not isinstance(raw, Mapping) or not raw.get("content_id"):
                    raise SupabaseRepositoryError("place detail missing content_id")
                content_id = str(raw["content_id"])
                details[content_id] = StoredPlaceDetail(
                    content_id=content_id,
                    content_type_id=str(raw.get("content_type_id") or ""),
                    title=_optional_text(raw.get("title")),
                    address=_optional_text(raw.get("address")),
                    latitude=_optional_float(raw.get("latitude")),
                    longitude=_optional_float(raw.get("longitude")),
                    operating_hours_raw=_optional_text(raw.get("operating_hours_raw")),
                    rest_date_raw=_optional_text(raw.get("rest_date_raw")),
                    detail_fetch_status=str(raw.get("detail_fetch_status") or ""),
                    detail_fetched_at=_parse_datetime(
                        raw.get("detail_fetched_at"), "detail_fetched_at"
                    ),
                    source_modified_at=_parse_datetime(
                        raw.get("source_modified_at"), "source_modified_at"
                    ),
                    lcls_systm1=_optional_text(raw.get("lcls_systm1")),
                    lcls_systm2=_optional_text(raw.get("lcls_systm2")),
                    lcls_systm3=_optional_text(raw.get("lcls_systm3")),
                    parking_info_raw=_optional_text(raw.get("parking_info_raw")),
                    parking_fee_raw=_optional_text(raw.get("parking_fee_raw")),
                    first_image_url=_optional_text(raw.get("first_image_url")),
                    thumbnail_url=_optional_text(raw.get("thumbnail_url")),
                    use_fee_raw=_optional_text(raw.get("use_fee_raw")),
                    info_center_raw=_optional_text(raw.get("info_center_raw")),
                    baby_carriage_raw=_optional_text(raw.get("baby_carriage_raw")),
                    pet_raw=_optional_text(raw.get("pet_raw")),
                    credit_card_raw=_optional_text(raw.get("credit_card_raw")),
                    restroom_raw=_optional_text(raw.get("restroom_raw")),
                    operating_schedule_raw=(
                        raw.get("operating_schedule")
                        if isinstance(raw.get("operating_schedule"), dict)
                        else None
                    ),
                    operating_parser_version=_optional_text(raw.get("operating_parser_version")),
                    **_barrier_free_fields(raw.get("place_barrier_free")),
                )
        return details

    async def upsert_place_list(
        self,
        places: Sequence[TourPlaceRecord],
        existing_states: Mapping[str, StoredPlaceState],
        sync_run_id: UUID,
        fetched_at: datetime,
    ) -> None:
        fetched_at_text = _iso(fetched_at)
        payloads: list[dict[str, object]] = []
        for place in places:
            row: dict[str, object] = {
                "content_id": place.content_id,
                "content_type_id": place.content_type_id,
                "title": place.title,
                "address": place.address,
                "latitude": place.latitude,
                "longitude": place.longitude,
                "area_code": place.area_code,
                "district_code": place.district_code,
                "lcls_systm1": place.lcls_systm1,
                "lcls_systm2": place.lcls_systm2,
                "lcls_systm3": place.lcls_systm3,
                "source_modified_at": _iso(place.source_modified_at),
                # 이미지는 목록 응답에서 오므로 상세조회 성패와 무관하게 갱신된다(D-056).
                "first_image_url": place.first_image_url,
                "thumbnail_url": place.thumbnail_url,
                "list_fetched_at": fetched_at_text,
                "last_seen_at": fetched_at_text,
                "last_sync_run_id": str(sync_run_id),
            }
            previous = existing_states.get(place.content_id)
            if previous is None:
                # `existing_states`는 이 구의 장소만 담는다. 그래서 "처음 보는 장소"와
                # "다른 구에 이미 있는 장소"가 여기서 구분되지 않는다 — 장소는 구를
                # 옮겨 다닌다(2026-08-30 "2025 제17회 서울건축문화제"가 종로구에서
                # 사라져 비활성이 된 뒤 중구 목록에 나타났다).
                #
                # 그런 행에 is_active만 켜면 inactive_reason이 남은 채로 활성이 되어
                # places_active_state_valid를 어기고, upsert가 400으로 튕겨 그 청크
                # 전체가 실패한다. detail_fetch_status도 같다 — failed였던 행을
                # pending으로 바꾸면서 detail_error_code를 안 지우면
                # places_detail_error_matches_status를 어긴다.
                #
                # 상태를 통째로 새 장소의 것으로 맞춘다. 행이 없으면 어차피 null이라
                # 무해하고, 다른 구에 있던 행이면 새 구에서 되살아나는 것이 맞다.
                row.update(
                    {
                        "detail_fetch_status": "pending",
                        "detail_error_code": None,
                        "is_active": True,
                        "inactive_reason": None,
                        "inactive_at": None,
                    }
                )
            elif previous.inactive_reason == "missing_from_source":
                row.update(
                    {
                        "is_active": True,
                        "inactive_reason": None,
                        "inactive_at": None,
                    }
                )
            payloads.append(row)

        payload_groups: dict[tuple[str, ...], list[dict[str, object]]] = {}
        for payload in payloads:
            key_shape = tuple(sorted(payload))
            payload_groups.setdefault(key_shape, []).append(payload)

        for group in payload_groups.values():
            for chunk in _chunks(group, _UPSERT_CHUNK_SIZE):
                await self._request(
                    "POST",
                    "/places",
                    params={"on_conflict": "content_id"},
                    json=list(chunk),
                    prefer="resolution=merge-duplicates,return=minimal",
                )

    async def update_operating_details(
        self,
        content_id: str,
        operating_hours_raw: str | None,
        rest_date_raw: str | None,
        operating_schedule: Mapping[str, object] | None,
        parse_status: str,
        parser_version: str,
        fetched_at: datetime,
        parking_info_raw: str | None = None,
        parking_fee_raw: str | None = None,
        use_fee_raw: str | None = None,
        discount_info_raw: str | None = None,
        info_center_raw: str | None = None,
        baby_carriage_raw: str | None = None,
        pet_raw: str | None = None,
        credit_card_raw: str | None = None,
        restroom_raw: str | None = None,
    ) -> None:
        if parse_status not in _VALID_PARSE_STATUSES:
            raise ValueError("유효하지 않은 parse_status입니다.")
        # detail_fetch_status 판정에는 주차·요금·안내처를 넣지 않는다. 넣으면 운영시간이
        # 없고 주차만 있는 장소가 empty에서 success로 바뀌어 재조회 주기가 달라진다 —
        # 이 컬럼은 운영정보 확보 여부를 뜻하므로 기존 의미를 유지한다(D-056).
        detail_status = (
            "empty" if operating_hours_raw is None and rest_date_raw is None else "success"
        )
        await self._request(
            "PATCH",
            "/places",
            params={"content_id": f"eq.{content_id}"},
            json={
                "operating_hours_raw": operating_hours_raw,
                "rest_date_raw": rest_date_raw,
                "parking_info_raw": parking_info_raw,
                "parking_fee_raw": parking_fee_raw,
                "use_fee_raw": use_fee_raw,
                "discount_info_raw": discount_info_raw,
                "info_center_raw": info_center_raw,
                "baby_carriage_raw": baby_carriage_raw,
                "pet_raw": pet_raw,
                "credit_card_raw": credit_card_raw,
                "restroom_raw": restroom_raw,
                "operating_schedule": operating_schedule,
                "operating_parse_status": parse_status,
                "operating_parser_version": parser_version,
                "detail_fetched_at": _iso(fetched_at),
                "detail_fetch_status": detail_status,
                "detail_error_code": None,
            },
            prefer="return=minimal",
        )

    async def mark_detail_failed(self, content_id: str, error_code: str) -> None:
        await self._request(
            "PATCH",
            "/places",
            params={"content_id": f"eq.{content_id}"},
            json={
                "detail_fetch_status": "failed",
                "detail_error_code": error_code,
            },
            prefer="return=minimal",
        )

    async def update_parsed_schedule(
        self,
        content_id: str,
        operating_schedule: Mapping[str, object] | None,
        parse_status: str,
        parser_version: str,
    ) -> None:
        if parse_status not in _VALID_PARSE_STATUSES:
            raise ValueError("유효하지 않은 parse_status입니다.")
        await self._request(
            "PATCH",
            "/places",
            params={"content_id": f"eq.{content_id}"},
            json={
                "operating_schedule": operating_schedule,
                "operating_parse_status": parse_status,
                "operating_parser_version": parser_version,
            },
            prefer="return=minimal",
        )

    async def reactivate_source_missing_places(
        self,
        content_ids: Sequence[str],
    ) -> int:
        if not content_ids:
            return 0
        changed = 0
        for chunk in _chunks(content_ids, _UPSERT_CHUNK_SIZE):
            quoted_ids = ",".join(f'"{content_id}"' for content_id in chunk)
            response = await self._request(
                "PATCH",
                "/places",
                params={
                    "content_id": f"in.({quoted_ids})",
                    "inactive_reason": "eq.missing_from_source",
                },
                json={
                    "is_active": True,
                    "inactive_reason": None,
                    "inactive_at": None,
                },
                prefer="return=representation",
            )
            payload = self._json(response)
            if not isinstance(payload, list):
                raise SupabaseRepositoryError("invalid reactivate response")
            changed += len(payload)
        return changed

    async def deactivate_unseen_places(
        self,
        area_code: str,
        district_code: str,
        sync_run_id: UUID,
        inactive_at: datetime,
    ) -> int:
        response = await self._request(
            "PATCH",
            "/places",
            params={
                "area_code": f"eq.{area_code}",
                "district_code": f"eq.{district_code}",
                "is_active": "eq.true",
                "or": (f"(last_sync_run_id.neq.{sync_run_id},last_sync_run_id.is.null)"),
            },
            json={
                "is_active": False,
                "inactive_reason": "missing_from_source",
                "inactive_at": _iso(inactive_at),
            },
            prefer="return=representation",
        )
        payload = self._json(response)
        if not isinstance(payload, list):
            raise SupabaseRepositoryError("invalid deactivate response")
        return len(payload)

    async def complete_sync_run(
        self,
        sync_run_id: UUID,
        *,
        status: str,
        api_total_count: int | None,
        processed_count: int,
        success_count: int,
        failed_count: int,
        new_count: int,
        updated_count: int,
        deactivated_count: int,
        detail_attempted_count: int,
        error_summary: Mapping[str, object] | None = None,
        completed_at: datetime,
    ) -> None:
        """실행을 종료 상태로 마감한다. 이미 마감된 실행은 덮어쓰지 않는다.

        `status=eq.running` 조건을 함께 건다. 개발자 패널이 잠금을 강제 해제하면서
        이 실행을 failed로 마감했을 수 있는데(abandon_sync_run), 그때 이 프로세스는
        자기 잠금이 사라진 것을 모른 채 계속 돌다가 정상 종료한다. 조건이 없으면
        여기서 status를 success로 덮어써 강제 해제 흔적이 지워지고, 화면에는 아무
        일도 없었던 것처럼 보인다.

        동기화 자체를 멈추지는 못한다 — 잠금은 시작할 때 한 번 잡고 도는 동안
        다시 확인하지 않는다. 여기서 막는 것은 "무슨 일이 있었는지가 기록에서
        사라지는 것"까지다.
        """
        if status not in _VALID_RUN_STATUSES:
            raise ValueError("유효하지 않은 동기화 종료 상태입니다.")
        await self._request(
            "PATCH",
            "/place_sync_runs",
            params={"id": f"eq.{sync_run_id}", "status": "eq.running"},
            json={
                "status": status,
                "api_total_count": api_total_count,
                "processed_count": processed_count,
                "success_count": success_count,
                "failed_count": failed_count,
                "new_count": new_count,
                "updated_count": updated_count,
                "deactivated_count": deactivated_count,
                # 일일 한도 판단의 근거가 되는 값이라 실행 기록에 남긴다. 메모리
                # 집계는 재시작하면 사라지고 스크립트 실행분도 놓친다.
                "detail_attempted_count": detail_attempted_count,
                "error_summary": error_summary,
                "completed_at": _iso(completed_at),
            },
            prefer="return=minimal",
        )

    # --- 개발자 Ops 패널 조회 전용 --------------------------------------------
    # 동기화 파이프라인은 쓰지 않는다. 여기 값이 틀려도 동기화 판정은 달라지지 않는다.

    async def count_rows(self, table: str, filters: Mapping[str, str] | None = None) -> int:
        """PostgREST Content-Range로 행 수만 받는다(본문 전송 없음)."""
        response = await self._request(
            "HEAD",
            f"/{table}",
            params={**(filters or {}), "select": "*"},
            prefer="count=exact",
        )
        total = response.headers.get("content-range", "").partition("/")[2]
        try:
            return int(total)
        except ValueError:
            raise SupabaseRepositoryError(f"invalid count for {table}") from None

    async def get_place_summaries_by_district(self) -> dict[str, object]:
        """적재된 구별 요약과 전 구 합계를 한 번의 페이징으로 만든다.

        구마다 따로 질의하면 왕복이 구 수만큼 늘고, 그보다 먼저 "어떤 구가 적재돼
        있는가"를 알아야 질의를 만들 수 있다. 그 목록을 코드에 박으면 구를 새로
        넣을 때마다 적재와 조회 두 곳을 고쳐야 한다. 전량을 한 번 훑어
        (area_code, district_code)로 묶으면 적재된 구가 결과에서 그대로 드러난다.

        전량이라 해도 세 개 구 2,300행 남짓에 상태·분류·제목 열만 읽으므로,
        상태별로 count 질의를 나누는 것보다 싸다.
        """
        rows = await self._fetch_place_summary_rows()

        grouped: dict[tuple[str, str], list[Mapping[str, object]]] = {}
        for row in rows:
            key = (
                str(row.get("area_code") or ""),
                str(row.get("district_code") or ""),
            )
            grouped.setdefault(key, []).append(row)

        registry = get_tour_category_registry()
        return {
            "overall": {
                **_summarize_places(rows),
                "category_coverage": _summarize_category_coverage(rows, registry),
            },
            "districts": [
                {
                    "area_code": area_code,
                    "district_code": district_code,
                    **_summarize_places(group),
                    "category_coverage": _summarize_category_coverage(group, registry),
                }
                for (area_code, district_code), group in sorted(grouped.items())
            ],
        }

    async def _fetch_place_summary_rows(self) -> list[Mapping[str, object]]:
        """상태·분류·제목 요약에 필요한 열만 전량 받는다. PostgREST는 한 응답에 1000행까지다."""
        rows: list[Mapping[str, object]] = []
        offset = 0
        while True:
            response = await self._request(
                "GET",
                "/places",
                params={
                    "select": _PLACE_SUMMARY_COLUMNS,
                    "order": "content_id.asc",
                    "limit": str(_READ_PAGE_SIZE),
                    "offset": str(offset),
                },
            )
            page = self._json(response)
            if not isinstance(page, list):
                raise SupabaseRepositoryError("invalid place summary response")
            for raw in page:
                if not isinstance(raw, Mapping):
                    raise SupabaseRepositoryError("invalid place summary row")
                rows.append(raw)
            if len(page) < _READ_PAGE_SIZE:
                break
            offset += _READ_PAGE_SIZE
        return rows

    async def list_region_place_rows(
        self,
        area_code: str,
        district_code: str,
        columns: Sequence[str],
        *,
        active_only: bool = True,
    ) -> list[Mapping[str, object]]:
        """구 하나의 장소 행을 요청한 열만 페이징해 전부 읽는다.

        열 목록을 호출자가 준다 — 이 저장소가 스냅샷 CSV 형식을 알 필요가 없고,
        형식을 아는 쪽(`place_snapshot.SNAPSHOT_COLUMNS`)에 열 정의가 하나만 남는다.

        기본은 활성 장소만이다. 비활성 장소는 목록에서 사라져서 비활성이 된 것이라,
        스냅샷에 넣으면 대조할 때마다 계속 "삭제"로 잡힌다.
        """
        params_base: dict[str, str] = {
            "select": ",".join(columns),
            "area_code": f"eq.{area_code}",
            "district_code": f"eq.{district_code}",
            "order": "content_id.asc",
        }
        if active_only:
            params_base["is_active"] = "eq.true"

        rows: list[Mapping[str, object]] = []
        offset = 0
        while True:
            response = await self._request(
                "GET",
                "/places",
                params={
                    **params_base,
                    "limit": str(_READ_PAGE_SIZE),
                    "offset": str(offset),
                },
            )
            page = self._json(response)
            if not isinstance(page, list):
                raise SupabaseRepositoryError("invalid place row response")
            for raw in page:
                if not isinstance(raw, Mapping):
                    raise SupabaseRepositoryError("invalid place row")
                rows.append(raw)
            if len(page) < _READ_PAGE_SIZE:
                break
            offset += _READ_PAGE_SIZE
        return rows

    async def list_active_place_rows_by_ids(
        self, content_ids: Sequence[str], columns: Sequence[str]
    ) -> list[Mapping[str, object]]:
        """활성 장소를 ID로 읽되 호출자가 요청한 순서를 보존한다."""
        unique_ids = list(dict.fromkeys(content_ids))
        if not unique_ids:
            return []
        rows_by_id: dict[str, Mapping[str, object]] = {}
        for chunk in _chunks(unique_ids, _UPSERT_CHUNK_SIZE):
            quoted_ids = ",".join(f'"{content_id}"' for content_id in chunk)
            response = await self._request(
                "GET",
                "/places",
                params={
                    "select": ",".join(columns),
                    "content_id": f"in.({quoted_ids})",
                    "is_active": "eq.true",
                },
            )
            payload = self._json(response)
            if not isinstance(payload, list):
                raise SupabaseRepositoryError("invalid place row response")
            for raw in payload:
                if not isinstance(raw, Mapping) or not raw.get("content_id"):
                    raise SupabaseRepositoryError("place row missing content_id")
                rows_by_id[str(raw["content_id"])] = raw
        return [rows_by_id[content_id] for content_id in unique_ids if content_id in rows_by_id]

    async def list_detail_backfill_ids(self, area_code: str, district_code: str) -> list[str]:
        """상세 정보를 아직 못 채운 장소의 content_id.

        동기화는 대조가 정한 변경분 외에 이 장소들도 함께 부른다
        (`PlaceSyncService._select_targets`) — 빼면 pending·failed가 영영 그대로
        남기 때문이다. 그래서 "이번 반영이 상세조회를 몇 번 쓰는가"를 계산하려면
        변경분만으로는 모자라고 이 목록이 필요하다.

        `empty`는 넣지 않는다. TourAPI가 상세를 주지 않는 장소라 다시 불러도 계속
        비어 있고, 대상에 넣으면 매번 같은 호출을 반복하게 된다.
        """
        response = await self._request(
            "GET",
            "/places",
            params={
                "select": "content_id",
                "area_code": f"eq.{area_code}",
                "district_code": f"eq.{district_code}",
                "detail_fetch_status": "in.(pending,failed)",
                "order": "content_id.asc",
            },
        )
        payload = self._json(response)
        if not isinstance(payload, list):
            raise SupabaseRepositoryError("invalid detail backfill response")
        ids: list[str] = []
        for row in payload:
            if not isinstance(row, Mapping) or not row.get("content_id"):
                raise SupabaseRepositoryError("detail backfill row missing content_id")
            ids.append(str(row["content_id"]))
        return ids

    async def summarize_detail_calls_since(self, since: datetime) -> dict[str, int]:
        """`since` 이후 시작한 동기화가 부른 detailIntro2 수를 더한다.

        detailIntro2를 부르는 코드는 PlaceSyncService 한 곳뿐이고(추천 경로는 DB에서
        읽는다) 그 경로는 실행마다 place_sync_runs 행을 남긴다. 그래서 호출마다
        카운터를 올리지 않고도 오늘 사용량을 셀 수 있다. 프로세스 메모리 집계와
        달리 서버를 재시작해도 남고, backend/scripts로 돈 실행분도 함께 잡힌다.

        **하한이다.** 재시도는 한 장소를 여러 번 부르지만 여기 세는 것은 장소 수고,
        중간에 죽어 완료 처리를 못 한 실행은 열이 비어 있다. 그래서 비어 있는 실행
        수(`runs_without_count`)를 함께 돌려준다 — 화면이 "관측한 값"과 "재지 못한
        구간"을 구분해 보여줄 수 있어야 한다.
        """
        response = await self._request(
            "GET",
            "/place_sync_runs",
            params={
                "select": "detail_attempted_count",
                "started_at": f"gte.{_iso(since)}",
            },
        )
        payload = self._json(response)
        if not isinstance(payload, list):
            raise SupabaseRepositoryError("invalid sync run summary response")

        total = 0
        runs = 0
        runs_without_count = 0
        for row in payload:
            if not isinstance(row, Mapping):
                raise SupabaseRepositoryError("invalid sync run summary row")
            runs += 1
            value = row.get("detail_attempted_count")
            if value is None:
                runs_without_count += 1
                continue
            total += int(value)
        return {
            "count": total,
            "runs": runs,
            "runs_without_count": runs_without_count,
        }

    async def list_recent_sync_runs(self, limit: int = 10) -> list[dict[str, object]]:
        response = await self._request(
            "GET",
            "/place_sync_runs",
            params={
                "select": "*",
                "order": "started_at.desc",
                "limit": str(limit),
            },
        )
        payload = self._json(response)
        if not isinstance(payload, list):
            raise SupabaseRepositoryError("invalid sync run list response")
        return [dict(row) for row in payload if isinstance(row, Mapping)]

    async def find_missing_concentration_mappings(self, content_ids: Sequence[str]) -> list[str]:
        """집중률 매핑이 없는 content_id를 가려낸다.

        매핑이 없는 장소는 혼잡도 조회를 **아예 하지 않고** no_data로 끝난다
        (`enrichment_service._enrich_candidate`). 동기화로 새로 들어온 장소는
        매핑이 당연히 없으므로, 알리지 않으면 그 장소만 조용히 혼잡도 판정에서
        빠진 채로 남는다. 매핑 적재는 별도 스크립트 소관이라 여기서는 사실만
        확인한다.
        """
        if not content_ids:
            return []
        found: set[str] = set()
        for chunk in _chunks(list(content_ids), _UPSERT_CHUNK_SIZE):
            quoted = ",".join(f'"{content_id}"' for content_id in chunk)
            response = await self._request(
                "GET",
                "/place_concentration_mappings",
                params={"select": "content_id", "content_id": f"in.({quoted})"},
            )
            payload = self._json(response)
            if not isinstance(payload, list):
                raise SupabaseRepositoryError("invalid concentration mapping response")
            for row in payload:
                if isinstance(row, Mapping) and row.get("content_id"):
                    found.add(str(row["content_id"]))
        return [content_id for content_id in content_ids if content_id not in found]

    async def count_barrier_free_by_district(self) -> dict[tuple[str, str], dict[str, int]]:
        """구별 무장애 행 수. `places`를 함께 읽어 활성 여부까지 센다.

        place_barrier_free에는 구 열이 없다(content_id 기준). place_enrichments처럼
        전체 건수만 세지 않고 구별로 쪼개는 이유는, 이 테이블은 장소 동기화가 구
        단위로 채우기 때문이다 — "이 구는 무장애를 채웠나"가 패널에서 답해야 할
        질문이다.

        행 수가 적어(4개 구를 다 채워도 430행) places 전량 요약과 달리 한 번에
        읽는다. 목록에 없는 장소는 행을 만들지 않으므로 places보다 훨씬 적다.
        """
        counts: dict[tuple[str, str], dict[str, int]] = {}
        offset = 0
        while True:
            response = await self._request(
                "GET",
                "/place_barrier_free",
                params={
                    "select": "content_id,places!inner(area_code,district_code,is_active)",
                    "order": "content_id.asc",
                    "limit": str(_READ_PAGE_SIZE),
                    "offset": str(offset),
                },
            )
            page = self._json(response)
            if not isinstance(page, list):
                raise SupabaseRepositoryError("invalid barrier free summary response")
            for raw in page:
                place = raw.get("places") if isinstance(raw, Mapping) else None
                if not isinstance(place, Mapping):
                    raise SupabaseRepositoryError("invalid barrier free summary row")
                key = (
                    str(place.get("area_code") or ""),
                    str(place.get("district_code") or ""),
                )
                bucket = counts.setdefault(key, {"active": 0, "total": 0})
                bucket["total"] += 1
                if place.get("is_active"):
                    bucket["active"] += 1
            if len(page) < _READ_PAGE_SIZE:
                return counts
            offset += _READ_PAGE_SIZE

    async def list_barrier_free_fetched_at(self, content_ids: Sequence[str]) -> dict[str, datetime]:
        """무장애 정보를 언제 확인했는지. 행이 없는 장소는 결과에도 없다.

        값이 비어 있는 행도 "확인했다"로 센다 — 무장애 목록에 있는데도 15개 필드가
        모두 빈 장소가 496건 중 60건이라, 값이 없다고 다시 부르면 그 60건에 매번
        호출을 태우게 된다.
        """
        if not content_ids:
            return {}
        fetched: dict[str, datetime] = {}
        for chunk in _chunks(list(content_ids), _UPSERT_CHUNK_SIZE):
            quoted = ",".join(f'"{content_id}"' for content_id in chunk)
            response = await self._request(
                "GET",
                "/place_barrier_free",
                params={
                    "select": "content_id,fetched_at",
                    "content_id": f"in.({quoted})",
                },
            )
            payload = self._json(response)
            if not isinstance(payload, list):
                raise SupabaseRepositoryError("invalid barrier free response")
            for row in payload:
                if not isinstance(row, Mapping):
                    continue
                content_id = _optional_text(row.get("content_id"))
                fetched_at = _parse_datetime(row.get("fetched_at"), "fetched_at")
                if content_id is not None and fetched_at is not None:
                    fetched[content_id] = fetched_at
        return fetched

    async def upsert_barrier_free_details(
        self,
        details: Sequence[PlaceBarrierFreeDetails],
        fetched_at: datetime,
    ) -> None:
        """무장애 상세를 반영한다. 부른 장소만 행이 된다.

        값이 전부 비어 있어도 행을 만든다. 무장애 목록에 있는데 28개 필드가 모두 빈
        장소가 4개 구에서 60건이고(전부 쇼핑몰 입점 매장, 2022·2024년 일괄 등록),
        행이 없으면 그 장소들을 실행할 때마다 다시 부르게 된다.

        반대로 목록에 없는 장소는 행을 만들지 않는다. 없다는 사실은 목록이 매번
        알려주므로 저장할 이유가 없다.
        """
        if not details:
            return
        fetched_at_text = _iso(fetched_at)
        rows: list[dict[str, object]] = []
        for detail in details:
            row: dict[str, object] = {
                "content_id": detail.content_id,
                "fetched_at": fetched_at_text,
            }
            for field_name, value in vars(detail).items():
                if field_name != "content_id":
                    row[field_name] = value
            rows.append(row)

        for chunk in _chunks(rows, _UPSERT_CHUNK_SIZE):
            await self._request(
                "POST",
                "/place_barrier_free",
                params={"on_conflict": "content_id"},
                json=list(chunk),
                prefer="resolution=merge-duplicates,return=minimal",
            )

    async def list_sync_locks(self) -> list[dict[str, object]]:
        """현재 잡혀 있는 동기화 잠금. 만료된 행도 그대로 보여준다.

        만료를 여기서 걸러내면 "잠금이 남아 실행이 막힌다"와 "잠금이 없다"가
        화면에서 같아 보인다. 판단은 화면에서 하도록 시각을 그대로 넘긴다.

        잠금을 만든 실행의 상태를 함께 싣는다. 잠금 행만으로는 "지금 돌고 있는
        동기화"와 "죽은 프로세스가 남긴 유령 잠금"이 구분되지 않는다 — 두 경우의
        행 모양이 똑같다. 실행이 이미 끝났으면(success/failed) 잠금은 확실히
        유령이고, running이면 살아 있을 수도 죽었을 수도 있어 화면이 경과 시간을
        보고 판단한다.
        """
        response = await self._request(
            "GET",
            "/place_sync_locks",
            params={"select": "*", "order": "acquired_at.desc"},
        )
        payload = self._json(response)
        if not isinstance(payload, list):
            raise SupabaseRepositoryError("invalid sync lock list response")
        locks = [dict(row) for row in payload if isinstance(row, Mapping)]
        if not locks:
            return locks

        run_ids = sorted({str(lock["sync_run_id"]) for lock in locks if lock.get("sync_run_id")})
        runs = await self._get_sync_runs_by_ids(run_ids) if run_ids else {}
        for lock in locks:
            run = runs.get(str(lock.get("sync_run_id") or ""))
            # 실행 기록이 없는 잠금도 있다(행이 지워졌거나 기록 전에 죽은 경우).
            # 그때는 run_status를 None으로 둬 화면이 "기록 없음"으로 보여준다.
            lock["run_status"] = run.get("status") if run else None
            lock["run_started_at"] = run.get("started_at") if run else None
            lock["run_processed_count"] = run.get("processed_count") if run else None
            lock["run_api_total_count"] = run.get("api_total_count") if run else None
        return locks

    async def _get_sync_runs_by_ids(self, run_ids: Sequence[str]) -> dict[str, dict[str, object]]:
        """id로 동기화 실행을 찾아 id → 행으로 돌려준다."""
        response = await self._request(
            "GET",
            "/place_sync_runs",
            params={
                "select": "id,status,started_at,processed_count,api_total_count",
                "id": f"in.({','.join(run_ids)})",
            },
        )
        payload = self._json(response)
        if not isinstance(payload, list):
            raise SupabaseRepositoryError("invalid sync run lookup response")
        return {
            str(row["id"]): dict(row)
            for row in payload
            if isinstance(row, Mapping) and row.get("id")
        }


def _summarize_places(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """장소 행 묶음의 활성 수와 상태 분포를 센다.

    구별 요약과 전 구 합계가 같은 함수를 쓴다 — 따로 세면 한쪽만 규칙이 바뀐 채
    남는 이중 경로가 생긴다.
    """
    active = 0
    detail_status: dict[str, int] = {}
    parse_status: dict[str, int] = {}
    parser_versions: dict[str, int] = {}
    latest_detail_fetched_at: datetime | None = None
    for raw in rows:
        if raw.get("is_active"):
            active += 1
        _bump(detail_status, raw.get("detail_fetch_status"))
        _bump(parse_status, raw.get("operating_parse_status"))
        _bump(parser_versions, raw.get("operating_parser_version"))
        fetched_at = _parse_datetime(raw.get("detail_fetched_at"), "detail_fetched_at")
        if fetched_at is not None and (
            latest_detail_fetched_at is None or fetched_at > latest_detail_fetched_at
        ):
            latest_detail_fetched_at = fetched_at

    return {
        "total": len(rows),
        "active": active,
        "inactive": len(rows) - active,
        "detail_fetch_status": detail_status,
        "operating_parse_status": parse_status,
        "operating_parser_version": parser_versions,
        "latest_detail_fetched_at": _iso(latest_detail_fetched_at),
    }


def _summarize_category_coverage(
    rows: Sequence[Mapping[str, object]], registry: TourCategoryRegistry
) -> dict[str, object]:
    """활성 장소만 TourAPI 대·중·소분류 트리로 묶는다.

    Ops 화면의 목적은 추천 가능한 관광 데이터가 어느 구에 얼마나 있는지 보는
    것이다. 비활성 장소는 원본 목록에서 사라진 과거 행일 수 있어 제외한다.
    다만 코드가 비어 있는 활성 행은 버리지 않고 ``분류 미확인`` 그룹으로 남겨
    동기화·원본 데이터 누락을 확인할 수 있게 한다.
    """
    leaves: dict[tuple[str | None, str | None, str | None], dict[str, object]] = {}
    for row in rows:
        if not row.get("is_active"):
            continue
        large = _category_code(row.get("lcls_systm1"))
        middle = _category_code(row.get("lcls_systm2"))
        small = _category_code(row.get("lcls_systm3"))
        leaf = leaves.setdefault(
            (large, middle, small),
            {"count": 0, "examples": set()},
        )
        leaf["count"] = int(leaf["count"]) + 1
        title = str(row.get("title") or "").strip()
        if title:
            examples = leaf["examples"]
            assert isinstance(examples, set)
            examples.add(title)

    middle_keys = {(large, middle) for large, middle, _small in leaves}
    groups: list[dict[str, object]] = []
    large_codes = {large for large, _middle, _small in leaves}
    for large in sorted(large_codes, key=lambda code: _category_sort_key(code, "large")):
        middle_groups: list[dict[str, object]] = []
        middles = {middle for current_large, middle, _small in leaves if current_large == large}
        for middle in sorted(middles, key=lambda code: _category_sort_key(code, "middle")):
            small_groups: list[dict[str, object]] = []
            smalls = {
                small
                for current_large, current_middle, small in leaves
                if current_large == large and current_middle == middle
            }
            for small in sorted(smalls, key=lambda code: _category_sort_key(code, "small")):
                leaf = leaves[(large, middle, small)]
                examples = leaf["examples"]
                assert isinstance(examples, set)
                small_groups.append(
                    {
                        "code": small,
                        "label": _category_label(registry, large, middle, small, "small"),
                        "count": leaf["count"],
                        "examples": _category_examples(small, examples),
                    }
                )
            middle_groups.append(
                {
                    "code": middle,
                    "label": _category_label(registry, large, middle, None, "middle"),
                    "count": sum(int(item["count"]) for item in small_groups),
                    "smalls": small_groups,
                }
            )
        groups.append(
            {
                "code": large,
                "label": _category_label(registry, large, None, None, "large"),
                "count": sum(int(item["count"]) for item in middle_groups),
                "middles": middle_groups,
            }
        )

    groups.sort(key=lambda group: (-int(group["count"]), str(group["label"])))
    for group in groups:
        middles = group["middles"]
        assert isinstance(middles, list)
        middles.sort(key=lambda item: (-int(item["count"]), str(item["label"])))
        for middle in middles:
            smalls = middle["smalls"]
            assert isinstance(smalls, list)
            smalls.sort(key=lambda item: (-int(item["count"]), str(item["label"])))

    return {
        "active_place_count": sum(int(leaf["count"]) for leaf in leaves.values()),
        "large_category_count": len(large_codes),
        "middle_category_count": len(middle_keys),
        "small_category_count": len(leaves),
        "groups": groups,
    }


def _category_code(value: object) -> str | None:
    code = str(value or "").strip()
    return code or None


def _category_examples(small_code: str | None, examples: set[object]) -> list[str]:
    """소분류를 설명하기 좋은 대표 장소를 최대 두 개 고른다.

    기본은 DB 순서와 무관한 제목순이다. 다만 사후면세점처럼 관광공사 코드명이
    실제 포함 매장의 성격을 충분히 설명하지 못하는 경우는 대표 브랜드를 우선한다.
    """
    titles = sorted(str(example) for example in examples)
    selected: list[str] = []
    for prefix in _CATEGORY_EXAMPLE_PREFIXES.get(small_code or "", ()):
        match = next((title for title in titles if title.startswith(prefix)), None)
        if match is not None:
            selected.append(match)
    selected.extend(title for title in titles if title not in selected)
    return selected[:2]


def _category_sort_key(code: str | None, level: str) -> tuple[int, str]:
    """정상 코드를 먼저, ``분류 미확인``은 각 단계의 마지막에 둔다."""
    return (1 if code is None else 0, code or level)


def _category_label(
    registry: TourCategoryRegistry,
    large: str | None,
    middle: str | None,
    small: str | None,
    level: str,
) -> str:
    if level == "small" and small is not None:
        category = registry.get_by_small_code(small)
        if category is not None:
            return category.lcls_systm3_name
    if level == "middle" and middle is not None:
        categories = registry.find_by_middle_code(middle)
        if categories:
            return categories[0].lcls_systm2_name
    if level == "large" and large is not None:
        categories = registry.find_by_large_code(large)
        if categories:
            return categories[0].lcls_systm1_name
    return (
        "분류 미확인"
        if (large if level == "large" else middle if level == "middle" else small) is None
        else f"코드 {large if level == 'large' else middle if level == 'middle' else small}"
    )


def _bump(counter: dict[str, int], value: object) -> None:
    key = str(value) if value is not None else "null"
    counter[key] = counter.get(key, 0) + 1


def _to_evidence_match(row: object) -> PlaceEvidenceMatch:
    if not isinstance(row, Mapping):
        raise SupabaseRepositoryError("invalid evidence row")
    raw_snippets = row.get("evidence")
    snippets = (
        tuple(_to_evidence_snippet(item) for item in raw_snippets)
        if isinstance(raw_snippets, list)
        else ()
    )
    return PlaceEvidenceMatch(
        content_id=str(row["content_id"]),
        place_title=str(row.get("place_title") or ""),
        avg_similarity=float(row["avg_similarity"]),
        snippets=snippets,
    )


def _to_evidence_snippet(item: object) -> PlaceEvidenceSnippet:
    if not isinstance(item, Mapping):
        raise SupabaseRepositoryError("invalid evidence snippet")
    published_at = item.get("published_at")
    return PlaceEvidenceSnippet(
        source_text=str(item.get("source_text") or ""),
        source_url=str(item["source_url"]) if item.get("source_url") else None,
        similarity=float(item["similarity"]),
        published_at=(datetime.fromisoformat(str(published_at)) if published_at else None),
        source_type=str(item["source_type"]) if item.get("source_type") else None,
        document_id=str(item["document_id"]) if item.get("document_id") else None,
    )


def _to_district_place_row(row: object) -> DistrictPlaceRow | None:
    """구 단위 조회의 행 하나를 후보 모델로 옮긴다. 쓸 수 없는 행이면 None.

    무장애 경로와 달리 예외로 끊지 않고 그 행만 버린다. 저쪽은 RPC가 좌표 있는
    행만 준다는 약속이 있어 비어 있으면 응답이 어긋난 것이지만, 이쪽은 `places`를
    그대로 읽으므로 **적재된 값이 원래 이상한 경우가 실재한다**. 한 건 때문에 구
    전체 추천을 못 하게 만드는 것은 과하다.

    버리는 경우는 둘이다.

    - 좌표가 없거나 서울 언저리 밖 — 원본 데이터 오류다(전 구 12건). 격자가 이
      좌표 하나로 통째로 망가진다. 실제로 걸러내기 전에는 강남구 격자의 대각이
      2,174km로 잡혔다.
    - content_id나 좌표가 숫자가 아님 — 후보로 쓸 수 없다.
    """
    if not isinstance(row, Mapping):
        return None
    content_id = str(row.get("content_id") or "")
    if not content_id:
        return None
    latitude = _optional_float(row.get("latitude"))
    longitude = _optional_float(row.get("longitude"))
    if not is_plausible_seoul_coordinate(latitude, longitude):
        return None
    # is_plausible_seoul_coordinate가 None을 걸렀으므로 여기서는 확정값이다.
    assert latitude is not None and longitude is not None
    return DistrictPlaceRow(
        content_id=content_id,
        title=str(row.get("title") or ""),
        address=_optional_str(row.get("address")),
        latitude=latitude,
        longitude=longitude,
        content_type_id=_optional_str(row.get("content_type_id")),
        lcls_systm1=_optional_str(row.get("lcls_systm1")),
        lcls_systm2=_optional_str(row.get("lcls_systm2")),
        lcls_systm3=_optional_str(row.get("lcls_systm3")),
        first_image_url=_optional_str(row.get("first_image_url")),
    )


def _to_barrier_free_place_row(row: object) -> BarrierFreePlaceRow:
    """RPC 행을 후보 모델로 옮긴다.

    좌표와 거리는 없으면 실패로 다룬다. RPC가 좌표 있는 행만 돌려주고 거리를 늘
    계산하므로, 비어 있다면 응답 모양이 어긋난 것이지 "값이 없는 장소"가 아니다.
    0.0으로 채우면 그 장소가 검색 중심에 있는 것으로 읽혀 맨 앞에 선다.
    """
    if not isinstance(row, Mapping):
        raise SupabaseRepositoryError("invalid barrier free place row")
    content_id = str(row.get("content_id") or "")
    if not content_id:
        raise SupabaseRepositoryError("barrier free place row missing content_id")
    latitude = row.get("latitude")
    longitude = row.get("longitude")
    distance = row.get("distance_km")
    if latitude is None or longitude is None or distance is None:
        raise SupabaseRepositoryError(
            f"barrier free place row missing coordinates or distance: {content_id}"
        )
    return BarrierFreePlaceRow(
        content_id=content_id,
        title=str(row.get("title") or ""),
        address=_optional_str(row.get("address")),
        latitude=float(latitude),
        longitude=float(longitude),
        content_type_id=_optional_str(row.get("content_type_id")),
        lcls_systm1=_optional_str(row.get("lcls_systm1")),
        lcls_systm2=_optional_str(row.get("lcls_systm2")),
        lcls_systm3=_optional_str(row.get("lcls_systm3")),
        first_image_url=_optional_str(row.get("first_image_url")),
        distance_km=float(distance),
        wheelchair_access_verdict=_optional_verdict(row.get("wheelchair_access_verdict")),
        stroller_access_verdict=_optional_verdict(row.get("stroller_access_verdict")),
        visual_guide_verdict=_optional_verdict(row.get("visual_guide_verdict")),
    )


def _optional_verdict(value: object) -> AccessibilityVerdict | None:
    """판정 값을 어휘로 옮긴다. 모르는 값이면 실패로 다룬다.

    None으로 넘기지 않는다. 컬럼에 검사 제약이 걸려 있어 모르는 값이 오면
    RPC가 바뀌었거나 적재가 어긋난 것이고, 조용히 비우면 판정을 못 받은 장소가
    "판정이 없는 장소"처럼 보여 안내에서 소리 없이 빠진다.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return AccessibilityVerdict(text)
    except ValueError as exc:
        raise SupabaseRepositoryError(f"unknown accessibility verdict: {text}") from exc


def _optional_str(value: object) -> str | None:
    """빈 문자열은 값이 없는 것으로 본다. 공백만 있는 값도 마찬가지다."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _to_mood_profile(row: object) -> PlaceMoodProfile:
    if not isinstance(row, Mapping):
        raise SupabaseRepositoryError("invalid mood profile row")
    raw_scores = row.get("axis_scores")
    scores: dict[str, float] = {}
    if isinstance(raw_scores, Mapping):
        for name, value in raw_scores.items():
            try:
                scores[str(name)] = float(value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                # 축 하나가 깨졌다고 장소 전체를 버리지 않는다. 축은 켜고 끄는
                # 중이라 옛 판본이 섞일 수 있고, 남은 축만으로도 정렬은 된다.
                continue
    return PlaceMoodProfile(
        content_id=str(row["content_id"]),
        axis_scores=scores,
        photo_count=int(row.get("photo_count") or 0),
    )


def _to_place_photo(row: object) -> PlacePhoto | None:
    """사진 행 하나를 도메인 값으로 옮긴다. 쓸 수 없는 행은 건너뛴다.

    ``origin_url``은 스키마상 not null이지만 빈 문자열까지 막지는 않는다. 주소가
    비면 화면에 깨진 이미지가 뜨므로 여기서 뺀다 — 한 장이 빠지는 것과 상세
    조회 전체가 실패하는 것은 무게가 다르다.
    """
    if not isinstance(row, Mapping):
        return None
    url = row.get("origin_url")
    content_id = row.get("content_id")
    photo_order = row.get("photo_order")
    if not url or not content_id or photo_order is None:
        return None
    image_name = row.get("image_name")
    return PlacePhoto(
        content_id=str(content_id),
        photo_order=int(photo_order),
        url=str(url),
        image_name=str(image_name) if image_name else None,
    )


def _to_mood_match(row: object) -> PlaceMoodMatch:
    if not isinstance(row, Mapping):
        raise SupabaseRepositoryError("invalid mood match row")
    distance = row.get("distance_km")
    return PlaceMoodMatch(
        content_id=str(row["content_id"]),
        similarity=float(row["similarity"]),
        profile=_to_mood_profile(row),
        distance_km=float(distance) if distance is not None else None,
    )
