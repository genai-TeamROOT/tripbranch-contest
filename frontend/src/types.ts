/*
 * 역할: 프론트엔드 전역에서 공유하는 TripBranch API와 상태 타입을 정의한다.
 * 입력: 런타임 입력 없음. 백엔드 API 계약을 기준으로 한 TypeScript 선언.
 * 출력: 컴포넌트, 상태, API 클라이언트가 사용하는 타입 별칭과 인터페이스.
 * 호출 시점: 빌드/타입체크와 각 모듈의 import 시 사용된다.
 * TODO: OpenAPI 생성이 도입되면 백엔드 스키마에서 자동 생성하도록 전환한다.
 */

export type WeatherCondition = "good" | "neutral" | "bad";

/** 화면 언어. Runtime은 한국어 계약을 유지하고, 영어는 API 경계에서 번역한다. */
export type Language = "ko" | "en";

export type EnvironmentType = "indoor" | "outdoor" | "mixed" | "unknown";

/** 실측 경로를 조회한 이동수단. 지금 서버가 실제로 내려보내는 값은 "walking"뿐이다. */
export type TravelMode = "walking" | "transit" | "driving";

export interface InterpretedConditions {
  location_query: string;
  preferred_categories: string[];
  weather_condition: WeatherCondition | null;
  search_radius_km: number;
  /*
   * 개발용 표시 전용. 위 4개 필드는 구형 계약이라 LLM이 추출한 14개 조건 중
   * 일부만 담을 수 있어(weather_intent/environment 등이 유실된다), 원본을 그대로
   * 실어 ConditionDebugMessage가 전부 보여줄 수 있게 한다.
   * /api/recommendations 요청에는 보내지 않는다(trip.ts에서 제거).
   * TODO: /api/chat 전환으로 LLMOutput을 직접 쓰게 되면 이 필드는 삭제한다.
   */
  raw_conditions?: UserConditions | null;
}

/**
 * 사진과 함께 화면에 그려야 하는 출처.
 *
 * Google Maps Platform 정책은 사진을 보여줄 때 작성자를 밝히고, 사용자가
 * source_uri로 원본 사진을 Google 지도에서 볼 수 있게 하라고 요구한다.
 * provider를 따로 두는 이유는 정책이 "Google Maps"라는 이름을 밝히라고 하기
 * 때문이다 — 작성자 이름만으로는 어디서 온 사진인지 알 수 없다.
 */
export interface ImageAttribution {
  provider: string;
  author_name: string;
  author_uri?: string | null;
  source_uri?: string | null;
}

export interface RecommendationItem {
  place_id: string;
  name: string;
  /** 대분류 코드. `restaurant`·`shopping` 같은 영어라 화면에 그대로 쓰지 않는다. */
  category: string;
  /*
   * TourAPI 신분류 중분류명(한식·전시시설·면세점). 화면에 보여줄 값은 이쪽이다.
   *
   * **None이 정상 값이다** — 카드 조회가 실패하면 안 붙는다(썸네일과 같은 이유로
   * 추천 자체는 살린다). 그때는 category를 한글로 옮겨 쓴다(placeCategoryLabel).
   * 실측에서는 추천 후보 7,448건이 모두 해석됐다(2026-09-08, 실패 0건).
   */
  category_label?: string | null;
  distance_km: number;
  remaining_minutes: number | null;
  /** D가 현재 적용한 당일 운영 구간으로 만든 표기값. 예: "09:00~18:00" */
  operating_hours_display?: string | null;
  /*
   * 실측 경로로 잰 이동 거리·시간과 그 이동수단. 세 값은 함께 채워지거나 함께
   * null이다. null이면 실측이 없다는 뜻이므로 distance_km(직선거리)로만 말한다 —
   * 프론트가 직선거리에 임의 속도를 곱해 시간을 만들면 근거 문장과 다른 값이
   * 표시된다(TP-102에서 41분 vs 24분으로 드러났다).
   */
  travel_distance_m?: number | null;
  travel_duration_seconds?: number | null;
  travel_mode?: TravelMode | null;
  environment_type: EnvironmentType;
  recommendation_reason: string;
  explanations: string[];
  warnings: string[];
  score: number;
  feature_scores: Record<string, number | null>;
  weights_used: Record<string, number>;
  /**
   * 취향 검색이 찾은 근거 문장 전부(유사도 내림차순). taste가 0이어도 검색
   * 자체가 실패한 것과 근거를 못 찾은 것을 구분할 수 있게 항상 채워진다 —
   * 빈 배열이면 컷을 넘는 근거가 없었다는 뜻이다. 개발자 디버그 화면 전용.
   */
  taste_evidence: TasteEvidenceQuote[];
  /** 태그가 D 취향 점수에 반영된 경우의 후보군 상대 점수(0~1). */
  taste_tag_score?: number | null;
  taste_tag_label?: string | null;
  taste_tag_documents?: number;
  taste_tag_details?: PreferenceTagScoreDetail[];
  /** 임베딩 평균 유사도 → 환산점수 → 태그 가산 후 최종 취향점수. */
  taste_embedding_similarity?: number | null;
  taste_embedding_score?: number | null;
  taste_combined_score?: number | null;
  /** D가 전체 후보를 정렬했을 때의 순위. 개발자 화면 전용. */
  scoring_rank?: number | null;
  /** 리뷰·블로그에서 문서 단위로 집계한 장소별 상위 취향 태그. */
  preference_tags?: PreferenceTagSummary[];
  /**
   * 대표 이미지. 백엔드가 COMPARE 전용 RecommendationCardTool을 빌려 채운다 —
   * 못 찾은 장소는 null/undefined로 오고, 카드는 그때 자리표시 칩을 그린다.
   */
  image_url?: string | null;
  /**
   * image_url이 404일 때 대신 그릴 주소(places.first_image_url). 대안이 없으면
   * null이다 — 같은 주소를 두 번 부르지 않도록 서버가 걸러 보낸다.
   *
   * 작은 썸네일(firstimage2)만 관광공사 서버에서 사라진 장소가 있다. 서버는 살아
   * 있는지 확인하지 않는다(추천 한 번에 확인 요청이 5~10건 붙는다) — 실패한
   * 카드에서만 PlaceThumbnail이 두 번째를 부른다.
   */
  image_url_fallback?: string | null;
  /**
   * image_url이 Google Places에서 온 사진일 때만 온다(관광공사 이미지가 하나도
   * 없던 장소). **값이 있으면 사진과 함께 반드시 그려야 한다** — Google 정책이
   * 작성자 표기와 원본 링크를 요구한다. 없앨 거면 사진도 같이 없애야 한다.
   */
  image_attribution?: ImageAttribution | null;
}

export interface PreferenceTagSummary {
  code: string;
  label: string;
  mention_count: number;
  /** 이번 발화의 취향과 실제 장소 태그가 일치할 때만 true. */
  is_query_match?: boolean;
}

export interface PreferenceTagScoreDetail {
  code: string;
  label?: string | null;
  positive_document_count: number;
  negative_document_count: number;
  candidate_max_positive_document_count: number;
  relative_score: number;
}

/*
 * 취향 태그 표가 실제로 읽는 것만 추린 형태. RecommendationItem을 통째로 다시
 * 싣지 않으려고 따로 둔다 — 표는 별도 메시지라 대화 저장소에 한 벌 더 들어가는데,
 * 점수·근거 문장까지 복사하면 저장 크기가 배로 커진다. RecommendationItem이 이
 * 모양을 만족하므로 그대로 넘겨도 된다.
 */
export interface PreferenceTagSummaryEntry {
  place_id: string;
  name: string;
  preference_tags?: PreferenceTagSummary[];
}

export interface TasteEvidenceQuote {
  text: string;
  similarity: number;
}

export type TravelOrigin = "search_center" | "user_location";

/**
 * 비차단형 전환 제안(D-071). travel_origin이 판정되지 않았고 사용자 위치와
 * 검색 기준점이 실제로 다를 때만 채워진다. 있을 때만 "OO 기준으로 다시 보기"
 * 버튼을 노출한다.
 */
