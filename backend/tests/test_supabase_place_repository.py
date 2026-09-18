from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock
from uuid import UUID

import httpx
import pytest

from app.domain.models import (
    PlaceBarrierFreeDetails,
    StoredPlaceState,
    TourPlaceRecord,
)
from app.repositories.supabase_places import (
    SupabasePlaceRepository,
    SupabaseRepositoryError,
    _category_examples,
)

RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
NOW = datetime(2026, 7, 24, 3, 0, tzinfo=UTC)


def _repository(
    handler: httpx.MockTransport,
    client: httpx.AsyncClient,
) -> SupabasePlaceRepository:
    return SupabasePlaceRepository(
        supabase_url="https://project.supabase.co/",
        secret_key="sb_secret_test",
        client=client,
    )


def _place(content_id: str) -> TourPlaceRecord:
    return TourPlaceRecord(
        content_id=content_id,
        content_type_id="12",
        title=f"장소 {content_id}",
        address="서울특별시 종로구",
        latitude=37.57,
        longitude=126.97,
        area_code="11",
        district_code="110",
        lcls_systm1="VE",
        lcls_systm2="VE01",
        lcls_systm3="VE010100",
        source_modified_at=NOW,
    )


def test_사후면세점_예시는_다이소와_올리브영을_우선한다() -> None:
    examples = {"0914 도산공원 플래그십 스토어", "다이소 종로점", "올리브영 종로점"}

    assert _category_examples("SH040300", examples) == ["다이소 종로점", "올리브영 종로점"]


@pytest.mark.asyncio
async def test_create_sync_run_uses_secret_key_header() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        return httpx.Response(201, json=[{"id": str(RUN_ID)}])

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await _repository(transport, client).create_sync_run("11", "110")

    request = seen["request"]
    assert isinstance(request, httpx.Request)
    assert request.url.path == "/rest/v1/place_sync_runs"
    assert request.headers["apikey"] == "sb_secret_test"
    assert "authorization" not in request.headers
    assert request.headers["prefer"] == "return=representation"
    assert request.read() == b'{"area_code":"11","district_code":"110"}'
    assert result == RUN_ID


@pytest.mark.asyncio
async def test_lock_rpcs_send_exact_database_arguments() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=True)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        repository = _repository(transport, client)
        acquired = await repository.try_acquire_sync_lock("11", "110", RUN_ID, "2 hours")
        released = await repository.release_sync_lock("11", "110", RUN_ID)

    assert acquired is True
    assert released is True
    assert [request.url.path for request in seen] == [
        "/rest/v1/rpc/try_acquire_place_sync_lock",
        "/rest/v1/rpc/release_place_sync_lock",
    ]
    assert b'"p_lock_ttl":"2 hours"' in seen[0].read()
    assert str(RUN_ID).encode() in seen[1].read()


