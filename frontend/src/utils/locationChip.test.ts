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

test("출발지를 정하지 않으면 검색 기준 한 칸으로 접는다", () => {
  /* 기기 GPS를 받지 않으므로 출발지가 없으면 서버는 검색지에서 거리를 잰다
     (domain/ranking_origin.py). 출발지 자리를 "현재 위치"라고 하면 카드의 거리가
     사용자가 있는 곳에서 잰 값으로 읽힌다. */
  const model = buildLocationChipModel({ origin: null, center: "광화문역" });

  expect(model).toMatchObject({
    kind: "single",
    name: "광화문역",
    isUnset: false,
    description: "광화문역에서 출발, 광화문역 주변에서 검색",
  });
});

test("출발지를 따로 정하면 두 칸으로 나눠 보여준다", () => {
  const model = buildLocationChipModel({ origin: "안국역", center: "광화문역" });

  expect(model).toMatchObject({
    kind: "pair",
    origin: "안국역",
    center: "광화문역",
    description: "안국역에서 출발, 광화문역 주변에서 검색",
  });
});

test("출발지와 검색 기준이 같으면 한 칸으로 접는다", () => {
  const model = buildLocationChipModel({ origin: "안국역", center: "안국역" });

  expect(model).toMatchObject({ kind: "single", name: "안국역", isUnset: false });
});

test("검색 기준을 비워두면 출발지가 검색 중심이 되어 한 칸이 된다", () => {
  /* agent_context/service.py의 사다리와 같은 판단이다 — 검색 기준이 없으면
     출발지가 중심이므로, 두 칸으로 나눠 봐야 같은 이름이 두 번 나온다. */
  const model = buildLocationChipModel({ origin: "안국역", center: null });

  expect(model).toMatchObject({ kind: "single", name: "안국역" });
});

test("아무것도 정하지 않았고 대화도 없으면 위치 미설정 한 칸이다", () => {
  /* "현재 위치"라고 하면 기기 위치를 쓰는 것처럼 읽힌다. 이 버전은 그런 좌표가 없다. */
  const model = buildLocationChipModel({ origin: null, center: null });

  expect(model).toEqual({
    kind: "single",
    name: "위치 미설정",
    isUnset: true,
    description: "위치를 아직 정하지 않았어요",
  });
});

test("설정이 비어 있을 때만 대화가 해석한 위치로 떨어진다", () => {
  /* 대화가 이미 있으면 서버가 그 위치를 들고 있어서 다음 발화도 거기서 찾는다. */
  const fallback = buildLocationChipModel({ origin: null, center: null }, "성수동");
  const setting = buildLocationChipModel({ origin: null, center: "광화문역" }, "성수동");

  expect(fallback).toMatchObject({ kind: "single", name: "성수동", isUnset: false });
  expect(setting).toMatchObject({ kind: "single", name: "광화문역" });
});

test("긴 이름은 잘라도 낭독 문구에는 원래 이름이 남는다", () => {
  /* 화면에서 잘린 이름이 낭독까지 잘리면 그 사용자는 어디인지 알 방법이 없다. */
  const long = "서울특별시립미술관서소문본관";
  const model = buildLocationChipModel({ origin: "안국역", center: long });

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
 * 사용자 위치를 모르는 턴은 서버가 검색지에서 거리를 잰다. 칩도 그 자리를 말해야
 * 카드의 거리가 어디서 잰 값인지와 어긋나지 않는다.
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

  const model = buildLocationChipModel({ origin: null, center: "광화문역" }, null, substituted);

  /* 출발지와 검색 기준이 같은 이름이 되므로 한 칸으로 접힌다. */
  expect(model).toMatchObject({ kind: "single", name: "광화문역" });
});

test("출발지를 정해 뒀어도 검색지로 대체된 턴이면 대체된 이름이 앞선다", () => {
  /* 정한 출발지 이름이 해석되지 않아 대체된 경우다. 거리를 실제로 잰 곳을 말한다. */
  const substituted = readSubstitutedOrigin(
    responseWithRouteOrigin({
      name: "광화문역",
      source: "search_center",
      latitude: 37.5,
      longitude: 127,
    }),
  );

  const model = buildLocationChipModel({ origin: "안국역", center: "광화문역" }, null, substituted);

  expect(model).toMatchObject({ kind: "single", name: "광화문역" });
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

/* 첫 발화 전에는 판정할 턴이 없다. */
test("아직 한 턴도 없으면 대체가 없다", () => {
  expect(readSubstitutedOrigin(null)).toBeNull();
  expect(readSubstitutedOrigin(responseWithRouteOrigin(null))).toBeNull();
});

/*
 * 좌표만 알고 부를 이름이 없는 지점도 있다. 이름이 없다고 "대체가 없었다"로 읽으면
 * 안 된다.
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

  const model = buildLocationChipModel({ origin: "안국역", center: "광화문역" }, null, substituted);

  expect(model).toMatchObject({ kind: "single", name: "광화문역" });
});
