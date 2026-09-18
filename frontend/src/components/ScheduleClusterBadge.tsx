/*
 * 역할: 타임라인에서 도보로 붙어 있는 정류장 묶음이 시작되는 자리를 알린다. (TP-243)
 * 입력: 그 묶음에 든 곳 수, 언어.
 * 출력: 발자국 아이콘 + "걸어서 5분 안쪽인 N곳" 한 줄.
 * 호출 시점: ScheduleResultMessage와 ScheduleRoute가 묶음의 첫 자리 앞에 그린다.
 *
 * **묶음마다 한 번만 그린다.** 구간마다 붙이면 세 곳이 묶였을 때 같은 말이 두 번
 * 반복되고, 이동 줄이 이동 이야기와 묶음 이야기로 갈라져 읽기 어려워진다.
 */

import { Footprints } from "lucide-react";
import { clusterBadgeLabel } from "../utils/scheduleTravel";

interface ScheduleClusterBadgeProps {
  count: number;
  isEn?: boolean;
}

export function ScheduleClusterBadge({ count, isEn = false }: ScheduleClusterBadgeProps) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-brand bg-white px-2 py-0.5 text-[11px] font-semibold text-brand">
      <Footprints size={12} aria-hidden />
      {clusterBadgeLabel(count, isEn)}
    </span>
  );
}
