/*
 * 역할: 상세 모달의 사진 영역이 여러 장·한 장·없음 세 경우를 각각 어떻게 그리는지 검증한다.
 *
 * 사진 목록(place_image_embeddings)이 있는 장소는 전체의 30%뿐이라, 나머지에서
 * 대표 이미지 한 장이 그대로 나오는지가 갤러리 자체만큼 중요하다.
 */

import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { fetchPlaceAiReason, fetchRecommendationPlaceDetails, searchPlaces } from "../../api/trip";
import { clearDirectionsOriginCache } from "../../hooks/useNaverDirections";
import {
  clearLocationSettings,
  setLocationCenter,
  setLocationOrigin,
} from "../../state/locationSettings";
import { openNaverDirections } from "../../utils/naverDirections";
import { TripProvider } from "../../state/TripContext";
import type {
  InfoPlaceCard,
  RecommendationItem,
  RecommendationPlaceDetailResponse,
} from "../../types";
import { RecommendationDetailPreviewModal } from "./RecommendationDetailPreviewModal";

vi.mock("../../api/trip", () => ({
  fetchRecommendationPlaceDetails: vi.fn(),
  fetchPlaceAiReason: vi.fn(),
  // 길찾기 훅이 위치 설정의 출발지 이름을 좌표로 풀 때 부른다.
  searchPlaces: vi.fn(),
}));

/* 링크를 여는 두 함수만 가로채고 나머지는 진짜를 쓴다. */
vi.mock("../../utils/naverDirections", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../utils/naverDirections")>()),
  openNaverMapSearch: vi.fn(),
  openNaverDirections: vi.fn(),
}));

const mockedFetch = vi.mocked(fetchRecommendationPlaceDetails);
const mockedReason = vi.mocked(fetchPlaceAiReason);
const mockedDirections = vi.mocked(openNaverDirections);

/* 길찾기 버튼은 출발지가 있어야 나온다. 위치 설정에 출발지 이름을 넣고, 그 이름이
   풀릴 좌표를 장소 검색 mock에 심는다. */
function seedOrigin() {
  setLocationOrigin("시청역");
  vi.mocked(searchPlaces).mockResolvedValue({
    places: [
      {
        name: "시청역",
        address: null,
        road_address: null,
        category: null,
        latitude: 37.5665,
        longitude: 126.978,
      },
    ],
    outside_service_area_count: 0,
  });
}

function card(overrides: Partial<InfoPlaceCard> = {}): InfoPlaceCard {
  return {
    question_type: "general_info",
    answer_fields: {},
    place_id: "126508",
    place_name: "경복궁",
    latitude: null,
    longitude: null,
    thumbnail_url: "https://example.test/gyeongbokgung.jpg",
    overview: "조선 왕조의 법궁이다.",
    operating_hours: "09:00~18:00",
    rest_date: null,
    parking: null,
    parking_fee: null,
    fee: null,
    baby_carriage: null,
    pet: null,
    credit_card: null,
    restroom: null,
    homepage: null,
    ...overrides,
  };
}

/** 모달은 열리자마자 상세를 보강 조회한다. 조회 결과가 화면에 그려지는 값이다. */
function renderModal(resolved: InfoPlaceCard) {
  mockedFetch.mockResolvedValue({
    status: "success",
    requested_place_id: resolved.place_id,
    place_card: resolved,
  });
  return render(
    <RecommendationDetailPreviewModal placeId="126508" placeName="경복궁" onClose={() => {}} />,
    { wrapper: TripProvider },
  );
}

function recommendationItem(overrides: Partial<RecommendationItem> = {}): RecommendationItem {
  return {
    place_id: "126508",
    name: "경복궁",
    category: "고궁",
    distance_km: 1.2,
    remaining_minutes: 90,
    operating_hours_display: "09:00~18:00",
    environment_type: "outdoor",
    recommendation_reason: "",
    explanations: [],
    warnings: [],
    score: 0,
    feature_scores: {},
    weights_used: {},
    taste_evidence: [],
    ...overrides,
  };
}

beforeEach(() => {
  mockedFetch.mockReset();
  // 문장 호출은 대부분의 테스트에서 관심 밖이다. 기본은 "문장 없음"으로 두고,
  // 문장을 보는 테스트만 각자 덮어쓴다.
  mockedReason.mockReset();
  mockedReason.mockResolvedValue({ ai_reason: null });
  mockedDirections.mockReset();
  sessionStorage.clear();
  clearLocationSettings();
  clearDirectionsOriginCache();
});

/*
 * 대표 이미지(thumbnail_url)를 목록 맨 앞에 함께 보여준다. 사진 목록이 있는
 * 6,830곳 중 44%는 대표 이미지가 목록에 없어(2026-09-03 실측), 목록만 그리면
 * 카드에서 보고 눌러 들어온 사진이 상세에서 사라졌다.
 */
it("대표 이미지를 사진 목록 맨 앞에 함께 보여준다", async () => {
  renderModal(
    card({
      photos: [
        { url: "https://tong.visitkorea.or.kr/126508-1.jpg", image_name: "경복궁 (1)" },
        { url: "https://tong.visitkorea.or.kr/126508-2.jpg", image_name: null },
        { url: "https://tong.visitkorea.or.kr/126508-3.jpg", image_name: null },
      ],
    }),
  );

  /* 카드에서 방금 본 사진이 그대로 첫 장이다 — 뒤에 붙이면 상세를 열 때 화면이
     다른 사진으로 갈아치워진 것처럼 보인다. */
  const main = await screen.findByRole("img", { name: "경복궁 사진 1번째" });
  expect(main).toHaveAttribute("src", "https://example.test/gyeongbokgung.jpg");
  expect(screen.getByText("1 / 4")).toBeInTheDocument();

  const list = screen.getByRole("group", { name: "경복궁 사진 목록" });
  expect(within(list).getAllByRole("button")).toHaveLength(4);
});

it("대표 이미지가 목록에도 있으면 두 번 보여주지 않는다", async () => {
  renderModal(
    card({
      thumbnail_url: "https://tong.visitkorea.or.kr/126508-1.jpg",
      photos: [
        { url: "https://tong.visitkorea.or.kr/126508-1.jpg", image_name: null },
        { url: "https://tong.visitkorea.or.kr/126508-2.jpg", image_name: null },
      ],
    }),
  );

  await screen.findByRole("img", { name: "경복궁 사진 1번째" });
  expect(screen.getByText("1 / 2")).toBeInTheDocument();
});

/*
 * 같은 파일을 스킴만 다르게 가리키는 장소가 108곳이다(2026-09-03 실측).
 * places.first_image_url은 http로 적재됐고 place_image_embeddings.origin_url은
 * https다 — 문자열로만 비교하면 그 장소들에서 같은 사진이 두 번 나온다.
 */
