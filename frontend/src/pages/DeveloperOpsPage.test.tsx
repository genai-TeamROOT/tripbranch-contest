/*
 * 역할: /dev-ops 운영 패널의 렌더링과 오류 처리를 검증한다.
 * 입력: mocked fetch 응답(/api/dev/api-usage, /api/dev/db-status).
 * 출력: 호출량 표·한도 게이지·fake 경고·DB 요약에 대한 assertion.
 * 호출 시점: vitest 실행 시 호출된다.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { DeveloperOpsPage } from "./DeveloperOpsPage";

const usageSnapshot = {
  process_started_at: "2026-08-09T09:00:00+09:00",
  generated_at: "2026-08-09T09:10:00+09:00",
  today: "2026-08-09",
  timezone: "Asia/Seoul",
  provider_modes: {
    llm: "real",
    place: "real",
    geocoding: "real",
    local_search: "real",
    weather: "real",
    concentration: "real",
    holiday: "real",
  },
  totals: { count: 517, ok: 515, error: 2 },
  today_totals: { count: 517, ok: 515, error: 2 },
  entries: [
    {
      provider: "tour_api",
      operation: "detailIntro2",
      count: 512,
      ok: 510,
      error: 2,
      today_count: 512,
      today_ok: 510,
      today_error: 2,
      daily_limit: 1000,
      avg_latency_ms: 220.5,
      max_latency_ms: 1800,
      last_called_at: "2026-08-09T09:09:00+09:00",
      last_status: "200",
    },
  ],
};

const categoryCoverage = {
  active_place_count: 844,
  large_category_count: 2,
  middle_category_count: 2,
  small_category_count: 2,
  groups: [
    {
      code: "FD",
      label: "음식",
      count: 500,
      middles: [
        {
          code: "FD05",
          label: "카페/ 찻집",
          count: 500,
          smalls: [
            {
              code: "FD050100",
              label: "카페",
              count: 42,
              examples: ["테라로사 서촌", "카페 레이어드"],
            },
          ],
        },
      ],
    },
    {
      code: "VE",
      label: "관람시설",
      count: 344,
      middles: [
        {
          code: "VE07",
          label: "박물관",
          count: 344,
          smalls: [
            {
              code: "VE070100",
              label: "미술관",
              count: 12,
              examples: ["국립현대미술관 서울"],
            },
          ],
        },
      ],
    },
  ],
};

const jongno = {
  area_code: "11",
  district_code: "110",
  district_name: "종로구",
  total: 883,
  active: 844,
  inactive: 39,
  detail_fetch_status: { succeeded: 710, failed: 142, empty: 31 },
  operating_parse_status: { parsed: 495, unknown: 388 },
  operating_parser_version: { "operating-hours-1.0.0": 883 },
  latest_detail_fetched_at: "2026-08-10T14:00:00+09:00",
  barrier_free_active: 164,
  barrier_free_total: 166,
  category_coverage: categoryCoverage,
};

const yongsan = {
  area_code: "11",
  district_code: "170",
  district_name: "용산구",
  total: 486,
  active: 486,
  inactive: 0,
  detail_fetch_status: { succeeded: 483, pending: 3 },
  operating_parse_status: { parsed: 282, unknown: 204 },
  operating_parser_version: { "operating-hours-1.0.0": 486 },
  latest_detail_fetched_at: "2026-08-21T13:00:00+09:00",
  barrier_free_active: 0,
  barrier_free_total: 0,
  category_coverage: { ...categoryCoverage, active_place_count: 486 },
};

const dbStatus = {
  overall: {
    total: 1369,
    active: 1330,
    inactive: 39,
    detail_fetch_status: { succeeded: 1193, failed: 142, empty: 31, pending: 3 },
    operating_parse_status: { parsed: 777, unknown: 592 },
    operating_parser_version: { "operating-hours-1.0.0": 1369 },
    latest_detail_fetched_at: "2026-08-21T13:00:00+09:00",
    barrier_free_active: 164,
    barrier_free_total: 166,
    category_coverage: { ...categoryCoverage, active_place_count: 1330 },
  },
  districts: [jongno, yongsan],
  place_enrichments_count: 51,
  place_concentration_mappings_count: 101,
  sync_runs: [
    {
      id: "run-1",
      area_code: "11",
      district_code: "110",
      status: "success",
      started_at: "2026-08-08T13:00:00+09:00",
      completed_at: "2026-08-08T14:00:00+09:00",
      processed_count: 844,
      new_count: 1,
      updated_count: 16,
      deactivated_count: 1,
      failed_count: 0,
      error_summary: null,
    },
  ],
  sync_locks: [],
  detail_ttl_days: 30,
};

const syncDistricts = {
  loaded: [
    {
      area_code: "11",
      district_code: "110",
      district_name: "종로구",
      place_count: 883,
      active_count: 844,
      latest_snapshot: "places_api_snapshot_11-110_20260810.csv",
      list_call_estimate: 1,
    },
    {
      area_code: "11",
      district_code: "170",
      district_name: "용산구",
      place_count: 486,
      active_count: 486,
      latest_snapshot: null,
      list_call_estimate: 1,
    },
  ],
  known: [
    { area_code: "11", district_code: "110", district_name: "종로구" },
    { area_code: "11", district_code: "140", district_name: "중구" },
    { area_code: "11", district_code: "170", district_name: "용산구" },
    { area_code: "11", district_code: "200", district_name: "성동구" },
  ],
};

const feedbackStats = {
  since: null,
  until: null,
  total: 5,
  rating_counts: { like: 2, dislike: 3 },
  reason_code_counts: {
    intent_mismatch: 0,
    clarification_unhelpful: 0,
    context_not_preserved: 1,
    location_misunderstood: 0,
    conditions_not_applied: 0,
    recommendation_not_suitable: 1,
    other: 0,
    unclassified: 1,
  },
  top_intents: [{ intent: "RECOMMEND", count: 4 }],
  other_intent_count: 0,
  missing_intent_count: 1,
};

const traceStats = {
  since: null,
  until: null,
  total: 6,
  step_stats: [
    {
      step: "llm_interpret",
      count: 3,
      avg_latency_ms: 150,
      max_latency_ms: 220,
      error_count: 1,
    },
    {
      step: "scoring",
      count: 3,
      avg_latency_ms: 80,
      max_latency_ms: 100,
      error_count: 0,
    },
  ],
  recent_errors: [
    {
      session_id: "sess_a",
      run_id: "run_1",
      step: "llm_interpret",
      error_type: "timeout",
      recorded_at: "2026-08-25T09:00:00+09:00",
    },
  ],
};

const snapshotRetention = {
  snapshots: ["places_api_snapshot_11-110_20260825.csv"],
  data_dir: "/repo/supabase/data",
  keep: 2,
  districts: [
    {
      area_code: "11",
      district_code: "110",
      district_name: "종로구",
      snapshot_count: 1,
      reconciliation_count: 1,
      latest_snapshot: "places_api_snapshot_11-110_20260825.csv",
      prunable_snapshots: [],
      prunable_reconciliations: [],
    },
  ],
};

const concentrationStatus = {
  districts: [
    {
      area_code: "11",
      district_code: "110",
      district_name: "종로구",
      concentration_code: "11110",
      active_places: 840,
      mapping_count: 101,
      latest_csv: "concentration_place_mapping_11110_20260808.csv",
      new_places_since_csv: 0,
    },
  ],
  rejection_count: 0,
};

/** 개발자 패널이 여는 조회 일곱 개를 한 곳에서 가른다. */
function panelBody(url: string) {
  if (url.includes("api-usage")) return usageSnapshot;
  if (url.includes("concentration/status")) return concentrationStatus;
  if (url.includes("place-sync/snapshots")) return snapshotRetention;
  if (url.includes("place-sync/districts")) return syncDistricts;
  if (url.includes("feedback/stats")) return feedbackStats;
  if (url.includes("trace/stats")) return traceStats;
  return dbStatus;
}

