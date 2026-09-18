/*
 * 역할: 위치 설정값을 상단 위치 칩이 그릴 모양으로 바꾼다.
 * 입력: 지금 정해져 있는 출발지·검색 기준, 그리고 둘 다 없을 때 쓸 대체 이름.
 * 출력: 한 칸으로 그릴지 두 칸으로 그릴지, 각 칸에 넣을 (잘린) 이름, 화면 낭독용 문구.
 * 호출 시점: HomePage·ChatPage가 AppHeader에 넘길 값을 만들 때.
 *
 * **왜 두 칸이 필요한가.** 예전에는 `center ?? origin ?? …` 사다리로 하나만 골라
 * 보여줬는데, 그러면 검색 기준이 있을 때 출발지가 화면에서 사라진다. "지금
 * 안국역인데 광화문역 근처"라고 말하면 헤더는 광화문역만 말하지만 카드의 이동시간과
 * 거리는 전부 안국역에서 잰 값이다(D-067). 사용자는 그 기준점을 화면 어디서도 볼 수
 * 없었다. 위치 설정 화면은 지금도 둘을 갈라 보여주고 있어서 두 화면이 다른 사실을
 * 말하던 셈이다.
 *
 * **둘이 같으면 한 칸으로 접는다.** `안국역 → 안국역`은 같은 이름을 두 번 쓰는 것이라
 * 읽는 사람이 얻는 게 없다. 두 칸이 기본이라 사용자는 매 턴 "출발 → 검색" 구조를
 * 보게 되고, 그래서 한 칸으로 접혀 있어도 "같은 곳이구나"로 읽힌다.
 *
 * **사용자 위치를 모르는 턴은 출발지 자리에 검색지를 쓴다.** 출발지를 정하지 않으면
 * 이 자리는 "현재 위치"라고 말해 왔는데, 기기 좌표도 발화한 위치도 없으면 서버는
 * 검색지를 그 자리에 놓고 거리를 잰다(domain/ranking_origin.py). 그때 칩만 "현재
 * 위치"라고 하면 카드의 거리가 어디서 잰 값인지와 어긋난다 — 사용자는 자기가 있는
 * 곳에서 잰 값으로 읽게 된다.
 */

import type { LocationSettings } from "../state/locationSettings";
import type { AgentResponse } from "../types";

/** 출발지를 따로 정하지 않았을 때 그 자리에 쓰는 이름. */
export const DEVICE_LOCATION_LABEL = "현재 위치";

/** 사용자 위치를 몰라 검색지로 대체된 턴의 기준점. 이름을 못 받았으면 name이 null이다. */
export interface SubstitutedOrigin {
  name: string | null;
}

/**
 * 사용자 위치를 몰라 검색지에서 거리를 잰 턴인지 본다. 그런 턴이면 그 지점의
 * 이름을, 아니면 null을 돌려준다.
 *
 * 서버가 턴마다 "거리를 실제로 어디서 쟀는지"를 이름과 출처로 함께 내려준다.
 * 출처가 넷인데 칩이 반응할 것은 하나뿐이다.
 *
 * - `search_center` — 사용자 위치를 몰라 검색지로 **대체한** 경우. 여기만 해당한다.
 * - `travel_origin_override` — "안국역에서 10분"처럼 발화가 출발점을 확정한 경우.
 *   값이 사실과 어긋난 게 아니라 사용자가 그렇게 말한 것이라 그대로 둔다.
 * - `query`·`device_gps` — 사용자 위치를 아는 경우. 지금까지와 같다.
 *
 * **첫 발화 전에는 null이다.** 기기 좌표는 발화를 보낼 때 받아오므로, 그전에
 * "좌표가 없다"만 보고 접으면 GPS가 멀쩡한 기기에서도 처음엔 한 칸으로 보이다가
 * 첫 답변 뒤에 두 칸으로 바뀐다. 서버가 판정한 턴에만 반응한다.
 */