it("http와 https만 다른 같은 사진은 한 장으로 본다", async () => {
  renderModal(
    card({
      thumbnail_url: "http://tong.visitkorea.or.kr/cms/resource/15/1868115_image2_1.jpg",
      photos: [
        {
          url: "https://tong.visitkorea.or.kr/cms/resource/15/1868115_image2_1.jpg",
          image_name: null,
        },
      ],
    }),
  );

  /* 한 장으로 합쳐졌으므로 갤러리가 아니라 단일 이미지로 그려진다. */
  const image = await screen.findByRole("img", { name: "경복궁 이미지" });
  /* 먼저 온 대표 이미지의 URL을 그대로 쓴다. */
  expect(image).toHaveAttribute(
    "src",
    "http://tong.visitkorea.or.kr/cms/resource/15/1868115_image2_1.jpg",
  );
  expect(screen.queryByRole("group", { name: "경복궁 사진 목록" })).not.toBeInTheDocument();
});

it("목록에서 고른 사진이 큰 사진으로 바뀐다", async () => {
  const user = userEvent.setup();
  renderModal(
    /* 대표 이미지가 없는 장소다 — 갤러리 이동만 보기 위해 사진 목록만 둔다. */
    card({
      thumbnail_url: null,
      photos: [
        { url: "https://tong.visitkorea.or.kr/126508-1.jpg", image_name: null },
        { url: "https://tong.visitkorea.or.kr/126508-2.jpg", image_name: null },
      ],
    }),
  );

  await screen.findByRole("img", { name: "경복궁 사진 1번째" });
  await user.click(screen.getByRole("button", { name: "경복궁 사진 2번째 보기" }));

  const main = screen.getByRole("img", { name: "경복궁 사진 2번째" });
  expect(main).toHaveAttribute("src", "https://tong.visitkorea.or.kr/126508-2.jpg");
  expect(screen.getByText("2 / 2")).toBeInTheDocument();
});

/*
 * 큰 사진을 손가락으로 밀어도 넘어간다. 작은 사진 줄은 그대로 두는데, 그쪽이
 * 키보드·스크린리더로 고를 수 있는 유일한 길이기 때문이다 — 스와이프는 덤이다.
 */
function swipe(surface: HTMLElement, from: { x: number; y: number }, to: { x: number; y: number }) {
  fireEvent.touchStart(surface, { touches: [{ clientX: from.x, clientY: from.y }] });
  fireEvent.touchEnd(surface, { changedTouches: [{ clientX: to.x, clientY: to.y }] });
}

/**
 * 두 장짜리 갤러리를 띄우고, **밀어도 되는 상태가 될 때까지 기다린 뒤** 미는
 * 면을 돌려준다.
 *
 * 큰 사진이 떴다고 다 끝난 것이 아니다. 상세 응답 뒤에 남은 상태 갱신이 흘러가는
 * 중에 밀면 그 갱신에 묻혀 첫 장 그대로였다 — 파일 하나만 돌릴 때는 8/8 통과하고
 * 전체 스위트를 병렬로 돌릴 때만 이따금 깨졌다. userEvent를 쓰는 옆 테스트가
 * 멀쩡했던 것은 그쪽이 내부적으로 기다려주기 때문이다.
 */
async function openTwoPhotoGallery() {
  renderModal(
    card({
      thumbnail_url: null,
      photos: [
        { url: "https://tong.visitkorea.or.kr/126508-1.jpg", image_name: null },
        { url: "https://tong.visitkorea.or.kr/126508-2.jpg", image_name: null },
      ],
    }),
  );

  await screen.findByRole("img", { name: "경복궁 사진 1번째" });
  await act(async () => {});
  return screen.getByTestId("photo-swipe-surface");
}

it("큰 사진을 왼쪽으로 밀면 다음 사진으로 넘어간다", async () => {
  const surface = await openTwoPhotoGallery();

  swipe(surface, { x: 240, y: 120 }, { x: 120, y: 126 });

  expect(await screen.findByRole("img", { name: "경복궁 사진 2번째" })).toHaveAttribute(
    "src",
    "https://tong.visitkorea.or.kr/126508-2.jpg",
  );
  expect(screen.getByText("2 / 2")).toBeInTheDocument();
});

it("마지막 사진에서 더 밀어도 처음으로 돌지 않는다", async () => {
  const surface = await openTwoPhotoGallery();

  swipe(surface, { x: 240, y: 120 }, { x: 120, y: 120 });
  await screen.findByRole("img", { name: "경복궁 사진 2번째" });

  // 돌면 사진 위의 "2 / 2"가 갑자기 "1 / 2"로 뛰는 것으로 읽힌다.
  swipe(surface, { x: 240, y: 120 }, { x: 120, y: 120 });
  expect(screen.getByText("2 / 2")).toBeInTheDocument();
});

it("세로로 쓸면 사진을 넘기지 않는다", async () => {
  const surface = await openTwoPhotoGallery();

  // 가로 30px, 세로 140px — 시트를 스크롤하려던 손짓이다.
  swipe(surface, { x: 240, y: 260 }, { x: 210, y: 120 });

  expect(screen.getByText("1 / 2")).toBeInTheDocument();
});

/*
 * 사진이 늦게 도착해도 화면이 밀리지 않아야 한다. 작은 사진 줄(68px)을 있을 때만
 * 그리면 상세를 열 때마다 그만큼 이동하는데, 사진이 두 장 이상인 장소가 38%뿐이라
 * (8,060곳 중 3,059곳, 2026-09-03 실측) 어느 쪽을 기준으로 잡아도 나머지에서 밀린다.
 * 그래서 세 경우(로딩·갤러리·이미지 없음) 모두 같은 높이를 차지한다.
 */
it("로딩 중에도 작은 사진 줄 자리를 미리 잡아 둔다", async () => {
  /* 응답을 붙잡아 두고 로딩 상태를 본다. */
  let resolveDetail!: (value: RecommendationPlaceDetailResponse) => void;
  mockedFetch.mockReturnValue(
    new Promise((resolve) => {
      resolveDetail = resolve;
    }),
  );
  render(
    <RecommendationDetailPreviewModal placeId="126508" placeName="경복궁" onClose={() => {}} />,
    { wrapper: TripProvider },
  );

  expect(screen.getByTestId("photo-strip-slot")).toBeInTheDocument();

  resolveDetail({
    status: "success",
    requested_place_id: "126508",
    place_card: card({
      photos: [
        { url: "https://tong.visitkorea.or.kr/126508-1.jpg", image_name: null },
        { url: "https://tong.visitkorea.or.kr/126508-2.jpg", image_name: null },
      ],
    }),
  });

  await screen.findByRole("group", { name: "경복궁 사진 목록" });
  /* 응답 뒤에도 같은 자리에 같은 높이의 줄이 있다 — 껍데기가 바뀌지 않는다. */
  expect(screen.getAllByTestId("photo-strip-slot")).toHaveLength(1);
});

/*
 * 운영시간은 추천 카드를 만들 때 D가 이미 계산해 item.operating_hours_display에
 * 실어 보낸 값이다 — 카드 목록에서 이미 본 값을, 상세 조회(fetchRecommendationPlaceDetails)
 * 응답을 기다리지 않고 먼저 보여준다.
 */
