/* INFO 장소 카드의 접기/펼치기와 결측값 숨김을 검증한다. */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, it, vi } from "vitest";
import { fetchRecommendationPlaceDetails, searchPlaces } from "../../api/trip";
import { clearDirectionsOriginCache } from "../../hooks/useNaverDirections";
import { clearLocationSettings, setLocationOrigin } from "../../state/locationSettings";
import { TripProvider } from "../../state/TripContext";
import type { InfoPlaceCard as InfoPlaceCardData } from "../../types";
import { openNaverDirections, openNaverMapSearch } from "../../utils/naverDirections";
import { PlaceInfoCard } from "./PlaceInfoCard";

// 카드가 useTripState(언어)를 읽으므로 TripProvider로 감싼다.
const renderWithTrip = (ui: Parameters<typeof render>[0]) => render(ui, { wrapper: TripProvider });

vi.mock("../../api/trip", () => ({
  fetchRecommendationPlaceDetails: vi.fn(),
  // 길찾기 훅이 위치 설정의 출발지 이름을 좌표로 풀 때 부른다.
  searchPlaces: vi.fn(),
}));

/* 링크를 여는 두 함수만 가로채고 나머지는 진짜를 쓴다. */
vi.mock("../../utils/naverDirections", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../utils/naverDirections")>()),
  openNaverMapSearch: vi.fn(),
  openNaverDirections: vi.fn(),
}));

const card: InfoPlaceCardData = {
  question_type: "parking",
  answer_fields: { parking: "가능", parking_fee: "무료" },
  place_id: "126508",
  place_name: "경복궁",
  latitude: null,
  longitude: null,
  thumbnail_url: "https://example.test/gyeongbokgung.jpg",
  overview: "조선 왕조의 법궁이다.",
  operating_hours: "09:00~18:00",
  rest_date: "매주 화요일 ※ 단, 정기휴일이 공휴일 및 대체공휴일과 겹치면 개방합니다.",
  parking: "가능",
  parking_fee: "무료",
  fee: "성인 3,000원",
  baby_carriage: "가능",
  pet: null,
  credit_card: "가능",
  restroom: "있음",
  homepage: "https://example.test",
};

it("질문 답과 썸네일은 바로 보이고, 클릭하면 같은 상세 모달을 연다", async () => {
  const user = userEvent.setup();
  renderWithTrip(<PlaceInfoCard card={card} />);

  expect(screen.getByText("주차 요금")).toBeInTheDocument();
  expect(screen.queryByText("조선 왕조의 법궁이다.")).not.toBeInTheDocument();
  expect(screen.getByRole("img", { name: "경복궁 이미지" })).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "장소 상세보기" }));

  const dialog = screen.getByRole("dialog", { name: "경복궁" });
  expect(within(dialog).getByText("조선 왕조의 법궁이다.")).toBeInTheDocument();
  expect(within(dialog).getByRole("img", { name: "경복궁 이미지" })).toBeInTheDocument();
  expect(within(dialog).getByText("성인 3,000원")).toBeInTheDocument();
  expect(
    within(dialog).getByText("※ 단, 정기휴일이 공휴일 및 대체공휴일과 겹치면 개방합니다."),
  ).toHaveClass("text-xs", "text-muted");
  // 홈페이지는 하단 별도 링크가 아니라 "관련 정보" 박스 안에 클릭 가능한 링크로 뜬다.
  // question_type이 "parking"이라 answer_fields엔 없지만(카드 최상위 필드), 박스가
  // 합성해서 보여준다.
  // 버튼에 적히는 이름은 도메인이다 — 전체 주소는 href와 title에 그대로 남는다.
  expect(
    within(dialog).getByRole("link", { name: /example\.test/ }),
  ).toHaveAttribute("href", "https://example.test");
});

it("관련 정보의 URL은 클릭 가능한 링크로 보여준다", async () => {
  const user = userEvent.setup();
  renderWithTrip(
    <PlaceInfoCard
      card={{ ...card, answer_fields: { homepage: "https://instagram.com/gyeongbokgung" } }}
    />,
  );

  await user.click(screen.getByRole("button", { name: "장소 상세보기" }));

  const dialog = within(screen.getByRole("dialog"));
  const homepageLink = dialog.getByRole("link", { name: /instagram\.com/ });
  expect(homepageLink).toHaveAttribute("href", "https://instagram.com/gyeongbokgung");
  // 긴 경로는 화면에 적지 않는다 — 누르는 것 말고 할 일이 없는 값이다.
  expect(homepageLink).toHaveAttribute("title", "https://instagram.com/gyeongbokgung");
});

