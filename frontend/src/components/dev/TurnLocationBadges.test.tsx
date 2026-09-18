/*
 * 역할: 위치 뱃지가 사용자 위치·검색 위치·경로 시작점을 구분해 보여주는지 검증한다.
 * 입력: context_fetch 위치 정보를 담은 감사 turn.
 * 출력: 대체된 시작점 경고, GPS 좌표의 근사 이름, 발화 위치 표기에 대한 assertion.
 * 호출 시점: vitest 실행 시 호출된다.
 */

import { render, screen } from "@testing-library/react";
import { vi } from "vitest";
import type { DeveloperAuditTurn, LocationDebug, ToolExecutionDebug } from "../../types";
import { TurnLocationBadges } from "./TurnLocationBadges";

vi.mock("../../api/dev", () => ({
  fetchNearestArea: vi.fn(async () => ({
    area_code: "POI006",
    area_name: "종로·청계 관광특구",
    distance_km: 0.42,
  })),
}));

const 경복궁: LocationDebug = {
  name: "경복궁",
  source: "query",
  latitude: 37.5788,
  longitude: 126.977,
};

const 안국역: LocationDebug = {
  name: "안국역",
  source: "query",
  latitude: 37.5765,
  longitude: 126.9855,
};

const 기기GPS: LocationDebug = {
  name: null,
  source: "device_gps",
  latitude: 37.5709,
  longitude: 126.999,
};

function _execution(fields: Partial<ToolExecutionDebug>): ToolExecutionDebug {
  return {
    operation: "context_fetch",
    request_id: "req-1",
    status: "success",
    latency_ms: 120,
    providers: [],
    context_items: [],
    rule_versions: {},
    resolved_location_name: null,
    resolved_location_address: null,
    error_code: null,
    clarification_code: null,
    is_proxy: null,
    candidate_status_counts: {},
    ...fields,
  };
}

function _turn(execution: ToolExecutionDebug): DeveloperAuditTurn {
  return {
    id: "turn-1",
    userInput: "근처 카페 알려줘",
    intent: "RECOMMEND",
    status: "complete",
    message: "추천 결과예요.",
    sessionId: "session-1",
    runId: "run-1",
    deviceLocation: null,
    elapsedMsClient: 1200,
    serverElapsedMs: 1100,
    stageTimings: [],
    extractedConditions: null,
    beforeConditions: null,
    afterConditions: null,
    recommendations: null,
    failure: null,
    response: {
      tool_executions: [execution],
      state: { api_context: null },
    } as unknown as DeveloperAuditTurn["response"],
  };
}

it("사용자 위치를 모르면 시작점이 검색 위치로 대체됐다고 알린다", () => {
  // 되묻기로 검색 위치만 정해진 턴. 사용자는 경복궁에 있다고 말한 적이 없는데
  // 거리·실측 경로는 전부 경복궁 기준으로 계산된다.
  render(
    <TurnLocationBadges
      turn={_turn(
        _execution({
          search_location: 경복궁,
          user_location: null,
          route_origin: { ...경복궁, source: "search_center" },
        }),
      )}
    />,
  );

  expect(screen.getByText("👤 사용자")).toBeInTheDocument();
  expect(screen.getByText("없음")).toBeInTheDocument();
  expect(screen.getByText("검색 위치 대체")).toBeInTheDocument();
});

it("사용자 위치를 발화로 말했으면 시작점이 그 위치임을 보여준다", () => {
  render(
    <TurnLocationBadges
      turn={_turn(
        _execution({ search_location: 경복궁, user_location: 안국역, route_origin: 안국역 }),
      )}
    />,
  );

  // 검색 위치와 시작점이 다른 상태가 정상이며, 대체 경고는 뜨지 않아야 한다.
  expect(screen.getAllByText("안국역")).toHaveLength(2);
  expect(screen.getByText("경복궁")).toBeInTheDocument();
  expect(screen.queryByText("검색 위치 대체")).not.toBeInTheDocument();
});

it("기기 GPS 좌표에는 근사 지역 이름을 붙이고 근사임을 표시한다", async () => {
  render(
    <TurnLocationBadges
      turn={_turn(
        _execution({ search_location: 경복궁, user_location: 기기GPS, route_origin: 기기GPS }),
      )}
    />,
  );

  // requested_query가 "gps_location" 자리표시자라 이름이 없다 — 상권 최근접으로 메운다.
  expect(await screen.findAllByText("≈ 종로·청계 관광특구")).toHaveLength(2);
  expect(screen.getAllByText("기기 GPS")).toHaveLength(2);
});

it("위치 정보가 없는 실행 단계면 아무것도 그리지 않는다", () => {
  // INFO/COMPARE는 C의 위치 해석을 거치지 않아 세 필드가 비어 있다.
  const { container } = render(
    <TurnLocationBadges turn={_turn(_execution({ operation: "info_concentration" }))} />,
  );

  expect(container).toBeEmptyDOMElement();
});