it("로딩 중에도 이미 아는 운영시간을 먼저 보여준다", async () => {
  let resolveDetail!: (value: RecommendationPlaceDetailResponse) => void;
  mockedFetch.mockReturnValue(
    new Promise((resolve) => {
      resolveDetail = resolve;
    }),
  );
  render(
    <RecommendationDetailPreviewModal
      item={recommendationItem({ operating_hours_display: "09:00~18:00", remaining_minutes: 90 })}
      onClose={() => {}}
    />,
    { wrapper: TripProvider },
  );

  /* 상세 조회 응답이 오기 전인데도 카드가 이미 아는 값이 바로 보인다. */
  expect(await screen.findByText("운영시간")).toBeInTheDocument();
  expect(screen.getByText("09:00~18:00")).toBeInTheDocument();
  /* 영업 상태는 장소명 옆에 있다(TP-248) — 운영시간 줄에 붙어 있으면 아래 표까지
     내려가야 보인다. */
  expect(screen.getByText("영업 중")).toBeInTheDocument();

  resolveDetail({
    status: "success",
    requested_place_id: "126508",
    place_card: card({ operating_hours: "매일 10:00~19:00" }),
  });

  /* 상세 조회 값이 도착하면 그쪽으로 바뀐다 — 미리보기 값이 남아 있지 않는다. */
  await screen.findByText("매일 10:00~19:00");
  expect(screen.queryByText("09:00~18:00")).not.toBeInTheDocument();
  /* 영업 상태는 장소명 옆에 그대로 있다. */
  expect(screen.getByText("영업 중")).toBeInTheDocument();
});

it("상시 개방인 곳은 영업 중이 아니라 24시간 운영이라고 말한다", async () => {
  /* 공원·산책로에 "영업 중"은 장사하는 곳처럼 들린다. 남은 시간이 있어도(상시라
     항상 있다) 그 문구를 쓰지 않는다. */
  mockedFetch.mockReturnValue(new Promise(() => {}));
  render(
    <RecommendationDetailPreviewModal
      item={recommendationItem({ operating_hours_display: "24시간", remaining_minutes: 600 })}
      onClose={() => {}}
    />,
    { wrapper: TripProvider },
  );

  expect(await screen.findByText("24시간 운영")).toBeInTheDocument();
  expect(screen.queryByText("영업 중")).not.toBeInTheDocument();
});

/* INFO·사진 검색 경로는 item 자체가 없어 참고할 값이 없다 — 근거 없이 지어내지 않는다. */
it("운영시간을 미리 알 수 없으면 미리보기를 그리지 않는다", async () => {
  mockedFetch.mockReturnValue(new Promise(() => {}));
  render(
    <RecommendationDetailPreviewModal placeId="126508" placeName="경복궁" onClose={() => {}} />,
    { wrapper: TripProvider },
  );

  expect(screen.queryByText("운영시간")).not.toBeInTheDocument();
});

/*
 * item.image_url도 operating_hours_display와 같은 이유로 이미 아는 값이다 —
 * 상세 조회를 기다리지 않고 카드에서 본 그 사진을 먼저 보여준다.
 */
/*
 * 큰 사진과 운영시간을 카드에서 이미 아는 값으로 먼저 채우고 나면, 상세 조회가
 * 여전히 진행 중이라는 사실을 알려줄 곳이 작은 사진 줄밖에 남지 않는다. 그래서
 * 첫 칸에 회전하는 아이콘을 얹는다.
 */
it("상세 조회가 끝나기 전엔 작은 사진 줄 첫 칸에 로딩 아이콘이 돈다", async () => {
  mockedFetch.mockReturnValue(new Promise(() => {}));
  render(
    <RecommendationDetailPreviewModal
      item={recommendationItem({ image_url: "https://example.test/card-thumbnail.jpg" })}
      onClose={() => {}}
    />,
    { wrapper: TripProvider },
  );

  expect(await screen.findByTestId("photo-strip-loading-spinner")).toBeInTheDocument();
});

it("로딩 중에도 이미 아는 대표 사진을 먼저 보여준다", async () => {
  let resolveDetail!: (value: RecommendationPlaceDetailResponse) => void;
  mockedFetch.mockReturnValue(
    new Promise((resolve) => {
      resolveDetail = resolve;
    }),
  );
  render(
    <RecommendationDetailPreviewModal
      item={recommendationItem({ image_url: "https://example.test/card-thumbnail.jpg" })}
      onClose={() => {}}
    />,
    { wrapper: TripProvider },
  );

  /* 상세 조회 응답이 오기 전인데도 카드가 이미 아는 사진이 바로 보인다. */
  const preview = await screen.findByRole("img", { name: "경복궁 이미지" });
  expect(preview).toHaveAttribute("src", "https://example.test/card-thumbnail.jpg");
  expect(screen.queryByText("상세 정보를 불러오는 중...")).not.toBeInTheDocument();

  resolveDetail({
    status: "success",
    requested_place_id: "126508",
    place_card: card({ thumbnail_url: "https://example.test/detail-thumbnail.jpg", photos: [] }),
  });

  /* 상세 조회 값이 도착하면 그쪽 사진으로 바뀐다 — 카드 사진이 남아 있지 않는다. */
  await waitFor(() =>
    expect(screen.getByRole("img", { name: "경복궁 이미지" })).toHaveAttribute(
      "src",
      "https://example.test/detail-thumbnail.jpg",
    ),
  );
});

/*
 * 카드 썸네일(작은 사진)과 상세 사진(원본 크기)은 같은 장소라도 화질·크롭이
 * 다르다 — 카드는 recommendation_cards.py가, 상세는 hybrid_place_details.py가
 * 서로 반대 우선순위로 고르기 때문이다. 그대로 바꿔치우면 사라졌다 나타나는
 * 것처럼 번쩍인다. 카드 썸네일을 흐리게 계속 깔아 둬서 "흐리다가 선명해진다"로
 * 읽히게 한다.
 */
it("상세 사진으로 바뀔 때 카드 썸네일을 흐리게 깔아 자연스럽게 잇는다", async () => {
  let resolveDetail!: (value: RecommendationPlaceDetailResponse) => void;
  mockedFetch.mockReturnValue(
    new Promise((resolve) => {
      resolveDetail = resolve;
    }),
  );
  render(
    <RecommendationDetailPreviewModal
      item={recommendationItem({ image_url: "https://example.test/card-thumbnail.jpg" })}
      onClose={() => {}}
    />,
    { wrapper: TripProvider },
  );
  await screen.findByRole("img", { name: "경복궁 이미지" });
  /* 로딩 중에는 이어 붙일 상세 사진이 아직 없어 흐린 배경이 필요 없다. */
  expect(screen.queryByTestId("photo-blur-placeholder")).not.toBeInTheDocument();

  resolveDetail({
    status: "success",
    requested_place_id: "126508",
    place_card: card({ thumbnail_url: "https://example.test/detail-thumbnail.jpg", photos: [] }),
  });
  await waitFor(() =>
    expect(screen.getByRole("img", { name: "경복궁 이미지" })).toHaveAttribute(
      "src",
      "https://example.test/detail-thumbnail.jpg",
    ),
  );

  /* 상세 사진이 자리를 넘겨받은 뒤에도 카드 썸네일이 흐린 배경으로 함께 있다 —
     빈 회색 칸으로 뚝 끊기지 않는다. */
  expect(screen.getByTestId("photo-blur-placeholder")).toHaveAttribute(
    "src",
    "https://example.test/card-thumbnail.jpg",
  );
});