it("프로토콜 없는 www. 도메인도 https://를 붙여 링크로 보여준다", async () => {
  // 실측(TourAPI homepage 필드): 3.6%가 "www.xxx.xxx" 형태로 온다 — http(s):// 없이.
  const user = userEvent.setup();
  renderWithTrip(
    <PlaceInfoCard card={{ ...card, answer_fields: { homepage: "www.royalpalace.go.kr" } }} />,
  );

  await user.click(screen.getByRole("button", { name: "장소 상세보기" }));

  const dialog = within(screen.getByRole("dialog"));
  const link = dialog.getByRole("link", { name: /royalpalace\.go\.kr/ });
  expect(link).toHaveAttribute("href", "https://www.royalpalace.go.kr");
});

it("없는 값은 카드에 임의 문구나 빈 이미지로 표시하지 않는다", () => {
  renderWithTrip(
    <PlaceInfoCard card={{ ...card, thumbnail_url: null, overview: null, pet: null }} />,
  );

  expect(screen.queryByRole("img")).not.toBeInTheDocument();
  expect(screen.queryByText("정보 없음")).not.toBeInTheDocument();
});

it("요금 항목과 ※ 안내를 각각 줄바꿈해 표시한다", async () => {
  const user = userEvent.setup();
  const fee = "- 성인 10,000원 - 학생 7,000원 ※ 무료: 장애인";
  renderWithTrip(<PlaceInfoCard card={{ ...card, answer_fields: { fee }, fee }} />);

  expect(screen.getByText("- 성인 10,000원 - 학생 7,000원 ※ 무료: 장애인")).toHaveClass(
    "whitespace-pre-line",
  );

  await user.click(screen.getByRole("button", { name: "장소 상세보기" }));

  const dialog = within(screen.getByRole("dialog"));
  expect(dialog.getByText("- 성인 10,000원")).toHaveClass("whitespace-pre-line");
  expect(dialog.getByText("- 학생 7,000원")).toHaveClass("whitespace-pre-line");
  expect(dialog.getByText("※ 무료: 장애인")).toHaveClass("text-xs", "text-muted");
});

it("붙어 있는 월별 운영시간을 기간별 카드로 나눈다", async () => {
  const user = userEvent.setup();
  const operatingHours =
    "[1월~2월/11월~12월]09:00~17:00 (입장마감 16:00)[3월~5월/9월~10월]09:00~18:00 (입장마감 17:00)[6월~8월]09:00~18:30 (입장마감 17:30)";
  renderWithTrip(
    <PlaceInfoCard
      card={{
        ...card,
        answer_fields: { operating_hours: operatingHours },
        operating_hours: operatingHours,
      }}
    />,
  );

  expect(screen.getByText("1월~2월 · 11월~12월")).toBeInTheDocument();
  expect(screen.getByText("09:00–17:00 · 입장 마감 16:00")).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "장소 상세보기" }));

  const dialog = screen.getByRole("dialog");
  expect(within(dialog).getByText("1월~2월 · 11월~12월")).toBeInTheDocument();
  expect(within(dialog).getByText("09:00–17:00 · 입장 마감 16:00")).toBeInTheDocument();
  expect(within(dialog).getByText("6월~8월")).toBeInTheDocument();
  expect(within(dialog).getByText("09:00–18:30 · 입장 마감 17:30")).toBeInTheDocument();
});

it("주소 INFO 카드도 클릭하면 전체 장소 상세를 보강 조회한다", async () => {
  const user = userEvent.setup();
  const minimalCard: InfoPlaceCardData = {
    ...card,
    question_type: "location_info",
    answer_fields: { address: "서울특별시 종로구 사직로 161" },
    thumbnail_url: null,
    overview: null,
    operating_hours: null,
    rest_date: null,
    parking: null,
    parking_fee: null,
    fee: null,
    baby_carriage: null,
    credit_card: null,
    restroom: null,
    homepage: null,
  };
  vi.mocked(fetchRecommendationPlaceDetails).mockResolvedValue({
    status: "success",
    requested_place_id: "126508",
    place_card: card,
  });

  renderWithTrip(<PlaceInfoCard card={minimalCard} />);
  await user.click(screen.getByRole("button", { name: "장소 상세보기" }));

  await waitFor(() => {
    expect(fetchRecommendationPlaceDetails).toHaveBeenCalledWith({
      place_id: "126508",
      place_name: "경복궁",
    });
  });
  const dialog = screen.getByRole("dialog", { name: "경복궁" });
  expect(within(dialog).getByText("조선 왕조의 법궁이다.")).toBeInTheDocument();
  expect(within(dialog).getByText("서울특별시 종로구 사직로 161")).toBeInTheDocument();
});

