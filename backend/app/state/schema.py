"""Package B - Agent State 데이터 모델.

계약 문서: docs/package-b/agent-state-contract-v1.md (1절, 3절)
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

KST = timezone(timedelta(hours=9))


def now_kst() -> datetime:
    """타임존이 포함된 현재 시각."""
    return datetime.now(KST)


# ---------------------------------------------------------------- 조건

class UserConditions(BaseModel):
    """사용자 발화에서 추출된 조건. (계약 1.2절)

    B는 각 필드의 허용값을 검증하지 않는다. 허용값 정의는 Package A의 책임이다.
    """

    current_location: str | None = None
    search_center: str | None = None
    place_types: list[str] = Field(default_factory=list)
    place_tags: list[str] = Field(default_factory=list)
    weather: str | None = None
    weather_intent: str | None = None
    concentration_intent: str | None = None
    transport: str | None = None
    max_travel_time: int | None = None   # 분
    time_available: int | None = None    # 분
    environment: str | None = None
    companion: str | None = None
    budget: str | None = None
    exclude_tags: list[str] = Field(default_factory=list)
    special_requirements: list[str] = Field(default_factory=list)
    taste_query: str | None = None
    accessibility_needs: list[str] = Field(default_factory=list)
    # "이번 요청의 이동시간을 어디서부터 잴까"의 판정. 값은 "user_location" |
    # "search_center"(app.schemas.TravelOrigin) 중 하나며 B는 검증하지 않는다.
    travel_origin: str | None = None


class ApiContext(BaseModel):
    """외부 API로 확보한 데이터. (계약 1.4절)

    operations 대상이 아니며 별도 경로로 갱신한다.
    condition_version 증가 판정에서 제외한다.
    """

    gps_location: str | None = None
    api_weather: str | None = None
    gps_location_updated_at: datetime | None = None
    api_weather_updated_at: datetime | None = None
    # PR #188: 위치 재확인 UX(30분 경과 시 "N분 전 위치로 계속"/"현재 위치 다시
    # 가져오기") 서버 상태 반영. gps_location_updated_at은 GPS 데이터의 기술적
    # TTL(1시간) 의미를 유지하고, 이 필드는 사용자가 실제로 재확인(현재 위치
    # 다시 가져오기)한 시각만 담아 혼용하지 않는다 — "N분 전 위치로 계속"을
    # 선택하면 이 값은 갱신되지 않는다. A가 이 값과 now를 비교해 30분 경과
    # 여부를 서버 상태 기준으로 판단한다. 기존 세션은 null(최초 재확인 대상).
    gps_location_confirmed_at: datetime | None = None


# ---------------------------------------------------------------- INFO 되묻기

class PendingInfoContext(BaseModel):
    """INFO의 place_ambiguous 되묻기가 저장해두는 원래 질문.

    RECOMMEND/SCHEDULE의 되묻기는 session_context.user_conditions만으로
    결정적으로 재구성되지만(_resolve_clarification_choice), INFO는 question_type/
    specific_question/place_context/visit_time을 세션 어디에도 저장하지 않아
    되묻기 버튼을 누르면 장소명만 가지고 처음부터 재분류됐다("주차장 질문이었다"는
    사실이 사라짐). 이 값을 pending_clarification="place_ambiguous"와 함께
    저장해두면 버튼 클릭 시 InfoPayload를 그대로 재구성할 수 있다.

    B는 이 값을 해석하지 않고 그대로 보관만 한다 — question_type/place_context
    허용값 정의는 Package A(app.schemas.QuestionType/PlaceContext)의 책임이다.
    """

    question_type: str
    place_context: str
    specific_question: str | None = None
    visit_time: str | None = None


# ---------------------------------------------------------------- 대화층

# 최근 대화를 몇 턴까지 들고 갈지. 늘리면 프롬프트가 길어지고 노출 범위도 넓어져
# 5턴으로 잡았다 — 되묻기 한 번을 사이에 두고 앞뒤 맥락이 이어질 최소 길이다.
MAX_RECENT_TURNS = 5
# 사용자 원문 상한. 넘으면 잘라서 저장한다 — 프롬프트 길이와 저장 범위를 동시에
# 묶는 상한이지, 의미를 요약하는 장치가 아니다(아래 ConversationTurn 참고).
MAX_TURN_USER_INPUT_CHARS = 300
# 어시스턴트 답변 상한. 사용자 원문과 같은 값으로 둔다 — 둘 다 프롬프트에 실리므로
# 한쪽만 넉넉히 잡을 이유가 없다.
MAX_TURN_ASSISTANT_MESSAGE_CHARS = 300


class ConversationTurn(BaseModel):
    """주고받은 대화 한 턴. append-only 성격이지만 MAX_RECENT_TURNS개만 남는다.

    B는 원칙적으로 대화 원문을 저장하지 않지만(ConditionChangeLog 참고),
    FeedbackRecord가 그랬듯 여기서 예외를 둔다 — 상황을 알아채고 먼저 제안하려면
    (docs/design/conversational-layer.md) 직전에 무슨 말이 오갔는지를 모델이
    봐야 하는데, 누적 조건·추천 이력만으로는 "그냥 삐끗했어" 같은 이어지는 발화를
    해석할 수 없다. 노출 범위를 좁히려고 두 가지를 지킨다.

    1. 어시스턴트 쪽은 **화면에 나간 답변 문장과 처리 재료를 함께** 담는다.
       처음에는 재료(intent/question_type/장소명/제안)만 담았는데, 그러면 모델이
       "내가 방금 뭐라고 말했는지"를 알 수 없어 답변 문장이 앞 턴과 어긋났다
       (2026-08-31 실사용: 동행을 friend로 잡아놓고도 "혼자서도 가기 좋고"로 답변).
       강의교재 36강도 model 답변을 이력에 넣는 것을 멀티턴의 핵심으로 든다.
       재료는 그대로 유지한다 — 분류 단계에는 "질문 유형: concentration"이 산문보다
       정확한 신호다. LLM을 한 번 더 불러 요약하지는 않는다(호출이 늘어난다);
       `AgentResponse.message`가 이미 만들어진 값이므로 그대로 옮긴다.
    2. 사용자 원문과 어시스턴트 답변 모두 상한(MAX_TURN_*_CHARS)으로 자르고, 세션이
       만료되면 상태와 함께 지워진다(session.py의 TTL 30분). 어시스턴트 답변 원문
       저장은 FeedbackRecord.assistant_message가 이미 같은 값을 남기는 선례를 따른
       것이라 새 정책 결정이 아니다.

    보안: 이 값은 신뢰할 수 없는 입력이다. 프롬프트에 넣을 때 system_instruction
    문자열에 치환하지 말고 대화 내용(contents) 자리로 보내야 한다 — 서버에
    저장했다는 사실이 입력을 안전하게 만들지 않는다.
    """

    user_input: str
    # 그 턴에 화면으로 나간 답변 문장(MAX_TURN_ASSISTANT_MESSAGE_CHARS로 잘림).
    # 과거 세션에는 없으므로 None일 수 있다 — 그때는 재료만으로 조립한다.
    assistant_message: str | None = None
    # 그 턴을 무엇으로 처리했는지. B는 값을 해석하지 않고 A가 준 것을 보관만 한다.
    intent: str | None = None
    question_type: str | None = None
    # 그 턴에 화면으로 나간 장소 이름. "거기" 같은 지시어를 이어받을 때 쓴다.
    place_names: list[str] = Field(default_factory=list)
    # 그 턴에 트리비가 먼저 제안한 action id(있었다면). 거절 판정의 근거가 된다.
    offered_action: str | None = None
    at: datetime = Field(default_factory=now_kst)


class SituationState(BaseModel):
    """지금 사용자가 처한 상황과, 그에 대해 이미 해본 시도.

    대화 원문만으로는 판단이 흔들려서 구조화된 상태를 따로 둔다. 특히
    rejected_actions는 "한 번 거절당한 제안은 같은 세션에서 다시 하지 않는다"는
    절제 규칙(conversational-layer.md 5장)이 참조할 유일한 근거다 — 이 필드가
    없으면 그 규칙은 지킬 방법이 없다.

    값의 허용 목록은 Package A가 정한다(app.schemas). B는 보관만 한다.
    """

    current_situation: str | None = None
    recent_constraints: list[str] = Field(default_factory=list)
    rejected_actions: list[str] = Field(default_factory=list)
    # 직전 턴이 낸 제안이 아직 응답을 기다리고 있으면 그 SituationKind 값(문자열).
    # pending_clarification과 같은 단발성 마커이지만 의미가 겹치지 않는 별도
    # 상태 기계라 필드를 분리했다 — 하나로 합치면 위치 선택·장소 모호성 해소 같은
    # 기존 되묻기 상태와 "제안 대기"가 같은 칸을 두고 서로 지웠다 켰다 하게 된다.
    # 다음 턴에 이 값이 있으면(대화층 4단계) A가 사용자 응답을 결정적으로
    # accept/reject로 해석하고, 그 외 응답이면 자연히 지워진다(딱 한 턴만 유효).
    pending_offer: str | None = None


# ---------------------------------------------------------------- 상태

class AgentState(BaseModel):
    """세션 단위 현재 상태. (계약 1.6절)"""

    session_id: str
    # 검증된 신원(Principal.user_id)이 연결되면 채워진다. 비어 있으면 채우고,
    # 값이 있으면 절대 덮어쓰지 않는다(D-063 결정 3) — session.py의 연결
    # 로직이 이 규칙을 지킨다. FK는 걸지 않는다(D-063 결정 4).
    user_id: str | None = None
    user_conditions: UserConditions = Field(default_factory=UserConditions)
    api_context: ApiContext = Field(default_factory=ApiContext)
    condition_version: int = 0
    last_run_id: str | None = None
    last_intent: str | None = None
    # 직전 턴이 되묻기로 끝났을 때 그 사유 코드(예: "location_required").
    # B는 판단하지 않고 A가 준 값을 보관만 한다 — 되묻기 판정은 LLM과 C가 하고,
    # 소비(부분 갱신 여부 결정)는 A의 state_transform이 한다.
    pending_clarification: str | None = None
    # pending_clarification == "place_ambiguous"일 때만 의미가 있다. 다른 코드로
    # 바뀌거나 지워지면 같이 지워진다(agent_runtime.py의 _remember_clarification).
    pending_info_context: PendingInfoContext | None = None
    # "운영 중이 아닌 곳도 볼게요"를 한 번 선택하면 이 시각까지는 매 턴 다시
    # 묻지 않고 폐점 후보도 계속 포함한다(실사용 피드백, 2026-08-13 — 매 턴
    # 버튼을 다시 눌러야 하는 게 불편하다는 지적).
    ignore_operating_hours_until: datetime | None = None
    # 최근 대화(오래된 것이 앞). MAX_RECENT_TURNS개를 넘으면 앞에서 버린다 —
    # 자르는 책임은 append_conversation_turn() 한 곳에만 둔다.
    # 대화 제목. 사이드바 채팅 히스토리가 목록에 쓴다. user_id와 같은 규칙으로
    # **비어 있으면 채우고 값이 있으면 덮어쓰지 않는다** — 첫 턴의 사용자 발화가
    # 제목이 되고, 사용자가 이름을 바꾸면 그 값이 남는다.
    #
    # recent_turns에서 파생하지 않는 이유는 그 배열이 MAX_RECENT_TURNS개만 남기
    # 때문이다. 첫 질문이 밀려나면 제목이 저절로 바뀐다.
    title: str | None = None
    # 그 대화의 위치(user_conditions.search_center에서 온다). 사이드바가 목록
    # 한 줄에 날짜와 함께 보여준다. title과 같은 규칙으로 **비어 있으면 채우고
    # 덮어쓰지 않는다.**
    #
    # user_conditions에서 그때그때 읽지 않고 여기 박는 이유는 이어가기(resume)가
    # 낡은 조건을 버리면서 user_conditions를 비우기 때문이다 — 지난 대화를 한 번
    # 열면 목록에서 위치가 사라지게 된다.
    location: str | None = None
    recent_turns: list[ConversationTurn] = Field(default_factory=list)
    # 상황 축이 잡은 현재 상태. 상황이 감지된 적이 없으면 None이다.
    situation_state: SituationState | None = None
    status: Literal["active", "expired"] = "active"
    created_at: datetime = Field(default_factory=now_kst)
    updated_at: datetime = Field(default_factory=now_kst)
    last_active_at: datetime = Field(default_factory=now_kst)


# ---------------------------------------------------------------- 이력

class RecommendedItem(BaseModel):
    """노출된 장소 1건. (계약 3.2절)

    estimated_arrival~reason 4개 필드는 SCHEDULE 전용 선택 필드다(SCHEDULE-06).
    distance_km~environment_type 3개 필드는 COMPARE 전용 선택 필드다
    (COMPARE 데이터 출처 A안, 2026-08-11). name은 SCHEDULE-09 2단계 전용
    선택 필드다(2026-08-11, D-060 — 아래 설명 참고). 셋 다 RECOMMEND-only
    흐름에서는 관련 없는 필드가 None으로 남는다.

    "B는 place_id만 저장하고 장소 상세 정보를 보관하지 않는다"는 원칙
    (history.py 참고)의 세 가지 예외다 — 일반 장소 상세(주소·좌표 등)는
    여전히 C의 책임이고 B가 저장하지 않는다. 여기 저장하는 값은 그 자체가
    "추천 시점에 계산된 Feature 스냅샷"이라, 시간이 지나 실제 값과 달라져도
    문제가 되지 않는다 — 오히려 COMPARE는 최신값이 아니라 이 스냅샷을 써야
    한다(int-04-compare.md §13). "과거 정보가 현재 정보로 오인되는" 상황을
    막으려는 원 원칙과 배치되지 않는 이유는, 이 값을 "현재 상태"로 다시
    쓰는 소비자가 없고 오직 "그때 보여준 비교 데이터"로만 쓰이기 때문이다.

    name(장소 이름)은 원래 이 예외에 넣지 않고 SCHEDULE-09 2단계에서 매 턴
    C의 새 응답에서 다시 매칭해 채우도록 설계했다. 그런데 실사용 테스트에서
    "경복궁" 같은 지명 검색이 호출마다 조금씩 다른 좌표로 resolve돼(Naver
    local search fallback) 이번 턴 주변 후보 목록이 매번 완전히 달라지는
    사례가 확인됐다 — 그러면 이전 턴에 고른 place_id가 이번 턴 후보에
    전혀 안 잡혀 이름을 못 채우고, pinned 유지가 통째로 실패해 REJECT_ALL처럼
    전체 재편성으로 조용히 폴백된다(2026-08-11 실사용 재현). name도 여기
    저장해두면 이 재검색에 의존하지 않아 안정적이다.

    latitude/longitude는 네 번째 예외다(SCHEDULE-12). 일정 편성의 후보 간 거리
    계산(`agent_runtime._build_pairwise_distances_km`)은 좌표를 이번 턴 C 응답에서만
    찾고 못 찾으면 조용히 건너뛴다. RECOMMEND 직후 SCHEDULE로 이어지는 흐름에서는
    후보가 같은 검색 결과라 그 전제가 성립했지만, 보관함(SavedPlaceList)으로 여러
    턴에 걸쳐 담은 장소가 후보에 들어오면 깨진다 — 이번 턴 검색 반경 밖이면 C 응답에
    아예 없어 LLM이 거리 근거 없이 동선을 짠다. 이 값도 name과 같은 "추천 시점
    스냅샷"이라 나중에 실제와 달라져도 문제가 되지 않는다.

    rank가 이미 방문 순서(ScheduleItem.order)를 담으므로 별도 order
    필드는 두지 않는다.
    """

    place_id: str
    run_id: str
    rank: int
    shown_at: datetime = Field(default_factory=now_kst)
    name: str | None = None
    # 추천 시점 좌표 스냅샷(SCHEDULE-12). C 응답에 좌표가 없던 경로로 기록된
    # 항목(routes/recommendations.py)과 이 필드 도입 이전 세션에서는 None이다.
    latitude: float | None = None
    longitude: float | None = None
    estimated_arrival: str | None = None
    estimated_duration_min: int | None = None
    travel_to_next_min: int | None = None
    reason: str | None = None
    distance_km: float | None = None
    remaining_minutes: int | None = None
    environment_type: str | None = None


class RecommendedItemInput(BaseModel):
    """history.record_recommended() 호출 시 넘기는 입력 1건.

    place_id/rank는 필수, 나머지는 SCHEDULE 전용(SCHEDULE-06),
    COMPARE 전용(2026-08-11), SCHEDULE-09 2단계 전용(2026-08-11, name) 또는
    SCHEDULE-12 전용(latitude/longitude) 선택 필드다.
    """

    place_id: str
    rank: int
    name: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    estimated_arrival: str | None = None
    estimated_duration_min: int | None = None
    travel_to_next_min: int | None = None
    reason: str | None = None
    distance_km: float | None = None
    remaining_minutes: int | None = None
    environment_type: str | None = None


class RejectedItem(BaseModel):
    """거절된 장소 1건. (계약 3.2절)

    reason_code는 Package A가 해석한 값을 그대로 저장하며 검증하지 않는다.
    """

    place_id: str
    run_id: str
    reason_code: str | None = None
    rejected_at: datetime = Field(default_factory=now_kst)


class ClosedExclusionItem(BaseModel):
    """D의 하드 필터(_is_closed)가 폐점이라 걸러낸 후보 1건. (TP-82)

    recommended/rejected와 달리 "노출됐다"도 "사용자가 거절했다"도 아니다 —
    D 응답에 아예 담기지 못해 노출 이력 경로를 탈 수 없는 후보를 별도로
    추적하기 위한 항목이다. 운영시간은 시각에 따라 바뀌므로(닫혀 있던
    곳이 다음 날 다시 열림) recommended/rejected처럼 영구 보관하지 않고,
    clear_recommended()에서 함께 비운다(history.py 참고).
    """

    place_id: str
    run_id: str
    excluded_at: datetime = Field(default_factory=now_kst)


class RecommendationHistory(BaseModel):
    """세션 단위 추천·거절 이력. append-only. (계약 3.2절)"""

    session_id: str
    # AgentState.user_id와 동일한 규칙(TP-101 3단계, D-063): 비어 있으면
    # 채우고, 값이 있으면 덮어쓰지 않는다. FK는 걸지 않는다.
    user_id: str | None = None
    recommended: list[RecommendedItem] = Field(default_factory=list)
    rejected: list[RejectedItem] = Field(default_factory=list)
    # TP-82: D의 하드 필터가 폐점이라 걸러낸 후보 id. recommended/rejected와
    # 분리된 별도 리스트다 — "노출했다"로 잘못 취급되면 COMPARE의 "첫 번째"가
    # 실제로 안 보여준 장소를 가리키게 된다(댓글/카드 참고).
    closed_excluded: list[ClosedExclusionItem] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=now_kst)


# ---------------------------------------------------------------- 보관함

class SavedPlaceItem(BaseModel):
    """사용자가 명시적으로 담은 장소 1건. (SCHEDULE-12)

    RecommendedItem/RejectedItem과 결정적으로 다른 점은 "누가 골랐는가"다 —
    recommended는 시스템이 보여준 것이고 rejected는 사용자가 물린 것이지만,
    이건 사용자가 능동적으로 고른 것이다. 그래서 다음 SCHEDULE 턴의 후보
    복귀(SCHEDULE-11/D-107)와 배치 보장에서 세 목록과 다르게 취급된다.

    name은 "B는 place_id만 저장하고 장소 상세 정보를 보관하지 않는다"
    (history.py) 원칙의 예외다 — RecommendedItem.name을 SCHEDULE-09
    2단계에서 예외로 넣은 것과 같은 이유이며, 근거도 같다. "경복궁"류 지명
    검색이 호출마다 조금씩 다른 좌표로 resolve돼 이번 턴 후보 목록이 매번
    달라지는 사례가 실사용에서 확인됐고(2026-08-11), 그러면 담아둔 place_id를
    이번 턴 후보에서 다시 못 찾아 이름을 못 채운다. 보관함은 담고 나서 여러
    턴 뒤에 쓰이는 것이 정상이라 이 재검색 실패 확률이 recommended보다 오히려
    높다.

    latitude/longitude는 후속 카드에서 채운다 — 후보 간 거리 계산
    (agent_runtime._build_pairwise_distances_km)이 이번 턴 C 응답에서만
    좌표를 찾기 때문에 검색 반경 밖의 보관함 장소는 거리 근거를 잃는다.
    지금은 필드만 열어두고 None으로 남긴다.
    """

    place_id: str
    name: str
    # 어느 실행에서 노출된 것을 담았는지. 이력(RecommendedItem.run_id)과 대조해
    # "그때 본 그 장소"를 되짚을 수 있게 남긴다.
    saved_from_run_id: str
    saved_at: datetime = Field(default_factory=now_kst)
    latitude: float | None = None
    longitude: float | None = None


class SavedPlaceList(BaseModel):
    """세션 단위 장소 보관함. (SCHEDULE-12)

    RecommendationHistory에 얹지 않고 별도 엔티티로 둔 이유가 두 가지다.

    1. 이력은 append-only인데 보관함은 담기/빼기가 되는 가변 상태다.
    2. `clear_recommended()`(계약 5.5절 history reset)가 recommended와
       closed_excluded를 비우는데, 보관함이 거기 얹혀 있으면 "다른 곳
       보여줘" 한 번에 사용자가 담아둔 것이 함께 날아간다.

    items의 순서는 담은 순서다(오래된 것이 앞). 일정 편성에서 보관함 개수가
    항목 수 상한을 넘을 때 무엇을 남길지 이 순서로 정하므로(SCHEDULE-12
    설계안 4절) 정렬을 바꾸지 않는다 — 점수 순으로 자르면 왜 그 곳이 빠졌는지
    사용자에게 설명할 수 없다.
    """

    session_id: str
    # AgentState.user_id와 동일한 규칙(TP-101 3단계, D-063): 비어 있으면
    # 채우고, 값이 있으면 덮어쓰지 않는다. FK는 걸지 않는다. 지금은 세션
    # TTL과 함께 소멸하지만, 정식 인증(D-062 Phase 5) 이후 계정 단위로
    # 옮길 때 이 필드가 이관 기준이 된다.
    user_id: str | None = None
    items: list[SavedPlaceItem] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=now_kst)


# ---------------------------------------------------------------- 취향

class UserPreference(BaseModel):
    """사용자가 취향 설정 화면에서 고른 항목 하나.

    프론트 `state/preferenceStorage.ts`의 SavedPreference와 같은 모양이다.
    백엔드는 이 값을 해석하지 않고 그대로 보관한다 — 칩과 DB 코드의 대응은
    화면이 갖고 있고(`pages/preferenceOptions.ts`), 여기서 다시 검증하면
    칩 목록을 고칠 때마다 두 곳이 갈린다.
    """

    label: str
    # preference | place_tag | custom. custom은 사용자가 직접 넣은 키워드라
    # 대응 코드가 없다(codes가 빈 배열).
    source: str
    codes: list[str] = Field(default_factory=list)


class UserPreferenceList(BaseModel):
    """계정 단위 취향. (TP-222 후속)

    **이 모델만 session_id가 아니라 user_id로 키를 잡는다.** 다른 상태
    엔티티(AgentState·RecommendationHistory·SavedPlaceList)는 전부 세션 단위이고
    세션 TTL과 함께 사라지지만, 취향은 세션을 넘어 사람에게 붙는 값이다 —
    세션에 얹으면 대화를 새로 시작할 때마다 다시 골라야 한다.

    그래서 user_id가 `str | None`이 아니라 필수다. 신원이 없으면 저장할 자리가
    정해지지 않으므로, 라우트도 RequiredPrincipal을 쓴다.

    items의 순서는 사용자가 고른 순서이고 화면이 그대로 보여준다.
    """

    user_id: str
    items: list[UserPreference] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=now_kst)


# ---------------------------------------------------------------- 즐겨찾기

class UserFavorite(BaseModel):
    """사용자가 즐겨찾기에 담은 장소 하나. (위치 설정 화면)

    프론트 `state/sidebarStorage.ts`의 FavoritePlace와 같은 모양이다. 백엔드는
    이 값을 해석하지 않고 그대로 보관한다 — UserPreference와 같은 판단이다.

    `label`과 `search_center_name`을 나눠 두는 이유는 사용자가 이름을 바꾸기
    때문이다. "역삼역"을 담아 "회사"로 고쳐도 검색에는 담을 때의 장소 이름이
    나가야 한다. 사이드바에서 자유 입력으로 만든 옛 항목에는 이 값이 없어
    label로 떨어진다.
    """

    id: str
    label: str
    search_center_name: str | None = None
    address: str | None = None


class UserFavoriteList(BaseModel):
    """계정 단위 즐겨찾기.

    UserPreferenceList와 같은 모양이다 — 세션이 아니라 사람에게 붙는 값이라
    키가 user_id이고, items의 순서는 담은 순서이며 화면이 그대로 보여준다.
    """

    user_id: str
    items: list[UserFavorite] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=now_kst)


# ---------------------------------------------------------------- 기록

class ConditionChangeLog(BaseModel):
    """조건 변경 기록 1건. append-only. (계약 2.8절, 5.5절)

    사용자 원문 발화와 LLM 원문 응답은 기록하지 않는다.
    """

    session_id: str
    run_id: str
    seq: int
    op: str                       # Add / Update / Remove / Reset
    field: str | None = None      # Reset인 경우 None
    before_value: Any = None
    after_value: Any = None
    reset_scope: str | None = None
    applied_at: datetime = Field(default_factory=now_kst)


class SessionMessage(BaseModel):
    """한 턴에 화면으로 나갔던 것 전부. append-only. (TP-222 후속 — 화면 기록)

    **recent_turns와 역할이 다르다.** 저것은 모델에 넣을 맥락이라
    MAX_RECENT_TURNS(=5)에서 잘리고, 이것은 사람이 다시 볼 화면이라 자르지
    않는다. 추천 이력과도 다르다 — 그것은 "다음 추천에서 뺄 곳"이라 대화를
    이어갈 때 비워진다. 셋을 한 데이터로 겸하는 동안은 지난 대화를 되돌릴 때
    손실이 구조적으로 생겼다.

    payload는 A의 AgentResponse를 직렬화한 그대로다. **B는 열어보지 않는다** —
    파싱하면 A의 스키마가 바뀔 때마다 B가 따라가야 하고, 지금 B는 app.schemas에
    의존하지 않는다. trace_records의 step 등을 다루는 방식과 같다.

    계약(agent-state-contract-v1.md)의 두 금지를 여는 자리다: 전제의 "사용자
    원문 발화와 LLM 원문 응답은 저장하지 않는다"와 3.2절의 "B는 place_id만
    저장한다". 원칙이 지키려던 것("과거 정보가 현재 정보로 오인되는" 상황)은
    저장이 아니라 표시에서 지킨다 — 운영시간처럼 시간이 지나면 틀리는 값은
    복원 화면에서 다시 그리지 않는다.
    """

    session_id: str
    # 그 턴의 run_id. 같은 턴의 다른 기록과 잇는 열쇠다. 응답이 run_id 없이
    # 끝나는 경로가 있어 선택이다.
    run_id: str | None = None
    user_id: str | None = None
    # payload 안에도 있지만 밖으로 꺼내 둔다 — 목록을 훑을 때 payload 전체를
    # 열지 않으려는 것이다.
    user_input: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    recorded_at: datetime = Field(default_factory=now_kst)


class SavedSchedule(BaseModel):
    """사용자가 저장한 일정 1건. (SCHEDULE, 카드 2)

    **화면 기록(SessionMessage)과 겸하지 않는다.** 저쪽은 "그때 화면에 나갔던 것"
    이고 현재 상태로 다시 읽는 소비자를 두지 않는다는 전제 위에 있다. 이것은
    사용자가 "이 일정을 쓰겠다"고 고른 것이라 이름을 붙이고 나중에 열고 고칠 수
    있어야 한다 — 스냅샷을 편집 대상으로 겸하게 하면 그 전제가 깨진다. 보관함
    (SavedPlaceList)을 추천 이력과 분리한 것과 같은 판단이다.

    **UserPreferenceList와 같은 계정 단위 엔티티다.** user_id가 필수이고 세션
    TTL과 무관하게 남는다. 라우트도 RequiredPrincipal을 쓴다.

    payload는 ScheduleResult를 직렬화한 그대로이며 **B는 열어보지 않는다**
    (SessionMessage.payload와 같은 취급 — app.schemas에 의존하지 않는다).

    session_id/run_id는 출처 표시일 뿐이다. 세션은 30일 뒤 정리되지만 이 행은
    남으므로, 이 값으로 원본 대화를 열려는 화면은 **없을 수 있다**를 전제로 다뤄야
    한다.
    """

    # 서버(DB 기본값)가 만든다. 저장 요청을 만들 때는 아직 없다.
    id: str | None = None
    user_id: str
    session_id: str | None = None
    run_id: str | None = None
    # 목록에 보여줄 이름. payload 안의 route_summary는 LLM이 쓴 문장이고
    # 이것은 사용자의 것이다.
    title: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=now_kst)
    updated_at: datetime = Field(default_factory=now_kst)


class TraceRecord(BaseModel):
    """실행 단계 1건. append-only. (docs/package-b/llmops-trace-contract-v1.md 2절)

    step/prompt_version/scoring_version/variant_id/error_type은 호출자(A/C/D)가
    해석한 값을 그대로 저장하며 검증하지 않는다 — B는 값의 의미를 판단하지 않는다.

    metrics는 그 단계의 도메인 지표다(TP-242). 같은 경계 원칙을 따라 키와 값을
    검증하지 않는다 — 무엇을 셀지는 그 기능을 아는 쪽이 정한다. 지표를 안 싣는
    단계는 None으로 남는다. **사용자 텍스트는 담지 않는다** — 장소 이름·발화가
    없다는 것이 trace_records를 대화 삭제 때 안 지우는 근거다.
    """

    session_id: str
    run_id: str
    trace_id: str
    step: str
    prompt_version: str | None = None
    scoring_version: str | None = None
    variant_id: str | None = None
    latency_ms: int | None = None
    token_usage: int | None = None
    error_type: str | None = None
    metrics: dict[str, Any] | None = None
    recorded_at: datetime = Field(default_factory=now_kst)


FeedbackReasonCode = Literal[
    "intent_mismatch",
    "clarification_unhelpful",
    "context_not_preserved",
    "location_misunderstood",
    "conditions_not_applied",
    "recommendation_not_suitable",
    "other",
]


class FeedbackRecord(BaseModel):
    """응답 1건에 대한 사용자 반응. append-only. (roadmap.md 14번)

    trace_id(run 내부 한 단계)가 아니라 run_id(그 턴의 최종 응답) 단위로
    붙는다 — 사용자는 "이 답변"에 좋아요/싫어요를 누르는 것이지, 그 답변을
    만든 개별 단계(LLM 호출/Tool 호출/Scoring)에 반응하는 게 아니다.
    rating은 화면 버튼이 만드는 고정된 두 값이라(TraceRecord의 step 등과
    달리 호출자가 자유롭게 정하는 값이 아니다) Literal로 검증한다.

    user_input/assistant_message는 2026-08-21 추가된 선택 필드다. B는 원칙적으로
    대화 원문을 저장하지 않지만(ConditionChangeLog 참고), 피드백을 남긴 턴에
    한해서만 예외를 둔다 — 테스트 중 "이 반응이 무엇에 대한 것인지" 확인할
    근거가 필요하다는 요청 때문. 대화 전체 로그와 달리 사용자가 명시적으로
    반응한 턴만 남으므로 노출 범위가 훨씬 좁다. 프론트가 텍스트를 못 찾거나
    안 보내면 None — 이 경우에도 rating 기록 자체는 그대로 유효하다.

    intent는 같은 날 추가된 확장 필드다(D-068 연장). step/prompt_version과
    같은 성격 — 그 턴의 assistant_text 메시지가 이미 들고 있는 값을 그대로
    옮겨오는 것뿐이라 B는 검증하지 않는다.

    comment는 "싫어요" 클릭 시 사용자가 선택적으로 남기는 짧은 사유다(develop
    PR에서 병합, D-069). like에는 입력창을 보여주지 않으므로 사실상 dislike
    전용이지만, 스키마에서 rating으로 강제하지는 않는다 — 화면 흐름이 바뀌어도
    스키마를 다시 손대지 않게 한다. user_input/assistant_message와 같은 위험
    등급(자유 텍스트)으로 취급하되, 짧은 사유라는 용도에 맞춰 500자로 길이를
    제한한다.

    reason_code는 개선 집계용 표준 사유다. comment는 어떤 사유에든 선택적으로
    덧붙일 수 있는 보조 설명이다. ``run_id``로 TraceRecord와 조인하면 "어느 프롬프트·어느
    인텐트에서 어떤 불만이 나왔는지"를 재현할 수 있다.
    """

    session_id: str
    run_id: str
    rating: Literal["like", "dislike"]
    user_input: str | None = None
    assistant_message: str | None = None
    intent: str | None = None
    reason_code: FeedbackReasonCode | None = None
    comment: str | None = Field(default=None, max_length=500)
    recorded_at: datetime = Field(default_factory=now_kst)