it("대표 사진을 미리 알 수 없으면 로딩 문구를 그대로 보여준다", async () => {
  mockedFetch.mockReturnValue(new Promise(() => {}));
  render(
    <RecommendationDetailPreviewModal placeId="126508" placeName="경복궁" onClose={() => {}} />,
    { wrapper: TripProvider },
  );

  expect(await screen.findByText("상세 정보를 불러오는 중...")).toBeInTheDocument();
});

it("사진이 한 장뿐이어도 작은 사진 줄 자리는 그대로 남는다", async () => {
  renderModal(card({ photos: [] }));

  await screen.findByRole("img", { name: "경복궁 이미지" });

  /* 고를 사진이 없어 버튼은 없지만, 자리는 남아 있어야 화면이 밀리지 않는다. */
  expect(screen.getAllByTestId("photo-strip-slot")).toHaveLength(1);
  expect(screen.queryByRole("group", { name: "경복궁 사진 목록" })).not.toBeInTheDocument();
});

it("사진 목록이 비면 대표 이미지 한 장을 그대로 보여준다", async () => {
  renderModal(card({ photos: [] }));

  const image = await screen.findByRole("img", { name: "경복궁 이미지" });
  expect(image).toHaveAttribute("src", "https://example.test/gyeongbokgung.jpg");
  // 한 장뿐이면 고를 것이 없으므로 목록과 장수 표시를 만들지 않는다.
  expect(screen.queryByRole("group", { name: "경복궁 사진 목록" })).not.toBeInTheDocument();
  expect(screen.queryByText("1 / 1")).not.toBeInTheDocument();
});

it("닫기 버튼을 누르면 슬라이드다운이 끝난 뒤 onClose를 부른다", async () => {
  const user = userEvent.setup();
  const onClose = vi.fn();
  mockedFetch.mockResolvedValue({
    status: "success",
    requested_place_id: "126508",
    place_card: card(),
  });
  render(
    <RecommendationDetailPreviewModal placeId="126508" placeName="경복궁" onClose={onClose} />,
    { wrapper: TripProvider },
  );

  await screen.findByRole("heading", { name: "경복궁" });
  await user.click(screen.getByRole("button", { name: "상세 창 닫기" }));

  await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
});

it("사진도 대표 이미지도 없으면 안내 문구를 보여준다", async () => {
  renderModal(card({ photos: [], thumbnail_url: null }));

  await waitFor(() => {
    expect(screen.getByText("등록된 이미지가 없어요.")).toBeInTheDocument();
  });
  expect(screen.queryByRole("img", { name: /경복궁/ })).not.toBeInTheDocument();
});

/*
 * 카드 이미지가 없는 장소 844곳 중 843곳(99.9%)은 상세 사진도 0장이다(2026-09-05
 * 실측). 열 때 이미 아는 사실이므로 응답을 기다렸다가 자리를 접지 않는다 —
 * 기다렸다 접으면 그 844곳이 전부 밀린다.
 */
it("카드에 이미지가 없으면 사진 영역을 처음부터 그리지 않는다", async () => {
  mockedFetch.mockReturnValue(new Promise(() => {}));
  render(
    <RecommendationDetailPreviewModal
      item={recommendationItem({ image_url: null })}
      onClose={() => {}}
    />,
    { wrapper: TripProvider },
  );

  await screen.findByRole("heading", { name: "경복궁" });

  expect(screen.queryByTestId("photo-strip-slot")).not.toBeInTheDocument();
  expect(screen.queryByText("상세 정보를 불러오는 중...")).not.toBeInTheDocument();
});

it("카드에 이미지가 없으면 응답이 와도 사진 영역이 생기지 않는다", async () => {
  let resolveDetail!: (value: RecommendationPlaceDetailResponse) => void;
  mockedFetch.mockReturnValue(
    new Promise((resolve) => {
      resolveDetail = resolve;
    }),
  );
  render(
    <RecommendationDetailPreviewModal
      item={recommendationItem({ image_url: null })}
      onClose={() => {}}
    />,
    { wrapper: TripProvider },
  );

  resolveDetail({
    status: "success",
    requested_place_id: "126508",
    place_card: card({ photos: [], thumbnail_url: null }),
  });

  await screen.findByRole("heading", { name: "경복궁" });

  // 로딩 전후로 같은 모양이라 화면이 밀리지 않는다.
  expect(screen.queryByTestId("photo-strip-slot")).not.toBeInTheDocument();
  expect(screen.queryByText("등록된 이미지가 없어요.")).not.toBeInTheDocument();
});

/*
 * 시트 높이는 내용과 무관하게 고정이다.
 *
 * 내용 높이로 정하면 상세 응답이 도착할 때 화면이 위로 자란다 — 개요·관련 정보는
 * 응답 전에 자리를 잡을 수 없기 때문이다. 사진 영역이 있던 시절에는 그 283px이
 * 로딩 시점에 상한을 채워 성장이 스크롤로 흡수됐을 뿐이다.
 */
it("상세 응답 전후로 시트 높이가 바뀌지 않는다", async () => {
  let resolveDetail!: (value: RecommendationPlaceDetailResponse) => void;
  mockedFetch.mockReturnValue(
    new Promise((resolve) => {
      resolveDetail = resolve;
    }),
  );
  render(
    <RecommendationDetailPreviewModal
      item={recommendationItem({ image_url: "https://example.test/card-thumbnail.jpg" })}
      onClose={() => {}}
    />,
    { wrapper: TripProvider },
  );

  const sheet = screen.getByTestId("place-detail-sheet");
  const before = sheet.className;

  resolveDetail({
    status: "success",
    requested_place_id: "126508",
    place_card: card({
      overview: "긴 개요 문장이 응답과 함께 도착한다. ".repeat(20),
      photos: [{ url: "https://tong.visitkorea.or.kr/126508-1.jpg", image_name: null }],
    }),
  });

  await screen.findByRole("heading", { name: "경복궁" });

  expect(sheet.className).toBe(before);
  expect(sheet.className).toContain("h-[88vh]");
  // max-h로 두면 내용이 상한 밑일 때 시트가 내용만큼만 커져서 성장이 보인다.
  expect(sheet.className).not.toContain("max-h-[88vh]");
});

it("사진 영역이 없는 장소도 같은 높이로 열린다", async () => {
  mockedFetch.mockReturnValue(new Promise(() => {}));
  render(
    <RecommendationDetailPreviewModal
      item={recommendationItem({ image_url: null })}
      onClose={() => {}}
    />,
    { wrapper: TripProvider },
  );

  const sheet = screen.getByTestId("place-detail-sheet");
  // "h-[88vh]"는 "max-h-[88vh]"의 부분 문자열이라 둘 다 본다.
  expect(sheet.className).toContain("h-[88vh]");
  expect(sheet.className).not.toContain("max-h-[88vh]");
  // 사진 영역을 접은 것이 시트 크기까지 줄이지는 않는다 — 정보가 위로 올라올 뿐이다.
  expect(screen.queryByTestId("photo-strip-slot")).not.toBeInTheDocument();
});