export interface TravelOriginToggle {
  alternative_origin: TravelOrigin;
  alternative_origin_name: string;
}

export interface RecommendationsResponse {
  recommendations: RecommendationItem[];
  unverified_recommendations: RecommendationItem[];
  scoring_candidates?: RecommendationItem[];
  scoring_excluded_candidates?: ExcludedScoringCandidate[];
  travel_origin_toggle?: TravelOriginToggle | null;
  elapsed_ms: number;
}

export interface ExcludedScoringCandidate {
  place_id: string;
  name: string;
  category: string;
  distance_km: number;
  reason: "closed" | "already_shown" | "rejected" | string;
}

/** Gemini Audio API가 짧은 사용자 음성을 전사한 결과. */
export interface TranscriptionResponse {
  text: string;
  elapsed_ms: number;
  model: string;
}

/** 올린 사진과 분위기가 닮은 장소 한 곳. */
export interface PhotoSimilarPlace {
  content_id: string;
  title: string;
  /**
   * 코사인 유사도.
   *
   * **순위를 위한 값이지 "얼마나 닮았다"의 눈금이 아니다.** 사진끼리의 경계값을
   * 아직 재지 않아 컷 없이 상위 N곳을 그대로 받는다(D-094). 백분율로 보여주지
   * 않는다.
   */
  similarity: number;
  /**
   * 장소 벡터를 만든 사진 수. 1이면 대표 이미지 한 장으로 대체된 곳이라
   * 그 한 장에 좌우된다(D-087).
   */
  photo_count: number;
  address?: string | null;
  image_url?: string | null;
}

export interface PhotoSimilarPlacesResponse {
  places: PhotoSimilarPlace[];
  /**
   * 어디를 중심으로 찾았는지. "{center_name} 주변에서 분위기가 닮은 곳이에요"로
   * 보여준다. 지역명 없이 좌표로만 찾았으면 "현재 위치"다.
   */
  center_name: string;
  /**
   * 이 검색이 속한 대화. **세션 없이 보내도 채워져서 돌아온다** — 홈에서 발화
   * 없이 사진부터 올리면 이 응답이 그 대화의 시작이라 서버가 발급한다. 받아서
   * 저장하지 않으면 이어지는 발화가 또 새 대화를 시작한다.
   */
  session_id: string;
  /** 하드 필터를 통과해 사진 검색에 넘어간 후보 수. 0이면 볼 곳 자체가 없었다는 뜻이다. */
  candidate_count: number;
  /** 후보 상한에 걸려 잘린 수. 0이 아니면 반경을 좁히는 편이 낫다. */
  truncated_count: number;
  elapsed_ms: number;
}

export interface PlaceSearchCandidate {
  name: string;
  address: string | null;
  road_address: string | null;
  category: string | null;
  latitude: number;
  longitude: number;
}

export interface PlaceSearchResponse {
  places: PlaceSearchCandidate[];
  /**
   * 좌표는 있는데 서울 밖이라 서버가 뺀 수. 0인지 아닌지에 따라 화면 문구가
   * 갈린다 - 0이면 "찾은 곳이 없어요"이고, 0이 아니면 "서울 지역만 검색할 수
   * 있어요"다. 사용자가 오타를 고쳐야 할지 지역을 바꿔야 할지가 다르다.
   */
  outside_service_area_count: number;
}

export interface ScheduleItem {
  order: number;
  place_id: string;
  place_name: string;
  estimated_arrival: string;
  estimated_duration_min: number;
  travel_to_next_min: number | null;
  // 그 이동을 무엇으로 어떻게 잰 값인지 (TP-216). 백엔드는 항상 채워 보내지만
  // (app.schemas.ScheduleItem, 기본값 None/False) 기존 테스트 픽스처와 이 필드
  // 이전에 저장된 세션 복원분과도 호환되도록 optional로 둔다.
  // mode가 없으면 서버가 좌표를 못 구해 시간표 폴백값을 쓴 구간이다.
  travel_to_next_mode?: TravelMode | null;
  travel_to_next_measured?: boolean;
  /*
   * 도보로 이어지는 묶음 번호 (TP-243). 같은 번호끼리 한 묶음이고, 묶이지 않은
   * 자리는 null이다. 서버가 방문 순서의 이웃 구간을 재서 정한다
   * (app/schedule/budget.py cluster_ids_in_order).
   *
   * 이 필드 이전에 저장된 일정·세션 복원분과 호환되도록 optional로 둔다
   * (travel_to_next_mode와 같은 이유). 없으면 묶음 표시가 없을 뿐이다.
   */
  cluster_id?: number | null;
  /*
   * 일정 카드에 그릴 장소 사진. 편성 단계에서 후보(RecommendationItem)의 값을 그대로
   * 옮겨 담은 것이라(app/schedule/planner.py) 화면이 장소별로 다시 조회하지 않는다 —
   * `/chat/place-details`로도 얻을 수 있지만 그 경로는 INFO 전체(이름 재해석 + 외부
   * 조회 + 취향 인사이트)를 타서 정류장 수만큼 외부 호출이 나간다.
   *
   * 이 필드 이전에 저장된 세션 복원분·기존 테스트 픽스처와도 호환되도록 optional로
   * 둔다(warnings·travel_to_next_mode와 같은 이유). 없으면 PlaceThumbnail이 자리표시를
   * 그린다.
   */
  image_url?: string | null;
  /** image_url이 404일 때 대신 그릴 주소. PlaceThumbnail의 fallbackSrc로 넘긴다. */
  image_url_fallback?: string | null;
  /** Google Places 사진이면 함께 온다. 있으면 반드시 화면에 그린다. */
  image_attribution?: ImageAttribution | null;
  reason: string;
  // 백엔드가 항상 채워 보내지만(app.schemas.ScheduleItem, 기본값 []), 기존
  // 테스트 픽스처가 이 필드 없이 만든 객체와도 호환되도록 optional로 둔다.
  // estimated_arrival이 후보 운영시간과 어긋날 때 planner.py가 결정적으로
  // 채우는 경고 — LLM이 생성하지 않는다.
  warnings?: string[];
}

export interface ScheduleResult {
  items: ScheduleItem[];
  total_duration_min: number;
  route_summary: string;
  basis_note: string;
  /* 백엔드가 보고한 일정 편성 파이프라인 처리 시간(ms). RecommendationResult의
     server_elapsed_ms와 같은 역할이다(SCHEDULE-10 후속). */
  elapsed_ms: number;
}

/*
 * 저장한 일정. (SCHEDULE 카드 2)
 *
 * 화면 기록(session_messages)과 다르다 — 저것은 "그때 화면에 나갔던 것"이고
 * 이것은 사용자가 "이 일정을 쓰겠다"고 고른 것이라 이름을 붙이고 나중에 연다.
 */
