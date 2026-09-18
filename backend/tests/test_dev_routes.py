"""개발자 Ops 패널 API(/api/dev/*) 테스트."""

from __future__ import annotations

from datetime import datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.errors import ProviderUnavailableError
from app.main import create_app
from app.observability import api_usage
from app.routes import dev as dev_routes
from app.services import place_snapshot
from app.services.place_sync import PlaceSyncResult, SyncProgress
from app.services.place_sync_jobs import get_job_registry


@pytest.fixture(autouse=True)
def _clean_registry():
    api_usage.reset_usage()
    yield
    api_usage.reset_usage()


def _client() -> TestClient:
    return TestClient(create_app())


def test_dev_routes_are_absent_outside_local(monkeypatch: pytest.MonkeyPatch) -> None:
    """배포 환경에서는 경로 자체가 없어야 한다 — 설정 플래그가 아니라 미등록으로 막는다."""
    monkeypatch.setattr(settings, "app_env", "production")
    with _client() as client:
        assert client.get("/api/dev/api-usage").status_code == 404
        assert client.get("/api/dev/db-status").status_code == 404


def test_api_usage_returns_snapshot_with_provider_modes() -> None:
    api_usage.record_call("tour_api", "detailIntro2", ok=True, latency_ms=12, status="200")

    with _client() as client:
        response = client.get("/api/dev/api-usage")

    assert response.status_code == 200
    payload = response.json()
    assert payload["totals"]["count"] == 1
    entry = payload["entries"][0]
    assert (entry["provider"], entry["operation"]) == ("tour_api", "detailIntro2")
    assert entry["daily_limit"] == settings.tour_api_daily_call_limit
    # Fake 구성으로 떠 있으면 표가 비는 게 정상이라 모드를 함께 내려야 화면이
    # "호출 없음"과 "fake라 호출 자체가 없음"을 구분할 수 있다.
    assert payload["provider_modes"]["place"] in {"fake", "real"}


def test_api_usage_reset_clears_counters() -> None:
    api_usage.record_call("tour_api", "detailIntro2", ok=True, latency_ms=12, status="200")

    with _client() as client:
        payload = client.post("/api/dev/api-usage/reset").json()

    assert payload["totals"]["count"] == 0
    assert payload["entries"] == []


def test_db_status_requires_supabase_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "supabase_url", "")
    monkeypatch.setattr(settings, "supabase_secret_key", "")

    with _client() as client:
        response = client.get("/api/dev/db-status")

    assert response.status_code == 400
    assert "SUPABASE_URL" in response.json()["error"]["message"]