it("카드에 이미지가 없어도 상세에 사진이 있으면 갤러리를 보여준다", async () => {
  /*
   * 카드 이미지가 없는 844곳 중 1곳은 상세 사진이 있다. 미리 접어 둔 자리가
   * 그때는 펴져야 한다 — 밀리더라도 사진을 감추는 것보다 낫다.
   */
  mockedFetch.mockResolvedValue({
    status: "success",
    requested_place_id: "126508",
    place_card: card({ photos: [], thumbnail_url: "https://example.test/found.jpg" }),
  });
  render(
    <RecommendationDetailPreviewModal
      item={recommendationItem({ image_url: null })}
      onClose={() => {}}
    />,
    { wrapper: TripProvider },
  );

  expect(await screen.findByRole("img", { name: "경복궁 이미지" })).toBeInTheDocument();
  expect(screen.getByTestId("photo-strip-slot")).toBeInTheDocument();
});

it("미리 알 수 없는 경로(사진 유사 검색·지난 추천)는 종전대로 자리를 잡아 둔다", async () => {
  mockedFetch.mockReturnValue(new Promise(() => {}));
  render(
    <RecommendationDetailPreviewModal placeId="126508" placeName="경복궁" onClose={() => {}} />,
    { wrapper: TripProvider },
  );

  // item도 card도 없으면 판단 근거가 없다 — 접었다 펴는 것보다 잡아 두는 쪽이 낫다.
  expect(screen.getByTestId("photo-strip-slot")).toBeInTheDocument();
});

it("카드에 이미지가 없어도 상세 조회 실패는 알린다", async () => {
  mockedFetch.mockResolvedValue({
    status: "unavailable",
    requested_place_id: "126508",
    place_card: null,
  });
  render(
    <RecommendationDetailPreviewModal
      item={recommendationItem({ image_url: null })}
      onClose={() => {}}
    />,
    { wrapper: TripProvider },
  );

  // 사진이 없는 것과 못 불러온 것은 다르다. 오류는 자리를 차지하더라도 보여준다.
  expect(await screen.findByText("상세 정보를 불러오지 못했어요.")).toBeInTheDocument();
});

it("무장애 값이 있으면 편의시설 구획으로 그린다", async () => {
  renderModal(
    card({
      accessible_restroom: "장애인 화장실 있음(1층)",
      elevator: "엘리베이터 있음",
      guide_dog: "보조견 동반 가능함",
    }),
  );

  const heading = await screen.findByText("편의시설");
  const section = heading.parentElement as HTMLElement;
  expect(within(section).getByText("장애인 화장실")).toBeInTheDocument();
  expect(within(section).getByText("장애인 화장실 있음(1층)")).toBeInTheDocument();
  expect(within(section).getByText("승강기")).toBeInTheDocument();
  expect(within(section).getByText("보조견 동반")).toBeInTheDocument();
  // 값이 없는 항목은 줄 자체가 없다. 빈 값을 "없음"으로 그리면 있는 시설을
  // 없다고 말하게 된다.
  expect(within(section).queryByText("휠체어 대여")).not.toBeInTheDocument();
  expect(within(section).queryByText("수유·기저귀")).not.toBeInTheDocument();
});

/*
 * 접기(TP-248). jsdom에는 레이아웃이 없어 line-clamp가 실제로 자르는지는 잴 수 없다.
 * 대신 **언제 "더 보기"를 띄울지**를 못 박는다 — 짧은 값에도 버튼이 붙으면 아홉 줄
 * 대부분에 쓸모없는 버튼이 생긴다.
 *
 * 넘침 판정은 scrollHeight > clientHeight로 하므로, 그 두 값을 심어 상황을 만든다.
 */
function stubOverflow(scrollHeight: number, clientHeight: number) {
  Object.defineProperty(HTMLElement.prototype, "scrollHeight", {
    configurable: true,
    value: scrollHeight,
  });
  Object.defineProperty(HTMLElement.prototype, "clientHeight", {
    configurable: true,
    value: clientHeight,
  });
}

it("편의시설 값이 두 줄을 넘으면 더 보기로 접는다", async () => {
  stubOverflow(60, 20);
  renderModal(
    card({
      accessible_parking: "장애인 전용 주차장 있음 (1.학여울역 1번출구 기준 왼쪽 3대)",
    }),
  );

  const heading = await screen.findByText("편의시설");
  const section = heading.parentElement as HTMLElement;
  /* 넘침 판정이 effect에서 상태를 바꾸므로 리렌더를 기다린다 — 동기 조회로 찾으면
     간헐적으로 렌더 전에 본다. */
  const more = await within(section).findByRole("button", { name: "더 보기" });

  /* 접혀 있어도 글자는 DOM에 전부 있다 — 자르는 것이 아니라 가리는 것이다.
     낭독기와 브라우저 찾기는 전문을 본다. */
  expect(
    within(section).getByText("장애인 전용 주차장 있음 (1.학여울역 1번출구 기준 왼쪽 3대)"),
  ).toBeInTheDocument();

  await userEvent.click(more);
  expect(within(section).getByRole("button", { name: "접기" })).toBeInTheDocument();
});

it("두 줄에 들어가는 값에는 더 보기를 붙이지 않는다", async () => {
  /* jsdom 기본값은 둘 다 0이라 넘치지 않는 상황이 된다. */
  stubOverflow(0, 0);
  renderModal(card({ accessible_restroom: "1층" }));

  const heading = await screen.findByText("편의시설");
  const section = heading.parentElement as HTMLElement;
  expect(within(section).queryByRole("button", { name: "더 보기" })).not.toBeInTheDocument();
});

it("무장애 값이 하나도 없으면 편의시설 구획을 숨긴다", async () => {
  renderModal(card({ parking: "가능 (240대)" }));

  // 상세가 그려진 뒤에 확인한다 — 조회 전이면 아직 아무 구획도 없다.
  expect(await screen.findByText("주차")).toBeInTheDocument();
  expect(screen.queryByText("편의시설")).not.toBeInTheDocument();
});

it("유모차는 무장애 값과 기존 값이 함께 보이지 않는다", async () => {
  // C가 둘 중 하나만 채워 보낸다(둘 다 있는 34곳 중 21곳에서 서로 반대라서).
  renderModal(card({ baby_carriage: null, stroller_rental: "대여가능(10대)" }));

  const heading = await screen.findByText("편의시설");
  const section = heading.parentElement as HTMLElement;
  expect(within(section).getByText("유모차 대여")).toBeInTheDocument();
  expect(within(section).getByText("대여가능(10대)")).toBeInTheDocument();
  // 위 표의 "유모차" 줄은 값이 비어 나오지 않는다.
  expect(screen.queryByText("유모차")).not.toBeInTheDocument();
});

/*
 * 로딩 자리를 회색 덩어리 하나로 두면 중앙값 0.73초·p90 1.44초(2026-09-05 실측)를
 * 그 상태로 버티게 되고, 값이 도착하는 순간 표가 통째로 나타나 화면이 한 번 튄다.
 * 완성됐을 때와 같은 줄 모양으로 두어 값만 차오르게 한다.
 */
it("상세를 기다리는 동안 표와 같은 줄 모양 스켈레톤을 그린다", async () => {
  mockedFetch.mockReturnValue(new Promise(() => {}));
  render(
    <RecommendationDetailPreviewModal placeId="126508" placeName="경복궁" onClose={() => {}} />,
    { wrapper: TripProvider },
  );

  const skeleton = screen.getByRole("status");
  expect(within(skeleton).getByText("장소 상세 정보를 불러오는 중")).toBeInTheDocument();
  // 완성된 표의 중앙값이 4줄이라 그만큼 잡아 둔다(8,060곳 실측).
  expect(screen.getAllByTestId("info-skeleton-row")).toHaveLength(4);
});