it("혼잡도 카드(place_id 없음)도 이름으로 상세를 보강 조회한다", async () => {
  const user = userEvent.setup();
  const concentrationCard: InfoPlaceCardData = {
    ...card,
    question_type: "concentration",
    place_id: null,
    place_name: "창덕궁",
    answer_fields: { concentration: "이번 주말 · 다소 혼잡" },
    thumbnail_url: null,
    overview: null,
  };
  vi.mocked(fetchRecommendationPlaceDetails).mockResolvedValue({
    status: "success",
    requested_place_id: null,
    place_card: { ...card, place_name: "창덕궁", overview: "창덕궁 상세 개요" },
  });

  renderWithTrip(<PlaceInfoCard card={concentrationCard} />);
  await user.click(screen.getByRole("button", { name: "장소 상세보기" }));

  await waitFor(() => {
    expect(fetchRecommendationPlaceDetails).toHaveBeenCalledWith(
      expect.objectContaining({ place_name: "창덕궁" }),
    );
  });
  const dialog = screen.getByRole("dialog", { name: "창덕궁" });
  expect(within(dialog).getByText("창덕궁 상세 개요")).toBeInTheDocument();
});

it("실시간 도시데이터 카드는 모달에서 추가 항목과 출처를 표시한다", async () => {
  const user = userEvent.setup();
  const realtimeCard: InfoPlaceCardData = {
    ...card,
    question_type: "realtime_event",
    answer_fields: { "테스트 행사": "2026-08-20~2026-08-21 · 광화문광장" },
    thumbnail_url: null,
    overview: null,
    operating_hours: null,
    rest_date: null,
    parking: null,
    parking_fee: null,
    fee: null,
    baby_carriage: null,
    credit_card: null,
    restroom: null,
    homepage: null,
    realtime_area_name: "광화문·덕수궁",
    realtime_observed_at: "8월 20일 16:20",
    realtime_source_url: "https://data.seoul.go.kr/example",
    realtime_detail_items: [
      {
        title: "테스트 행사",
        subtitle: "2026-08-20~2026-08-21",
        details: { 장소: "광화문광장" },
        thumbnail_url: "https://example.test/event.jpg",
        external_url: "https://example.test/event",
      },
    ],
  };

  renderWithTrip(<PlaceInfoCard card={realtimeCard} />);
  await user.click(screen.getByRole("button", { name: "장소 상세보기" }));

  const dialog = screen.getByRole("dialog");
  expect(within(dialog).getByText("실시간 지역 정보")).toBeInTheDocument();
  expect(within(dialog).getByText("광화문·덕수궁 · 8월 20일 16:20 기준")).toBeInTheDocument();
  expect(within(dialog).getByRole("img", { name: "테스트 행사 이미지" })).toBeInTheDocument();
  /* 출처는 라벨이다. 서울 열린데이터광장 페이지는 데이터셋 설명이라 사용자가 읽을
     화면이 아니어서, 어디서 온 값인지만 밝히고 누를 수 있게 두지 않는다. */
  expect(within(dialog).getByText("출처: ⓒ서울 실시간 도시데이터")).toBeInTheDocument();
  expect(
    within(dialog).queryByRole("link", { name: /서울 실시간 도시데이터/ }),
  ).not.toBeInTheDocument();
  expect(within(dialog).getByRole("link", { name: "자세히 보기 ↗" })).toHaveAttribute(
    "href",
    "https://example.test/event",
  );
});