export interface SavedScheduleSummary {
  id: string;
  title: string;
  /* 어느 대화에서 나왔는지. 세션은 30일 뒤 정리되지만 이 일정은 남으므로
   **없을 수 있다**를 전제로 쓴다. */
  session_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface SavedScheduleDetail extends SavedScheduleSummary {
  /* 저장 시점의 ScheduleResult 그대로다. **지금 기준으로 다시 계산한 값이 아니다** —
     도착 시각·이동 시간은 그때 기준이라 화면이 그 사실을 밝혀야 한다. */
  payload: ScheduleResult;
}

export interface SavedSchedulesResponse {
  items: SavedScheduleSummary[];
}

export interface ComparisonItem {
  place_id: string;
  place_name: string;
  rank: number;
  distance_km: number | null;
  remaining_minutes: number | null;
  environment_type: string | null;
  /** TRAVEL_TIME 전용(TP-105/106 실측 연결). 좌표는 실측 조회에만 쓰이고 화면에는 없다. */
  latitude: number | null;
  longitude: number | null;
  travel_distance_km: number | null;
  travel_walking_minutes: number | null;
  travel_driving_minutes: number | null;
  travel_transit_minutes: number | null;
}

export interface ComparisonResult {
  criteria: "time" | "travel_time" | "overall";
  items: ComparisonItem[];
}

export interface PreferenceEvidenceQuote {
  polarity: "positive" | "mixed" | "negative";
  text: string;
  source_type: string;
  source_url?: string | null;
}

/**
 * 후기 답변의 근거가 된 문장 하나와 그 출처. 링크만 주면 사용자가 긴 블로그 글에서
 * 해당 대목을 직접 찾아야 해서, 문장을 함께 받아 인용으로 보여준다.
 * 초기 적재분 일부는 링크가 없어 `url`이 비는데, 그때는 인용만 그린다.
 */
export interface ReviewSource {
  text: string;
  url?: string | null;
  source_type?: string | null;
  published_at?: string | null;
}

export interface PlacePreferenceInsight {
  code: string;
  label: string;
  mention_count: number;
  positive_document_count: number;
  negative_document_count: number;
  evidence: PreferenceEvidenceQuote[];
}

/** INFO 장소 질의에 함께 내려오는 펼침형 상세 카드 데이터. */
export interface InfoPlaceCard {
  question_type: string;
  /** 사용자가 물어본 항목의 실제 답. 카드 전체 정보와 섞지 않는다. */
  answer_fields: Record<string, string>;
  place_id: string | null;
  place_name: string | null;
  /** 목적지 좌표. 지도 앱 길찾기 딥링크용. 좌표를 못 얻은 카드는 null. */
  latitude: number | null;
  longitude: number | null;
  thumbnail_url: string | null;
  /**
   * 여러 장 보기용 사진 목록. 순서가 곧 보여줄 순서이고 첫 번째가 대표 사진이다.
   * thumbnail_url을 대체하지 않는다 — 목록이 비어도 대표 이미지는 있는 장소가
   * 대부분이라 둘을 함께 본다.
   */
  photos?: PlacePhotoItem[];
  overview: string | null;
  operating_hours: string | null;
  rest_date: string | null;
  parking: string | null;
  parking_fee: string | null;
  fee: string | null;
  baby_carriage: string | null;
  pet: string | null;
  credit_card: string | null;
  restroom: string | null;
  homepage: string | null;
  /*
   * 무장애 여행 정보(D-077). 값이 있는 항목만 그리고, 아홉 개가 모두 비면 구획
   * 자체를 숨긴다 — 이 데이터는 있으면 적고 없으면 비우는 식이라 빈 값을 "없음"으로
   * 그리면 안 된다. 무장애 원문이 있는 장소는 전체의 15%이고, 아홉 중 하나라도
   * 나오는 곳은 11%다.
   *
   * stroller_rental이 차면 baby_carriage가 비고, 비면 baby_carriage가 남는다.
   * 두 값이 같은 사실을 말하는데 62%에서 어긋나 C가 하나만 골라 보낸다.
   */
  accessible_restroom?: string | null;
  accessible_parking?: string | null;
  elevator?: string | null;
  visual_guide?: string | null;
  wheelchair_rental?: string | null;
  nursing_room?: string | null;
  seating?: string | null;
  stroller_rental?: string | null;
  guide_dog?: string | null;
  preference_insights?: PlacePreferenceInsight[];
  /**
   * 후기로 답한 턴에서 그 답의 근거가 된 글. 답변 본문은 링크를 말하지 않고 이
   * 목록으로 "출처"를 그린다. 링크가 없는 근거는 백엔드가 담지 않는다.
   */
  review_sources?: ReviewSource[];
  population_current_level?: string | null;
  population_current_message?: string | null;
  population_observed_at?: string | null;
  /** 향후 예측 중 가장 붐빌 시간대 요약. 과거 추이는 원본 API 미제공으로 없다. */
  population_peak_forecast_summary?: string | null;
  population_forecasts?: PopulationForecastBar[];
  concentration_forecasts?: ConcentrationForecastBar[];
  realtime_area_name?: string | null;
  realtime_observed_at?: string | null;
  realtime_source_url?: string | null;
  realtime_map_url?: string | null;
  realtime_detail_items?: RealtimeInfoDetailItem[];
  /**
   * 서울시 실시간 인구·상권 요약. 실시간 혼잡도(concentration)와 실시간 상권
   * (realtime_commercial) 카드에만 실린다 — 두 유형만 서울시 citydata를 이미
   * 호출하므로 추가 호출 없이 채울 수 있다.
   */
  seoul_realtime_summary?: SeoulRealtimeSummary | null;
  /**
   * 도로 위 돌발상황 4분류(사고/고장 · 공사/집회 · 기상/화재 · 기타) 진행 건수.
   * realtime_traffic 전용, 0건 포함 항상 4개다.
   */
  road_incident_counts?: RoadIncidentCategoryCount[];
}

export interface RoadIncidentCategoryCount {
  label: string;
  count: number;
}

export interface SeoulRealtimePaymentCategory {
  label: string;
  activity_level?: string | null;
  payment_count?: number | null;
  /** 최근 10분 결제 금액 구간(원). 서울시가 단일 값을 주지 않는다. */
  payment_amount_min?: number | null;
  payment_amount_max?: number | null;
}

export interface SeoulRealtimeSummary {
  /** 현재 인구 지표 구간(명). 현재 단계·기준 시각은 population_* 필드에 있다. */
  population_min?: number | null;
  population_max?: number | null;
  /**
   * 앞으로 가장 붐빌 시간대. 서울시 앱의 "오늘의 인기 시간대"와 달리 과거를 포함한
   * 하루 통계가 아니라 예측이다 — 원본이 미래 12시간만 준다. 지금이 이미 예측
   * 피크만큼 붐비면 백엔드가 비워 보낸다.
   */
  peak_forecast_hour_label?: string | null;
  peak_forecast_level?: string | null;
  top_age_label?: string | null;
  top_age_rate?: number | null;
  /** 상권 구획은 서울시가 121곳 중 82곳에만 제공한다 — 없으면 통째로 감춘다. */
  commercial_level?: string | null;
  commercial_observed_at?: string | null;
  payment_count?: number | null;
  payment_amount_min?: number | null;
  payment_amount_max?: number | null;
  top_payment_categories?: SeoulRealtimePaymentCategory[];
}

export interface PopulationForecastBar {
  forecast_at: string;
  congestion_level: string | null;
  population_min: number | null;
  population_max: number | null;
}

export interface ConcentrationForecastBar {
  forecast_date: string;
  concentration_rate: number;
  concentration_level: string;
  concentration_label: string;
}

/** 장소 상세 화면에 여러 장으로 보여줄 사진 한 장. */
export interface PlacePhotoItem {
  url: string;
  /** detailImage2의 원본 파일명. 지금은 대체 텍스트 후보로만 쓴다. */
  image_name: string | null;
}

/** 서울시 실시간 도시데이터를 상세 모달에 표시하는 항목. */
export interface RealtimeInfoDetailItem {
  title: string;
  subtitle: string | null;
  details: Record<string, string>;
  thumbnail_url: string | null;
  external_url: string | null;
  /** 항목별 길찾기 목적지 좌표. 공중화장실처럼 목록의 각 항목이 곧 목적지인
   *  카드에서만 채워진다. 없으면 details["주소"]로 지도 검색을 폴백한다. */
  latitude?: number | null;
  longitude?: number | null;
}

/** 추천 카드 클릭 시 C PlaceDetails를 직접 조회하는 응답이다. */
export interface RecommendationPlaceDetailResponse {
  status: "success" | "no_data" | "unavailable";
  requested_place_id: string | null;
  place_card: InfoPlaceCard | null;
}

/**
 * "AI가 추천하는 이유" 문장 단건 응답. 상세조회와 **다른 호출**로 받는다 — 한
 * 응답에 묶으면 문장 생성에 드는 1~2초가 주소·운영시간·사진이 뜨는 시각을 그대로
 * 민다.
 *
 * **null이 정상 값이다.** 취향 태그가 없는 장소이거나, 서버 설정이 꺼졌거나,
 * 생성이 실패한 경우 전부 null이다. 화면은 그때 카드가 이미 들고 있는
 * `recommendation_reason` 한 줄만 보여준다.
 *
 * 서버는 항상 이 키를 싣는다. `?`는 부분 응답을 만드는 테스트 픽스처를 위한
 * 것이고(`InfoPlaceCard.road_incident_counts`와 같은 이유), 읽는 쪽은 undefined와
 * null을 같게 다룬다 — 둘 다 "문장이 없다"다.
 */
export interface PlaceReasonResponse {
  ai_reason?: string | null;
}

export type ChatPhase =
  "idle" | "interpreting" | "waiting_for_debug_confirmation" | "recommending" | "ready" | "error";

export type ChatMessage =
  | {
      id: string;
      type: "user_text";
      text: string;
    }
  | {
      id: string;
      type: "assistant_text";
      text: string;
      intent?: Intent;
      status?: LLMOutputStatus;
      /** SSE로 요약 문장을 받는 중인 말풍선이다. */
      streaming?: boolean;
      /** AgentResponse.message_footnote 그대로. 본문 아래 작고 옅은 글씨로 보여준다. */
      footnote?: string;
    }
  | {
      /*
       * 대화가 언제 오간 것인지 알리는 가운데 정렬 한 줄. 지난 대화를 되돌릴 때만
       * 넣는다 — 실시간 대화는 지금 오가는 중이라 밝힐 것이 없다.
       *
       * 배너로 "지난 대화예요"라고 문장을 띄우던 것을 대신한다. 메신저에서 늘
       * 보던 모양이라 읽지 않아도 뜻이 통하고, 화면을 덜 차지한다.
       */
      id: string;
      type: "time_separator";
      at: string;
      /** 앞부분이 남아 있지 않은 대화(화면 기록이 온전하지 않음). */
      partial?: boolean;
    }
  | {
      id: string;
      type: "interpretation_summary";
      text: string;
    }
  | {
      /*
       * 실패한 턴을 대화 안에 남긴다(TP-245). 가운데 정렬 한 줄이라 답변 말풍선과
       * 섞이지 않고, time_separator가 쓰던 모양을 따른다.
       *
       * **배너가 아니라 메시지인 이유.** 배너는 대화 목록 맨 위에 있어서 대화가
       * 길어지면 화면 밖으로 밀려났다. 이 화면은 새 메시지마다 맨 아래로
       * 스크롤하므로, 메시지로 넣으면 따로 붙잡아 두지 않아도 눈앞에 온다.
       *
       * 사용자 발화는 실패해도 대화에 남는다 — 실패도 그때 있었던 일이라 무엇에
       * 대한 실패인지가 위아래로 읽힌다.
       */
      id: string;
      type: "turn_error";
      text: string;
      /** 있으면 "다시 시도"가 붙고, 누르면 이 발화를 그대로 다시 보낸다. */
      retryInput?: string;
    }
  | {
      id: string;
      type: "photo_similar_result";
      /**
       * 올린 사진의 축소본(data URL). 무엇에 대한 답인지 이력에서 보이게 한다.
       * 브라우저가 못 여는 형식이면 null이고, 그때도 검색 결과는 그대로 나온다.
       */
      imageUrl: string | null;
      /**
       * 지난 대화를 되돌려 그린 말풍선인지. 참이면 사진 자리에 "사진은 저장하지
       * 않아 못 보여준다"는 안내를 대신 놓는다.
       *
       * **imageUrl이 비었다는 것만으로는 갈라낼 수 없다.** 실시간에도 브라우저가
       * 못 여는 형식(HEIC 등)이면 축소본이 없는데, 그때 "저장하지 않아서"라고
       * 말하면 틀린 설명이 된다.
       */
      restored?: boolean;
      /**
       * 검색이 끝나기 전에는 places가 없다. 사진만 먼저 띄우고 "찾는 중"을
       * 보여주기 위해서다 — 응답이 1~2초라 아무것도 없으면 멈춘 것처럼 보인다.
       *
       * failed는 요청이 실패한 경우다. 사유는 바로 뒤에 붙는 turn_error가 말하므로
       * 여기서는 올린 사진만 남긴다 — 채팅이 실패해도 사용자 발화를 남기는 것과
       * 같은 규칙이다(TP-245).
       *
       * location_required는 보낼 위치가 없어 요청을 아예 하지 않은 경우다. 실패와
       * 나누는 이유는 사용자가 할 일이 다르기 때문이다 — 실패는 다시 해보면 되고,
       * 이쪽은 위치를 먼저 정해야 한다.
       */
      status: "loading" | "done" | "failed" | "location_required";
      /** 어디를 중심으로 찾았는지. "내 주변에서 찾았어요"를 보여준다. */
      centerName: string;
      places: PhotoSimilarPlace[];
      /** 하드 필터를 통과해 검색에 넘어간 후보 수. 0이면 볼 곳 자체가 없었다. */
      candidateCount: number;
      elapsedMs: number;
    }
  | {
      id: string;
      type: "condition_debug";
      userInput: string;
      conditions: InterpretedConditions;
      /*
       * B가 병합한 누적 조건. 실제 추천에 쓰이는 값이며, 되묻기 턴에서는 이번 턴
       * 추출분(conditions.raw_conditions)과 달라진다 — 앞 턴 조건이 살아 있기 때문.
       */
      mergedConditions: UserConditions | null;
      /* 해당 사용자 발화에 대해 Agent가 최종 분류한 Intent. */
      intent?: Intent;
      status: "pending" | "confirmed";
    }
  /*
   * 로컬 테스트용 "/status" 명령의 결과. 서버 호출 없이 화면에만 쌓이며,
   * 조회에 실패하면 error에 사유가 담긴다.
   */
  | {
      id: string;
      type: "session_status";
      status: SessionContextResponse | null;
      error: string | null;
    }
  /*
   * 추천 카드 앞에 뜨는 고정 안내 한 줄(+작은 회색 보조설명). 문구는 언어별로
   * 고정이라 필드를 안 싣는다 — 렌더링 컴포넌트가 language로 직접 고른다
   * (2026-09-09, 카드 캡션 문구 통합). 결과가 0건인 턴에는 만들지 않는다.
   */
  | {
      id: string;
      type: "recommendation_caption";
    }
  | {
      id: string;
      type: "recommendation_result";
      recommendations: RecommendationItem[];
      unverified_recommendations: RecommendationItem[];
      /* 있을 때만 "OO 기준으로 다시 보기" 버튼을 노출한다(D-071). */
      travel_origin_toggle?: TravelOriginToggle | null;
      /* 추천 요청 클릭부터 응답 수신까지의 클라이언트 실측 시간(ms). */
      elapsed_ms: number;
      /* 백엔드가 보고한 서버 처리 시간(ms). 네트워크·렌더 시간은 포함하지 않는다. */
      server_elapsed_ms: number;
    }
  /*
   * 추천 결과에 딸린 동작 버튼(다른 장소 보기·반경 확대·기준 전환)만 담는다.
   * 카드와 갈라 둔 이유는 **수명이 다르기 때문이다** — 카드는 기록으로 남지만
   * 버튼은 다음 발화가 나가는 순간 걷어낸다(follow_up_suggestions와 같은 규칙).
   * 지난 턴의 버튼을 그대로 두면 그때 기준의 요청이 지금 맥락으로 나간다.
   */
  | {
      id: string;
      type: "recommendation_actions";
      /* 있을 때만 "OO 기준으로 다시 보기" 버튼을 노출한다(D-071). */
      travel_origin_toggle?: TravelOriginToggle | null;
      /* 그 턴이 빈손이었는가. 버튼 구성이 갈린다 — 빈손이면 "반경 넓혀 다시 찾기",
         아니면 "다른 장소 보기"다. 카드가 다른 메시지로 떨어져 나가서 여기서
         후보 목록을 다시 셀 수 없으므로 판정 결과를 실어 보낸다. */
      has_no_results: boolean;
    }
  /*
   * 추천 카드와 같은 턴에 붙는 장소별 취향 태그 표. 버튼과 달리 기록이므로
   * 걷어내지 않는다. 표가 실제로 읽는 세 필드만 담아 저장 크기를 키우지 않는다.
   */
  | {
      id: string;
      type: "preference_tag_summary";
      items: PreferenceTagSummaryEntry[];
    }
  | {
      /*
       * 지난 대화를 펼쳤을 때만 나온다. recommendation_result와 구조가 비슷해
       * 보이지만 합치지 않는다 — 이쪽은 점수·사진·운영시간이 없고, 특히
       * remaining_minutes로 "지금 영업 중"을 그리면 사흘 전 스냅샷으로 현재를
       * 말하는 것이 된다.
       */
      id: string;
      type: "past_recommendation_result";
      places: PastRecommendation[];
    }
  | {
      id: string;
      type: "schedule_result";
      schedule: ScheduleResult;
      /* 이 일정을 저장할 때 함께 보낸다. run_id는 같은 턴을 두 번 저장하지 않기
         위한 열쇠이고, session_id는 "어느 대화에서 나왔는지"를 남긴다. 응답이
         run_id 없이 끝나는 경로가 있어 둘 다 선택이다. */
      run_id?: string;
      session_id?: string;
      /* 일정 요청 클릭부터 응답 수신까지의 클라이언트 실측 시간(ms).
         recommendation_result의 elapsed_ms와 같은 역할이다. */
      elapsed_ms: number;
    }
  /*
   * 일정 결과에 딸린 재편성 버튼(다른 코스 보기·검색 범위 넓히기)만 담는다.
   * recommendation_actions와 같은 이유로 갈라 둔다 — 새 발화가 나가면 걷어낸다.
   *
   * "일정 저장하기"는 여기 없다. 그건 새 요청이 아니라 그 턴의 일정을 run_id로
   * 저장하는 것이라, 지난 일정을 나중에 저장하는 것도 정상적인 사용이다.
   * 그래서 저장 버튼은 schedule_result 쪽에 남는다.
   */
  | {
      id: string;
      type: "schedule_actions";
      /* 일정을 못 짠 턴인가. 못 짰으면 "검색 범위 넓혀서 다시 찾기"만 낸다. */
      has_no_schedule: boolean;
    }
  | {
      id: string;
      type: "place_info_result";
      card: InfoPlaceCard;
    }
  | {
      id: string;
      type: "compare_result";
      comparison: ComparisonResult;
    }
  /*
   * 인텐트와 무관하게 턴 하나가 완결된 답변을 냈을 때(되묻기·에러 제외) 그 턴의
   * 모든 메시지(텍스트+카드) 뒤에 한 번만 붙는 좋아요/싫어요 컨트롤. 결과별
   * 컴포넌트마다 따로 붙이지 않고 여기서 한 곳에 모아 모든 인텐트를 덮는다.
   */
  | {
      id: string;
      type: "feedback";
      sessionId: string;
      runId: string;
      intent?: Intent;
      userInput?: string;
      assistantMessage?: string;
    }
  | {
      id: string;
      type: "clarification";
      text: string;
      options: ClarificationOption[];
    }
  /*
   * 한 턴이 끝난 뒤 다음 발화를 제안하는 버튼 묶음. feedback과 마찬가지로 턴의
   * 맨 뒤에 한 번만 붙고, 사용자가 다음 발화를 보내는 순간 사라진다 — 대화를
   * 위로 거슬러 올라갔을 때 옛 턴의 버튼이 남아 있으면 어느 답변에 대한
   * 제안인지 알 수 없다.
   */
  | {
      id: string;
      type: "follow_up_suggestions";
      suggestions: string[];
    };

export interface ApiErrorBody {
  code: string;
  message: string;
  retryable: boolean;
  details: unknown;
}

// --- LLMOutput(Intent 분류 + 조건 추출) 관련 타입 ---
// backend/app/schemas.py의 LLMOutput 계약을 그대로 옮긴 개발용 디버그 타입.
// 화면 표시에 필요한 최소한만 좁혀서 선언하며, enum 값은 string으로 느슨하게 받는다.

export type Intent =
  "RECOMMEND" | "INFO" | "MODIFY" | "COMPARE" | "GENERAL" | "OUT_OF_SCOPE" | "SCHEDULE";

export type LLMOutputStatus = "complete" | "needs_clarification";

export interface UserConditions {
  current_location: string | null;
  search_center: string | null;
  place_types: string[];
  place_tags: string[];
  weather: string | null;
  weather_intent: string | null;
  concentration_intent?: string | null;
  transport: string | null;
  max_travel_time: number | null;
  travel_origin?: string | null;
  time_available: number | null;
  environment: string | null;
  companion: string | null;
  budget: string | null;
  exclude_tags: string[];
  special_requirements: string[];
  accessibility_needs?: string[];
  taste_query?: string | null;
  /*
   * 백엔드 UserConditions에는 아직 없는 필드다. Agent가 반경을 산출해 내려주게 되면
   * toLegacyConditions()가 이 값을 우선 사용하고, 없으면 기본값(2.0km)을 쓴다.
   */
  search_radius_km?: number | null;
}

export interface RecommendPayload {
  conditions: UserConditions;
}

export interface InfoPayload {
  place_name: string | null;
  place_context: string;
  question_type: string;
  specific_question: string | null;
}

export interface ModifyPayload {
  modify_type: "REJECT_ALL" | "CHANGE_CONDITION";
  condition_changes: UserConditions | null;
  changed_fields: string[];
}

export interface ComparePayload {
  targets: "all" | number[];
  criteria: string;
}

export interface GeneralPayload {
  topic: string;
  original_question: string;
}

export interface OutOfScopePayload {
  category: string;
  severity: string;
}

/** 되묻기에 붙는 버튼 하나. 클릭 시 id를 그대로 clarification_choice로 돌려보낸다. */
export interface ClarificationOption {
  id: string;
  label: string;
  resolved_intent: Intent;
}

export interface ClarificationPayload {
  missing_fields: { field: string; reason: string }[];
  ambiguous_fields: { field: string; user_input: string; candidates: string[]; reason: string }[];
  message: string;
  options: ClarificationOption[];
}

export interface LLMOutput {
  intent: Intent;
  status: LLMOutputStatus;
  recommend: RecommendPayload | null;
  info: InfoPayload | null;
  modify: ModifyPayload | null;
  compare: ComparePayload | null;
  general: GeneralPayload | null;
  out_of_scope: OutOfScopePayload | null;
  clarification: ClarificationPayload | null;
}

/*
 * /api/interpret 응답은 LLMOutput 자체가 아니라 세션 상태와 함께 감싼 형태다
 * (backend InterpretResponse). state는 현재 화면에서 쓰지 않아 좁게 선언한다.
 */
export interface InterpretResponse {
  output: LLMOutput;
  state: unknown;
}

export interface InterpretDebugRequest {
  user_input: string;
  has_previous_recommendation?: boolean;
  shown_place_count?: number;
  current_conditions?: Partial<UserConditions> | null;
}

// --- Agent Runtime(run_agent()) 디버그 관련 타입 ---
// backend/app/schemas.py의 AgentRequest/AgentResponse 계약을 그대로 옮긴 개발용 타입.

export interface AgentDebugRequest {
  user_input: string;
  language?: Language;
  session_id?: string | null;
  /*
   * 위치 설정 화면에서 고른 검색 위치의 이름(예: "안국역"). "어디를 기준으로
   * 찾을지"다. 이번 턴 발화가 위치를 말하지 않았을 때에만 검색 위치로 쓰인다 -
   * 발화가 이긴다. 기기 GPS 좌표(device_location)는 보내지 않는다 — 이 버전은
   * 위치를 이름으로만 받는다.
   */
  selected_search_center?: string | null;
  /*
   * 위치 설정 화면에서 정한 출발지의 이름. selected_search_center와 다른 질문의
   * 답이다 - 이쪽은 "사용자가 어디 있는가"라 이동 시간을 재는 시작점이 되고,
   * 저쪽은 "어디 주변을 찾을까"다(D-067이 둘을 분리한 이유). 발화가 출발지를
   * 말했으면 발화가 이긴다.
   */
  selected_current_location?: string | null;
  /** 직전 INFO 상세 카드의 장소명. "여기/이곳" 같은 대화 지시어 해소 후보다. */
  conversation_place_name?: string | null;
  /*
   * 최근에 후속 질문 버튼으로 보여준 문구. 서버가 같은 문구를 다시 권하지 않는 데 쓴다.
   *
   * **서버가 알 수 없는 값이라 화면이 보낸다.** 세션에 남는 것은 사용자가 실제로 한
   * 말뿐이라, 보여주기만 하고 누르지 않은 문구는 화면 말고 아는 곳이 없다.
   */
  recent_follow_ups?: string[];
  /*
   * 되묻기 버튼 클릭 시 ClarificationOption.id를 그대로 echo. user_input에는 버튼
   * label을 채워 보내되(채팅 이력 표시용) 라우팅은 이 필드만으로 결정된다.
   */
  clarification_choice?: string | null;
  /*
   * "OO 기준으로 다시 보기" 비차단형 전환 버튼 클릭(D-071). user_input에는 버튼
   * label을 채워 보내되(채팅 이력 표시용) 라우팅은 이 필드만으로 결정된다 —
   * clarification_choice와 같은 이유로 classify_intent()를 다시 태우지 않는다.
   */
  travel_origin_override?: TravelOrigin | null;
  /*
   * 보관함 하단 바의 "이 장소들로 일정 짜기" 클릭(SCHEDULE-12 카드 3).
   * user_input에는 버튼 label을 채워 보내되 라우팅은 이 필드만으로 결정된다 —
   * classify_intent()를 다시 태우지 않는다. 보관함이 비어 있으면 서버가
   * 평소 경로로 폴백한다.
   */
  schedule_from_saved?: boolean;
  /*
   * 개발자용 채팅(/dev-chat) 전용 디버그 스위치. true면 이번 턴은 폐점 후보도
   * 항상 채점에 포함한다 — no_data_closed 되묻기를 매번 누르지 않고 강제로
   * 켤 수 있다.
   */
  debug_ignore_operating_hours?: boolean;
}

export interface SessionState {
  session_id: string;
  run_id: string;
  session_created: boolean;
  condition_version: number;
  condition_changed: boolean;
  user_conditions: UserConditions;
  shown_place_ids: string[];
  excluded_place_ids: string[];
  gps_expired: boolean;
  weather_expired: boolean;
}

export interface StateApplyResponse {
  session_id: string;
  run_id: string;
  session_created: boolean;
  user_conditions: UserConditions;
  api_context?: ApiContextView;
  condition_version: number;
  condition_changed: boolean;
  applied_operations?: StateOperation[];
  ignored_operations?: IgnoredStateOperation[];
  excluded_place_ids: string[];
  reset_applied: string | null;
}

export interface StateOperation {
  op: string;
  field: string | null;
  before_value?: unknown;
  after_value?: unknown;
  value?: unknown;
}

export interface IgnoredStateOperation {
  operation: StateOperation;
  reason: string;
}

/* GET /api/state/{session_id} 응답(계약 6.3절). 로컬 "/status" 표시에 쓴다. */
export interface ApiContextView {
  gps_location: string | null;
  api_weather: string | null;
  gps_expired: boolean;
  weather_expired: boolean;
}

/*
 * 사용자가 추천 카드에서 명시적으로 담은 장소 1건(SCHEDULE-12).
 * backend/app/state/schema.py의 SavedPlaceItem 계약을 그대로 옮긴다.
 *
 * recommended/rejected와 결정적으로 다른 점은 "누가 골랐는가"다 — 이건
 * 사용자가 능동적으로 고른 것이라 다음 SCHEDULE 턴에서 다르게 취급된다.
 */
export interface SavedPlaceItem {
  place_id: string;
  name: string;
  /** 어느 실행에서 노출된 것을 담았는지. 이력과 대조해 되짚을 때 쓴다. */
  saved_from_run_id: string;
  saved_at: string;
  latitude?: number | null;
  longitude?: number | null;
}

/*
 * 담기/빼기 응답. 담긴 목록 전체를 항상 함께 반환하므로 낙관적으로 갱신한 뒤
 * 이 값으로 확정하면 되고 별도 재조회가 필요 없다.
 *
 * changed는 이번 요청으로 실제 변화가 있었는지다. 같은 장소를 두 번 담거나
 * 담기지 않은 장소를 빼는 요청은 오류가 아니라 changed=false다(멱등).
 */
export interface SavedPlacesResponse {
  session_id: string;
  items: SavedPlaceItem[];
  changed: boolean;
}

/*
 * 계정 단위 취향(GET·PUT /api/preferences).
 *
 * session_id가 없다 — 이 값은 세션에 속하지 않고 사람에게 붙는다.
 * updated_at이 null이면 **그 계정이 한 번도 저장한 적이 없다는 뜻**이다.
 * 빈 목록을 저장한 경우("전부 해제")와 구분되며, 로컬 값을 올려보낼지
 * 판단하는 기준이 된다(state/preferenceSync.ts).
 */
export interface PreferencesResponse {
  items: SavedPreferenceItem[];
  updated_at: string | null;
}

/*
 * 계정 단위 즐겨찾기(GET/PUT /api/favorites). PreferencesResponse와 같은 모양이고,
 * updated_at이 null이면 "이 계정이 한 번도 저장한 적 없다"는 뜻이다 - 빈 목록을
 * 저장한 경우("전부 지움")와 구분되며, 이 기기의 값을 올릴지 판단하는 기준이
 * 된다(state/favoritesSync.ts).
 */
export interface FavoritesResponse {
  items: FavoritePlaceItem[];
  updated_at: string | null;
}

export interface FavoritePlaceItem {
  id: string;
  label: string;
  /* 검색에 나가는 장소 이름. 사용자가 label을 바꿔도 이 값은 그대로 둔다. */
  search_center_name?: string | null;
  address?: string | null;
}

export interface SavedPreferenceItem {
  label: string;
  source: "preference" | "place_tag" | "custom";
  /* preferenceStorage의 SavedPreference와 같은 모양을 유지한다 — 두 타입 사이를
     복사 없이 주고받으려면 codes의 readonly 여부까지 같아야 한다. */
  codes: readonly string[];
}

/*
 * 사이드바 채팅 히스토리의 한 줄(GET /api/sessions).
 *
 * 대화 내용은 담기지 않는다 — 목록을 그리는 데 필요한 것만 온다.
 * title은 첫 턴의 사용자 발화이거나 사용자가 바꾼 이름이고, 대화를 이어가도
 * 바뀌지 않는다(백엔드가 agent_states.title에 박아둔다).
 */
export interface ChatSessionSummary {
  session_id: string;
  title: string;
  /* 그 대화의 위치(처음 잡힌 search_center). 장소 이름이 아니다 — "블루보틀 성수"는
     그 대화가 무엇이었는지 말해주지 않지만 "성수동"은 말해준다. */
  location: string | null;
  last_active_at: string;
}

/** 저장된 대화 한 턴. 백엔드 app/state/schema.py의 ConversationTurn과 대응. */
export interface StoredConversationTurn {
  user_input: string;
  assistant_message: string | null;
  intent: string | null;
  place_names: string[];
  at: string;
}

/*
 * 지난 대화 하나(GET /api/sessions/{id}).
 *
 * turns는 **대화 전체가 아니다** — 백엔드가 MAX_RECENT_TURNS개만 보관한다.
 * resumable이 false면 세션 TTL(30분)이 지나 이어서 대화할 수 없다. 화면이 그
 * 사실을 밝혀야 한다.
 */
/*
 * 지난 대화에서 화면에 나갔던 장소 하나.
 *
 * **그때 본 카드를 그대로 되살릴 수는 없다.** 백엔드가 저장하는 것은 여기 있는
 * 값뿐이고 점수·근거 문장·사진·카테고리·운영시간은 기록 자체가 없다
 * (실측 459건: 이름 100%, 거리·실내외 87%, 이유 13%). 그래서 RecommendationItem이
 * 아니라 별도 타입이다 — 없는 필드를 빈 값으로 채워 넣으면 화면이 "그때 그
 * 카드"인 척하게 된다.
 */
export interface PastRecommendation {
  place_id: string;
  /** 같은 턴에 함께 나간 장소를 한 묶음으로 되돌리는 열쇠. */
  run_id: string;
  name: string;
  rank: number;
  distance_km: number | null;
  environment_type: string | null;
  reason: string | null;
  shown_at: string;
}

/*
 * 그 턴에 화면으로 나갔던 것 전부(session_messages 한 행).
 *
 * payload는 그 턴의 AgentResponse 그대로다. 백엔드는 이걸 열어보지 않고
 * 보관만 한다 — 화면이 실시간과 **같은 함수**(buildAgentMessages)로 다시
 * 그리기 위한 것이라, 해석하는 쪽이 프론트 하나뿐이어야 갈라지지 않는다.
 */
export interface StoredSessionMessage {
  session_id: string;
  run_id: string | null;
  user_id: string | null;
  user_input: string | null;
  payload: AgentResponse;
  recorded_at: string;
}

export interface ChatSessionDetail {
  session_id: string;
  title: string;
  /* 모델 맥락. 최근 5턴만 남는다 — restore_from_messages면 화면은 이걸 쓰지 않는다. */
  turns: StoredConversationTurn[];
  /* 저장된 조각으로 만든 근사치. restore_from_messages가 false일 때만 채워진다. */
  recommendations: PastRecommendation[];
  /* 화면 기록. 그때 화면에 나갔던 것 그대로다. */
  messages: StoredSessionMessage[];
  /*
   * messages만으로 대화를 그대로 되돌릴 수 있는지. **판정은 백엔드 한 곳에서만
   * 한다** — 같은 계산을 여기에도 두면 한쪽만 바뀌는 순간 조용히 갈라진다.
   * false면 turns/recommendations로 되돌리고 "마지막 부분"이라고 밝혀야 한다.
   */
  restore_from_messages: boolean;
  last_active_at: string;
  resumable: boolean;
}

export interface ChatSessionsResponse {
  sessions: ChatSessionSummary[];
}

export interface SessionContextResponse {
  session_id: string | null;
  session_exists: boolean;
  has_recommendation: boolean;
  recommended_count: number;
  shown_place_ids: string[];
  excluded_place_ids: string[];
  last_recommended_run_id: string | null;
  last_intent: string | null;
  pending_clarification: string | null;
  /*
   * 사용자가 담은 장소(담은 순서). shown_place_ids와 달리 마지막 run으로
   * 좁히지 않아 여러 턴에 걸쳐 담은 것이 전부 들어 있다.
   */
  saved_places: SavedPlaceItem[];
  user_conditions: UserConditions;
  api_context: ApiContextView;
  condition_version: number;
}

/**
 * POST /api/feedback 요청. backend/app/state/service.py의 RecordFeedbackRequest와 대응.
 * user_input/assistant_message는 피드백을 남긴 턴의 질문·답변 원문을 찾을 수 있을
 * 때만 채운다. reason_code는 집계용 표준 싫어요 사유, comment는 선택적 자유 입력이다.
 */
export type FeedbackReasonCode =
  | "intent_mismatch"
  | "clarification_unhelpful"
  | "context_not_preserved"
  | "location_misunderstood"
  | "conditions_not_applied"
  | "recommendation_not_suitable"
  | "other";

export interface RecordFeedbackRequest {
  session_id: string;
  run_id: string;
  rating: "like" | "dislike";
  /** 품질 분석용 사용자 발화 원문 및 최종 응답. */
  user_input?: string;
  assistant_message?: string;
  /** 이 피드백이 달린 턴의 Intent. */
  intent?: string;
  /** 싫어요의 개선용 표준 사유. 좋아요에는 보내지 않는다. */
  reason_code?: FeedbackReasonCode;
  /** 어떤 싫어요 사유에든 선택적으로 남기는 자유 입력(최대 500자). */
  comment?: string;
}

/** POST /api/feedback 응답. */
export interface RecordFeedbackResponse {
  recorded_at: string;
}

/** GET /api/feedback/stats의 intent 항목 1개. (TP-146) */
export interface FeedbackIntentCount {
  intent: string;
  count: number;
}

/**
 * GET /api/feedback/stats 응답. backend/app/state/service.py의
 * FeedbackStatsResponse와 대응.
 *
 * reason_code_counts는 표준 7개 사유 + "unclassified"(사유 없이 남긴
 * dislike) 키를 항상 전부 포함한다. like 행은 여기 안 들어간다.
 * top_intents는 상위 N개(요청한 top_intents 개수)만 담고, 그 뒤 롱테일은
 * other_intent_count로 합쳐진다. intent 자체가 없는 행은 missing_intent_count.
 */
export interface FeedbackStatsResponse {
  since: string | null;
  until: string | null;
  total: number;
  rating_counts: Record<"like" | "dislike", number>;
  reason_code_counts: Record<FeedbackReasonCode | "unclassified", number>;
  top_intents: FeedbackIntentCount[];
  other_intent_count: number;
  missing_intent_count: number;
}

export interface TraceStepStat {
  step: string;
  count: number;
  avg_latency_ms: number | null;
  max_latency_ms: number | null;
  error_count: number;
}

export interface TraceRecentError {
  session_id: string;
  run_id: string;
  step: string;
  error_type: string;
  recorded_at: string;
}

/**
 * GET /api/trace/stats 응답. backend/app/state/service.py의
 * TraceStatsResponse와 대응.
 *
 * step_stats는 reason_code_counts와 달리 고정된 값 집합이 아니다 —
 * 등장한 step만 담긴다(step은 A/C/D가 자유롭게 붙이는 문자열이라
 * B가 미리 알 수 없다). recent_errors는 error_type이 있는 행만
 * 최근순으로 상위 N건(요청한 recent_errors_limit개).
 */
export interface TraceStatsResponse {
  since: string | null;
  until: string | null;
  total: number;
  step_stats: TraceStepStat[];
  recent_errors: TraceRecentError[];
}

export interface AgentResponse {
  llm_output: LLMOutput;
  state: StateApplyResponse;
  recommendations: RecommendationsResponse | null;
  schedule?: ScheduleResult | null;
  comparison?: ComparisonResult | null;
  info_place_card?: InfoPlaceCard | null;
  /** 근처 주차장/공영주차장처럼 짝인 실시간 질문을 하나 물으면 다른 쪽도 이어서
   * 조회해 둘째 카드로 붙인다(TP-115). info_place_card 다음 말풍선으로 순차 표시한다. */
  secondary_info_place_card?: InfoPlaceCard | null;
  message: string;
  /** message에 넣기엔 긴 부가 정보(D-085). 있으면 본문 아래 작고 옅은 글씨로 보여준다. */
  message_footnote?: string | null;
  /**
   * 이 턴 뒤에 버튼으로 보여줄 다음 발화 후보(0~3개). 누르면 이 문구가 그대로
   * user_input으로 재전송된다 — 되묻기 버튼(ClarificationOption)이 id로 Intent를
   * 못 박는 것과 다르다.
   */
  suggested_follow_ups?: string[];
  llm_execution?: LLMExecutionMetadata | null;
  tool_execution?: ToolExecutionDebug | null;
  tool_executions?: ToolExecutionDebug[];
}

export type AgentProgressStage =
  | "interpreting"
  | "merging_conditions"
  | "fetching_context"
  | "scoring"
  | "scheduling"
  | "composing_message";

export interface AgentProgressEvent {
  stage: AgentProgressStage;
  message: string;
  elapsed_ms: number;
}

/** SSE 서버 경과 시간을 바탕으로 계산한 Agent 단계별 실행 구간. */
export interface AgentStageTiming {
  stage: AgentProgressStage;
  message: string;
  started_at_ms: number;
  duration_ms: number;
  /** 답변 스트림의 message_start부터 첫 message_delta까지 걸린 시간(TTFT). */
  time_to_first_token_ms?: number;
}

export interface AgentStreamResultEvent {
  elapsed_ms: number;
  llm_output: LLMOutput;
  state: StateApplyResponse;
  recommendations: RecommendationsResponse;
  /** 카드 바로 위에 즉시 표시할 고정 안내문. */
  message?: string;
}

/*
 * 이번 턴이 실제로 쓸 출발지·검색 기준. 조건 병합 직후에 오므로 도구 조회·채점·
 * 답변 스트리밍보다 앞선다 — 상단 위치 칩이 결과를 기다리지 않고 바뀐다.
 *
 * 두 값은 각각 null일 수 있다. 그때는 "이 위치를 지우라"가 아니라 "서버도 위치를
 * 모른다"는 뜻이라, 화면은 지금 값을 그대로 둔다
 * (state/locationSettings.ts의 syncLocationSettingsFromConditions).
 *
 * 단발 POST /api/chat 폴백에는 이 이벤트가 없다 — 그 경로는 done의 state로 같은
 * 값을 받는다.
 */
export interface AgentStreamLocationResolvedEvent {
  elapsed_ms: number;
  current_location: string | null;
  search_center: string | null;
}

export interface AgentStreamMessageDeltaEvent {
  elapsed_ms: number;
  text: string;
}

/** 카드 없이 LLM 본문을 먼저 표시할 때 로딩 말풍선을 연다. */
export interface AgentStreamMessageStartEvent {
  elapsed_ms: number;
  intent: Intent;
}

export interface AgentStreamDoneEvent {
  elapsed_ms: number;
  response: AgentResponse;
}

export interface AgentStreamErrorEvent extends ApiErrorBody {
  elapsed_ms: number;
}

export type AgentStreamEvent =
  | { type: "progress"; data: AgentProgressEvent }
  | { type: "result"; data: AgentStreamResultEvent }
  | { type: "location_resolved"; data: AgentStreamLocationResolvedEvent }
  | { type: "message_start"; data: AgentStreamMessageStartEvent }
  | { type: "message_delta"; data: AgentStreamMessageDeltaEvent }
  | { type: "done"; data: AgentStreamDoneEvent }
  | { type: "follow_ups"; data: AgentStreamFollowUpsEvent }
  | { type: "error"; data: AgentStreamErrorEvent };

/*
 * done **뒤에** 오는 유일한 이벤트다. 후속 질문 생성은 답변이 이미 화면에 다 뜬 뒤에
 * 도는 호출이라, done보다 앞에 두면 그 시간만큼 턴이 안 끝나 답변과 카드 아래에
 * 로딩 말풍선이 한 번 더 뜬 것처럼 보인다(D-102). 제안할 게 없으면 서버가 이 이벤트를
 * 아예 보내지 않는다.
 */
export interface AgentStreamFollowUpsEvent {
  suggestions: string[];
  elapsed_ms: number;
}

export interface ToolProviderDebug {
  source: string;
  status: string;
  retrieved_at: string | null;
}

/** fetched=false는 C가 그 항목을 조회하지 않았다는 뜻 — 조회 후 실패와 구분된다. */
export interface ToolContextItemDebug {
  key: string;
  fetched: boolean;
  status: string | null;
  error_code: string | null;
  warning_codes: string[];
  item_count: number | null;
}

export interface CandidateConcentrationDebug {
  place_id: string;
  name: string;
  status: string;
  is_proxy: boolean;
  /** 값을 빌려온 실제 장소와 후보로부터의 거리. is_proxy=false면 둘 다 null. */
  proxy_place_name: string | null;
  proxy_distance_km: number | null;
}

/*
 * 이번 턴에 쓰인 위치 하나. name은 지오코딩 결과가 아니라 사용자가 말한 원문이다
 * (백엔드 LocationDebug 주석 참고 — resolved_name은 도로명 주소라 표시용이 아니다).
 * source가 "device_gps"면 부를 이름이 없어 name이 null이다.
 */
export interface LocationDebug {
  name: string | null;
  /**
   * "search_center"는 사용자 위치를 몰라 검색 위치를 시작점으로 대체했다는 뜻이다.
   * "travel_origin_override"는 사용자 위치를 알면서도 발화가 조사로 출발점을
   * 확정해("안국역에서 10분", D-071) 검색 위치를 고른 것이다 — 대체가 아니라
   * 정상 동작이라 둘을 구분한다.
   */
  source: "query" | "device_gps" | "search_center" | "travel_origin_override";
  latitude: number;
  longitude: number;
}

export interface ToolExecutionDebug {
  operation?:
    | "context_fetch"
    | "info_concentration"
    | "info_realtime_commercial"
    | "info_realtime_population"
    | "info_realtime_citydata"
    | "candidate_enrichment"
    | "compare_fetch";
  request_id: string;
  status: string;
  latency_ms: number | null;
  providers: ToolProviderDebug[];
  context_items: ToolContextItemDebug[];
  rule_versions: Record<string, string>;
  resolved_location_name: string | null;
  resolved_location_address: string | null;
  /*
   * 위치 세 갈래. RECOMMEND(context_fetch)에서만 채워진다 — INFO/COMPARE는 C의 위치
   * 해석을 거치지 않고 A가 기기 GPS로 직접 경로를 조회한다. 이전 실행 이력에는
   * 없을 수 있어 optional로 둔다.
   */
  search_location?: LocationDebug | null;
  user_location?: LocationDebug | null;
  route_origin?: LocationDebug | null;
  error_code: string | null;
  clarification_code: string | null;
  is_proxy: boolean | null;
  /**
   * info_realtime_population 전용. 우리 121곳 목록엔 없지만 서울시 API는 실제로
   * 지원하는 지역을 찾았을 때만 채워진다(TP-141/D-084). 응답 판정에는 영향을
   * 주지 않는 감시용 신호 — 이전 실행 이력에는 없을 수 있어 optional로 둔다.
   */
  stale_area_detected?: {
    probed_area_name: string;
    probed_area_code: string | null;
    matched_area_name: string;
    matched_area_distance_km: number;
  } | null;
  candidate_status_counts: Record<string, number>;
  /** candidate_enrichment 전용: 후보별로 혼잡도가 어디서 온 값인지. */
  candidate_concentration?: CandidateConcentrationDebug[];
}

export interface LLMCallMetadata {
  operation: string;
  attempted_models: string[];
  served_model: string | null;
  /** 구조화 LLM 호출 전체 경과 시간(ms). 이전 실행 이력에는 없을 수 있다. */
  latency_ms?: number | null;
  /**
   * 같은 모델에 대해 타임아웃·429·5xx로 다시 시도한 횟수(0=첫 시도에서 끝남).
   * 재시도가 성공하면 로그도 안 남고 attempted_models도 안 늘어나, 이 값이
   * 없으면 latency_ms가 큰 이유가 "모델이 느렸다"인지 "재시도했다"인지 구분이
   * 안 된다. 스트리밍 호출은 항상 0. 이전 실행 이력에는 없을 수 있다.
   */
  retry_count?: number | null;
}

export interface LLMExecutionMetadata {
  calls: LLMCallMetadata[];
}

export interface DeveloperAuditFailure {
  code: string;
  message: string;
  retryable: boolean;
  details: unknown;
}

export interface DeveloperAuditTurn {
  id: string;
  userInput: string;
  intent: Intent | "ERROR";
  status: LLMOutputStatus | "error";
  message: string;
  sessionId: string | null;
  runId: string | null;
  elapsedMsClient: number;
  serverElapsedMs: number | null;
  stageTimings: AgentStageTiming[];
  extractedConditions: InterpretedConditions | null;
  beforeConditions: UserConditions | null;
  afterConditions: UserConditions | null;
  recommendations: RecommendationsResponse | null;
  response: AgentResponse | null;
  failure: DeveloperAuditFailure | null;
}

/*
 * POST /api/chat 요청·응답. 현재 백엔드가 AgentRequest/AgentResponse를 그대로
 * 사용하므로 별칭으로 둔다.
 * TODO: 공개 계약이 좁혀지면(D-016) 이 타입을 독립 선언으로 바꾼다.
 */
export type ChatRequest = AgentDebugRequest;
export type ChatResponse = AgentResponse;
