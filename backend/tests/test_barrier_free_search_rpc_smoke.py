"""무장애 후보 검색 RPC를 실제 Supabase로 확인하는 Smoke Test.

**여기서만 확인할 수 있는 것이 있다.** 원문을 읽어 "이 편의가 있다"를 가르는 판정은
`search_places_barrier_free` 함수 안에 있어, Fake로는 그 규칙이 도는지 알 수 없다.
특히 다음 한 줄이 이 파일의 존재 이유다.

    `없`·`미설치` 같은 단어로 부정을 판정하지 않는다.

접근로와 주출입구는 문장의 주어가 턱·단차라서 "없다"가 긍정이다. 단어로 거르면
894건을 잘못 버리고 4건을 맞게 버리는데(2026-09-01 실측), 그렇게 되어도 오류는
나지 않고 후보 수만 3분의 1로 줄어든다. 나중에 누가 "없음도 걸러야지" 하고 단어를
추가하면 여기서 걸린다.

실행:

    RUN_REAL_PROVIDER_TESTS=true python -m pytest -m smoke \\
        tests/test_barrier_free_search_rpc_smoke.py -v -s
"""

from __future__ import annotations

import os

import httpx
import pytest

from app.config import Settings
from app.domain.models import AccessibilityNeed, AccessibilityVerdict
from app.repositories.supabase_places import SupabasePlaceRepository

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.smoke,
    pytest.mark.skipif(
        os.getenv("RUN_REAL_PROVIDER_TESTS") != "true",
        reason="RUN_REAL_PROVIDER_TESTS=true일 때만 실제 Supabase를 호출합니다.",
    ),
]

settings = Settings()

# 안국역. 무장애 값이 채워진 장소가 반경 2km에 206곳으로 가장 많은 지점 중 하나다
# (2026-09-01 실측). 표본이 커야 판정이 뒤집혔을 때 눈에 띈다.
_ANGUK_LATITUDE = 37.5765
_ANGUK_LONGITUDE = 126.9857

# `출입구까지 턱이 없어 휠체어 접근 가능함`처럼 `없`이 긍정으로 쓰인 원문을 가진
# 장소다. 단어로 부정을 판정하면 이 장소들이 사라진다.
_WHEELCHAIR_EXPECTED_TITLES = {"대학로", "마로니에공원", "종묘광장공원"}

# `출입통로가 좁아 휠체어, 전동 스쿠터 사용자 진입 어려움`. 들어갈 수단이 아예 없어
# 판정이 impossible이고 후보에서 빠져야 한다. 유모차는 폭이 작아 지나가므로 남는다.
_WHEELCHAIR_BLOCKED_TITLE = "너비집"

# `출입구까지 평지로 연결되어 있으나 턱이 있어 접근 불가능한 구간 있음`.
#
# **이 장소는 빠지지 않는다.** 들어갈 수는 있고 못 가는 구간이 남을 뿐이라
# 판정이 partial이다. 옛 규칙은 원문에 `불가`가 들어 있다는 이유만으로 이 장소를
# 통째로 뺐는데, 그러면 휠체어로 갈 수 있는 공원이 추천에서 사라진다.
_PARTIALLY_BLOCKED_TITLE = "사직공원(서울)"


def _repository(client: httpx.AsyncClient) -> SupabasePlaceRepository:
    return SupabasePlaceRepository(
        supabase_url=settings.supabase_url,
        secret_key=settings.supabase_secret_key,
        client=client,
        timeout_seconds=settings.external_api_timeout_seconds,
    )


async def test_단차_없음_조건이_충분한_후보를_돌려준다() -> None:
    async with httpx.AsyncClient() as client:
        rows = await _repository(client).search_places_barrier_free(
            latitude=_ANGUK_LATITUDE,
            longitude=_ANGUK_LONGITUDE,
            radius_km=2.0,
            needs=(AccessibilityNeed.WHEELCHAIR_ACCESS,),
            limit=300,
        )

    print(f"\n안국역 2km · wheelchair_access: {len(rows)}곳")
    # 2026-09-01 실측 206곳. 적재가 늘 수 있어 하한만 본다 — 판정이 단어 매칭으로
    # 뒤집히면 100곳 아래로 떨어진다.
    assert len(rows) >= 150


async def test_없다는_말이_긍정인_원문을_버리지_않는다() -> None:
    """이 파일의 핵심. `턱이 없어 접근 가능함`이 후보로 남아야 한다."""
    async with httpx.AsyncClient() as client:
        rows = await _repository(client).search_places_barrier_free(
            latitude=_ANGUK_LATITUDE,
            longitude=_ANGUK_LONGITUDE,
            radius_km=3.0,
            needs=(AccessibilityNeed.WHEELCHAIR_ACCESS,),
            limit=500,
        )

    titles = {row.title for row in rows}
    missing = _WHEELCHAIR_EXPECTED_TITLES - titles
    assert not missing, (
        f"`없`이 긍정으로 쓰인 원문을 가진 장소가 빠졌습니다: {sorted(missing)}. "
        "판정에 단어 매칭이 들어갔는지 확인하세요."
    )


