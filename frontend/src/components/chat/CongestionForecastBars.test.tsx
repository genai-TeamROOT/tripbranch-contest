/* 혼잡도 게이지·예측 막대그래프의 색상 매핑과 결측값 처리를 검증한다. */

import { fireEvent, render, screen } from "@testing-library/react";
import type { InfoPlaceCard as InfoPlaceCardData } from "../../types";
import {
  CongestionLevelChip,
  CongestionLevelGauge,
  ConcentrationForecastBars,
  PopulationForecastBars,
  RoadTrafficStatusSection,
} from "./CongestionForecastBars";

const baseCard: InfoPlaceCardData = {
  question_type: "concentration",
  answer_fields: {},
  place_id: null,
  place_name: "경복궁",
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
};

describe("CongestionLevelGauge", () => {
  it("현재 단계에 마커와 굵은 라벨을 표시한다", () => {
    render(<CongestionLevelGauge level="약간 붐빔" />);

    expect(screen.getByLabelText("현재 인구 혼잡도 약간 붐빔")).toBeInTheDocument();
    expect(screen.getByText("약간 붐빔")).toHaveClass("font-semibold");
    expect(screen.getByText("여유")).not.toHaveClass("font-semibold");
  });

  it("레벨이 없으면 아무것도 렌더링하지 않는다", () => {
    const { container } = render(<CongestionLevelGauge level={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("알 수 없는 레벨 문자열이 와도 깨지지 않는다", () => {
    render(<CongestionLevelGauge level="예측불가" />);
    expect(screen.getByLabelText("현재 인구 혼잡도 예측불가")).toBeInTheDocument();
    // 4단계 중 어디에도 해당하지 않으므로 강조 라벨이 없다.
    for (const label of ["여유", "보통", "약간 붐빔", "붐빔"]) {
      expect(screen.getByText(label)).not.toHaveClass("font-semibold");
    }
  });
});

describe("PopulationForecastBars", () => {
  it("예측이 없으면 렌더링하지 않는다", () => {
    const { container } = render(<PopulationForecastBars card={baseCard} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("혼잡도 단계별로 다른 색상 막대를 그린다", () => {
    const card: InfoPlaceCardData = {
      ...baseCard,
      population_forecasts: [
        { forecast_at: "2026-08-20 15:00", congestion_level: "여유", population_min: null, population_max: null },
        { forecast_at: "2026-08-20 16:00", congestion_level: "붐빔", population_min: null, population_max: null },
        { forecast_at: "2026-08-20 17:00", congestion_level: "모름", population_min: null, population_max: null },
      ],
    };
    const { container } = render(<PopulationForecastBars card={card} />);

    const bars = container.querySelectorAll("[data-population-bar]");
    expect(bars[0]).toHaveClass("bg-emerald-500");
    expect(bars[1]).toHaveClass("bg-red-500");
    // 알 수 없는 레벨은 회색 fallback으로 처리해 깨지지 않는다.
    expect(bars[2]).toHaveClass("bg-gray-400");
  });

  it("현재 시각 막대를 점선 구분선과 강조 테두리로 앞쪽에 따로 보여준다", () => {
    const card: InfoPlaceCardData = {
      ...baseCard,
      population_current_level: "붐빔",
      population_forecasts: [
        { forecast_at: "2026-08-20 16:00", congestion_level: "여유", population_min: null, population_max: null },
      ],
    };
    const { container } = render(<PopulationForecastBars card={card} />);

    const bars = container.querySelectorAll("[data-population-bar]");
    // 첫 번째 막대는 예측이 아니라 "현재" — 현재 레벨(붐빔) 색이어야 한다.
    expect(bars[0]).toHaveClass("bg-red-500");
    expect(bars[1]).toHaveClass("bg-emerald-500");
    expect(screen.getByText("현재")).toHaveClass("font-semibold");

    // 강조 테두리는 칸이 아니라 막대에 건다 — 칸에 걸면 축이 선 뒤에 테두리가
    // 최고 눈금까지 올라가 실제보다 많아 보인다.
    expect(bars[0]).toHaveClass("ring-2");
    expect(bars[0].parentElement?.parentElement).toHaveClass("border-dashed");
  });

  it("현재 혼잡도 레벨이 없으면 '현재' 막대를 추가하지 않는다", () => {
    const card: InfoPlaceCardData = {
      ...baseCard,
      population_forecasts: [
        { forecast_at: "2026-08-20 16:00", congestion_level: "여유", population_min: null, population_max: null },
      ],
    };
    render(<PopulationForecastBars card={card} />);
    expect(screen.queryByText("현재")).not.toBeInTheDocument();
  });

  it("피크 전망 요약이 있으면 강조 문구로 보여준다", () => {
    const card: InfoPlaceCardData = {
      ...baseCard,
      population_forecasts: [
        { forecast_at: "2026-08-20 15:00", congestion_level: "붐빔", population_min: null, population_max: null },
      ],
      population_peak_forecast_summary: "15시(1시간 후)에 가장 붐빌 것으로 예상돼요. 혼잡정도는 붐빔일 것으로 예상돼요.",
    };
    render(<PopulationForecastBars card={card} />);

    expect(
      screen.getByText("15시(1시간 후)에 가장 붐빌 것으로 예상돼요. 혼잡정도는 붐빔일 것으로 예상돼요."),
    ).toBeInTheDocument();
  });
});

describe("PopulationForecastBars 세로축", () => {
  const scaledCard: InfoPlaceCardData = {
    ...baseCard,
    population_current_level: "붐빔",
    population_observed_at: "9월 5일 16:25",
    seoul_realtime_summary: { population_min: 78000, population_max: 80000 },
    population_forecasts: [
      {
        forecast_at: "2026-09-05 17:00",
        congestion_level: "약간 붐빔",
        population_min: 76000,
        population_max: 78000,
      },
      {
        forecast_at: "2026-09-05 18:00",
        congestion_level: "보통",
        population_min: 38000,
        population_max: 40000,
      },
    ],
  };

  it("인구 수가 모두 있으면 눈금을 세우고 실제 수치 비율로 막대를 그린다", () => {
    const { container } = render(<PopulationForecastBars card={scaledCard} />);

    // 최대 7.9만 → 2만 간격으로 0~8만 눈금.
    for (const label of ["0명", "2만명", "4만명", "6만명", "8만명"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    const bars = container.querySelectorAll("[data-population-bar]");
    // 현재 7.9만/8만 = 98.75%, 3.9만/8만 = 48.75% — 단계가 아니라 수치 비율이다.
    expect((bars[0] as HTMLElement).style.height).toBe("98.75%");
    expect((bars[2] as HTMLElement).style.height).toBe("48.75%");
  });

  it("막대에 커서를 올리면 시각·혼잡도·인구수를 말풍선으로 보여준다", () => {
    const { container } = render(<PopulationForecastBars card={scaledCard} />);

    const currentBar = container.querySelector('[data-population-bar="current"]');
    const column = currentBar!.parentElement!.parentElement!;
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();

    fireEvent.mouseEnter(column);
    const tooltip = screen.getByRole("tooltip");
    // 좁은 타일과 달리 말풍선에서는 자릿수를 접지 않는다.
    expect(tooltip).toHaveTextContent("78,000~80,000명");
    expect(tooltip).toHaveTextContent("붐빔");

    fireEvent.mouseLeave(column);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("hover가 없는 터치 기기를 위해 탭으로도 열고 닫는다", () => {
    const { container } = render(<PopulationForecastBars card={scaledCard} />);
    const column = container
      .querySelector('[data-population-bar="2026-09-05 18:00"]')!
      .parentElement!.parentElement!;

    fireEvent.click(column);
    expect(screen.getByRole("tooltip")).toHaveTextContent("38,000~40,000명");
    fireEvent.click(column);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("커서를 못 쓰는 사용자를 위해 막대 자체에 같은 내용을 붙인다", () => {
    render(<PopulationForecastBars card={scaledCard} />);

    expect(
      screen.getByRole("button", { name: "현재, 붐빔, 78,000~80,000명" }),
    ).toBeInTheDocument();
  });

  it("눈금이 서면 칸 배경을 지워 막대 위에 옅은 블록이 남지 않게 한다", () => {
    const { container } = render(<PopulationForecastBars card={scaledCard} />);

    const track = container.querySelector('[data-population-bar="current"]')?.parentElement;
    expect(track?.className).not.toMatch(/bg-(red|orange|amber|emerald)-50/);
  });

  it("인구 수가 하나라도 비면 눈금을 세우지 않고 단계 높이로 돌아간다", () => {
    const card: InfoPlaceCardData = {
      ...scaledCard,
      population_forecasts: [
        ...scaledCard.population_forecasts!.slice(0, 1),
        {
          forecast_at: "2026-09-05 18:00",
          congestion_level: "보통",
          population_min: null,
          population_max: null,
        },
      ],
    };
    const { container } = render(<PopulationForecastBars card={card} />);

    expect(screen.queryByText("8만명")).not.toBeInTheDocument();
    const bars = container.querySelectorAll("[data-population-bar]");
    // 붐빔 = CONGESTION_HEIGHT 92.
    expect((bars[0] as HTMLElement).style.height).toBe("92%");
  });

  it("안내 아이콘은 어긋남과 무관하게 항상 뜨고, 무엇을 보여주는 데이터인지·출처·기준 시각을 말한다", () => {
    render(<PopulationForecastBars card={scaledCard} />);

    const icon = screen.getByLabelText("인구 혼잡도 예측 안내");
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();

    fireEvent.click(icon);
    const tooltip = screen.getByRole("tooltip");
    expect(tooltip).toHaveTextContent(
      "향후 12시간 인구 혼잡도 예측이에요 (9월 5일 16:25 기준, 통신 데이터 기반 · 서울시 실시간 도시데이터).",
    );
    // 어긋나지 않는 카드(scaledCard)라 어긋남 설명은 안 붙는다.
    expect(tooltip).not.toHaveTextContent("계산 기준이 달라서");
  });

  it("바로 옆 막대끼리 인구 수와 혼잡도 단계 순서가 실제로 어긋나면, 같은 아이콘 툴팁에 그 설명을 덧붙인다", () => {
    // 난지한강공원 실측(2026-09-05)과 같은 모양 — 예측(17시)이 현재보다 인구는
    // 적은데 단계는 더 높다(약간 붐빔 3,250명 → 붐빔 2,750명).
    const invertedCard: InfoPlaceCardData = {
      ...scaledCard,
      population_current_level: "약간 붐빔",
      seoul_realtime_summary: { population_min: 3000, population_max: 3500 },
      population_forecasts: [
        {
          forecast_at: "2026-09-05 17:00",
          congestion_level: "붐빔",
          population_min: 2500,
          population_max: 3000,
        },
      ],
    };
    render(<PopulationForecastBars card={invertedCard} />);

    fireEvent.click(screen.getByLabelText("인구 혼잡도 예측 안내"));
    const tooltip = screen.getByRole("tooltip");
    // 항상 뜨는 안내와 어긋남 설명이 같은 툴팁 안에 함께 있다.
    expect(tooltip).toHaveTextContent("향후 12시간 인구 혼잡도 예측이에요");
    expect(tooltip).toHaveTextContent(
      "'현재'와 '예측' 혼잡도는 계산 기준이 달라서, 막대 높이(인구 수)와 색(혼잡도 단계)이 안 맞아 보일 때가 있어요.",
    );
  });

  it("그래프가 뭘 보여주는지·출처는 아이콘 안내에만 있고, 별도 캡션으로 반복하지 않는다", () => {
    render(<PopulationForecastBars card={scaledCard} />);
    expect(screen.queryByText(/통신 데이터 기반/)).not.toBeInTheDocument();
  });
});

describe("PopulationForecastBars 시각 라벨", () => {
  // 실측(2026-09-07)에서 모바일 카드 폭(~340px)에 재현한 것과 같은 12슬롯 예측.
  const twelveHourCard: InfoPlaceCardData = {
    ...baseCard,
    population_current_level: "여유",
    population_observed_at: "9월 7일 11:00",
    seoul_realtime_summary: { population_min: 1500, population_max: 2000 },
    population_forecasts: Array.from({ length: 12 }, (_, index) => ({
      forecast_at: `2026-09-07 ${String(12 + index).padStart(2, "0")}:00`,
      congestion_level: "여유",
      population_min: 1000,
      population_max: 1200,
    })),
  };

  it("라벨을 막대 칸에 가두지 않는다 — 잘리지 않고 전체 글자가 그대로 나온다", () => {
    render(<PopulationForecastBars card={twelveHourCard} />);

    // 좁은 칸 안에 truncate로 가두던 이전 방식이면 "현재"·"12시"가 "현...",
    // "1..."로 잘렸다(2026-09-07 모바일 375px 실측). 이제는 칸 폭과 무관하게
    // 글자 전체가 그대로 나와야 한다.
    expect(screen.getByText("현재")).toBeInTheDocument();
    expect(screen.getByText("14시")).toBeInTheDocument();
    expect(screen.queryByText(/\.\.\.$/)).not.toBeInTheDocument();
  });

  it("'현재' 바로 다음 칸에는 라벨을 보여주지 않는다 — 붙어 보이지 않게 3칸 간격을 지킨다", () => {
    render(<PopulationForecastBars card={twelveHourCard} />);

    // "현재"(0번) 다음 예측은 12시(1번 칸)인데, 이 칸은 라벨을 보여주지 않는다
    // — 안 그러면 "현재"와 곧바로 붙어 보인다(2026-09-07 실측으로 확인).
    expect(screen.queryByText("12시")).not.toBeInTheDocument();
    expect(screen.queryByText("13시")).not.toBeInTheDocument();
    // 3칸 간격(현재=0, 14시=3, 17시=6, 20시=9, 23시=12)으로만 보여준다.
    for (const label of ["14시", "17시", "20시", "23시"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });

  it("라벨 span에 더는 칸 폭 제약(truncate)을 걸지 않는다", () => {
    render(<PopulationForecastBars card={twelveHourCard} />);
    expect(screen.getByText("현재")).not.toHaveClass("truncate");
    expect(screen.getByText("14시")).not.toHaveClass("truncate");
  });
});

describe("CongestionLevelChip", () => {
  it("인구 혼잡도와 상권 활동을 같은 색 사다리로 보여준다", () => {
    const { rerender } = render(<CongestionLevelChip level="여유" />);
    expect(screen.getByText("여유")).toHaveClass("bg-emerald-50");

    rerender(<CongestionLevelChip level="붐빔" />);
    expect(screen.getByText("붐빔")).toHaveClass("bg-rust-tint");

    // 상권 원문은 "바쁜 시간대"처럼 접미사가 붙어 오기도 한다 — 같은 단계로 읽는다.
    rerender(<CongestionLevelChip level="바쁜 시간대" />);
    expect(screen.getByText("바쁜 시간대")).toHaveClass("bg-amber-100");
  });

  it("모르는 단계는 중립색으로 두고 깨지지 않는다", () => {
    render(<CongestionLevelChip level="예측불가" />);
    expect(screen.getByText("예측불가")).toHaveClass("bg-chip");
  });

  it("값이 없으면 렌더링하지 않는다", () => {
    const { container } = render(<CongestionLevelChip level={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("ConcentrationForecastBars", () => {
  it("집중률 단계별로 다른 색상 막대를 그린다", () => {
    const card: InfoPlaceCardData = {
      ...baseCard,
      concentration_forecasts: [
        { forecast_date: "2026-08-20", concentration_rate: 20, concentration_level: "quiet", concentration_label: "한적함" },
        { forecast_date: "2026-08-21", concentration_rate: 90, concentration_level: "crowded", concentration_label: "혼잡" },
      ],
    };
    const { container } = render(<ConcentrationForecastBars card={card} />);

    const bars = container.querySelectorAll('[aria-label="관광지 혼잡도 7일 예측"] > div > div > div');
    expect(bars[0]).toHaveClass("bg-emerald-500");
    expect(bars[1]).toHaveClass("bg-red-500");
  });
});

describe("CongestionLevelGauge와 levels prop", () => {
  it("3단계(도로소통) 배열을 넘기면 그 순서로 게이지를 그린다", () => {
    render(
      <CongestionLevelGauge
        level="정체"
        levels={[
          { label: "원활", color: "bg-emerald-500" },
          { label: "서행", color: "bg-amber-400" },
          { label: "정체", color: "bg-red-500" },
        ]}
        ariaLabelPrefix="현재 도로소통 단계"
      />,
    );

    expect(screen.getByLabelText("현재 도로소통 단계 정체")).toBeInTheDocument();
    expect(screen.getByText("정체")).toHaveClass("font-semibold");
    // 인구 혼잡도 라벨("여유" 등)이 섞여 나오면 안 된다.
    expect(screen.queryByText("여유")).not.toBeInTheDocument();
  });
});

describe("RoadTrafficStatusSection", () => {
  const trafficCard: InfoPlaceCardData = {
    ...baseCard,
    question_type: "realtime_traffic",
    answer_fields: {
      "도로소통 단계": "원활",
      "평균 주행속도": "32km/h",
      "안내": "해당 장소로 이동·진입하는 도로가 크게 막히지 않아요.",
    },
  };

  it("단계 게이지·평균속도·안내문구를 보여준다", () => {
    render(<RoadTrafficStatusSection card={trafficCard} />);

    expect(screen.getByLabelText("현재 도로소통 단계 원활")).toBeInTheDocument();
    expect(screen.getByText("평균 32km/h")).toBeInTheDocument();
    expect(
      screen.getByText("해당 장소로 이동·진입하는 도로가 크게 막히지 않아요."),
    ).toBeInTheDocument();
  });

  it("question_type이 realtime_traffic이 아니면 렌더링하지 않는다", () => {
    const { container } = render(
      <RoadTrafficStatusSection card={{ ...trafficCard, question_type: "concentration" }} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("단계 값이 없으면 렌더링하지 않는다", () => {
    const { container } = render(
      <RoadTrafficStatusSection card={{ ...trafficCard, answer_fields: {} }} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("돌발상황 4분류를 서울시가 준 순서 그대로 건수와 함께 보여준다", () => {
    render(
      <RoadTrafficStatusSection
        card={{
          ...trafficCard,
          road_incident_counts: [
            { label: "사고/고장", count: 0 },
            { label: "공사/집회", count: 2 },
            { label: "기상/화재", count: 0 },
            { label: "기타", count: 0 },
          ],
        }}
      />,
    );

    expect(screen.getByText("사고/고장")).toBeInTheDocument();
    expect(screen.getByText("공사/집회")).toBeInTheDocument();
    expect(screen.getByText("기상/화재")).toBeInTheDocument();
    expect(screen.getByText("기타")).toBeInTheDocument();
    // 진행 중(1건 이상)인 분류만 강조색을 쓴다.
    expect(screen.getByText("2")).toHaveClass("text-rust");
    expect(screen.getAllByText("0")[0]).toHaveClass("text-ink");
  });

  it("건수 데이터가 없으면(구버전 응답) 4분류 칸을 그리지 않는다", () => {
    const { container } = render(<RoadTrafficStatusSection card={trafficCard} />);
    expect(container.querySelector(".grid")).not.toBeInTheDocument();
  });
});