def test_db_status_splits_places_by_district_and_keeps_history_global(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """장소 요약은 구별로 나누고, 동기화 이력·잠금은 전 구 목록 그대로 준다."""
    monkeypatch.setattr(settings, "supabase_url", "https://project.supabase.co")
    monkeypatch.setattr(settings, "supabase_secret_key", "sb_secret_test")

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "HEAD":
            return httpx.Response(200, headers={"Content-Range": "*/12"})
        if path.endswith("/places"):
            return httpx.Response(
                200,
                json=[
                    {
                        "area_code": "11",
                        "district_code": "110",
                        "is_active": True,
                        "detail_fetch_status": "succeeded",
                        "operating_parse_status": "parsed",
                        "operating_parser_version": "operating-hours-1.0.0",
                        "detail_fetched_at": "2026-08-08T05:00:00+00:00",
                    },
                    {
                        "area_code": "11",
                        "district_code": "170",
                        "is_active": False,
                        "detail_fetch_status": "pending",
                        "operating_parse_status": "unknown",
                        "operating_parser_version": "operating-hours-1.0.0",
                        "detail_fetched_at": None,
                    },
                ],
            )
        if path.endswith("/place_barrier_free"):
            return httpx.Response(
                200,
                json=[
                    {
                        "content_id": "1",
                        "places": {
                            "area_code": "11",
                            "district_code": "110",
                            "is_active": True,
                        },
                    },
                    # 비활성 장소에 달린 행. 활성 수에는 안 들어가고 전체에는 들어간다.
                    {
                        "content_id": "2",
                        "places": {
                            "area_code": "11",
                            "district_code": "170",
                            "is_active": False,
                        },
                    },
                ],
            )
        if path.endswith("/place_sync_runs"):
            return httpx.Response(
                200,
                json=[{"id": "run-1", "district_code": "170", "status": "success"}],
            )
        if path.endswith("/place_sync_locks"):
            return httpx.Response(200, json=[])
        raise AssertionError(f"unexpected path {path}")

    monkeypatch.setattr(
        dev_routes,
        "status_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    with _client() as client:
        payload = client.get("/api/dev/db-status").json()

    assert payload["overall"]["total"] == 2
    assert payload["overall"]["active"] == 1
    # 이름은 코드에 박지 않고 tour_api_ldong_codes.json에서 찾아 붙인다.
    assert [(d["district_code"], d["district_name"]) for d in payload["districts"]] == [
        ("110", "종로구"),
        ("170", "용산구"),
    ]
    assert payload["districts"][0]["total"] == 1
    # 무장애 행은 구별로 센다. place_barrier_free에는 구 열이 없어 places와 묶어야 한다.
    assert payload["districts"][0]["barrier_free_active"] == 1
    assert payload["districts"][0]["barrier_free_total"] == 1
    assert payload["districts"][1]["barrier_free_active"] == 0
    assert payload["districts"][1]["barrier_free_total"] == 1
    assert payload["overall"]["barrier_free_active"] == 1
    assert payload["overall"]["barrier_free_total"] == 2
    assert payload["districts"][1]["inactive"] == 1
    # 이력과 잠금은 구로 나누지 않는다 — 화면에서도 탭 밖에 둔다.
    assert payload["sync_runs"] == [
        {"id": "run-1", "district_code": "170", "status": "success"}
    ]
    assert payload["sync_locks"] == []
    assert payload["place_enrichments_count"] == 12
    assert payload["place_concentration_mappings_count"] == 12
    assert payload["detail_ttl_days"] == settings.place_sync_detail_ttl_days
    # 오늘 상세조회 사용량은 place_sync_runs에서 센다. 메모리 집계와 달리 서버를
    # 재시작해도 남고 스크립트 실행분도 잡히지만, 값이 없는 실행은 따로 알린다.
    assert payload["detail_calls_today"] == {
        "count": 0,
        "runs": 1,
        "runs_without_count": 1,
        "daily_limit": settings.tour_api_daily_call_limit,
    }


def test_db_status_names_unknown_district_as_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """자료에 없는 코드가 와도 조회 전체가 실패하지 않는다 — 이름만 비운다."""
    monkeypatch.setattr(settings, "supabase_url", "https://project.supabase.co")
    monkeypatch.setattr(settings, "supabase_secret_key", "sb_secret_test")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(200, headers={"Content-Range": "*/0"})
        if request.url.path.endswith("/places"):
            return httpx.Response(
                200,
                json=[{"area_code": "11", "district_code": "999", "is_active": True}],
            )
        return httpx.Response(200, json=[])

    monkeypatch.setattr(
        dev_routes,
        "status_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    with _client() as client:
        payload = client.get("/api/dev/db-status").json()

    assert payload["districts"][0]["district_name"] is None
    assert payload["districts"][0]["district_code"] == "999"


def test_db_status_does_not_count_itself_in_api_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """패널의 상태 조회가 자기 호출량 표에 잡히면 안 된다.

    잡히면 새로고침 한 번에 place_sync_runs·place_sync_locks 등이 늘어나,
    추천 요청이 동기화 테이블을 읽은 것처럼 보인다(추천 경로는 place_sync_*를
    읽지 않는다). 측정 도구가 측정값을 만드는 상태라 회귀로 막는다.
    """
    monkeypatch.setattr(settings, "supabase_url", "https://project.supabase.co")
    monkeypatch.setattr(settings, "supabase_secret_key", "sb_secret_test")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(200, headers={"Content-Range": "*/0"})
        return httpx.Response(200, json=[])

    monkeypatch.setattr(
        dev_routes,
        "status_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    with _client() as client:
        assert client.get("/api/dev/db-status").status_code == 200
        usage = client.get("/api/dev/api-usage").json()

    assert usage["entries"] == []
    assert usage["totals"]["count"] == 0


@pytest.fixture(autouse=True)
def _clean_jobs():
    get_job_registry().reset()
    yield
    get_job_registry().reset()


class _FakeBarrierFreeProvider:
    """대조가 부르는 무장애 목록을 가로챈다.

    테스트에서 `listed`를 채워 예상 호출수를 검증한다. 기본값이 빈 dict이므로
    아무것도 설정하지 않은 테스트에서는 대상이 0건으로 나온다.
    """

    listed: dict[str, str] = {}
    calls: list[tuple[str, str]] = []

    def __init__(self, **kwargs: object) -> None:
        pass

    async def list_barrier_free_content_ids(
        self, area_code: str, district_code: str
    ) -> dict[str, str]:
        type(self).calls.append((area_code, district_code))
        return dict(type(self).listed)


@pytest.fixture(autouse=True)
def _barrier_free_list(monkeypatch: pytest.MonkeyPatch) -> type[_FakeBarrierFreeProvider]:
    """무장애 목록 조회를 전 테스트에서 가짜로 바꾼다.

    autouse인 이유는 대조가 이 목록을 실제로 부르기 때문이다. 막지 않으면 테스트가
    실 TourAPI로 나가고, 등록되지 않은 키라 403이 조용히 잡혀 "0건"으로 보인다 —
    네트워크에 나가는 것도, 그 결과가 그럴듯한 0으로 보이는 것도 곤란하다.
    """
    _FakeBarrierFreeProvider.listed = {}
    _FakeBarrierFreeProvider.calls = []
    monkeypatch.setattr(dev_routes, "RealBarrierFreeProvider", _FakeBarrierFreeProvider)
    return _FakeBarrierFreeProvider


@pytest.fixture
def _real_place(monkeypatch: pytest.MonkeyPatch) -> None:
    """동기화 실행 조건(PLACE_PROVIDER=real)만 만든다.

    provider_mode 자체를 real로 올리면 llm·weather·geocoding·local_search까지
    real이 되어 부팅 검증(validate_provider_config)이 그 키들을 전부 요구한다.
    로컬에는 backend/.env에 값이 있고 CI에는 없어 CI에서만 깨진다. 아래에서 real
    전용 키를 명시적으로 비워 로컬에서도 CI와 같은 조건으로 돌게 한다.
    """
    monkeypatch.setattr(settings, "provider_mode", "fake")
    monkeypatch.setattr(settings, "place_provider", "real")
    monkeypatch.setattr(settings, "tour_api_service_key", "tour-key")
    monkeypatch.setattr(settings, "supabase_url", "https://project.supabase.co")
    monkeypatch.setattr(settings, "supabase_secret_key", "sb_secret_test")
    for attribute in (
        "llm_api_key",
        "weather_api_key",
        "naver_map_client_id",
        "naver_map_client_secret",
        "naver_local_search_client_id",
        "naver_local_search_client_secret",
    ):
        monkeypatch.setattr(settings, attribute, "")


def _snapshot_row(content_id: str, **overrides: str) -> dict[str, str]:
    row = {
        "content_id": content_id,
        "content_type_id": "12",
        "title": f"장소 {content_id}",
        "address": "서울특별시 종로구",
        "latitude": "37.5",
        "longitude": "127.0",
        "area_code": "11",
        "district_code": "110",
        "lcls_systm1": "VE",
        "lcls_systm2": "VE01",
        "lcls_systm3": "VE010100",
        "source_modified_at": "2026-08-01T00:00:00+09:00",
        "first_image_url": "",
        "thumbnail_url": "",
        "list_fetched_at": "2026-08-08T13:00:00+09:00",
    }
    row.update(overrides)
    return row


def test_place_sync_refuses_fake_place_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fake 데이터가 운영 places에 upsert되는 사고를 막는다(D-042와 같은 함정)."""
    monkeypatch.setattr(settings, "provider_mode", "fake")
    monkeypatch.setattr(settings, "place_provider", "fake")

    with _client() as client:
        response = client.post("/api/dev/place-sync/reconcile", json={})

    assert response.status_code == 400
    assert "PLACE_PROVIDER" in response.json()["error"]["message"]


def test_apply_rejects_mismatched_confirm(
    monkeypatch: pytest.MonkeyPatch, _real_place: None, tmp_path
) -> None:
    monkeypatch.setattr(place_snapshot, "DATA_DIR", tmp_path)
    monkeypatch.setattr(dev_routes.place_snapshot, "DATA_DIR", tmp_path)
    place_snapshot.write_snapshot({"1": _snapshot_row("1")}, tmp_path / "snap.csv")

    with _client() as client:
        response = client.post(
            "/api/dev/place-sync/apply",
            json={
                "snapshot": "snap.csv",
                "detail_content_ids": ["1"],
                "confirm": "11-999",
            },
        )

    assert response.status_code == 400
    assert "11-110" in response.json()["error"]["message"]


def test_apply_rejects_snapshot_outside_data_dir(
    monkeypatch: pytest.MonkeyPatch, _real_place: None, tmp_path
) -> None:
    """경로를 데이터 디렉터리 안으로 가둔다."""
    monkeypatch.setattr(dev_routes.place_snapshot, "DATA_DIR", tmp_path)

    with _client() as client:
        response = client.post(
            "/api/dev/place-sync/apply",
            json={
                "snapshot": "../../etc/passwd",
                "detail_content_ids": [],
                "confirm": "11-110",
            },
        )

    assert response.status_code == 400


def _mock_no_backfill(monkeypatch: pytest.MonkeyPatch) -> None:
    """상세 정보를 못 채운 장소가 없는 DB. 대조가 그것도 세기 때문에 필요하다."""
    monkeypatch.setattr(settings, "supabase_url", "https://project.supabase.co")
    monkeypatch.setattr(settings, "supabase_secret_key", "sb_secret_test")
    monkeypatch.setattr(
        dev_routes,
        "status_client",
        lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[]))
        ),
    )


def _baseline_name(area_code: str = "11", district_code: str = "110") -> str:
    """테스트용 기준 스냅샷 파일명. 구가 들어간 현재 규칙을 따른다."""
    return place_snapshot.snapshot_file_name(
        area_code, district_code, datetime(2026, 1, 1, tzinfo=place_snapshot.KST)
    )


def test_reconcile_writes_snapshot_and_selects_detail_targets(
    monkeypatch: pytest.MonkeyPatch, _real_place: None, tmp_path
) -> None:
    """대조는 목록 1회만 부르고 DB는 건드리지 않는다."""
    monkeypatch.setattr(dev_routes.place_snapshot, "DATA_DIR", tmp_path)
    _mock_no_backfill(monkeypatch)
    baseline = {
        "1": _snapshot_row("1"),
        # 2번은 이번 목록에서 빠진다 → removed
        "2": _snapshot_row("2"),
        "3": _snapshot_row("3"),
    }
    place_snapshot.write_snapshot(baseline, tmp_path / _baseline_name())

    current = {
        # 1번은 좌표만 바뀜 → updated이지만 상세조회 제외
        "1": _snapshot_row("1", latitude="37.6"),
        # 3번은 수정시각이 바뀜 → 상세조회 대상
        "3": _snapshot_row("3", source_modified_at="2026-08-09T00:00:00+09:00"),
        # 4번은 신규 → 상세조회 대상
        "4": _snapshot_row("4"),
    }

    async def fake_fetch(client, api_key, area, district, fetched_at):
        return current

    monkeypatch.setattr(dev_routes.place_snapshot, "fetch_place_rows", fake_fetch)

    with _client() as client:
        payload = client.post("/api/dev/place-sync/reconcile", json={}).json()

    assert payload["counts"] == {"added": 1, "removed": 1, "updated": 2}
    assert payload["detail_content_ids"] == ["3", "4"]
    # 좌표만 바뀐 1번은 상세조회에서 빠지되, 조용히 사라지지 않고 드러나야 한다.
    assert payload["detail_excluded_ids"] == ["1"]
    assert (tmp_path / payload["snapshot"]).exists()
    assert (tmp_path / payload["reconciliation"]).exists()
    # 파일명에 구가 없으면 같은 날 다른 구를 대조할 때 서로를 덮어쓴다.
    assert payload["snapshot"].startswith("places_api_snapshot_11-110_")
    assert payload["reconciliation"].startswith("places_reconciliation_11-110_")
    assert payload["baseline"] == _baseline_name()


def test_reconcile_reports_skipped_columns_from_old_baseline(
    monkeypatch: pytest.MonkeyPatch, _real_place: None, tmp_path
) -> None:
    """옛 스냅샷에 없는 열은 비교하지 않는다는 사실을 화면이 알아야 한다."""
    monkeypatch.setattr(dev_routes.place_snapshot, "DATA_DIR", tmp_path)
    _mock_no_backfill(monkeypatch)
    old_row = _snapshot_row("1")
    del old_row["first_image_url"]
    del old_row["thumbnail_url"]
    import csv as _csv

    baseline_path = tmp_path / _baseline_name()
    with baseline_path.open("w", newline="", encoding="utf-8-sig") as fp:
        writer = _csv.DictWriter(fp, fieldnames=list(old_row))
        writer.writeheader()
        writer.writerow(old_row)

    async def fake_fetch(client, api_key, area, district, fetched_at):
        return {"1": _snapshot_row("1")}

    monkeypatch.setattr(dev_routes.place_snapshot, "fetch_place_rows", fake_fetch)

    with _client() as client:
        payload = client.post("/api/dev/place-sync/reconcile", json={}).json()

    assert set(payload["skipped_columns"]) == {"first_image_url", "thumbnail_url"}


def test_apply_rejects_snapshot_from_another_district(
    monkeypatch: pytest.MonkeyPatch, _real_place: None, tmp_path
) -> None:
    """다른 구 스냅샷으로 반영하면 대상 구의 활성 장소가 전부 비활성화된다.

    스냅샷에 없는 장소는 "목록에서 사라진 것"으로 판정돼
    `deactivate_unseen_places`가 끄는데, 중구 스냅샷에는 종로구 장소가 하나도
    없으므로 종로구 전량이 대상이 된다. 실행 전에 막는다.
    """
    monkeypatch.setattr(dev_routes.place_snapshot, "DATA_DIR", tmp_path)
    place_snapshot.write_snapshot(
        {"1": _snapshot_row("1", district_code="140")},
        tmp_path / place_snapshot.snapshot_file_name(
            "11", "140", datetime(2026, 8, 20, tzinfo=place_snapshot.KST)
        ),
    )

    with _client() as client:
        response = client.post(
            "/api/dev/place-sync/apply",
            json={
                "snapshot": "places_api_snapshot_11-140_20260820.csv",
                "detail_content_ids": ["1"],
                "confirm": "11-110",
            },
        )

    assert response.status_code == 400
    message = response.json()["error"]["message"]
    assert "11-140" in message and "11-110" in message


def test_reconcile_ignores_other_district_snapshot_as_baseline(
    monkeypatch: pytest.MonkeyPatch, _real_place: None, tmp_path
) -> None:
    """다른 구 스냅샷은 기준으로 잡히지 않는다.

    잡히면 "전량 삭제 + 전량 신규"가 나오는데, 그 모양은 실제 대량 변경과
    구분되지 않는다. 2026-08-20에 중구를 종로구 스냅샷과 대조해 "삭제 844건"이
    나온 사고가 그것이다.
    """
    monkeypatch.setattr(dev_routes.place_snapshot, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "supabase_url", "https://project.supabase.co")
    monkeypatch.setattr(settings, "supabase_secret_key", "sb_secret_test")
    # DB에도 종로구 장소가 없는 상태로 둔다. 여기서 보려는 것은 파일 선택이다.
    monkeypatch.setattr(
        dev_routes,
        "status_client",
        lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[]))
        ),
    )
    place_snapshot.write_snapshot(
        {"9": _snapshot_row("9", district_code="140")},
        tmp_path / place_snapshot.snapshot_file_name(
            "11", "140", datetime(2026, 8, 20, tzinfo=place_snapshot.KST)
        ),
    )

    async def fake_fetch(client, api_key, area, district, fetched_at):
        return {"1": _snapshot_row("1")}

    monkeypatch.setattr(dev_routes.place_snapshot, "fetch_place_rows", fake_fetch)

    with _client() as client:
        payload = client.post("/api/dev/place-sync/reconcile", json={}).json()

    # 종로구 기준이 없으니 "기준 없음"이지, 중구 것을 끌어다 쓰지 않는다.
    assert payload["baseline"] is None
    assert payload["counts"] == {"added": 0, "removed": 0, "updated": 0}
    assert payload["baseline_source"] == "none"


def test_reconcile_builds_baseline_from_database_when_no_snapshot(
    monkeypatch: pytest.MonkeyPatch, _real_place: None, tmp_path
) -> None:
    """스냅샷이 없어도 DB에 장소가 있으면 그것으로 기준을 세운다.

    없으면 전량이 신규로 잡혀, 이미 DB에 있는 장소에 detailIntro2를 한 번씩 더
    쓴다. 용산구 486건이면 하루 한도 1,000회의 절반이다.
    """
    monkeypatch.setattr(dev_routes.place_snapshot, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "supabase_url", "https://project.supabase.co")
    monkeypatch.setattr(settings, "supabase_secret_key", "sb_secret_test")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/place_barrier_free"):
            # 무장애 정보를 확인한 장소가 아직 없다.
            return httpx.Response(200, json=[])
        assert request.url.params["district_code"] == "eq.110"
        # 1번은 DB에 있고, 2번은 이번 목록에만 있다 → 신규 1건.
        return httpx.Response(200, json=[_snapshot_row("1")])

    monkeypatch.setattr(
        dev_routes,
        "status_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    async def fake_fetch(client, api_key, area, district, fetched_at):
        return {"1": _snapshot_row("1"), "2": _snapshot_row("2")}

    monkeypatch.setattr(dev_routes.place_snapshot, "fetch_place_rows", fake_fetch)

    with _client() as client:
        payload = client.post("/api/dev/place-sync/reconcile", json={}).json()

    assert payload["baseline_source"] == "database"
    # 기준을 파일로 남기지 않는다 — 오늘 날짜 파일과 이름이 겹쳐 덮어써진다.
    assert payload["baseline"].startswith("places@")
    assert payload["counts"] == {"added": 1, "removed": 0, "updated": 0}
    assert payload["detail_content_ids"] == ["2"]


def test_apply_runs_job_and_reports_progress(
    monkeypatch: pytest.MonkeyPatch, _real_place: None, tmp_path
) -> None:
    monkeypatch.setattr(dev_routes.place_snapshot, "DATA_DIR", tmp_path)
    place_snapshot.write_snapshot(
        {"1": _snapshot_row("1")}, tmp_path / "places_api_snapshot_20260809.csv"
    )

    captured: dict[str, object] = {}

    class _FakeService:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def sync(self, area_code, district_code, **kwargs):
            captured.update(kwargs)
            kwargs["on_progress"](SyncProgress(phase="details", processed=1, total=1))
            return PlaceSyncResult(
                status="success",
                dry_run=kwargs["dry_run"],
                sync_run_id=None,
                api_total_count=1,
                processed_count=1,
                success_count=1,
                failed_count=0,
                new_count=0,
                updated_count=1,
                deactivated_count=0,
                detail_target_count=1,
                detail_attempted_count=1,
                reparse_count=0,
                barrier_free_target_count=0,
                barrier_free_attempted_count=0,
                barrier_free_stored_count=0,
                error_summary={},
            )

    monkeypatch.setattr(dev_routes, "PlaceSyncService", _FakeService)

    async def fake_missing(self, content_ids):
        return list(content_ids)

    monkeypatch.setattr(
        dev_routes.SupabasePlaceRepository,
        "find_missing_concentration_mappings",
        fake_missing,
    )

    with _client() as client:
        started = client.post(
            "/api/dev/place-sync/apply",
            json={
                "snapshot": "places_api_snapshot_20260809.csv",
                "detail_content_ids": ["1"],
                "added_content_ids": ["1"],
                "dry_run": True,
                "confirm": "11-110",
            },
        ).json()
        job = client.get(f"/api/dev/place-sync/jobs/{started['job_id']}").json()

    assert captured["detail_content_ids"] == frozenset({"1"})
    assert captured["dry_run"] is True
    assert job["status"] == "success"
    assert (job["phase"], job["processed"], job["total"]) == ("details", 1, 1)
    assert job["result"]["detail_attempted_count"] == 1
    # 신규 장소는 집중률 매핑이 없다 — 이 동기화가 매핑 테이블을 갱신하지 않으므로
    # 알리지 않으면 그 장소만 조용히 혼잡도 판정에서 빠진다.
    assert job["unmapped_new_place_ids"] == ["1"]


def _clear_supabase(monkeypatch: pytest.MonkeyPatch) -> None:
    """Supabase를 못 보는 상태로 둔다. 테스트가 운영 DB에 붙지 않게 한다."""
    monkeypatch.setattr(settings, "supabase_url", "")
    monkeypatch.setattr(settings, "supabase_secret_key", "")


def _write_snapshot_pair(tmp_path, region: str = "11-110") -> None:
    """대조에 필요한 스냅샷 두 장. 앞 세대가 기준이 된다."""
    place_snapshot.write_snapshot(
        {"1": _snapshot_row("1"), "2": _snapshot_row("2")},
        tmp_path / f"places_api_snapshot_{region}_20260828.csv",
    )
    place_snapshot.write_snapshot(
        {"1": _snapshot_row("1"), "3": _snapshot_row("3")},
        tmp_path / f"places_api_snapshot_{region}_20260829.csv",
    )


def test_저장된_스냅샷으로_대조하면_외부_호출이_없다(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """어제 뜬 스냅샷이 있으면 목록을 다시 받을 이유가 없다.

    오늘 상세조회 한도가 없어 반영을 못 하고 다음 날 이어서 하는 경우가 이것이다.
    """
    monkeypatch.setattr(dev_routes.place_snapshot, "DATA_DIR", tmp_path)
    _clear_supabase(monkeypatch)
    _write_snapshot_pair(tmp_path)

    def _explode(*args, **kwargs):
        raise AssertionError("saved 모드는 TourAPI를 부르면 안 된다.")

    monkeypatch.setattr(dev_routes.place_snapshot, "fetch_place_rows", _explode)

    with _client() as client:
        response = client.post(
            "/api/dev/place-sync/reconcile",
            json={"area_code": "11", "district_code": "110", "source": "saved"},
        )

    payload = response.json()
    assert response.status_code == 200
    assert payload["source"] == "saved"
    assert payload["snapshot"] == "places_api_snapshot_11-110_20260829.csv"
    assert payload["baseline"] == "places_api_snapshot_11-110_20260828.csv"
    assert payload["counts"] == {"added": 1, "removed": 1, "updated": 0}
    # 무장애는 목록을 불러야 셀 수 있는데 그게 없애려던 호출이다. 0회가 아니라
    # "확인하지 못했다"로 돌려준다.
    assert payload["barrier_free_checked"] is False


def test_저장된_스냅샷으로_대조해도_파일을_새로_쓰지_않는다(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """오늘 날짜로 다시 쓰면 어제 목록이 오늘 것으로 둔갑한다.

    그 파일이 다음 대조의 기준이 되면서 하루치 변화가 통째로 사라진다.
    """
    monkeypatch.setattr(dev_routes.place_snapshot, "DATA_DIR", tmp_path)
    _clear_supabase(monkeypatch)
    _write_snapshot_pair(tmp_path)
    before = sorted(path.name for path in tmp_path.glob("places_*.csv"))

    with _client() as client:
        client.post(
            "/api/dev/place-sync/reconcile",
            json={"area_code": "11", "district_code": "110", "source": "saved"},
        )

    assert sorted(path.name for path in tmp_path.glob("places_*.csv")) == before


def test_저장된_스냅샷이_없으면_실행하지_않는다(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(dev_routes.place_snapshot, "DATA_DIR", tmp_path)
    _clear_supabase(monkeypatch)

    with _client() as client:
        response = client.post(
            "/api/dev/place-sync/reconcile",
            json={"area_code": "11", "district_code": "110", "source": "saved"},
        )

    assert response.status_code >= 400
    assert "저장된 스냅샷이 없습니다" in response.text


def test_기준을_세울_수_없으면_전량_신규로_진행하지_않는다(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """스냅샷이 한 장뿐이면 비교할 기준이 없다.

    API 경로에서는 새 구를 처음 적재하는 정상 경로지만, 저장된 스냅샷을 다시 쓰는
    자리에서 기준이 없다는 건 앞 세대가 지워졌다는 뜻이다. 그대로 진행하면 이미
    DB에 있는 장소에 detailIntro2를 전량 다시 쓴다.
    """
    monkeypatch.setattr(dev_routes.place_snapshot, "DATA_DIR", tmp_path)
    # DB 기준도 못 세우는 상태로 둔다. 자격증명이 있으면 places로 기준을 만들 수
    # 있고, 그건 외부 호출이 아니라 막을 이유가 없는 정상 경로다.
    _clear_supabase(monkeypatch)
    place_snapshot.write_snapshot(
        {"1": _snapshot_row("1")},
        tmp_path / "places_api_snapshot_11-110_20260829.csv",
    )

    with _client() as client:
        response = client.post(
            "/api/dev/place-sync/reconcile",
            json={"area_code": "11", "district_code": "110", "source": "saved"},
        )

    assert response.status_code >= 400
    assert "앞 세대 스냅샷이 없습니다" in response.text


def _apply_with_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    *,
    status: str,
    body: dict,
) -> dict:
    """반영을 한 번 돌리고 job 스냅샷을 돌려준다. 대조 CSV 삭제 규칙 검증용."""
    monkeypatch.setattr(dev_routes.place_snapshot, "DATA_DIR", tmp_path)
    place_snapshot.write_snapshot(
        {"1": _snapshot_row("1")},
        tmp_path / "places_api_snapshot_11-110_20260830.csv",
    )
    (tmp_path / "places_reconciliation_11-110_20260829.csv").write_text(
        "content_id\n", encoding="utf-8"
    )
    (tmp_path / "places_reconciliation_11-110_20260830.csv").write_text(
        "content_id\n", encoding="utf-8"
    )

    class _FakeService:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def sync(self, area_code, district_code, **kwargs):
            return PlaceSyncResult(
                status=status,
                dry_run=kwargs["dry_run"],
                sync_run_id=None,
                api_total_count=1,
                processed_count=1,
                success_count=1,
                failed_count=0,
                new_count=0,
                updated_count=1,
                deactivated_count=0,
                detail_target_count=1,
                detail_attempted_count=1,
                reparse_count=0,
                barrier_free_target_count=0,
                barrier_free_attempted_count=0,
                barrier_free_stored_count=0,
                error_summary={},
            )

    monkeypatch.setattr(dev_routes, "PlaceSyncService", _FakeService)

    async def fake_missing(self, content_ids):
        return []

    monkeypatch.setattr(
        dev_routes.SupabasePlaceRepository,
        "find_missing_concentration_mappings",
        fake_missing,
    )

    with _client() as client:
        started = client.post(
            "/api/dev/place-sync/apply",
            json={
                "snapshot": "places_api_snapshot_11-110_20260830.csv",
                "detail_content_ids": ["1"],
                "confirm": "11-110",
                **body,
            },
        ).json()
        return client.get(f"/api/dev/place-sync/jobs/{started['job_id']}").json()


def test_반영이_끝나면_대조_CSV를_지우고_스냅샷은_남긴다(
    monkeypatch: pytest.MonkeyPatch, _real_place: None, tmp_path
) -> None:
    """대조 CSV는 스냅샷 두 개에서 다시 만들 수 있는 파생물이다.

    스냅샷은 다르다 — 다음 대조의 기준이라 지우면 전량이 신규로 잡히고, 이미 DB에
    있는 장소에 detailIntro2를 한 번씩 더 쓴다.
    """
    job = _apply_with_result(
        monkeypatch, tmp_path, status="success", body={"dry_run": False}
    )

    assert job["status"] == "success"
    assert list(tmp_path.glob("places_reconciliation_*.csv")) == []
    assert (tmp_path / "places_api_snapshot_11-110_20260830.csv").exists()


def test_한도_소진으로_끝난_반영도_대조_CSV를_지운다(
    monkeypatch: pytest.MonkeyPatch, _real_place: None, tmp_path
) -> None:
    """partial_failure는 상세를 일부 못 채운 것이고 목록 반영과 비활성화는 끝났다.

    대조 CSV가 담는 것은 목록 단위 변경이라, 그 기록은 이미 소비됐다.
    """
    _apply_with_result(
        monkeypatch, tmp_path, status="partial_failure", body={"dry_run": False}
    )

    assert list(tmp_path.glob("places_reconciliation_*.csv")) == []


def test_dry_run은_대조_CSV를_지우지_않는다(
    monkeypatch: pytest.MonkeyPatch, _real_place: None, tmp_path
) -> None:
    """DB에 아무것도 쓰지 않았다. 변경분은 그대로 남아 있다."""
    _apply_with_result(monkeypatch, tmp_path, status="success", body={"dry_run": True})

    assert len(list(tmp_path.glob("places_reconciliation_*.csv"))) == 2


def test_상한이_걸린_반영은_대조_CSV를_지우지_않는다(
    monkeypatch: pytest.MonkeyPatch, _real_place: None, tmp_path
) -> None:
    """상한이 걸린 실행은 비활성화를 건너뛴다.

    대조가 찾은 "삭제" 행이 DB에 반영되지 않은 채 남는데, 그 기록을 지우면 무엇이
    남았는지 알 방법이 없다.
    """
    _apply_with_result(
        monkeypatch,
        tmp_path,
        status="success",
        body={"dry_run": False, "details_limit": 1},
    )

    assert len(list(tmp_path.glob("places_reconciliation_*.csv"))) == 2


def test_실패한_반영은_대조_CSV를_지우지_않는다(
    monkeypatch: pytest.MonkeyPatch, _real_place: None, tmp_path
) -> None:
    _apply_with_result(
        monkeypatch, tmp_path, status="failed", body={"dry_run": False}
    )

    assert len(list(tmp_path.glob("places_reconciliation_*.csv"))) == 2


def _stub_concentration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    *,
    names: list[str],
    places: list[tuple[str, str]],
) -> list[list]:
    """집중률 경로의 외부 호출을 막고 파일 자리를 tmp_path로 옮긴다.

    올린 payload를 담은 목록을 돌려준다 — 적재가 실제로 무엇을 보냈는지 본다.
    """
    from app.services import concentration_mapping

    monkeypatch.setattr(settings, "supabase_url", "https://project.supabase.co")
    monkeypatch.setattr(settings, "supabase_secret_key", "secret")
    monkeypatch.setattr(settings, "tour_api_service_key", "key")
    monkeypatch.setattr(concentration_mapping, "DATA_DIR", tmp_path)
    monkeypatch.setattr(
        concentration_mapping,
        "DEFAULT_OVERRIDES",
        tmp_path / "concentration_manual_overrides.csv",
    )
    monkeypatch.setattr(
        concentration_mapping,
        "DEFAULT_REJECTIONS",
        tmp_path / "concentration_rejections.csv",
    )

    async def fake_names(_settings, _area, _district):
        return names

    async def fake_places(_settings, *, district_code):
        return [
            concentration_mapping.PlaceRow(content_id, title)
            for content_id, title in places
        ]

    uploaded: list[list] = []

    async def fake_upsert(_settings, rows):
        uploaded.append(list(rows))
        return len(rows)

    monkeypatch.setattr(
        concentration_mapping, "fetch_concentration_place_names", fake_names
    )
    monkeypatch.setattr(concentration_mapping, "load_places_from_supabase", fake_places)
    monkeypatch.setattr(concentration_mapping, "upsert_mappings", fake_upsert)
    return uploaded


def test_집중률_생성이_확실한_것과_애매한_것을_가른다(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """규칙이 이름을 고쳐 붙인 것만 사람이 본다.

    이름이 크게 다른 장소를 잘못 붙이면 엉뚱한 곳의 혼잡도를 답한다(D-043).
    """
    _stub_concentration(
        monkeypatch,
        tmp_path,
        names=["경복궁", "종묘 [유네스코 세계유산]"],
        places=[("1", "경복궁"), ("2", "종묘"), ("3", "이름없는카페")],
    )

    with _client() as client:
        response = client.post(
            "/api/dev/concentration/build",
            json={"area_code": "11", "district_code": "110"},
        )

    payload = response.json()
    assert response.status_code == 200
    assert payload["concentration_code"] == "11110"
    assert [row["place_title"] for row in payload["certain"]] == ["경복궁"]
    assert [row["place_title"] for row in payload["ambiguous"]] == ["종묘"]
    assert [place["title"] for place in payload["unmatched"]] == ["이름없는카페"]


def test_집중률_생성은_CSV를_쓰지_않는다(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """사람이 걸러낸 뒤에 써야 CSV와 DB가 같아진다.

    먼저 쓰면 승인 전 상태가 파일로 남고, 그 파일을 CLI로 적재하면 거절한 것까지
    들어간다.
    """
    _stub_concentration(
        monkeypatch, tmp_path, names=["경복궁"], places=[("1", "경복궁")]
    )

    with _client() as client:
        client.post(
            "/api/dev/concentration/build",
            json={"area_code": "11", "district_code": "110"},
        )

    assert list(tmp_path.glob("concentration_place_mapping_*.csv")) == []


def test_집중률_적재가_승인분만_올리고_거절을_남긴다(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from app.services import concentration_mapping

    uploaded = _stub_concentration(
        monkeypatch, tmp_path, names=["경복궁"], places=[("1", "경복궁")]
    )

    with _client() as client:
        response = client.post(
            "/api/dev/concentration/apply",
            json={
                "area_code": "11",
                "district_code": "110",
                "rows": [
                    {
                        "content_id": "1",
                        "place_title": "경복궁",
                        "concentration_title": "경복궁",
                        "match_method": "exact",
                        "aliases": [],
                        "search_key": None,
                        "search_keys": ["경복궁"],
                    }
                ],
                "rejections": [
                    {
                        "place_title": "북촌생활사박물관",
                        "concentration_title": "북촌",
                        "note": "다른 장소",
                    }
                ],
                "confirm": "11110",
            },
        )

    payload = response.json()
    assert response.status_code == 200
    assert payload["imported_count"] == 1
    assert [row.content_id for row in uploaded[0]] == ["1"]
    # 승인분만 CSV로 남는다.
    assert (tmp_path / payload["csv"]).exists()
    # 거절은 파일에 남아 다음 생성에서 후보로 올라오지 않는다.
    rejections = concentration_mapping.load_rejections(
        tmp_path / "concentration_rejections.csv"
    )
    assert ("북촌생활사박물관", "북촌") in rejections


def test_집중률_적재는_확인_문자열이_맞아야_한다(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """표에 보이는 11-110이 아니라 집중률 코드 11110이어야 한다."""
    uploaded = _stub_concentration(
        monkeypatch, tmp_path, names=["경복궁"], places=[("1", "경복궁")]
    )

    with _client() as client:
        response = client.post(
            "/api/dev/concentration/apply",
            json={
                "area_code": "11",
                "district_code": "110",
                "rows": [],
                "confirm": "11-110",
            },
        )

    assert response.status_code >= 400
    assert uploaded == []


def test_집중률_현황이_CSV_이후_신규_장소를_센다(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """CSV가 오래된 것과 갱신이 필요한 것은 다르다.

    새 장소가 안 들어왔으면 매핑을 다시 만들어도 결과가 같다. 화면이 어느 구를
    해야 하는지 답하려면 CSV 날짜가 아니라 그 뒤에 생긴 장소 수를 봐야 한다.
    """
    from app.services import concentration_mapping

    monkeypatch.setattr(settings, "supabase_url", "https://project.supabase.co")
    monkeypatch.setattr(settings, "supabase_secret_key", "secret")
    monkeypatch.setattr(concentration_mapping, "DATA_DIR", tmp_path)
    monkeypatch.setattr(
        concentration_mapping,
        "DEFAULT_REJECTIONS",
        tmp_path / "concentration_rejections.csv",
    )
    (tmp_path / "concentration_place_mapping_11110_20260808.csv").write_text(
        "content_id\n", encoding="utf-8"
    )

    async def fake_summaries(self):
        return {
            "districts": [
                {"area_code": "11", "district_code": "110", "active": 840},
                # CSV가 없는 구는 통째로 안 해본 것이라 활성 장소 전부가 대상이다.
                {"area_code": "11", "district_code": "140", "active": 896},
            ]
        }

    monkeypatch.setattr(
        dev_routes.SupabasePlaceRepository,
        "get_place_summaries_by_district",
        fake_summaries,
    )

    async def fake_counts(_settings, _codes):
        return {"110": 101, "140": 49}

    asked: list[tuple[str, str]] = []

    async def fake_created_after(_settings, district_code, since):
        asked.append((district_code, since))
        return 12

    monkeypatch.setattr(
        concentration_mapping, "count_mappings_by_district", fake_counts
    )
    monkeypatch.setattr(
        concentration_mapping, "count_places_created_after", fake_created_after
    )

    with _client() as client:
        payload = client.get("/api/dev/concentration/status").json()

    by_code = {row["district_code"]: row for row in payload["districts"]}
    # 파일명 날짜를 ISO로 바꿔 물어본다.
    assert asked == [("110", "2026-08-08")]
    assert by_code["110"]["new_places_since_csv"] == 12
    assert by_code["140"]["new_places_since_csv"] == 896


def test_nearest_area_resolves_coordinate_to_area_name() -> None:
    """종로 한복판 좌표는 서울시 상권 지역 이름으로 근사된다."""

    with _client() as client:
        response = client.get("/api/dev/nearest-area", params={"location": "37.5709,126.9990"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["area_name"]
    assert payload["area_code"]
    # 근사치라는 사실을 숨기지 않으려고 거리를 함께 준다.
    assert payload["distance_km"] is not None
    assert payload["distance_km"] <= 2.0


def test_nearest_area_returns_empty_beyond_proxy_distance() -> None:
    """82개 지역에서 2km를 넘으면 빌려올 이름이 없다 — 임의의 상권으로 대체하지 않는다."""

    with _client() as client:
        response = client.get("/api/dev/nearest-area", params={"location": "35.1796,129.0756"})

    assert response.status_code == 200
    assert response.json() == {"area_code": None, "area_name": None, "distance_km": None}


def test_nearest_area_rejects_malformed_location() -> None:
    with _client() as client:
        response = client.get("/api/dev/nearest-area", params={"location": "종로3가"})

    assert response.status_code == 400
    # 원인을 화면에 그대로 띄우는 게 이 패널의 목적이라 공통 핸들러 문구로 덮이면 안 된다.
    assert "위도,경도" in response.json()["error"]["message"]


@pytest.mark.asyncio
async def test_database_baseline_reports_unavailable_without_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """자격증명이 없으면 "DB에 없다"가 아니라 "확인하지 못했다"로 돌려준다.

    두 가지를 같은 결과로 뭉개면, 낭비된 상세조회를 두고 화면이 "원래 없던
    구"인지 "못 본 것"인지 설명할 수 없다.

    (앱 기동은 이 상태를 애초에 막지만 — PLACE_DETAILS_SOURCE=supabase면
    validate_provider_config가 거부한다 — 다른 details source에서는 자격증명 없이도
    패널이 뜬다.)
    """
    monkeypatch.setattr(settings, "supabase_url", "")
    monkeypatch.setattr(settings, "supabase_secret_key", "")

    baseline, source = await dev_routes._baseline_from_database("11", "110")

    assert baseline == {}
    assert source == "unavailable"


def test_sync_districts_lists_loaded_and_known(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """자료가 있는 구와, 구 추가 입력을 검증할 사전을 함께 준다."""
    monkeypatch.setattr(settings, "supabase_url", "https://project.supabase.co")
    monkeypatch.setattr(settings, "supabase_secret_key", "sb_secret_test")
    monkeypatch.setattr(dev_routes.place_snapshot, "DATA_DIR", tmp_path)
    # 대조만 하고 아직 반영하지 않은 구. 파일만 있어도 목록에 남아야 한다.
    place_snapshot.write_snapshot(
        {"9": _snapshot_row("9", district_code="200")},
        tmp_path / place_snapshot.snapshot_file_name(
            "11", "200", datetime(2026, 8, 21, tzinfo=place_snapshot.KST)
        ),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {"area_code": "11", "district_code": "110", "is_active": True},
                {"area_code": "11", "district_code": "110", "is_active": False},
            ],
        )

    monkeypatch.setattr(
        dev_routes,
        "status_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    with _client() as client:
        payload = client.get("/api/dev/place-sync/districts").json()

    loaded = {row["district_code"]: row for row in payload["loaded"]}
    assert loaded["110"]["place_count"] == 2
    assert loaded["110"]["active_count"] == 1
    assert loaded["110"]["district_name"] == "종로구"
    # 대조 한 번이 쓰는 목록 호출 수. 1,000건마다 1회다.
    assert loaded["110"]["list_call_estimate"] == 1
    # DB에는 없고 스냅샷만 있는 구도 선택지에 남는다.
    assert loaded["200"]["place_count"] == 0
    assert loaded["200"]["latest_snapshot"] == (
        "places_api_snapshot_11-200_20260821.csv"
    )
    # 사전은 서울 25개 구 전체다 — 없는 코드 입력을 화면이 막는 근거다.
    assert len(payload["known"]) == 25
    assert {"area_code": "11", "district_code": "170", "district_name": "용산구"} in (
        payload["known"]
    )


def test_apply_passes_details_limit_to_service(
    monkeypatch: pytest.MonkeyPatch, _real_place: None, tmp_path
) -> None:
    """상한이 서비스까지 가야 한다. 안 가면 화면만 나눠 받은 척한다."""
    monkeypatch.setattr(dev_routes.place_snapshot, "DATA_DIR", tmp_path)
    place_snapshot.write_snapshot(
        {"1": _snapshot_row("1")},
        tmp_path / place_snapshot.snapshot_file_name(
            "11", "110", datetime(2026, 8, 21, tzinfo=place_snapshot.KST)
        ),
    )
    captured: dict[str, object] = {}

    class _FakeService:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def sync(self, area_code, district_code, **kwargs):
            captured.update(kwargs)
            return PlaceSyncResult(
                status="success",
                dry_run=True,
                sync_run_id=None,
                api_total_count=1,
                processed_count=1,
                success_count=1,
                failed_count=0,
                new_count=0,
                updated_count=1,
                deactivated_count=0,
                detail_target_count=1,
                detail_attempted_count=1,
                reparse_count=0,
                barrier_free_target_count=0,
                barrier_free_attempted_count=0,
                barrier_free_stored_count=0,
                error_summary={},
            )

    monkeypatch.setattr(dev_routes, "PlaceSyncService", _FakeService)
    monkeypatch.setattr(settings, "supabase_url", "https://project.supabase.co")
    monkeypatch.setattr(settings, "supabase_secret_key", "sb_secret_test")

    with _client() as client:
        started = client.post(
            "/api/dev/place-sync/apply",
            json={
                "snapshot": "places_api_snapshot_11-110_20260821.csv",
                "detail_content_ids": ["1"],
                "dry_run": True,
                "details_limit": 300,
                "confirm": "11-110",
            },
        ).json()
        for _ in range(50):
            job = client.get(f"/api/dev/place-sync/jobs/{started['job_id']}").json()
            if job["status"] != "running":
                break

    assert captured["details_limit"] == 300
    assert job["params"]["details_limit"] == 300


def test_apply_rejects_details_limit_below_one(
    monkeypatch: pytest.MonkeyPatch, _real_place: None, tmp_path
) -> None:
    monkeypatch.setattr(dev_routes.place_snapshot, "DATA_DIR", tmp_path)
    place_snapshot.write_snapshot(
        {"1": _snapshot_row("1")},
        tmp_path / place_snapshot.snapshot_file_name(
            "11", "110", datetime(2026, 8, 21, tzinfo=place_snapshot.KST)
        ),
    )

    with _client() as client:
        response = client.post(
            "/api/dev/place-sync/apply",
            json={
                "snapshot": "places_api_snapshot_11-110_20260821.csv",
                "detail_content_ids": ["1"],
                "details_limit": 0,
                "confirm": "11-110",
            },
        )

    assert response.status_code == 422


def test_reconcile_counts_places_missing_details_as_extra_calls(
    monkeypatch: pytest.MonkeyPatch, _real_place: None, tmp_path
) -> None:
    """반영은 변경분과 **함께** pending·failed 장소도 부른다. 그 수를 대조가 알려야 한다.

    2026-08-21 종로구 대조가 이 상태였다. 화면은 "상세조회 15회"라고 표시했지만
    실제 반영은 못 채운 142건을 더해 157회를 썼을 것이다. 한도가 왜 줄었는지
    설명할 수 없는 숫자가 된다.
    """
    monkeypatch.setattr(dev_routes.place_snapshot, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "supabase_url", "https://project.supabase.co")
    monkeypatch.setattr(settings, "supabase_secret_key", "sb_secret_test")
    place_snapshot.write_snapshot(
        {"1": _snapshot_row("1"), "2": _snapshot_row("2"), "3": _snapshot_row("3")},
        tmp_path / _baseline_name(),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/place_barrier_free"):
            return httpx.Response(200, json=[])
        assert request.url.params["detail_fetch_status"] == "in.(pending,failed)"
        return httpx.Response(
            200,
            json=[
                # 2번은 이번 변경분에도 들어 있다 — 두 번 세면 안 된다.
                {"content_id": "2"},
                {"content_id": "3"},
                # 9번은 이번 목록에 없다(비활성). 동기화가 훑지 않으므로 제외한다.
                {"content_id": "9"},
            ],
        )

    monkeypatch.setattr(
        dev_routes,
        "status_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    async def fake_fetch(client, api_key, area, district, fetched_at):
        return {
            "1": _snapshot_row("1"),
            "2": _snapshot_row("2", source_modified_at="2026-08-21T00:00:00+09:00"),
            "3": _snapshot_row("3"),
        }

    monkeypatch.setattr(dev_routes.place_snapshot, "fetch_place_rows", fake_fetch)

    with _client() as client:
        payload = client.post("/api/dev/place-sync/reconcile", json={}).json()

    assert payload["detail_content_ids"] == ["2"]
    assert payload["detail_backfill_ids"] == ["3"]
    assert payload["detail_backfill_checked"] is True


@pytest.mark.asyncio
async def test_detail_backfill_reports_unchecked_without_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """못 본 것과 "보충할 게 없다"를 0으로 뭉개면 예상 호출수가 확정값처럼 보인다."""
    monkeypatch.setattr(settings, "supabase_url", "")
    monkeypatch.setattr(settings, "supabase_secret_key", "")

    ids, checked = await dev_routes._detail_backfill_ids(
        "11", "110", {"1": _snapshot_row("1")}, frozenset()
    )

    assert ids == []
    assert checked is False


def test_list_call_estimate_counts_one_call_per_thousand_places() -> None:
    """areaBasedList2도 일일 한도가 있다(2026-08-07 소진). 쪽수만큼 호출이 늘어난다."""
    assert dev_routes._list_call_estimate(0) == 1
    assert dev_routes._list_call_estimate(883) == 1
    assert dev_routes._list_call_estimate(1000) == 1
    assert dev_routes._list_call_estimate(1001) == 2


@pytest.mark.asyncio
async def test_무장애_예상_호출수는_목록과_교집합을_낸다(
    monkeypatch: pytest.MonkeyPatch,
    _real_place: None,
    _barrier_free_list: type[_FakeBarrierFreeProvider],
    tmp_path,
) -> None:
    """"아직 확인 안 한 장소 수"를 그대로 보여주면 4.6배 부풀려진다.

    종로구는 숙박을 뺀 755건이 대상 후보지만 무장애 목록에 있는 건 164건뿐이고,
    나머지는 호출 없이 "목록에 없음" 행만 쓰고 끝난다. 하루 한도(1,000회) 옆에
    붙는 숫자라 상한이 아니라 실제에 가까운 값이어야 한다.
    """
    monkeypatch.setattr(dev_routes.place_snapshot, "DATA_DIR", tmp_path)
    # 4건 중 하나(3번)는 숙박이라 애초에 대상이 아니다.
    _barrier_free_list.listed = {"1": "12", "3": "32", "9": "12"}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/place_barrier_free"):
            # 2번은 이미 확인했다 — 다시 부르지 않는다.
            return httpx.Response(
                200,
                json=[{"content_id": "2", "fetched_at": "2026-08-25T00:00:00+00:00"}],
            )
        return httpx.Response(200, json=[])

    monkeypatch.setattr(
        dev_routes,
        "status_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    async def fake_fetch(client, api_key, area, district, fetched_at):
        return {
            "1": _snapshot_row("1"),
            "2": _snapshot_row("2"),
            "3": _snapshot_row("3", content_type_id="32"),
            "4": _snapshot_row("4"),
        }

    monkeypatch.setattr(dev_routes.place_snapshot, "fetch_place_rows", fake_fetch)

    with _client() as client:
        payload = client.post("/api/dev/place-sync/reconcile", json={}).json()

    # 대상은 1·2·4(3번은 숙박) → 확인 안 한 것은 1·4 → 목록에 있는 것은 1번뿐이다.
    assert payload["barrier_free_detail_count"] == 1
    assert payload["barrier_free_checked"] is True
    assert _barrier_free_list.calls == [("11", "110")]


@pytest.mark.asyncio
async def test_무장애_목록_조회가_실패하면_확인하지_못한_것으로_알린다(
    monkeypatch: pytest.MonkeyPatch,
    _real_place: None,
    _barrier_free_list: type[_FakeBarrierFreeProvider],
    tmp_path,
) -> None:
    """0건과 "못 봤다"를 뭉개면 화면이 0회를 확정된 값처럼 보여준다."""
    monkeypatch.setattr(dev_routes.place_snapshot, "DATA_DIR", tmp_path)

    class _실패하는_provider(_FakeBarrierFreeProvider):
        async def list_barrier_free_content_ids(self, area_code, district_code):
            raise ProviderUnavailableError("TourAPI(무장애)", detail="HTTP 403")

    monkeypatch.setattr(dev_routes, "RealBarrierFreeProvider", _실패하는_provider)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    monkeypatch.setattr(
        dev_routes,
        "status_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    async def fake_fetch(client, api_key, area, district, fetched_at):
        return {"1": _snapshot_row("1")}

    monkeypatch.setattr(dev_routes.place_snapshot, "fetch_place_rows", fake_fetch)

    with _client() as client:
        payload = client.post("/api/dev/place-sync/reconcile", json={}).json()

    assert payload["barrier_free_detail_count"] == 0
    assert payload["barrier_free_checked"] is False


def _lock_release_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    run_status: str | None,
    calls: list[tuple[str, str]],
) -> TestClient:
    """잠금 1건과 그 실행 1건만 있는 Supabase를 흉내 낸다."""
    monkeypatch.setattr(settings, "supabase_url", "https://project.supabase.co")
    monkeypatch.setattr(settings, "supabase_secret_key", "sb_secret_test")
    run_id = "44444444-4444-4444-8444-444444444444"

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append((request.method, path))
        if path.endswith("/place_sync_locks") and request.method == "GET":
            return httpx.Response(
                200,
                json=[
                    {
                        "area_code": "11",
                        "district_code": "740",
                        "sync_run_id": run_id,
                        "acquired_at": "2026-08-29T00:59:00+09:00",
                        "expires_at": "2026-08-29T02:59:00+09:00",
                    }
                ],
            )
        if path.endswith("/place_sync_runs") and request.method == "GET":
            if run_status is None:
                return httpx.Response(200, json=[])
            return httpx.Response(
                200,
                json=[
                    {
                        "id": run_id,
                        "status": run_status,
                        "started_at": "2026-08-29T00:59:00+09:00",
                        "processed_count": 111,
                        "api_total_count": None,
                    }
                ],
            )
        if path.endswith("/place_sync_locks") and request.method == "DELETE":
            return httpx.Response(200, json=[{"area_code": "11", "district_code": "740"}])
        if path.endswith("/place_sync_runs") and request.method == "PATCH":
            return httpx.Response(200, json=[{"id": run_id, "status": "failed"}])
        return httpx.Response(200, json=[])

    monkeypatch.setattr(
        dev_routes,
        "status_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return TestClient(create_app())


def _release_body(force: bool = False) -> dict[str, object]:
    return {
        "area_code": "11",
        "district_code": "740",
        "sync_run_id": "44444444-4444-4444-8444-444444444444",
        "force": force,
    }


def test_sync_lock_release_refuses_running_run_without_force(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실행이 아직 running이면 force 없이는 풀지 않는다.

    잠금 행만으로는 살아 있는 동기화와 유령 잠금이 구분되지 않는다. 화면이
    경과 시간을 보고 판단하도록 여기서는 거부하고 사실만 돌려준다.
    """
    calls: list[tuple[str, str]] = []
    with _lock_release_client(monkeypatch, run_status="running", calls=calls) as client:
        response = client.post("/api/dev/place-sync/locks/release", json=_release_body())

    assert response.status_code == 200
    payload = response.json()
    assert payload["released"] is False
    assert payload["reason"] == "run_still_running"
    # 거부했으면 지우는 요청이 나가면 안 된다.
    assert ("DELETE", "/rest/v1/place_sync_locks") not in calls


def test_sync_lock_release_deletes_lock_when_run_finished(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실행이 이미 끝났으면 잠금은 확실히 유령이라 force 없이 지운다."""
    calls: list[tuple[str, str]] = []
    with _lock_release_client(monkeypatch, run_status="failed", calls=calls) as client:
        response = client.post("/api/dev/place-sync/locks/release", json=_release_body())

    assert response.status_code == 200
    payload = response.json()
    assert payload["released"] is True
    # 이미 끝난 실행은 다시 마감하지 않는다.
    assert payload["run_abandoned"] is False
    assert ("DELETE", "/rest/v1/place_sync_locks") in calls


def test_sync_lock_release_abandons_running_run_when_forced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """force면 잠금을 지우고 running 실행도 함께 마감한다.

    잠금만 풀고 실행을 running으로 두면 이력이 영영 "진행 중"으로 남는다.
    """
    calls: list[tuple[str, str]] = []
    with _lock_release_client(monkeypatch, run_status="running", calls=calls) as client:
        response = client.post(
            "/api/dev/place-sync/locks/release", json=_release_body(force=True)
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["released"] is True
    assert payload["run_abandoned"] is True
    assert ("PATCH", "/rest/v1/place_sync_runs") in calls


def test_sync_lock_release_rejects_unknown_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """sync_run_id가 다르면 404다.

    그 사이 잠금이 만료되고 다른 실행이 새로 잡았을 수 있어, 구만 보고 지우면
    살아 있는 동기화의 잠금을 뺏는다.
    """
    calls: list[tuple[str, str]] = []
    with _lock_release_client(monkeypatch, run_status="failed", calls=calls) as client:
        response = client.post(
            "/api/dev/place-sync/locks/release",
            json={
                **_release_body(force=True),
                "sync_run_id": "55555555-5555-4555-8555-555555555555",
            },
        )

    assert response.status_code == 404
    assert ("DELETE", "/rest/v1/place_sync_locks") not in calls


def test_apply_passes_closure_extractor_to_sync_service(
    monkeypatch: pytest.MonkeyPatch, _real_place: None, tmp_path
) -> None:
    """반영 실행이 휴무 추출기를 실제로 넘기는지. (TP-231)

    안 넘기면 오류 없이 지금까지와 같은 결과가 나온다 — 주차가 섞인 휴무만
    조용히 빠진 채 저장된다. 그래서 넘기는 것 자체를 못 박는다.
    """
    monkeypatch.setattr(dev_routes.place_snapshot, "DATA_DIR", tmp_path)
    place_snapshot.write_snapshot(
        {"1": _snapshot_row("1")}, tmp_path / "places_api_snapshot_20260809.csv"
    )

    sentinel = object()
    captured: dict[str, object] = {}

    class _FakeService:
        def __init__(self, *args, **kwargs) -> None:
            captured.update(kwargs)

        async def sync(self, area_code, district_code, **kwargs):
            kwargs["on_progress"](SyncProgress(phase="details", processed=1, total=1))
            return PlaceSyncResult(
                status="success",
                dry_run=True,
                sync_run_id=None,
                api_total_count=1,
                processed_count=1,
                success_count=1,
                failed_count=0,
                new_count=0,
                updated_count=1,
                deactivated_count=0,
                detail_target_count=1,
                detail_attempted_count=1,
                reparse_count=0,
                barrier_free_target_count=0,
                barrier_free_attempted_count=0,
                barrier_free_stored_count=0,
                error_summary={},
            )

    monkeypatch.setattr(dev_routes, "PlaceSyncService", _FakeService)
    monkeypatch.setattr(dev_routes, "get_closure_extractor", lambda: sentinel)

    async def fake_missing(self, content_ids):
        return []

    monkeypatch.setattr(
        dev_routes.SupabasePlaceRepository,
        "find_missing_concentration_mappings",
        fake_missing,
    )

    with _client() as client:
        started = client.post(
            "/api/dev/place-sync/apply",
            json={
                "snapshot": "places_api_snapshot_20260809.csv",
                "detail_content_ids": ["1"],
                "added_content_ids": ["1"],
                "dry_run": True,
                "confirm": "11-110",
            },
        ).json()
        client.get(f"/api/dev/place-sync/jobs/{started['job_id']}")

    assert captured["closure_extractor"] is sentinel