/*
 * 줄 수는 처음부터 최종값이고, 200ms 지연은 회색 바에만 걸린다.
 *
 * 줄 수를 지연에 묶으면 200ms 지점에 표가 1줄(또는 0줄)에서 4줄로 커지면서 개요·
 * 추천 이유 같은 아래 내용이 통째로 밀린다. 지연의 목적("조회가 빨라지면 번쩍이지
 * 않게")은 움직이는 바를 늦추는 것으로 그대로 지켜진다.
 */
it("지연 200ms 동안에도 줄 자리는 그대로 두고 회색 바만 감춘다", async () => {
  mockedFetch.mockReturnValue(new Promise(() => {}));
  render(
    <RecommendationDetailPreviewModal placeId="126508" placeName="경복궁" onClose={() => {}} />,
    { wrapper: TripProvider },
  );

  const rowsBefore = screen.getAllByTestId("info-skeleton-row").length;
  expect(rowsBefore).toBe(4);
  // 지연 중에는 바가 자리만 차지하고 보이지 않는다.
  for (const bar of screen.getAllByTestId("info-skeleton-bar")) {
    expect(bar).toHaveClass("invisible");
  }

  await waitFor(() => {
    expect(screen.getAllByTestId("info-skeleton-bar")[0]).not.toHaveClass("invisible");
  });

  // 바가 켜져도 줄 수는 그대로다 — 이것이 밀림의 직접 원인이었다.
  expect(screen.getAllByTestId("info-skeleton-row")).toHaveLength(rowsBefore);
});

it("item이 없는 경로에서도 표 자리를 처음부터 잡는다", async () => {
  /* 사진 유사 검색·지난 추천은 이름과 place_id만 넘기고 연다. 아는 운영시간이
     없어서 예전에는 지연이 끝날 때까지 상자 자체가 없었고, 200ms에 상자째
     나타나며 아래가 밀렸다. */
  mockedFetch.mockReturnValue(new Promise(() => {}));
  render(
    <RecommendationDetailPreviewModal placeId="126508" placeName="경복궁" onClose={() => {}} />,
    { wrapper: TripProvider },
  );

  expect(screen.getByRole("status")).toBeInTheDocument();
  expect(screen.getAllByTestId("info-skeleton-row")).toHaveLength(4);
});

/* 운영시간은 추천 카드에서 이미 아는 값이라 그 줄만 진짜로 채워져 있다. */
it("운영시간을 이미 알면 스켈레톤을 한 줄 적게 그린다", async () => {
  mockedFetch.mockReturnValue(new Promise(() => {}));
  const { rerender } = render(
    <RecommendationDetailPreviewModal placeId="126508" placeName="경복궁" onClose={() => {}} />,
    { wrapper: TripProvider },
  );
  await screen.findByRole("status");
  expect(screen.getAllByTestId("info-skeleton-row")).toHaveLength(4);

  rerender(
    <RecommendationDetailPreviewModal
      item={recommendationItem({ operating_hours_display: "09:00~18:00" })}
      onClose={() => {}}
    />,
  );

  await screen.findByText("09:00~18:00");
  expect(screen.getAllByTestId("info-skeleton-row")).toHaveLength(3);
  // 운영시간 줄과 스켈레톤이 같은 상자에 있어야 값이 도착할 때 상자 수가 안 바뀐다.
  const skeletonBox = screen.getByRole("status");
  expect(within(skeletonBox).getByText("09:00~18:00")).toBeInTheDocument();
  /* 영업 상태는 그 상자 밖, 장소명 옆이다. */
  expect(within(skeletonBox).queryByText("영업 중")).not.toBeInTheDocument();
  expect(screen.getByText("영업 중")).toBeInTheDocument();
});

it("상세가 도착하면 스켈레톤이 사라지고 실제 표가 남는다", async () => {
  let resolveDetail!: (value: RecommendationPlaceDetailResponse) => void;
  mockedFetch.mockReturnValue(
    new Promise((resolve) => {
      resolveDetail = resolve;
    }),
  );
  render(
    <RecommendationDetailPreviewModal placeId="126508" placeName="경복궁" onClose={() => {}} />,
    { wrapper: TripProvider },
  );
  expect(await screen.findByRole("status")).toBeInTheDocument();

  resolveDetail({
    status: "success",
    requested_place_id: "126508",
    place_card: card({ parking: "가능 (240대)" }),
  });

  expect(await screen.findByText("가능 (240대)")).toBeInTheDocument();
  expect(screen.queryByRole("status")).not.toBeInTheDocument();
});

/*
 * 길찾기 버튼은 스크롤 영역 바깥의 하단 고정 바다. 늦게 생기면 그만큼 본문
 * 높이가 줄며 읽던 자리가 밀린다 — 자리는 먼저 잡되 누르지는 못하게 한다.
 */
it("상세를 기다리는 동안 길찾기 버튼 자리를 잡되 누를 수 없다", async () => {
  seedOrigin();
  mockedFetch.mockReturnValue(new Promise(() => {}));
  const user = userEvent.setup();
  render(
    <RecommendationDetailPreviewModal placeId="126508" placeName="경복궁" onClose={() => {}} />,
    { wrapper: TripProvider },
  );

  const button = await screen.findByRole("button", { name: /네이버 지도로 길찾기/ });
  expect(button).toBeDisabled();

  // 목적지 좌표가 상세 응답에 실려 오므로 그 전에는 열 지도가 없다.
  await user.click(button);
  expect(mockedDirections).not.toHaveBeenCalled();
});

it("상세가 도착하면 길찾기 버튼이 활성화된다", async () => {
  seedOrigin();
  let resolveDetail!: (value: RecommendationPlaceDetailResponse) => void;
  mockedFetch.mockReturnValue(
    new Promise((resolve) => {
      resolveDetail = resolve;
    }),
  );
  const user = userEvent.setup();
  render(
    <RecommendationDetailPreviewModal placeId="126508" placeName="경복궁" onClose={() => {}} />,
    { wrapper: TripProvider },
  );
  expect(await screen.findByRole("button", { name: /네이버 지도로 길찾기/ })).toBeDisabled();

  resolveDetail({
    status: "success",
    requested_place_id: "126508",
    place_card: card({ latitude: 37.5796, longitude: 126.977 }),
  });

  const button = await screen.findByRole("button", { name: /네이버 지도로 길찾기/ });
  await waitFor(() => expect(button).toBeEnabled());
  await user.click(button);
  await waitFor(() =>
    expect(mockedDirections).toHaveBeenCalledWith(
      expect.objectContaining({
        origin: { lat: 37.5665, lng: 126.978, name: "시청역" },
        destLat: 37.5796,
        destLng: 126.977,
      }),
    ),
  );
});

/*
 * 출발지가 없으면 길찾기 대신 출발지를 정하러 가는 자리로 쓴다.
 *
 * 자리째 숨기면 사용자는 버튼이 왜 없는지 알 수 없다. 기기 위치는 받지 않으므로
 * "현재 위치 사용" 대신 위치 설정 화면으로 보낸다.
 */
