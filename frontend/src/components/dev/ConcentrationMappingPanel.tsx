/*
 * 역할: 집중률 장소명을 우리 장소와 붙여 매핑을 만들고 DB에 올린다.
 * 입력: /api/dev/concentration/{status,build,apply}.
 * 출력: 구별 매핑 현황, 애매한 후보의 체크 목록, 미매핑 목록, 확인 다이얼로그.
 * 호출 시점: DeveloperOpsPage의 갱신 탭이 렌더링될 때.
 *
 * 매핑이 없는 장소는 혼잡도 조회를 통째로 건너뛴다(enrichment_service). 오류가 나지
 * 않고 그 장소만 조용히 판정에서 빠지므로, 장소 동기화 뒤에는 매핑을 새로 만들어야
 * 한다. 지금까지는 scripts 두 개를 손으로 돌렸다.
 *
 * 애매한 후보를 사람이 고르게 하는 이유는 D-043과 같다 — 이름이 크게 다른 장소를
 * 잘못 붙이면 엉뚱한 곳의 혼잡도를 답한다. 규칙이 이름을 고쳐 붙인 것(normalized,
 * exact_with_alias)만 눈으로 보고, 정확 일치와 수동 지정은 건수만 센다.
 *
 * 체크를 푼 것은 `concentration_rejections.csv`에 남는다. 파일에 남기지 않으면 다음
 * 생성 때 같은 후보가 다시 올라와 매번 같은 판정을 되풀이해야 한다.
 */

import { useMemo, useState } from "react";
import type {
  ConcentrationBuildResult,
  ConcentrationApplyResult,
  ConcentrationDistrict,
  ConcentrationStatus,
} from "../../api/dev";
import { METHOD_LABELS, districtLabel, splitApproval } from "./concentrationMapping";

/** 미매핑 목록에서 한 번에 보여줄 수. 종로구는 739건이라 다 그리면 표를 못 읽는다. */
const UNMATCHED_PREVIEW = 20;

