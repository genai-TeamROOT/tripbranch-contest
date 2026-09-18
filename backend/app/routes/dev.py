"""개발자 Ops 패널 전용 API 라우터.

역할: 프론트 `/dev-ops` 화면이 쓰는 호출량 집계, 장소 DB 상태, 스냅샷 대조와
      동기화 실행을 제공한다.
입력: GET /api/dev/api-usage, GET /api/dev/db-status, POST /api/dev/place-sync/*.
출력: 관측용 JSON과 동기화 job 상태. 추천 판정에는 어떤 영향도 주지 않는다.
호출 시점: 개발자가 /dev-ops 화면을 열거나, 대조·반영을 실행할 때.

동기화는 대조와 반영 두 단계로 나눈다. 대조는 목록 API 1회로 스냅샷을 남기고
이전 스냅샷과 비교만 하며 DB를 건드리지 않는다. 반영은 그 대조 결과가 정한
대상에만 상세조회를 보내 DB에 쓴다. 한 버튼으로 합치면 "무엇이 바뀌는지" 모르는
채로 운영 DB에 쓰게 된다.

이 라우터는 `APP_ENV=local`일 때만 main.py가 등록한다 — 배포 환경에서는 경로
자체가 존재하지 않는다(404). DB 쓰기 엔드포인트가 붙을 자리라 노출 범위를
설정이 아니라 등록 여부로 끊는다.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import httpx
from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.agent_context.seoul_realtime_areas import select_nearest_commercial_area
from app.config import settings
from app.domain.models import TourPlacePage
from app.errors import AppError
from app.observability.api_exchanges import get_recorder
from app.observability.api_usage import (
    create_external_client,
    get_usage_snapshot,
    reset_usage,
)
from app.providers.factory import get_closure_extractor
from app.providers.real_place import RealPlaceProvider
from app.providers.tour_barrier_free import RealBarrierFreeProvider
from app.providers.tour_ldong_registry import find_district_name, list_districts
from app.repositories.supabase_places import SupabasePlaceRepository
from app.services import concentration_mapping, place_snapshot
from app.services.place_sync import (
    PlaceSyncResult,
    PlaceSyncService,
    SyncProgress,
    barrier_free_candidate_ids,
    barrier_free_stale_ids,
)
from app.services.place_sync_jobs import (
    SyncJobConflictError,
    SyncJobOutcome,
    get_job_registry,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dev", tags=["dev"])


class DevPanelError(AppError):
    """개발자 패널이 그대로 보여줄 오류.

    HTTPException을 쓰면 main.py의 공통 핸들러가 메시지를 "요청 내용을
    확인해주세요."로 갈아끼워 원인이 사라진다. 원인을 화면에 띄우는 게 이 패널의
    목적이라 AppError 경로를 탄다.
    """

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(
            code="dev_panel_error",
            message=message,
            status_code=status_code,
            retryable=False,
        )


def status_client() -> httpx.AsyncClient:
    """상태 조회 전용 클라이언트 — 일부러 계측을 붙이지 않는다.

    `create_external_client()`를 쓰면 패널이 자기 조회를 자기 표에 집계한다.
    새로고침 한 번에 places / place_enrichments / place_concentration_mappings /
    place_sync_runs / place_sync_locks 다섯 줄이 늘어나, 추천 요청이 동기화
    테이블을 건드린 것처럼 보인다(실제로 추천 경로는 place_sync_*를 읽지 않는다).
    측정 도구가 측정값을 만들면 안 된다.

    반대로 패널에서 실행하는 동기화 job의 외부 호출은 계측 대상이다 — 그건
    관측하려는 트래픽 자체이므로 계측된 클라이언트를 쓴다.
    """
    return httpx.AsyncClient()


def _require_supabase() -> tuple[str, str]:
    """Supabase 자격증명이 없으면 조회 자체가 불가능하다는 걸 그대로 알린다."""
    url = settings.supabase_url.strip()
    key = settings.supabase_secret_key.strip()
    if not url or not key:
        raise DevPanelError(
            "SUPABASE_URL / SUPABASE_SECRET_KEY가 없어 DB 상태를 조회할 수 "
            "없습니다. backend/.env를 확인하세요."
        )
    return url, key


@router.get("/api-usage")
async def get_api_usage() -> dict[str, Any]:
    """이 프로세스가 기동 이후 보낸 외부 호출 집계."""
    return get_usage_snapshot()


@router.post("/api-usage/reset")
async def post_api_usage_reset() -> dict[str, Any]:
    reset_usage()
    return get_usage_snapshot()


class NearestAreaResponse(BaseModel):
    """좌표에 붙일 사람이 읽을 수 있는 지역 이름.

    기기 GPS는 좌표만 주므로 개발자 패널의 위치 뱃지에 찍을 이름이 없다. 서울시
    실시간 상권 82개 지역의 대표 좌표(agent_context/seoul_realtime_areas.py)에서
    최근접을 골라 근사 이름을 만든다 — 외부 호출은 하지 않는다.

    근사치라는 사실을 숨기지 않으려고 distance_km를 항상 함께 준다. 최근접이 2km를
    넘으면 세 값 모두 None이고, 그때는 표시할 이름이 없다는 뜻이다.
    """

    area_code: str | None = None
    area_name: str | None = None
    distance_km: float | None = None


@router.get("/nearest-area")
async def get_nearest_area(location: str) -> NearestAreaResponse:
    """`location`("위도,경도")에 가장 가까운 서울시 상권 지역 이름.

    추천 판정과 무관한 표시 전용 조회다. 좌표를 상권 지역으로 대체해 **조회**하는
    C의 경로(realtime_commercial)와 같은 표를 쓰지만, 여기서는 이름만 꺼내 쓰고
    상권 데이터를 부르지 않는다.
    """

    parts = location.split(",")
    if len(parts) != 2:
        raise DevPanelError("location은 '위도,경도' 형식이어야 합니다.")
    try:
        latitude, longitude = (float(part.strip()) for part in parts)
    except ValueError:
        raise DevPanelError("location의 위도·경도를 숫자로 읽을 수 없습니다.") from None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise DevPanelError("location의 위도·경도 범위가 올바르지 않습니다.")

    nearest = select_nearest_commercial_area(latitude=latitude, longitude=longitude)
    if nearest is None:
        return NearestAreaResponse()
    area, distance_km = nearest
    return NearestAreaResponse(
        area_code=area.code, area_name=area.name, distance_km=distance_km
    )


class CaptureRequest(BaseModel):
    enabled: bool


@router.get("/exchanges")
async def get_exchanges() -> dict[str, Any]:
    """최근 외부 API 요청·응답 원문(자격증명은 마스킹된 상태)."""
    return get_recorder().snapshot()


@router.post("/exchanges/capture")
async def post_exchange_capture(request: CaptureRequest) -> dict[str, Any]:
    """캡처를 켜고 끈다.

    기본은 꺼짐이고, 이 라우터가 APP_ENV=local에서만 등록되므로 배포 환경에서는
    켤 방법이 없다 — 응답 본문 버퍼링이 운영 메모리를 먹지 않게 하는 장치다.
    """
    recorder = get_recorder()
    recorder.set_enabled(request.enabled)
    return recorder.snapshot()


@router.post("/exchanges/clear")
async def post_exchange_clear() -> dict[str, Any]:
    recorder = get_recorder()
    recorder.clear()
    return recorder.snapshot()


@router.get("/db-status")
async def get_db_status(sync_run_limit: int = 10) -> dict[str, Any]:
    """장소 DB의 현재 상태와 최근 동기화 이력.

    장소 요약은 적재된 구별로 나누고 전 구 합계를 함께 준다 — 패널이 탭으로 나눠
    보여준다. 예전에는 설정값(`place_sync_district_code`) 한 구만 세었는데, 같은
    화면의 동기화 이력은 전 구를 보여주고 있어 "용산구 486건 신규"와 "활성 844"가
    나란히 놓였다. 두 숫자가 서로 다른 범위를 세고 있다는 걸 화면에서 알 방법이
    없었다.

    구 목록을 파라미터로 받지 않는 이유: 어떤 구가 적재돼 있는지는 places가 아는
    사실이지 호출자가 정할 값이 아니다. 목록을 넘기게 하면 새 구를 넣고도 화면에
    안 보이는 상태가 생긴다.

    place_enrichments와 집중률 매핑은 구 열이 없어(둘 다 content_id 기준) 전체
    건수만 센다. 구별로 쪼개려면 places와 대조해야 하는데, 이 두 테이블은 장소
    동기화가 건드리지 않아 구별로 볼 실익이 없다.

    `detail_calls_today`는 오늘 detailIntro2를 몇 번 불렀는지를 place_sync_runs에서
    센 값이다. 호출량 표(api-usage)는 프로세스 메모리라 재시작하면 0이 되고
    backend/scripts 실행분도 놓치는데, 이 값은 둘 다 살아남는다. 그래도 하한이다 —
    재시도가 안 세지고, 중간에 죽은 실행은 열이 비어 있다.
    """
    url, key = _require_supabase()

    async with status_client() as client:
        repository = SupabasePlaceRepository(
            supabase_url=url,
            secret_key=key,
            client=client,
            # 2,300행 조회는 챗봇 요청보다 오래 걸린다. 요청 경로용 공통 타임아웃을
            # 그대로 쓰면 패널이 아무 이유 없이 타임아웃으로 보인다.
            timeout_seconds=max(settings.external_api_timeout_seconds, 30.0),
        )
        summaries = await repository.get_place_summaries_by_district()
        barrier_free_counts = await repository.count_barrier_free_by_district()
        enrichment_count = await repository.count_rows("place_enrichments")
        concentration_mapping_count = await repository.count_rows(
            "place_concentration_mappings"
        )
        sync_runs = await repository.list_recent_sync_runs(sync_run_limit)
        locks = await repository.list_sync_locks()
        detail_calls = await repository.summarize_detail_calls_since(
            _today_start_kst()
        )

    districts = [
        {
            **summary,
            # 이름을 못 찾아도 코드는 그대로 남는다 — 화면이 코드로 표시한다.
            "district_name": find_district_name(
                str(summary.get("area_code") or ""),
                str(summary.get("district_code") or ""),
            ),
            **_barrier_free_summary(
                barrier_free_counts.get(
                    (
                        str(summary.get("area_code") or ""),
                        str(summary.get("district_code") or ""),
                    )
                )
            ),
        }
        for summary in _as_summary_list(summaries.get("districts"))
    ]

    overall = summaries.get("overall")
    if isinstance(overall, Mapping):
        overall = {
            **overall,
            # 전 구 합계는 구별 값을 더한다 — 따로 세면 한쪽만 규칙이 바뀐 채 남는다.
            **_barrier_free_summary(
                {
                    "active": sum(c["active"] for c in barrier_free_counts.values()),
                    "total": sum(c["total"] for c in barrier_free_counts.values()),
                }
            ),
        }

    return {
        "overall": overall,
        "districts": districts,
        "place_enrichments_count": enrichment_count,
        "place_concentration_mappings_count": concentration_mapping_count,
        "sync_runs": sync_runs,
        "sync_locks": locks,
        "detail_ttl_days": settings.place_sync_detail_ttl_days,
        "detail_calls_today": {
            **detail_calls,
            "daily_limit": settings.tour_api_daily_call_limit,
        },
    }


def _barrier_free_summary(counts: Mapping[str, int] | None) -> dict[str, int]:
    """무장애 행 수를 요약에 실을 모양으로 바꾼다.

    행이 없는 구는 0으로 채운다 — 키를 빼면 화면이 "아직 안 채웠다"와 "이 응답에는
    그 값이 없다"를 구분할 수 없다.
    """
    return {
        "barrier_free_active": (counts or {}).get("active", 0),
        "barrier_free_total": (counts or {}).get("total", 0),
    }


def _today_start_kst() -> datetime:
    """오늘 0시(KST). TourAPI 일일 한도가 그 경계로 초기화된다."""
    return datetime.now(place_snapshot.KST).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def _as_summary_list(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


# --- 동기화: 대조 → 반영 -----------------------------------------------------


class ReconcileRequest(BaseModel):
    area_code: str | None = None
    district_code: str | None = None
    baseline: str | None = Field(
        default=None,
        description="비교 기준 스냅샷 파일명. 생략하면 저장된 최신 스냅샷을 쓴다.",
    )
    source: Literal["api", "saved"] = Field(
        default="api",
        description=(
            "api는 TourAPI 목록을 새로 받아 스냅샷을 남긴다. saved는 저장된 최신 "
            "스냅샷을 그대로 읽어 대조만 다시 계산한다 — 외부 호출이 0회다."
        ),
    )


class ApplyRequest(BaseModel):
    area_code: str | None = None
    district_code: str | None = None
    snapshot: str = Field(description="반영에 사용할 스냅샷 파일명(대조 단계 산출물)")
    detail_content_ids: list[str] = Field(
        description="상세조회 대상 content_id. 대조 결과에서 정한다."
    )
    added_content_ids: list[str] = Field(
        default_factory=list,
        description=(
            "이번에 새로 들어온 content_id. 반영 후 집중률 매핑 유무를 확인하는 데 쓴다."
        ),
    )
    dry_run: bool = True
    details_limit: int | None = Field(
        default=None,
        ge=1,
        description=(
            "이번 실행에서 부를 상세조회 상한. 새 구는 대상이 수백 건이라 하루 "
            "한도(1,000회)를 한 번에 소진할 수 있어 나눠 받는다. 상한이 걸린 "
            "실행은 비활성화를 건너뛴다 — 목록을 다 처리하지 못했으므로 "
            "'사라진 장소'를 판정할 수 없다."
        ),
    )
    confirm: str = Field(description="'<area>-<district>' 문자열. 오타 실행 방지용")


def _require_real_place_provider() -> str:
    """Fake 구성으로 운영 DB에 쓰는 사고를 막는다.

    Fake provider는 `"테스트 카페"` 같은 값을 돌려준다. 그게 places에 upsert되면
    되돌리기 어렵다(D-042의 조용한 fake 부팅과 같은 함정).
    """
    if settings.resolved_place_provider != "real":
        raise DevPanelError(
            "PLACE_PROVIDER가 real이 아니라 동기화를 실행할 수 없습니다. "
            "fake 데이터가 운영 DB에 들어가는 것을 막기 위한 제한이에요."
        )
    if not settings.tour_api_service_key.strip():
        raise DevPanelError("TOUR_API_SERVICE_KEY가 없어 TourAPI를 호출할 수 없습니다.")
    return settings.tour_api_service_key


def _snapshot_path(name: str) -> Path:
    """스냅샷 파일명을 데이터 디렉터리 안으로 가둔다."""
    candidate = (place_snapshot.DATA_DIR / Path(name).name).resolve()
    if candidate.parent != place_snapshot.DATA_DIR.resolve():
        raise DevPanelError("스냅샷 경로가 올바르지 않습니다.")
    if not candidate.exists():
        raise DevPanelError(f"스냅샷 파일을 찾을 수 없습니다: {candidate.name}")
    return candidate


def _require_snapshot_region(
    snapshot: dict[str, dict[str, str]],
    snapshot_name: str,
    area_code: str,
    district_code: str,
) -> None:
    """스냅샷이 담은 구가 지금 다루는 구와 같은지 내용으로 확인한다.

    다른 구 스냅샷으로 반영하면 그 구에 없는 장소, 즉 **대상 구의 활성 장소 전부**가
    "목록에서 사라진 것"으로 판정돼 비활성화된다(`deactivate_unseen_places`).
    되돌리려면 동기화를 다시 돌려야 하고, 그 사이 추천은 결과 없음이 된다.

    대조 쪽도 같은 함수로 막는다 — 다른 구를 기준으로 잡으면 "전량 삭제 + 전량
    신규"가 나오는데, 이 모양은 실제 대량 변경과 구분이 안 된다(2026-08-20 중구
    사례).
    """
    regions = place_snapshot.snapshot_regions(snapshot)
    expected = (area_code.strip(), district_code.strip())
    if not regions or regions == {expected}:
        return
    found = ", ".join(sorted(f"{area}-{district}" for area, district in regions))
    raise DevPanelError(
        f"스냅샷 {snapshot_name}은 {found} 자료라 "
        f"{expected[0]}-{expected[1]}에 쓸 수 없습니다."
    )


def _district_label(area_code: str, district_code: str) -> str:
    """이력에 적을 구 표기. 이름을 못 찾으면 코드만 적는다."""
    name = find_district_name(area_code, district_code)
    slug = place_snapshot.region_slug(area_code, district_code)
    return f"{name} {slug}" if name else slug


def _record_history(row: dict[str, Any]) -> None:
    """갱신 이력에 한 줄 남긴다. 실패해도 호출한 쪽을 막지 않는다.

    대조와 반영은 TourAPI 일일 한도를 실제로 쓰는 작업이다. 기록을 못 남겼다고
    그 결과까지 실패로 만들면 한도만 태우고 아무것도 남지 않는다 — 이력은 사람이
    읽는 기록이지 대조의 입력이 아니다.
    """
    try:
        place_snapshot.append_history_row(row)
    except OSError:
        logger.exception("갱신 이력을 남기지 못했다 (row=%s)", row)


# 정리 화면의 기본 유지 개수. 1개만 남기면 같은 날 두 번째 대조가 기준을 잃는다 —
# 파일명이 날짜라 첫 대조가 만든 파일을 덮어쓰고, 남은 것이 그것뿐이면
# find_baseline이 빈손으로 돌아와 places 재구성 기준으로 떨어진다.
DEFAULT_SNAPSHOT_KEEP = 2


@router.get("/place-sync/snapshots")
async def get_snapshots(keep: int = DEFAULT_SNAPSHOT_KEEP) -> dict[str, Any]:
    """구별로 스냅샷과 대조 결과가 몇 개씩 있고, 그중 무엇을 지울 수 있는지.

    `keep`을 받아 지울 후보를 함께 돌려준다. 화면이 "지울 파일 보기"를 따로
    부르지 않아도 되고, 미리보기와 실제 정리가 같은 함수(`select_prunable`)를 써서
    보여준 것과 지우는 것이 갈라지지 않는다.

    구 목록은 파일에서 만든다 — DB에 행이 있어도 스냅샷 파일이 없는 구가 있고
    (광진구·구로구·금천구), 그런 구는 정리할 것도 없다.
    """
    districts: dict[tuple[str, str], dict[str, Any]] = {}

    for path in place_snapshot.list_snapshots():
        codes = place_snapshot.district_from_snapshot_name(path.name)
        if codes is None:
            # 구가 이름에 없는 옛 스냅샷. 어느 구 것인지 알 수 없어 정리 후보로도
            # 세지 않는다 — 잘못 묶으면 남의 구 기준을 지운다.
            continue
        districts.setdefault(codes, {"snapshots": [], "reconciliations": []})[
            "snapshots"
        ].append(path.name)

    for codes, entry in districts.items():
        entry["reconciliations"] = [
            path.name
            for path in place_snapshot.list_reconciliations(
                area_code=codes[0], district_code=codes[1]
            )
        ]

    rows = []
    for codes in sorted(districts):
        entry = districts[codes]
        prunable_snapshots = place_snapshot.select_prunable(
            area_code=codes[0], district_code=codes[1], keep=keep
        )
        prunable_reconciliations = place_snapshot.select_prunable(
            area_code=codes[0],
            district_code=codes[1],
            keep=keep,
            prefix=place_snapshot.RECONCILIATION_PREFIX,
        )
        rows.append(
            {
                "area_code": codes[0],
                "district_code": codes[1],
                "district_name": find_district_name(*codes),
                "snapshot_count": len(entry["snapshots"]),
                "reconciliation_count": len(entry["reconciliations"]),
                "latest_snapshot": entry["snapshots"][0] if entry["snapshots"] else None,
                "prunable_snapshots": [path.name for path in prunable_snapshots],
                "prunable_reconciliations": [
                    path.name for path in prunable_reconciliations
                ],
            }
        )

    return {
        "snapshots": [path.name for path in place_snapshot.list_snapshots()],
        "data_dir": str(place_snapshot.DATA_DIR),
        "keep": keep,
        "districts": rows,
    }


class SnapshotPruneRequest(BaseModel):
    """구별로 최근 몇 개만 남기고 나머지를 지운다."""

    keep: int = Field(
        default=DEFAULT_SNAPSHOT_KEEP,
        ge=1,
        description=(
            "구별로 남길 개수. 1 미만은 받지 않는다 — 스냅샷이 0개가 되면 다음 "
            "대조가 기준을 잃고 전량을 신규로 잡아 detailIntro2를 그만큼 낭비한다."
        ),
    )
    include_reconciliations: bool = Field(
        default=True,
        description=(
            "대조 결과 CSV도 같은 개수로 정리할지. 전 구 순회 한 번에 25개가 생겨 "
            "스냅샷보다 빨리 쌓인다."
        ),
    )
    confirm: str = Field(description="확인 문자열. 'PRUNE'이어야 한다.")


@router.post("/place-sync/snapshots/prune")
async def post_prune_snapshots(request: SnapshotPruneRequest) -> dict[str, Any]:
    """구별로 최근 `keep`개만 남기고 옛 파일을 지운다.

    지우기 **전에** 이력에 파일명을 남긴다. 순서를 뒤집으면 지우다 실패했을 때
    무엇이 사라졌는지 아무 데도 안 남는다. 지운 파일은 git 추적 대상이라
    `git show <커밋>:supabase/data/<파일명>`으로 되찾을 수 있는데, 그러려면 이름을
    알아야 한다.

    이름 규칙 밖의 파일은 후보에 오르지 않는다 — 후보는 `select_prunable`이
    `places_api_snapshot_<지역>-<구>_*.csv` glob으로만 고른다.
    """
    if request.confirm.strip() != "PRUNE":
        raise DevPanelError("확인 문자열이 일치하지 않습니다. 'PRUNE'을 입력하세요.")

    districts: set[tuple[str, str]] = set()
    for path in place_snapshot.list_snapshots():
        codes = place_snapshot.district_from_snapshot_name(path.name)
        if codes is not None:
            districts.add(codes)

    deleted: list[str] = []
    failed: list[dict[str, str]] = []
    now = datetime.now(place_snapshot.KST)

    for codes in sorted(districts):
        targets = place_snapshot.select_prunable(
            area_code=codes[0], district_code=codes[1], keep=request.keep
        )
        if request.include_reconciliations:
            targets += place_snapshot.select_prunable(
                area_code=codes[0],
                district_code=codes[1],
                keep=request.keep,
                prefix=place_snapshot.RECONCILIATION_PREFIX,
            )
        if not targets:
            continue

        removed: list[str] = []
        for path in targets:
            try:
                path.unlink()
            except OSError as exc:
                # 한 파일이 안 지워져도 나머지는 계속 지운다. 거기서 멈추면 어떤
                # 구는 정리되고 어떤 구는 안 된 채로 남고, 왜 그런지는 어디에도
                # 안 적힌다.
                logger.exception("스냅샷을 지우지 못했다 (path=%s)", path)
                failed.append({"file": path.name, "error": str(exc)})
                continue
            removed.append(path.name)
            deleted.append(path.name)

        # 실제로 지운 것만 적는다. 지우기 전에 대상 목록을 적으면 실패한 파일까지
        # "지웠다"로 남아, 이력을 보고 되찾으려 할 때 있는 파일을 찾게 된다.
        # 되찾는 근거는 이력이 아니라 git이다 — 이 표는 어느 커밋을 뒤질지 알려준다.
        if not removed:
            continue
        _record_history(
            {
                "일시": f"{now:%Y-%m-%d %H:%M}",
                "구": _district_label(*codes),
                "종류": "정리",
                "기준 스냅샷": "",
                "신규": "",
                "수정": "",
                "삭제": len(removed),
                "상세조회": "",
                "비고": f"구별 {request.keep}개 유지 · 지움 " + ", ".join(removed),
            }
        )

    return {
        "keep": request.keep,
        "deleted": deleted,
        "failed": failed,
        "history_file": place_snapshot.HISTORY_FILE_NAME,
    }


@router.get("/place-sync/districts")
async def get_sync_districts() -> dict[str, Any]:
    """동기화 화면이 고를 수 있는 구와, 코드 입력을 검증할 사전.

    `loaded`는 자료가 있는 구다 — places에 행이 있거나 스냅샷 파일이 있는 구.
    파일만 있는 구도 넣는 이유는, 대조만 하고 아직 반영하지 않은 구가 화면에서
    사라지지 않게 하기 위해서다.

    `known`은 시군구 사전이다. 화면이 "구 추가" 입력을 이걸로 검증한다 — 없는
    코드로 동기화를 걸면 TourAPI가 빈 목록을 돌려주고, 그 결과는 "장소가 0건인
    구"와 구분되지 않는다.

    새 구를 목록에 저장해두지 않는다. 한 번 대조하면 스냅샷 파일이 생기고 반영하면
    places에 행이 생기므로, 자료가 곧 목록이 된다. 따로 저장하면 자료 없이 이름만
    남은 구가 목록에 쌓인다.
    """
    url, key = _require_supabase()

    async with status_client() as client:
        repository = SupabasePlaceRepository(
            supabase_url=url,
            secret_key=key,
            client=client,
            timeout_seconds=max(settings.external_api_timeout_seconds, 30.0),
        )
        summaries = await repository.get_place_summaries_by_district()

    loaded: dict[tuple[str, str], dict[str, Any]] = {}
    for summary in _as_summary_list(summaries.get("districts")):
        area_code = str(summary.get("area_code") or "")
        district_code = str(summary.get("district_code") or "")
        active_count = int(summary.get("active", 0) or 0)
        loaded[(area_code, district_code)] = {
            "area_code": area_code,
            "district_code": district_code,
            "district_name": find_district_name(area_code, district_code),
            "place_count": summary.get("total", 0),
            "active_count": active_count,
            "latest_snapshot": None,
            "list_call_estimate": _list_call_estimate(active_count),
        }

    for path in place_snapshot.list_snapshots():
        codes = place_snapshot.district_from_snapshot_name(path.name)
        if codes is None:
            continue
        entry = loaded.setdefault(
            codes,
            {
                "area_code": codes[0],
                "district_code": codes[1],
                "district_name": find_district_name(*codes),
                "place_count": 0,
                "active_count": 0,
                "latest_snapshot": None,
                "list_call_estimate": 1,
            },
        )
        # 최신순 목록이라 그 구에서 처음 만난 파일이 가장 최근 것이다.
        if entry["latest_snapshot"] is None:
            entry["latest_snapshot"] = path.name

    return {
        "loaded": [loaded[key] for key in sorted(loaded)],
        "known": list_districts(settings.place_sync_area_code),
    }


class SyncLockReleaseRequest(BaseModel):
    """손으로 푸는 동기화 잠금 해제 요청."""

    area_code: str
    district_code: str
    # 소유자를 함께 받는다. 그 사이 잠금이 만료되고 다른 실행이 새로 잡았을 수
    # 있어, 구만 보고 지우면 살아 있는 동기화의 잠금을 뺏는다.
    sync_run_id: str
    # 실행이 아직 running일 때는 이 값이 참이어야 지운다. 화면이 경과 시간을
    # 보여주고 사용자가 한 번 더 확인하게 하기 위한 장치다.
    force: bool = False


@router.post("/place-sync/locks/release")
async def post_sync_lock_release(request: SyncLockReleaseRequest) -> dict[str, Any]:
    """남은 동기화 잠금을 손으로 푼다.

    서버가 강제 종료되면 잠금이 DB에 남아 최대 TTL(2시간) 동안 그 구의 동기화가
    막힌다 — 재시작해도 안 풀린다. 정리 코드가 프로세스와 함께 죽기 때문이다
    (2026-08-29 강동구).

    잠금 행만으로는 "지금 돌고 있는 동기화"와 "죽은 프로세스가 남긴 유령 잠금"이
    구분되지 않는다. 그래서 잠금을 만든 실행의 상태를 보고 갈라 처리한다.

    - 실행이 이미 끝났거나(success/failed) 기록이 없으면 바로 지운다. 잠금이
      유령인 것이 확실하다.
    - 실행이 running이면 ``force``가 참이어야 지운다. 살아 있을 수도 죽었을
      수도 있어, 화면이 경과 시간을 보여주고 사용자가 판단하게 한다.

    지울 때 running 실행도 함께 failed로 마감한다. 잠금만 풀면 이력이 영영
    "진행 중"으로 남아 통계와 화면이 실제와 어긋난다.
    """
    url, key = _require_supabase()
    try:
        run_id = UUID(request.sync_run_id)
    except ValueError:
        raise DevPanelError("sync_run_id가 UUID 형식이 아닙니다.") from None

    async with status_client() as client:
        repository = SupabasePlaceRepository(
            supabase_url=url,
            secret_key=key,
            client=client,
            timeout_seconds=max(settings.external_api_timeout_seconds, 30.0),
        )
        locks = await repository.list_sync_locks()
        target = next(
            (
                lock
                for lock in locks
                if str(lock.get("area_code")) == request.area_code
                and str(lock.get("district_code")) == request.district_code
                and str(lock.get("sync_run_id")) == str(run_id)
            ),
            None,
        )
        if target is None:
            # 이미 풀렸거나 다른 실행이 새로 잡았다. 어느 쪽이든 지울 대상이 아니다.
            raise DevPanelError(
                "그 잠금을 찾지 못했습니다. 이미 풀렸거나 다른 실행이 새로 잡았습니다.",
                status_code=404,
            )

        run_status = target.get("run_status")
        if run_status == "running" and not request.force:
            return {
                "released": False,
                "reason": "run_still_running",
                "message": (
                    "이 잠금을 만든 동기화가 아직 진행 중으로 기록돼 있습니다. "
                    "다른 곳에서 동기화가 돌고 있다면 끊깁니다."
                ),
                "lock": target,
            }

        released = await repository.delete_sync_lock(
            request.area_code, request.district_code, run_id
        )
        if not released:
            raise DevPanelError(
                "잠금을 지우지 못했습니다. 그 사이 상태가 바뀌었을 수 있습니다.",
                status_code=409,
            )

        abandoned = False
        if run_status == "running":
            abandoned = await repository.abandon_sync_run(
                run_id,
                reason=(
                    "개발자 패널에서 잠금을 수동 해제했다. 어디까지 처리했는지는 "
                    "죽은 프로세스만 알므로 카운터는 그대로 둔다."
                ),
                completed_at=datetime.now(UTC),
            )
        return {
            "released": True,
            "run_abandoned": abandoned,
            "lock": target,
        }


async def _reconcile_from_saved(
    area: str, district: str, request: ReconcileRequest
) -> dict[str, Any]:
    """저장된 스냅샷을 그대로 읽어 대조만 다시 계산한다. 외부 호출이 0회다.

    대조 결과는 스냅샷 두 장에서 순수하게 계산된다 — `build_reconciliation_rows`는
    파일만 읽고 API도 DB도 부르지 않는다. 그래서 어제 뜬 스냅샷이 남아 있으면
    목록 조회를 다시 할 이유가 없다. 오늘 상세조회 한도가 없어 반영을 못 하고
    다음 날 이어서 하는 경우가 바로 그것이다.

    스냅샷 파일은 새로 쓰지 않는다. 오늘 날짜로 다시 쓰면 어제 목록이 오늘 것으로
    둔갑하고, 그 파일이 다음 대조의 기준이 되면서 하루치 변화가 통째로 사라진다.
    대조 결과 CSV도 쓰지 않는다 — 어제 스냅샷의 대조 결과가 오늘 날짜 파일로
    남으면 어느 시점 자료인지 알 수 없게 된다.

    무장애 예상 호출수는 세지 않는다. 그러려면 무장애 목록을 불러야 하는데, 그건
    이 경로가 없애려던 외부 호출이다. `barrier_free_checked=False`로 돌려주어
    화면이 "0회"가 아니라 "확인하지 못했다"로 읽게 한다.
    """
    logger.info(
        "장소 대조 source=saved area=%s-%s (외부 호출 없음)", area, district
    )
    snapshots = place_snapshot.list_snapshots(
        area_code=area, district_code=district
    )
    if not snapshots:
        raise DevPanelError(
            f"{area}-{district}에 저장된 스냅샷이 없습니다. 전 구 대조를 먼저 "
            "실행하세요."
        )

    snapshot_path = snapshots[0]
    current = place_snapshot.load_snapshot(snapshot_path)
    _require_snapshot_region(current, snapshot_path.name, area, district)

    baseline_path = (
        _snapshot_path(request.baseline)
        if request.baseline
        else place_snapshot.find_baseline(
            area_code=area, district_code=district, exclude=snapshot_path
        )
    )
    if baseline_path is not None:
        baseline = place_snapshot.load_snapshot(baseline_path)
        baseline_label = baseline_path.name
        baseline_source = "file"
    else:
        baseline, baseline_source = await _baseline_from_database(area, district)
        baseline_label = (
            f"places@{datetime.now(place_snapshot.KST):%Y-%m-%d}" if baseline else None
        )

    if not baseline:
        # 기준이 없으면 전량이 신규가 된다. API 경로에서는 새 구를 처음 적재하는
        # 정상 경로지만, 저장된 스냅샷을 다시 쓰는 자리에서는 그럴 이유가 없다 —
        # 스냅샷이 있는데 기준이 없다는 건 앞 세대가 지워졌다는 뜻이고, 그대로
        # 진행하면 이미 DB에 있는 장소에 detailIntro2를 전량 다시 쓴다.
        raise DevPanelError(
            f"{area}-{district}는 기준으로 삼을 앞 세대 스냅샷이 없습니다. 그대로 "
            f"진행하면 {len(current)}건 전부가 신규로 잡혀 상세조회를 그만큼 "
            "씁니다. 스냅샷 보관 개수를 2 이상으로 두거나, 이 구만 전 구 대조로 "
            "다시 받으세요."
        )

    _require_snapshot_region(baseline, baseline_label or "", area, district)
    baseline_columns = list(next(iter(baseline.values()), {}).keys())
    compared = place_snapshot.comparable_columns(baseline_columns)
    skipped = [
        column for column in place_snapshot.COMPARED_COLUMNS if column not in compared
    ]
    rows = place_snapshot.build_reconciliation_rows(baseline, current, compared)
    detail_ids, excluded_ids = place_snapshot.select_detail_targets(rows)
    backfill_ids, backfill_checked = await _detail_backfill_ids(
        area, district, current, detail_ids
    )

    counts = {"added": 0, "removed": 0, "updated": 0}
    for row in rows:
        counts[str(row["change_type"])] += 1

    _record_history(
        {
            "일시": f"{datetime.now(place_snapshot.KST):%Y-%m-%d %H:%M}",
            "구": _district_label(area, district),
            # "대조"와 가른다. 목록을 새로 받지 않았고 스냅샷도 안 남겼다.
            "종류": "재사용",
            "기준 스냅샷": baseline_label,
            "신규": counts["added"],
            "수정": counts["updated"],
            "삭제": counts["removed"],
            "상세조회": len(detail_ids) + len(backfill_ids),
            "비고": (
                f"저장된 {snapshot_path.name} ({len(current)}건) 재사용 · "
                f"보충 {len(backfill_ids)} · 제외 {len(excluded_ids)} · 외부 호출 0회"
            ),
        }
    )

    return {
        "area_code": area,
        "district_code": district,
        "snapshot": snapshot_path.name,
        "snapshot_count": len(current),
        "baseline": baseline_label,
        "baseline_source": baseline_source,
        "baseline_count": len(baseline),
        "skipped_columns": skipped,
        "counts": counts,
        "detail_content_ids": sorted(detail_ids),
        "detail_excluded_ids": sorted(excluded_ids),
        "detail_backfill_ids": backfill_ids,
        "detail_backfill_checked": backfill_checked,
        "barrier_free_detail_count": 0,
        "barrier_free_checked": False,
        "rows": rows,
        "source": "saved",
    }


@router.post("/place-sync/reconcile")
async def post_reconcile(request: ReconcileRequest) -> dict[str, Any]:
    """목록을 1회 조회해 스냅샷을 남기고 이전 스냅샷과 대조한다. DB에는 쓰지 않는다.

    기준으로 쓸 스냅샷 파일이 없으면 places에서 그 구의 장소를 읽어 기준을 만든다.
    그러지 않으면 전량이 신규로 잡혀, 이미 DB에 있는 장소에 detailIntro2를 한 번씩
    더 쓴다(용산구 486건이면 하루 한도 1,000회의 절반이다).

    DB로 만든 기준은 파일로 남기지 않는다. 파일명 날짜가 오늘과 겹치면 이번 대조가
    쓰는 파일과 이름이 같아, 만들자마자 덮어써지고 기준이 사라진다. 무엇과
    비교했는지는 대조 결과 CSV의 baseline 칸에 `places@2026-08-21` 꼴로 남긴다.
    """
    area = request.area_code or settings.place_sync_area_code
    district = request.district_code or settings.place_sync_district_code
    if request.source == "saved":
        # 외부 호출이 없으므로 TourAPI 인증키도 요구하지 않는다.
        return await _reconcile_from_saved(area, district, request)

    api_key = _require_real_place_provider()
    now = datetime.now(place_snapshot.KST)
    # source를 로그에 남긴다. 두 모드가 같은 엔드포인트를 써서 접근 로그 한 줄만
    # 보면 목록을 새로 받았는지 저장된 스냅샷을 읽었는지 구분되지 않는다.
    logger.info("장소 대조 source=api area=%s-%s", area, district)

    async with create_external_client() as client:
        current = await place_snapshot.fetch_place_rows(
            client, api_key, area, district, now
        )

    snapshot_path = place_snapshot.DATA_DIR / place_snapshot.snapshot_file_name(
        area, district, now
    )
    baseline_path = (
        _snapshot_path(request.baseline)
        if request.baseline
        else place_snapshot.find_baseline(
            area_code=area, district_code=district, exclude=snapshot_path
        )
    )
    # 같은 날 다시 대조하면 덮어쓴다. 스냅샷은 git 추적 대상이라 덮어쓴 차이가
    # 그대로 diff로 남는다.
    place_snapshot.write_snapshot(current, snapshot_path)

    barrier_free_calls, barrier_free_checked = await _barrier_free_detail_count(
        api_key, area, district, current
    )

    if baseline_path is not None:
        baseline = place_snapshot.load_snapshot(baseline_path)
        baseline_label = baseline_path.name
        baseline_source = "file"
    else:
        baseline, baseline_source = await _baseline_from_database(area, district)
        baseline_label = f"places@{now:%Y-%m-%d}" if baseline else None

    if not baseline:
        result = {
            "area_code": area,
            "district_code": district,
            "snapshot": snapshot_path.name,
            "snapshot_count": len(current),
            "baseline": None,
            "baseline_source": baseline_source,
            "skipped_columns": [],
            "counts": {"added": 0, "removed": 0, "updated": 0},
            "detail_content_ids": sorted(current),
            "detail_excluded_ids": [],
            "detail_backfill_ids": [],
            "detail_backfill_checked": True,
            "barrier_free_detail_count": barrier_free_calls,
            "barrier_free_checked": barrier_free_checked,
            "rows": [],
            "message": _NO_BASELINE_MESSAGES[baseline_source],
            "source": "api",
        }
        _record_history(
            {
                "일시": f"{now:%Y-%m-%d %H:%M}",
                "구": _district_label(area, district),
                "종류": "대조",
                # 기준이 없으면 그 사실을 적는다. 빈 칸으로 두면 "기준이 있었는데
                # 안 적었다"와 구분되지 않는다.
                "기준 스냅샷": "없음",
                "신규": len(current),
                "수정": 0,
                "삭제": 0,
                "상세조회": len(current),
                "비고": (
                    f"새 스냅샷 {snapshot_path.name} ({len(current)}건) · "
                    f"기준이 없어 전량 신규 · 무장애 {barrier_free_calls}"
                ),
            }
        )
        return result

    _require_snapshot_region(baseline, baseline_label or "", area, district)
    baseline_columns = list(next(iter(baseline.values()), {}).keys())
    compared = place_snapshot.comparable_columns(baseline_columns)
    # 조용히 빼면 "안 바뀌었다"와 "안 봤다"가 결과에서 구분되지 않는다.
    skipped = [
        column for column in place_snapshot.COMPARED_COLUMNS if column not in compared
    ]
    rows = place_snapshot.build_reconciliation_rows(baseline, current, compared)
    reconciliation_path = (
        place_snapshot.DATA_DIR
        / place_snapshot.reconciliation_file_name(area, district, now)
    )
    place_snapshot.write_reconciliation(
        rows, reconciliation_path, baseline_name=baseline_label or "", compared_at=now
    )
    detail_ids, excluded_ids = place_snapshot.select_detail_targets(rows)
    backfill_ids, backfill_checked = await _detail_backfill_ids(
        area, district, current, detail_ids
    )

    counts = {"added": 0, "removed": 0, "updated": 0}
    for row in rows:
        counts[str(row["change_type"])] += 1

    _record_history(
        {
            "일시": f"{now:%Y-%m-%d %H:%M}",
            "구": _district_label(area, district),
            "종류": "대조",
            "기준 스냅샷": baseline_label,
            "신규": counts["added"],
            "수정": counts["updated"],
            "삭제": counts["removed"],
            # 반영이 실제로 부를 수 — 변경분에 지난 실행에서 못 채운 건이 더해진다.
            # 변경분만 적으면 이력이 실제 호출량을 설명하지 못한다.
            "상세조회": len(detail_ids) + len(backfill_ids),
            "비고": (
                f"새 스냅샷 {snapshot_path.name} ({len(current)}건) · "
                f"보충 {len(backfill_ids)} · 제외 {len(excluded_ids)} · "
                f"무장애 {barrier_free_calls}"
            ),
        }
    )
    return {
        "area_code": area,
        "district_code": district,
        "snapshot": snapshot_path.name,
        "snapshot_count": len(current),
        "baseline": baseline_label,
        "baseline_source": baseline_source,
        "baseline_count": len(baseline),
        "reconciliation": reconciliation_path.name,
        "skipped_columns": skipped,
        "counts": counts,
        "detail_content_ids": sorted(detail_ids),
        "detail_excluded_ids": sorted(excluded_ids),
        "detail_backfill_ids": backfill_ids,
        "detail_backfill_checked": backfill_checked,
        "barrier_free_detail_count": barrier_free_calls,
        "barrier_free_checked": barrier_free_checked,
        "rows": rows,
        "source": "api",
    }


_NO_BASELINE_MESSAGES = {
    "none": (
        "기준 스냅샷도 없고 DB에도 이 구의 장소가 없어 대조를 건너뛰었습니다. "
        "전량이 신규로 취급됩니다."
    ),
    "unavailable": (
        "기준 스냅샷이 없고 SUPABASE_URL / SUPABASE_SECRET_KEY가 없어 DB를 "
        "확인하지 못했습니다. 전량이 신규로 취급됩니다 — DB에 이 구의 장소가 "
        "이미 있다면 상세조회를 그만큼 낭비하게 됩니다."
    ),
}


def _list_call_estimate(place_count: int) -> int:
    """대조 한 번이 쓸 목록 API 호출 수.

    `areaBasedList2`도 오퍼레이션 단위로 일일 한도가 걸려 있다(2026-08-07 소진).
    한 번에 1회라 작아 보이지만 구를 바꿔가며 누르면 그만큼 쌓이고, 지금은 화면에
    그 사실이 보이지 않는다.

    쪽수는 DB의 활성 장소 수로 어림한다 — 실제 기준은 TourAPI의 totalCount라
    불러봐야 알 수 있고, 그걸 알려고 부르면 세려던 호출을 먼저 쓰게 된다.
    """
    pages, remainder = divmod(max(place_count, 0), place_snapshot.LIST_PAGE_SIZE)
    return max(1, pages + (1 if remainder else 0))


async def _detail_backfill_ids(
    area_code: str,
    district_code: str,
    current: Mapping[str, Mapping[str, str]],
    detail_ids: frozenset[str],
) -> tuple[list[str], bool]:
    """이번 반영이 변경분과 **함께** 부를 장소들.

    동기화는 대조가 정한 변경분 외에 pending·failed 장소도 부른다
    (`PlaceSyncService._select_targets`). 그걸 빼고 계산하면 화면이 "상세조회
    15회"라고 해놓고 실제로는 157회를 쓴다 — 2026-08-21 종로구 대조가 그 상태였다.
    한도가 왜 줄었는지 아무도 설명할 수 없게 된다.

    목록에 없는 장소는 제외한다. 동기화는 이번 목록에 있는 장소만 훑으므로,
    비활성이라 목록에서 빠진 장소는 아무리 pending이어도 불리지 않는다.

    두 번째 반환값은 "확인했는가"다. 자격증명이 없어 못 본 것과 "보충할 게 없다"를
    같은 0으로 뭉개면, 화면이 예상 호출수를 확정된 값처럼 보여주게 된다.
    """
    url = settings.supabase_url.strip()
    key = settings.supabase_secret_key.strip()
    if not url or not key:
        return [], False

    async with status_client() as client:
        repository = SupabasePlaceRepository(
            supabase_url=url,
            secret_key=key,
            client=client,
            timeout_seconds=max(settings.external_api_timeout_seconds, 30.0),
        )
        pending = await repository.list_detail_backfill_ids(area_code, district_code)
    return [
        content_id
        for content_id in pending
        if content_id in current and content_id not in detail_ids
    ], True


async def _barrier_free_detail_count(
    api_key: str,
    area_code: str,
    district_code: str,
    current: Mapping[str, Mapping[str, str]],
) -> tuple[int, bool]:
    """이번 반영이 무장애 상세(detailWithTour2)를 몇 번 부를지.

    무장애 목록을 1회 불러 실제 대상을 센다. 목록을 부르지 않고 "아직 확인하지 않은
    장소 수"를 그대로 보여주면 4.6배 부풀려진다 — 종로구에서 755회로 표시되지만
    실제 상세 호출은 164회다. 무장애 레코드가 있는 장소가 19%뿐이기 때문이다.
    한도 옆에 붙는 숫자라 상한보다 실제에 가까운 값이 낫고, 목록은 구당 1회다.

    순서와 규칙을 동기화(`_sync_barrier_free`)와 똑같이 맞춘다 — 목록 → 등록된
    장소만 추림 → 확인 시각 조회 → TTL 판정. 조건을 따로 적으면 한쪽만 고쳤을 때
    화면이 실제와 다른 수를 보여주게 된다.

    두 번째 반환값은 "확인했는가"다. 자격증명이 없거나 목록 조회가 실패해 못 본
    것과 "부를 게 없다"를 같은 0으로 뭉개면, 화면이 0회를 확정된 값처럼 보여준다.
    """
    url = settings.supabase_url.strip()
    key = settings.supabase_secret_key.strip()
    if not url or not key:
        return 0, False

    candidates = barrier_free_candidate_ids(
        {
            content_id: str(row.get("content_type_id", "")).strip()
            for content_id, row in current.items()
        }
    )
    if not candidates:
        return 0, True

    # 목록 조회는 계측 대상 트래픽이다 — 상태 조회와 달리 실제로 한도를 쓴다.
    async with create_external_client() as client:
        provider = RealBarrierFreeProvider(
            api_key=api_key,
            client=client,
            timeout_seconds=settings.external_api_timeout_seconds,
        )
        try:
            listed = await provider.list_barrier_free_content_ids(
                area_code, district_code
            )
        except AppError:
            return 0, False

    registered = [content_id for content_id in candidates if content_id in listed]
    if not registered:
        return 0, True

    async with status_client() as client:
        repository = SupabasePlaceRepository(
            supabase_url=url,
            secret_key=key,
            client=client,
            timeout_seconds=max(settings.external_api_timeout_seconds, 30.0),
        )
        already = await repository.list_barrier_free_fetched_at(registered)
    return len(
        barrier_free_stale_ids(
            registered,
            already,
            now=datetime.now(UTC),
            ttl=timedelta(days=settings.place_sync_detail_ttl_days),
        )
    ), True


async def _baseline_from_database(
    area_code: str, district_code: str
) -> tuple[dict[str, dict[str, str]], str]:
    """places에서 그 구의 활성 장소를 읽어 기준 스냅샷을 만든다.

    Supabase 자격증명은 여기서만 필요하다 — 파일 기준이 있는 대조는 DB를 아예
    건드리지 않으므로, 위에서 미리 요구하면 되던 일이 안 되게 만든다. 자격증명이
    없으면 "DB에 없다"가 아니라 "확인하지 못했다"로 돌려준다. 두 가지를 같은
    결과로 뭉개면 화면이 낭비되는 상세조회의 이유를 설명할 수 없다.

    비활성 장소는 넣지 않는다. 목록에서 사라져서 비활성이 된 것이라, 넣으면 대조할
    때마다 계속 "삭제"로 잡힌다.
    """
    url = settings.supabase_url.strip()
    key = settings.supabase_secret_key.strip()
    if not url or not key:
        return {}, "unavailable"

    async with status_client() as client:
        repository = SupabasePlaceRepository(
            supabase_url=url,
            secret_key=key,
            client=client,
            timeout_seconds=max(settings.external_api_timeout_seconds, 30.0),
        )
        rows = await repository.list_region_place_rows(
            area_code, district_code, place_snapshot.SNAPSHOT_COLUMNS
        )
    baseline = place_snapshot.snapshot_rows_from_db(rows)
    return baseline, "database" if baseline else "none"


class _SnapshotListProvider:
    """목록은 스냅샷에서, 상세조회는 TourAPI에서.

    대조에 쓴 목록과 DB에 반영하는 목록이 같은 데이터임을 보장한다 — 다시 조회하면
    그 사이 원본이 바뀌어 대조 결과와 실제 반영분이 어긋난다. 목록 API 호출도 0회다.
    """

    def __init__(self, snapshot_path: Path, inner: RealPlaceProvider) -> None:
        self._records = place_snapshot.records_from_snapshot(snapshot_path)
        self._inner = inner

    async def list_places_by_area(
        self,
        area_code: str,
        district_code: str,
        page_no: int,
        num_of_rows: int = 100,
    ) -> TourPlacePage:
        start = (page_no - 1) * num_of_rows
        return TourPlacePage(
            page_no=page_no,
            num_of_rows=num_of_rows,
            total_count=len(self._records),
            places=tuple(self._records[start : start + num_of_rows]),
        )

    async def get_operating_details(self, content_id: str, content_type_id: str):
        return await self._inner.get_operating_details(content_id, content_type_id)


def _prune_reconciliations_after_apply(
    area_code: str,
    district_code: str,
    result: PlaceSyncResult,
    request: ApplyRequest,
) -> list[str]:
    """반영이 끝난 구의 대조 결과 CSV를 지운다. 지운 파일명을 돌려준다.

    대조 CSV는 파생물이다 — 스냅샷 두 개를 `build_reconciliation_rows`에 넣으면
    같은 내용이 다시 나오고, 외부 호출도 DB 조회도 없다. 반영이 끝났다면 그
    변경분은 DB에 들어갔으므로 파일로 들고 있을 이유가 없다. git이 추적하므로
    되짚어야 할 때는 `git show <커밋>:supabase/data/<파일명>`으로 꺼낸다.

    스냅샷은 지우지 않는다. 그건 파생물이 아니라 다음 대조의 기준이다.

    지우지 않는 경우가 둘 있다.

    - `dry_run`: DB에 아무것도 안 썼다. 변경분은 그대로 남아 있다.
    - `details_limit`이 걸린 실행: 비활성화를 건너뛰므로(place_sync.py) 대조가
      찾은 "삭제" 행은 DB에 반영되지 않았다. 그 기록을 지우면 무엇이 남았는지
      알 방법이 없다.

    `partial_failure`는 지운다. 한도 소진으로 상세를 일부 못 채운 것이고, 목록
    반영과 비활성화는 끝났다 — 대조 CSV가 담는 것은 목록 단위 변경이다.
    """
    if request.dry_run or request.details_limit is not None:
        return []
    if result.status == "failed":
        return []

    removed: list[str] = []
    for path in place_snapshot.list_reconciliations(
        area_code=area_code, district_code=district_code
    ):
        try:
            path.unlink()
        except OSError:
            # 한 파일이 안 지워져도 반영 자체는 성공이다. 여기서 예외를 올리면
            # DB에 다 쓴 실행이 실패로 기록된다.
            logger.exception("대조 결과를 지우지 못했다 (path=%s)", path)
            continue
        removed.append(path.name)
    return removed


@router.post("/place-sync/apply")
async def post_apply(request: ApplyRequest) -> dict[str, Any]:
    """대조가 정한 대상에만 상세조회를 보내 DB에 반영한다."""
    api_key = _require_real_place_provider()
    url, key = _require_supabase()
    area = request.area_code or settings.place_sync_area_code
    district = request.district_code or settings.place_sync_district_code

    expected = f"{area}-{district}"
    if request.confirm.strip() != expected:
        raise DevPanelError(
            f"확인 문자열이 일치하지 않습니다. '{expected}'를 입력하세요."
        )

    snapshot_path = _snapshot_path(request.snapshot)
    _require_snapshot_region(
        place_snapshot.load_snapshot(snapshot_path),
        snapshot_path.name,
        area,
        district,
    )
    detail_ids = frozenset(request.detail_content_ids)

    async def run(on_progress: Callable[[SyncProgress], None]) -> SyncJobOutcome:
        # 동기화가 보내는 외부 호출은 계측 대상이다 — 상태 조회와 달리 이건
        # 관측하려는 트래픽 자체다.
        async with create_external_client() as client:
            provider = RealPlaceProvider(
                api_key=api_key,
                client=client,
                timeout_seconds=settings.external_api_timeout_seconds,
            )
            repository = SupabasePlaceRepository(
                supabase_url=url,
                secret_key=key,
                client=client,
                timeout_seconds=max(settings.external_api_timeout_seconds, 30.0),
            )
            service = PlaceSyncService(
                _SnapshotListProvider(snapshot_path, provider),
                repository,
                # 무장애 정보(KorWithService2)는 같은 인증키를 쓰지만 다른
                # 서비스라 호출도 따로 나간다. 상세조회와 같은 실행에서 함께
                # 채워야 "스냅샷 반영은 됐는데 무장애만 비어 있는" 상태가 남지
                # 않는다.
                barrier_free_provider=RealBarrierFreeProvider(
                    api_key=api_key,
                    client=client,
                    timeout_seconds=settings.external_api_timeout_seconds,
                ),
                # 정규식이 못 읽은 휴무 문구는 이 실행에서 LLM이 읽는다(TP-231).
                # 상세조회와 같은 실행에서 채워야 "장소는 들어왔는데 휴무만 옛
                # 파서 결과인" 상태가 남지 않는다 — 무장애를 여기서 채우는 것과
                # 같은 이유다. 꺼져 있으면 None이고 정규식 결과만 저장한다.
                closure_extractor=get_closure_extractor(),
                page_size=settings.place_sync_page_size,
                detail_concurrency=settings.place_sync_detail_concurrency,
                detail_ttl_days=settings.place_sync_detail_ttl_days,
                retry_count=settings.external_api_retry_count,
            )
            result = await service.sync(
                area,
                district,
                dry_run=request.dry_run,
                details_limit=request.details_limit,
                detail_content_ids=detail_ids,
                on_progress=on_progress,
            )
            # 새로 들어온 장소는 집중률 매핑이 없다. 매핑이 없으면 혼잡도 조회를
            # 아예 건너뛰므로(enrichment_service), 알리지 않으면 그 장소만 조용히
            # 판정에서 빠진 채 남는다. 매핑 적재는 별도 스크립트 소관이라 여기서는
            # 확인만 한다.
            unmapped = await repository.find_missing_concentration_mappings(
                request.added_content_ids
            )
            # 대조 CSV는 파생물이라 반영이 끝나면 들고 있을 이유가 없다.
            # 이력을 쓰기 전에 지워, 몇 개를 지웠는지가 같은 줄에 남게 한다.
            pruned = _prune_reconciliations_after_apply(area, district, result, request)

            # 반영도 이력에 남긴다. 대조만 하고 반영하지 않은 구가 생기므로
            # (전 구 순회는 한도를 넘길 구를 건너뛴다), 대조 줄만 있으면 "이 변경이
            # DB에 실제로 들어갔는가"를 이력으로 답할 수 없다.
            _record_history(
                {
                    "일시": f"{datetime.now(place_snapshot.KST):%Y-%m-%d %H:%M}",
                    "구": _district_label(area, district),
                    "종류": "반영",
                    "기준 스냅샷": snapshot_path.name,
                    "신규": result.new_count,
                    "수정": result.updated_count,
                    # 상한이 걸린 실행은 비활성화를 건너뛴다. 0으로 적으면 "사라진
                    # 장소가 없었다"로 읽히지만 실제로는 보지도 않았다.
                    "삭제": (
                        result.deactivated_count
                        if request.details_limit is None
                        else "미판정"
                    ),
                    "상세조회": result.detail_attempted_count,
                    "비고": " · ".join(
                        part
                        for part in (
                            result.status,
                            f"무장애 {result.barrier_free_stored_count}/"
                            f"{result.barrier_free_attempted_count}",
                            f"실패 {result.failed_count}" if result.failed_count else "",
                            (
                                f"상한 {request.details_limit}건(비활성화 건너뜀)"
                                if request.details_limit is not None
                                else ""
                            ),
                            (
                                ", ".join(sorted(result.error_summary))
                                if result.error_summary
                                else ""
                            ),
                            f"대조 CSV {len(pruned)}개 삭제" if pruned else "",
                        )
                        if part
                    ),
                }
            )
            return SyncJobOutcome(result=result, unmapped_new_place_ids=unmapped)

    try:
        job = get_job_registry().start(
            {
                "area_code": area,
                "district_code": district,
                "snapshot": snapshot_path.name,
                "dry_run": request.dry_run,
                "detail_target_count": len(detail_ids),
                "added_count": len(request.added_content_ids),
                # 상한이 걸린 실행은 비활성화를 건너뛴다. 화면이 그 사실을 알려면
                # job 파라미터에 남아 있어야 한다.
                "details_limit": request.details_limit,
            },
            run,
        )
    except SyncJobConflictError as exc:
        raise DevPanelError(str(exc), status_code=409) from None
    return job.snapshot()


@router.get("/place-sync/jobs/{job_id}")
async def get_sync_job(job_id: str) -> dict[str, Any]:
    job = get_job_registry().get(job_id)
    if job is None:
        raise DevPanelError("해당 job을 찾을 수 없습니다.", status_code=404)
    return job.snapshot()


@router.get("/place-sync/jobs")
async def get_running_sync_job() -> dict[str, Any]:
    job = get_job_registry().running()
    return {"running": job.snapshot() if job is not None else None}


# ── 집중률 매핑 ────────────────────────────────────────────────────────────────
#
# 매핑이 없는 장소는 혼잡도 조회를 통째로 건너뛴다(enrichment_service). 오류가
# 나지 않고 그 장소만 조용히 판정에서 빠지므로, 장소 동기화 뒤에는 매핑을 새로
# 만들어야 한다. 지금까지는 scripts 두 개를 손으로 돌렸다.
#
# job을 두지 않는다. 구 하나가 집중률 목록 8~9회 + Supabase 1~2회라 몇 초면 끝나서,
# 전 구 갱신처럼 화면이 구를 하나씩 부르는 것으로 충분하다.


def _concentration_district_code(area_code: str, district_code: str) -> str:
    """집중률 API가 쓰는 시군구 5자리. `places`는 뒤 3자리만 담는다."""
    return f"{area_code}{district_code}"


def _mapping_row_json(row: concentration_mapping.MappingRow) -> dict[str, Any]:
    return {
        "content_id": row.content_id,
        "place_title": row.place_title,
        "concentration_title": row.concentration_title,
        "match_method": row.match_method,
        "aliases": list(row.aliases),
        "search_key": row.search_key,
        "search_keys": list(row.search_keys),
    }


def _mapping_row_from_json(payload: Mapping[str, Any]) -> concentration_mapping.MappingRow:
    return concentration_mapping.MappingRow(
        content_id=str(payload["content_id"]),
        place_title=str(payload["place_title"]),
        concentration_title=str(payload["concentration_title"]),
        match_method=str(payload["match_method"]),
        aliases=tuple(str(alias) for alias in payload.get("aliases") or []),
        search_key=payload.get("search_key") or None,
        search_keys=tuple(str(key) for key in payload.get("search_keys") or []),
    )


def _latest_mapping_csv(concentration_code: str) -> str | None:
    paths = sorted(
        concentration_mapping.DATA_DIR.glob(
            f"concentration_place_mapping_{concentration_code}_*.csv"
        ),
        reverse=True,
    )
    return paths[0].name if paths else None


def _csv_date(file_name: str | None) -> str | None:
    """매핑 CSV 파일명에서 날짜를 ISO로. `..._11110_20260808.csv` → `2026-08-08`."""
    if file_name is None:
        return None
    stem = file_name.rsplit("_", 1)[-1].removesuffix(".csv")
    if len(stem) != 8 or not stem.isdigit():
        return None
    return f"{stem[:4]}-{stem[4:6]}-{stem[6:]}"


@router.get("/concentration/status")
async def get_concentration_status() -> dict[str, Any]:
    """구별로 활성 장소가 몇 건이고 그중 매핑이 몇 건인지.

    매핑이 활성 장소보다 훨씬 적은 것은 정상이다 — 집중률 API가 관광지 위주로만
    다뤄서, 나머지는 "매칭 실패"가 아니라 "대상이 아님"이다. 화면이 그렇게 읽도록
    실패 수가 아니라 두 수를 나란히 준다.
    """
    url, key = _require_supabase()

    async with status_client() as client:
        repository = SupabasePlaceRepository(
            supabase_url=url,
            secret_key=key,
            client=client,
            timeout_seconds=max(settings.external_api_timeout_seconds, 30.0),
        )
        summaries = await repository.get_place_summaries_by_district()

    districts = _as_summary_list(summaries.get("districts"))
    codes = [str(summary.get("district_code") or "") for summary in districts]
    mapping_counts = await concentration_mapping.count_mappings_by_district(
        settings, codes
    )
    rejections = concentration_mapping.load_rejections(
        concentration_mapping.DEFAULT_REJECTIONS
    )

    rows = []
    for summary in districts:
        area_code = str(summary.get("area_code") or "")
        district_code = str(summary.get("district_code") or "")
        concentration_code = _concentration_district_code(area_code, district_code)
        latest_csv = _latest_mapping_csv(concentration_code)
        csv_date = _csv_date(latest_csv)
        # CSV가 아예 없으면 그 구는 통째로 안 해본 것이라 활성 장소 전부가 대상이다.
        new_since = (
            await concentration_mapping.count_places_created_after(
                settings, district_code, csv_date
            )
            if csv_date is not None
            else int(summary.get("active", 0) or 0)
        )
        rows.append(
            {
                "area_code": area_code,
                "district_code": district_code,
                "district_name": find_district_name(area_code, district_code),
                "concentration_code": concentration_code,
                "active_places": int(summary.get("active", 0) or 0),
                "mapping_count": mapping_counts.get(district_code, 0),
                "latest_csv": latest_csv,
                # "CSV가 오래됐다"가 곧 "갱신이 필요하다"는 아니다. 그 구에 새 장소가
                # 들어오지 않았으면 다시 만들어도 결과가 같다. 실제로 봐야 할 신호는
                # 마지막 CSV 이후에 생긴 장소 수다.
                "new_places_since_csv": new_since,
            }
        )
    return {
        "districts": sorted(rows, key=lambda row: row["district_code"]),
        "rejection_count": len(rejections),
    }


class ConcentrationBuildRequest(BaseModel):
    area_code: str
    district_code: str = Field(description="`places`의 시군구 코드(예: 종로구 110)")


@router.post("/concentration/build")
async def post_concentration_build(
    request: ConcentrationBuildRequest,
) -> dict[str, Any]:
    """집중률 장소명을 받아 그 구의 활성 장소와 붙여 후보를 만든다. CSV는 쓰지 않는다.

    CSV를 여기서 쓰지 않는 이유: 사람이 애매한 후보를 걸러낸 뒤에 써야 CSV와 DB가
    같아진다. 여기서 먼저 쓰면 승인 전 상태가 파일로 남고, 그 파일을 CLI로 적재하면
    거절한 것까지 들어간다.

    장소명 목록은 매번 새로 받는다. 저장해둔 목록으로 다시 계산하면 그사이 추가된
    장소 때문에 모호해진 검색어를 놓친다(D-043).
    """
    _require_supabase()
    if not settings.tour_api_service_key:
        raise DevPanelError("TOUR_API_SERVICE_KEY가 필요합니다.")

    concentration_code = _concentration_district_code(
        request.area_code, request.district_code
    )
    names = await concentration_mapping.fetch_concentration_place_names(
        settings, request.area_code, concentration_code
    )
    places = await concentration_mapping.load_places_from_supabase(
        settings, district_code=request.district_code
    )
    overrides = concentration_mapping.load_manual_overrides(
        concentration_mapping.DEFAULT_OVERRIDES
    )
    rejections = concentration_mapping.load_rejections(
        concentration_mapping.DEFAULT_REJECTIONS
    )
    matched, unmatched, leftover = concentration_mapping.match_places(
        places, names, overrides, rejections
    )
    matched, unresolved = concentration_mapping.apply_search_keys(matched, names)
    unresolved_ids = {row.content_id for row in unresolved}

    # 확실한 것과 사람이 볼 것을 가른다. manual은 사람이 이미 적어둔 것이고 exact는
    # 이름이 그대로 같다. 나머지는 규칙이 이름을 고쳐 붙인 것이라 눈으로 봐야 한다.
    certain = [row for row in matched if row.match_method in ("manual", "exact")]
    ambiguous = [row for row in matched if row.match_method not in ("manual", "exact")]

    def _row_json(row: concentration_mapping.MappingRow) -> dict[str, Any]:
        return {
            **_mapping_row_json(row),
            # 붙긴 했지만 검색어가 다른 집중률 장소도 끌어온다. 조회는 되고 응답을
            # 정식 명칭으로 걸러야 한다 — 매칭 실패와는 다른 종류의 경고다.
            "search_key_ambiguous": row.content_id in unresolved_ids,
        }

    return {
        "area_code": request.area_code,
        "district_code": request.district_code,
        "concentration_code": concentration_code,
        "concentration_name_count": len(names),
        "place_count": len(places),
        "certain": [_row_json(row) for row in certain],
        "ambiguous": [_row_json(row) for row in ambiguous],
        "unmatched": [
            {"content_id": place.content_id, "title": place.title}
            for place in unmatched
        ],
        "leftover": leftover,
    }


class ConcentrationRejection(BaseModel):
    place_title: str
    concentration_title: str
    note: str = ""


class ConcentrationApplyRequest(BaseModel):
    """승인한 매핑을 CSV로 남기고 DB에 올린다."""

    area_code: str
    district_code: str
    rows: list[dict[str, Any]] = Field(
        description=(
            "승인한 매핑. 생성 단계가 돌려준 행을 그대로 보낸다 — 다시 계산하면 "
            "집중률 목록을 한 번 더 받아야 한다."
        )
    )
    rejections: list[ConcentrationRejection] = Field(
        default_factory=list,
        description="이번에 체크를 푼 짝. 거절 목록 파일에 덧붙여 다음 생성에서 뺀다.",
    )
    confirm: str = Field(description="확인 문자열. 집중률 시군구 코드여야 한다.")


@router.post("/concentration/apply")
async def post_concentration_apply(
    request: ConcentrationApplyRequest,
) -> dict[str, Any]:
    """승인분만 CSV로 쓰고 `place_concentration_mappings`에 올린다.

    거절한 짝은 먼저 파일에 남긴다. 적재가 실패해도 사람의 판정은 남아야 한다 —
    다시 시도할 때 같은 후보를 또 걸러내게 하지 않는다.
    """
    _require_supabase()
    concentration_code = _concentration_district_code(
        request.area_code, request.district_code
    )
    if request.confirm.strip() != concentration_code:
        raise DevPanelError(
            f"확인 문자열이 일치하지 않습니다. '{concentration_code}'를 입력하세요."
        )

    added = concentration_mapping.append_rejections(
        [
            concentration_mapping.Rejection(
                place_title=rejection.place_title,
                concentration_title=rejection.concentration_title,
                note=rejection.note,
            )
            for rejection in request.rejections
        ],
        concentration_mapping.DEFAULT_REJECTIONS,
    )

    rows = [_mapping_row_from_json(row) for row in request.rows]
    now = datetime.now(place_snapshot.KST)
    csv_path = (
        concentration_mapping.DATA_DIR
        / f"concentration_place_mapping_{concentration_code}_{now:%Y%m%d}.csv"
    )
    concentration_mapping.write_mapping_csv(rows, csv_path)
    imported = await concentration_mapping.upsert_mappings(settings, rows)

    return {
        "concentration_code": concentration_code,
        "csv": csv_path.name,
        "imported_count": imported,
        "rejected_count": len(added),
        "rejection_file": concentration_mapping.DEFAULT_REJECTIONS.name,
    }
