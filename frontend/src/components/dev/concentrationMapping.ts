/*
 * 역할: 집중률 매핑 화면이 쓰는 매칭 방법 이름과 승인/거절 분류.
 * 입력: 생성 결과와 승인한 content_id 집합.
 * 출력: 적재할 행과 거절 목록에 남길 짝.
 * 호출 시점: ConcentrationMappingPanel과 DeveloperOpsPage가 부른다.
 *
 * 체크를 푼 것이 "이번 적재에서만 빠지는가"와 "다음 생성에도 안 올라오는가"는 다른
 * 문제다. 거절로 남기지 않으면 매번 같은 판정을 되풀이하게 된다. 그 판정을 정하는
 * 자리를 화면에서 떼어 한 곳에 둔다.
 */

import type { ConcentrationBuildResult, ConcentrationRow } from "../../api/dev";

export const METHOD_LABELS: Record<string, string> = {
  manual: "수동",
  exact: "정확",
  exact_with_alias: "별칭",
  normalized: "정규화",
};

export function districtLabel(district: {
  district_name: string | null;
  area_code: string;
  district_code: string;
}) {
  const name = district.district_name ?? `구 ${district.district_code}`;
  return `${name} ${district.area_code}-${district.district_code}`;
}

/** 승인한 행과 거절한 짝을 가른다.
 *
 * 확실한 것(manual·exact)은 체크 대상이 아니라 늘 들어간다. 규칙이 이름을 고쳐 붙인
 * 것만 사람이 고르고, 고르지 않은 것은 거절로 남는다 — 이번 적재에서 빼는 데 그치지
 * 않고 다음 생성에서도 후보로 올라오지 않는다. */
export function splitApproval(
  result: ConcentrationBuildResult,
  approved: Set<string>,
): {
  rows: ConcentrationRow[];
  rejections: { place_title: string; concentration_title: string; note: string }[];
} {
  const rows = [
    ...result.certain,
    ...result.ambiguous.filter((row) => approved.has(row.content_id)),
  ];
  const rejections = result.ambiguous
    .filter((row) => !approved.has(row.content_id))
    .map((row) => ({
      place_title: row.place_title,
      concentration_title: row.concentration_title,
      note: `${METHOD_LABELS[row.match_method] ?? row.match_method} 매칭을 화면에서 거절`,
    }));
  return { rows, rejections };
}