function DistrictTable({
  districts,
  selected,
  onSelect,
  disabled,
}: {
  districts: ConcentrationDistrict[];
  selected: ConcentrationDistrict | null;
  onSelect: (district: ConcentrationDistrict) => void;
  disabled: boolean;
}) {
  return (
    <div className="max-h-64 overflow-y-auto rounded-md border border-gray-200 dark:border-gray-800">
      <table className="w-full text-left text-xs">
        <thead className="sticky top-0 bg-gray-50 dark:bg-gray-950">
          <tr className="border-b border-gray-200 dark:border-gray-800">
            <th className="py-1.5 pl-2 font-medium">구</th>
            <th className="py-1.5 pr-2 font-medium">활성 장소</th>
            <th className="py-1.5 pr-2 font-medium">매핑</th>
            <th className="py-1.5 pr-2 font-medium">최신 CSV</th>
            {/* 어느 구를 해야 하는지 답하는 열이다. CSV 날짜만으로는 알 수 없다. */}
            <th className="py-1.5 pr-2 font-medium">CSV 이후 신규</th>
          </tr>
        </thead>
        <tbody>
          {districts.map((district) => {
            const active =
              selected?.district_code === district.district_code &&
              selected?.area_code === district.area_code;
            return (
              <tr
                key={district.concentration_code}
                onClick={() => !disabled && onSelect(district)}
                className={`cursor-pointer border-b border-gray-100 last:border-0 dark:border-gray-800/60 ${
                  active ? "bg-blue-50 dark:bg-blue-950/30" : ""
                } ${disabled ? "cursor-not-allowed opacity-60" : ""}`}
              >
                <td className="py-1.5 pl-2">
                  {districtLabel(district)}{" "}
                  <span className="font-mono text-[11px] text-gray-400">
                    {district.concentration_code}
                  </span>
                </td>
                <td className="py-1.5 pr-2 tabular-nums">{district.active_places}</td>
                <td className="py-1.5 pr-2 tabular-nums">{district.mapping_count}</td>
                <td className="py-1.5 pr-2 font-mono text-[11px] text-gray-500">
                  {district.latest_csv?.slice(-12, -4) ?? "없음"}
                </td>
                <td
                  className={`py-1.5 pr-2 tabular-nums ${
                    district.new_places_since_csv > 0
                      ? "font-semibold text-amber-700 dark:text-amber-300"
                      : "text-gray-400"
                  }`}
                >
                  {district.new_places_since_csv || "—"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function ConcentrationMappingPanel({
  status,
  selected,
  result,
  applyResult,
  error,
  loading,
  building,
  applying,
  busy,
  approved,
  onSelectDistrict,
  onToggleApproved,
  onRefresh,
  onBuild,
  onApply,
}: {
  status: ConcentrationStatus | null;
  selected: ConcentrationDistrict | null;
  result: ConcentrationBuildResult | null;
  applyResult: ConcentrationApplyResult | null;
  error: string | null;
  loading: boolean;
  building: boolean;
  applying: boolean;
  /** 장소 동기화가 돌고 있으면 참. 같은 구의 places를 읽는 중에 매핑을 만들면
   *  방금 들어온 장소가 빠진 채로 붙는다. */
  busy: boolean;
  approved: Set<string>;
  onSelectDistrict: (district: ConcentrationDistrict) => void;
  onToggleApproved: (contentId: string) => void;
  onRefresh: () => void;
  onBuild: () => void;
  onApply: (input: { confirm: string }) => void;
}) {
  const [confirm, setConfirm] = useState("");
  const [showDialog, setShowDialog] = useState(false);
  const [showAllUnmatched, setShowAllUnmatched] = useState(false);

  const split = useMemo(
    () => (result ? splitApproval(result, approved) : null),
    [result, approved],
  );
  const ambiguousChecked = result
    ? result.ambiguous.filter((row) => approved.has(row.content_id)).length
    : 0;
  const locked = busy || building || applying;

  return (
    <section className="rounded-lg border border-gray-200 bg-white p-4 dark:border-gray-800 dark:bg-gray-900">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-base font-bold text-gray-950 dark:text-gray-50">집중률 매핑</h2>
          <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
            집중률 API의 장소명을 우리 장소와 붙여요. 매핑이 없으면 그 장소는 혼잡도 조회를 통째로
            건너뛰어요 — 오류 없이 판정에서만 빠져요.
          </p>
        </div>
        <button
          type="button"
          onClick={onRefresh}
          disabled={loading || locked}
          className="rounded-md border border-gray-300 px-3 py-1.5 text-sm disabled:opacity-50 dark:border-gray-700"
        >
          {loading ? "불러오는 중…" : "다시 읽기"}
        </button>
      </header>

      {error && (
        <p className="mt-3 rounded-md bg-red-50 p-3 text-xs text-red-900 dark:bg-red-950/40 dark:text-red-100">
          {error}
        </p>
      )}

      {busy && (
        <p className="mt-3 rounded-md bg-amber-50 p-3 text-xs text-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
          장소 동기화가 도는 중에는 매핑을 만들 수 없어요. 같은 구의 장소를 읽는 중이라 방금 들어온
          장소가 빠진 채로 붙어요.
        </p>
      )}

      {status && (
        <>
          <div className="mt-3">
            <DistrictTable
              districts={status.districts}
              selected={selected}
              onSelect={onSelectDistrict}
              disabled={locked}
            />
          </div>
          <p className="mt-2 text-xs text-gray-500 dark:text-gray-400">
            {/* 매핑이 활성 장소보다 훨씬 적은 것은 정상이다. 집중률 API가 관광지
             * 위주로만 다뤄서 나머지는 "매칭 실패"가 아니라 "대상이 아님"이다. */}
            <strong>"CSV 이후 신규"가 0인 구는 다시 만들어도 결과가 같아요.</strong> 매핑 수가 활성
            장소보다 훨씬 적은 건 정상이에요 — 집중률 API가 관광지 위주로만 다뤄서, 나머지는 실패가
            아니라 대상이 아닌 장소예요. 거절 목록에 {status.rejection_count}건이 쌓여 있어요.
          </p>

          <div className="mt-3 flex flex-wrap items-center gap-3 border-t border-gray-200 pt-3 dark:border-gray-800">
            <span className="text-xs text-gray-500 dark:text-gray-400">
              {selected
                ? `${districtLabel(selected)} · 집중률 목록을 새로 받아요 (8~9회, 챗봇 혼잡도와 같은 한도)`
                : "구를 고르세요"}
            </span>
            <button
              type="button"
              onClick={onBuild}
              disabled={locked || selected === null}
              className="ml-auto rounded-md border border-gray-300 px-3 py-1.5 text-sm disabled:opacity-50 dark:border-gray-700"
            >
              {building ? "만드는 중…" : "1. 매핑 생성"}
            </button>
          </div>
        </>
      )}

      {result && (
        <div className="mt-4 border-t border-gray-200 pt-3 dark:border-gray-800">
          <p className="text-xs text-gray-500 dark:text-gray-400">
            집중률 장소명 {result.concentration_name_count}건 · 활성 장소 {result.place_count}건
          </p>

          <p className="mt-2 rounded-md bg-gray-100 p-2.5 text-xs dark:bg-gray-800">
            <strong>확실 {result.certain.length}건</strong> — 이름이 그대로 같거나 수동 지정에 적힌
            것이라 그대로 들어가요.
          </p>

          {result.ambiguous.length > 0 && (
            <div className="mt-3">
              <p className="text-xs font-semibold">
                애매한 매핑 {result.ambiguous.length}건 · 체크 {ambiguousChecked}건
              </p>
              <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
                규칙이 이름을 고쳐 붙인 것들이에요. 체크를 풀면 이번 적재에서 빠지고{" "}
                <strong>거절 목록에 남아 다음 생성에도 안 올라와요.</strong>
              </p>
              <div className="mt-2 max-h-64 overflow-y-auto rounded-md border border-gray-200 dark:border-gray-800">
                <table className="w-full text-left text-xs">
                  <tbody>
                    {result.ambiguous.map((row) => (
                      <tr
                        key={row.content_id}
                        className="border-b border-gray-100 last:border-0 dark:border-gray-800/60"
                      >
                        <td className="w-8 py-1.5 pl-2">
                          <input
                            type="checkbox"
                            aria-label={`${row.place_title} 매핑 승인`}
                            checked={approved.has(row.content_id)}
                            onChange={() => onToggleApproved(row.content_id)}
                          />
                        </td>
                        <td className="w-14 py-1.5 pr-2">
                          <span className="rounded bg-amber-100 px-1.5 py-0.5 font-semibold text-amber-900 dark:bg-amber-950/50 dark:text-amber-100">
                            {METHOD_LABELS[row.match_method] ?? row.match_method}
                          </span>
                        </td>
                        <td className="py-1.5 pr-2">{row.place_title}</td>
                        <td className="w-6 py-1.5 text-gray-400">→</td>
                        <td className="py-1.5 pr-2">
                          {row.concentration_title}
                          {row.search_key_ambiguous && (
                            <span className="ml-1.5 text-[11px] text-amber-700 dark:text-amber-300">
                              검색어 '{row.search_keys[0]}'가 다른 장소도 끌어와요
                            </span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {result.unmatched.length > 0 && (
            <div className="mt-3">
              <p className="text-xs font-semibold">미매핑 {result.unmatched.length}건</p>
              <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
                집중률 API가 다루지 않는 장소예요. 붙여야 할 장소가 섞여 있으면{" "}
                <code>concentration_manual_overrides.csv</code>에 적어주세요.
              </p>
              <ul className="mt-2 max-h-40 overflow-y-auto rounded-md border border-gray-200 p-2 text-xs dark:border-gray-800">
                {(showAllUnmatched
                  ? result.unmatched
                  : result.unmatched.slice(0, UNMATCHED_PREVIEW)
                ).map((place) => (
                  <li key={place.content_id} className="py-0.5">
                    {place.title}{" "}
                    <span className="font-mono text-[11px] text-gray-400">{place.content_id}</span>
                  </li>
                ))}
              </ul>
              {result.unmatched.length > UNMATCHED_PREVIEW && (
                <button
                  type="button"
                  onClick={() => setShowAllUnmatched((current) => !current)}
                  className="mt-1 text-xs text-blue-700 dark:text-blue-300"
                >
                  {showAllUnmatched
                    ? "접기"
                    : `${result.unmatched.length - UNMATCHED_PREVIEW}건 더 보기`}
                </button>
              )}
            </div>
          )}

          {result.leftover.length > 0 && (
            <p className="mt-3 text-xs text-gray-500 dark:text-gray-400">
              집중률 API엔 있는데 우리 장소와 안 붙은 이름 {result.leftover.length}건:{" "}
              {result.leftover.slice(0, 8).join(", ")}
              {result.leftover.length > 8 && " …"}
            </p>
          )}

          <div className="mt-4 flex flex-wrap items-center gap-3 border-t border-gray-200 pt-3 dark:border-gray-800">
            <span className="text-xs text-gray-500 dark:text-gray-400">
              적재 {split?.rows.length ?? 0}건 · 거절 {split?.rejections.length ?? 0}건
              {" · 외부 호출 0회"}
            </span>
            <button
              type="button"
              onClick={() => {
                setConfirm("");
                setShowDialog(true);
              }}
              disabled={locked || (split?.rows.length ?? 0) === 0}
              className="ml-auto rounded-md bg-gray-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50 dark:bg-gray-100 dark:text-gray-900"
            >
              2. CSV 저장 + DB 적재
            </button>
          </div>
        </div>
      )}

      {applyResult && (
        <p className="mt-3 rounded-md bg-emerald-50 p-3 text-xs text-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-100">
          매핑 {applyResult.imported_count}건을 올렸어요. <code>{applyResult.csv}</code>로도
          남겼고요.
          {applyResult.rejected_count > 0 && (
            <>
              {" "}
              거절 {applyResult.rejected_count}건은 <code>{applyResult.rejection_file}</code>에
              적었어요 — 다음 생성에서는 후보로 올라오지 않아요.
            </>
          )}
        </p>
      )}

      {showDialog && result && split && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="w-full max-w-md rounded-lg bg-white p-5 dark:bg-gray-900">
            <h3 className="text-base font-bold">매핑 {split.rows.length}건을 올립니다</h3>
            <p className="mt-2 text-sm text-gray-600 dark:text-gray-300">
              운영 Supabase의 <code>place_concentration_mappings</code>에 씁니다. 같은 장소의 매핑이
              이미 있으면 새 값으로 덮어요.
            </p>
            <ul className="mt-3 space-y-1 text-xs text-gray-600 dark:text-gray-300">
              <li>· 대상: {result.concentration_code}</li>
              <li>
                · 확실 {result.certain.length} + 승인한 애매 {ambiguousChecked}
              </li>
              <li>· 거절 {split.rejections.length}건 (거절 목록에 남습니다)</li>
              <li>· 외부 API 호출 0회</li>
            </ul>
            <label className="mt-4 block text-xs text-gray-600 dark:text-gray-300">
              확인을 위해 <span className="font-mono font-bold">{result.concentration_code}</span>{" "}
              를 입력하세요.
              <input
                aria-label="집중률 확인 문자열"
                value={confirm}
                onChange={(event) => setConfirm(event.target.value)}
                className="mt-1 w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm dark:border-gray-700 dark:bg-gray-950"
              />
            </label>
            <div className="mt-4 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setShowDialog(false)}
                className="rounded-md border border-gray-300 px-3 py-1.5 text-sm dark:border-gray-700"
              >
                취소
              </button>
              <button
                type="button"
                disabled={confirm.trim() !== result.concentration_code}
                onClick={() => {
                  setShowDialog(false);
                  onApply({ confirm: confirm.trim() });
                }}
                className="rounded-md bg-gray-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50 dark:bg-gray-100 dark:text-gray-900"
              >
                실행
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
