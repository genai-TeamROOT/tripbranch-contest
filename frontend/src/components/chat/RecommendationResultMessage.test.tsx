import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, vi } from "vitest";

import { fetchFeatures } from "../../api/features";
import { FeatureFlagsProvider } from "../../state/FeatureFlagsContext";
import { TripProvider } from "../../state/TripContext";
import type { RecommendationItem } from "../../types";
import { RecommendationResultMessage } from "./RecommendationResultMessage";

function item(overrides: Partial<RecommendationItem> = {}): RecommendationItem {
  return {
    place_id: "place-1",
    name: "아키비스트 서촌",
    category: "restaurant",
    distance_km: 0.54,
    remaining_minutes: null,
    operating_hours_display: "11:00~21:00",
    environment_type: "indoor",
    recommendation_reason: "테스트 추천이에요.",
    explanations: [],
    warnings: ["지금은 운영시간이 아니에요."],
    score: 0.9,
    feature_scores: {},
    weights_used: {},
    taste_evidence: [],
    ...overrides,
  };
}

/* 캡션 테스트만 실제 FeatureFlagsProvider를 쓴다. 서버 응답(GET /api/features)만
   바꿔 끼운다 — 나머지 테스트는 Provider 없이 렌더해 캡션과 무관하다. */
vi.mock("../../api/features", () => ({ fetchFeatures: vi.fn() }));

function withFlags({ children }: { children: ReactNode }) {
  return (
    <FeatureFlagsProvider>
      <TripProvider>{children}</TripProvider>
    </FeatureFlagsProvider>
  );
}

function renderCaption(language: "ko" | "en") {
  render(
    <RecommendationResultMessage
      recommendations={[item({ remaining_minutes: 120, warnings: [] })]}
      unverifiedRecommendations={[]}
      elapsedMs={0}
      serverElapsedMs={0}
      language={language}
    />,
    { wrapper: withFlags },
  );
}

it("취향이 켜진 서버에서는 캡션이 취향을 말한다", async () => {
  vi.mocked(fetchFeatures).mockResolvedValue({ taste_enabled: true });
  renderCaption("ko");

  expect(await screen.findByText("거리·날씨·취향 등을 고려했어요")).toBeInTheDocument();
});

it("취향이 켜진 서버에서는 영어 캡션도 취향을 말한다", async () => {
  vi.mocked(fetchFeatures).mockResolvedValue({ taste_enabled: true });
  renderCaption("en");

  expect(
    await screen.findByText("Ranked by distance, weather, your preferences, and more"),
  ).toBeInTheDocument();
});

it("취향이 꺼진 서버에서는 캡션에서 취향을 뺀다", async () => {
  /* 순위에 취향 축이 아예 없는데 "취향을 고려했다"고 쓰면 사실과 다르다. */
  vi.mocked(fetchFeatures).mockResolvedValue({ taste_enabled: false });
  renderCaption("ko");

  await waitFor(() => expect(fetchFeatures).toHaveBeenCalled());
  expect(screen.getByText("거리·날씨 등을 고려했어요")).toBeInTheDocument();
  expect(screen.queryByText("거리·날씨·취향 등을 고려했어요")).not.toBeInTheDocument();
});

it("취향이 꺼진 서버에서는 영어 캡션에서도 취향을 뺀다", async () => {
  vi.mocked(fetchFeatures).mockResolvedValue({ taste_enabled: false });
  renderCaption("en");

  await waitFor(() => expect(fetchFeatures).toHaveBeenCalled());
  expect(screen.getByText("Ranked by distance, weather, and more")).toBeInTheDocument();
});

it("기능 조회가 실패하면 취향이 꺼진 캡션을 쓴다", async () => {
  vi.mocked(fetchFeatures).mockRejectedValue(new Error("features down"));
  renderCaption("ko");

  await waitFor(() => expect(fetchFeatures).toHaveBeenCalled());
  expect(screen.getByText("거리·날씨 등을 고려했어요")).toBeInTheDocument();
});

function renderResult(unverifiedRecommendations: RecommendationItem[]) {
  render(
    <RecommendationResultMessage
      recommendations={[]}
      unverifiedRecommendations={unverifiedRecommendations}
      elapsedMs={0}
      serverElapsedMs={0}
    />,
    { wrapper: TripProvider },
  );
}

it("폐점 후보도 추천 장소 줄에 들어가고 운영시간 구간은 그대로 보인다", () => {
  /* 2026-09-08에 별도 섹션을 걷었다. 캡션이 하던 말은 카드가 한다 — 운영시간
     구간과 경고 문구가 남아 있으므로 정보가 사라지지 않는다. */
  renderResult([item()]);

  expect(screen.getByText("추천 장소")).toBeInTheDocument();
  expect(screen.queryByText("현재 운영시간이 아닌 장소")).not.toBeInTheDocument();
  expect(screen.getByText("11:00~21:00 (현재 운영시간 아님)")).toBeInTheDocument();
  expect(screen.getByText("지금은 운영시간이 아니에요.")).toBeInTheDocument();
});

