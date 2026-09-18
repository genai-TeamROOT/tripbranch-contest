/*
 * 역할: 위치 설정 화면에서 정한 출발지와 검색 기준을 sessionStorage에 담는다.
 * 입력: 고른 장소 이름(각각), 또는 비우기.
 * 출력: 지금 정해져 있는 두 값.
 * 호출 시점: LocationPage가 정할 때, HomePage·ChatPage가 발화를 보내거나 상단
 *   위치 pill을 그릴 때, 그리고 응답이 도착해 서버가 쓴 위치를 되돌려 쓸 때
 *   (syncLocationSettingsFromConditions).
 *
 * **두 값은 다른 질문의 답이다.**
 *
 *   출발지(origin)      "사용자가 어디 있는가" — 이동시간을 재는 시작점
 *   검색 기준(center)   "어디 주변을 찾을까" — 후보를 모으는 중심
 *
 * D-067이 둘을 분리한 이유가 여기 있다. "지금 혜화역인데 안국역 근처"를 하나로
 * 합치면 거리·경로가 전부 안국역에서 계산돼, 자동차 1분으로 표시된 곳이 실제로는
 * 23분이었다. 그래서 화면도 고른 장소를 둘 중 무엇으로 쓸지 묻는다.
 *
 * 각각 백엔드의 AgentRequest.selected_current_location·selected_search_center로
 * 간다. 비어 있으면 예전 동작 그대로다 — 출발지는 기기 좌표, 검색 기준은 발화가 정한다.
 *
 * **대화가 아니라 설정이라 TripState에 두지 않는다.** 거기 두면 "새 대화"를 누를 때
 * RESET으로 지워진다 — 실제로 위치를 골라도 사이드바 홈을 거치면 무시되는 문제가
 * 있었다. 취향이 preferenceStorage로 빠져 있는 것과 같은 이유다.
 *
 * **localStorage가 아니라 sessionStorage에 둔다.** 취향("조용한 곳을 좋아함")은
 * 며칠 뒤에도 유효하지만 이 값들은 그날 그 자리의 값이다.
 *
 * 신원이 바뀌면 지운다 — state/localUserData.ts가 로그아웃에서 함께 비운다.
 */

import type { UserConditions } from "../types";

const LOCATION_SETTINGS_KEY = "tb_location_settings";

export interface LocationSettings {
  /** 출발지로 쓸 장소 이름. null이면 기기 좌표를 그대로 쓴다. */
  origin: string | null;
  /** 검색 기준으로 쓸 장소 이름. null이면 발화나 기기 좌표가 정한다. */
  center: string | null;
}

const EMPTY: LocationSettings = { origin: null, center: null };