function mockFetch(handler: (url: string) => { status: number; body: unknown }) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const { status, body } = handler(String(input));
      return new Response(JSON.stringify(body), {
        status,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/dev-ops"]}>
      <DeveloperOpsPage />
    </MemoryRouter>,
  );
}

/** 갱신 탭을 연 채로 띄운다. 동기화 패널은 관찰 탭에 없다.
 *
 *  탭을 URL에 두므로 초기 진입만 바꾸면 되고, 메뉴를 클릭할 필요가 없다 —
 *  클릭으로 열면 테스트가 검증하려는 것과 무관한 단계가 앞에 붙는다. */
function renderSyncPage() {
  return render(
    <MemoryRouter initialEntries={["/dev-ops?tab=sync"]}>
      <DeveloperOpsPage />
    </MemoryRouter>,
  );
}

function renderCategoryPage() {
  return render(
    <MemoryRouter initialEntries={["/dev-ops?tab=categories"]}>
      <DeveloperOpsPage />
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

it("관찰 탭에서는 갱신 패널을 내주지 않는다", async () => {
  mockFetch((url) => ({ status: 200, body: panelBody(url) }));

  renderPage();

  expect(await screen.findByRole("heading", { name: "외부 API 호출량" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "장소 DB 상태" })).toBeInTheDocument();
  // 운영 DB와 파일을 바꾸는 패널은 관찰 탭에 없다.
  expect(screen.queryByRole("heading", { name: "DB 갱신" })).toBeNull();
  expect(screen.queryByRole("heading", { name: "전 구 갱신" })).toBeNull();
  expect(screen.queryByRole("heading", { name: "스냅샷 보관" })).toBeNull();
});

it("갱신 메뉴를 누르면 동기화 패널로 바뀐다", async () => {
  const user = userEvent.setup();
  mockFetch((url) => ({ status: 200, body: panelBody(url) }));

  renderPage();
  await user.click(await screen.findByRole("button", { name: /데이터 갱신/ }));

  expect(screen.getByRole("heading", { name: "DB 갱신" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "전 구 갱신" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "스냅샷 보관" })).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "외부 API 호출량" })).toBeNull();
});

it("구별 카테고리 현황에서 대·중·소분류와 예시 장소를 펼쳐 본다", async () => {
  const user = userEvent.setup();
  mockFetch((url) => ({ status: 200, body: panelBody(url) }));

  renderCategoryPage();

  expect(await screen.findByRole("heading", { name: "구별 카테고리 현황" })).toBeInTheDocument();
  expect(screen.getByText("활성 장소")).toBeInTheDocument();
  expect(screen.getByText("음식 · FD")).toBeInTheDocument();

  const largeDetails = screen.getByText("음식 · FD").closest("details");
  expect(largeDetails).toHaveAttribute("open");

  const middleSummary = screen.getByText("카페/ 찻집 · FD05");
  const middleDetails = middleSummary.closest("details");
  expect(middleDetails).not.toHaveAttribute("open");
  await user.click(middleSummary);
  expect(middleDetails).toHaveAttribute("open");
  expect(screen.getByText("카페 · FD050100")).toBeInTheDocument();
  expect(screen.getByText("테라로사 서촌 · 카페 레이어드")).toBeInTheDocument();

  await user.click(screen.getByRole("tab", { name: "종로구" }));
  expect(screen.getByText("844개")).toBeInTheDocument();
});

