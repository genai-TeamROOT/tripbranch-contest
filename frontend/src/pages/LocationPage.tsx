/*
 * 역할: 위치 설정 화면. Figma "Location (Sheet)"(29:2) 화면 그대로 옮긴 것이다.
 * 입력: TripContext의 device_location, localStorage의 즐겨찾기, 검색어.
 * 출력: 장소 검색 결과 목록(GET /api/places/search — 서버가 서울 안으로 좁혀
 *   준다)과 그중 고른 장소의 쓰임새(모달로 출발지·검색 기준을 갈라 물어
 *   locationSettings에 저장 → 다음 요청부터 AgentRequest.selected_current_location·
 *   selected_search_center로 실려 간다), "현재 위치 사용"(실제
 *   브라우저 GPS 재조회 — SET_DEVICE_LOCATION 디스패치), 즐겨찾기와 최근 고른
 *   장소 목록(눌러서 검색 위치로 잡는다).
 *
 * 즐겨찾기는 사이드바와 저장소를 공유한다 — 여기서 담은 장소가 사이드바 목록에도
 * 함께 보인다.
 *
 * 검색 위치와 현재 위치는 다른 값이다. 검색 위치는 "어디를 기준으로 찾을지"이고
 * 현재 위치는 "사용자가 지금 있는 곳"이라, 이동시간 출발점과 위치 재확인은
 * 현재 위치만 본다. 그래서 고른 장소를 device_location에 넣지 않는다.
 * 호출 시점: 사이드바 "위치 설정"에서 바텀시트로 열린다(DESIGN_SYSTEM.md §5).
 */

import {
  ArrowRight,
  Check,
  Crosshair,
  Info,
  MapPin,
  MapPinCheck,
  MapPinned,
  Navigation,
  Pencil,
  Search,
  Star,
  Trash2,
  X,
} from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { AppHeader } from "../components/layout/AppHeader";
import { FavoritesLimitModal } from "../components/layout/FavoritesLimitModal";
import { useTripDispatch, useTripState } from "../state/TripContext";
import { createId, type FavoritePlace } from "../state/sidebarStorage";
import { getLocationAgeMinutes } from "../utils/locationRefresh";
import { getBrowserDeviceLocation } from "../utils/geolocation";
import { setLocationCenter, setLocationOrigin } from "../state/locationSettings";
import {
  LocationPurposeModal,
  type LocationPurpose,
} from "../components/layout/LocationPurposeModal";
import { useFavorites } from "../hooks/useFavorites";
import { useLocationSettings } from "../hooks/useLocationSettings";
import {
  forgetRecentSearch,
  loadRecentSearches,
  rememberRecentSearch,
} from "../state/recentSearchesStorage";
import { searchPlaces } from "../api/trip";
import type { PlaceSearchCandidate } from "../types";

const MAX_FAVORITES = 10;

/*
 * 즐겨찾기 줄 안의 아이콘 버튼. **아이콘은 15px인데 누를 자리는 32px이다** —
 * 크기를 안 주면 아이콘 크기가 곧 터치 타깃이 되어 14px밖에 안 됐다(2026-09-08
 * 실측). 색은 각 버튼이 정한다 — 여기에 text-muted를 넣으면 "이름 저장"의
 * text-brand와 같은 specificity로 부딪혀 어느 쪽이 이길지 클래스 순서가 아니라
 * 생성된 CSS 순서로 갈린다.
 */
const ROW_ACTION_CLASS =
  "flex h-8 w-8 shrink-0 items-center justify-center rounded-full transition-colors";