it("실시간 주차 카드에는 데이터 출처와 서울시 주차정보 포털 링크를 함께 표시한다", async () => {
  const user = userEvent.setup();
  const parkingCard: InfoPlaceCardData = {
    ...card,
    question_type: "realtime_parking",
    answer_fields: { "[공영] 세종로 공영주차장": "현재 535대 주차 가능(총 1,260대, 유료)" },
    thumbnail_url: null,
    overview: null,
    operating_hours: null,
    rest_date: null,
    parking: null,
    parking_fee: null,
    fee: null,
    baby_carriage: null,
    credit_card: null,
    restroom: null,
    homepage: null,
    realtime_area_name: "종로구",
    realtime_observed_at: "8월 27일 14:20",
    realtime_source_url: "https://data.seoul.go.kr/dataList/OA-21709/S/1/datasetView.do",
    realtime_detail_items: [
      {
        title: "세종로 공영주차장",
        subtitle: "총 1,260대 · 535대 가능 · 유료",
        details: {
          유형: "공영",
          주소: "서울특별시 종로구 세종대로 189",
          거리: "약 300m",
          // 백엔드 재시작 전 응답 키도 새 UI에서 읽어야 한다.
          "잔여 면수": "535면",
          "총 주차면": "1,260면",
          "현재 주차": "725대",
          요금: "유료",
          "기준 시각": "2026-08-27 14:20",
        },
        thumbnail_url: null,
        external_url: null,
      },
      {
        title: "서울스퀘어 주차장",
        subtitle: "총 461대 · 실시간 주차 대수 미제공 · 유료",
        details: {
          유형: "민영",
          주소: "서울특별시 중구 남대문로5가 541-0",
          "총 주차": "총 461대",
          요금: "유료",
        },
        thumbnail_url: null,
        external_url: null,
      },
    ],
  };

  renderWithTrip(<PlaceInfoCard card={parkingCard} />);
  expect(screen.getByText("현재 535대 주차 가능")).toHaveClass("text-emerald-700");
  expect(screen.getByText("세종로 공영주차장")).toBeInTheDocument();
  expect(screen.getByText("공영")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "장소 상세보기" }));

  const dialog = within(screen.getByRole("dialog"));
  expect(dialog.getByText("출처: ⓒ서울 실시간 도시데이터")).toBeInTheDocument();
  expect(dialog.queryByRole("link", { name: /서울 실시간 도시데이터/ })).not.toBeInTheDocument();
  expect(dialog.getByRole("link", { name: "서울시 실시간 주차정보 ↗" })).toHaveAttribute(
    "href",
    "https://parking.seoul.go.kr/",
  );
  expect(dialog.getByText("535대 가능")).toBeInTheDocument();
  expect(dialog.getByText("총 1,260대")).toBeInTheDocument();
  expect(dialog.getByText("서울특별시 종로구 세종대로 189")).toBeInTheDocument();
  expect(dialog.getByText("실시간 주차 가능")).toBeInTheDocument();
  expect(dialog.getByText("실시간 잔여 현황 미제공")).toBeInTheDocument();
  expect(dialog.queryByRole("heading", { name: "관련 정보" })).not.toBeInTheDocument();

  await user.click(dialog.getAllByRole("button", { name: "네이버 지도로 길찾기" })[0]);
  expect(openNaverMapSearch).toHaveBeenCalledWith("서울특별시 종로구 세종대로 189");

  await user.click(dialog.getByRole("button", { name: "공영 1" }));
  expect(dialog.queryByText("서울스퀘어 주차장")).not.toBeInTheDocument();
  await user.click(dialog.getByRole("button", { name: "민영 1" }));
  expect(dialog.getByText("서울스퀘어 주차장")).toBeInTheDocument();
});

it("근처 주차장이 3곳을 넘으면 접어두고 눌러야 나머지가 보인다", async () => {
  const user = userEvent.setup();
  const manyLotsCard: InfoPlaceCardData = {
    ...card,
    question_type: "realtime_parking",
    answer_fields: {
      "[공영] 세종로 공영주차장": "현재 535대 주차 가능(약 300m, 총 1,260대, 유료)",
      "[공영] 서울글로벌센터 공영주차장": "현재 20대 주차 가능(약 500m, 총 44대, 유료)",
      "[민영] 경복궁 주차장": "실시간 주차 현황 미제공(약 250m, 총 266대, 유료)",
      "[민영] 삼청 주차장": "실시간 주차 현황 미제공(약 700m, 총 80대, 유료)",
    },
    thumbnail_url: null,
    overview: null,
    operating_hours: null,
    rest_date: null,
    parking: null,
    parking_fee: null,
    fee: null,
    baby_carriage: null,
    credit_card: null,
    restroom: null,
    homepage: null,
    realtime_area_name: "경복궁",
    realtime_observed_at: "8월 27일 14:20",
    realtime_source_url: "https://data.seoul.go.kr/dataList/OA-21285/S/1/datasetView.do",
    realtime_detail_items: [],
  };

  renderWithTrip(<PlaceInfoCard card={manyLotsCard} />);

  expect(screen.getByText("세종로 공영주차장")).toBeInTheDocument();
  expect(screen.queryByText("삼청 주차장")).not.toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "1곳 더 보기" }));
  expect(screen.getByText("삼청 주차장")).toBeInTheDocument();
});

