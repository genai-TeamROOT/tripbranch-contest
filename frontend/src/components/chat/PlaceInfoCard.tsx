/*
 * 역할: INFO 장소 질의의 간략 답변과 전체 장소 상세 정보를 한 카드에 표시한다.
 * 입력: C가 한 번의 상세 조회로 내려준 InfoPlaceCard.
 * 출력: 질문 답 요약과 클릭 시 열리는 장소 상세 모달.
 * 호출 시점: ChatMessageList가 place_info_result 메시지를 렌더할 때 호출된다.
 */

import { Fragment, useEffect, useRef, useState } from "react";
import type {
  InfoPlaceCard as InfoPlaceCardData,
  RealtimeInfoDetailItem,
  ReviewSource,
} from "../../types";
import { useTripState } from "../../state/TripContext";
import { useNaverDirections } from "../../hooks/useNaverDirections";
import { openNaverMapSearch } from "../../utils/naverDirections";
import {
  groupSubwayArrivals,
  parseSubwayArrival,
  subwayLineColor,
  type SubwayLineGroup,
} from "../../utils/subwayDisplay";
import { PlaceCardRow } from "./PlaceCardRow";
import { PlaceThumbnail } from "../PlaceThumbnail";
import {
  ConcentrationForecastBars,
  PopulationForecastBars,
  RoadTrafficStatusSection,
} from "./CongestionForecastBars";
import { SeoulRealtimeSummarySection } from "./SeoulRealtimeSummarySection";
import { RecommendationDetailPreviewModal } from "./RecommendationDetailPreviewModal";
import { CollapseToggleButton } from "./CollapseToggleButton";
import { splitOverviewParagraphs } from "../../utils/overviewText";
import { hasSeoulRealtimeContent, hasTourApiContent } from "../../utils/dataSourceAttribution";
import { SeoulRealtimeSourceNote, TourApiSourceNote } from "./SourceNotes";
import { HomepageLink } from "./HomepageLink";

const FIELD_LABELS: Record<string, string> = {
  operating_hours: "운영시간",
  rest_date: "휴무일",
  fee: "요금",
  parking: "주차",
  parking_fee: "주차 요금",
  baby_carriage: "유모차",
  pet: "반려동물 동반",
  credit_card: "카드 결제",
  restroom: "화장실",
  address: "주소",
  telephone: "전화번호",
  /* 무장애 여행 정보(D-077). 계약 키를 그대로 두면 화면에 wheelchair_access처럼
   * 영문 키가 그대로 찍힌다. */
  wheelchair_access: "휠체어 접근",
  accessible_restroom: "장애인 화장실",
  accessible_parking: "장애인 주차",
  wheelchair_rental: "휠체어 대여",
  stroller_rental: "유모차 대여",
  nursing_room: "수유실",
  guide_dog: "보조견 동반",
  braille_block: "점자블록",
  braille_promotion: "점자 안내물",
  audio_guide: "음성 안내",
  public_transport: "대중교통",
  infant_family_etc: "영유아·가족 편의",
  disability_etc: "장애인 편의 기타",
  overview: "개요",
  homepage: "홈페이지",
  concentration: "혼잡도",
  event: "행사",
  "상권 지역": "상권 지역",
  "상권 기준": "상권 기준",
  업종: "업종",
  "실시간 활동": "실시간 활동",
  "기준 시각": "기준 시각",
  안내: "안내",
};

/* 이 목록에 있는 필드는 백엔드 계약 키라 항상 같은 항목만 나온다. 그 외
   자유 텍스트 키(상권 지역 등)는 영어 화면에서도 한글 그대로 둔다. */
const FIELD_LABELS_EN: Record<string, string> = {
  operating_hours: "Hours",
  rest_date: "Closed on",
  fee: "Admission",
  parking: "Parking",
  parking_fee: "Parking fee",
  baby_carriage: "Stroller rental",
  pet: "Pets allowed",
  credit_card: "Card payment",
  restroom: "Restroom",
  address: "Address",
  telephone: "Phone",
  wheelchair_access: "Wheelchair access",
  accessible_restroom: "Accessible restroom",
  accessible_parking: "Accessible parking",
  wheelchair_rental: "Wheelchair rental",
  stroller_rental: "Stroller rental",
  nursing_room: "Nursing room",
  guide_dog: "Guide dogs allowed",
  braille_block: "Braille blocks",
  braille_promotion: "Braille guides",
  audio_guide: "Audio guide",
  public_transport: "Public transport",
  infant_family_etc: "Family amenities",
  disability_etc: "Other accessibility",
  overview: "Overview",
  homepage: "Website",
  concentration: "Crowd level",
  event: "Event",
};