function renderModalInRouter(onClose: () => void = () => {}) {
  mockedFetch.mockResolvedValue({
    status: "success",
    requested_place_id: "126508",
    place_card: card({ latitude: 37.5796, longitude: 126.977 }),
  });
  return render(
    <MemoryRouter initialEntries={["/chat"]}>
      <Routes>
        <Route
          path="/chat"
          element={
            <RecommendationDetailPreviewModal placeId="126508" placeName="경복궁" onClose={onClose} />
          }
        />
        <Route path="/location" element={<div>위치 설정 화면</div>} />
      </Routes>
    </MemoryRouter>,
    { wrapper: TripProvider },
  );
}

it("출발지도 검색 위치도 없으면 길찾기 대신 출발지 정하기를 보여준다", async () => {
  const getCurrentPosition = vi.fn();
  vi.stubGlobal("navigator", { geolocation: { getCurrentPosition } });
  renderModalInRouter();

  expect(await screen.findByRole("button", { name: "출발지 정하기" })).toBeInTheDocument();
  expect(screen.getByText("출발지를 정하면 길을 안내해 드릴 수 있어요")).toBeInTheDocument();
  /* 출발지가 없으면 길찾기는 열 수 없으므로 그 버튼은 없다. */
  expect(screen.queryByRole("button", { name: /네이버 지도로 길찾기/ })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /현재 위치 사용/ })).not.toBeInTheDocument();
  /* 기기 위치는 어떤 경로로도 묻지 않는다. */
  expect(getCurrentPosition).not.toHaveBeenCalled();
  vi.unstubAllGlobals();
});

it("출발지 정하기를 누르면 모달을 닫고 위치 설정(/location)으로 간다", async () => {
  const onClose = vi.fn();
  const user = userEvent.setup();
  renderModalInRouter(onClose);

  await user.click(await screen.findByRole("button", { name: "출발지 정하기" }));

  expect(onClose).toHaveBeenCalledOnce();
  expect(await screen.findByText("위치 설정 화면")).toBeInTheDocument();
});

it("출발지 없이 검색 위치만 있으면 출발지 정하기 대신 길찾기를 보여준다", async () => {
  /* 추천을 받은 뒤의 흔한 상태다 — 대화에서 말한 장소가 검색 위치로 저장된다. 이때
     거리는 검색 위치에서 재므로 길찾기도 거기서 출발한다(useNaverDirections). */
  setLocationCenter("인사동");
  renderModalInRouter();

  expect(
    await screen.findByRole("button", { name: /네이버 지도로 길찾기/ }),
  ).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "출발지 정하기" })).not.toBeInTheDocument();
});

/*
 * 스켈레톤 행과 실제 행이 같은 뼈대(InfoRowShell)에서 나와야 값이 도착할 때 표
 * 높이가 그대로다. 한때 두 곳에 따로 적혀 있어 스켈레톤이 7px 낮았고, 표가 그만큼
 * 늘어나며 아래 내용이 밀렸다. jsdom은 실제 높이를 재지 못하므로 뼈대가 같은지로
 * 지킨다.
 */
it("스켈레톤 행과 실제 행이 같은 뼈대를 쓴다", async () => {
  let resolveDetail!: (value: RecommendationPlaceDetailResponse) => void;
  mockedFetch.mockReturnValue(
    new Promise((resolve) => {
      resolveDetail = resolve;
    }),
  );
  render(
    <RecommendationDetailPreviewModal placeId="126508" placeName="경복궁" onClose={() => {}} />,
    { wrapper: TripProvider },
  );

  await screen.findByRole("status");
  const skeletonRowClass = screen.getAllByTestId("info-skeleton-row")[0].className;

  resolveDetail({
    status: "success",
    requested_place_id: "126508",
    place_card: card({ parking: "가능 (240대)" }),
  });

  const realRow = (await screen.findAllByTestId("info-row"))[0];
  expect(realRow.className).toBe(skeletonRowClass);
});


/*
 * 실시간 도시데이터 INFO는 지역 단위 데이터라 카드에 목적지 좌표가 없다. 관광 상세로
 * 보강하지도 않는다 — needsDetailEnrichment가 지도·목록이 있으면 막는다.
 *
 * 그런 카드에서 하단 바를 띄우면, 출발지를 정해도 갈 곳이 없어 길찾기가 끝내 안 나온다.
 */
it("목적지가 없는 실시간 카드에는 길찾기 바를 띄우지 않는다", async () => {
  seedOrigin();
  const districtCard = card({
    question_type: "concentration",
    place_name: "종로구",
    latitude: null,
    longitude: null,
    realtime_area_name: "종로구",
    realtime_detail_items: [
      {
        title: "약간 붐빔 6곳",
        subtitle: "붐빈다고 느낄 수 있어요.",
        details: { 지역: "경복궁" },
        thumbnail_url: null,
        external_url: null,
      },
    ],
  });
  render(
    <RecommendationDetailPreviewModal card={districtCard} onClose={() => {}} />,
    { wrapper: TripProvider },
  );

  expect(await screen.findByText("약간 붐빔 6곳")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /네이버 지도로 길찾기/ })).not.toBeInTheDocument();
});

it("출발지가 없어도 목적지가 없으면 출발지 정하기를 권하지 않는다", async () => {
  const districtCard = card({
    question_type: "concentration",
    place_name: "종로구",
    latitude: null,
    longitude: null,
    realtime_area_name: "종로구",
    realtime_detail_items: [
      {
        title: "보통 4곳",
        subtitle: "크게 붐비지는 않아요.",
        details: { 지역: "보신각" },
        thumbnail_url: null,
        external_url: null,
      },
    ],
  });
  render(
    <RecommendationDetailPreviewModal card={districtCard} onClose={() => {}} />,
    { wrapper: TripProvider },
  );

  expect(await screen.findByText("보통 4곳")).toBeInTheDocument();
  expect(screen.queryByText(/출발지를 정하면/)).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "출발지 정하기" })).not.toBeInTheDocument();
});

// --- "AI가 추천하는 이유" 두 번째 줄 (recommend.place_reason) ------------------
//
// 문장은 상세조회와 **다른 호출**로 받는다(fetchPlaceAiReason). 한 호출에 묶으면
// 문장을 기다리는 동안 주소·운영시간·사진이 통째로 안 나온다.

it("상세 카드는 문장을 기다리지 않는다 — 문장이 오기 전에 이미 그려진다", async () => {
  /* 이 테스트가 이 분리의 요점이다. 문장 호출을 영원히 끝나지 않게 두고도 주소가
     보여야 한다 — 한 호출에 묶여 있으면 여기서 아무것도 안 보인다. */
  mockedFetch.mockResolvedValue({
    status: "success",
    requested_place_id: "126508",
    place_card: card({
      place_id: "126508",
      place_name: "경복궁",
      answer_fields: { address: "서울 종로구 사직로 161" },
    }),
  });
  mockedReason.mockReturnValue(new Promise(() => {}));

  render(
    <RecommendationDetailPreviewModal
      item={recommendationItem({ recommendation_reason: "거리 조건을 종합한 1순위 추천이에요." })}
      onClose={() => {}}
    />,
    { wrapper: TripProvider },
  );

  expect(await screen.findByText("서울 종로구 사직로 161")).toBeInTheDocument();
  // 문장 자리는 아직 비어 있다.
  expect(screen.queryAllByTestId("ai-reason-placeholder")).toHaveLength(1);
});