it("호출량 표와 일일 한도 게이지를 보여준다", async () => {
  mockFetch((url) => ({ status: 200, body: panelBody(url) }));

  renderPage();

  // detailIntro2는 호출량 표와 DB 갱신 패널의 한도 경고 문구 양쪽에 나온다.
  expect((await screen.findAllByText("detailIntro2")).length).toBeGreaterThan(0);
  expect(await screen.findByText("512 / 1000")).toBeInTheDocument();
  // 누적 호출과 오늘 호출 두 카드에 같은 값이 뜬다.
  expect(screen.getAllByText("517")).toHaveLength(2);
});

it("카카오맵 길찾기 provider 라벨을 표시한다", async () => {
  mockFetch((url) => ({
    status: 200,
    body: url.includes("api-usage")
      ? {
          ...usageSnapshot,
          entries: [
            {
              ...usageSnapshot.entries[0],
              provider: "kakao_map",
              operation: "walk",
              daily_limit: null,
            },
          ],
        }
      : panelBody(url),
  }));

  renderPage();

  expect(await screen.findByText("카카오맵 길찾기")).toBeInTheDocument();
  expect(screen.getByText("walk")).toBeInTheDocument();
});

it("fake provider가 있으면 표가 비는 이유를 경고로 알린다", async () => {
  mockFetch((url) => ({
    status: 200,
    body: url.includes("api-usage")
      ? {
          ...usageSnapshot,
          provider_modes: { ...usageSnapshot.provider_modes, place: "fake" },
          totals: { count: 0, ok: 0, error: 0 },
          today_totals: { count: 0, ok: 0, error: 0 },
          entries: [],
        }
      : panelBody(url),
  }));

  renderPage();

  // 빈 표를 "트래픽 없음"으로 오독하면 fake로 뜬 서버를 real로 착각한다(D-042).
  expect(await screen.findByText(/Fake Provider: 장소/)).toBeInTheDocument();
  expect(screen.getByText("아직 기록된 외부 호출이 없습니다.")).toBeInTheDocument();
});

it("전체 탭에서는 전 구 합계와 동기화 이력을 보여준다", async () => {
  mockFetch((url) => ({ status: 200, body: panelBody(url) }));

  renderPage();

  expect(await screen.findByText("1330")).toBeInTheDocument();
  expect(screen.getByText("전체 1369 · 비활성 39")).toBeInTheDocument();
  // 구 열이 없는 두 테이블은 전체 탭에만 나온다.
  expect(screen.getByText("place_enrichments")).toBeInTheDocument();
  expect(screen.getByText("집중률 매핑")).toBeInTheDocument();
  // 이력과 잠금은 탭 밖이라 전체 탭에서도 그대로 보인다.
  expect(screen.getByText("success")).toBeInTheDocument();
  expect(screen.getByText("places")).toBeInTheDocument();
  expect(screen.getByText("신규 1 · 기존 16 · 비활성 1")).toBeInTheDocument();
  expect(screen.getByText("place_sync_locks")).toBeInTheDocument();
  expect(screen.getByText("잠금 없음 — 실행 가능한 상태예요.")).toBeInTheDocument();
});

it("구 탭을 누르면 그 구의 요약만 보여준다", async () => {
  mockFetch((url) => ({ status: 200, body: panelBody(url) }));
  const user = userEvent.setup();
  renderPage();

  await user.click(await screen.findByRole("tab", { name: /종로구/ }));

  expect(screen.getByText("전체 883 · 비활성 39")).toBeInTheDocument();
  // 최근 상세조회 시각도 그 구 값이다 — 전체 합계(8월 21일)와 다른 날짜여야 한다.
  expect(screen.getByText(/2026\. 8\. 10\./)).toBeInTheDocument();
  // 구별로 나눌 수 없는 두 테이블은 구 탭에서 감춘다.
  expect(screen.queryByText("place_enrichments")).not.toBeInTheDocument();
  expect(screen.queryByText("집중률 매핑")).not.toBeInTheDocument();
  // 이력과 잠금은 탭과 무관하게 남는다.
  expect(screen.getByText("최근 동기화 이력")).toBeInTheDocument();
  expect(screen.getByText("동기화 잠금")).toBeInTheDocument();

  await user.click(screen.getByRole("tab", { name: /용산구/ }));
  expect(screen.getByText("전체 486 · 비활성 0")).toBeInTheDocument();

  await user.click(screen.getByRole("tab", { name: /전체/ }));
  expect(screen.getByText("전체 1369 · 비활성 39")).toBeInTheDocument();
  expect(screen.getByText("place_enrichments")).toBeInTheDocument();
});

/* 무장애 행 수는 구별로 나뉜다. place_barrier_free에 구 열이 없어 places와 묶어
 * 세는 값이라, 구를 바꿔도 안 바뀌면 전 구 합계를 보여주고 있다는 뜻이다. */
it("구별 무장애 행 수를 places 옆에 함께 보여준다", async () => {
  mockFetch((url) => ({ status: 200, body: panelBody(url) }));
  const user = userEvent.setup();
  renderPage();

  await user.click(await screen.findByRole("tab", { name: /종로구/ }));
  expect(screen.getByText("place_barrier_free 활성")).toBeInTheDocument();
  expect(screen.getByText("164")).toBeInTheDocument();
  expect(screen.getByText("전체 166")).toBeInTheDocument();

  // 아직 안 채운 구는 0으로 보인다 — 칸이 비면 "값이 없다"와 구분되지 않는다.
  await user.click(screen.getByRole("tab", { name: /용산구/ }));
  expect(screen.getByText("전체 0")).toBeInTheDocument();
});

