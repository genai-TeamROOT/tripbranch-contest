/*
 * 역할: /dev-chat 채팅 입력 위에서 직전 턴이 쓴 위치 세 갈래를 뱃지로 보여준다.
 * 입력: 마지막 DeveloperAuditTurn.
 * 출력: 사용자 위치 / 검색 위치 / 경로 시작점 뱃지 한 줄(우측 정렬).
 * 호출 시점: DeveloperChatPage가 ChatComposer를 그리기 직전에 렌더링한다.
 *
 * 세 값을 따로 찍는 이유는 셋이 서로 다를 수 있고, 다른 것 자체가 봐야 할 사실이기
 * 때문이다(TP-112: 후보를 **모으는** 중심과 후보를 **줄 세우는** 기준점은 다르다).
 * 특히 시작점의 source가 "search_center"면 사용자 위치를 몰라 검색 위치를 그 자리에
 * 앉힌 턴이다 — 그 턴의 거리·실측 경로는 사용자가 자기 위치라고 말한 적 없는 지점에서
 * 잰 값이라, 값 자체는 정상으로 보여도 표기가 사실과 어긋난다. 다른 두 뱃지만으로는
 * 이 대체가 일어났는지 화면에서 알 수 없어 시작점을 따로 둔다.
 *
 * source가 "travel_origin_override"인 경우는 다르다 — "안국역에서 10분"처럼 발화가
 * 조사로 출발점을 확정한 턴이라(D-071), 사용자 위치를 몰라서가 아니라 사용자가 그렇게
 * 말해서 검색 위치를 쓴다. 사실과 어긋난 게 아니므로 warn 대상이 아니다("search_center"
 * 만 경고한다).
 */

import { useEffect, useState } from "react";
import { fetchNearestArea } from "../../api/dev";
import type { DeveloperAuditTurn, LocationDebug } from "../../types";

/** GPS 좌표에 붙일 근사 지역 이름. 조회 전이거나 82개 지역에서 2km를 넘으면 null. */
function useNearestAreaName(location: string | null): string | null {
  const [areaName, setAreaName] = useState<string | null>(null);

  useEffect(() => {
    if (location === null) {
      setAreaName(null);
      return;
    }
    let cancelled = false;
    void fetchNearestArea(location).then((area) => {
      if (!cancelled) setAreaName(area.area_name);
    });
    return () => {
      cancelled = true;
    };
  }, [location]);

  return areaName;
}

function toCoordinateText(location: LocationDebug): string {
  return `${location.latitude.toFixed(4)},${location.longitude.toFixed(4)}`;
}

const SOURCE_LABELS: Record<LocationDebug["source"], string> = {
  query: "발화",
  device_gps: "기기 GPS",
  search_center: "검색 위치 대체",
  // "안국역에서 10분"처럼 발화가 조사로 출발점을 확정한 경우(D-071). 사용자
  // 위치를 몰라서 대체된 게 아니라 정상 동작이라 위 라벨과 구분한다.
  travel_origin_override: "발화로 확정",
};

interface BadgeProps {
  icon: string;
  label: string;
  location: LocationDebug | null | undefined;
  /** 좌표만 있는 위치에 붙일 근사 이름. 없으면 좌표를 그대로 보여준다. */
  approximateName?: string | null;
  emptyText: string;
  /** 사실과 어긋날 수 있는 상태. 지금은 시작점이 검색 위치로 대체된 경우뿐이다. */
  warn?: boolean;
}

function LocationBadge({ icon, label, location, approximateName, emptyText, warn }: BadgeProps) {
  const tone = warn
    ? "border-amber-300 bg-amber-50 dark:border-amber-800 dark:bg-amber-950/40"
    : "border-gray-200 bg-white dark:border-gray-800 dark:bg-gray-900";

  let valueText: string;
  let hintText: string | null = null;
  if (!location) {
    valueText = emptyText;
  } else if (location.name !== null) {
    valueText = location.name;
    hintText = SOURCE_LABELS[location.source];
  } else if (approximateName) {
    // 근사치라는 사실을 숨기지 않는다 — 상권 중심점에서 잰 이름이지 이 좌표의 지명이 아니다.
    valueText = `≈ ${approximateName}`;
    hintText = SOURCE_LABELS[location.source];
  } else {
    valueText = toCoordinateText(location);
    hintText = SOURCE_LABELS[location.source];
  }

  return (
    <div
      // 폭을 나눠 갖지 않고 내용만큼만 차지한다 — 균등 분할하면 좁은 칸에서 지명이 잘린다.
      className={`flex items-baseline gap-1.5 rounded-md border px-2 py-1 ${tone}`}
      title={location ? toCoordinateText(location) : undefined}
    >
      <span className="shrink-0 text-[11px] text-gray-500 dark:text-gray-400">
        {icon} {label}
      </span>
      <span className="text-xs font-semibold text-gray-900 dark:text-gray-100">
        {valueText}
      </span>
      {hintText && (
        <span
          className={`shrink-0 text-[11px] ${
            warn ? "text-amber-700 dark:text-amber-300" : "text-gray-400 dark:text-gray-500"
          }`}
        >
          {hintText}
        </span>
      )}
    </div>
  );
}

/**
 * 위치 정보를 실은 실행 단계를 고른다. RECOMMEND의 context_fetch만 세 위치를 채운다 —
 * INFO/COMPARE는 C의 위치 해석을 거치지 않고 A가 기기 GPS로 직접 경로를 조회한다.
 */
function findLocationExecution(turn: DeveloperAuditTurn) {
  const executions = turn.response?.tool_executions ?? [];
  return executions.find((execution) => execution.search_location || execution.route_origin);
}

export function TurnLocationBadges({ turn }: { turn: DeveloperAuditTurn }) {
  const execution = findLocationExecution(turn);
  const userLocation = execution?.user_location ?? null;
  const routeOrigin = execution?.route_origin ?? null;

  // 이름이 없는 좌표에만 근사 이름을 붙인다. 시작점이 사용자 위치와 같은 좌표면
  // 같은 캐시 항목을 재사용하므로 조회는 한 번만 나간다.
  const userCoordinate =
    userLocation && userLocation.name === null ? toCoordinateText(userLocation) : null;
  const originCoordinate =
    routeOrigin && routeOrigin.name === null ? toCoordinateText(routeOrigin) : null;
  const userAreaName = useNearestAreaName(userCoordinate);
  const originAreaName = useNearestAreaName(originCoordinate);

  if (!execution) return null;

  return (
    // 채팅 입력 바로 위, 오른쪽 정렬. 대화를 가리지 않게 한 줄로 붙인다.
    <div className="mb-2 flex flex-wrap items-center justify-end gap-1.5">
      <LocationBadge
        icon="👤"
        label="사용자"
        location={userLocation}
        approximateName={userAreaName}
        emptyText="없음"
      />
      <LocationBadge
        icon="🎯"
        label="검색"
        location={execution.search_location}
        emptyText="없음"
      />
      <LocationBadge
        icon="🧭"
        label="시작점"
        location={routeOrigin}
        approximateName={originAreaName}
        emptyText="없음"
        warn={routeOrigin?.source === "search_center"}
      />
    </div>
  );
}