it("폐점과 확인 불가가 섞여도 줄은 하나고 순위가 이어진다", () => {
  render(
    <RecommendationResultMessage
      recommendations={[item({ place_id: "v1", name: "경복궁", remaining_minutes: 120 })]}
      unverifiedRecommendations={[
        item({ place_id: "c1", name: "심야 갤러리", operating_hours_display: "19:00~23:00" }),
        item({ place_id: "u1", name: "동네 서점", operating_hours_display: null }),
      ]}
      elapsedMs={0}
      serverElapsedMs={0}
    />,
    { wrapper: TripProvider },
  );

  expect(screen.getAllByText("추천 장소")).toHaveLength(1);
  const row = screen.getByText("추천 장소").closest("section") as HTMLElement;
  expect(within(row).getByText("1위")).toBeInTheDocument();
  expect(within(row).getByText("2위")).toBeInTheDocument();
  expect(within(row).getByText("3위")).toBeInTheDocument();
  expect(within(row).getByText("심야 갤러리")).toBeInTheDocument();
  expect(within(row).getByText("동네 서점")).toBeInTheDocument();
  expect(screen.queryByText("현재 운영시간이 아닌 장소")).not.toBeInTheDocument();
  expect(screen.queryByText("운영시간을 확인할 수 없는 장소")).not.toBeInTheDocument();
});

it("운영시간 원문도 없는 후보는 추천 장소 줄에 함께 들어간다", () => {
  /* 2026-09-08에 바꿨다. 전에는 "운영시간을 확인할 수 없는 장소" 캡션으로 줄을
     하나 더 그렸는데, 그 사실은 카드가 "확인 불가"로 이미 말한다. */
  renderResult([item({ operating_hours_display: null })]);

  expect(screen.getByText("추천 장소")).toBeInTheDocument();
  expect(screen.queryByText("운영시간을 확인할 수 없는 장소")).not.toBeInTheDocument();
  expect(screen.getByText("확인 불가")).toBeInTheDocument();
  expect(screen.queryByText("현재 운영시간이 아닌 장소")).not.toBeInTheDocument();
  /* 순위 번호가 이어 붙는다 — 검증된 후보가 없으면 이 후보가 1위 자리에 온다. */
  expect(screen.getByText("1위")).toBeInTheDocument();
});

it("검증된 후보 뒤로 순위가 이어지고 줄은 하나다", () => {
  render(
    <RecommendationResultMessage
      recommendations={[item({ place_id: "verified-1", name: "경복궁", remaining_minutes: 120 })]}
      unverifiedRecommendations={[
        item({ place_id: "unknown-1", name: "동네 서점", operating_hours_display: null }),
      ]}
      elapsedMs={0}
      serverElapsedMs={0}
    />,
    { wrapper: TripProvider },
  );

  const row = screen.getByText("추천 장소").closest("section") as HTMLElement;
  expect(within(row).getByText("1위")).toBeInTheDocument();
  expect(within(row).getByText("2위")).toBeInTheDocument();
  expect(within(row).getByText("동네 서점")).toBeInTheDocument();
  expect(screen.queryByText("운영시간을 확인할 수 없는 장소")).not.toBeInTheDocument();
});

it("추천 카드를 클릭하면 C PlaceDetails가 채워진 상세 창을 연다", async () => {
  const user = userEvent.setup();
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({
      status: "success",
      requested_place_id: "place-1",
      place_card: {
        question_type: "general_info",
        answer_fields: { homepage: "https://example.test/archivist" },
        place_id: "place-1",
        place_name: "아키비스트 서촌",
        thumbnail_url: "https://example.test/archivist.jpg",
        overview: "서촌의 카페입니다.",
        operating_hours: "11:00~21:00",
        rest_date: "매주 화요일",
        parking: null,
        parking_fee: null,
        fee: null,
        baby_carriage: null,
        pet: null,
        credit_card: "가능",
        restroom: null,
        homepage: "https://example.test/archivist",
      },
    }),
  });
  vi.stubGlobal("fetch", fetchMock);
  window.fetch = fetchMock;
  render(
    <RecommendationResultMessage
      recommendations={[item()]}
      unverifiedRecommendations={[]}
      elapsedMs={0}
      serverElapsedMs={0}
    />,
    { wrapper: TripProvider },
  );

  await user.click(screen.getByRole("button", { name: "아키비스트 서촌 장소 정보 미리 보기" }));

  const dialog = screen.getByRole("dialog", { name: "아키비스트 서촌" });
  expect(dialog).toBeInTheDocument();
  await waitFor(() =>
    expect(fetchMock).toHaveBeenCalledWith("/api/chat/place-details", expect.anything()),
  );
  expect(
    await within(dialog).findByRole("img", { name: "아키비스트 서촌 이미지" }),
  ).toBeInTheDocument();
  expect(within(dialog).getByText("서촌의 카페입니다.")).toBeInTheDocument();
  expect(within(dialog).getByText("매주 화요일")).toBeInTheDocument();
  expect(within(dialog).getByText("11:00~21:00")).toBeInTheDocument();
  /* 영업 상태는 장소명 옆이다(TP-248). */
  expect(within(dialog).getByText("운영 종료")).toBeInTheDocument();
  // 홈페이지는 "관련 정보" 박스 안에서 클릭 가능한 링크로만 노출된다(하단 중복 링크 제거).
  expect(within(dialog).getByRole("link", { name: /example\.test/ })).toHaveAttribute(
    "href",
    "https://example.test/archivist",
  );

  await user.click(screen.getByRole("button", { name: "상세 창 닫기" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.mocked(fetchFeatures).mockReset();
});