/** 피드백 통계 패널만 스코프해서 찾는다 — 다른 패널도 "2"·"3" 같은 짧은
 * 숫자를 표시하므로(예: ApiUsagePanel의 실패 카운트) 전역 getByText는
 * 우연히 다른 패널의 숫자와 겹칠 수 있다.
 *
 * 머리말("피드백 통계")은 /api/feedback/stats 응답이 오기 전에도 그려지므로,
 * 머리말만 보고 반환하면 뒤따르는 동기 getByText가 "불러오는 중…"만 든 패널을
 * 훑다가 실패한다 — CI처럼 느린 환경에서 가끔 터진다(TP-158).
 *
 * 기다리는 대상은 새로고침 버튼 문구가 아니라 요약 카드의 "전체"다. 버튼은
 * loading이 false로 돌아오기만 하면 "새로고침"이 되므로 응답이 실패해
 * stats가 null인 상태와 구분되지 않는다. "전체"는 FeedbackStatsPanel이
 * stats를 받은 뒤에만 그리는 값이라 본문이 실제로 렌더됐음을 보장한다. */
async function findFeedbackStatsPanel() {
  const heading = await screen.findByText("피드백 통계");
  const panel = heading.closest("section");
  if (!panel) throw new Error("피드백 통계 패널을 찾지 못했습니다.");
  await within(panel).findByText("전체");
  return panel;
}

it("피드백 통계 요약 카드를 보여준다", async () => {
  mockFetch((url) => ({ status: 200, body: panelBody(url) }));

  renderPage();
  const panel = await findFeedbackStatsPanel();

  expect(within(panel).getByText("5")).toBeInTheDocument();
  expect(within(panel).getByText("2")).toBeInTheDocument();
  expect(within(panel).getByText("3")).toBeInTheDocument();
});

it("reason_code가 없는 dislike는 사유 미입력으로 잡힌다", async () => {
  mockFetch((url) => ({ status: 200, body: panelBody(url) }));

  renderPage();
  const panel = await findFeedbackStatsPanel();

  expect(within(panel).getByText("사유 미입력")).toBeInTheDocument();
  // context_not_preserved 1건 + recommendation_not_suitable 1건 + unclassified 1건 = dislike 3건.
  expect(within(panel).getByText("맥락 유지 실패")).toBeInTheDocument();
  expect(within(panel).getByText("추천 부적절")).toBeInTheDocument();
});

it("intent 상위 목록과 intent 없는 건수를 따로 보여준다", async () => {
  mockFetch((url) => ({ status: 200, body: panelBody(url) }));

  renderPage();
  const panel = await findFeedbackStatsPanel();

  expect(within(panel).getByText("RECOMMEND")).toBeInTheDocument();
  expect(
    within(panel).getByText(/intent가 기록되지 않은 피드백 1건은 위 집계에서/),
  ).toBeInTheDocument();
});

it("피드백이 하나도 없으면 빈 상태 문구를 보여준다", async () => {
  mockFetch((url) => ({
    status: 200,
    body: url.includes("feedback/stats")
      ? {
          since: null,
          until: null,
          total: 0,
          rating_counts: { like: 0, dislike: 0 },
          reason_code_counts: {
            intent_mismatch: 0,
            clarification_unhelpful: 0,
            context_not_preserved: 0,
            location_misunderstood: 0,
            conditions_not_applied: 0,
            recommendation_not_suitable: 0,
            other: 0,
            unclassified: 0,
          },
          top_intents: [],
          other_intent_count: 0,
          missing_intent_count: 0,
        }
      : panelBody(url),
  }));

  renderPage();

  expect(await screen.findByText("아직 기록된 피드백이 없습니다.")).toBeInTheDocument();
});

/** Trace 통계 패널만 스코프해서 찾는다 — findFeedbackStatsPanel과 같은 이유다.
 * 응답을 기다리는 것도 같다: 요약 카드의 "전체 실행"이 나타나야 stats가 들어온
 * 상태다. */
async function findTracePanel() {
  const heading = await screen.findByText("Trace 통계");
  const panel = heading.closest("section");
  if (!panel) throw new Error("Trace 통계 패널을 찾지 못했습니다.");
  await within(panel).findByText("전체 실행");
  return panel;
}

it("Trace 통계 요약 카드를 보여준다", async () => {
  mockFetch((url) => ({ status: 200, body: panelBody(url) }));

  renderPage();
  const panel = await findTracePanel();

  expect(within(panel).getByText("6")).toBeInTheDocument();
  expect(within(panel).getByText("2")).toBeInTheDocument();
});

it("step별 건수·평균/최대 latency·에러 건수를 보여준다", async () => {
  mockFetch((url) => ({ status: 200, body: panelBody(url) }));

  renderPage();
  const panel = await findTracePanel();
  // "llm_interpret"은 step별 집계 표와 최근 에러 목록 양쪽에 나온다 —
  // 표(table) 안으로 좁혀서 찾는다.
  const stepTable = within(panel).getByText("step별 집계").closest("div");
  if (!stepTable) throw new Error("step별 집계 표를 찾지 못했습니다.");

  expect(within(stepTable).getByText("llm_interpret")).toBeInTheDocument();
  expect(within(stepTable).getByText("scoring")).toBeInTheDocument();
  expect(within(stepTable).getByText("150ms")).toBeInTheDocument();
  expect(within(stepTable).getByText("220ms")).toBeInTheDocument();
});

