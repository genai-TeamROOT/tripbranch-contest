/*
 * 역할: 취향 설정 화면. Figma "Preferences"(28:2) 화면을 옮긴 것이다.
 *   저장하면 **계정에 남고**(PUT /api/preferences) 이 기기에도 함께 남는다
 *   (state/preferenceSync.ts). 홈 화면에는 보이지 않는다(2026-09-07) — 홈의
 *   취향 칩 줄을 지운 뒤로는 확인하려면 이 화면에 다시 들어와야 한다.
 * 호출 시점: 사이드바 "취향 설정"에서 연다. 위치·일정과 함께 전체 페이지다 —
 *   시트로 여는 화면은 이제 앱에 없다(2026-09-07, AppShell).
 *
 * **저장하면 추천 순위에 반영된다**(SCORING_VERSION 1.5.0 취향 RAG 질의
 * 보강). 부제는 이 사실만 말한다(2026-09-07) — 홈 화면에 안 보인다는 사실은
 * 뺐다, 그건 이 화면을 쓰는 이유가 아니라 부수적인 정보라서다.
 *
 * 칩 목록과 각 칩이 대응하는 DB 코드는 preferenceOptions.ts에 있다 —
 * 근거가 있는 문구만 남긴 목록이라 그 배경도 거기 적혀 있다.
 */

import { Compass, Sparkles, Users } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ErrorBanner } from "../components/ErrorBanner";
import { AppHeader } from "../components/layout/AppHeader";
import { useElementHeightVar } from "../hooks/useElementHeightVar";
import { loadPreferences, type SavedPreference } from "../state/preferenceStorage";
import { pushPreferences, syncPreferences } from "../state/preferenceSync";
import { useTripState } from "../state/TripContext";
import {
  COMPANION_OPTIONS,
  MOOD_OPTIONS,
  PREFERENCE_GROUPS,
  THEME_OPTIONS,
  type PreferenceOption,
} from "./preferenceOptions";

const MIN_SELECTED = 3;
const MAX_SELECTED = 5;

function ChipGroup({
  icon: Icon,
  label,
  options,
  selected,
  onToggle,
  blockUnselected,
  isEn,
}: {
  icon: typeof Sparkles;
  label: string;
  options: readonly PreferenceOption[];
  selected: Set<string>;
  onToggle: (option: string) => void;
  /*
   * 아직 안 고른 칩을 못 누르게 한다. 예전에는 상한에 걸리면 `toggle`이 조용히
   * 무시했는데, 눌러도 아무 일이 안 나는 것과 고장은 화면에서 구분되지 않는다 —
   * 못 누른다는 사실이 칩 자체에 보여야 한다.
   *
   * 이미 고른 칩은 빼는 동작이라 언제나 누를 수 있다.
   */
  blockUnselected: boolean;
  isEn: boolean;
}) {
  return (
    <section className="flex w-full flex-col gap-2.5">
      <div className="flex items-center gap-1.5">
        <Icon size={14} className="text-label" />
        <h2 className="text-xs font-bold text-label">{label}</h2>
      </div>
      <div className="flex flex-wrap gap-2">
        {options.map((option) => {
          const isSelected = selected.has(option.label);
          return (
            <button
              key={option.label}
              type="button"
              aria-pressed={isSelected}
              disabled={!isSelected && blockUnselected}
              onClick={() => onToggle(option.label)}
              /* disabled:opacity-40은 이 화면의 "선택 초기화"와 같은 표현이다. */
              className={`rounded-full px-4 py-2 text-sm font-medium transition-colors disabled:opacity-40 ${
                isSelected ? "bg-brand text-white" : "bg-white text-ink shadow-resting"
              }`}
            >
              {isEn ? option.labelEn : option.label}
            </button>
          );
        })}
      </div>
    </section>
  );
}

