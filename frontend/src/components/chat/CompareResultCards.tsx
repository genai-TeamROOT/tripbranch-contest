/*
 * 역할: COMPARE(TRAVEL_TIME) 응답의 장소별 실측 거리·수단별 소요시간을 카드로 보여준다.
 * 입력: ComparisonResult(비교 기준 + 장소별 비교 사실), 길찾기 출발점으로 쓸 deviceLocation.
 * 출력: 비교 대상 장소마다 거리·도보/자동차/대중교통 소요시간을 한눈에 보는 카드 목록.
 *       카드를 누르면 RECOMMEND 상세 카드와 같은 방식(TP-120)으로 "현재 위치 → 그 장소"
 *       네이버지도 대중교통 길찾기 딥링크가 열린다.
 * 호출 시점: ChatMessageList가 compare_result 메시지를 렌더링할 때 호출된다.
 *
 * criteria=travel_time일 때만 의미 있는 카드다(travel_* 필드가 이때만 채워진다).
 * time/overall 비교는 기존처럼 답변 문장만으로 충분하다고 보고 카드를 만들지 않는다
 * — 사용자 요청은 "이동 용이성 비교가 텍스트로만 오니 안 와닿는다"는 것이었다.
 */

import { ChevronRight, MapPin, Navigation } from "lucide-react";
import type { ComparisonItem, ComparisonResult } from "../../types";
import { useNaverDirections } from "../../hooks/useNaverDirections";

interface CompareResultCardsProps {
  comparison: ComparisonResult;
  /** 길찾기 출발점("위도,경도"). RECOMMEND 상세 카드와 같은 소스(디바이스 GPS)를 쓴다. */
  deviceLocation?: string | null;
}

const TRAVEL_MODES: {
  label: string;
  field: keyof Pick<
    ComparisonItem,
    "travel_walking_minutes" | "travel_driving_minutes" | "travel_transit_minutes"
  >;
}[] = [
  { label: "도보", field: "travel_walking_minutes" },
  { label: "자동차", field: "travel_driving_minutes" },
  { label: "대중교통", field: "travel_transit_minutes" },
];

function fastestMinutes(item: ComparisonItem): number | null {
  const values = TRAVEL_MODES.map(({ field }) => item[field]).filter(
    (value): value is number => value !== null,
  );
  return values.length > 0 ? Math.min(...values) : null;
}

function CompareTravelCard({
  item,
  isFastest,
  deviceLocation,
}: {
  item: ComparisonItem;
  isFastest: boolean;
  deviceLocation?: string | null;
}) {
  const modeEntries = TRAVEL_MODES.map(({ label, field }) => ({
    label,
    minutes: item[field],
  })).filter((entry): entry is { label: string; minutes: number } => entry.minutes !== null);

  /* 출발점은 훅이 정한다(위치 설정의 출발지 → 기기 좌표). 여기서는 이 카드가 목적지
     좌표를 갖고 있는지만 본다 — 그건 카드마다 다른 사실이다. */
  const directions = useNaverDirections(deviceLocation);
  const canRoute = item.latitude != null && item.longitude != null && directions.canRoute;

  const openDirections = () => {
    if (!canRoute || item.latitude == null || item.longitude == null) return;
    void directions.openDirections({
      destLat: item.latitude,
      destLng: item.longitude,
      destName: item.place_name,
    });
  };

  return (
    <li
      className={`flex flex-col gap-2 rounded-2xl p-3.5 shadow-resting ${
        isFastest ? "bg-sky-light" : "bg-white"
      }${canRoute ? " cursor-pointer transition-colors hover:bg-chip" : ""}`}
      role={canRoute ? "button" : undefined}
      tabIndex={canRoute ? 0 : undefined}
      aria-label={canRoute ? `${item.place_name}까지 네이버 지도로 길찾기` : undefined}
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
      <div className="flex items-baseline justify-between gap-2">
        <p className="text-sm font-bold text-ink">{item.place_name}</p>
        {isFastest && (
          <span className="rounded-full bg-brand px-2 py-0.5 text-[11px] font-semibold text-white">
            가장 빠름
          </span>
        )}
      </div>

      {item.travel_distance_km !== null && (
        <p className="flex items-center gap-1 text-xs text-muted">
          <MapPin size={11} /> 약 {item.travel_distance_km}km
        </p>
      )}

      {modeEntries.length > 0 ? (
        <dl className="grid grid-cols-3 gap-2">
          {modeEntries.map(({ label, minutes }) => (
            <div
              key={label}
              className="flex flex-col items-center gap-0.5 rounded-xl bg-chip py-1.5"
            >
              <dt className="text-[11px] text-muted">{label}</dt>
              <dd className="text-sm font-semibold text-ink">{minutes}분</dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className="text-xs text-muted">이동 경로를 확인하지 못했어요.</p>
      )}

      {canRoute && (
        <span className="flex w-fit items-center gap-0.5 text-xs font-semibold text-brand">
          <Navigation size={12} /> 네이버 지도로 길찾기 <ChevronRight size={12} />
        </span>
      )}
    </li>
  );
}

export function CompareResultCards({ comparison, deviceLocation }: CompareResultCardsProps) {
  if (comparison.criteria !== "travel_time") return null;

  const fastest = Math.min(
    ...comparison.items.map(fastestMinutes).filter((value): value is number => value !== null),
  );

  return (
    <ul className="mr-auto grid w-full grid-cols-1 gap-2.5 sm:grid-cols-2">
      {comparison.items.map((item) => (
        <CompareTravelCard
          key={item.place_id}
          item={item}
          isFastest={Number.isFinite(fastest) && fastestMinutes(item) === fastest}
          deviceLocation={deviceLocation}
        />
      ))}
    </ul>
  );
}