it("최근 에러 목록을 보여준다", async () => {
  mockFetch((url) => ({ status: 200, body: panelBody(url) }));

  renderPage();
  const panel = await findTracePanel();

  expect(within(panel).getByText("timeout")).toBeInTheDocument();
});

it("trace가 하나도 없으면 빈 상태 문구를 보여준다", async () => {
  mockFetch((url) => ({
    status: 200,
    body: url.includes("trace/stats")
      ? { since: null, until: null, total: 0, step_stats: [], recent_errors: [] }
      : panelBody(url),
  }));

  renderPage();

  expect(await screen.findByText("아직 기록된 trace가 없습니다.")).toBeInTheDocument();
});

it("상세조회 TTL은 구별 값이 아니라 머리말에만 쓴다", async () => {
  mockFetch((url) => ({ status: 200, body: panelBody(url) }));

  renderPage();

  expect(await screen.findByText("상세조회 TTL 30일")).toBeInTheDocument();
});

it("라우터가 없는 서버(404)에는 APP_ENV 확인을 안내한다", async () => {
  mockFetch(() => ({
    status: 404,
    body: {
      error: {
        code: "invalid_request",
        message: "요청한 API를 찾을 수 없어요.",
        retryable: false,
        details: null,
      },
    },
  }));

  renderPage();

  await waitFor(() => {
    expect(screen.getAllByText(/APP_ENV=local/).length).toBeGreaterThan(0);
  });
});

const reconcileResult = {
  area_code: "11",
  district_code: "110",
  snapshot: "places_api_snapshot_11-110_20260809.csv",
  snapshot_count: 844,
  baseline: "places_api_snapshot_11-110_20260808.csv",
  baseline_source: "file" as const,
  baseline_count: 844,
  reconciliation: "places_reconciliation_20260809.csv",
  skipped_columns: [],
  counts: { added: 1, removed: 1, updated: 2 },
  detail_content_ids: ["3", "4"],
  detail_excluded_ids: ["1"],
  detail_backfill_ids: [] as string[],
  detail_backfill_checked: true,
  barrier_free_detail_count: 164,
  barrier_free_checked: true,
  rows: [
    {
      content_id: "4",
      title: "새 장소",
      content_type_id: "12",
      change_type: "added" as const,
      changed_columns: [],
      previous: {},
      current: {},
    },
    {
      content_id: "1",
      title: "좌표만 바뀐 장소",
      content_type_id: "12",
      change_type: "updated" as const,
      changed_columns: ["latitude", "longitude"],
      previous: {},
      current: {},
    },
  ],
};

const runningJob = {
  job_id: "job-1",
  params: {
    area_code: "11",
    district_code: "110",
    snapshot: "places_api_snapshot_11-110_20260809.csv",
    dry_run: true,
    detail_target_count: 2,
    added_count: 1,
    details_limit: null,
  },
  status: "running",
  started_at: "2026-08-09T19:00:00+09:00",
  finished_at: null,
  phase: "details",
  processed: 1,
  total: 2,
  result: null,
  error: null,
  unmapped_new_place_ids: [],
};

function mockSyncFetch() {
  const posted: { url: string; body: unknown }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (init?.method === "POST") {
        posted.push({ url, body: JSON.parse(String(init.body)) });
      }
      const body = url.includes("reconcile")
        ? reconcileResult
        : url.includes("place-sync/apply") || url.includes("place-sync/jobs")
          ? runningJob
          : panelBody(url);
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
  return posted;
}

it("대조 결과와 상세조회 대상 건수를 보여준다", async () => {
  mockSyncFetch();
  const user = userEvent.setup();
  renderSyncPage();

  await user.click(await screen.findByRole("button", { name: "1. 스냅샷 대조" }));

  expect(await screen.findByText("새 장소")).toBeInTheDocument();
  expect(screen.getByText(/예상 외부 호출: 목록 0회 \+ 상세조회 2회/)).toBeInTheDocument();
  // 수정시각이 안 바뀐 건은 상세조회에서 빠지되 조용히 사라지지 않아야 한다.
  expect(screen.getByText(/상세조회 제외 1건/)).toBeInTheDocument();
});

it("확인 문자열을 정확히 입력해야 반영이 시작된다", async () => {
  const posted = mockSyncFetch();
  const user = userEvent.setup();
  renderSyncPage();

  await user.click(await screen.findByRole("button", { name: "1. 스냅샷 대조" }));
  await user.click(await screen.findByRole("button", { name: "2. 반영 실행" }));

  const execute = screen.getByRole("button", { name: "실행" });
  expect(execute).toBeDisabled();

  await user.type(screen.getByLabelText("확인 문자열"), "11-999");
  expect(execute).toBeDisabled();

  await user.clear(screen.getByLabelText("확인 문자열"));
  await user.type(screen.getByLabelText("확인 문자열"), "11-110");
  await user.click(execute);

  const applyCall = posted.find((call) => call.url.includes("place-sync/apply"));
  expect(applyCall?.body).toEqual({
    // 구를 빠뜨리면 서버가 설정 기본값으로 실행해, 대상 구의 활성 장소가 전부
    // 비활성화된다. 대조 결과가 정한 구를 그대로 싣는다.
    area_code: "11",
    district_code: "110",
    snapshot: "places_api_snapshot_11-110_20260809.csv",
    detail_content_ids: ["3", "4"],
    // 신규 장소는 반영 후 집중률 매핑 유무를 확인하는 데 쓰인다.
    added_content_ids: ["4"],
    // 패널은 항상 실제 반영이다. dry-run은 한도를 똑같이 쓰면서 결과를 남기지
    // 않아, 모르고 켜두면 "돌렸는데 아무것도 안 바뀜"이 된다.
    dry_run: false,
    details_limit: null,
    confirm: "11-110",
  });
});

