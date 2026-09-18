begin;

-- Google Places에서 좌표 기반으로 매칭한 장소 프로필이다.
-- TourAPI 원본 장소명·좌표·행정구역은 public.places가 진실의 원천이므로 여기서는
-- 중복 저장하지 않고 content_id로만 연결한다.
create table public.place_google_profiles (
  content_id text primary key,
  google_place_id text not null,
  google_name text not null,
  google_maps_uri text,
  matched_distance_m double precision not null,
  google_review_total integer,
  google_rating numeric(2, 1),
  google_primary_type text,
  google_types jsonb not null default '[]'::jsonb,
  google_price_level text,
  google_price_range jsonb,
  google_regular_opening_hours jsonb,
  google_outdoor_seating boolean,
  google_good_for_children boolean,
  google_good_for_groups boolean,
  google_allows_dogs boolean,
  google_reservable boolean,
  google_serves_breakfast boolean,
  google_serves_lunch boolean,
  google_serves_dinner boolean,
  google_serves_coffee boolean,
  google_serves_dessert boolean,
  google_serves_vegetarian_food boolean,
  google_dine_in boolean,
  google_takeout boolean,
  google_parking_options jsonb,
  google_accessibility_options jsonb,
  google_photo_count integer,
  google_photos jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  constraint place_google_profiles_content_id_fk
    foreign key (content_id)
    references public.places (content_id)
    on delete cascade,
  constraint place_google_profiles_distance_nonnegative
    check (matched_distance_m >= 0),
  constraint place_google_profiles_review_total_nonnegative
    check (google_review_total is null or google_review_total >= 0),
  constraint place_google_profiles_rating_valid
    check (google_rating is null or google_rating between 0 and 5),
  constraint place_google_profiles_photo_count_nonnegative
    check (google_photo_count is null or google_photo_count >= 0),
  constraint place_google_profiles_types_is_array
    check (jsonb_typeof(google_types) = 'array'),
  constraint place_google_profiles_price_range_is_object
    check (google_price_range is null or jsonb_typeof(google_price_range) = 'object'),
  constraint place_google_profiles_opening_hours_is_object
    check (
      google_regular_opening_hours is null
      or jsonb_typeof(google_regular_opening_hours) = 'object'
    ),
  constraint place_google_profiles_parking_is_object
    check (google_parking_options is null or jsonb_typeof(google_parking_options) = 'object'),
  constraint place_google_profiles_accessibility_is_object
    check (
      google_accessibility_options is null
      or jsonb_typeof(google_accessibility_options) = 'object'
    ),
  constraint place_google_profiles_photos_is_array
    check (google_photos is null or jsonb_typeof(google_photos) = 'array')
);

create index place_google_profiles_google_place_id_idx
  on public.place_google_profiles (google_place_id);

create trigger place_google_profiles_set_updated_at
before update on public.place_google_profiles
for each row execute function public.set_updated_at();

-- Google 원본 데이터는 서버 경로에서만 사용한다.
alter table public.place_google_profiles enable row level security;
revoke all on table public.place_google_profiles from anon, authenticated;

comment on table public.place_google_profiles is
  'Google Places API에서 장소별로 수집한 평점·유형·이용환경·사진 메타데이터. places와 content_id로 1:1 연결한다.';

commit;