it("실시간 인구 혼잡도 카드는 안내 문구와 서울시 지도 미리보기를 표시한다", async () => {
  const user = userEvent.setup();
  const populationCard: InfoPlaceCardData = {
    ...card,
    question_type: "concentration",
    answer_fields: { concentration: "보통" },
    thumbnail_url: null,
    overview: null,
    operating_hours: null,
    rest_date: null,
    parking: null,
    parking_fee: null,
    fee: null,
    baby_carriage: null,
    credit_card: null,
    restroom: null,
    homepage: null,
    realtime_area_name: "성수카페거리",
    realtime_observed_at: "8월 20일 11:40",
    population_current_message: "사람이 몰려있을 수 있지만 크게 붐비지는 않아요.",
    realtime_map_url:
      "https://data.seoul.go.kr/SeoulRtd/map?hotspotNm=%EC%84%B1%EC%88%98%EC%B9%B4%ED%8E%98%EA%B1%B0%EB%A6%AC&y=127.0&x=37.5",
    realtime_detail_items: [
      {
        title: "혼잡도 안내",
        subtitle: "보통",
        details: { 안내: "사람이 몰려있을 수 있지만 크게 붐비지는 않아요." },
        thumbnail_url: null,
        external_url: null,
      },
    ],
  };

  renderWithTrip(<PlaceInfoCard card={populationCard} />);
  await user.click(screen.getByRole("button", { name: "장소 상세보기" }));

  const dialog = screen.getByRole("dialog");
  expect(within(dialog).getByText("혼잡도 안내")).toBeInTheDocument();
  expect(
    within(dialog).getByText("사람이 몰려있을 수 있지만 크게 붐비지는 않아요."),
  ).toBeInTheDocument();
  expect(within(dialog).getByRole("link", { name: "실시간 혼잡도 지도 ↗" })).toHaveAttribute(
    "href",
    populationCard.realtime_map_url,
  );
  expect(within(dialog).getByTitle("성수카페거리 실시간 혼잡도 지도")).toHaveAttribute(
    "src",
    populationCard.realtime_map_url,
  );
  expect(screen.getByText("사람이 몰려있을 수 있지만 크게 붐비지는 않아요.")).toBeInTheDocument();
});

it("인구 혼잡도 예측 그래프와 게이지는 요약 카드와 상세 모달 양쪽에 나온다", async () => {
  const user = userEvent.setup();
  const populationCard: InfoPlaceCardData = {
    ...card,
    question_type: "concentration",
    answer_fields: { concentration: "보통" },
    thumbnail_url: null,
    overview: null,
    population_current_level: "약간 붐빔",
    population_observed_at: "8월 20일 14:00",
    population_peak_forecast_summary:
      "16시(2시간 후)에 가장 붐빌 것으로 예상돼요. 혼잡정도는 붐빔일 것으로 예상돼요.",
    population_forecasts: [
      {
        forecast_at: "2026-08-20 15:00",
        congestion_level: "보통",
        population_min: 3000,
        population_max: 3500,
      },
      {
        forecast_at: "2026-08-20 16:00",
        congestion_level: "붐빔",
        population_min: 5000,
        population_max: 5500,
      },
    ],
    realtime_map_url: "https://data.seoul.go.kr/SeoulRtd/map?hotspotNm=test&y=127&x=37",
    realtime_detail_items: [],
  };

  renderWithTrip(<PlaceInfoCard card={populationCard} />);

  // 요약 카드: 게이지가 "약간 붐빔"을 강조하고, 피크 시간 요약이 보인다.
  expect(screen.getByLabelText("현재 인구 혼잡도 약간 붐빔")).toBeInTheDocument();
  expect(
    screen.getByText(
      "16시(2시간 후)에 가장 붐빌 것으로 예상돼요. 혼잡정도는 붐빔일 것으로 예상돼요.",
    ),
  ).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "장소 상세보기" }));

  const dialog = within(screen.getByRole("dialog"));
  expect(dialog.getByLabelText("현재 인구 혼잡도 약간 붐빔")).toBeInTheDocument();
  expect(dialog.getByLabelText("현재부터 향후 12시간 인구 혼잡도 예측")).toBeInTheDocument();
  expect(
    dialog.getByText(
      "16시(2시간 후)에 가장 붐빌 것으로 예상돼요. 혼잡정도는 붐빔일 것으로 예상돼요.",
    ),
  ).toBeInTheDocument();
});