@pytest.mark.asyncio
async def test_find_active_places_by_name_reads_coordinates_and_mapping() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/v1/places"
        assert request.url.params["or"].startswith('(title.eq."쌈지길",')
        assert request.url.params["is_active"] == "eq.true"
        return httpx.Response(
            200,
            json=[
                {
                    "content_id": "128553",
                    "title": "쌈지길",
                    "address": "서울특별시 종로구 인사동길 44",
                    "latitude": 37.5743062352,
                    "longitude": 126.9848674428,
                    "place_concentration_mappings": [{"primary_concentration_name": "쌈지길"}],
                }
            ],
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        locations = await _repository(transport, client).find_active_places_by_name("쌈지길")

    assert len(locations) == 1
    assert locations[0].content_id == "128553"
    assert locations[0].concentration_name == "쌈지길"


@pytest.mark.asyncio
async def test_find_active_places_by_name_falls_back_through_title_variants() -> None:
    """정확 일치 → 지역 접두사 → 공백 무시 → 괄호 부기 → 별칭 순으로 넓힌다(D-043).

    지역 검색은 "북촌 한옥마을"을 주는데 저장소는 "북촌한옥마을"이고, 사용자는
    "종묘"라고 하는데 저장소 제목은 "종묘 [유네스코 세계유산]"이다.

    필터는 한 번에 던지고 우선순위는 받은 뒤에 가린다. 제목 조회 1회 + 별칭 조회
    1회가 상한이다.
    """
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        or_filter = request.url.params.get("or")
        alias = request.url.params.get("place_concentration_mappings.concentration_aliases")
        seen.append(or_filter if or_filter is not None else f"alias:{alias}")
        if or_filter is None:
            return httpx.Response(200, json=[])
        return httpx.Response(
            200,
            json=[
                {
                    "content_id": "126510",
                    "title": "종묘 [유네스코 세계유산]",
                    "address": None,
                    "latitude": 37.5739,
                    "longitude": 126.9945,
                    "place_concentration_mappings": {
                        "primary_concentration_name": "종묘 [유네스코 세계유산]",
                        "concentration_search_keys": ["종묘"],
                    },
                }
            ],
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        locations = await _repository(transport, client).find_active_places_by_name("종묘")

    # 사다리를 한 칸씩 던지지 않는다 - 제목 조회는 한 번뿐이고, 걸렸으므로 별칭
    # 조회까지 가지 않는다. 지역 접두사 필터도 같은 조회에 함께 실린다.
    assert seen == [
        '(title.eq."종묘",title.ilike."서울 종묘",'
        'title.ilike."종묘 [*",title.ilike."종묘 (*",title.ilike."종묘(*")'
    ]
    assert len(locations) == 1
    assert locations[0].concentration_name == "종묘 [유네스코 세계유산]"
    # 조회는 검색어로, 대조는 정식 명칭으로 해야 종묘광장공원과 섞이지 않는다.
    assert locations[0].concentration_search_keys == ("종묘",)


@pytest.mark.asyncio
async def test_find_active_places_by_name_prefers_earlier_filter_over_later() -> None:
    """한 번에 받아도 정확 일치가 부기 붙은 제목을 이긴다.

    사다리를 한 칸씩 던지던 시절에는 먼저 걸린 칸에서 멈춰 이 순서가 저절로
    지켜졌다. 한 번에 던지면 두 행이 함께 오므로 받은 뒤에 가려야 한다.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("or") is None:
            return httpx.Response(200, json=[])
        return httpx.Response(
            200,
            json=[
                # 응답 순서를 일부러 뒤집어 둔다 - 우선순위가 응답 순서에 기대면
                # 안 된다.
                {
                    "content_id": "126510",
                    "title": "종묘 [유네스코 세계유산]",
                    "address": None,
                    "latitude": 37.5739,
                    "longitude": 126.9945,
                    "place_concentration_mappings": None,
                },
                {
                    "content_id": "999999",
                    "title": "종묘",
                    "address": None,
                    "latitude": 37.5740,
                    "longitude": 126.9946,
                    "place_concentration_mappings": None,
                },
            ],
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        locations = await _repository(transport, client).find_active_places_by_name("종묘")

    # 두 행이 함께 왔지만 정확 일치 하나만 남는다. 둘 다 남기면 호출자가 후보
    # 2건으로 보고 되묻기로 새 버린다.
    assert len(locations) == 1
    assert locations[0].content_id == "999999"


@pytest.mark.asyncio
async def test_find_active_places_by_name_keeps_same_rung_matches_ambiguous() -> None:
    """같은 칸에 여럿이 걸리면 그대로 넘긴다 - 호출자가 모호하다고 판정해야 한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("or") is None:
            return httpx.Response(200, json=[])
        return httpx.Response(
            200,
            json=[
                {
                    "content_id": "1",
                    "title": "종묘 [유네스코 세계유산]",
                    "address": None,
                    "latitude": 37.5739,
                    "longitude": 126.9945,
                    "place_concentration_mappings": None,
                },
                {
                    "content_id": "2",
                    "title": "종묘 [별관]",
                    "address": None,
                    "latitude": 37.5741,
                    "longitude": 126.9947,
                    "place_concentration_mappings": None,
                },
            ],
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        locations = await _repository(transport, client).find_active_places_by_name("종묘")

    assert len(locations) == 2


@pytest.mark.asyncio
async def test_find_active_places_by_name_quotes_titles_with_commas() -> None:
    """쉼표가 든 이름도 필터가 깨지지 않는다.

    감싸지 않으면 쉼표가 or=의 구분자로 읽혀 PostgREST가 PGRST100으로 끊는다.
    저장소에 실제로 있는 형태다("꽃,밥에피다" 등 활성 5건).
    """
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        or_filter = request.url.params.get("or")
        if or_filter is None:
            return httpx.Response(200, json=[])
        seen.append(or_filter)
        return httpx.Response(
            200,
            json=[
                {
                    "content_id": "3",
                    "title": "꽃,밥에피다",
                    "address": None,
                    "latitude": 37.57,
                    "longitude": 126.98,
                    "place_concentration_mappings": None,
                }
            ],
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        locations = await _repository(transport, client).find_active_places_by_name("꽃,밥에피다")

    assert seen == [
        '(title.eq."꽃,밥에피다",title.ilike."서울 꽃,밥에피다",'
        'title.ilike."꽃,밥에피다 [*",title.ilike."꽃,밥에피다 (*",'
        'title.ilike."꽃,밥에피다(*")'
    ]
    assert len(locations) == 1
    assert locations[0].title == "꽃,밥에피다"


@pytest.mark.asyncio
async def test_find_active_places_by_name_treats_brackets_as_literal() -> None:
    """대괄호는 문자 클래스가 아니라 글자 그대로다.

    fnmatch로 판정하면 "종묘 [*"의 대괄호를 문자 클래스로 읽어 엉뚱한 제목이
    걸린다. 저장소 제목에 대괄호가 실제로 있다(활성 22건).
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("or") is None:
            return httpx.Response(200, json=[])
        return httpx.Response(
            200,
            json=[
                {
                    "content_id": "4",
                    # 대괄호가 문자 클래스로 읽히면 이 제목이 "종묘 [*"에 걸린다.
                    "title": "종묘 대제",
                    "address": None,
                    "latitude": 37.57,
                    "longitude": 126.99,
                    "place_concentration_mappings": None,
                }
            ],
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        locations = await _repository(transport, client).find_active_places_by_name("종묘")

    # 어떤 필터에도 안 걸리므로 버린다.
    assert locations == ()


@pytest.mark.asyncio
async def test_find_active_places_by_name_finds_seoul_prefixed_title() -> None:
    """사용자는 "명동성당"이라고 하는데 저장소 제목은 "서울 명동성당"이다.

    TourAPI가 국가지정문화재류에 붙이는 접두사이고 활성 26곳이 여기 걸린다.
    """
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        or_filter = request.url.params.get("or")
        if or_filter is None:
            return httpx.Response(200, json=[])
        seen.append(or_filter)
        return httpx.Response(
            200,
            json=[
                {
                    "content_id": "126804",
                    "title": "서울 명동성당",
                    "address": "서울특별시 중구 명동길 74 (명동2가)",
                    "latitude": 37.56367587,
                    "longitude": 126.9867758233,
                    "place_concentration_mappings": [],
                }
            ],
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        locations = await _repository(transport, client).find_active_places_by_name("명동성당")

    # 접두사 필터가 같은 조회에 실려 나간다.
    assert 'title.ilike."서울 명동성당"' in seen[0]
    assert len(locations) == 1
    assert locations[0].content_id == "126804"
    assert locations[0].title == "서울 명동성당"


@pytest.mark.asyncio
async def test_find_active_places_by_name_prefers_exact_title_over_prefix() -> None:
    """접두사 없는 동명 장소가 생기면 그쪽이 이긴다.

    한 번에 조회하므로 둘 다 받아오지만, 정확 일치가 접두사보다 앞선 칸이라
    거기서 갈린다.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("or") is None:
            return httpx.Response(200, json=[])
        return httpx.Response(
            200,
            json=[
                {
                    "content_id": "126804",
                    "title": "서울 명동성당",
                    "address": None,
                    "latitude": 37.56367587,
                    "longitude": 126.9867758233,
                    "place_concentration_mappings": [],
                },
                {
                    "content_id": "999999",
                    "title": "명동성당",
                    "address": None,
                    "latitude": 37.5636,
                    "longitude": 126.9867,
                    "place_concentration_mappings": [],
                },
            ],
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        locations = await _repository(transport, client).find_active_places_by_name("명동성당")

    assert len(locations) == 1
    assert locations[0].content_id == "999999"


@pytest.mark.asyncio
async def test_find_active_places_by_name_does_not_double_prefix() -> None:
    """정식 제목을 그대로 넣어도 "서울 서울 ..."을 조회하지 않는다."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        or_filter = request.url.params.get("or")
        if or_filter is not None:
            seen.append(or_filter)
        return httpx.Response(200, json=[])

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        await _repository(transport, client).find_active_places_by_name("서울 명동성당")

    assert "서울 서울 명동성당" not in seen[0]


@pytest.mark.asyncio
async def test_find_active_places_by_name_uses_mapping_alias_as_last_resort() -> None:
    """ "창덕궁"은 저장소 제목이 "창덕궁과 후원 [유네스코 세계유산]"이라 제목 규칙으로
    닿지 않는다. 접두 매칭으로 넓히면 창덕궁 낙선재·약다방까지 걸리므로 사람이 지정한
    별칭으로만 잇는다.
    """
    alias_filters: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        alias = request.url.params.get("place_concentration_mappings.concentration_aliases")
        if alias is None:
            return httpx.Response(200, json=[])
        alias_filters.append(alias)
        assert "!inner" in request.url.params["select"]
        return httpx.Response(
            200,
            json=[
                {
                    "content_id": "127642",
                    "title": "창덕궁과 후원 [유네스코 세계유산]",
                    "address": None,
                    "latitude": 37.5794,
                    "longitude": 126.9910,
                    "place_concentration_mappings": {
                        "primary_concentration_name": "창덕궁과 후원 [유네스코 세계유산]",
                        "concentration_search_keys": ["창덕궁과", "후원"],
                    },
                }
            ],
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        locations = await _repository(transport, client).find_active_places_by_name("창덕궁")

    assert alias_filters == ["cs.{창덕궁}"]
    assert len(locations) == 1
    assert locations[0].title == "창덕궁과 후원 [유네스코 세계유산]"
    assert locations[0].concentration_search_keys == ("창덕궁과", "후원")


@pytest.mark.asyncio
async def test_get_region_place_states_maps_rows() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["area_code"] == "eq.11"
        assert request.url.params["district_code"] == "eq.110"
        return httpx.Response(
            200,
            json=[
                {
                    "content_id": "126508",
                    "source_modified_at": "2026-07-23T15:30:45+00:00",
                    "detail_fetched_at": None,
                    "detail_fetch_status": "pending",
                    "operating_parser_version": "operating-hours-1.0.0",
                    "operating_hours_raw": None,
                    "rest_date_raw": None,
                    "is_active": True,
                    "inactive_reason": None,
                }
            ],
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        states = await _repository(transport, client).get_region_place_states("11", "110")

    assert states["126508"].detail_fetch_status == "pending"
    assert states["126508"].source_modified_at == datetime(2026, 7, 23, 15, 30, 45, tzinfo=UTC)


@pytest.mark.asyncio
async def test_upsert_chunks_by_100_and_preserves_existing_manual_state() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(201)

    manual_state = StoredPlaceState(
        content_id="0",
        source_modified_at=None,
        detail_fetched_at=NOW,
        detail_fetch_status="success",
        operating_parser_version="operating-hours-1.0.0",
        operating_hours_raw="09:00~18:00",
        rest_date_raw=None,
        is_active=False,
        inactive_reason="manual_exclusion",
    )
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        await _repository(transport, client).upsert_place_list(
            [_place(str(index)) for index in range(201)],
            {"0": manual_state},
            RUN_ID,
            NOW,
        )

    assert len(requests) == 3
    request_payloads = [json.loads(request.content) for request in requests]
    assert [len(payload) for payload in request_payloads] == [1, 100, 100]
    assert all(len({frozenset(row) for row in payload}) == 1 for payload in request_payloads)
    first_row = request_payloads[0][0]
    assert "operating_hours_raw" not in first_row
    assert "detail_fetch_status" not in first_row
    assert "is_active" not in first_row
    new_row = request_payloads[1][0]
    assert new_row["detail_fetch_status"] == "pending"
    assert new_row["is_active"] is True
    assert requests[0].url.params["on_conflict"] == "content_id"
    assert requests[0].headers["prefer"] == ("resolution=merge-duplicates,return=minimal")


@pytest.mark.asyncio
async def test_upsert_reactivates_only_source_missing_place() -> None:
    payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.extend(json.loads(request.content))
        return httpx.Response(201)

    missing_state = StoredPlaceState(
        content_id="1",
        source_modified_at=None,
        detail_fetched_at=None,
        detail_fetch_status="failed",
        operating_parser_version="operating-hours-1.0.0",
        operating_hours_raw=None,
        rest_date_raw=None,
        is_active=False,
        inactive_reason="missing_from_source",
    )
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        await _repository(transport, client).upsert_place_list(
            [_place("1")], {"1": missing_state}, RUN_ID, NOW
        )

    assert payloads[0]["is_active"] is True
    assert payloads[0]["inactive_reason"] is None
    assert payloads[0]["inactive_at"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("hours", "rest_date", "expected_status"),
    [
        ("09:00~18:00", "매주 화요일", "success"),
        (None, None, "empty"),
    ],
)
async def test_update_operating_details_sets_success_or_empty(
    hours: str | None,
    rest_date: str | None,
    expected_status: str,
) -> None:
    seen_payload: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_payload.update(json.loads(request.content))
        return httpx.Response(204)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        await _repository(transport, client).update_operating_details(
            "126508",
            hours,
            rest_date,
            {"availability": "scheduled"} if hours else None,
            "parsed" if hours else "unknown",
            "operating-hours-1.0.0",
            NOW,
        )

    assert seen_payload["detail_fetch_status"] == expected_status
    assert seen_payload["detail_error_code"] is None


@pytest.mark.asyncio
async def test_mark_detail_failed_does_not_overwrite_cached_details() -> None:
    seen_payload: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_payload.update(json.loads(request.content))
        return httpx.Response(204)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        await _repository(transport, client).mark_detail_failed("126508", "TOUR_DETAIL_TIMEOUT")

    assert seen_payload == {
        "detail_fetch_status": "failed",
        "detail_error_code": "TOUR_DETAIL_TIMEOUT",
    }


@pytest.mark.asyncio
async def test_update_parsed_schedule_does_not_change_detail_fetched_at() -> None:
    seen_payload: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_payload.update(json.loads(request.content))
        return httpx.Response(204)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        await _repository(transport, client).update_parsed_schedule(
            "126508",
            {"availability": "scheduled"},
            "parsed",
            "operating-hours-1.1.0",
        )

    assert seen_payload["operating_parser_version"] == "operating-hours-1.1.0"
    assert "detail_fetched_at" not in seen_payload
    assert "operating_hours_raw" not in seen_payload


@pytest.mark.asyncio
async def test_deactivate_unseen_places_returns_changed_count() -> None:
    seen_request: httpx.Request | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_request
        seen_request = request
        return httpx.Response(200, json=[{"content_id": "old-1"}, {"content_id": "old-2"}])

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        changed = await _repository(transport, client).deactivate_unseen_places(
            "11", "110", RUN_ID, NOW
        )

    assert changed == 2
    assert seen_request is not None
    assert seen_request.url.params["is_active"] == "eq.true"
    assert "last_sync_run_id.neq." in seen_request.url.params["or"]
    assert json.loads(seen_request.content)["inactive_reason"] == "missing_from_source"


@pytest.mark.asyncio
async def test_complete_sync_run_updates_counts() -> None:
    seen_payload: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_payload.update(json.loads(request.content))
        return httpx.Response(204)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        await _repository(transport, client).complete_sync_run(
            RUN_ID,
            status="partial_failure",
            api_total_count=3,
            processed_count=3,
            success_count=2,
            failed_count=1,
            new_count=3,
            updated_count=0,
            deactivated_count=0,
            detail_attempted_count=3,
            error_summary={"TOUR_DETAIL_TIMEOUT": 1},
            completed_at=NOW,
        )

    assert seen_payload["status"] == "partial_failure"
    assert seen_payload["failed_count"] == 1
    assert seen_payload["error_summary"] == {"TOUR_DETAIL_TIMEOUT": 1}
    # 일일 한도 판단의 근거라 실행 기록에 남아야 한다.
    assert seen_payload["detail_attempted_count"] == 3


@pytest.mark.asyncio
async def test_http_error_does_not_expose_secret_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, request=request)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(SupabaseRepositoryError) as exc_info:
            await _repository(transport, client).create_sync_run("11", "110")

    assert "sb_secret_test" not in str(exc_info.value)
    assert "sb_secret_test" not in repr(exc_info.value.details)
    assert exc_info.value.details == {"upstream_detail": "HTTP 401"}


@pytest.mark.asyncio
async def test_find_concentration_mapped_places_reads_single_object_embed() -> None:
    """places ↔ mappings는 1:1이라 PostgREST가 배열이 아닌 단일 객체로 내려준다.

    배열만 처리하던 파서 탓에 concentration_name이 항상 None이 되어 대체 조회가
    후보를 하나도 찾지 못했다(2026-08-03).
    """
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json=[
                {
                    "content_id": "129501",
                    "title": "낙산공원",
                    "address": "서울특별시 종로구 낙산길 41",
                    "latitude": 37.5805179476871,
                    "longitude": 127.006496092905,
                    "place_concentration_mappings": {"primary_concentration_name": "낙산공원"},
                },
                {
                    "content_id": "129502",
                    "title": "매핑없음",
                    "address": None,
                    "latitude": 37.58,
                    "longitude": 127.0,
                    "place_concentration_mappings": None,
                },
            ],
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        repository = SupabasePlaceRepository(
            "https://project.supabase.co/", "sb_secret_test", client
        )
        places = await repository.find_concentration_mapped_places()

    assert [place.concentration_name for place in places] == ["낙산공원"]
    assert captured[0].url.params["is_active"] == "eq.true"


@pytest.mark.asyncio
async def test_find_concentration_mapped_places_reads_every_page() -> None:
    """상한을 넘는 매핑도 전부 읽는다.

    한 번만 읽으면 넘치는 순간 오류 없이 뒷부분이 잘린다 - 대체 후보가 조용히
    사라져 "가까운 곳이 있는데 no_data"가 된다. 지금은 101건이라 실데이터로
    밟히지 않는 경로라서, 늘어난 뒤 처음 도는 일이 없도록 여기서 고정한다.
    """
    captured: list[httpx.Request] = []
    page_size = 3

    def row(index: int) -> dict[str, object]:
        return {
            "content_id": f"{index:06d}",
            "title": f"장소 {index}",
            "address": None,
            "latitude": 37.57,
            "longitude": 126.98,
            "place_concentration_mappings": {"primary_concentration_name": f"장소 {index}"},
        }

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        offset = int(request.url.params["offset"])
        # 7건 = 꽉 찬 페이지 둘 + 1건짜리 마지막 페이지.
        remaining = max(0, 7 - offset)
        return httpx.Response(
            200,
            json=[row(offset + i) for i in range(min(page_size, remaining))],
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        repository = SupabasePlaceRepository(
            "https://project.supabase.co/", "sb_secret_test", client
        )
        with mock.patch("app.repositories.supabase_places._READ_PAGE_SIZE", page_size):
            places = await repository.find_concentration_mapped_places()

    assert len(places) == 7
    assert [request.url.params["offset"] for request in captured] == ["0", "3", "6"]
    # 정렬이 없으면 페이지마다 순서가 달라져 같은 행이 두 번 오거나 아예 빠진다.
    assert captured[0].url.params["order"] == "content_id.asc"


@pytest.mark.asyncio
async def test_find_concentration_mapped_places_stops_on_partial_page() -> None:
    """덜 찬 페이지에서 멈춘다 - 빈 페이지를 한 번 더 부르지 않는다."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json=[
                {
                    "content_id": "129501",
                    "title": "낙산공원",
                    "address": None,
                    "latitude": 37.58,
                    "longitude": 127.0,
                    "place_concentration_mappings": {"primary_concentration_name": "낙산공원"},
                }
            ],
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        repository = SupabasePlaceRepository(
            "https://project.supabase.co/", "sb_secret_test", client
        )
        places = await repository.find_concentration_mapped_places()

    assert len(places) == 1
    assert len(captured) == 1


@pytest.mark.asyncio
async def test_find_concentration_mapped_places_also_reads_array_embed() -> None:
    """관계 형태가 배열로 바뀌어도 같은 값을 읽는다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "content_id": "129501",
                    "title": "낙산공원",
                    "address": None,
                    "latitude": 37.58,
                    "longitude": 127.0,
                    "place_concentration_mappings": [{"primary_concentration_name": "낙산공원"}],
                }
            ],
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        repository = SupabasePlaceRepository(
            "https://project.supabase.co/", "sb_secret_test", client
        )
        places = await repository.find_concentration_mapped_places()

    assert places[0].concentration_name == "낙산공원"


@pytest.mark.asyncio
async def test_count_rows_reads_content_range_total() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, headers={"Content-Range": "*/844"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        repository = SupabasePlaceRepository(
            "https://project.supabase.co/", "sb_secret_test", client
        )
        total = await repository.count_rows("place_enrichments")

    assert total == 844
    assert captured[0].method == "HEAD"
    assert captured[0].headers["prefer"] == "count=exact"


@pytest.mark.asyncio
async def test_count_rows_rejects_missing_content_range() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        repository = SupabasePlaceRepository(
            "https://project.supabase.co/", "sb_secret_test", client
        )
        with pytest.raises(SupabaseRepositoryError):
            await repository.count_rows("places")


@pytest.mark.asyncio
async def test_place_summaries_split_by_district_and_total() -> None:
    """구별 분포와 전 구 합계를 한 번의 조회로 만든다."""
    rows = [
        {
            "area_code": "11",
            "district_code": "110",
            "is_active": True,
            "title": "한글 카페",
            "lcls_systm1": "FD",
            "lcls_systm2": "FD05",
            "lcls_systm3": "FD050100",
            "detail_fetch_status": "succeeded",
            "operating_parse_status": "parsed",
            "operating_parser_version": "operating-hours-1.0.0",
            "detail_fetched_at": "2026-08-08T05:00:00+00:00",
        },
        {
            "area_code": "11",
            "district_code": "110",
            "is_active": False,
            "title": "비활성 카페",
            "lcls_systm1": "FD",
            "lcls_systm2": "FD05",
            "lcls_systm3": "FD050100",
            "detail_fetch_status": "pending",
            "operating_parse_status": "unknown",
            "operating_parser_version": "operating-hours-0.9.0",
            "detail_fetched_at": None,
        },
        {
            "area_code": "11",
            "district_code": "170",
            "is_active": True,
            "title": "용산 미분류 장소",
            "lcls_systm1": None,
            "lcls_systm2": None,
            "lcls_systm3": None,
            "detail_fetch_status": "failed",
            "operating_parse_status": "unknown",
            "operating_parser_version": "operating-hours-1.0.0",
            "detail_fetched_at": "2026-08-21T05:00:00+00:00",
        },
    ]

    requested: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request)
        return httpx.Response(200, json=rows)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        repository = SupabasePlaceRepository(
            "https://project.supabase.co/", "sb_secret_test", client
        )
        summaries = await repository.get_place_summaries_by_district()

    # 구별로 질의를 나누지 않는다 — 나누면 적재된 구 목록을 미리 알아야 한다.
    assert len(requested) == 1
    assert "district_code" not in requested[0].url.params

    districts = summaries["districts"]
    assert isinstance(districts, list)
    assert [(d["area_code"], d["district_code"]) for d in districts] == [
        ("11", "110"),
        ("11", "170"),
    ]

    jongno = districts[0]
    assert (jongno["total"], jongno["active"], jongno["inactive"]) == (2, 1, 1)
    assert jongno["detail_fetch_status"] == {"succeeded": 1, "pending": 1}
    assert jongno["latest_detail_fetched_at"] == "2026-08-08T05:00:00+00:00"
    assert jongno["category_coverage"] == {
        "active_place_count": 1,
        "large_category_count": 1,
        "middle_category_count": 1,
        "small_category_count": 1,
        "groups": [
            {
                "code": "FD",
                "label": "음식",
                "count": 1,
                "middles": [
                    {
                        "code": "FD05",
                        "label": "카페/ 찻집",
                        "count": 1,
                        "smalls": [
                            {
                                "code": "FD050100",
                                "label": "카페",
                                "count": 1,
                                "examples": ["한글 카페"],
                            }
                        ],
                    }
                ],
            }
        ],
    }

    yongsan = districts[1]
    assert (yongsan["total"], yongsan["active"], yongsan["inactive"]) == (1, 1, 0)
    assert yongsan["latest_detail_fetched_at"] == "2026-08-21T05:00:00+00:00"
    assert yongsan["category_coverage"]["groups"][0]["label"] == "분류 미확인"
    assert yongsan["category_coverage"]["groups"][0]["middles"][0]["smalls"][0]["examples"] == [
        "용산 미분류 장소"
    ]

    overall = summaries["overall"]
    assert isinstance(overall, dict)
    assert (overall["total"], overall["active"], overall["inactive"]) == (3, 2, 1)
    assert overall["detail_fetch_status"] == {
        "succeeded": 1,
        "pending": 1,
        "failed": 1,
    }
    assert overall["operating_parse_status"] == {"parsed": 1, "unknown": 2}
    # 파서 버전이 섞여 있으면 다음 동기화에서 재파싱 대상이 생긴다는 신호다.
    assert overall["operating_parser_version"] == {
        "operating-hours-1.0.0": 2,
        "operating-hours-0.9.0": 1,
    }
    # 합계의 최신 상세조회 시각은 전 구를 통틀어 가장 나중이다.
    assert overall["latest_detail_fetched_at"] == "2026-08-21T05:00:00+00:00"


@pytest.mark.asyncio
async def test_list_sync_locks_keeps_expired_rows() -> None:
    """만료된 잠금을 저장소에서 걸러내면 '잠금 없음'과 구분되지 않는다."""
    expired = {
        "area_code": "11",
        "district_code": "110",
        "sync_run_id": str(RUN_ID),
        "acquired_at": "2026-08-01T00:00:00+00:00",
        "expires_at": "2026-08-01T02:00:00+00:00",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/place_sync_runs"):
            # 실행 기록이 지워진 잠금. run_status가 None으로 실린다.
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=[expired])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        repository = SupabasePlaceRepository(
            "https://project.supabase.co/", "sb_secret_test", client
        )
        locks = await repository.list_sync_locks()

    assert len(locks) == 1
    assert {key: locks[0][key] for key in expired} == expired
    # 실행 기록이 없어도 열은 채워 보낸다 — 화면이 "기록 없음"을 표시할 수 있어야 한다.
    assert locks[0]["run_status"] is None


@pytest.mark.asyncio
async def test_list_sync_locks_joins_run_status() -> None:
    """잠금을 만든 실행의 상태를 함께 싣는다.

    잠금 행만으로는 "지금 돌고 있는 동기화"와 "죽은 프로세스가 남긴 유령 잠금"이
    구분되지 않는다 — 두 경우의 행 모양이 똑같다.
    """
    lock = {
        "area_code": "11",
        "district_code": "740",
        "sync_run_id": str(RUN_ID),
        "acquired_at": "2026-08-29T00:00:00+00:00",
        "expires_at": "2026-08-29T02:00:00+00:00",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/place_sync_runs"):
            return httpx.Response(
                200,
                json=[
                    {
                        "id": str(RUN_ID),
                        "status": "running",
                        "started_at": "2026-08-29T00:00:00+00:00",
                        "processed_count": 111,
                        "api_total_count": None,
                    }
                ],
            )
        return httpx.Response(200, json=[lock])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        repository = SupabasePlaceRepository(
            "https://project.supabase.co/", "sb_secret_test", client
        )
        locks = await repository.list_sync_locks()

    assert locks[0]["run_status"] == "running"
    assert locks[0]["run_processed_count"] == 111


@pytest.mark.asyncio
async def test_find_missing_concentration_mappings_returns_unmapped_ids() -> None:
    """매핑 없는 장소는 혼잡도 조회를 아예 건너뛰므로 누가 빠졌는지 알아야 한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"content_id": "2"}])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        repository = SupabasePlaceRepository(
            "https://project.supabase.co/", "sb_secret_test", client
        )
        missing = await repository.find_missing_concentration_mappings(["1", "2", "3"])

    assert missing == ["1", "3"]


@pytest.mark.asyncio
async def test_find_missing_concentration_mappings_skips_request_when_empty() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("빈 목록에는 요청을 보내지 않아야 한다")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        repository = SupabasePlaceRepository(
            "https://project.supabase.co/", "sb_secret_test", client
        )
        assert await repository.find_missing_concentration_mappings([]) == []


@pytest.mark.asyncio
async def test_detail_call_summary_separates_unmeasured_runs() -> None:
    """재지 못한 실행을 0으로 합치면 합계가 실제보다 정확해 보인다."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json=[
                {"detail_attempted_count": 486},
                {"detail_attempted_count": 3},
                # 중간에 죽어 완료 처리를 못 한 실행, 또는 열 추가 이전 행.
                {"detail_attempted_count": None},
            ],
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        repository = SupabasePlaceRepository(
            "https://project.supabase.co/", "sb_secret_test", client
        )
        summary = await repository.summarize_detail_calls_since(NOW)

    assert summary == {"count": 489, "runs": 3, "runs_without_count": 1}
    # 오늘 것만 세야 한다 — 경계를 안 걸면 누적 전체가 오늘 사용량으로 보인다.
    assert captured[0].url.params["started_at"].startswith("gte.")


@pytest.mark.asyncio
async def test_무장애_upsert는_부른_장소만_행으로_만든다() -> None:
    """목록에 없는 장소는 행을 만들지 않는다.

    없다는 사실은 무장애 목록 조회가 매번 알려주므로 저장할 이유가 없다 —
    종로구에서 그렇게 쌓인 빈 행이 590개였다. 반대로 불러서 값이 비어 온 장소는
    행을 남긴다. 그래야 같은 빈 응답을 다시 받지 않는다.
    """
    posted: list[tuple[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        posted.append((str(request.url), json.loads(request.content)))
        return httpx.Response(201, json=[])

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        repository = _repository(transport, client)
        await repository.upsert_barrier_free_details(
            [
                PlaceBarrierFreeDetails(
                    content_id="126508",
                    accessible_restroom_raw="장애인 화장실 있음",
                    wheelchair_rental_raw="대여가능",
                ),
                # 항목이 미입력인 장소(쇼핑몰 입점 매장 60건이 이 모양이다).
                PlaceBarrierFreeDetails(content_id="3306733"),
            ],
            NOW,
        )

    rows = [row for _, payload in posted for row in payload]  # type: ignore[union-attr]
    by_id = {row["content_id"]: row for row in rows}
    assert all("/place_barrier_free" in url for url, _ in posted)
    assert all("on_conflict=content_id" in url for url, _ in posted)
    assert sorted(by_id) == ["126508", "3306733"]

    filled = by_id["126508"]
    assert filled["accessible_restroom_raw"] == "장애인 화장실 있음"
    assert filled["wheelchair_rental_raw"] == "대여가능"
    # 값이 없는 필드도 null로 보낸다 — 옛 값이 남으면 근거 없는 문장을 계속 내보낸다.
    assert filled["stroller_rental_raw"] is None

    # 미입력 장소도 fetched_at은 남는다. 그게 "불러봤다"는 표시다.
    empty = by_id["3306733"]
    assert empty["fetched_at"] is not None
    assert empty["accessible_restroom_raw"] is None


@pytest.mark.asyncio
async def test_무장애_상세가_없으면_요청하지_않는다() -> None:
    """부른 장소가 없는데 빈 POST를 보내면 PostgREST가 400을 낸다."""
    posted: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        posted.append(str(request.url))
        return httpx.Response(201, json=[])

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        repository = _repository(transport, client)
        await repository.upsert_barrier_free_details([], NOW)

    assert posted == []


@pytest.mark.asyncio
async def test_무장애_확인_시각은_값이_비어도_읽는다() -> None:
    """값이 없다고 다시 부르면 "목록에 있는데 값이 없는" 장소를 매번 재조회한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/place_barrier_free")
        assert request.url.params["select"] == "content_id,fetched_at"
        return httpx.Response(
            200,
            json=[{"content_id": "126508", "fetched_at": "2026-08-25T03:00:00+00:00"}],
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        repository = _repository(transport, client)
        fetched = await repository.list_barrier_free_fetched_at(["126508", "999"])

    assert list(fetched) == ["126508"]
    assert fetched["126508"] == datetime(2026, 8, 25, 3, 0, tzinfo=UTC)


def test_무장애_필드가_전부_마이그레이션에_있다() -> None:
    """계약 필드를 늘리고 마이그레이션을 안 고치면 저장이 통째로 실패한다.

    이 저장소에서 반복된 실패 유형이라(미완결 스키마 마이그레이션) 파일을 직접
    읽어 대조한다.

    파일명은 glob으로 찾는다. 이 테스트가 보는 것은 필드 누락이지 파일명이 아니고,
    번호가 바뀌었다고 깨지면 안 된다 — 실제로 2026-08-25에 같은 날 다른 마이그레이션과
    번호가 겹쳐 `202608250001`에서 `202608250002`로 옮긴 적이 있다.
    """
    migrations = sorted(
        (Path(__file__).resolve().parents[2] / "supabase" / "migrations").glob(
            "*_create_place_barrier_free.sql"
        )
    )
    assert len(migrations) == 1, f"무장애 테이블 마이그레이션이 하나여야 한다: {migrations}"
    migration = migrations[0].read_text(encoding="utf-8")

    fields = {
        name for name in vars(PlaceBarrierFreeDetails(content_id="1")) if name != "content_id"
    }
    missing = {field for field in fields if f"  {field} text" not in migration}
    assert missing == set()


@pytest.mark.asyncio
async def test_find_active_places_by_name_finds_paren_suffix_without_space() -> None:
    """사용자는 "조계사"라고 하는데 저장소 제목은 "조계사(서울)"이다.

    괄호를 공백 없이 붙인 제목이 활성 2,761건 중 47건으로, 공백을 둔 14건보다 세 배
    넘게 많다(2026-08-25 실측). 공백 있는 형태만 보면 이 47곳이 괄호를 빼고 부를 때
    한 곳도 안 찾힌다 — 동대문디자인플라자(DDP)·남산공원(서울)·대원군별장(석파정)처럼
    사람이 괄호 없이 부르는 이름들이다.
    """
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        or_filter = request.url.params.get("or")
        if or_filter is None:
            return httpx.Response(200, json=[])
        seen.append(or_filter)
        return httpx.Response(
            200,
            json=[
                {
                    "content_id": "128144",
                    "title": "조계사(서울)",
                    "address": "서울특별시 종로구 우정국로 55",
                    "latitude": 37.5729,
                    "longitude": 126.9810,
                    "place_concentration_mappings": None,
                }
            ],
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        locations = await _repository(transport, client).find_active_places_by_name("조계사")

    assert "조계사(*" in seen[0]
    assert len(locations) == 1
    assert locations[0].title == "조계사(서울)"


@pytest.mark.asyncio
async def test_find_active_places_by_name_paren_filter_does_not_widen() -> None:
    """괄호 필터가 부분 일치로 넓어지면 안 된다.

    와일드카드가 여는 괄호 뒤에만 있어 "조계사"가 "조계사터"·"조계사길"에는 걸리지
    않는다. 넓어지면 엉뚱한 장소가 검색 중심이 된다(_title_filters의 원래 경고).
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("or") is None:
            return httpx.Response(200, json=[])
        return httpx.Response(
            200,
            json=[
                {
                    "content_id": "9",
                    # 괄호 필터가 넓어지면 이 제목이 "조계사(*"에 걸린다.
                    "title": "조계사터",
                    "address": None,
                    "latitude": 37.57,
                    "longitude": 126.99,
                    "place_concentration_mappings": None,
                }
            ],
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        locations = await _repository(transport, client).find_active_places_by_name("조계사")

    # 어떤 필터에도 안 걸리므로 버린다.
    assert locations == ()


@pytest.mark.asyncio
async def test_complete_sync_run_does_not_overwrite_abandoned_run() -> None:
    """이미 마감된 실행은 정상 종료가 덮어쓰지 않는다.

    개발자 패널이 잠금을 강제 해제하면서 실행을 failed로 마감해도, 그 프로세스는
    자기 잠금이 사라진 것을 모른 채 계속 돌다가 정상 종료한다. 조건이 없으면
    여기서 success로 덮어써 강제 해제 흔적이 지워진다.
    """
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url.query, "utf-8"))
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        repository = SupabasePlaceRepository(
            "https://project.supabase.co/", "sb_secret_test", client
        )
        await repository.complete_sync_run(
            RUN_ID,
            status="success",
            api_total_count=10,
            processed_count=10,
            success_count=10,
            failed_count=0,
            new_count=1,
            updated_count=9,
            deactivated_count=0,
            detail_attempted_count=10,
            completed_at=datetime(2026, 8, 29, tzinfo=UTC),
        )

    assert seen
    # running인 행만 갱신한다 — 이미 failed로 마감된 행은 조건에서 빠진다.
    assert "status=eq.running" in seen[0]


@pytest.mark.asyncio
async def test_다른_구에서_비활성이던_장소도_되살린다() -> None:
    """`existing_states`는 이 구의 장소만 담아서 "처음 보는 장소"와 "다른 구에 이미
    있는 장소"가 구분되지 않는다. 장소는 구를 옮겨 다닌다.

    2026-08-30에 "2025 제17회 서울건축문화제"가 종로구에서 사라져 비활성이 된 뒤
    중구 목록에 나타났다. is_active만 켜면 inactive_reason이 남은 채로 활성이 되어
    places_active_state_valid를 어기고, upsert가 400으로 튕겨 그 청크 전체가
    실패한다 — 중구 반영이 그렇게 계속 실패했다.
    """
    payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.extend(json.loads(request.content))
        return httpx.Response(201)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        # 이 구에는 기록이 없다. 다른 구에 비활성으로 남아 있는 상태다.
        await _repository(transport, client).upsert_place_list([_place("3528062")], {}, RUN_ID, NOW)

    row = payloads[0]
    assert row["is_active"] is True
    # 활성이면 두 값이 모두 null이어야 한다(places_active_state_valid).
    assert row["inactive_reason"] is None
    assert row["inactive_at"] is None
    # failed였던 행을 pending으로 바꾸면서 오류 코드를 안 지우면
    # places_detail_error_matches_status를 어긴다.
    assert row["detail_fetch_status"] == "pending"
    assert row["detail_error_code"] is None


@pytest.mark.asyncio
async def test_find_active_places_by_name_drops_broken_coordinates() -> None:
    """서울 언저리 밖으로 찍힌 행은 버리고 나머지로 간다.

    원본 데이터에 깨진 좌표가 실재한다 — 활성 8,007곳 중 12건이고 그중 10건이
    (19.69, 117.99)라는 같은 값이다(남중국해, 2026-09-11 확인). "계남근린공원"은 깨진 행과
    정상 행이 함께 있어 "2건이니 애매하다"로 판정됐고, 두 행의 주소가 같아 자치구를 붙여도
    선택지가 하나로 합쳐져 눌러도 제자리였다.

    한 건 때문에 이름 해석을 통째로 실패시키지는 않는다. 구 단위 후보 조회가 같은 이유로
    같은 검사를 한다(_map_district_place_row).
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "content_id": "2611568",
                    "title": "계남근린공원",
                    "address": "서울특별시 양천구 중앙로17길 21 (신정동)",
                    "latitude": 19.69442748,
                    "longitude": 117.9925662504,
                },
                {
                    "content_id": "3428372",
                    "title": "계남근린공원",
                    "address": "서울특별시 양천구 중앙로17길 21 (신정동)",
                    "latitude": 37.5098751207,
                    "longitude": 126.8550905317,
                },
            ],
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        locations = await _repository(transport, client).find_active_places_by_name("계남근린공원")

    assert len(locations) == 1
    assert locations[0].content_id == "3428372"