async def test_들어갈_수_없는_곳은_빠지고_유모차에는_남는다() -> None:
    """불가만 뺀다. 그리고 같은 장소가 어휘에 따라 갈린다."""
    async with httpx.AsyncClient() as client:
        repository = _repository(client)
        wheelchair = await repository.search_places_barrier_free(
            latitude=_ANGUK_LATITUDE,
            longitude=_ANGUK_LONGITUDE,
            radius_km=3.0,
            needs=(AccessibilityNeed.WHEELCHAIR_ACCESS,),
            limit=500,
        )
        stroller = await repository.search_places_barrier_free(
            latitude=_ANGUK_LATITUDE,
            longitude=_ANGUK_LONGITUDE,
            radius_km=3.0,
            needs=(AccessibilityNeed.STROLLER_ACCESS,),
            limit=500,
        )

    assert _WHEELCHAIR_BLOCKED_TITLE not in {row.title for row in wheelchair}
    # 통로가 좁을 뿐이라 유모차는 지나간다. 여기서도 빠지면 유모차 판정이
    # 휠체어 값을 그대로 쓰고 있다는 뜻이다.
    assert _WHEELCHAIR_BLOCKED_TITLE in {row.title for row in stroller}


async def test_못_가는_구간이_남는_곳은_빼지_않고_부분으로_남긴다() -> None:
    """옛 규칙은 원문에 `불가`가 있다는 이유로 이 장소를 통째로 뺐다.

    들어갈 수는 있으므로 후보로 남기고, 판정을 함께 올려 답변이 "일부 구간은
    접근이 어렵다"고 말하게 한다. 빠지면 휠체어로 갈 수 있는 공원이 사라진다.
    """
    async with httpx.AsyncClient() as client:
        rows = await _repository(client).search_places_barrier_free(
            latitude=_ANGUK_LATITUDE,
            longitude=_ANGUK_LONGITUDE,
            radius_km=3.0,
            needs=(AccessibilityNeed.WHEELCHAIR_ACCESS,),
            limit=500,
        )

    found = [row for row in rows if row.title == _PARTIALLY_BLOCKED_TITLE]
    assert found, f"{_PARTIALLY_BLOCKED_TITLE}이(가) 후보에서 빠졌습니다."
    assert found[0].wheelchair_access_verdict is AccessibilityVerdict.PARTIAL


async def test_조건을_더하면_후보가_줄어든다() -> None:
    """여럿이면 전부 만족해야 한다(AND). OR이면 오히려 늘어난다."""
    async with httpx.AsyncClient() as client:
        repository = _repository(client)
        step_free_only = await repository.search_places_barrier_free(
            latitude=_ANGUK_LATITUDE,
            longitude=_ANGUK_LONGITUDE,
            radius_km=2.0,
            needs=(AccessibilityNeed.WHEELCHAIR_ACCESS,),
            limit=500,
        )
        with_restroom = await repository.search_places_barrier_free(
            latitude=_ANGUK_LATITUDE,
            longitude=_ANGUK_LONGITUDE,
            radius_km=2.0,
            needs=(
                AccessibilityNeed.WHEELCHAIR_ACCESS,
                AccessibilityNeed.ACCESSIBLE_RESTROOM,
            ),
            limit=500,
        )

    print(
        f"\nwheelchair_access 단독 {len(step_free_only)}곳 → "
        f"+accessible_restroom {len(with_restroom)}곳"
    )
    assert 0 < len(with_restroom) < len(step_free_only)
    assert {row.content_id for row in with_restroom} <= {
        row.content_id for row in step_free_only
    }


async def test_거리순으로_돌아온다() -> None:
    async with httpx.AsyncClient() as client:
        rows = await _repository(client).search_places_barrier_free(
            latitude=_ANGUK_LATITUDE,
            longitude=_ANGUK_LONGITUDE,
            radius_km=2.0,
            needs=(AccessibilityNeed.WHEELCHAIR_ACCESS,),
            limit=50,
        )

    distances = [row.distance_km for row in rows]
    assert distances == sorted(distances)
    assert all(distance <= 2.0 for distance in distances)


async def test_반경_밖은_돌아오지_않는다() -> None:
    """사각형 선걷어내기만 하고 하버사인을 빠뜨리면 모서리가 새어 나온다."""
    async with httpx.AsyncClient() as client:
        rows = await _repository(client).search_places_barrier_free(
            latitude=_ANGUK_LATITUDE,
            longitude=_ANGUK_LONGITUDE,
            radius_km=0.5,
            needs=(AccessibilityNeed.WHEELCHAIR_ACCESS,),
            limit=500,
        )

    assert all(row.distance_km <= 0.5 for row in rows)


