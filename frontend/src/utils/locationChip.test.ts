/*
 * 역할: 상단 위치 칩이 무엇을 몇 칸으로 보여줄지 정하는 규칙을 검증한다.
 * 호출 시점: vitest 실행 시.
 *
 * **이 파일의 핵심은 "언제 두 칸인가"다.** 하나로 줄이면 카드의 이동시간을 어디서
 * 쟀는지가 화면에서 사라지고(D-067), 늘 두 칸이면 같은 이름을 두 번 쓰는 자리가
 * 생긴다. 그 경계를 여기서 못 박는다.
 */

import { expect, test } from "vitest";
import {
  buildLocationChipModel,
  MAX_CHIP_NAME_LENGTH,
  readSubstitutedOrigin,
  truncateName,
} from "./locationChip";
import type { AgentResponse, LocationDebug } from "../types";

/* 실행 기록 한 건만 들고 있는 응답. 칩이 보는 것은 route_origin 하나뿐이라 나머지는
   채우지 않는다. */
function responseWithRouteOrigin(routeOrigin: LocationDebug | null): AgentResponse {
  return {
    tool_executions: [
      {
        request_id: "req-1",
        status: "success",
        latency_ms: 10,
        providers: [],
        context_items: [],
        rule_versions: {},
        resolved_location_name: null,
        resolved_location_address: null,
        route_origin: routeOrigin,
        error_code: null,
        clarification_code: null,
        is_proxy: null,
        candidate_status_counts: {},
      },
    ],
  } as unknown as AgentResponse;
}

test("출발지를 정하지 않으면 기기 좌표에서 출발한다고 말한다", () => {
  /* 출발지가 "없는" 상태는 없다 — 안 정했으면 기기 좌표가 출발지다. */
  const model = buildLocationChipModel({ origin: null, center: "광화문역" }, null, true);

  expect(model).toMatchObject({
    kind: "pair",
    origin: "현재 위치",
    center: "광화문역",
    isDeviceLocation: true,
    isDeviceLocationPending: false,
    description: "현재 위치에서 출발, 광화문역 주변에서 검색",
  });
});

test("출발지를 따로 정하면 깜빡이는 점 대신 출발지 아이콘을 쓴다", () => {
  /* 그 점의 뜻은 "지금 GPS를 쓰는 중" 하나여야 한다. 사용자가 고른 장소 옆에서
     실시간을 흉내 내면 거기 있는 것처럼 읽힌다. */
  const model = buildLocationChipModel({ origin: "안국역", center: "광화문역" });

  expect(model).toMatchObject({
    kind: "pair",
    origin: "안국역",
    center: "광화문역",
    isDeviceLocation: false,
  });
});

test("출발지와 검색 기준이 같으면 한 칸으로 접는다", () => {
  const model = buildLocationChipModel({ origin: "안국역", center: "안국역" });

  expect(model).toMatchObject({ kind: "single", name: "안국역", isDeviceLocation: false });
});

test("검색 기준을 비워두면 출발지가 검색 중심이 되어 한 칸이 된다", () => {
  /* agent_context/service.py의 사다리와 같은 판단이다 — 검색 기준이 없으면
     출발지가 중심이므로, 두 칸으로 나눠 봐야 같은 이름이 두 번 나온다. */
  const model = buildLocationChipModel({ origin: "안국역", center: null });

  expect(model).toMatchObject({ kind: "single", name: "안국역" });
});

test("아무것도 정하지 않았고 대화도 없으면 현재 위치 한 칸이다", () => {
  const model = buildLocationChipModel({ origin: null, center: null }, null, true);

  expect(model).toMatchObject({
    kind: "single",
    name: "현재 위치",
    isDeviceLocation: true,
    isDeviceLocationPending: false,
  });
});

test("설정이 비어 있을 때만 대화가 해석한 위치로 떨어진다", () => {
  /* 대화가 이미 있으면 서버가 그 위치를 들고 있어서 다음 발화도 거기서 찾는다. */
  const fallback = buildLocationChipModel({ origin: null, center: null }, "성수동");
  const setting = buildLocationChipModel({ origin: null, center: "광화문역" }, "성수동");

  expect(fallback).toMatchObject({ kind: "pair", origin: "현재 위치", center: "성수동" });
  expect(setting).toMatchObject({ kind: "pair", center: "광화문역" });
});

test("긴 이름은 잘라도 낭독 문구에는 원래 이름이 남는다", () => {
  /* 화면에서 잘린 이름이 낭독까지 잘리면 그 사용자는 어디인지 알 방법이 없다. */
  const long = "서울특별시립미술관서소문본관";
  const model = buildLocationChipModel({ origin: null, center: long });

  expect(model.kind).toBe("pair");
  if (model.kind !== "pair") return;
  expect(model.center).toBe("서울특별시립미술관서…");
  expect(Array.from(model.center)).toHaveLength(MAX_CHIP_NAME_LENGTH + 1);
  expect(model.description).toContain(long);
});

test("상한 이하면 자르지 않는다", () => {
  expect(truncateName("국립중앙박물관")).toBe("국립중앙박물관");
  expect(truncateName("가".repeat(MAX_CHIP_NAME_LENGTH))).toBe(
    "가".repeat(MAX_CHIP_NAME_LENGTH),
  );
});