it("AI가 추천하는 이유는 LLM 문장만 보여준다 — 고정 문장은 쓰지 않는다", async () => {
  mockedFetch.mockResolvedValue({
    status: "success",
    requested_place_id: "126508",
    place_card: card({ place_id: "126508", place_name: "경복궁" }),
  });
  mockedReason.mockResolvedValue({ ai_reason: "후기에서 고즈넉한 산책로가 자주 언급돼요." });

  render(
    <RecommendationDetailPreviewModal
      item={recommendationItem({
        recommendation_reason: "날씨·운영시간·취향 조건을 종합한 4순위 추천이에요.",
      })}
      onClose={() => {}}
    />,
    { wrapper: TripProvider },
  );

  // 고정 문장("<축들> 조건을 종합한 N순위 추천이에요.")은 2026-09-16부터 쓰지 않는다.
  expect(
    screen.queryByText("날씨·운영시간·취향 조건을 종합한 4순위 추천이에요."),
  ).not.toBeInTheDocument();
  expect(
    await screen.findByText("후기에서 고즈넉한 산책로가 자주 언급돼요."),
  ).toBeInTheDocument();
});

it("사용자 취향과 맞은 태그 코드를 함께 보낸다 — 문장이 그 후기부터 말하도록", async () => {
  /* 서버는 상위 3태그만 문장 근거로 넘기는데 그 순서가 "이 장소에서 많이 언급된
     순서"다. 일치 표시를 안 보내면 사용자가 말한 취향에 걸린 태그가 근거에서
     통째로 빠진다. 추천 카드가 이미 들고 있는 값이라 조회가 더 붙지 않는다. */
  mockedFetch.mockResolvedValue({
    status: "success",
    requested_place_id: "126508",
    place_card: card({ place_id: "126508", place_name: "경복궁" }),
  });

  render(
    <RecommendationDetailPreviewModal
      item={recommendationItem({
        category_label: "고궁",
        preference_tags: [
          { code: "quiet", label: "조용한", mention_count: 5, is_query_match: true },
          { code: "photo", label: "사진 찍기 좋은", mention_count: 30 },
        ],
      })}
      onClose={() => {}}
    />,
    { wrapper: TripProvider },
  );

  await screen.findByText("경복궁");
  await waitFor(() => {
    expect(mockedReason).toHaveBeenCalledWith({
      place_id: "126508",
      place_name: "경복궁",
      category_label: "고궁",
      matched_preference_codes: ["quiet"],
    });
  });
});

it("추천 카드로 열 때만 문장을 요청한다", async () => {
  mockedFetch.mockResolvedValue({
    status: "success",
    requested_place_id: "126508",
    place_card: card({ place_id: "126508", place_name: "경복궁" }),
  });

  render(
    <RecommendationDetailPreviewModal
      item={recommendationItem({ category_label: "고궁" })}
      onClose={() => {}}
    />,
    { wrapper: TripProvider },
  );

  await screen.findByText("경복궁");
  // 상세 응답이 해석한 place_id로 부른다 — 서버가 그 id로 근거를 다시 읽는다.
  await waitFor(() => {
    expect(mockedReason).toHaveBeenCalledWith({
      place_id: "126508",
      place_name: "경복궁",
      category_label: "고궁",
      // 취향이 안 걸린 턴에서는 빈 목록이다 — 서버가 언급 수 순서를 그대로 쓴다.
      matched_preference_codes: [],
    });
  });
});

it("사진 검색처럼 item 없이 열면 문장을 요청하지 않는다", async () => {
  renderModal(card({ place_id: "126508", place_name: "경복궁" }));

  await screen.findByText("경복궁");
  expect(mockedReason).not.toHaveBeenCalled();
});

it("문장이 없는 장소는 그 줄을 접는다 — 자리표시자가 남지 않는다", async () => {
  /* 취향 태그가 없는 장소·생성 실패가 이 경우다. 자리표시자를 접지 않으면
     읽을 것이 없는 회색 줄이 영원히 남는다. */
  mockedFetch.mockResolvedValue({
    status: "success",
    requested_place_id: "126508",
    place_card: card({ place_id: "126508", place_name: "경복궁" }),
  });
  mockedReason.mockResolvedValue({ ai_reason: null });

  render(
    <RecommendationDetailPreviewModal
      item={recommendationItem({ recommendation_reason: "거리 조건을 종합한 1순위 추천이에요." })}
      onClose={() => {}}
    />,
    { wrapper: TripProvider },
  );

  await screen.findByText("경복궁");
  await waitFor(() => {
    expect(screen.queryAllByTestId("ai-reason-placeholder")).toHaveLength(0);
  });
  // 남길 문장이 없으면 제목까지 접는다 — 빈 절이 남지 않는다.
  expect(screen.queryByText("AI가 추천하는 이유")).not.toBeInTheDocument();
});

it("문장 생성이 실패해도 그 줄만 접고 카드는 그대로 둔다", async () => {
  mockedFetch.mockResolvedValue({
    status: "success",
    requested_place_id: "126508",
    place_card: card({
      place_id: "126508",
      place_name: "경복궁",
      answer_fields: { address: "서울 종로구 사직로 161" },
    }),
  });
  mockedReason.mockRejectedValue(new Error("500"));

  render(
    <RecommendationDetailPreviewModal
      item={recommendationItem({ recommendation_reason: "거리 조건을 종합한 1순위 추천이에요." })}
      onClose={() => {}}
    />,
    { wrapper: TripProvider },
  );

  expect(await screen.findByText("서울 종로구 사직로 161")).toBeInTheDocument();
  await waitFor(() => {
    expect(screen.queryAllByTestId("ai-reason-placeholder")).toHaveLength(0);
  });
});

it("문장이 도착하기 전에는 같은 높이의 자리를 잡아 둔다", async () => {
  /* 이 절을 표보다 위에 둔 이유가 "나중에 채워져도 이 절이 밀리거나 늘지 않는다"
     였다(2026-09-08). 늦게 오는 줄을 자리 없이 끼우면 그 성질이 깨진다.
     위 "그 줄을 접는다" 테스트와 짝이다 — 자리표시자가 아예 안 그려지면 그 테스트는
     헛돌기 때문에, 그려지는 경우를 여기서 함께 못 박는다. */
  mockedFetch.mockResolvedValue({
    status: "success",
    requested_place_id: "126508",
    place_card: card({ place_id: "126508", place_name: "경복궁" }),
  });
  mockedReason.mockReturnValue(new Promise(() => {}));

  render(
    <RecommendationDetailPreviewModal
      item={recommendationItem({ recommendation_reason: "거리 조건을 종합한 1순위 추천이에요." })}
      onClose={() => {}}
    />,
    { wrapper: TripProvider },
  );

  expect(screen.queryByText("거리 조건을 종합한 1순위 추천이에요.")).not.toBeInTheDocument();
  await waitFor(() => {
    expect(screen.queryAllByTestId("ai-reason-placeholder")).toHaveLength(1);
  });
});