it("제외된 건을 포함하도록 체크하면 상세조회 대상에 들어간다", async () => {
  const posted = mockSyncFetch();
  const user = userEvent.setup();
  renderSyncPage();

  await user.click(await screen.findByRole("button", { name: "1. 스냅샷 대조" }));
  await user.click(await screen.findByRole("checkbox", { name: /상세조회 제외 1건/ }));
  expect(screen.getByText(/예상 외부 호출: 목록 0회 \+ 상세조회 3회/)).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "2. 반영 실행" }));
  await user.type(screen.getByLabelText("확인 문자열"), "11-110");
  await user.click(screen.getByRole("button", { name: "실행" }));

  const applyCall = posted.find((call) => call.url.includes("place-sync/apply"));
  expect(applyCall?.body).toMatchObject({ detail_content_ids: ["3", "4", "1"] });
});

it("드롭다운에서 고른 구로 대조를 건다", async () => {
  const posted = mockSyncFetch();
  const user = userEvent.setup();
  renderSyncPage();

  await user.selectOptions(await screen.findByLabelText("대상 구"), "11-170");
  await user.click(screen.getByRole("button", { name: "1. 스냅샷 대조" }));

  const call = posted.find((posted) => posted.url.includes("reconcile"));
  expect(call?.body).toEqual({
    area_code: "11",
    district_code: "170",
    baseline: null,
    // 구 단위 패널은 늘 목록을 새로 받는다. 저장된 스냅샷 재사용은 전 구 순회에만
    // 있다 — 구 하나를 다시 보려는 자리에서 어제 목록을 읽으면 의도와 어긋난다.
    source: "api",
  });
});

it("구를 바꾸면 앞 구의 대조 결과를 지운다", async () => {
  mockSyncFetch();
  const user = userEvent.setup();
  renderSyncPage();

  await user.click(await screen.findByRole("button", { name: "1. 스냅샷 대조" }));
  expect(await screen.findByText("새 장소")).toBeInTheDocument();

  await user.selectOptions(screen.getByLabelText("대상 구"), "11-170");

  // 남겨두면 종로구를 대조한 화면에서 용산구를 반영하는 조작이 가능해 보인다.
  expect(screen.queryByText("새 장소")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "2. 반영 실행" })).not.toBeInTheDocument();
});

it("사전에 없는 구 코드는 추가되지 않는다", async () => {
  mockSyncFetch();
  const user = userEvent.setup();
  renderSyncPage();

  await user.click(await screen.findByRole("button", { name: "구 추가" }));
  await user.type(screen.getByLabelText("추가할 구 코드"), "999");
  await user.click(screen.getByRole("button", { name: "추가 완료" }));

  // 없는 코드로 동기화를 걸면 TourAPI가 빈 목록을 주고, 그 결과는 "장소가 0건인
  // 구"와 구분되지 않는다.
  expect(screen.getByText(/시군구 사전에 없는 코드예요: 999/)).toBeInTheDocument();
});

it("추가한 구가 드롭다운에 들어가고 바로 선택된다", async () => {
  mockSyncFetch();
  const user = userEvent.setup();
  renderSyncPage();

  await user.click(await screen.findByRole("button", { name: "구 추가" }));
  await user.type(screen.getByLabelText("추가할 구 코드"), "140");
  await user.click(screen.getByRole("button", { name: "추가 완료" }));

  const select = screen.getByLabelText("대상 구") as HTMLSelectElement;
  expect(select.value).toBe("11-140");
  expect(screen.getByRole("option", { name: /중구 11-140 · 자료 없음/ })).toBeInTheDocument();
  // 자료가 없는 구는 전량이 신규로 잡힌다는 것을 미리 알린다.
  expect(screen.getByText(/자료가 없는 구예요/)).toBeInTheDocument();
});

it("상세조회 상한이 예상 호출수와 반영 요청에 반영된다", async () => {
  const posted = mockSyncFetch();
  const user = userEvent.setup();
  renderSyncPage();

  await user.click(await screen.findByRole("button", { name: "1. 스냅샷 대조" }));
  await user.type(await screen.findByLabelText("상세조회 상한"), "1");

  expect(screen.getByText(/예상 외부 호출: 목록 0회 \+ 상세조회 1회/)).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "2. 반영 실행" }));
  // 상한이 걸린 실행은 비활성화를 건너뛴다는 사실을 실행 전에 알려야 한다.
  expect(screen.getByText(/비활성화를 건너뜁니다/)).toBeInTheDocument();
  await user.type(screen.getByLabelText("확인 문자열"), "11-110");
  await user.click(screen.getByRole("button", { name: "실행" }));

  const applyCall = posted.find((call) => call.url.includes("place-sync/apply"));
  expect((applyCall?.body as { details_limit: number }).details_limit).toBe(1);
});