interface PlaceInfoCardProps {
  card: InfoPlaceCardData;
}

interface OperatingHoursRow {
  period: string;
  hours: string;
}

function parseOperatingHours(value: string): OperatingHoursRow[] | null {
  // TourAPI는 "[기간]시간[기간]시간"처럼 구분자 없이 이어 붙여 내려준다.
  // 원문은 바꾸지 않고 카드에서만 기간별 행으로 나눈다.
  const rows = Array.from(value.matchAll(/\[([^\]]+)\]\s*-?\s*(.*?)(?=\[|$)/g))
    .map(([, period, hours]) => ({
      period: period.trim().split("/").join(" · "),
      hours: hours
        .trim()
        .replace(/(\d{2}:\d{2})\s*~\s*(\d{2}:\d{2})/g, "$1–$2")
        .replace(/\(\s*입장\s*마감\s*([^)]+)\)/g, "· 입장 마감 $1")
        .replace(/\s{2,}/g, " "),
    }))
    .filter(({ period, hours }) => period && hours);
  return rows.length > 0 ? rows : null;
}

function OperatingHoursRows({ rows }: { rows: OperatingHoursRow[] }) {
  /* 위쪽 여백을 두지 않는다 — 이 묶음은 <dd> 안에 들어가고, 여백을 주면 값
     블록만 라벨보다 내려가 같은 행인데 서로 어긋나 보인다(2026-09-09 화면
     확인). 행 간격은 바깥 <dl>이 맡는다. */
  return (
    <div className="grid gap-2 sm:grid-cols-2">
      {rows.map(({ period, hours }) => (
        <div key={period} className="rounded-xl bg-chip px-3 py-2">
          <p className="text-xs font-semibold text-label">{period}</p>
          <p className="mt-0.5 text-sm text-ink">{hours}</p>
        </div>
      ))}
    </div>
  );
}

function formatCardValue(fieldKey: keyof InfoPlaceCardData, value: string) {
  // TourAPI 원문의 예외 안내(※)는 문장에 붙여 두면 읽기 어렵다. 원문 뜻은
  // 바꾸지 않고 줄만 분리한다. 요금의 "-" 항목도 카드에서 불릿처럼 보이게 한다.
  let formatted = value.replace(/\s*※\s*/g, "\n※ ");
  if (fieldKey === "fee") {
    formatted = formatted.replace(/(?:^|\s)-\s*/g, "\n- ");
  }
  return formatted.trim();
}

/* 개요처럼 긴 설명은 여섯 줄까지만 보이고 "더 보기"로 편다.
 *
 * **자르는 것이 아니라 접는다.** line-clamp는 화면에서만 가리고 원문은 그대로
 * 남으므로 낭독기와 브라우저 찾기는 전문을 본다(상세 모달의 같은 처리와 동일).
 *
 * 여섯 줄인 이유는 "경복궁이 뭐야?" 같은 질문의 TourAPI 개요가 수백 자라 카드
 * 하나가 화면을 다 덮어버리기 때문이다 — 첫 대여섯 줄이면 무엇인지는 알 수 있고,
 * 나머지는 필요한 사람만 편다.
 */
const CLAMPED_ANSWER_FIELDS = new Set(["overview"]);

