/*
 * 역할: 집중률 매핑 패널의 승인/거절 분류와 화면 표시를 검증한다.
 * 입력: 생성 결과와 승인한 content_id 집합.
 * 출력: 적재 대상·거절 목록 계산과 렌더링에 대한 assertion.
 * 호출 시점: vitest 실행 시 호출된다.
 *
 * 체크를 푼 것이 "이번 적재에서만 빠지는가"와 "거절 목록에 남는가"는 다른 문제다.
 * 남기지 않으면 다음 생성 때 같은 후보가 다시 올라와 매번 같은 판정을 되풀이한다.
 */

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import type {
  ConcentrationBuildResult,
  ConcentrationRow,
  ConcentrationStatus,
} from "../../api/dev";
import { ConcentrationMappingPanel } from "./ConcentrationMappingPanel";
import { splitApproval } from "./concentrationMapping";

function _row(fields: Partial<ConcentrationRow> = {}): ConcentrationRow {
  return {
    content_id: "1",
    place_title: "경복궁",
    concentration_title: "경복궁",
    match_method: "exact",
    aliases: [],
    search_key: null,
    search_keys: ["경복궁"],
    search_key_ambiguous: false,
    ...fields,
  };
}

function _result(fields: Partial<ConcentrationBuildResult> = {}): ConcentrationBuildResult {
  return {
    area_code: "11",
    district_code: "110",
    concentration_code: "11110",
    concentration_name_count: 33,
    place_count: 840,
    certain: [_row()],
    ambiguous: [],
    unmatched: [],
    leftover: [],
    ...fields,
  };
}

function _status(): ConcentrationStatus {
  return {
    districts: [
      {
        area_code: "11",
        district_code: "110",
        district_name: "종로구",
        concentration_code: "11110",
        active_places: 840,
        mapping_count: 101,
        latest_csv: "concentration_place_mapping_11110_20260808.csv",
        new_places_since_csv: 12,
      },
    ],
    rejection_count: 3,
  };
}

describe("splitApproval", () => {
  const ambiguous = [
    _row({ content_id: "2", place_title: "종묘", match_method: "normalized" }),
    _row({ content_id: "3", place_title: "북촌생활사박물관", match_method: "normalized" }),
  ];

  it("확실한 것은 체크와 무관하게 늘 들어간다", () => {
    const { rows } = splitApproval(_result({ ambiguous }), new Set());
    expect(rows.map((row) => row.content_id)).toEqual(["1"]);
  });

  it("체크한 애매한 후보만 적재하고 나머지는 거절로 남긴다", () => {
    const { rows, rejections } = splitApproval(_result({ ambiguous }), new Set(["2"]));
    expect(rows.map((row) => row.content_id)).toEqual(["1", "2"]);
    expect(rejections.map((entry) => entry.place_title)).toEqual(["북촌생활사박물관"]);
  });

  it("거절에는 어느 이름을 거절했는지 함께 담는다", () => {
    // 장소만 남기면 그 장소는 영영 어떤 이름에도 못 붙는다.
    const { rejections } = splitApproval(_result({ ambiguous }), new Set(["3"]));
    expect(rejections[0]).toMatchObject({
      place_title: "종묘",
      concentration_title: "경복궁",
    });
    expect(rejections[0].note).toContain("정규화");
  });
});

function _panel(
  result: ConcentrationBuildResult | null,
  approved = new Set<string>(),
  busy = false,
) {
  return (
    <ConcentrationMappingPanel
      status={_status()}
      selected={_status().districts[0]}
      result={result}
      applyResult={null}
      error={null}
      loading={false}
      building={false}
      applying={false}
      busy={busy}
      approved={approved}
      onSelectDistrict={() => {}}
      onToggleApproved={() => {}}
      onRefresh={() => {}}
      onBuild={() => {}}
      onApply={() => {}}
    />
  );
}

describe("ConcentrationMappingPanel", () => {
  it("매핑이 활성 장소보다 적은 것이 정상임을 알린다", () => {
    render(_panel(null));
    expect(screen.getByText(/대상이 아닌 장소/)).toBeInTheDocument();
    expect(screen.getByText(/3건이 쌓여 있어요/)).toBeInTheDocument();
  });

  it("CSV 이후 신규가 0인 구는 할 필요가 없음을 알린다", () => {
    render(_panel(null));
    expect(screen.getByText(/다시 만들어도 결과가 같아요/)).toBeInTheDocument();
    // 어느 구를 해야 하는지 표가 답해야 한다. CSV 날짜만으로는 알 수 없다.
    expect(screen.getByRole("columnheader", { name: "CSV 이후 신규" })).toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument();
  });

  it("애매한 후보에만 체크박스를 낸다", () => {
    render(
      _panel(
        _result({
          ambiguous: [_row({ content_id: "2", place_title: "종묘", match_method: "normalized" })],
        }),
        new Set(["2"]),
      ),
    );
    expect(screen.getByLabelText("종묘 매핑 승인")).toBeChecked();
    // 확실한 것은 체크 대상이 아니라 건수만 센다.
    expect(screen.queryByLabelText("경복궁 매핑 승인")).toBeNull();
  });

  it("검색어가 모호한 건은 붙었어도 경고를 함께 낸다", () => {
    render(
      _panel(
        _result({
          ambiguous: [
            _row({
              content_id: "2",
              place_title: "종묘",
              concentration_title: "종묘 [유네스코 세계유산]",
              match_method: "normalized",
              search_keys: ["종묘"],
              search_key_ambiguous: true,
            }),
          ],
        }),
      ),
    );
    expect(screen.getByText(/다른 장소도 끌어와요/)).toBeInTheDocument();
  });

  it("장소 동기화가 도는 중에는 매핑을 만들 수 없다", () => {
    render(_panel(null, new Set(), true));
    expect(screen.getByRole("button", { name: /매핑 생성/ })).toBeDisabled();
    expect(screen.getByText(/빠진 채로 붙어요/)).toBeInTheDocument();
  });
});