it("DB로 기준을 만든 대조는 그 사실을 알린다", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const body = url.includes("reconcile")
        ? { ...reconcileResult, baseline: "places@2026-08-21", baseline_source: "database" }
        : panelBody(url);
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
  const user = userEvent.setup();
  renderSyncPage();

  await user.click(await screen.findByRole("button", { name: "1. 스냅샷 대조" }));

  // 파일로 남지 않는 기준이라는 걸 모르면, 다음 대조에서 왜 기준이 바뀌었는지
  // 알 수 없다.
  expect(await screen.findByText(/places 테이블로 기준을 만들었어요/)).toBeInTheDocument();
});

it("지난 실행에서 못 채운 건까지 예상 호출수에 넣는다", async () => {
  const posted: { url: string; body: unknown }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (init?.method === "POST") posted.push({ url, body: JSON.parse(String(init.body)) });
      const body = url.includes("reconcile")
        ? { ...reconcileResult, detail_backfill_ids: ["7", "8", "9"] }
        : url.includes("place-sync/apply") || url.includes("place-sync/jobs")
          ? runningJob
          : panelBody(url);
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
  const user = userEvent.setup();
  renderSyncPage();

  await user.click(await screen.findByRole("button", { name: "1. 스냅샷 대조" }));

  // 변경분 2 + 못 채운 3. 변경분만 세면 화면이 실제보다 훨씬 적은 수를 보여준다.
  expect(
    await screen.findByText(/상세조회 5회 \(이번 변경분 2 \+ 지난 실행에서 못 채운 3\)/),
  ).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "2. 반영 실행" }));
  await user.type(screen.getByLabelText("확인 문자열"), "11-110");
  await user.click(screen.getByRole("button", { name: "실행" }));

  const applyCall = posted.find((call) => call.url.includes("place-sync/apply"));
  expect((applyCall?.body as { detail_content_ids: string[] }).detail_content_ids).toEqual([
    "3",
    "4",
    "7",
    "8",
    "9",
  ]);
});

it("못 채운 건을 확인하지 못하면 예상 호출수가 확정이 아님을 알린다", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const body = url.includes("reconcile")
        ? { ...reconcileResult, detail_backfill_checked: false }
        : panelBody(url);
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
  const user = userEvent.setup();
  renderSyncPage();

  await user.click(await screen.findByRole("button", { name: "1. 스냅샷 대조" }));

  expect(
    await screen.findByText(/상세를 못 채운 장소가 DB에 얼마나 있는지 확인하지 못했어요/),
  ).toBeInTheDocument();
});

/* 무장애 목록을 못 부르면 예상 호출수가 0으로 나온다. 0건과 "못 봤다"를 뭉개면
 * 화면이 0회를 확정된 값처럼 보여준다. */
it("무장애 목록을 확인하지 못하면 0회가 확정이 아님을 알린다", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const body = url.includes("reconcile")
        ? { ...reconcileResult, barrier_free_detail_count: 0, barrier_free_checked: false }
        : panelBody(url);
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
  const user = userEvent.setup();
  renderSyncPage();

  await user.click(await screen.findByRole("button", { name: "1. 스냅샷 대조" }));

  expect(await screen.findByText(/무장애 목록을 확인하지 못했어요/)).toBeInTheDocument();
});

/* 예상 호출수는 상한이 아니라 실제 수다. 대조가 무장애 목록을 1회 불러 교집합을
 * 내기 때문에, 여기에 "아직 확인 안 한 장소 수"가 그대로 뜨면 종로구 기준
 * 4.6배(755 vs 164) 부풀려진 값을 한도 옆에 보여주게 된다. */
it("무장애 예상 호출수를 목록·상세로 나눠 보여준다", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const body = url.includes("reconcile") ? reconcileResult : panelBody(url);
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
  const user = userEvent.setup();
  renderSyncPage();

  await user.click(await screen.findByRole("button", { name: "1. 스냅샷 대조" }));

  expect(await screen.findByText(/무장애 목록 1회 \+ 무장애 상세 164회/)).toBeInTheDocument();
});

/* 패널에는 dry-run 선택지가 없지만 apply 엔드포인트는 여전히 받는다. 그렇게 돈
 * job이 화면에 오면 숫자가 실제 반영과 똑같이 보이므로, 결과 표기는 남겨둔다. */
