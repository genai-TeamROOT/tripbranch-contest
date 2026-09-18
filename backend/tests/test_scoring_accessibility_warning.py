"""무장애 판정이 `partial`인 후보에 안내가 붙는지 확인한다.

**여기서 지키려는 것은 "부분 가능이 조용히 온전한 것처럼 보이지 않는다"이다.**
저장소 조회는 `partial`을 후보에서 빼지 않는다 — 팔각정 하나 못 간다고 휠체어로
갈 수 있는 공원을 추천에서 지우는 것이 과하기 때문이다. 그래서 후보에 남아 있다는
사실만으로는 온전히 갈 수 있다는 뜻이 되지 않고, 그 차이를 말해 주는 것이 이 안내다.

말해 주지 않으면 사용자는 판정이 `possible`인 곳과 구분하지 못한 채 갔다가
되돌아온다. 오류는 어디에도 남지 않는다.

표시 측이 첫 줄만 보여주므로(`frontend/src/components/PlaceCard.tsx`) 순서도 함께
못 박는다. 운영시간 경고 뒤에 두면 미확인 후보에서 무장애 안내가 통째로 가려진다.
"""

from __future__ import annotations

from datetime import datetime, time

from app.domain.models import OperatingHours, ScoringCandidate
from app.domain.scoring import prepare_candidates

NOW = datetime(2026, 7, 23, 14, 0, 0)

_WHEELCHAIR = "일부 구역은 휠체어 접근이 어려워요."
_STROLLER = "일부 구역은 유모차로 다니기 어려워요."
_VISUAL = "점자·음성 안내가 일부 구역에만 있어요."
_UNVERIFIED = "방문 전에 운영 여부를 확인해주세요."

# 14:00 기준으로 마감까지 240분 남은 구간.
_OPEN_HOURS = OperatingHours(time(9, 0), time(18, 0))


def _candidate(
    verdicts: dict[str, str] | None,
    *,
    hours: OperatingHours | None = _OPEN_HOURS,
) -> ScoringCandidate:
    return ScoringCandidate(
        place_id="p1",
        name="공원A",
        category="park",
        environment_type="outdoor",
        distance_km=0.5,
        operating_hours=hours,
        accessibility_verdicts=verdicts,
    )


def _warnings(candidate: ScoringCandidate) -> tuple[str, ...]:
    return prepare_candidates([candidate], now=NOW).eligible_candidates[0].warnings


def test_부분_가능이면_안내가_붙는다() -> None:
    assert _warnings(_candidate({"wheelchair_access": "partial"})) == (_WHEELCHAIR,)


def test_온전히_가능하면_아무_말도_하지_않는다() -> None:
    """`possible`에 안내를 붙이면 모든 후보에 같은 줄이 달려 뜻이 사라진다."""
    assert _warnings(_candidate({"wheelchair_access": "possible"})) == ()


def test_무장애_조건이_없으면_지금까지와_같다() -> None:
    """무장애를 요구하지 않은 요청의 동작이 바뀌면 안 된다."""
    assert _warnings(_candidate(None)) == ()


def test_어휘마다_다른_문구를_쓴다() -> None:
    """휠체어가 못 가는 것과 점자 안내가 없는 것은 다른 얘기다."""
    assert _warnings(_candidate({"stroller_access": "partial"})) == (_STROLLER,)
    assert _warnings(_candidate({"visual_guide": "partial"})) == (_VISUAL,)


def test_요구한_어휘가_여럿이면_모두_알린다() -> None:
    """어느 것이 걸렸는지 하나만 말하면 나머지는 사라진다.

    순서를 어휘 이름순으로 고정한다 — 실행마다 줄 순서가 바뀌면 표시 측이 첫
    줄만 쓰는 화면에서 같은 장소가 다른 안내를 받는다.
    """
    warnings = _warnings(
        _candidate({"wheelchair_access": "partial", "visual_guide": "partial"})
    )
    assert warnings == (_VISUAL, _WHEELCHAIR)


def test_부분이_아닌_어휘는_섞이지_않는다() -> None:
    warnings = _warnings(
        _candidate({"wheelchair_access": "partial", "stroller_access": "possible"})
    )
    assert warnings == (_WHEELCHAIR,)


def test_무장애_안내가_운영시간_경고보다_앞에_온다() -> None:
    """표시 측은 첫 줄만 보여준다.

    운영시간은 "가서 닫혀 있을 수 있다"이고 무장애는 "가도 못 들어가는 데가
    있다"라 무게가 다르다. 뒤에 두면 운영시간 미확인 후보에서 무장애 안내가
    통째로 가려진다 — 그런 후보가 적지 않다.
    """
    warnings = _warnings(
        _candidate({"wheelchair_access": "partial"}, hours=None)
    )
    assert warnings == (_WHEELCHAIR, _UNVERIFIED)


def test_문구를_모르는_어휘는_빈_줄을_만들지_않는다() -> None:
    """판정표가 늘어 문구보다 어휘가 앞설 수 있다.

    그때 빈 문자열이 목록에 끼면 카드가 빈 줄을 띄우고, 그 자리가 첫 줄이면
    아무 말도 하지 않은 것과 같아진다.
    """
    warnings = _warnings(
        _candidate({"hearing_loop": "partial", "wheelchair_access": "partial"})
    )
    assert warnings == (_WHEELCHAIR,)


def test_안내는_점수를_바꾸지_않는다() -> None:
    """`partial`은 "덜 좋은 곳"이 아니라 "한 군데를 못 가는 곳"이다.

    점수를 깎으면 갈 수 있는 좋은 장소가 뒤로 밀린다. 사용자에게 필요한 것은
    순위 조정이 아니라 그 사실을 아는 것이다.
    """
    plain = prepare_candidates([_candidate(None)], now=NOW).eligible_candidates[0]
    flagged = prepare_candidates(
        [_candidate({"wheelchair_access": "partial"})], now=NOW
    ).eligible_candidates[0]

    assert plain.remaining_minutes == flagged.remaining_minutes
    assert plain.is_unverified == flagged.is_unverified