async def test_빈_조건은_저장소에_닿기_전에_막힌다() -> None:
    async with httpx.AsyncClient() as client:
        with pytest.raises(ValueError):
            await _repository(client).search_places_barrier_free(
                latitude=_ANGUK_LATITUDE,
                longitude=_ANGUK_LONGITUDE,
                radius_km=2.0,
                needs=(),
                limit=10,
            )


async def test_노인_동반_어휘가_다른_장소를_고른다() -> None:
    """의자식 테이블·저상버스는 단차 정보와 다른 컬럼을 읽는다.

    값 유무로 판정하면 안 되는 두 어휘라(의자식은 231행 중 142행, 저상버스는
    126행 중 99행) 문구 판정이 실제로 걸리는지 여기서 확인한다.
    """
    async with httpx.AsyncClient() as client:
        repository = _repository(client)
        seating = await repository.search_places_barrier_free(
            latitude=_ANGUK_LATITUDE, longitude=_ANGUK_LONGITUDE, radius_km=2.0,
            needs=(AccessibilityNeed.SEATING_AVAILABLE,), limit=500,
        )
        transit = await repository.search_places_barrier_free(
            latitude=_ANGUK_LATITUDE, longitude=_ANGUK_LONGITUDE, radius_km=2.0,
            needs=(AccessibilityNeed.LOW_FLOOR_TRANSIT,), limit=500,
        )

    print(f"\n의자식 테이블 {len(seating)}곳 · 저상버스 {len(transit)}곳")
    # 값 유무로 판정하면 각각 89곳·27곳이 더 들어온다. 그만큼 늘어나면 문구 판정이
    # 빠진 것이다.
    assert 0 < len(seating) < 60
    assert 0 < len(transit) < 50


async def test_휠체어와_유모차가_갈린다() -> None:
    """두 어휘로 나눈 값어치가 실제로 나오는지 본다.

    통로가 좁아 휠체어만 막히는 곳이 실재해서, 유모차 후보가 더 많아야 한다.
    같아지면 판정 적재가 빠졌거나 RPC가 다시 원문을 읽고 있다는 뜻이다.
    """
    async with httpx.AsyncClient() as client:
        repository = _repository(client)
        wheelchair = await repository.search_places_barrier_free(
            latitude=_ANGUK_LATITUDE, longitude=_ANGUK_LONGITUDE, radius_km=2.0,
            needs=(AccessibilityNeed.WHEELCHAIR_ACCESS,), limit=500,
        )
        stroller = await repository.search_places_barrier_free(
            latitude=_ANGUK_LATITUDE, longitude=_ANGUK_LONGITUDE, radius_km=2.0,
            needs=(AccessibilityNeed.STROLLER_ACCESS,), limit=500,
        )

    print(f"\n휠체어 {len(wheelchair)}곳 · 유모차 {len(stroller)}곳")
    # 2026-09-02 실측 188곳 대 190곳. 휠체어를 막는 문장이 유모차는 통과시킨다.
    assert len(stroller) > len(wheelchair)
    assert {r.content_id for r in wheelchair} < {r.content_id for r in stroller}


async def test_판정이_후보와_함께_올라온다() -> None:
    """후보만 오면 부분과 가능이 같은 것이 된다.

    둘 다 후보로 남기기 때문에, 판정 값이 없으면 "일부 구역은 접근이 어렵다"를
    말할 근거가 사라진다. 후보에 남았다는 것은 불가가 아니라는 뜻일 뿐이다.
    """
    async with httpx.AsyncClient() as client:
        rows = await _repository(client).search_places_barrier_free(
            latitude=_ANGUK_LATITUDE, longitude=_ANGUK_LONGITUDE, radius_km=2.0,
            needs=(AccessibilityNeed.WHEELCHAIR_ACCESS,), limit=500,
        )

    verdicts = [row.wheelchair_access_verdict for row in rows]
    assert all(verdict is not None for verdict in verdicts), (
        "후보인데 판정이 비어 있습니다. 판정 적재가 빠졌는지 확인하세요."
    )
    # 불가는 후보에서 이미 빠져 있어야 한다.
    assert AccessibilityVerdict.IMPOSSIBLE not in verdicts
    partial = [v for v in verdicts if v is AccessibilityVerdict.PARTIAL]
    print(f"\n후보 {len(rows)}곳 중 부분 {len(partial)}곳")
    # 2026-09-02 실측 7곳. 0이면 부분이 후보에서 빠지고 있다는 뜻이다.
    assert partial