test("이모지가 섞인 이름을 반쪽으로 자르지 않는다", () => {
  /* .length는 UTF-16 단위라 이모지 하나가 2로 세어져, 경계에 걸리면 깨진 글자가
     남는다. 코드포인트로 세야 한다. */
  const name = "🎨".repeat(MAX_CHIP_NAME_LENGTH + 2);

  const cut = truncateName(name);

  expect(cut).toBe("🎨".repeat(MAX_CHIP_NAME_LENGTH) + "…");
  expect(cut).not.toContain("�");
});

/*
 * 이름이 "현재 위치"인 것과 좌표를 갖고 있는 것은 다른 사실이다.
 *
 * 좌표는 발화를 보낼 때만 받고, 새 대화(RESET)는 좌표만 지우고 출발지·검색지는
 * sessionStorage에 남긴다. 그래서 "현재 위치"라고 적힌 채 좌표가 없는 상태가
 * 실제로 생긴다 — 그때 초록 점이 깜빡이면 화면이 사실과 다른 말을 한다.
 */
test("좌표를 아직 못 받았으면 기기 좌표 자리라도 초록이 아니다", () => {
  const model = buildLocationChipModel({ origin: null, center: "광화문역" }, null, false);

  expect(model).toMatchObject({
    kind: "pair",
    origin: "현재 위치",
    isDeviceLocation: false,
    isDeviceLocationPending: true,
  });
});

test("사용자가 이름으로 정한 자리는 좌표가 없어도 대기 상태가 아니다", () => {
  /* 회색 점은 "곧 여기가 될 텐데 아직 모른다"는 뜻이라, 사용자가 고른 장소에는
     붙으면 안 된다. 그쪽은 지금처럼 아이콘이 붙는다. */
  const model = buildLocationChipModel({ origin: "안국역", center: "광화문역" }, null, false);

  expect(model).toMatchObject({
    isDeviceLocation: false,
    isDeviceLocationPending: false,
  });
});

/*
 * 사용자 위치를 모르는 턴은 서버가 검색지에서 거리를 잰다. 그때 칩이 "현재 위치"라고
 * 하면 카드의 거리가 사용자가 있는 곳에서 잰 값으로 읽힌다.
 */
test("검색지로 대체된 턴이면 출발지 자리에 그 검색지를 쓴다", () => {
  const substituted = readSubstitutedOrigin(
    responseWithRouteOrigin({
      name: "광화문역",
      source: "search_center",
      latitude: 37.5,
      longitude: 127,
    }),
  );

  const model = buildLocationChipModel(
    { origin: null, center: "광화문역" },
    null,
    false,
    substituted,
  );

  /* 출발지와 검색 기준이 같은 이름이 되므로 한 칸으로 접힌다. */
  expect(model).toMatchObject({
    kind: "single",
    name: "광화문역",
    isDeviceLocation: false,
    /* 기기 좌표를 쓰는 자리가 아니므로 기다리는 표시도 붙지 않는다. */
    isDeviceLocationPending: false,
  });
});

/*
 * "광화문역에서 10분"처럼 발화가 출발점을 확정한 턴도 검색지에서 거리를 재지만,
 * 그건 사용자가 그렇게 말한 것이라 화면이 바뀔 이유가 없다.
 */
test("발화가 출발점을 확정한 턴은 대체로 치지 않는다", () => {
  const substituted = readSubstitutedOrigin(
    responseWithRouteOrigin({
      name: "광화문역",
      source: "travel_origin_override",
      latitude: 37.5,
      longitude: 127,
    }),
  );

  expect(substituted).toBeNull();
});

/*
 * 첫 발화 전에는 판정할 턴이 없다. 여기서 접어버리면 GPS가 멀쩡한 기기에서도 처음엔
 * 한 칸으로 보이다가 첫 답변 뒤에 두 칸으로 바뀐다.
 */
test("아직 한 턴도 없으면 지금까지와 같은 모양이다", () => {
  expect(readSubstitutedOrigin(null)).toBeNull();
  expect(readSubstitutedOrigin(responseWithRouteOrigin(null))).toBeNull();

  const model = buildLocationChipModel({ origin: null, center: "광화문역" }, null, true, null);

  expect(model).toMatchObject({ kind: "pair", origin: "현재 위치", center: "광화문역" });
});

/*
 * 좌표만 알고 부를 이름이 없는 지점도 있다. 이름이 없다고 "대체가 없었다"로 읽으면
 * 칩이 다시 "현재 위치"를 말하게 된다.
 */
test("대체된 지점의 이름을 못 받으면 검색 기준 이름을 그 자리에 쓴다", () => {
  const substituted = readSubstitutedOrigin(
    responseWithRouteOrigin({
      name: null,
      source: "search_center",
      latitude: 37.5,
      longitude: 127,
    }),
  );

  const model = buildLocationChipModel(
    { origin: null, center: "광화문역" },
    null,
    false,
    substituted,
  );

  expect(model).toMatchObject({ kind: "single", name: "광화문역" });
});
