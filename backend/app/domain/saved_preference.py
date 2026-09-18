"""계정에 저장해 둔 취향 칩을 채점 입력으로 옮긴다.

취향 설정 화면에서 고른 칩(`state.UserPreference`)은 그동안 저장만 되고 추천에
실리지 않았다(`state/preferences.py` 모듈 주석). 이 모듈이 그 값을 취향 근거
검색 질의로 바꾼다 — **무엇을 점수 입력으로 쓸지는 채점 규칙이라 D가 정한다.**
읽어서 넘기는 배선은 A가 한다(`agent_runtime.py`).

순수 함수만 둔다. 저장소도 설정도 보지 않으므로 칩 목록만 있으면 테스트된다.

## 발화가 정본이고, 저장 칩은 그 뒤에 붙는다

이번 턴에 말한 취향이 정본이다. 저장값은 발화를 덮지 않고 **뒤에 덧붙는다** —
질의 문자열에서 앞에 오는 말이 발화이므로 사용자가 방금 한 말이 남는다.

처음에는 발화가 있으면 저장값을 통째로 버렸다(2026-09-04 최초 구현). 합치는
쪽이 점수는 높았지만(취향점수 중앙 0.29 → 0.43, 종로·중구 500곳) **모순을 걸러낼
방법이 없어서** 버리는 쪽을 골랐었다. 실측 예:

    발화 "북적이는 활기찬 곳" + 저장 칩 "조용한 곳"
      발화만 → 순희네빈대떡, 종로3가 포장마차
      합침   → 안국선원, 북촌동양문화박물관, 선화랑   ← 조용한 곳만 나옴

아래 축 표가 그 거르는 방법이라, 이제는 합친다.

## 모순만 뺀다 — 중복은 그대로 둔다

처음에는 중복(발화와 같은 뜻인 칩)도 뺐다. 실측으로 걷어냈다 — 후보 500곳
5개 사례에서 **중복을 두는 쪽이 발화를 더 잘 반영했다.** 같은 뜻이라 벡터를
발화 쪽으로 당기기 때문이다(순위상관, 중복 둠 → 뺌):

| 사례 | 발화 반영 | 나머지 칩 반영 |
| --- | --- | --- |
| 야경 | 0.932 → 0.913 | 0.833 → 0.848 |
| 조용 | 0.936 → 0.918 | 0.724 → 0.761 |
| 아늑 | 0.928 → 0.883 | 0.750 → 0.810 |
| 힐링 | 0.921 → 0.906 | 0.911 → 0.921 |
| 사진 | 0.928 → 0.919 | 0.833 → 0.841 |

5/5 일관되게, 빼면 발화가 덜 반영되고 나머지 칩이 조금 더 반영된다. 발화 우선이
목표이므로 두는 쪽을 고른다. 두 질의의 전체 순위상관은 0.984~0.998로 어차피
거의 같아서, 이걸 잡자고 임계값과 인코딩 경로를 새로 들일 값어치가 없었다.

## 동행과 분위기는 규칙이 다르다

**동행은 모순을 따지지 않고, 발화에 동행이 있으면 동행 칩을 통째로 뺀다.**
동행 값 여섯(solo·couple·friend·parent·child·pet)은 서로 배타적이지 않아
"다르면 모순"이 성립하지 않는다 — "부모님이랑"에 "아이와 함께"는 3대가 함께
가는 경우라 모순이 아닌데, 값만 비교하면 모순으로 잡힌다. 반대로 "단체 모임"은
`Companion` 어휘에 대응 값이 아예 없어 값 비교로는 판정 자체가 안 된다.

축 소속만 보면 둘 다 해결된다. 이번 턴에 누구와 가는지 말했으면 그게 정본이고,
저장해 둔 동행 취향은 "말하지 않았을 때의 기본값"이다.

잃는 것도 있다. `companion`은 채점에도 하드 필터에도 쓰이지 않으므로
(`agent_runtime`의 이동수단 판정에만 쓴다), 발화가 `taste_query`에 동행 표현을
남기지 않은 요청은 동행 신호가 **어디에도 안 남는다** — "아이랑 갈 데 추천"은
`companion=child`만 채우고 취향 서술이 없어 `taste_query`가 null이다. 그래도
받아들이는 이유는 동행 칩이 원래 약하기 때문이다(단독 통과율 2.4~10.0%, 좋은
조합에 붙이면 0.46 → 0.25~0.30).

**분위기는 반대로 값을 본다.** 혼잡도는 진짜 이항 대립(조용 ↔ 붐빔)이라
"다르면 모순"이 정확하고, 축 전체를 빼면 부딪히지 않는 칩까지 잃는다. 실측으로
남은 것은 `quiet × SEEK` 한 줄뿐이다 — 이유는 `_CONTRADICTIONS` 주석에 있다.

임베딩 유사도로 모순을 거르는 안은 **반대말을 못 잡아서** 기각했다. "조용한
곳"과 "혼자 조용히 쉴 만한"은 0.694로 붙지만 "조용한 곳"과 "북적이는 활기찬"은
0.186으로 오히려 **멀다** — 모순일수록 유사도가 낮아 임계값으로는 안 걸린다.

## 발화가 그 축을 말했는지는 LLM이 이미 답해 뒀다

`concentration_intent`와 `companion`은 `recommend.extract`가 매 턴 뽑는 값이다.
여기서는 **읽기만 한다** — 프롬프트를 건드리지 않으므로 같은 요청에 항상 같은
결과가 나오고, 프롬프트 버전을 올릴 일도 없다.

`environment`도 뽑히지만 **받지 않는다.** 실내외로 칩을 빼봐야 결과가 같았다
(`_CONTRADICTIONS` 주석의 실측). 쓰지 않는 값을 인자로 들고 있으면 "실내외를
보고 있다"고 읽히므로 시그니처에서 뺐다.

## 고른 칩은 종류를 가리지 않고 전부 넣는다

화면이 최소 3개·최대 5개를 고르게 하므로(`PreferencesPage.tsx`), **고른 것을
버리면 사용자가 고른 수만큼 반영되지 않는다.** 처음에는 분류(`place_tag`)와 동행
칩을 걸렀는데, 실제 저장값으로 재보니 그 필터가 손해였다 — 종로·중구 500곳:

| 저장값 | 전부 | 동행만 제외 | 동행+분류 제외 |
| --- | --- | --- | --- |
| 분류 3개가 섞인 5개 | 0.33 | 0.39 | **0.22** (칩 1개만 남음) |
| 분류 위주 5개 | 0.52 | 0.52 | **0.26** (칩 1개만 남음) |
| 분위기 위주 5개 | 0.51 | 0.51 | 0.51 |

분류 칩을 빼면 5개를 고른 사람의 질의가 1개짜리가 되고, 칩이 적을수록 점수가
낮다(1개 0.31 → 5개 0.63). **분류 칩을 질의에 넣는 것과 하드 필터로 쓰는 것은
다르다** — 여기서는 순위만 다듬고 후보를 지우지 않으므로 "그 분류만 나온다"는
위험이 생기지 않는다.

동행 칩("친구와 함께" 등)은 단독 통과율이 2.4~10.0%로 낮고 좋은 조합에 붙이면
점수를 깎는다(0.46 → 0.25~0.30). 그래도 넣는 이유는 **화면이 동행 칩을 최대 1개로
제한할 예정이라 최악이 1개뿐이고, 동행만 고른 사용자가 취향을 아예 못 쓰는 것보다는
낫기 때문**이다. 그 제한이 들어오기 전에 여러 개를 저장한 사용자는 그만큼 손해를
보는데, 다시 고르면 정리되는 값이라 백엔드에서 자르지 않는다(2026-09-04 팀 결정).

## 5개를 고르면 5개가 모두 맞는 곳만 나오는 게 아니다

라벨을 공백으로 이어 **벡터 하나로** 검색한다. AND도 OR도 아니고 "합친 말에 가까운
곳"이다 — 후보 500곳 실측에서 5개 전부 맞는 곳은 35곳인데 이 방식은 318곳이
걸렸다(하나라도 맞는 곳은 337곳).

칩마다 따로 검색해 최댓값을 쓰는 방식도 재봤으나 택하지 않았다. 그쪽은 칩 하나만
세게 맞는 곳이 이겨서 취향점수 중앙이 0.43으로 낮았다(합친 질의 0.51). 여러 칩에
골고루 맞는 곳이 위로 오는 편이 5개를 고른 뜻에 가깝고, 검색도 한 번이면 된다.

**아무 칩에도 안 걸린 후보가 추천에서 빠지지는 않는다.** 취향은 순위를 다듬는 축이라
근거가 없으면 0.0점일 뿐이고(`scoring.py::_taste_score`), 날씨·영업시간이 좋으면
그대로 올라온다.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence
from typing import Protocol

# 화면 칩의 출처(`frontend/src/pages/preferenceOptions.ts`).
# - preference: 리뷰·블로그에서 뽑은 장소별 취향 태그(`place_preference_tags`)
# - place_tag: TourAPI 소분류. **질의에는 넣되 하드 필터로는 절대 쓰지 않는다**
# - custom: 사용자가 직접 적은 키워드. 대응 코드가 없어 codes가 빈 배열이다
# 화면이 아는 출처만 질의에 넣는다. 새 종류가 생기면 **그 값을 여기 적기 전까지
# 채점이 조용히 먹지 않는다** — 화면이 칩을 늘렸는데 점수가 따라 바뀌면 무엇 때문에
# 순위가 달라졌는지 로그로 못 찾는다.
_QUERY_SOURCES = frozenset({"preference", "place_tag", "custom"})

# 동행 칩(`frontend/src/pages/preferenceOptions.ts`의 COMPANION_OPTIONS).
# **값을 적지 않는다** — 발화가 동행을 말했으면 어느 값이든 통째로 뺀다.
_COMPANION_CODES = frozenset(
    {"date", "with_friends", "with_kids", "group_gathering", "with_parents", "alone"}
)

# 분위기 칩 → 그 칩과 부딪히는 **발화 값**들. 지금은 한 줄이다.
#
# `place_tag` 칩은 코드를 여러 개 든다("자연·공원" = 공원·산·호수·계곡·수목원).
# 라벨을 키로 쓰지 않는 것은 문구가 바뀌면 조용히 안 걸리기 때문이다.
#
# **처음에는 8줄이었다. 실측으로 7줄을 걷어냈다**(2026-09-04, 종로·중구 구 단위
# 요청, 후보 30곳):
#
# - **실내외 5칩분**(indoor·walk·공원·산·호수·계곡·수목원)을 뺐다. 비 오는 날
#   `environment=indoor` 요청에서 "자연·공원" 칩을 두든 빼든 상위 5곳의 야외
#   비율이 **똑같이 4/5(종로)·2/5(중구)**였다. 반경 경로는 산술로도 불가능하다 —
#   취향 최대폭 0.15가 환경 축 차이 0.245(1.00 대 0.30)보다 작아 취향이 환경을
#   이길 수 없다.
#
#   **다만 "환경 축이 야외를 걸러준다"는 뜻은 아니다.** 취향 축을 아예 꺼도 야외가
#   4/5였다 — 공원·산은 `operating_schedule`이 all_day라 잔여시간 만점을 받고,
#   실내 시설은 운영시간 정보가 없어 낮은 점수를 받는 탓이다. 취향과 무관한 별개
#   문제라 여기서 고치지 않는다(운영시간 적재 / 환경 가중치 영역).
#
# - **trendy_hotspot을 뺐다.** "힙하다"가 반드시 붐비는 것은 아니다 — 조용한
#   힙플레이스가 모순이 아니다(2026-09-04 팀 결정). 코드 이름이 hotspot이라
#   붐비는 쪽으로 봤던 것이고, 실측 근거가 없던 줄이다.
#
# 남긴 한 줄은 **실측에서 결과가 통째로 뒤집혔던 유일한 조합**이다 — "북적이는
# 활기찬" 발화에 "조용한 곳" 칩을 합치면 순희네빈대떡·종로3가 포장마차가
# 안국선원·북촌동양문화박물관·선화랑으로 바뀌었다(종로·중구 500곳).
#
# 값 어휘는 `app.schemas.ConcentrationIntent`와 똑같이 맞춘다 — 이름이 같아야
# 비교가 문자열 하나로 끝난다. **혼잡도 아닌 값을 넣으면 조용히 안 걸린다**
# (`environment`를 더 이상 넘기지 않으므로) — 테스트가 그걸 막는다.
_CONTRADICTIONS: dict[str, frozenset[str]] = {
    "quiet": frozenset({"SEEK"}),  # "조용한 곳"
}

# 발화가 혼잡도를 **정하지 않은** 값. null과 같이 취급한다.
# `IGNORE`는 "사람 많아도 괜찮아"라 SEEK가 아니다
# (`prompts/_shared/rules/concentration_intent.md`) — 조용한 곳을 저장한 사람에게
# "사람 많아도 괜찮아"는 조용한 곳을 원하지 않는다는 말이 아니다.
_UNDECIDED = frozenset({"IGNORE"})


class SavedPreferenceChip(Protocol):
    """`state.schema.UserPreference`가 만족하는 최소 계약.

    B의 모델을 직접 import 하지 않는다 — D 도메인이 상태 계층에 의존하면 채점
    규칙을 테스트하는 데 저장소 스키마가 딸려 온다.
    """

    label: str
    source: str
    codes: list[str]


def usable_chips(chips: Iterable[SavedPreferenceChip]) -> list[SavedPreferenceChip]:
    """질의에 쓸 칩만 고른다. 고른 순서를 유지한다.

    순서를 지키는 이유는 사용자가 고른 순서가 곧 우선순위로 읽히기 때문이다
    (`state/schema.py::UserPreferenceList` 주석). 질의 문자열에서 앞에 오는 말이
    임베딩에 더 세게 걸리는지는 재보지 않았지만, 순서를 흔들 근거도 없다.
    """
    return [chip for chip in chips if chip.source in _QUERY_SOURCES and chip.label.strip()]


def is_companion_chip(chip: SavedPreferenceChip) -> bool:
    """동행 칩인가. 값은 보지 않는다 — 발화가 동행을 말했으면 통째로 뺀다."""
    return any(code in _COMPANION_CODES for code in chip.codes)


def contradicts(chip: SavedPreferenceChip, spoken_values: Collection[str]) -> bool:
    """분위기 칩이 발화가 확정한 값과 부딪히는가.

    칩의 코드 중 **하나라도** 부딪히면 부딪히는 것으로 본다. place_tag 칩이
    코드를 여럿 들기 때문이다("자연·공원" = 공원·산·호수·계곡·수목원).
    """
    return any(
        not _CONTRADICTIONS.get(code, frozenset()).isdisjoint(spoken_values) for code in chip.codes
    )


def decided_values(*, concentration_intent: str | None = None) -> set[str]:
    """발화가 값을 확정한 분위기 축의 값들. "상관없다"는 확정으로 치지 않는다.

    집합으로 돌려주는 이유는 `_CONTRADICTIONS`가 축을 구분하지 않기 때문이다 —
    축이 늘면 여기에 값을 더 담으면 되고 판정부는 그대로다.
    """
    return {str(value) for value in (concentration_intent,) if value and value not in _UNDECIDED}


def to_taste_query(
    chips: Sequence[SavedPreferenceChip],
    *,
    concentration_intent: str | None = None,
    companion: str | None = None,
) -> str | None:
    """저장된 칩을 취향 근거 검색 질의로 바꾼다. 쓸 것이 없으면 `None`.

    발화와 부딪히는 칩만 뺀 나머지를 남긴다. **발화 문구는 여기에 넣지 않는다** —
    호출부가 발화 질의 뒤에 이 값을 이어 붙인다
    (`real_recommendation_provider::_taste_matches_for`). 발화 질의는 장소 유형을
    덧붙이는 보강(`_enrich_taste_query`)을 따로 태우기 때문에, 두 문자열을 여기서
    합치면 그 보강이 저장값에까지 걸린다.

    **발화 취향(`taste_query`) 자체는 보지 않는다.** 중복을 빼지 않기로 했으므로
    "이번 턴에 취향을 말했는지"가 판정에 안 쓰인다(모듈 docstring).

    `None`과 빈 문자열을 구분한다. `None`은 "저장값으로 보탤 것이 없다"이고,
    빈 문자열을 돌려주면 호출부가 취향 축을 켜서 전 후보가 0점인 축이 다른 축의
    몫만 깎는다(`scoring.py::_taste_score` 주석).

    라벨을 그대로 쓰고 `codes`는 질의에 넣지 않는다. 코드는 영어라 한국어 임베딩과
    맞지 않는다 — 종로·중구 500곳 실측에서 `healing` 2.6% 대 "힐링하기 좋은" 52.4%,
    `cozy` 13.8% 대 "아늑한 공간" 53.0%였다. 코드는 칩을 가려내는 데만 쓴다.
    """
    spoken_values = decided_values(concentration_intent=concentration_intent)
    said_companion = bool(companion)

    labels = [
        chip.label.strip()
        for chip in usable_chips(chips)
        if not (said_companion and is_companion_chip(chip)) and not contradicts(chip, spoken_values)
    ]
    if not labels:
        return None
    return " ".join(labels)


__all__ = [
    "SavedPreferenceChip",
    "contradicts",
    "decided_values",
    "is_companion_chip",
    "to_taste_query",
    "usable_chips",
]