function ClampedAnswerValue({ value, isEn }: { value: string; isEn: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const [overflowing, setOverflowing] = useState(false);
  const clampRef = useRef<HTMLDivElement | null>(null);

  /* 글자 수가 아니라 실제로 넘쳤는지로 판정한다. 화면 폭과 언어에 따라 같은 값도
     줄 수가 달라져서, 글자 수로 재면 어떤 화면에서는 버튼이 헛돈다. */
  useEffect(() => {
    const node = clampRef.current;
    if (!node || expanded) return;
    const measure = () => setOverflowing(node.scrollHeight > node.clientHeight + 1);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, [value, expanded]);

  return (
    <div className="flex flex-col items-stretch gap-1.5">
      {/* 줄 간격·문단 나눔·접는 높이를 상세 모달의 개요 절과 같게 둔다. 같은 글이
          두 화면에서 다른 모양으로 보이면 어느 쪽이 요약본인지 헷갈린다.
          10.5rem은 leading-7(1.75rem) × 6줄이다. */}
      <div
        ref={clampRef}
        className={`flex flex-col gap-3 leading-7${
          expanded ? "" : " max-h-[10.5rem] overflow-hidden"
        }`}
      >
        {splitOverviewParagraphs(value).map((paragraph, index) => (
          <p key={`${index}-${paragraph.slice(0, 12)}`}>{paragraph}</p>
        ))}
      </div>
      {/* 편 뒤에는 넘침 판정이 거짓이 되므로(가릴 것이 없다) expanded도 함께 본다. */}
      {(overflowing || expanded) && (
        <CollapseToggleButton
          expanded={expanded}
          onToggle={() => setExpanded((previous) => !previous)}
          isEn={isEn}
        />
      )}
    </div>
  );
}

function isRealtimeParkingCard(card: InfoPlaceCardData): boolean {
  return ["realtime_parking", "realtime_public_parking"].includes(card.question_type);
}

function isPublicToiletCard(card: InfoPlaceCardData): boolean {
  return card.question_type === "public_toilet";
}

/* 개방 여부 칩 색. "확인 필요"를 초록으로 두면 열려 있다고 읽히므로 중립색을 쓴다. */
const TOILET_OPEN_CHIP_STYLE: Record<string, string> = {
  "지금 이용 가능": "bg-[#e7f6ec] text-calm",
  "지금은 닫혀 있음": "bg-rust-tint text-rust",
  "개방시간 확인 필요": "bg-chip text-muted",
};

/* 화장실 한 곳. 카드 전체가 도보 길찾기 버튼이다(CompareResultCards와 같은 방식) —
 * 급해서 묻는 질문이라 한 번 눌러 바로 출발할 수 있어야 한다. 좌표가 없으면
 * 주소로 지도 검색을 폴백하고, 그것도 없으면 정보만 보여준다. */
function PublicToiletSummary({
  item,
  isEn,
}: {
  item: RealtimeInfoDetailItem;
  isEn: boolean;
}) {
  const openLabel = item.details["개방 여부"] ?? "";
  const distance = item.details["거리"];
  const hours = item.details["개방시간"];
  const address = item.details["주소"];
  const accessible = item.details["장애인화장실"];

  const hasCoordinates = item.latitude != null && item.longitude != null;
  /* 출발점은 훅이 정한다(위치 설정의 출발지). 주소만 있는 항목은 길찾기
     대신 장소 검색으로 여는 기존 경로가 그대로 남는다. */
  const directions = useNaverDirections();
  const canRoute = (hasCoordinates && directions.canRoute) || Boolean(address);

  const openDirections = () => {
    if (hasCoordinates && directions.canRoute) {
      void directions.openDirections({
        destLat: item.latitude as number,
        destLng: item.longitude as number,
        destName: item.title,
        // 화장실은 걸어서 간다 — 대중교통 경로를 띄우면 급한 사람에게 쓸모없다.
        mode: "walk",
      });
      return;
    }
    if (address) openNaverMapSearch(address);
  };

  return (
    <article
      className={`min-w-0 rounded-xl border border-border bg-white px-3 py-2.5${
        canRoute ? " cursor-pointer transition-colors hover:bg-chip" : ""
      }`}
      role={canRoute ? "button" : undefined}
      tabIndex={canRoute ? 0 : undefined}
      aria-label={
        canRoute
          ? isEn
            ? `Walking directions to ${item.title} on Naver Maps`
            : `${item.title}까지 네이버 지도 도보 길찾기`
          : undefined
      }
      onClick={canRoute ? openDirections : undefined}
      onKeyDown={
        canRoute
          ? (event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                openDirections();
              }
            }
          : undefined
      }
    >
      <div className="flex min-w-0 items-start justify-between gap-2">
        <span className="min-w-0 flex-1 text-sm font-bold text-ink" title={item.title}>
          {item.title}
        </span>
        {openLabel && (
          <span
            className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-semibold ${
              TOILET_OPEN_CHIP_STYLE[openLabel] ?? "bg-chip text-muted"
            }`}
          >
            {openLabel}
          </span>
        )}
      </div>

      <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
        {distance && (
          <span className="rounded-md bg-sky-light px-1.5 py-0.5 text-[11px] font-semibold text-brand-deep">
            {distance}
          </span>
        )}
        {hours && <span className="text-[11px] text-muted">{hours}</span>}
        {accessible && (
          <span className="rounded-md bg-chip px-1.5 py-0.5 text-[11px] text-muted">
            {isEn ? "Accessible" : "장애인화장실"} {accessible}
          </span>
        )}
      </div>

      {address && <p className="mt-1 truncate text-[11px] text-muted">{address}</p>}

      {canRoute && (
        <span className="mt-2 flex w-fit items-center gap-0.5 text-xs font-semibold text-brand">
          {isEn ? "Walking directions on Naver Maps" : "네이버 지도로 도보 길찾기"}
          <span aria-hidden="true">›</span>
        </span>
      )}
    </article>
  );
}

function PublicToiletList({
  items,
  isEn,
}: {
  items: RealtimeInfoDetailItem[];
  isEn: boolean;
}) {
  return (
    <section className="grid gap-2 px-4 py-3">
      {items.map((item) => (
        <PublicToiletSummary key={item.title} item={item} isEn={isEn} />
      ))}
    </section>
  );
}

type ParkingLotType = "공영" | "민영" | "기타";

const PARKING_TYPE_BADGE_STYLE: Record<ParkingLotType, string> = {
  공영: "bg-sky-light text-brand-deep",
  민영: "bg-gold-tint text-[#8a5a12]",
  기타: "bg-chip text-muted",
};

// 서버는 이름 앞에 "[공영]"/"[민영]"을 붙여 보낸다(기타는 접두어 없음). 뱃지로
// 따로 떼어 보여주는 편이 대괄호 텍스트보다 한눈에 들어온다.
function splitParkingTitle(title: string): { type: ParkingLotType; name: string } {
  const matched = title.match(/^\[(공영|민영)\]\s*(.+)$/);
  if (!matched) return { type: "기타", name: title };
  return { type: matched[1] as ParkingLotType, name: matched[2] };
}

// _format_realtime_parking()이 만드는 "상태(거리, 총 대수, 요금)" 형태를 상태 칩과
// 메타 태그들로 나눈다. 괄호가 없으면(형식이 안 맞으면) 값 전체를 상태로 둔다.
function parseParkingValue(value: string): { status: string; available: boolean; meta: string[] } {
  const matched = value.match(/^(.+?)\(([^)]*)\)\s*$/);
  if (!matched) return { status: value, available: false, meta: [] };
  const status = matched[1].trim();
  // 백엔드가 ", "로 이어 붙인다(_format_realtime_parking). 콤마 하나로 나누면
  // "약 1,076m"처럼 숫자 자체에 천 단위 콤마가 있는 항목이 잘린다.
  const meta = matched[2]
    .split(", ")
    .map((part) => part.trim())
    .filter(Boolean);
  const available = /^현재\s+[\d,]+대\s+주차\s*(가능|중)/.test(status);
  return { status, available, meta };
}

// 근처 주차장 응답은 최대 9곳까지 나와, 이름 줄 + 값 줄로 다 펼치면 세로로 너무
// 길어진다. 뱃지·상태 칩으로 밀도를 낮추고, 거리·대수·요금은 아래 한 줄에
// 작은 태그로 모은다.
function RealtimeParkingSummary({ title, value }: { title: string; value: string }) {
  const { type, name } = splitParkingTitle(title);
  const { status, available, meta } = parseParkingValue(value);
  return (
    <article className="min-w-0 rounded-xl border border-border bg-white px-3 py-2.5">
      <div className="flex min-w-0 items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-1.5">
          <span
            className={`shrink-0 rounded-full px-1.5 py-0.5 text-[10px] font-semibold ${PARKING_TYPE_BADGE_STYLE[type]}`}
          >
            {type}
          </span>
          <span className="min-w-0 truncate text-sm font-bold text-ink" title={name}>
            {name}
          </span>
        </div>
        <span
          className={`shrink-0 rounded-full px-2 py-0.5 text-xs font-semibold ${
            available ? "bg-emerald-50 text-emerald-700" : "bg-chip text-muted"
          }`}
        >
          {status}
        </span>
      </div>
      {meta.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-x-2.5 gap-y-1 pl-[calc(1.75rem+0.375rem)] text-xs text-muted">
          {meta.map((part) => (
            <span key={part}>{part}</span>
          ))}
        </div>
      )}
    </article>
  );
}

// 목록형 실시간 카드(주차·행사)가 공유하는 "더 보기" 자리. 접힌 채로 시작해
// 답변 흐름을 밀어내지 않다가, 누르면 나머지를 펼친다.
function MoreItemsButton({
  hiddenCount,
  unit,
  onClick,
}: {
  hiddenCount: number;
  unit: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className="flex items-center justify-center gap-1 rounded-lg py-1.5 text-xs font-semibold text-brand-deep hover:bg-chip"
      onClick={onClick}
    >
      {hiddenCount}{unit} 더 보기
      <span aria-hidden="true">⌄</span>
    </button>
  );
}

// 기본으로 보여줄 주차장 수. 근처 주차장(최대 9곳)·공영주차장(최대 5곳) 응답이
// 전부 펼쳐지면 카드가 답변 흐름을 밀어내므로, 나머지는 펼쳐야 보이게 접는다.
const REALTIME_PARKING_COLLAPSED_COUNT = 3;

function RealtimeParkingList({ answers }: { answers: [string, string][] }) {
  const [expanded, setExpanded] = useState(false);
  const visible = expanded ? answers : answers.slice(0, REALTIME_PARKING_COLLAPSED_COUNT);
  const hiddenCount = answers.length - visible.length;
  return (
    <section className="grid gap-2 px-4 py-3">
      {visible.map(([title, value]) => (
        <RealtimeParkingSummary key={title} title={title} value={value} />
      ))}
      {hiddenCount > 0 && (
        <MoreItemsButton hiddenCount={hiddenCount} unit="곳" onClick={() => setExpanded(true)} />
      )}
    </section>
  );
}

// event(TourAPI 행사)도 realtime_event(서울시 실시간 행사)와 같은 가로 스크롤
// 사진 카드로 보여준다 — 둘 다 realtime_detail_items 모양(제목/부제/썸네일)으로
// 내려오므로 렌더는 공유하고 판정만 question_type을 더 받는다.
function isEventCardRow(card: InfoPlaceCardData): boolean {
  return card.question_type === "realtime_event" || card.question_type === "event";
}

// 추천 카드(PlaceCard)와 같은 너비·비율의 사진 카드다 — 폭이 다르면 같은 줄에
// 섞였을 때(추천 결과 다음에 행사가 오는 경우 등) 스크롤 리듬이 어긋난다.
function RealtimeEventCard({ item }: { item: RealtimeInfoDetailItem }) {
  const openable = Boolean(item.external_url);
  return (
    <li className="w-40 shrink-0">
      <div
        className={`relative w-full text-left${openable ? " cursor-pointer" : ""}`}
        role={openable ? "link" : undefined}
        tabIndex={openable ? 0 : undefined}
        aria-label={openable ? `${item.title} 행사 정보 보기` : undefined}
        onClick={
          openable
            ? () => window.open(item.external_url as string, "_blank", "noopener")
            : undefined
        }
        onKeyDown={
          openable
            ? (event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  window.open(item.external_url as string, "_blank", "noopener");
                }
              }
            : undefined
        }
      >
        <div className="group relative overflow-hidden rounded-2xl">
          {/* 서울시 응답에 THUMBNAIL이 비는 경우가 있다. */}
          <PlaceThumbnail src={item.thumbnail_url} />
        </div>
        <div className="pt-2">
          <p className="line-clamp-2 text-sm font-bold text-ink">{item.title}</p>
          {item.subtitle && (
            <p className="mt-1 line-clamp-2 text-[11px] leading-relaxed text-muted">
              {item.subtitle}
            </p>
          )}
        </div>
      </div>
    </li>
  );
}

// 행사는 이름 자체가 길어 주차장처럼 3곳으로 접으면 답변보다 목록이 먼저
// 눈에 띈다. 대신 가로 스크롤이라 접을 필요가 없다 — 추천 결과와 같은 방식
// (PlaceCardRow)으로 한 줄에 늘어놓고 옆으로 넘겨 보게 한다.
function RealtimeEventCardRow({ items }: { items: RealtimeInfoDetailItem[] }) {
  return (
    <div className="px-4 py-3">
      <PlaceCardRow>
        {items.map((item) => (
          <RealtimeEventCard key={item.title} item={item} />
        ))}
      </PlaceCardRow>
    </div>
  );
}

function isRealtimeSubwayCard(card: InfoPlaceCardData): boolean {
  return card.question_type === "realtime_subway";
}

// 한 방면(상행/하행 등) 안의 도착 한 건. 행선지(종착역)와 도착 안내를
// 한 줄에 놓는다 — 방면 묶음 헤더가 이미 방향을 말해주므로 여기서는
// 반복하지 않는다.
function SubwayArrivalRow({ item }: { item: RealtimeInfoDetailItem }) {
  const { arrival } = parseSubwayArrival(item.subtitle ?? "");
  const arrivalKnown = arrival !== null && !arrival.includes("미제공");
  const destination = item.details["종착역"] ? `${item.details["종착역"]}행` : "행선지 정보 미제공";
  return (
    <div className="flex min-w-0 items-center justify-between gap-2">
      <span className="min-w-0 truncate text-xs text-ink">{destination}</span>
      {arrival && (
        <span
          className={`shrink-0 rounded-full px-1.5 py-0.5 text-[11px] font-semibold ${
            arrivalKnown ? "bg-emerald-50 text-emerald-700" : "bg-white text-muted"
          }`}
        >
          {arrival}
        </span>
      )}
    </div>
  );
}

// 같은 역·같은 호선이라도 상행/하행은 다른 방향이라, 방면마다 별도 칸으로
// 나눠 나란히 보여준다(2026-09-02 실사용 지적) — 나열 순서만으로는 구분이
// 안 됐다.
function SubwayLineGroupCard({ group }: { group: SubwayLineGroup }) {
  return (
    <article className="min-w-0 rounded-xl border border-border bg-white px-3 py-2.5">
      <div className="flex min-w-0 items-center gap-1.5">
        <span
          className="h-2.5 w-2.5 shrink-0 rounded-full"
          style={{ backgroundColor: subwayLineColor(group.stationLine) }}
          aria-hidden="true"
        />
        <span className="min-w-0 truncate text-sm font-bold text-ink" title={group.stationLine}>
          {group.stationLine}
        </span>
      </div>
      <div
        className={`mt-2 grid gap-2 ${group.directions.length > 1 ? "grid-cols-2" : "grid-cols-1"}`}
      >
        {group.directions.map((direction) => (
          /* 바탕 대신 테두리로 구분한다(2026-09-16). **테두리를 빼지는 않는다** —
             상행/하행이 나열 순서만으로는 구분이 안 된다는 실사용 지적으로 칸을
             나눈 자리라(2026-09-02), 경계가 사라지면 두 방향이 붙어 읽힌다. */
          <div
            key={direction.direction}
            className="min-w-0 rounded-lg border border-border px-2 py-1.5"
          >
            <p className="text-[11px] font-semibold text-muted">{direction.direction}</p>
            <div className="mt-1 grid gap-1">
              {direction.items.map((item, index) => (
                <SubwayArrivalRow key={`${item.title}-${index}`} item={item} />
              ))}
            </div>
          </div>
        ))}
      </div>
    </article>
  );
}

function SubwayArrivalList({ items }: { items: RealtimeInfoDetailItem[] }) {
  const groups = groupSubwayArrivals(items);
  return (
    <section className="grid gap-2 px-4 py-3">
      {groups.map((group) => (
        <SubwayLineGroupCard key={group.stationLine} group={group} />
      ))}
    </section>
  );
}

const REVIEW_SOURCE_LABELS: Record<string, { ko: string; en: string }> = {
  naver_post: { ko: "네이버 블로그", en: "Naver blog" },
  google_review: { ko: "Google 리뷰", en: "Google review" },
};

function reviewSourceLabel(sourceType: string | null | undefined, isEn: boolean) {
  const label = REVIEW_SOURCE_LABELS[sourceType ?? ""];
  if (label) return isEn ? label.en : label.ko;
  return isEn ? "Visitor review" : "방문자 후기";
}

/*
 * 후기로 답한 턴에만 그린다. 답변 문장은 링크도 인용도 말하지 않기로 했고(그래야
 * 답이 읽기 쉽다), 그 근거를 여기서 인용으로 보여준다. 상세 모달의 "방문자 후기에
 * 나타난 특징"과 같은 인용 모양을 쓴다 — 같은 성격의 값이 두 자리에서 다르게
 * 보이면 사용자가 다른 것으로 읽는다.
 */
function ReviewSourceList({
  sources,
  placeName,
  isEn,
}: {
  sources: ReviewSource[];
  placeName: string | null;
  isEn: boolean;
}) {
  if (sources.length === 0) return null;
  /*
   * 장소 이름만 쓰고 질문 내용은 넣지 않는다. 질문 원문(specific_question)은
   * 키워드가 아니라 문장이고 형태도 제각각이라("창경궁 야간관람 어때?" / "아이와
   * 가기 좋대?") 그대로 붙이면 "…어때? 관련 후기예요"가 된다. 키워드만 뽑는 것도
   * "뭐가 맛있대?"류에서 건질 말이 없어 문구가 더 어색해진다.
   */
  const heading = placeName
    ? isEn
      ? `${placeName} reviews related to your question`
      : `물어보신 내용과 관련된 ${placeName} 후기예요`
    : isEn
      ? "Reviews related to your question"
      : "물어보신 내용과 관련된 후기예요";
  return (
    <section className="border-t border-border px-4 py-3">
      <p className="text-[11px] font-semibold text-muted">{heading}</p>
      <ul className="mt-2 grid gap-2">
        {sources.map((source, index) => (
          <li
            key={source.url ?? `${index}-${source.text.slice(0, 12)}`}
            className="rounded-xl bg-chip px-3 py-2.5"
          >
            <blockquote className="border-l-2 border-brand/40 pl-2.5 text-xs leading-5 text-ink">
              {`“${source.text}”`}
            </blockquote>
            <div className="mt-1.5 pl-2.5 text-[11px] text-muted">
              {source.url ? (
                <a
                  href={source.url}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="font-semibold text-brand hover:underline"
                >
                  {reviewSourceLabel(source.source_type, isEn)} ↗
                </a>
              ) : (
                <span>{reviewSourceLabel(source.source_type, isEn)}</span>
              )}
              {source.published_at ? ` · ${source.published_at.slice(0, 10)}` : ""}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function PlaceInfoCard({ card }: PlaceInfoCardProps) {
  const [showDetail, setShowDetail] = useState(false);
  const { language } = useTripState();
  const isEn = language === "en";
  const answers = Object.entries(card.answer_fields);

  return (
    <article className="mr-auto w-full overflow-hidden rounded-2xl bg-white shadow-resting">
      {card.thumbnail_url && (
        // 기본 카드에서 장소를 바로 알아볼 수 있도록, 작은 아이콘보다 충분히 큰
        // 중간 높이 썸네일을 카드 상단에 둔다. 상세 영역에서는 중복하지 않는다.
        <div className="flex h-44 w-full items-center justify-center overflow-hidden bg-chip">
          <img
            src={card.thumbnail_url}
            alt={isEn ? `${card.place_name ?? "Place"} image` : `${card.place_name ?? "장소"} 이미지`}
            loading="lazy"
            className="h-full w-full object-cover"
          />
        </div>
      )}
      {/* 이름 자리는 누를 수 없다 — 상세를 여는 것은 옆의 "장소 상세보기" 글자뿐이다.
          장소 이름을 함께 누를 수 있게 하면 카드 안에서 어디를 눌러야 상세가
          열리는지가 흐려진다. 배경 없이 글자만 두고 브랜드 색으로 눌러지는
          자리임을 알린다 — 일정 상세(ScheduleRoute)의 같은 자리 버튼과 문구·
          스타일을 맞췄다. */}
      <div className="flex w-full items-center justify-between gap-3 px-4 py-3">
        <span className="min-w-0 text-sm font-bold text-ink">
          {card.place_name ?? (isEn ? "Place details" : "장소 상세 정보")}
        </span>
        <button
          type="button"
          className="shrink-0 whitespace-nowrap text-xs font-bold text-brand"
          aria-haspopup="dialog"
          onClick={() => setShowDetail(true)}
        >
          {isEn ? "View place details" : "장소 상세보기"}
        </button>
      </div>

      {isPublicToiletCard(card) && (card.realtime_detail_items?.length ?? 0) > 0 ? (
        <PublicToiletList
          items={card.realtime_detail_items ?? []}
          isEn={isEn}
        />
      ) : isRealtimeParkingCard(card) && answers.length > 0 ? (
        <RealtimeParkingList answers={answers} />
      ) : isEventCardRow(card) && (card.realtime_detail_items?.length ?? 0) > 0 ? (
        <RealtimeEventCardRow items={card.realtime_detail_items ?? []} />
      ) : isRealtimeSubwayCard(card) && (card.realtime_detail_items?.length ?? 0) > 0 ? (
        <SubwayArrivalList items={card.realtime_detail_items ?? []} />
      ) : answers.length > 0 ? (
        /* 라벨 열은 grid의 auto 트랙 하나가 맡는다. 행마다 <dt>를 따로 두면 그
           행의 라벨 글자 폭이 그대로 열 폭이 되어, "휴무일"(3자)과 "운영시간"(4자)
           사이에서 값이 시작하는 자리가 어긋난다(2026-09-09 화면 확인). dt/dd 를
           감싸는 행 <div>를 두지 않는 것이 핵심이다 — 감싸면 행마다 별개의 포맷
           맥락이 되어 서로의 라벨 폭을 모른다. 행 간격(gap-y)도 여기서 준다 —
           예전에는 행이 서로 붙어 있어 휴무일의 두 번째 줄과 다음 항목이 한
           덩어리로 읽혔다. */
        <dl className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-2 px-4 py-3 text-sm">
          {answers.map(([key, value]) => {
            const hoursRows = key === "operating_hours" ? parseOperatingHours(value) : null;
            return (
              <Fragment key={key}>
                {/* 값이 칩 묶음이면 라벨을 6px 내린다. 칩의 첫 글자는 칩 안쪽
                    여백(py-2, 8px)만큼 내려가 있어서, 라벨을 행 맨 위에 두면
                    같은 행인데 라벨만 위로 뜬다. 다른 행은 값이 글자라 그대로 맞다. */}
                <dt className={`text-muted ${hoursRows ? "pt-1.5" : ""}`}>
                  {isEn
                    ? (FIELD_LABELS_EN[key] ?? FIELD_LABELS[key] ?? key)
                    : (FIELD_LABELS[key] ?? key)}
                </dt>
                <dd className="min-w-0 whitespace-pre-line text-ink">
                  {hoursRows ? (
                    <OperatingHoursRows rows={hoursRows} />
                  ) : key === "homepage" ? (
                    <HomepageLink value={value} isEn={isEn} />
                  ) : CLAMPED_ANSWER_FIELDS.has(key) ? (
                    <ClampedAnswerValue
                      value={formatCardValue(key as keyof InfoPlaceCardData, value)}
                      isEn={isEn}
                    />
                  ) : (
                    formatCardValue(key as keyof InfoPlaceCardData, value)
                  )}
                </dd>
              </Fragment>
            );
          })}
        </dl>
      ) : null}

      <ReviewSourceList
        sources={card.review_sources ?? []}
        placeName={card.place_name ?? null}
        isEn={isEn}
      />
      <ConcentrationForecastBars card={card} />
      <PopulationForecastBars card={card} />
      <SeoulRealtimeSummarySection card={card} />
      <RoadTrafficStatusSection card={card} />

      {/* 답변 카드에도 같은 규칙으로 맨 아래 한 줄씩. 관광공사 상세와 서울시
          실시간이 한 카드에 함께 실리는 경우가 있어(혼잡도 답변) 둘 다 필요하면
          두 줄이 된다. */}
      {(hasTourApiContent(card) || hasSeoulRealtimeContent(card)) && (
        /* 오른쪽 아래에 붙인다. 왼쪽 끝에 두면 본문 첫 글자와 같은 선에서 시작해
           읽는 흐름의 일부처럼 보였다 — 표기는 본문이 아니라 카드에 다는 꼬리표다.
           상세 모달이 출처를 오른쪽에 두는 것과도 같은 방향이다. */
        <div className="flex flex-col items-end gap-0.5 px-4 pb-3">
          {hasTourApiContent(card) && <TourApiSourceNote isEn={isEn} />}
          {hasSeoulRealtimeContent(card) && <SeoulRealtimeSourceNote isEn={isEn} />}
        </div>
      )}

      {showDetail && (
        <RecommendationDetailPreviewModal card={card} onClose={() => setShowDetail(false)} />
      )}
    </article>
  );
}