export function LocationPage() {
  const state = useTripState();
  const dispatch = useTripDispatch();
  const isEn = state.language === "en";
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [favorites, setFavorites] = useFavorites();
  const [showFavoritesLimit, setShowFavoritesLimit] = useState(false);
  const [query, setQuery] = useState("");
  /* null은 "아직 검색하지 않았다"이고 빈 배열은 "찾았는데 없었다"다. 둘을 하나로
     합치면 화면을 처음 열었을 때부터 "찾은 장소가 없어요"가 뜬다. */
  const [searchResults, setSearchResults] = useState<PlaceSearchCandidate[] | null>(null);
  const [outsideServiceAreaCount, setOutsideServiceAreaCount] = useState(0);
  const [isSearching, setIsSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  /* 저장소가 진실이고 이 state는 화면을 다시 그리기 위한 사본이다 — 발화를 보낼
     때는 HomePage·ChatPage가 저장소를 직접 읽는다. */
  const locationSettings = useLocationSettings();
  /* 고른 장소를 출발지로 쓸지 검색 기준으로 쓸지 아직 못 정한 상태. null이면
     모달이 닫혀 있다. */
  const [pendingPlace, setPendingPlace] = useState<string | null>(null);
  const [recentSearches, setRecentSearches] = useState<string[]>(() => loadRecentSearches());
  /* 이름을 고치는 중인 즐겨찾기. null이면 아무것도 고치고 있지 않다. */
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState("");
  const searchBoxRef = useRef<HTMLDivElement>(null);


  const ageMinutes = getLocationAgeMinutes(state.device_location_captured_at);

  /* 결과 패널은 화면 위에 떠 있어서 스스로 닫히지 않는다. 바깥을 누르거나 Esc를
     누르면 닫는다 — 열어둔 채로 아래 즐겨찾기를 누르려다 가려지는 일이 없게. */
  useEffect(() => {
    if (searchResults === null) return;

    function handlePointerDown(event: MouseEvent) {
      if (!searchBoxRef.current?.contains(event.target as Node)) setSearchResults(null);
    }
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setSearchResults(null);
    }

    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [searchResults]);

  async function handleUseCurrentLocation() {
    if (isRefreshing) return;
    setIsRefreshing(true);
    setErrorMessage(null);
    try {
      const deviceLocation = await getBrowserDeviceLocation({ forceFresh: true, language: state.language });
      dispatch({
        type: "SET_DEVICE_LOCATION",
        payload: { deviceLocation, capturedAt: Date.now() },
      });
      /* 이 버튼의 뜻은 하나다 — "내 위치는 기기 좌표다". 그래서 쓰임새를 되묻지
         않고 출발지만 되돌린다. 검색 기준까지 비우려면 칩의 ✕로 따로 푼다. */
      setLocationOrigin(null);
    } catch (error) {
      setErrorMessage(
        error instanceof Error ? error.message : isEn ? "Couldn't get your location." : "위치를 가져오지 못했어요.",
      );
    } finally {
      setIsRefreshing(false);
    }
  }

  /* 입력할 때마다 부르지 않고 제출할 때만 부른다. Naver 지역 검색은 호출 한도가
     있는 유료 API라 글자마다 부르면 한 번 검색에 열 번 넘게 나간다. */
  /* 모달에서 고른 쓰임새대로 저장한다. */
  function applyPurpose(purpose: LocationPurpose) {
    if (pendingPlace === null) return;
    if (purpose === "origin") setLocationOrigin(pendingPlace);
    else setLocationCenter(pendingPlace);
    setPendingPlace(null);
  }

  function handleSelect(place: PlaceSearchCandidate) {
    /* 좌표가 아니라 이름을 저장한다 — 이름을 좌표로 바꾸는 경로는 백엔드의
       ResolveLocationTool 하나로 이미 정리돼 있다(AgentRequest.
       selected_search_center 주석 참고). */
    setPendingPlace(place.name);
    setSearchResults(null);
    setQuery("");
  }

  function startRename(favoriteId: string, label: string) {
    setRenamingId(favoriteId);
    setRenameDraft(label);
  }

  /* 빈 이름은 저장하지 않는다 — 목록에서 어느 줄인지 알 수 없게 된다. 검색 위치로
     보내는 값(searchCenterName)은 건드리지 않으므로, 이름을 바꿔도 위치는 그대로다. */
  function commitRename() {
    const trimmed = renameDraft.trim();
    if (trimmed) {
      setFavorites((prev) =>
        prev.map((favorite) =>
          favorite.id === renamingId ? { ...favorite, label: trimmed } : favorite,
        ),
      );
    }
    setRenamingId(null);
  }

  /* 같은 즐겨찾기가 출발지일 수도 검색 기준일 수도 있어, 하나로 뭉치면 목록에서
     어느 쪽인지 알 수 없다. 역할을 그대로 돌려준다. */
  function favoriteRole(favorite: FavoritePlace): "origin" | "center" | null {
    const name = favorite.searchCenterName ?? favorite.label;
    if (locationSettings.center === name) return "center";
    if (locationSettings.origin === name) return "origin";
    return null;
  }

  function favoriteLabel(favorite: FavoritePlace) {
    const role = favoriteRole(favorite);
    if (isEn) {
      if (role === "center") return `${favorite.label} is the current search center`;
      if (role === "origin") return `${favorite.label} is the current starting point`;
      return `Set ${favorite.label} as search location`;
    }
    if (role === "center") return `${favorite.label}이 지금 검색 기준이에요`;
    if (role === "origin") return `${favorite.label}이 지금 출발지예요`;
    return `${favorite.label}을 검색 위치로 설정`;
  }

  function isFavorite(place: PlaceSearchCandidate) {
    return favorites.some(
      (favorite) => (favorite.searchCenterName ?? favorite.label) === place.name,
    );
  }

  /* 같은 곳을 두 번 담아 줄이 두 개 생기지 않게, 이미 있으면 뺀다(누르면 토글). */
  function toggleFavorite(place: PlaceSearchCandidate) {
    if (!isFavorite(place) && favorites.length >= MAX_FAVORITES) {
      setShowFavoritesLimit(true);
      return;
    }
    setFavorites((prev) =>
      isFavorite(place)
        ? prev.filter((favorite) => (favorite.searchCenterName ?? favorite.label) !== place.name)
        : [
            ...prev,
            {
              id: createId("fav"),
              label: place.name,
              searchCenterName: place.name,
              address: place.road_address ?? place.address,
            },
          ],
    );
  }

  async function handleSearch(event: FormEvent) {
    event.preventDefault();
    await runSearch(query);
  }

  async function runSearch(rawQuery: string) {
    const trimmed = rawQuery.trim();
    if (!trimmed) return;
    /* 검색 중에 다시 누르면 예전에는 조용히 무시했다. 요청이 매달려 있을 때는
       "안 눌렸나"와 "잠겼나"가 화면에서 구분되지 않아서, 무시하더라도 무시했다고
       말한다(TP-240). */
    if (isSearching) {
      setSearchError(isEn ? "Still searching. One moment." : "아직 검색 중이에요. 잠시만요.");
      return;
    }
    setQuery(trimmed);
    setIsSearching(true);
    setSearchError(null);
    /* 결과가 오기 전에 남긴다 — 못 찾은 검색어야말로 다시 꺼내 고쳐 쓰게 된다. */
    setRecentSearches(rememberRecentSearch(trimmed));
    try {
      const response = await searchPlaces(trimmed);
      setSearchResults(response.places);
      setOutsideServiceAreaCount(response.outside_service_area_count);
    } catch (error) {
      setSearchResults(null);
      setSearchError(
        error instanceof Error ? error.message : isEn ? "Couldn't find the place." : "장소를 찾지 못했어요.",
      );
    } finally {
      setIsSearching(false);
    }
  }

  return (
    <main className="flex h-full flex-col overflow-y-auto">
      <AppHeader keepStrip />
      <div className="mx-auto flex w-full max-w-2xl flex-1 flex-col gap-3 px-4 pb-10">
        {/* 결과를 문서 흐름에 두면 아래 카드들이 통째로 밀려 내려간다. 검색창을
            기준으로 띄워서 화면이 그대로 있게 한다. */}
        <div ref={searchBoxRef} className="relative">
          <form
            onSubmit={handleSearch}
            className="flex h-12 items-center gap-2 rounded-xl border border-border bg-white px-3.5 focus-within:border-brand"
          >
            <Search size={16} className="shrink-0 text-muted" />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              aria-label={isEn ? "Search places" : "장소 검색"}
              placeholder={isEn ? "Search places, subway stations, addresses" : "장소, 지하철역, 주소 검색"}
              className="min-w-0 flex-1 bg-transparent text-base text-ink outline-none placeholder:text-muted"
            />
            <button
              type="submit"
              disabled={!query.trim() || isSearching}
              className="shrink-0 text-xs font-bold text-brand transition-opacity disabled:opacity-40"
            >
              {isEn ? (isSearching ? "Searching" : "Search") : isSearching ? "검색 중" : "검색"}
            </button>
          </form>

          {searchResults !== null && (
            <div className="absolute inset-x-0 top-full z-20 mt-1 max-h-80 overflow-y-auto rounded-xl border border-border bg-white px-3.5 shadow-card">
              <h2 className="sr-only">{isEn ? "Search results" : "검색 결과"}</h2>
              <div className="divide-y divide-border">
                {searchResults.length === 0 ? (
                  /* 서울 밖이라 걸러진 것과 아예 못 찾은 것은 다음에 할 일이
                   다르다 — 앞은 지역을 바꿔야 하고 뒤는 검색어를 고쳐야 한다. */
                  <p className="py-3 text-sm text-muted">
                    {isEn
                      ? outsideServiceAreaCount > 0
                        ? "We can only search within Seoul"
                        : "No places found"
                      : outsideServiceAreaCount > 0
                        ? "서울 지역만 검색할 수 있어요"
                        : "찾은 장소가 없어요"}
                  </p>
                ) : (
                  searchResults.map((place) => (
                    <div
                      key={`${place.name}-${place.latitude},${place.longitude}`}
                      className="-mx-3.5 flex items-start gap-2.5 px-3.5 py-3 transition-colors hover:bg-chip"
                    >
                      <button
                        type="button"
                        aria-label={isEn ? `Set ${place.name} as search location` : `${place.name} 검색 위치로 설정`}
                        onClick={() => handleSelect(place)}
                        className="flex min-w-0 flex-1 items-start gap-2.5 text-left"
                      >
                        <MapPin size={15} className="mt-0.5 shrink-0 text-brand" />
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-sm font-bold text-ink">{place.name}</p>
                          <p className="truncate text-xs text-muted">
                            {place.road_address ?? place.address ?? ""}
                          </p>
                        </div>
                        {place.category && (
                          <span className="shrink-0 text-xs text-muted">{place.category}</span>
                        )}
                      </button>
                      <button
                        type="button"
                        aria-label={
                          isEn
                            ? isFavorite(place)
                              ? `Remove ${place.name} from favorites`
                              : `Add ${place.name} to favorites`
                            : isFavorite(place)
                              ? `${place.name} 즐겨찾기 해제`
                              : `${place.name} 즐겨찾기 추가`
                        }
                        aria-pressed={isFavorite(place)}
                        onClick={() => toggleFavorite(place)}
                        /* 한도가 차면 흐리게만 두고 disabled로 막지 않는다 — 눌러야
                           왜 안 담기는지 모달로 말할 수 있다. aria-disabled도 붙이지
                           않는다. 누르면 답이 오는 버튼을 "아무 일도 안 함"으로
                           읽히게 하는 표시라서다. */
                        className={`mt-0.5 shrink-0 transition-colors ${
                          !isFavorite(place) && favorites.length >= MAX_FAVORITES
                            ? "text-border"
                            : "text-muted hover:text-gold"
                        }`}
                      >
                        <Star
                          size={15}
                          className={isFavorite(place) ? "fill-gold text-gold" : undefined}
                        />
                      </button>
                    </div>
                  ))
                )}
              </div>
            </div>
          )}
        </div>

        {searchError && (
          <p role="alert" className="px-1 text-xs text-rust">
            {searchError}
          </p>
        )}

        <button
          type="button"
          disabled={isRefreshing}
          onClick={() => void handleUseCurrentLocation()}
          className="flex items-center gap-2.5 rounded-xl bg-white px-3.5 py-3 text-left shadow-resting transition-opacity disabled:opacity-50"
        >
          <Crosshair
            size={17}
            className={`shrink-0 text-brand ${isRefreshing ? "animate-pulse" : ""}`}
          />
          <span className="text-sm font-bold text-brand">
            {isEn
              ? isRefreshing
                ? "Getting your location…"
                : "Use current location"
              : isRefreshing
                ? "위치를 가져오는 중이에요…"
                : "현재 위치 사용"}
          </span>
        </button>
        {/* 서울 안내를 칩 위로 올렸다(2026-09-08). 이건 한 번 읽고 마는 고정
            안내인데 위쪽에서 그 자리를 차지하고 있으면, 정작 지금 무엇이 잡혀
            있는지를 말하는 칩이 밀려 내려간다. 칩은 바로 아래 즐겨찾기·최근
            검색과 붙어 있는 편이 낫다 — 그 목록을 눌러 바뀌는 값이 칩이다.

            **박스를 걷고 회색 글자로 바꾼다**(2026-09-08). 전에는 `bg-sky-light`
            박스에 `text-brand-deep`이었는데, 그 대비가 "지금 눌러야 할 것"처럼
            읽혔다 — 실제로는 바뀌지 않는 고정 안내다. 이 화면이 이미 쓰는 평문
            한 줄 형식(`px-1 text-xs text-muted`, 아래 "현재 위치 · N분 전")을
            그대로 따른다. **`Info` 아이콘은 남긴다** — 걷어낸 것은 박스와 강한
            대비지, 이 줄이 안내라는 표시까지는 아니다.

            정렬은 `items-center`다. `items-start`+`mt-0.5`로는 아이콘 중심이 글자
            중심보다 **1.3px 위**였다(실측: 아이콘 200.5 vs 글자 glyph 201.8) —
            `leading-relaxed`가 만든 19.5px 줄 상자 안에서 12px 글자가 2.5px
            내려앉기 때문이고, `mt`는 2px 단위라 딱 맞는 값이 없다.

            가운데 정렬은 **문구가 한 줄일 때만** 옳다(두 줄이면 아이콘이 줄 사이로
            내려간다). 이 문구는 지원 하한인 320px에서도 한 줄이다 — 글자에 주어지는
            폭 261px에 한국어 205px, 영어 244px(실측). 문구가 이보다 길어지면 다시
            `items-start`로 돌리고 첫 줄 중심에 맞춰야 한다. */}
        <p className="flex items-center gap-1.5 px-1 text-xs leading-relaxed text-muted">
          <Info size={13} className="shrink-0" />
          <span>
            {isEn ? "We currently only recommend places in Seoul" : "현재 서울 지역 장소만 추천해 드리고 있어요"}
          </span>
        </p>

        {state.device_location && (
          /* 좌표를 그대로 보여주면 사용자에게는 숫자 두 개일 뿐이다. 주소로 바꾸는
             역지오코딩은 아직 없으므로 "현재 위치"라고만 말한다. */
          <p className="px-1 text-xs text-muted">
            {isEn ? "Current location" : "현재 위치"}
            {ageMinutes === null
              ? ""
              : isEn
                ? ` · checked ${ageMinutes} min ago`
                : ` · ${ageMinutes}분 전에 확인했어요`}
          </p>
        )}
        {errorMessage && (
          <p role="alert" className="px-1 text-xs text-rust">
            {errorMessage}
          </p>
        )}

        {/* 지금 정해져 있는 두 값. 서로 다른 질문의 답이라 칩을 따로 두되, 사이에
            화살표를 넣어 "여기서 출발해 저기 주변을 찾는다"는 관계를 보인다.

            줄바꿈하지 않는다 — 칩이 아래로 내려가면 화살표만 줄 끝에 남는다. 대신
            칩은 제 내용만큼만 차지하고, 한 줄에 정말 안 들어갈 때만 이름을 자른다.
            반씩 나눠 가지면 "현재 위치에서 출발"처럼 짧은 쪽이 남는 자리를 붙들고
            있어서, 긴 이름 쪽이 자리가 있는데도 먼저 잘린다.

            **눈에 띄게 하는 일을 크기·채움이 아니라 테두리에 맡긴다**(2026-09-08).
            `border border-brand` 한 줄이고, 글자(`text-xs`)와 채움(`bg-chip`)은
            원래 값이다. 오는 길에 두 가지를 시도했다 —

            ① 글자를 14px로(헤더 칩과 같게) 키웠더니 기본 문구가 **칩마다 4px씩**
               잘렸다. 줄 343px에 칩 143+170, 화살표 14, 간격 16이 정확히 꽉 찬
               상태였다(실측). padding·간격을 깎아 5px을 남겨 맞출 수는 있었지만,
               헤더와 같게 하려던 것이 헤더와 다른 padding으로 끝났다.
            ② 채움을 `bg-brand`+흰 글자로 했더니 눈에는 확실히 띄는데, 칩 안
               `X` 버튼의 `hover:text-rust`(지우기)가 파란 바탕에서 탁해져 색의
               뜻을 잃었다.

            테두리는 둘 다 건드리지 않는다 — 폭이 칩마다 2px만 늘고, 안쪽
            아이콘·X의 색 규칙도 그대로 산다.

            채움은 `bg-white` + `shadow-resting`이다 — **취향 설정 화면의 칩과 같은
            값이다**(PreferencesPage의 미선택 상태 `bg-white text-ink shadow-resting`).
            `bg-chip`(연한 회청)일 때는 테두리만으로 떠 있고 면은 배경에 가까웠는데,
            흰 면 + 그림자가 되면 칩이 종이처럼 얹힌다. 두 화면의 칩이 같은 물성을
            쓰게 된 것이 덤이다.

            위 안내 문구와의 간격은 컨테이너 `gap-3`(12px)에 `mt-4`를 더해 **28px**이다 —
            안내는 한 번 읽고 마는 글이고 칩은 지금 값이라, 12px로 붙어 있으면 안내가
            칩의 설명처럼 한 덩어리로 읽혔다. 20px(`mt-2`)과 28px을 렌더해 보고
            골랐다. **부작용이 하나 있다** — 안내가 위쪽 "현재 위치 사용" 버튼과는
            12px, 아래 칩과는 28px이 되어 위로 붙어 읽힌다. 안내를 양쪽 다 띄우려면
            안내에도 위 여백을 줘야 하는데, 이번 요청은 아래 간격이라 손대지 않았다.

            줄은 `justify-center`다. 375px에서는 칩이 줄을 거의 채워 좌우 여백이
            얼마 안 남지만, 넓은 화면(900px 기준 좌우 129px)에서는 뚜렷하다.

            화살표는 칩 **밖**(페이지 배경 위)이라 `text-muted`다. */}
        <div className="mt-4 flex items-center justify-center gap-2">
          <span className="flex min-w-0 items-center gap-1.5 rounded-full border border-brand bg-white px-3 py-1.5 text-xs text-ink shadow-resting">
            <Navigation size={13} className="shrink-0 text-brand" aria-hidden />
            <span className="truncate">
              {isEn
                ? `From ${locationSettings.origin ?? "current location"}`
                : `${locationSettings.origin ?? "현재 위치"}에서 출발`}
            </span>
            {locationSettings.origin && (
              <button
                type="button"
                aria-label={isEn ? "Reset starting point to current location" : "출발지를 현재 위치로 되돌리기"}
                onClick={() => setLocationOrigin(null)}
                className="shrink-0 text-muted transition-colors hover:text-rust"
              >
                <X size={12} />
              </button>
            )}
          </span>
          <ArrowRight size={14} aria-hidden className="shrink-0 text-muted" />
          <span className="flex min-w-0 items-center gap-1.5 rounded-full border border-brand bg-white px-3 py-1.5 text-xs text-ink shadow-resting">
            {/* 핀이 아니라 바닥 원이 깔린 핀이다 — 이 칩은 "그 지점"이 아니라
                "그 자리 주변"을 뒤진다는 뜻이라서다. */}
            <MapPinned size={13} className="shrink-0 text-brand" aria-hidden />
            {/* 비어 있다고 기준이 없는 게 아니다 — 그때는 출발지가, 출발지도 없으면
                기기 좌표가 검색 기준이 된다(agent_context/service.py). 그래서 실제로
                어디를 뒤지는지를 그대로 쓴다. */}
            <span className="truncate">
              {isEn
                ? `Search around ${locationSettings.center ?? locationSettings.origin ?? "current location"}`
                : `${locationSettings.center ?? locationSettings.origin ?? "현재 위치"} 주변에서 검색`}
            </span>
            {locationSettings.center && (
              <button
                type="button"
                aria-label={isEn ? "Reset search center" : "검색 기준 되돌리기"}
                onClick={() => setLocationCenter(null)}
                className="shrink-0 text-muted transition-colors hover:text-rust"
              >
                <X size={12} />
              </button>
            )}
          </span>
        </div>

        <div className="mt-2 flex items-center justify-between">
          <h2 className="text-xs font-bold text-label">{isEn ? "Favorites" : "즐겨찾기"}</h2>
          <span className="text-xs font-bold text-muted">
            {favorites.length}/{MAX_FAVORITES}
          </span>
        </div>
        <div className="divide-y divide-border border-t border-border">
          {favorites.length === 0 ? (
            <p className="py-3 text-sm text-muted">
              {isEn ? "No favorites yet" : "등록된 즐겨찾기가 없어요"}
            </p>
          ) : (
            favorites.map((favorite) => (
              /* 줄 전체가 "이 장소를 쓰겠다"는 버튼이다 — **이름·주소를 포함해
                 어디를 눌러도** 모달이 열린다(2026-09-08).

                 처음 설계는 달랐다: 글자가 이름 바꾸기였고 그 오른쪽 빈 자리만
                 모달을 열었다(jjinsword, 2026-09-03의 "글자는 이름 바꾸기,
                 그 사이 빈 자리를 눌러 고른다"). 실제로 재 보니 **375px 줄에서
                 이름은 94px, 빈 자리가 189px**였다 — 넓은 쪽이 이름 편집도 아닌
                 빈 공간에 걸려 있고, 정작 이름을 누르면 편집이 열렸다. 장소를
                 고르는 일이 이름을 고치는 일보다 훨씬 잦으므로 줄 전체를 선택에
                 주고, 이름 편집은 연필 아이콘으로 옮겼다.

                 핀은 지금 기준으로 잡혀 있는지만 보여주는 표시로 남는다. */
              <div
                key={favorite.id}
                role="button"
                tabIndex={0}
                aria-label={favoriteLabel(favorite)}
                aria-pressed={favoriteRole(favorite) !== null}
                onClick={() => setPendingPlace(favorite.searchCenterName ?? favorite.label)}
                onKeyDown={(event) => {
                  /* 줄 자체에 포커스가 있을 때만 연다. 이 줄 안에는 이름 입력창과
                     버튼이 있어서, 안 걸러내면 이름을 고치다 스페이스만 눌러도
                     모달이 뜬다. 이름이 버튼이 아니게 된 뒤에도 입력창이 남아
                     있으므로 이 가드는 그대로 필요하다. */
                  if (event.target !== event.currentTarget) return;
                  if (event.key !== "Enter" && event.key !== " ") return;
                  event.preventDefault();
                  setPendingPlace(favorite.searchCenterName ?? favorite.label);
                }}
                /* hover 배경을 구분선과 같은 폭으로 맞추고 둥글게 한다(2026-09-08).
                   전에는 `-mx-4 px-4`로 줄을 화면 폭까지 늘려서, 배경이 구분선보다
                   **좌우 16px씩(합 32px) 넓게** 삐져나온 각진 사각형이었다
                   (실측: 줄 0~375px vs 구분선 16~359px).

                   좌우 padding은 0이다. 4px만 줘도 핀이 20px로 밀리는데, 헤더
                   "즐겨찾기"·검색창·칩이 전부 16px 레일에 서 있어서 이 줄만
                   어긋난다. 배경 왼쪽 끝과 핀 왼쪽 끝이 맞닿지만, 핀은 세로
                   가운데라 12px 곡률이 닿지 않는 자리다. */
                className="flex cursor-pointer items-center gap-2.5 rounded-xl py-3 transition-colors hover:bg-chip"
              >
                <span
                  aria-hidden
                  className={`shrink-0 ${favoriteRole(favorite) ? "text-brand" : "text-muted"}`}
                >
                  {favoriteRole(favorite) === "origin" ? (
                    <Navigation size={16} />
                  ) : favoriteRole(favorite) === "center" ? (
                    <MapPinCheck size={16} />
                  ) : (
                    <MapPin size={16} />
                  )}
                </span>
                {renamingId === favorite.id ? (
                  <input
                    autoFocus
                    value={renameDraft}
                    aria-label={isEn ? `Rename ${favorite.label}` : `${favorite.label} 이름 바꾸기`}
                    onChange={(event) => setRenameDraft(event.target.value)}
                    onClick={(event) => event.stopPropagation()}
                    onBlur={commitRename}
                    onKeyDown={(event) => {
                      /* 줄 전체가 버튼이라 여기서 끊지 않으면 키가 위로 올라간다. */
                      event.stopPropagation();
                      if (event.key === "Enter") commitRename();
                      if (event.key === "Escape") setRenamingId(null);
                    }}
                    className="min-w-0 flex-1 rounded-lg border border-brand px-2 py-1 text-sm text-ink outline-none"
                  />
                ) : (
                  /* 버튼이 아니라 그냥 글자다 — 눌리는 것은 줄 전체다. 예전에는
                     여기가 이름 바꾸기 버튼이었고, 그래서 남는 가로를 빈
                     스페이서(`min-h-6 flex-1`)로 따로 두어야 했다. 이름이 가로를
                     다 먹으면 어디를 눌러도 이름 편집이 됐기 때문이다. 지금은 그
                     전제가 없어져 스페이서를 걷고 이름이 남는 가로를 받는다. */
                  <div className="min-w-0 flex-1">
                    <span className="block truncate text-sm text-ink">{favorite.label}</span>
                    {favorite.address && (
                      <span className="block truncate text-xs text-muted">{favorite.address}</span>
                    )}
                  </div>
                )}
                {/* 이름을 고치는 중에는 저장 하나만 남긴다 — 연필은 이미 누른
                    버튼이고, 휴지통은 이름을 다 쓰고 누르려다 지우는 사고가 난다. */}
                {renamingId === favorite.id ? (
                  <button
                    type="button"
                    aria-label={isEn ? "Save name" : "이름 저장"}
                    onMouseDown={(event) => event.preventDefault()}
                    onClick={(event) => {
                      event.stopPropagation();
                      commitRename();
                    }}
                    className={`${ROW_ACTION_CLASS} text-brand`}
                  >
                    <Check size={15} />
                  </button>
                ) : (
                  <>
                    {/* 휴지통 왼쪽이다 — 이름 편집이 글자에서 여기로 옮겨왔다. */}
                    <button
                      type="button"
                      aria-label={isEn ? `Rename ${favorite.label}` : `${favorite.label} 이름 바꾸기`}
                      onClick={(event) => {
                        event.stopPropagation();
                        startRename(favorite.id, favorite.label);
                      }}
                      className={`${ROW_ACTION_CLASS} text-muted hover:text-brand`}
                    >
                      <Pencil size={15} />
                    </button>
                    <button
                      type="button"
                      aria-label={isEn ? `Delete ${favorite.label} from favorites` : `${favorite.label} 즐겨찾기 삭제`}
                      onClick={(event) => {
                        event.stopPropagation();
                        setFavorites((prev) => prev.filter((item) => item.id !== favorite.id));
                      }}
                      className={`${ROW_ACTION_CLASS} text-muted hover:text-rust`}
                    >
                      <Trash2 size={15} />
                    </button>
                  </>
                )}
              </div>
            ))
          )}
        </div>

        <h2 className="mt-2 text-xs font-bold text-label">{isEn ? "Recent searches" : "최근 검색"}</h2>
        <div className="divide-y divide-border border-t border-border">
          {recentSearches.length === 0 ? (
            <p className="py-3 text-sm text-muted">
              {isEn ? "No searches yet" : "아직 검색한 장소가 없어요"}
            </p>
          ) : (
            recentSearches.map((keyword) => (
              /* 즐겨찾기 줄과 같은 구조·같은 규칙이다.

                 hover 배경: 여기는 더 어긋나 있었다 — `-mx-4`에 `w-full`이 같이
                 붙어 폭은 부모(343px) 그대로인 채 위치만 왼쪽으로 밀려서, 배경이
                 왼쪽으로만 16px 넘치고 오른쪽은 16px 모자랐다(실측: 줄 0~343px vs
                 구분선 16~359px). 즐겨찾기 줄은 `div`라 양쪽으로 늘어났으니,
                 **같은 클래스가 두 목록에서 다르게 동작하고 있었다.**

                 `<button>`이던 줄을 `div role="button"`으로 바꿨다(2026-09-08) —
                 휴지통을 넣으려면 버튼 안에 버튼이 들어가야 해서다. 즐겨찾기 줄이
                 먼저 같은 이유로 이 모양이었고, aria-label을 줄에 직접 주는 것도
                 그쪽과 같다(안 주면 줄 이름에 휴지통 라벨까지 딸려 붙는다). */
              <div
                key={keyword}
                role="button"
                tabIndex={0}
                aria-label={isEn ? `Search ${keyword} again` : `${keyword} 다시 검색`}
                onClick={() => void runSearch(keyword)}
                onKeyDown={(event) => {
                  /* 줄 자체에 포커스가 있을 때만 검색한다 — 휴지통에서 누른
                     스페이스가 위로 올라가면 지우면서 검색까지 나간다. */
                  if (event.target !== event.currentTarget) return;
                  if (event.key !== "Enter" && event.key !== " ") return;
                  event.preventDefault();
                  void runSearch(keyword);
                }}
                className="flex cursor-pointer items-center gap-2.5 rounded-xl py-3 text-left transition-colors hover:bg-chip"
              >
                <Search size={16} className="shrink-0 text-muted" />
                <span className="min-w-0 flex-1 truncate text-sm text-ink">{keyword}</span>
                {/* 즐겨찾기와 달리 연필은 없다 — 최근 검색은 사용자가 친 말
                    그대로가 값이라 고칠 것이 없다. 다시 검색하면 새로 남는다. */}
                <button
                  type="button"
                  aria-label={
                    isEn
                      ? `Remove ${keyword} from recent searches`
                      : `${keyword} 최근 검색에서 삭제`
                  }
                  onClick={(event) => {
                    event.stopPropagation();
                    setRecentSearches(forgetRecentSearch(keyword));
                  }}
                  className={`${ROW_ACTION_CLASS} text-muted hover:text-rust`}
                >
                  <Trash2 size={15} />
                </button>
              </div>
            ))
          )}
        </div>
      </div>

      {pendingPlace !== null && (
        <LocationPurposeModal
          placeName={pendingPlace}
          onPick={applyPurpose}
          onClose={() => setPendingPlace(null)}
        />
      )}

      {showFavoritesLimit && (
        <FavoritesLimitModal max={MAX_FAVORITES} onClose={() => setShowFavoritesLimit(false)} />
      )}
    </main>
  );
}