it("도로소통 카드는 단계 게이지를 요약 카드와 상세 모달 양쪽에 보여준다", async () => {
  const user = userEvent.setup();
  const trafficCard: InfoPlaceCardData = {
    ...card,
    question_type: "realtime_traffic",
    answer_fields: {
      "도로소통 단계": "원활",
      "평균 주행속도": "32km/h",
      안내: "해당 장소로 이동·진입하는 도로가 크게 막히지 않아요.",
    },
    thumbnail_url: null,
    overview: null,
    operating_hours: null,
    rest_date: null,
    parking: null,
    parking_fee: null,
    fee: null,
    baby_carriage: null,
    credit_card: null,
    restroom: null,
    homepage: null,
    realtime_detail_items: [
      {
        title: "도로소통 안내",
        subtitle: "원활",
        details: { 안내: "해당 장소로 이동·진입하는 도로가 크게 막히지 않아요." },
        thumbnail_url: null,
        external_url: null,
      },
    ],
  };

  renderWithTrip(<PlaceInfoCard card={trafficCard} />);
  expect(screen.getByLabelText("현재 도로소통 단계 원활")).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "장소 상세보기" }));

  const dialog = within(screen.getByRole("dialog"));
  expect(dialog.getByLabelText("현재 도로소통 단계 원활")).toBeInTheDocument();
});

/* 무장애 값(D-077)은 계약 키가 영문이라, 라벨 지도에 없으면 화면에 wheelchair_access
 * 그대로 찍힌다. 키가 늘어날 때마다 라벨을 함께 넣었는지 이 테스트가 잡는다. */
it("무장애 항목을 한글 라벨로 보여준다", () => {
  renderWithTrip(
    <PlaceInfoCard
      card={{
        ...card,
        question_type: "facility",
        answer_fields: {
          wheelchair_access: "주출입구는 경사로가 있어 휠체어 접근 가능함",
          accessible_restroom: "장애인 화장실 있음",
          accessible_parking: "장애인 주차장 있음(9면)",
          wheelchair_rental: "대여가능",
          stroller_rental: "대여가능",
          nursing_room: "수유실 있음",
          guide_dog: "동반가능",
          braille_block: "점자블록 있음",
          braille_promotion: "점자 안내물 있음",
          audio_guide: "음성 안내 있음",
          public_transport: "저상버스 운행",
          infant_family_etc: "기저귀교환대 있음",
          disability_etc: "관람경로 표시",
        },
      }}
    />,
  );

  for (const label of [
    "휠체어 접근",
    "장애인 화장실",
    "장애인 주차",
    "휠체어 대여",
    "유모차 대여",
    "수유실",
    "보조견 동반",
    "점자블록",
    "점자 안내물",
    "음성 안내",
    "대중교통",
    "영유아·가족 편의",
    "장애인 편의 기타",
  ]) {
    expect(screen.getByText(label)).toBeInTheDocument();
  }
  // 영문 계약 키가 화면에 남아 있으면 안 된다.
  expect(screen.queryByText("wheelchair_access")).not.toBeInTheDocument();
});

it("실시간 행사는 추천 카드처럼 사진과 함께 가로로 늘어놓는다", async () => {
  const user = userEvent.setup();
  const openSpy = vi.spyOn(window, "open").mockImplementation(() => null);
  const eventsCard: InfoPlaceCardData = {
    ...card,
    question_type: "realtime_event",
    answer_fields: {
      "집옥재 작은 도서관 개방": "2026-04-01~2026-10-31 · 경복궁 집옥재·팔우정",
      "수문장 순라의식": "2026-03-01~2026-11-29 · 경복궁 내부",
    },
    thumbnail_url: null,
    overview: null,
    operating_hours: null,
    rest_date: null,
    parking: null,
    parking_fee: null,
    fee: null,
    baby_carriage: null,
    credit_card: null,
    restroom: null,
    homepage: null,
    realtime_area_name: "경복궁",
    realtime_observed_at: "9월 2일 11:00",
    realtime_source_url: "https://data.seoul.go.kr/example",
    realtime_detail_items: [
      {
        title: "집옥재 작은 도서관 개방",
        subtitle: "2026-04-01~2026-10-31 · 경복궁 집옥재·팔우정",
        details: { 기간: "2026-04-01~2026-10-31", 장소: "경복궁 집옥재·팔우정" },
        thumbnail_url: "https://example.test/jipokjae.jpg",
        external_url: "https://example.test/jipokjae",
      },
      {
        // 서울시 응답에 THUMBNAIL이 비는 경우가 있다 — 사진 없음 자리표시를 보여준다.
        title: "수문장 순라의식",
        subtitle: "2026-03-01~2026-11-29 · 경복궁 내부",
        details: { 기간: "2026-03-01~2026-11-29", 장소: "경복궁 내부" },
        thumbnail_url: null,
        external_url: null,
      },
    ],
  };

  renderWithTrip(<PlaceInfoCard card={eventsCard} />);

  expect(
    screen.getByRole("link", { name: "집옥재 작은 도서관 개방 행사 정보 보기" }).querySelector("img"),
  ).toHaveAttribute("src", "https://example.test/jipokjae.jpg");
  expect(screen.getByText("집옥재 작은 도서관 개방")).toBeInTheDocument();
  expect(
    screen.getByText("2026-04-01~2026-10-31 · 경복궁 집옥재·팔우정"),
  ).toBeInTheDocument();
  // 링크가 없는 행사는 사진 없음 자리표시만 보이고 누를 수 없다.
  expect(screen.getByText("수문장 순라의식")).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "수문장 순라의식 행사 정보 보기" })).toBeNull();

  await user.click(screen.getByRole("link", { name: "집옥재 작은 도서관 개방 행사 정보 보기" }));
  expect(openSpy).toHaveBeenCalledWith("https://example.test/jipokjae", "_blank", "noopener");
  openSpy.mockRestore();
});