export function readSubstitutedOrigin(
  response: AgentResponse | null | undefined,
): SubstitutedOrigin | null {
  /* 위치 세 갈래는 RECOMMEND(context_fetch)에서만 채워진다 — 실행 목록에서 이 값을
     실은 것을 찾는다. INFO·COMPARE 턴에는 없어 그대로 null이 된다. */
  const origin = response?.tool_executions?.find((item) => item.route_origin)?.route_origin;
  if (!origin || origin.source !== "search_center") return null;
  /* **이름이 없는 것과 대체가 없었던 것은 다르다.** 좌표만 알고 부를 이름이 없는
     지점도 있어서(기기 좌표가 그렇다) 이름만 돌려주면 그 둘이 같은 null로 뭉개진다.
     대체되었다는 사실이 이 함수의 답이고, 이름은 그 안에 담는다. */
  return { name: origin.name };
}

/*
 * 이름 하나가 차지할 수 있는 최대 글자수.
 *
 * 375px 화면에서 칩이 쓸 수 있는 폭은 295px이고(헤더 좌우 여백 32 + 햄버거와 간격
 * 48을 뺀 값), 그중 88px은 칩 내부 고정분(좌우 여백·아이콘 둘·화살표)이라 이름
 * 둘이 나눠 쓸 폭은 207px이다. 한글은 14px 글꼴에서 글자당 약 14px이므로 둘을
 * 합쳐 14~15자가 물리적 한계다.
 *
 * **그래서 이 상한은 좁은 화면을 위한 값이 아니다.** 그쪽은 CSS truncate가 받는다.
 * 이 값이 막는 것은 넓은 화면에서 긴 이름 하나가 칩을 통째로 잡아먹는 경우다 —
 * 장소 검색에는 실제로 `COSMOS BIGBANG 20TH ANNIVERSARY MEDIA EXHIBITION` 같은
 * 48자짜리가 걸린다.
 *
 * 10자는 사용자가 위치로 고르는 이름을 다 담는다 — 광화문역(4), 성수동(3),
 * 종로구(3), 현재 위치(5), 국립중앙박물관(7). 장소 스냅샷 16,860건(축제·전시까지
 * 섞여 위치로 고를 이름보다 긴 쪽)으로도 62%가 10자 이하다.
 */
export const MAX_CHIP_NAME_LENGTH = 10;

const ELLIPSIS = "…";

/*
 * 코드포인트로 센다. `.length`는 UTF-16 단위라 이모지가 섞인 이름을 반쪽만 잘라
 * 깨진 글자를 남긴다.
 */
export function truncateName(name: string, max: number = MAX_CHIP_NAME_LENGTH): string {
  const points = Array.from(name);
  if (points.length <= max) return name;
  return points.slice(0, max).join("") + ELLIPSIS;
}

export type LocationChipModel =
  | {
      kind: "single";
      /* 잘린 이름. 화면에 그대로 그린다. */
      name: string;
      /* 이 자리가 기기 좌표이고 그 좌표를 실제로 갖고 있는가 — 깜빡이는 점을 붙일지 정한다. */
      isDeviceLocation: boolean;
      /* 기기 좌표를 쓸 자리인데 아직 못 받았는가. 깜빡이지 않는 회색 점이 붙는다. */
      isDeviceLocationPending: boolean;
      /* 낭독용 문구. 자르지 않은 원래 이름이 들어간다. */
      description: string;
    }
  | {
      kind: "pair";
      origin: string;
      center: string;
      isDeviceLocation: boolean;
      isDeviceLocationPending: boolean;
      description: string;
    };

/*
 * 낭독 문구는 위치 설정 화면의 말투를 그대로 쓴다 — 두 화면이 같은 말을 해야
 * 사용자가 옮겨 다니며 다시 배우지 않는다.
 *
 * **자르지 않은 이름을 넣는다.** 화면에서 `…`로 잘린 이름이 낭독까지 잘리면 그
 * 사용자는 어디인지 알 방법이 없다.
 */
function describe(origin: string, center: string): string {
  return `${origin}에서 출발, ${center} 주변에서 검색`;
}

