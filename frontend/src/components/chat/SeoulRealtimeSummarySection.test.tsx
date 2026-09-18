/* 서울시 실시간 요약 블록이 어떤 카드에 뜨고, 값이 빠진 구획을 어떻게 감추는지 검증한다. */

import { render, screen } from "@testing-library/react";
import type { InfoPlaceCard as InfoPlaceCardData } from "../../types";
import { SeoulRealtimeSummarySection } from "./SeoulRealtimeSummarySection";

const baseCard: InfoPlaceCardData = {
  question_type: "concentration",
  answer_fields: {},
  place_id: null,
  place_name: "강남역",
  latitude: null,
  longitude: null,
  thumbnail_url: null,
  overview: null,
  operating_hours: null,
  rest_date: null,
  parking: null,
  parking_fee: null,
  fee: null,
  baby_carriage: null,
  pet: null,
  credit_card: null,
  restroom: null,
  homepage: null,
  population_current_level: "붐빔",
  population_observed_at: "9월 5일 16:25",
  seoul_realtime_summary: {
    population_min: 78000,
    population_max: 80000,
    peak_forecast_hour_label: "오후 5시",
    peak_forecast_level: "약간 붐빔",
    top_age_label: "20대",
    top_age_rate: 29,
    commercial_level: "보통",
    commercial_observed_at: "9월 5일 16:40",
    payment_count: 329,
    payment_amount_min: 7900000,
    payment_amount_max: 8000000,
    top_payment_categories: [
      {
        label: "의료 · 병원",
        activity_level: "한산한",
        payment_amount_min: 1300000,
        payment_amount_max: 1400000,
      },
      {
        label: "음식·음료 · 기타요식",
        activity_level: "바쁜",
        payment_amount_min: 1000000,
        payment_amount_max: 1100000,
      },
    ],
  },
};

describe("SeoulRealtimeSummarySection", () => {
  it("인구 값을 서울시 원문 구간 그대로 보여준다", () => {
    render(<SeoulRealtimeSummarySection card={baseCard} />);

    // 만 명이 넘으면 좁은 타일에서 잘리지 않게 만 단위로 접는다.
    expect(screen.getByText("7.8~8만명")).toBeInTheDocument();
    expect(screen.getByText("오후 5시")).toBeInTheDocument();
    expect(screen.getByText("20대")).toBeInTheDocument();
    expect(screen.getByText("29.0%")).toBeInTheDocument();
    // 단계는 회색 캡션이 아니라 색 칩으로 보여준다.
    expect(screen.getByText("붐빔")).toHaveClass("bg-rust-tint");
  });

  it("상권 활동 단계를 '~ 시간대'로 붙여 하나로 보여준다(매출 총액·건수는 뺀다)", () => {
    render(
      <SeoulRealtimeSummarySection card={{ ...baseCard, question_type: "realtime_commercial" }} />,
    );

    expect(screen.getByText("보통 시간대")).toBeInTheDocument();
    expect(screen.queryByText("790~800만원")).not.toBeInTheDocument();
    expect(screen.queryByText("329건")).not.toBeInTheDocument();
    expect(screen.queryByText("최근 10분 매출 총액")).not.toBeInTheDocument();
    expect(screen.queryByText("상권 활동")).not.toBeInTheDocument();
  });

  it("결제 상위 업종을 세로 목록이 아니라 한 줄에 순위·업종명만 나란히 보여준다(금액은 뺀다)", () => {
    const { container } = render(
      <SeoulRealtimeSummarySection card={{ ...baseCard, question_type: "realtime_commercial" }} />,
    );

    // 세로로 쌓인 카드 목록(li마다 줄바꿈)이 아니라 한 줄(flex row)이다.
    const list = container.querySelector("ol");
    expect(list).toHaveClass("flex");
    // 한 줄에 다 넣어야 해서 대분류 접두어("의료 · ", "음식·음료 · ")는 빼고
    // 소분류만 보여준다 — 전체 원문은 title 툴팁으로 남긴다.
    expect(screen.getByText("병원")).toBeInTheDocument();
    expect(screen.getByText("기타요식")).toBeInTheDocument();
    expect(screen.getByTitle("의료 · 병원")).toBeInTheDocument();
    expect(screen.queryByText("130~140만원")).not.toBeInTheDocument();
  });

  it("상권 활동 단계 칩과 Top 3 목록을 같은 한 줄에 함께 둔다", () => {
    render(
      <SeoulRealtimeSummarySection card={{ ...baseCard, question_type: "realtime_commercial" }} />,
    );

    const row = screen.getByText("보통 시간대").closest("div");
    // 칩과 목록(ol)이 형제로 같은 행 안에 있다 — 두 줄로 갈라지지 않는다.
    expect(row?.querySelector("ol")).not.toBeNull();
  });

  it("상권 미제공 지역(경복궁 등)은 상권 구획을 통째로 감춘다", () => {
    render(
      <SeoulRealtimeSummarySection
        card={{
          ...baseCard,
          question_type: "realtime_commercial",
          seoul_realtime_summary: {
            population_min: 2500,
            population_max: 3000,
            top_age_label: "20대",
            top_age_rate: 21.7,
          },
        }}
      />,
    );

    // 만 명 미만이면 접지 않고 원래 자릿수를 그대로 보여준다.
    expect(screen.getByText("2,500~3,000명")).toBeInTheDocument();
    expect(screen.getByText("실시간 인구")).toBeInTheDocument();
    expect(screen.queryByText("실시간 인기 상권")).not.toBeInTheDocument();
  });

  it("실시간 상권(realtime_commercial) 질문에는 상권 구획을 싣는다", () => {
    render(
      <SeoulRealtimeSummarySection card={{ ...baseCard, question_type: "realtime_commercial" }} />,
    );
    expect(screen.getByText("실시간 인기 상권")).toBeInTheDocument();
  });

  it("실시간 혼잡도(concentration) 질문에는 같은 응답에 상권 값이 실려 와도 상권 구획을 감춘다", () => {
    render(<SeoulRealtimeSummarySection card={{ ...baseCard, question_type: "concentration" }} />);

    // 인구 구획은 그대로 뜬다 — 물어본 것과 관련 없는 상권 구획만 뺀다.
    expect(screen.getByText("실시간 인구")).toBeInTheDocument();
    expect(screen.queryByText("실시간 인기 상권")).not.toBeInTheDocument();
    expect(screen.queryByText("보통 시간대")).not.toBeInTheDocument();
    expect(screen.queryByText("병원")).not.toBeInTheDocument();
  });

  it("서울시 데이터를 조회하지 않는 질문 유형에는 렌더링하지 않는다", () => {
    const { container } = render(
      <SeoulRealtimeSummarySection card={{ ...baseCard, question_type: "operating_hours" }} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("요약이 없으면 렌더링하지 않는다", () => {
    const { container } = render(
      <SeoulRealtimeSummarySection card={{ ...baseCard, seoul_realtime_summary: null }} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