it("실시간 지하철 도착은 호선 색으로 묶고, 상행·하행을 별도 칸으로 나눈다", () => {
  const subwayCard: InfoPlaceCardData = {
    ...card,
    question_type: "realtime_subway",
    answer_fields: {
      "종로3가 3호선 · 상행": "대화행 · 상행 · 9분 30초 후 (옥수)",
      "종로3가 3호선 · 하행": "오금행 · 하행 · 도착 정보 미제공",
    },
    thumbnail_url: null,
    overview: null,
    operating_hours: null,
    rest_date: null,
    parking: null,
    parking_fee: null,
    fee: null,
    baby_carriage: null,
    credit_card: null,
    restroom: null,
    homepage: null,
    realtime_area_name: "종로3가",
    realtime_observed_at: "9월 2일 16:00",
    realtime_source_url: "https://data.seoul.go.kr/example",
    realtime_detail_items: [
      {
        title: "종로3가 3호선",
        subtitle: "대화행 · 상행 · 9분 30초 후 (옥수)",
        details: { 방면: "상행", 종착역: "대화", "도착 안내": "9분 30초 후 (옥수)" },
        thumbnail_url: null,
        external_url: null,
      },
      {
        title: "종로3가 3호선",
        subtitle: "오금행 · 하행 · 도착 정보 미제공",
        details: { 방면: "하행", 종착역: "오금" },
        thumbnail_url: null,
        external_url: null,
      },
    ],
  };

  renderWithTrip(<PlaceInfoCard card={subwayCard} />);

  // 역+호선은 한 번만 나오고, 상행/하행이 그 아래 별도 칸으로 갈린다.
  expect(screen.getAllByText("종로3가 3호선").length).toBe(1);
  expect(screen.getByText("상행")).toBeInTheDocument();
  expect(screen.getByText("하행")).toBeInTheDocument();
  expect(screen.getByText("대화행")).toBeInTheDocument();
  expect(screen.getByText("오금행")).toBeInTheDocument();
  expect(screen.getByText("9분 30초 후 (옥수)")).toHaveClass("text-emerald-700");
  // 도착 정보가 없으면 초록이 아니라 중립색 칩이다.
  expect(screen.getByText("도착 정보 미제공")).not.toHaveClass("text-emerald-700");
});