/** 라벨로 옵션을 되찾는다. 목록에 없으면 사용자가 직접 넣은 키워드다. */
function toSavedPreference(label: string): SavedPreference {
  const option = PREFERENCE_GROUPS.flat().find((candidate) => candidate.label === label);
  return option
    ? { label, source: option.source, codes: option.codes }
    : { label, source: "custom", codes: [] };
}

export function PreferencesPage() {
  /* 저장 바가 스크롤 영역 위에 겹치므로, 겹치는 만큼 그쪽이 아래를 비워야
     마지막 항목이 바에 영영 가리지 않는다(useElementHeightVar). */
  const bottomBarRef = useRef<HTMLDivElement>(null);
  useElementHeightVar(bottomBarRef, "--tb-bottombar-h");

  const navigate = useNavigate();
  const { language } = useTripState();
  const isEn = language === "en";

  /* 저장해 둔 값이 있으면 그 상태로 열린다 — 다시 고르게 하지 않는다. */
  const [restored] = useState(loadPreferences);
  const [selected, setSelected] = useState<Set<string>>(
    () => new Set(restored.map((preference) => preference.label)),
  );
  /*
   * **새로 만들 수는 없고, 이미 저장한 것만 보여준다**(2026-09-06). "키워드 직접
   * 입력"을 없앴지만 그 전에 저장해 둔 값은 계속 그려야 한다 — 안 그리면 선택
   * 개수(N/5)에는 잡히는데 화면에 없는 칩이 생겨서, 5개를 다 못 고르는데 이유가
   * 어디에도 보이지 않는다. 여기 있으면 눌러서 빼고 저장하는 것으로 정리된다.
   */
  const [customKeywords, setCustomKeywords] = useState<string[]>(() =>
    restored.filter((preference) => preference.source === "custom").map(({ label }) => label),
  );
  const [cleared, setCleared] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  /* state가 아니라 ref다 — 아래 effect의 then 콜백이 마운트 시점의 값을 붙잡고
     있어서, state로 두면 사용자가 칩을 만져도 콜백은 계속 false로 본다. */
  const touchedRef = useRef(false);

  /*
   * 이 기기 값으로 먼저 그린 뒤 계정 값으로 맞춘다. 로딩 화면을 두지 않는 이유는
   * 대부분의 경우 둘이 같아서 깜빡임만 남기 때문이다. 다른 기기에서 바꾼 경우에만
   * 선택이 바뀌고, 그때는 바뀌는 것이 맞다.
   *
   * 사용자가 이미 칩을 만지기 시작했으면 덮어쓰지 않는다 — 서버 응답이 늦게 와서
   * 방금 고른 것을 지우면 안 된다.
   */
  useEffect(() => {
    let active = true;
    void syncPreferences().then((synced) => {
      if (!active || touchedRef.current) return;
      setSelected(new Set(synced.map((preference) => preference.label)));
      setCustomKeywords(
        synced.filter((preference) => preference.source === "custom").map(({ label }) => label),
      );
    });
    return () => {
      active = false;
    };
  }, []);

  function toggle(option: string) {
    touchedRef.current = true;
    setCleared(false);
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(option)) {
        next.delete(option);
      } else if (next.size < MAX_SELECTED) {
        next.add(option);
      }
      return next;
    });
  }

  /*
   * **동행은 하나만 고른다**(2026-09-06 사용자 결정). 안 고르는 것도 된다 —
   * 필수는 아니다.
   *
   * 다른 동행을 누르면 막지 않고 **바꾼다.** 막으면 먼저 빼고 다시 눌러야 해서
   * 한 번에 될 일이 두 번 걸린다. 먼저 빼고 넣으므로 개수가 늘지 않고, 그래서
   * 5개를 다 고른 상태에서도 동행 교체는 된다 — 상한 판정(blockUnselected)에서
   * 동행만 예외인 이유가 이것이다. 이 둘을 따로 만들면 "5개 찼을 때 동행을 못
   * 바꾸는" 상태가 생긴다.
   *
   * 예전에 동행을 둘 이상 저장해 둔 값은 열 때 손대지 않는다 — 저장한 것을
   * 말없이 지우지 않는다. 동행 칩을 한 번 누르면 그때 하나로 정리된다.
   */
  function toggleCompanion(option: string) {
    touchedRef.current = true;
    setCleared(false);
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(option)) {
        next.delete(option);
        return next;
      }
      for (const companion of COMPANION_OPTIONS) next.delete(companion.label);
      if (next.size < MAX_SELECTED) next.add(option);
      return next;
    });
  }

  /*
   * 초기화는 저장해 둔 값까지 지운다. 화면만 비우면 저장값을 되돌릴 방법이
   * 없어서다 — 저장 버튼은 3개 미만이면 눌리지 않으므로 "다 빼고 저장"이라는
   * 경로가 존재하지 않는다.
   */
  async function handleReset() {
    touchedRef.current = true;
    setSelected(new Set());
    setCustomKeywords([]);
    const hadSaved = loadPreferences().length > 0;
    setErrorMessage(null);
    /* 계정에도 반영한다. 빈 목록을 저장하는 것이지 행을 지우는 게 아니다 —
       "아직 고른 적 없음"과 "다 지웠음"은 다른 상태다. */
    try {
      await pushPreferences([]);
      setCleared(hadSaved);
    } catch {
      /* 이 기기에서는 이미 지워졌다(pushPreferences가 로컬을 먼저 쓴다). */
      setCleared(hadSaved);
      setErrorMessage(
        isEn
          ? "Couldn't remove this from your account. It may still remain on other devices."
          : "계정에서 지우지 못했어요. 다른 기기에는 아직 남아 있을 수 있어요.",
      );
    }
  }

  /*
   * 저장하면 홈으로 보낸다.
   *
   * ⚠️ **지금 홈에는 저장됐다는 표시가 없다**(2026-09-07). 원래는 홈에 뜬
   * "내 취향" 줄 자체가 확인이었는데 그 줄을 지웠다 — 저장하고 홈으로 보내면
   * 사용자는 아무 변화도 못 본다. 이 화면에 안내를 띄우고 머무르거나, 홈에
   * 표시를 되살리거나 둘 중 하나가 필요하다.
   */
  async function handleSave() {
    if (isSaving) return;
    setIsSaving(true);
    setErrorMessage(null);
    try {
      await pushPreferences([...selected].map(toSavedPreference));
      navigate("/");
    } catch {
      /* 고른 값은 이 기기에 이미 저장됐다. 다만 계정에 못 올렸으므로 다른
         기기에서는 안 보인다 — 그 사실을 알리고 화면에 머문다. */
      setErrorMessage(
        isEn
          ? "Couldn't save this to your account. It's still saved on this device."
          : "계정에 저장하지 못했어요. 이 기기에는 남아 있어요.",
      );
    } finally {
      setIsSaving(false);
    }
  }

  const remaining = MIN_SELECTED - selected.size;
  const canSave = remaining <= 0;
  const atMax = selected.size >= MAX_SELECTED;
  /* 동행이 이미 하나 있으면 다른 동행은 교체라 개수가 늘지 않는다 — 상한에 걸려
     있어도 누를 수 있어야 한다(toggleCompanion 주석). */
  const companionPicked = COMPANION_OPTIONS.some((option) => selected.has(option.label));

  /*
   * 카운터 옆에 붙는 한 줄. 아래 저장 버튼이 "몇 개 더"를 세는 것과 역할이 다르다 —
   * 여기는 **규칙**(최소 3, 최대 5, 다 찼을 때 무엇을 해야 하는지)을 말한다.
   */
  const limitHint = isEn
    ? !canSave
      ? `Pick at least ${MIN_SELECTED} to save`
      : atMax
        ? `That's all ${MAX_SELECTED}. Remove one to swap.`
        : `You can pick ${MAX_SELECTED - selected.size} more`
    : !canSave
      ? `저장하려면 ${MIN_SELECTED}개는 골라야 해요`
      : atMax
        ? "다 골랐어요. 바꾸려면 하나를 빼주세요"
        : `${MAX_SELECTED - selected.size}개 더 고를 수 있어요`;

  return (
    <main className="relative flex h-full flex-col overflow-hidden">
      {/*
       * 헤더와 저장 바는 스크롤 영역 **위에 겹친다**(2026-09-09). 흐름 안에 두면
       * 그 자리가 죽은 칸이 되어 내용이 위아래로 잘려 보이고, `sticky` 로 두면
       * iOS 에서 갱신이 멈춰 스크롤할 때 화면 밖으로 밀려난다(ChatComposer 주석).
       * 겹치는 만큼 스크롤 칸이 위아래를 비운다.
       */}
      <div className="absolute inset-x-0 top-0 z-20">
        <AppHeader keepStrip />
      </div>

      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto overscroll-none pb-[var(--tb-bottombar-h,0px)] pt-[var(--tb-header-h,0px)]">
        {/*
         * 세로 간격은 Figma Preferences(28:2)의 gap 프레임을 그대로 따른다 —
         * 헤더 아래 24(56:2), 묶음 사이 24, 마지막 요소와 BottomBar 사이 24(28:102).
         */}
        <div className="mx-auto flex w-full max-w-2xl flex-1 flex-col gap-6 px-4 pb-6 pt-6">
          <div>
            <h1 className="text-2xl font-bold leading-snug text-ink">
              {isEn ? (
                <>
                  What kind of moments
                  <br />
                  draw you in?
                </>
              ) : (
                <>
                  어떤 순간에
                  <br />
                  끌리시나요?
                </>
              )}
            </h1>
            <p className="mt-2 text-sm leading-relaxed text-muted">
              {isEn
                ? `Your picks shape your recommendations. Pick at least ${MIN_SELECTED} and up to ${MAX_SELECTED}.`
                : `고르신 취향이 추천 결과에 반영돼요. 최소 ${MIN_SELECTED}개, 최대 ${MAX_SELECTED}개까지 골라주세요.`}
            </p>

            {/* 부제와 Meta 사이만 12다(28:20) — 컨테이너 gap 24를 쓰면 두 배로 벌어진다. */}
            {/* 좁은 화면에서는 초기화가 다음 줄로 내려간다(ml-auto가 오른쪽에 붙인다).
                안내 문구를 줄임표로 자르지 않기 위해서다 — 자르면 지금 무엇을 해야
                하는지가 사라진다. */}
            <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1.5">
              {/* 최소와 최대를 함께 낸다. `N / 5`만 내면 3개를 채워야 저장된다는
                  사실이 이 자리에서 안 보인다. */}
              {/*
               * **최소치를 채웠는지를 칩의 채움으로 말한다(2026-09-17).** 비어 있으면
               * 아직 저장할 수 없고, 브랜드 색으로 차면 저장 조건을 넘긴 것이다 —
               * 아래 저장하기 버튼이 열리는 것과 같은 조건(`canSave`)을 쓴다.
               *
               * 색이 유일한 신호는 아니다. 같은 사실을 칩의 글자("3개 선택됨")와
               * 옆의 안내, 저장하기 버튼의 활성 상태가 함께 말한다.
               *
               * 두 상태 모두 테두리를 둔다. 채운 쪽에서 빼면 1px씩 줄어 3개째를
               * 고르는 순간 칩이 들썩이고, 그 옆 안내 문구까지 밀린다.
               */}
              <span
                className={`rounded-full border border-brand px-3 py-1.5 text-xs font-bold transition-colors ${
                  canSave ? "bg-brand text-white" : "bg-white text-brand"
                }`}
              >
                {isEn
                  ? `${selected.size} of ${MIN_SELECTED}–${MAX_SELECTED} selected`
                  : `${MIN_SELECTED}–${MAX_SELECTED}개 중 ${selected.size}개 선택됨`}
              </span>
              {/*
               * 라이브 영역으로 두지 않는다. 아래 "지웠어요" 안내가 이미 role=status라
               * 둘이 되고, 칩을 누를 때마다 매번 울려 시끄럽다. 상한에 걸렸다는 사실은
               * 칩이 disabled가 되는 것으로 이미 전달된다.
               */}
              <span className="text-xs text-muted">{limitHint}</span>
              <button
                type="button"
                onClick={handleReset}
                disabled={selected.size === 0}
                className="ml-auto text-xs font-bold text-muted transition-colors hover:text-ink disabled:opacity-40"
              >
                {isEn ? "Clear selection" : "선택 초기화"}
              </button>
            </div>
          </div>

          <ChipGroup
            icon={Sparkles}
            label={isEn ? "Mood" : "분위기"}
            options={MOOD_OPTIONS}
            selected={selected}
            onToggle={toggle}
            blockUnselected={atMax}
            isEn={isEn}
          />
          <ChipGroup
            icon={Compass}
            label={isEn ? "Theme" : "테마"}
            options={THEME_OPTIONS}
            selected={selected}
            onToggle={toggle}
            blockUnselected={atMax}
            isEn={isEn}
          />
          {/* 동행만 하나짜리다 — 라벨에도 그렇게 적는다. 규칙을 눌러 보고 알게
              하지 않는다. */}
          <ChipGroup
            icon={Users}
            label={isEn ? "Companions (pick one)" : "동행 (1개만)"}
            options={COMPANION_OPTIONS}
            selected={selected}
            onToggle={toggleCompanion}
            blockUnselected={atMax && !companionPicked}
            isEn={isEn}
          />

          {customKeywords.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {customKeywords.map((keyword) => (
                <button
                  key={keyword}
                  type="button"
                  aria-pressed={selected.has(keyword)}
                  disabled={!selected.has(keyword) && atMax}
                  onClick={() => toggle(keyword)}
                  className={`rounded-full px-4 py-2 text-sm font-medium transition-colors disabled:opacity-40 ${
                    selected.has(keyword)
                      ? "bg-brand text-white"
                      : "bg-white text-ink shadow-resting"
                  }`}
                >
                  {keyword}
                </button>
              ))}
            </div>
          )}

          {errorMessage && <ErrorBanner message={errorMessage} />}

          {/*
           * **"홈 화면에서도 사라져요"를 뺐다(2026-09-17).** 지우기 전에도 홈에는
           * 취향이 안 보인다 — 홈의 취향 칩 줄은 2026-09-07에 없앴다(HomePage).
           * 사라질 것이 없는 곳을 가리키고 있었고, 읽는 사람에게는 "홈에 뭔가
           * 보였었나" 하고 되짚게 만드는 문장이었다.
           */}
          {cleared && (
            <p
              role="status"
              className="rounded-xl bg-chip px-3.5 py-2.5 text-xs leading-relaxed text-ink"
            >
              {isEn
                ? "Your saved preferences have been cleared."
                : "저장해 둔 취향을 지웠어요."}
            </p>
          )}
        </div>
      </div>

      <div
        ref={bottomBarRef}
        className="absolute inset-x-0 bottom-0 z-20 mx-auto w-full max-w-2xl bg-gradient-to-t from-bg via-bg to-bg/0 px-4 pb-7 pt-4"
      >
        <button
          type="button"
          disabled={!canSave || isSaving}
          onClick={handleSave}
          className="flex h-[52px] w-full items-center justify-center rounded-full bg-brand text-base font-bold text-white transition-colors disabled:bg-brand/40"
        >
          {isEn
            ? isSaving
              ? "Saving…"
              : canSave
                ? "Save"
                : `Pick ${remaining} more`
            : isSaving
              ? "저장하는 중이에요…"
              : canSave
                ? "저장하기"
                : `${remaining}개 더 골라주세요`}
        </button>
      </div>
    </main>
  );
}