function normalize(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

export function loadLocationSettings(): LocationSettings {
  try {
    const raw = sessionStorage.getItem(LOCATION_SETTINGS_KEY);
    if (!raw) return EMPTY;
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return EMPTY;
    const value = parsed as Record<string, unknown>;
    return { origin: normalize(value.origin), center: normalize(value.center) };
  } catch {
    /* 저장소가 막혀 있거나(시크릿 모드 등) 값이 깨졌어도 화면은 계속 동작해야 한다. */
    return EMPTY;
  }
}

/*
 * 값이 바뀌면 알려준다. 상단 위치 pill이 구독한다.
 *
 * 저장소는 값이 바뀌어도 React에 알려주지 않는다. 위치 설정 화면에서 정한 순간
 * 헤더가 그대로면 사용자는 반영이 안 된 줄 안다 — 시트로 열린 화면 뒤에서 홈이
 * 계속 마운트된 채라 다시 그려지지 않기 때문이다(savedSchedules와 같은 방식).
 */
type Listener = (settings: LocationSettings) => void;
const listeners = new Set<Listener>();

export function subscribeLocationSettings(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function write(next: LocationSettings): LocationSettings {
  try {
    if (next.origin === null && next.center === null) {
      sessionStorage.removeItem(LOCATION_SETTINGS_KEY);
    } else {
      sessionStorage.setItem(LOCATION_SETTINGS_KEY, JSON.stringify(next));
    }
  } catch {
    /* 저장하지 못해도 이번 화면 동작은 그대로 이어간다. */
  }
  listeners.forEach((listener) => listener(next));
  return next;
}

/** 출발지를 정한다. null이면 기기 좌표로 되돌린다. */
export function setLocationOrigin(origin: string | null): LocationSettings {
  return write({ ...loadLocationSettings(), origin: normalize(origin) });
}

/** 검색 기준을 정한다. null이면 발화·기기 좌표가 정하도록 되돌린다. */
export function setLocationCenter(center: string | null): LocationSettings {
  return write({ ...loadLocationSettings(), center: normalize(center) });
}

/*
 * 서버가 들고 있는 위치 조건을 이 화면의 설정에 되돌려 쓴다.
 *
 * **이 함수가 없으면 배선이 한쪽 방향뿐이다.** 위치 설정 화면에서 정한 값은
 * HomePage·ChatPage가 매 발화에 selected_current_location·selected_search_center로
 * 실어 보내지만, 발화가 위치를 바꿨을 때 그 결과가 저장소로 돌아오는 길이 없었다.
 * 그래서 "지금 안국역인데 광화문역 근처"라고 말해도 상단 pill과 위치 설정 화면은
 * 전에 고른 서대문역을 계속 보여줬다.
 *
 * 표시만의 문제가 아니다. 백엔드 _apply_selected_locations()는 조건 병합(B)보다
 * 앞에서 도는데, "추출된 값이 이미 있으면 손대지 않는다"라서 위치를 말하지 않은
 * 다음 RECOMMEND 턴에는 저장소의 낡은 서대문역이 그대로 채워져 세션 조건의
 * 광화문역을 덮어썼다 — 대화로 옮긴 위치가 조용히 원위치됐다.
 *
 * 읽는 값은 발화가 아니라 **서버가 병합해 들고 있는 조건**
 * (AgentResponse.state.user_conditions)이다. 발화가 위치를 말하지 않은 턴에도
 * 직전 값이 그대로 실려 오므로, 그때는 같은 값을 다시 쓰는 셈이라 아무것도
 * 바뀌지 않는다.
 *
 * **null이면 지우지 않고 지금 값을 그대로 둔다.** null은 "이 설정을 지우라"가
 * 아니라 "서버도 위치를 모른다"는 뜻이다. RECOMMEND 턴은 백엔드
 * _apply_selected_locations()가 조건 병합보다 앞에서 이 설정값을 채워 주므로
 * 설정이 있는 한 거의 null로 오지 않는다 — 실제로 비어 오는 쪽은 세션에 아직
 * RECOMMEND 조건이 없는 INFO 턴 같은 경우다. 위치를 정해 두고 "경복궁 운영시간
 * 알려줘"부터 물었다고 해서 그 설정이 사라져야 할 이유는 없다. 이 값들을
 * TripState에 두지 않은 이유(위 파일 주석)와 같은 판단이다.
 *
 * 그래서 대화로 위치를 푸는 길은 없다. 조건을 실제로 비우는 발화
 * ("조건 다시 정할게"=soft, "새로 시작"=full — state_transform.py의
 * _RESET_SCOPE_PHRASES)도 같은 턴에 위치를 말하지 않으면 위 경로로 이 설정값이
 * 다시 채워진다. 푸는 것은 위치 설정 화면 칩의 ✕ 하나다.
 *
 * 두 값은 따로 판단한다 — "광화문역 근처 알려줘"는 검색 기준만 바꾸고 출발지는
 * 설정해 둔 값을 남겨야 한다.
 *
 * 조건 자체가 없는 응답(null)도 그냥 지나간다. 실제 서버는 항상 채워 보내지만,
 * 이 호출은 스트림 콜백 안이라 여기서 예외가 나면 턴 전체가 오류로 끝난다 —
 * 위치 표시를 못 맞추는 것과 답변을 통째로 잃는 것은 무게가 다르다.
 */
export function syncLocationSettingsFromConditions(
  conditions: Pick<UserConditions, "current_location" | "search_center"> | null | undefined,
): LocationSettings {
  const current = loadLocationSettings();
  if (!conditions) return current;
  const next: LocationSettings = {
    origin: normalize(conditions.current_location) ?? current.origin,
    center: normalize(conditions.search_center) ?? current.center,
  };
  /* 바뀐 게 없으면 쓰지 않는다 — write()는 구독자에게 항상 알리므로, 매 턴
     그냥 부르면 값이 그대로인데도 헤더가 다시 그려진다. */
  if (next.origin === current.origin && next.center === current.center) return current;
  return write(next);
}

export function clearLocationSettings(): void {
  write(EMPTY);
}

export { LOCATION_SETTINGS_KEY };