describe("근처 공중화장실 카드", () => {
  afterEach(() => {
    clearLocationSettings();
    clearDirectionsOriginCache();
  });

  const toiletCard: InfoPlaceCardData = {
    ...card,
    question_type: "public_toilet",
    place_name: "인사동",
    answer_fields: {
      "인사동마루 신관 개방화장실": "도보 50m · 지금 이용 가능 · 24시간",
      "쌈지길(지하1층)": "도보 60m · 지금은 닫혀 있음 · 10:30~20:30",
    },
    realtime_detail_items: [
      {
        title: "인사동마루 신관 개방화장실",
        subtitle: "도보 50m · 지금 이용 가능 · 24시간",
        details: {
          거리: "도보 50m",
          "개방 여부": "지금 이용 가능",
          개방시간: "24시간",
          주소: "서울특별시 종로구 인사동길 35-4",
          장애인화장실: "남자, 여자",
        },
        thumbnail_url: null,
        external_url: null,
        latitude: 37.57432,
        longitude: 126.98563,
      },
      {
        title: "쌈지길(지하1층)",
        subtitle: "도보 60m · 지금은 닫혀 있음 · 10:30~20:30",
        details: {
          거리: "도보 60m",
          "개방 여부": "지금은 닫혀 있음",
          개방시간: "10:30~20:30",
          주소: "서울특별시 종로구 인사동길 44",
        },
        thumbnail_url: null,
        external_url: null,
        latitude: 37.57411,
        longitude: 126.98527,
      },
    ],
  };

  it("두 곳의 거리·개방 여부를 보여주고 개방 여부에 따라 칩 색이 다르다", () => {
    renderWithTrip(<PlaceInfoCard card={toiletCard} />);

    expect(screen.getByText("인사동마루 신관 개방화장실")).toBeInTheDocument();
    expect(screen.getByText("쌈지길(지하1층)")).toBeInTheDocument();
    expect(screen.getByText("도보 50m")).toBeInTheDocument();
    // 열린 곳은 초록, 닫힌 곳은 경고색이라 눈으로 바로 갈린다.
    expect(screen.getByText("지금 이용 가능")).toHaveClass("text-calm");
    expect(screen.getByText("지금은 닫혀 있음")).toHaveClass("text-rust");
  });

  it("좌표와 출발지가 있으면 카드를 눌러 도보 길찾기를 연다", async () => {
    const user = userEvent.setup();
    // 위치 설정에 출발지를 심는다 — 이게 없으면 주소 검색으로 폴백한다.
    setLocationOrigin("인사동");
    vi.mocked(searchPlaces).mockResolvedValue({
      places: [
        {
          name: "인사동",
          address: null,
          road_address: null,
          category: null,
          latitude: 37.5739,
          longitude: 126.9852,
        },
      ],
      outside_service_area_count: 0,
    });
    renderWithTrip(<PlaceInfoCard card={toiletCard} />);

    await user.click(
      screen.getByRole("button", { name: "인사동마루 신관 개방화장실까지 네이버 지도 도보 길찾기" }),
    );

    await waitFor(() => expect(openNaverDirections).toHaveBeenCalledWith(
      expect.objectContaining({
        destLat: 37.57432,
        destLng: 126.98563,
        destName: "인사동마루 신관 개방화장실",
        // 화장실은 걸어서 가므로 대중교통이 아니다.
        mode: "walk",
      }),
    ));
  });

  it("출발지가 없으면 주소로 지도 검색을 폴백한다", async () => {
    const user = userEvent.setup();
    renderWithTrip(<PlaceInfoCard card={toiletCard} />);

    await user.click(
      screen.getByRole("button", { name: "쌈지길(지하1층)까지 네이버 지도 도보 길찾기" }),
    );

    expect(openNaverMapSearch).toHaveBeenCalledWith("서울특별시 종로구 인사동길 44");
  });
});

describe("후기 답변의 출처", () => {
  const reviewCard: InfoPlaceCardData = {
    ...card,
    question_type: "review_opinion",
    answer_fields: {},
    review_sources: [
      {
        text: "야간개장 때 조명이 들어오니 낮과는 또 다른 분위기였어요",
        url: "https://blog.naver.com/a/1",
        source_type: "naver_post",
        published_at: "2026-05-01",
      },
      {
        text: "걷는 구간이 길어 편한 신발을 권해요",
        url: null,
        source_type: "google_review",
        published_at: null,
      },
    ],
  };

  it("근거가 된 후기를 인용으로 보여주고 원문 링크를 건다", () => {
    renderWithTrip(<PlaceInfoCard card={reviewCard} />);

    expect(screen.getByText("물어보신 내용과 관련된 경복궁 후기예요")).toBeInTheDocument();
    expect(
      screen.getByText(/야간개장 때 조명이 들어오니 낮과는 또 다른 분위기였어요/),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /네이버 블로그/ })).toHaveAttribute(
      "href",
      "https://blog.naver.com/a/1",
    );
  });

  it("링크가 없는 후기도 인용은 남기되 링크를 만들지 않는다", () => {
    renderWithTrip(<PlaceInfoCard card={reviewCard} />);

    expect(screen.getByText(/걷는 구간이 길어 편한 신발을 권해요/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Google 리뷰/ })).not.toBeInTheDocument();
    expect(screen.getByText(/Google 리뷰/)).toBeInTheDocument();
  });

  it("근거가 없으면 구획째 그리지 않는다", () => {
    renderWithTrip(<PlaceInfoCard card={{ ...reviewCard, review_sources: [] }} />);

    expect(screen.queryByText(/후기예요/)).not.toBeInTheDocument();
  });
});