/**
 * 위치 칩이 그릴 모양을 정한다.
 *
 * @param settings 지금 정해져 있는 출발지·검색 기준.
 * @param fallbackCenter 둘 다 비어 있을 때 검색 기준 자리에 쓸 이름. 대화가 이미
 *   해석해 둔 위치가 있으면 그것을 넘긴다(없으면 기기 좌표를 쓴다는 뜻이라 null).
 * @param substitutedOrigin 사용자 위치를 몰라 검색지에서 거리를 잰 턴이면 그 지점의
 *   이름(`readSubstitutedOrigin`의 결과). 그런 턴이 아니면 null이다. 이름을 못
 *   받았으면 검색 기준 자리의 이름을 그대로 쓴다 — 거리를 잰 곳이 거기라는 사실은
 *   이름을 몰라도 달라지지 않는다.
 * @param hasDeviceLocation 기기 좌표를 실제로 갖고 있는가(TripState.device_location).
 *   **이름과 좌표는 따로 논다** — 출발지를 안 정하면 이 자리는 "현재 위치"라고 말하지만,
 *   좌표는 발화를 보낼 때만 받는다. 그래서 이름만 보고 초록 점을 붙이면 좌표가 없는데도
 *   "지금 GPS를 쓰는 중"이라고 말하게 된다. 실제로 그런 상태가 생긴다 — 새 대화(RESET)는
 *   좌표를 지우지만 출발지·검색지는 sessionStorage에 남기 때문이다.
 */
export function buildLocationChipModel(
  settings: LocationSettings,
  fallbackCenter: string | null = null,
  hasDeviceLocation = false,
  substitutedOrigin: SubstitutedOrigin | null = null,
): LocationChipModel {
  const origin = settings.origin;
  /* 검색 기준을 비워두면 출발지가 검색 중심이 되고, 그것도 없으면 대화가 해석한
     위치가, 그것도 없으면 기기 좌표가 중심이 된다(agent_context/service.py). */
  const center = settings.center ?? settings.origin ?? fallbackCenter;

  const centerLabel = center ?? DEVICE_LOCATION_LABEL;
  /* 검색지로 대체된 턴이면 **설정값보다 이 이름이 앞선다.** 출발지를 정해 뒀는데
     그 이름이 해석되지 않아 대체된 경우까지 포함해서, 거리를 실제로 잰 곳을
     말하는 것이 이 자리의 일이기 때문이다. */
  const substitutedLabel = substitutedOrigin === null ? null : (substitutedOrigin.name ?? centerLabel);
  const originLabel = substitutedLabel ?? origin ?? DEVICE_LOCATION_LABEL;
  /* 이 자리에 기기 좌표를 쓸 자리인가(이름 기준)와, 그 좌표를 실제로 갖고 있는가는
     다른 질문이다. 셋으로 갈라야 화면이 사실과 어긋나지 않는다 — 쓰는 중(초록),
     쓸 예정인데 아직 없음(회색), 사용자가 이름으로 정함(아이콘). */
  /* 대체된 턴은 기기 좌표를 쓰지 않는다 — 그 자리에 점을 붙이면 없는 좌표를
     쓰는 중이라고 말하게 된다. */
  const usesDeviceLocation = substitutedLabel === null && origin === null;
  const isDeviceLocation = usesDeviceLocation && hasDeviceLocation;
  const isDeviceLocationPending = usesDeviceLocation && !hasDeviceLocation;

  /* 둘이 같은 곳을 가리키면 한 칸으로 접는다. 출발지를 정하지 않았는데 검색
     기준도 없는 경우(둘 다 기기 좌표)도 여기로 온다. */
  if (originLabel === centerLabel) {
    return {
      kind: "single",
      name: truncateName(centerLabel),
      isDeviceLocation,
      isDeviceLocationPending,
      description: describe(originLabel, centerLabel),
    };
  }

  return {
    kind: "pair",
    origin: truncateName(originLabel),
    center: truncateName(centerLabel),
    isDeviceLocation,
    isDeviceLocationPending,
    description: describe(originLabel, centerLabel),
  };
}