it("dry-run으로 돈 job은 DB에 쓰지 않았다는 것과 한도를 썼다는 것을 알린다", async () => {
  const finishedDryRun = {
    ...runningJob,
    status: "success",
    finished_at: "2026-08-22T00:05:00+09:00",
    phase: "done",
    processed: 2,
    total: 2,
    result: {
      status: "success",
      dry_run: true,
      sync_run_id: null,
      processed_count: 486,
      success_count: 486,
      failed_count: 0,
      new_count: 486,
      updated_count: 0,
      deactivated_count: 0,
      detail_target_count: 142,
      detail_attempted_count: 142,
      reparse_count: 0,
      error_summary: {},
    },
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const body = url.includes("reconcile")
        ? reconcileResult
        : url.includes("place-sync/apply") || url.includes("place-sync/jobs")
          ? finishedDryRun
          : panelBody(url);
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
  const user = userEvent.setup();
  renderSyncPage();

  await user.click(await screen.findByRole("button", { name: "1. 스냅샷 대조" }));
  await user.click(screen.getByRole("button", { name: "2. 반영 실행" }));
  await user.type(screen.getByLabelText("확인 문자열"), "11-110");
  await user.click(screen.getByRole("button", { name: "실행" }));

  // 숫자만 크게 띄우면 하지도 않은 일을 한 것처럼 보인다.
  expect(await screen.findByText(/dry-run이라 DB에는 아무것도 쓰지 않았어요/)).toBeInTheDocument();
  // "갱신"은 값이 바뀐 수가 아니라 DB에 이미 있던 수라 "기존"으로 쓴다.
  // (동기화 이력 표에도 같은 이름의 열이 있어 여러 개가 잡힌다.)
  expect(screen.getAllByText("기존").length).toBeGreaterThan(0);
  // 비활성화는 판정 자체를 건너뛴다 — 0으로 보이면 "사라진 장소가 없다"로 읽힌다.
  expect(screen.getByText("미판정")).toBeInTheDocument();
});

it("대조가 쓰는 목록 API 호출 수를 누르기 전에 알린다", async () => {
  mockFetch((url) => ({ status: 200, body: panelBody(url) }));

  renderSyncPage();

  // areaBasedList2도 일일 한도가 있다. 한 번에 1회라도 구를 바꿔가며 누르면 쌓인다.
  expect(await screen.findByText(/대조는 목록 API를 1회 써요/)).toBeInTheDocument();
  // 반영이 "목록 0회"인 것과 헷갈리지 않게 이유를 함께 적는다.
  expect(screen.getByText(/반영은 이 스냅샷을 다시 쓰므로/)).toBeInTheDocument();
});

/*
 * 잠금 해제 — 서버가 강제 종료되면 잠금이 DB에 남아 최대 2시간 동안 그 구의
 * 동기화가 막힌다. 실행이 이미 끝났으면 바로 풀고, 아직 running이면 한 번 더
 * 확인받는다(다른 곳에서 도는 동기화를 끊을 수 있어서다).
 */
test("끝난 실행이 남긴 잠금은 확인 없이 바로 푼다", async () => {
  const posted: { url: string; body: Record<string, unknown> }[] = [];
  const withLock = {
    ...dbStatus,
    sync_locks: [
      {
        area_code: "11",
        district_code: "740",
        sync_run_id: "11111111-1111-4111-8111-111111111111",
        acquired_at: "2026-08-29T00:59:00+09:00",
        expires_at: "2026-08-29T02:59:00+09:00",
        run_status: "failed",
        run_started_at: "2026-08-29T00:59:00+09:00",
        run_processed_count: 111,
      },
    ],
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (init?.method === "POST") {
        posted.push({ url, body: JSON.parse(String(init.body)) });
        return new Response(JSON.stringify({ released: true, run_abandoned: false }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      const body = url.includes("db-status") ? withLock : panelBody(url);
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
  const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);

  renderPage();
  const button = await screen.findByRole("button", { name: "잠금 해제" });
  await userEvent.click(button);

  await waitFor(() => expect(posted).toHaveLength(1));
  expect(posted[0].url).toContain("/dev/place-sync/locks/release");
  // 소유자를 함께 보낸다 — 구만 보고 지우면 그 사이 새로 잡은 잠금을 뺏는다.
  expect(posted[0].body).toMatchObject({
    area_code: "11",
    district_code: "740",
    sync_run_id: "11111111-1111-4111-8111-111111111111",
    force: false,
  });
  // 실행이 이미 끝났으므로 되묻지 않는다.
  expect(confirmSpy).not.toHaveBeenCalled();
  await screen.findByText("잠금을 풀었어요.");
  confirmSpy.mockRestore();
});

test("아직 running인 실행의 잠금은 확인을 받고 force로 푼다", async () => {
  const posted: { url: string; body: Record<string, unknown> }[] = [];
  const withRunningLock = {
    ...dbStatus,
    sync_locks: [
      {
        area_code: "11",
        district_code: "740",
        sync_run_id: "22222222-2222-4222-8222-222222222222",
        acquired_at: "2026-08-29T00:59:00+09:00",
        expires_at: "2026-08-29T02:59:00+09:00",
        run_status: "running",
        run_started_at: "2026-08-29T00:59:00+09:00",
        run_processed_count: 111,
      },
    ],
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (init?.method === "POST") {
        posted.push({ url, body: JSON.parse(String(init.body)) });
        return new Response(JSON.stringify({ released: true, run_abandoned: true }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      const body = url.includes("db-status") ? withRunningLock : panelBody(url);
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
  const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);

  renderPage();
  // running이면 버튼 문구부터 다르다 — 무엇을 하는지 눌러보기 전에 보여야 한다.
  const button = await screen.findByRole("button", { name: "강제 해제" });
  await userEvent.click(button);

  await waitFor(() => expect(posted).toHaveLength(1));
  expect(confirmSpy).toHaveBeenCalled();
  expect(posted[0].body).toMatchObject({ force: true });
  await screen.findByText("잠금을 풀고 진행 중이던 실행을 실패로 마감했어요.");
  confirmSpy.mockRestore();
});

test("running 잠금 해제를 취소하면 요청을 보내지 않는다", async () => {
  const posted: string[] = [];
  const withRunningLock = {
    ...dbStatus,
    sync_locks: [
      {
        area_code: "11",
        district_code: "740",
        sync_run_id: "33333333-3333-4333-8333-333333333333",
        acquired_at: "2026-08-29T00:59:00+09:00",
        expires_at: "2026-08-29T02:59:00+09:00",
        run_status: "running",
        run_started_at: "2026-08-29T00:59:00+09:00",
      },
    ],
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (init?.method === "POST") posted.push(url);
      const body = url.includes("db-status") ? withRunningLock : panelBody(url);
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
  const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);

  renderPage();
  await userEvent.click(await screen.findByRole("button", { name: "강제 해제" }));

  expect(confirmSpy).toHaveBeenCalled();
  expect(posted).toHaveLength(0);
  confirmSpy.mockRestore();
});
