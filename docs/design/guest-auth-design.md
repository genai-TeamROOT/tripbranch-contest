# 게스트 로그인 설계 v0.1

- 작성일: 2026-08-19
- 상태: 초안 (구현 전)
- 관련 결정: `D-062`

## 1. 배경과 목표

현재 서비스에는 사용자 개념이 없다. 인증 헤더도, `user_id` 컬럼도 없고, 있는 것은
익명 `session_id`(`sess_...`) 하나뿐이다 — `app/state/session.py`가 발급하고, TTL
30분이 지나면 오류 없이 새로 발급한다(Package B 계약 5.2절).

정식 로그인 기능은 아직 들어오지 않았다. 그 전까지 게스트 상태로 서비스를 쓰게
하되, 다음 두 가지를 얻는 것이 이 설계의 목표다.

1. **로그인 관문을 미리 세운다.** 홈에 "게스트로 시작하기"를 두고, 나중에 그 자리에
   소셜·이메일 로그인이 들어와도 화면 구조와 라우팅이 그대로 유지되게 한다.
2. **게스트로 쌓인 데이터를 계정으로 승계한다.** 로그인 기능이 들어왔을 때 게스트
   기간의 세션·추천 이력이 그 계정의 것으로 이어져야 한다.

정식 로그인은 Supabase Auth로 가기로 정해져 있다. 이 설계는 그 전제 위에 선다.

## 2. 핵심 결정 — 게스트를 "로그인 안 한 상태"가 아니라 정식 사용자 한 명으로 만든다

Supabase Auth의 `signInAnonymously()`는 `auth.users`에 실제 행을 만들고
(`is_anonymous = true`) 정상 JWT를 발급한다. 나중에 `linkIdentity()`나
`updateUser()`로 카카오·이메일을 연결하면 **`sub`(uid)가 그대로 유지된 채**
`is_anonymous`만 false가 된다.

이 성질 덕분에 승계가 "데이터 이관"이 아니라 "플래그 전환"이 된다. 게스트 행을
정식 계정 행으로 복사하는 배치 작업 자체가 필요 없다.

자체 `guest_id`를 발급했다가 로그인 시점에 병합하는 방식과 비교하면, 병합 로직·부분
실패 복구·중복 소유자 처리를 전부 만들지 않아도 된다는 점이 결정적이다. 목표 2가
사실상 이 선택 하나로 해결된다.

## 3. 신원과 세션은 다른 층이다

```text
user     Supabase uid. 기기에 영속하고, 로그인 시 같은 uid로 승격된다.
 └─ session   sess_.... TTL 30분. 만료되면 조용히 재발급된다.
     └─ run       run_....
```

`session_id`의 발급·만료·재발급 규칙(B 계약 5.2절)은 **하나도 바꾸지 않는다.**
게스트 신원은 그 위에 얹는 층이다.

"탭을 닫아도 대화가 이어진다"는 별개 문제이므로 이번 범위에서 제외한다. 프론트의
`sessionStorage` 기반 상태 복원(`frontend/src/state/storage.ts`)은 그대로 둔다.

다만 **게스트 토큰 자체는 기기에 남아야 한다** — 남지 않으면 승계할 대상이 없다.
supabase-js가 기본으로 localStorage에 세션을 저장하고 자동 갱신하므로 별도 작업은
없다. 대화는 30분마다 끊기고 신원은 계속 유지되는 것이 의도한 동작이다.

## 4. 전달 계약 — 헤더로만 받는다

```text
Authorization: Bearer <supabase access token>
```

**`AgentRequest` body에 `user_id`를 넣지 않는다.** body에 두면 클라이언트가 임의의
uid를 적어 보낼 수 있다. 신원은 서명 검증을 통과한 토큰에서만 나온다.

신규 모듈:

| 파일 | 역할 |
|---|---|
| `backend/app/auth/principal.py` | `Principal(user_id: str, is_anonymous: bool)` |
| `backend/app/auth/verify.py` | JWKS 캐시 + JWT 검증 (`issuer={supabase_url}/auth/v1`, `audience="authenticated"`) |
| `backend/app/auth/dependency.py` | `get_principal()` (optional) / `require_principal()` (401) |

### 4-1. 검증은 공개키 로컬 검증으로 한다 (확정)

이 프로젝트의 Auth는 **비대칭 키(ES256)로 서명하고 공개키를 공개한다.** 확인 방법:

```bash
curl -s "$SUPABASE_URL/auth/v1/.well-known/jwks.json" -H "apikey: $PUBLISHABLE_KEY"
# {"keys":[{"alg":"ES256","kty":"EC","use":"sig","key_ops":["verify"],"kid":"...",...}]}
```

`key_ops`가 `["verify"]`다 — 이 키로는 검증만 되고 서명은 되지 않는다. 서명용
비공개키는 Supabase 밖으로 나오지 않으므로, 백엔드는 비밀값을 하나도 보관하지 않는다.
예전 방식(HS256 공유 비밀키)이었다면 JWT secret을 `backend/.env`에 두어야 했고 그
값이 유출되면 누구나 토큰을 위조할 수 있었다. 비대칭 방식은 그 위험 자체가 없다.

대안인 `GET /auth/v1/user` 호출은 새 의존성이 없지만 요청마다 외부 왕복이 붙고, 더
중요하게는 **Supabase Auth 장애가 전 API 장애로 번진다.** 공개키를 캐시해두면 Auth가
흔들려도 이미 발급된 토큰은 계속 검증된다. 그래서 로컬 검증으로 확정한다.
의존성으로 `pyjwt[crypto]`가 하나 늘어난다.

#### 검증 코드에서 반드시 지킬 것

이 방식의 위험은 "공개키가 새는 것"이 아니라 **검증 코드를 잘못 짜는 것**이다.

```python
jwt.decode(
    token,
    key,
    algorithms=["ES256"],                     # 헤더의 alg를 믿지 않는다
    issuer=f"{settings.supabase_url}/auth/v1",
    audience="authenticated",
)
```

- **알고리즘을 우리가 고정한다.** 토큰 헤더의 `alg`를 그대로 따르면 알고리즘 혼동
  공격이 성립한다 — 공격자가 `alg`를 `HS256`으로 바꾸고 **공개된 공개키를 HMAC
  비밀키로 써서** 서명하면, 순진한 검증기는 이를 통과시킨다. 공개키는 누구나 가질 수
  있으므로 아무나 토큰을 위조할 수 있게 된다. `alg: none`도 같은 계열이다. PyJWT는
  `algorithms`를 필수 인자로 받아 이 실수를 구조적으로 막는다.
- **JWKS 주소는 설정값으로 고정한다.** 토큰이 들고 오는 URL(`jku` 등)을 따라가서 키를
  받아오지 않는다. 공격자가 자기 키를 심을 수 있다.
- **`iss`를 확인한다.** 서명이 유효한 다른 Supabase 프로젝트의 토큰을 막는다.
- **`exp`를 확인한다.** PyJWT 기본 동작이지만 끄지 않는다.
- **`kid`가 캐시에 없으면 JWKS를 다시 받아온다.** 키 회전 시 캐시만 붙들고 있으면 그날
  전 요청이 401이 된다.

정작 더 큰 위험은 검증 방식이 아니라 **토큰 자체의 탈취**다. `localStorage`의 access
token을 XSS로 훔치면 그것은 정상 토큰이라 어떤 검증도 통과한다. 지금은 수명이 1시간이고
게스트라 피해 범위가 좁지만, 정식 로그인이 들어오면 XSS 방어를 따로 점검해야 한다.

라우트는 `principal: Principal | None = Depends(get_principal)`을 받아
`run_agent(request, principal=...)`로 넘긴다. `run_agent()`가 이미 키워드 전용 인자를
받는 형태라 시그니처 확장은 기존 호출부를 깨지 않는다.

## 5. 조용한 통과를 막는다

인증을 처음부터 필수로 걸면 기존 테스트, dev 패널, smoke 테스트가 모두 깨진다.
그래서 초기에는 optional로 둔다. 그런데 그대로 두면 "프론트가 토큰을 안 붙였는데
아무 일 없이 동작"하는 상태가 되고, 이는 `npm run dev`가 `.env`를 못 읽어 전 Provider가
fake로 뜨던 사건과 같은 성격의 실패다 — 오류 없이 잘못된 모드로 도는 것.

그래서 두 경우를 구분한다.

| 상황 | 처리 |
|---|---|
| 토큰 없음 | 통과. 단 `WARN` 로그를 남기고 응답 메타에 `authenticated: false`를 노출한다 |
| 토큰 있으나 검증 실패 | 익명으로 강등하지 않고 **401로 끊는다** (D-042와 같은 방향) |

Phase 2에서 "토큰 없는 요청 비율"을 로그로 관측하고, 0에 수렴한 뒤 Phase 4에서
`require_principal()`로 필수화한다. 부팅 시점 검증도 `validate_provider_config()`와
같은 자리에 둔다 — Supabase URL/JWKS 설정이 비어 있으면 첫 요청이 아니라 부팅에서
드러나야 한다.

## 6. 저장 스키마

마이그레이션: `supabase/migrations/202608200002_add_user_id_to_agent_state_tables.sql`
(TP-101 3단계, 2026-08-20 작성 — 아직 Supabase에 적용 전)

- `agent_states.user_id uuid null` + `(user_id, last_active_at)` 인덱스
- `recommendation_histories.user_id uuid null`
- `condition_change_logs` / `trace_records`는 `session_id`로 조인 가능하므로 이번엔
  추가하지 않는다

nullable로 두는 이유는 기존 행과 토큰 없는 요청을 함께 수용하기 위해서다.

코드 쪽에서는 `AgentState`에 `user_id: str | None`을 추가하고
`create_session(store, user_id=...)`로 받는다. B 계약 스키마에 필드가 늘어나므로
**기존 픽스처 전수 갱신이 함께 따라온다** — 필드만 추가하고 픽스처를 두면 현실에
없는 모양의 테스트 데이터가 남는다.

**RLS는 건드리지 않는다.** 프론트는 Supabase DB에 직접 붙지 않고 FastAPI만 통한다.
Supabase Auth는 신원 발급 용도로만 쓰고, 데이터 접근은 지금처럼 서버 secret key
단독 경로를 유지한다. 테이블의 `revoke ... from anon, authenticated` 상태를 그대로
둔다는 뜻이다.

### 6-1. 착수 전 결정 대기 (D-063)

6절은 "무엇을 저장할지"까지만 정해져 있다. 실제로 착수하려면 아래 네 가지를 먼저
정해야 하고, **모두 Package B 소유 영역이다.** 각 항목에 이 문서 작성자의 권장안과
근거를 붙여 두었으니 판단만 하면 된다. 논의는 `D-063`에서 이어간다.

| # | 결정할 것 | 권장 |
|---|---|---|
| 1 | `STATE_STORE_BACKEND`를 `supabase`로 전환할 시점 | Phase 3에 **포함하지 않음** |
| 2 | 소유권 검증(남의 `session_id` 거부)을 어느 Phase에 둘지 | D-073으로 부분 구현(2절 갱신 참고) |
| 3 | `user_id`가 비어 있는 세션에 신원이 붙으면 채울지 | 채우되 **덮어쓰기 금지** |
| 4 | `agent_states.user_id`에 `auth.users(id)` 외래키를 걸지 | **걸지 않음** |

#### 1. 저장소 전환 시점

`STATE_STORE_BACKEND`는 게스트 로그인 작업이 만든 설정이 아니다. `[B-05]`(2026-08-03)로
이미 들어와 있고 `SupabaseStateStore`·마이그레이션·부팅 검증까지 갖춰져 있다. 값만
바꾸면 되는 상태다.

문제는 기본값이 `memory`라는 점이다. 이대로 `user_id`를 저장하면 **서버 재시작마다
소유자 정보가 사라진다** — 코드는 완성되는데 승계할 데이터가 쌓이지 않는다.

권장: **전환을 Phase 3에 포함하지 않는다.** 컬럼·필드·경로를 모두 준비해 두고,
`supabase`로 켜는 순간 동작하는 상태까지만 만든다. 전환은 모든 세션 읽기·쓰기가
네트워크를 타게 되는 변경이라 지연·장애 특성이 달라지고, 그 판단은 저장소 소유자의
몫이다. 게스트 로그인 편의로 켜고 지나갈 성격이 아니다.

#### 2. 소유권 검증 위치

Phase 3이 끝나면 검증에 필요한 재료가 갖춰지지만(저장된 소유자 + 토큰의 신원), 정작
검증은 하지 않는 상태가 된다. 지금은 `session_id`만 알면 남의 세션도 조회된다.

권장: **Phase 4로 미룬다.** Phase 4가 인증 필수화라 "신원이 반드시 있다"가 전제되는
시점이고, 그때 소유자 대조를 함께 넣는 편이 자연스럽다. Phase 3에 넣으면 신원이 없는
요청(현재 optional)에서 어떻게 처리할지가 애매해진다.

**갱신(2026-08-24, D-073):** Phase 4 착수 시점이 계속 미정으로 남아 이 항목만 따로
먼저 닫았다. "신원이 없는 요청에서 어떻게 처리할지 애매해진다"는 문제는, `principal`이
있는 요청에서만 대조하고 없으면 그대로 통과시키는 것으로 해소했다 — Phase 3의 optional
인증 전제와 충돌하지 않는다. 전면 필수화(Phase 4)는 여전히 별도 결정 사항으로 남아
있다. 상세는 D-073 참고.

#### 3. 비어 있는 세션에 신원이 붙는 경우

연결은 `create_session()` 시점에 한 번 일어난다. 그래서 기존 세션과 토큰 없이 만들어진
세션은 `user_id`가 `null`로 남는다.

권장: **비어 있으면 채우고, 값이 있으면 절대 덮어쓰지 않는다.** 빈 칸을 채우는 것은
소유권 이전이 아니지만, 이미 다른 `user_id`가 있는 세션을 덮어쓰는 것은 소유권 탈취다.

#### 4. 외래키를 걸지 않는 이유

사용자 테이블은 이미 있다 — Supabase가 관리하는 `auth.users`이며, `signInAnonymously()`
호출마다 행이 하나씩 생긴다. `id`(uuid)가 우리가 저장할 값이다. 우리가 만들 것도, 만들어서도
안 된다(GoTrue 소유). 정식 로그인이 붙어도 새 행이 생기는 것이 아니라 기존 행의
`is_anonymous`가 false로 바뀔 뿐이다.

`public.agent_states.user_id`에서 `auth.users(id)`로 FK를 거는 선택지가 있으나, 걸지
않기를 권한다.

- `db-store-design-v2.md` §2-3이 **테이블 간 FK를 의도적으로 두지 않았다.**
  `delete_state`/`delete_history`가 독립적으로 호출되는 구조와 어긋난다는 이유였다.
  여기만 예외를 두면 일관성이 깨진다.
- `public` 스키마가 우리 통제 밖인 `auth` 스키마에 의존하게 된다.
- **오래된 익명 사용자 정리(10절)와 충돌한다.** FK가 있으면 삭제가 막히거나(`restrict`)
  세션까지 함께 지워진다(`cascade`). 어느 쪽도 의도한 동작이 아니다.

FK 없이 두면 사용자가 지워져도 세션 행은 남고 `user_id`가 고아값이 될 뿐이다. 그 처리는
정리 정책에서 따로 다룬다.

#### 마이그레이션 초안 (아직 적용하지 않음)

아래는 결정이 끝난 뒤 `supabase/migrations/`에 파일로 옮길 내용이다. **지금 그 디렉터리에
두지 않는 이유는 `supabase db push`나 MCP `apply_migration`이 집어갈 수 있기 때문이다.**
소유자 확인 전에는 문서 안에만 둔다.

```sql
begin;

-- D-062 Phase 3: 세션의 소유자를 기록한다.
-- nullable인 이유는 기존 행과 토큰 없는 요청을 함께 수용하기 위해서다.
-- auth.users(id)로의 FK는 의도적으로 걸지 않는다 (위 4번 참고).
alter table public.agent_states add column if not exists user_id uuid;
alter table public.recommendation_histories add column if not exists user_id uuid;

-- "이 사용자의 최근 세션"을 뽑는 조회가 승계·관측의 기본 질의다.
create index if not exists agent_states_user_id_last_active_idx
  on public.agent_states (user_id, last_active_at desc);

commit;
```

`condition_change_logs`와 `trace_records`는 `session_id`로 조인할 수 있어 이번 범위에서
제외한다.

#### 코드 쪽에서 함께 열리는 경로

필드 하나가 늘어나는 것에 비해 손대는 파일이 넓다. 값이 아래 경로를 통과해야 한다.

```text
routes/chat.py (principal)  →  run_agent(principal=...)        [A]
  →  state_transform.py                                        [A]
    →  StateApplyRequest                                       [B 계약]
      →  state/service.py apply()                              [B]
        →  session.get_or_create_session(store, ..., user_id=)  [B]
          →  create_session(store, user_id=...)                [B]
```

Phase 2는 의존성 안에서 끝나 `run_agent()`를 건드리지 않았으나, Phase 3은 이 체인을
전부 통과해야 한다. 그리고 `AgentState`에 필드가 늘어나므로 **기존 픽스처 전수 갱신이
함께 따라온다**(`tests/state/` 3개 파일이 `AgentState(...)`를 직접 만든다).

## 7. 프론트엔드

`@supabase/supabase-js`를 추가한다(현재 의존성에 없다). 환경변수는
`VITE_SUPABASE_URL`, `VITE_SUPABASE_PUBLISHABLE_KEY`를 쓴다. publishable key는 노출
전제 키이므로 프론트에 두어도 되지만, `supabase_secret_key`는 절대 프론트로 가지
않는다.

| 파일 | 내용 |
|---|---|
| `frontend/src/auth/supabaseClient.ts` | 클라이언트 생성. `persistSession: true` |
| `frontend/src/auth/AuthContext.tsx` | 부팅 시 `getSession()`, 게스트 진입 버튼에서 `signInAnonymously()` |
| `frontend/src/pages/HomePage.tsx` | 로그인 관문. "게스트로 시작하기" 버튼 |
| `frontend/src/App.tsx` | `<RequireUser>`로 `/chat`, `/dev-chat` 보호 (기존 TODO 자리) |
| `frontend/src/api/client.ts` | `Authorization` 주입 |

**익명 로그인은 버튼을 눌렀을 때만 호출한다.** 페이지 로드마다 자동 발급하면 크롤러가
긁기만 해도 `auth.users`가 쌓이고 MAU 과금으로 이어진다.

`client.ts`는 `request`, `requestBinary`, `streamPost` **세 군데 모두**에 헤더를 넣어야
한다. `requestBinary`는 `/api/transcribe`가 쓰는 경로라 빠뜨리기 쉽다. React 밖의
모듈 레벨 fetch 래퍼이므로 `setAuthTokenProvider()`로 토큰 getter를 주입하는 형태를
쓴다.

## 8. 승계 시나리오

정상 경로는 비용이 들지 않는다.

```text
게스트 사용 → linkIdentity({provider:'kakao'}) → 같은 uid, is_anonymous=false
             또는 updateUser({email, password})
```

백엔드는 아무 변경이 없다. `user_id`가 이미 채워져 있으므로 이력이 그대로 이어진다.

**미해결 케이스가 하나 있다.** 이미 계정이 있는 사람이 게스트 상태에서 그 계정으로
로그인하면 uid가 달라진다 — 이때만 실제 이관이 필요하다. 이번 범위에서는 "게스트로
쓰던 기록은 남지 않는다"는 확인 다이얼로그로 끊고, 실제 병합은 로그인 도입 시점에
별도 결정으로 다룬다.

**승계는 익명성도 함께 무너뜨린다.** uid가 유지된다는 것은, 계정을 연결하는 순간
그 이전의 게스트 기간 데이터가 실명 사용자에게 소급해서 귀속된다는 뜻이다. 승계를
공짜로 만들어주는 성질과 같은 성질이다. 자체 `guest_id`를 발급했다가 로그인 시
병합하는 방식이라면 "병합되지 않은 게스트 데이터는 끝까지 익명"이라고 말할 여지가
있지만, 이 설계는 처음부터 `auth.users`에 행을 만들므로 그 여지가 없다. 9절을 볼 것.

## 9. 개인정보 관점

게스트라고 해서 익명 데이터가 아니다. 8절대로 uid가 계정으로 승격되면 게스트 기간의
기록이 실명 사용자에게 귀속되므로, **"게스트니까 익명이라 괜찮다"는 판단은 이
설계에서 성립하지 않는다.** 나중에 대화 로그 저장을 검토하는 사람이 가장 잘못 짚기
쉬운 지점이라 여기 명시한다.

### 9-1. 이미 존재하는 노출

이 설계가 새로 만드는 문제가 아니라, 이미 있는 노출의 범위가 넓어지는 것이다.

- 사용자 발화가 매 요청 외부 LLM(Gemini)으로 나간다 — 처리위탁·국외이전에 해당한다.
- GPS 좌표가 `api_context.gps_location`에 저장된다. **위치정보는 개인정보보호법이
  아니라 위치정보법이 따로 규율하므로 요구 수준이 더 높다.**
- `trace_records`에 세션별 실행 기록이 쌓인다.

반대로 이미 잘 지켜지고 있는 것도 있다 — `POST /api/transcribe`는 녹음 파일을
저장하지 않고 요청 메모리에서만 쓴다(`app/routes/transcribe.py`). 음성은 그 자체로
식별력이 있어 저장 시 요구 수준이 크게 올라간다. 이 선택을 유지한다.

#### 발화를 LLM에 넘기는 것 자체는 문제가 아니다

법적 구조로는 처리위탁이다. 위탁은 별도 동의 없이도 개인정보처리방침에 수탁자와
위탁 업무를 공개하면 되는 구조다. 문제는 "넘기느냐"가 아니라 "어떤 경로로
넘기느냐"에서 갈린다.

**현재 경로는 Gemini Developer API(AI Studio)다.** `app/providers/gemini.py`가
`genai.Client(api_key=...)`로 붙는데, 이는 Vertex AI 경로가 아니다. 여기서 두 가지가
따라온다.

1. **무료 티어와 유료 티어의 성격이 다르다.** 무료 티어는 입력·출력이 Google의 제품
   개선과 모델 학습에 쓰일 수 있고, 결제가 연결된 유료 티어는 학습에 쓰지 않는다고
   약관에 명시돼 있다. 코드에서는 둘 다 같은 API 키 형태라 구분되지 않으므로
   `LLM_API_KEY`가 어느 쪽인지 별도로 확인해야 한다. 무료 키로 사용자 발화를 넘기고
   있다면 성격이 "외부 위탁 처리"가 아니라 "학습 데이터 제공"에 가까워진다.
2. **리전을 지정할 수 없어 사실상 국외로 나간다.** 국외이전은 위탁보다 요건이 한 단계
   높다(고지 또는 동의). 실서비스로 간다면 Vertex AI로 옮겨 `asia-northeast3` 등으로
   리전을 고정하는 선택지가 있다.

#### 프롬프트에 식별자를 넣지 않는다

현재 `app/providers/gemini_prompts.py`의 프롬프트 빌더에는 `session_id`,
`gps_location`, `device_location`이 **하나도 들어가지 않는다.** 외부로 나가는 것은
지시문과 발화 원문뿐이다.

**이 상태를 규칙으로 유지한다 — 게스트 uid·`session_id`·좌표를 프롬프트나 LLM 호출
메타데이터에 붙이지 않는다.** 식별자가 없으면 외부에 남는 것은 문맥 없는 발화
조각이라 결합할 실마리가 없다. 디버깅 편의로 `session_id`를 프롬프트에 넣고 싶어지는
순간이 오는데, 그 순간 외부 서비스 쪽에 결합 가능한 축이 생긴다. 추적이 필요하면
`trace_records`처럼 내부에만 남는 경로를 쓴다.

#### 전송과 저장은 다른 문제다

| | LLM 전송 | 서버 대화 로그 저장 |
|---|---|---|
| 성격 | 경유(휘발) | 축적 |
| 유출 시 영향 | 해당 요청 | 전체 이력 |
| 대응 수단 | 티어·약관·고지 | 보관기간·삭제권·접근통제 |

전송이 이미 이뤄지고 있다는 사실이 저장을 정당화하지 않는다. 9-4절의 결론은 그대로
유효하다.

### 9-2. 저장 대상별 위험도

| 대상 | 위험 | 비고 |
|---|---|---|
| 조건 15개(`user_conditions`) | 낮음 | 열거값 중심이라 예측 가능하다 |
| GPS 좌표 | 중간 | 위치정보법 대상. 정밀 좌표를 영속할 이유가 적다 |
| 대화 원문 | 높음 | 자유 텍스트라 무엇이 들어올지 통제할 수 없다 |

대화 원문이 특히 어렵다. "휠체어로 갈 수 있는 곳"(건강), "OO역 우리 집 근처"(주소)
처럼 민감정보나 식별정보가 섞여 들어올 수 있는데, 사용자가 무엇을 입력할지 서비스가
통제할 수 없다.

### 9-3. 대화 로그를 서버에 남기게 된다면

최소한 다음을 함께 결정한다.

- **보관기간을 정하고 자동 삭제한다**(예: 90일). 익명 사용자 정리 스케줄(10절)과 같은
  작업으로 묶는다.
- **게스트 진입 버튼을 동의 지점으로 쓴다.** 로그인 관문 UI를 만드는 김에 수집 항목·
  목적·보관기간 고지를 그 자리에 붙인다.
- **GPS는 좌표 원본 대신 행정동 수준으로 저장한다.** 서비스 범위가 종로구라 정밀
  좌표를 영속할 실익이 적다.
- 삭제 요구 경로를 정한다. 게스트는 uid밖에 없어 본인 확인 수단이 없으므로, 실질적으로
  "기기에서 로그아웃 = 접근 불가" 이상을 만들기 어렵다는 한계를 인정하고 시작한다.

### 9-4. 그래서 저장을 미루는 편이 낫다

대화 이어쓰기를 구현하는 세 방법은 개인정보 위험도 순서가 구현 비용 순서와 같다
(12절).

1. 프론트 `localStorage` — **서버에 대화가 남지 않으므로 이 절의 문제가 발생하지 않는다.**
2. 조건만 서버에서 복원 — 이미 저장 중인 구조화 값이라 추가 위험이 작다.
3. 서버에 대화 로그 저장 — 이 절의 논의가 전부 필요해진다.

체감 문제("새로고침하면 대화가 사라진다")는 대부분 1번으로 해결된다. 3번은 "기기를
바꿔도 대화가 이어져야 한다"는 요구가 실제로 생겼을 때 그 값어치와 함께 저울질한다.

## 10. 보안·운영 설정

- Supabase 대시보드에서 익명 로그인(Anonymous sign-ins)을 활성화한다. 코드로 켤 수
  없는 항목이다.
- **남용 방지는 rate limit을 먼저 쓰고, CAPTCHA는 조건부로 둔다.** 익명 로그인은
  이메일·비밀번호 같은 자연스러운 장벽이 없어 스크립트로 반복 호출하면 `auth.users`가
  계속 쌓이고, 익명 사용자도 MAU에 그대로 집계된다.
  - Authentication → Rate Limits의 익명 로그인 항목(기본 IP당 시간당 30회)을 먼저
    확인한다. 정상 사용자는 기기당 사실상 1회만 호출하므로 이 값으로 대량 생성은
    막힌다.
  - CAPTCHA(hCaptcha/Cloudflare Turnstile)는 **익명 사용자 수가 실제로 비정상적으로
    늘어날 때 켠다.** 켜는 순간 모든 auth 엔드포인트에 적용되므로 이후 소셜 로그인까지
    영향을 받고, 외부 서비스 가입·키 발급·프론트 위젯·`options.captchaToken` 전달
    배선이 함께 필요하다. 지금 단계에서 선제적으로 지불할 비용은 아니다.
  - 켤 때는 Turnstile이 사용자에게 화면을 거의 노출하지 않아 UX 손해가 작다.
- 오래된 익명 사용자 정리 스케줄을 잡는다(예: 30일 미접속 삭제). 정리 시 해당
  `user_id`를 참조하는 행 처리 방침도 함께 정한다.
  - **갱신(2026-08-24, D-074/TP-134):** B가 소유한 네 테이블(`agent_states`/
    `recommendation_histories`/`condition_change_logs`/`trace_records`)
    쪽은 `backend/scripts/cleanup_expired_sessions.py`로 닫았다 —
    `agent_states.last_active_at` 기준 30일(조정 가능) 이상 미사용 세션을
    수동/외부 스케줄로 정리한다. `auth.users`의 익명 계정 자체를 지우는
    스케줄(Supabase Admin API 필요)은 여전히 별도 과제로 남아 있다 — FK가
    없어(D-063 결정 4) 두 정리는 서로 의존하지 않고 독립적으로 실행 가능하다.
  - **갱신(2026-08-24, D-078):** `auth.users`의 익명 계정 자체를 지우는 쪽도
    닫았다 — `backend/scripts/cleanup_anonymous_users.py`가 Supabase Auth
    Admin API로 `is_anonymous=true`이면서 `created_at` 기준 30일(조정 가능)
    이상 지난 계정을 조회·삭제한다(`--dry-run` 지원). D-074의 세션 정리
    스크립트와는 완전히 독립적으로 실행된다.

## 11. 반영 순서

| Phase | 내용 | 단독 배포 |
|---|---|---|
| 0 | Supabase 익명 로그인 활성화(2026-08-19 완료), rate limit 확인, `D-062` 기록 | — |
| 1 | 프론트: supabase-js, AuthContext, 게스트 버튼, `RequireUser`, 헤더 주입 | 가능 (백엔드가 헤더를 무시) |
| 2 | 백엔드: `app/auth/` 검증, optional principal, 관측 로그 | 가능 |
| 3 | 스키마: `user_id` 컬럼·필드 추가, 픽스처 전수 갱신 | 가능 |
| 4 | `require_principal()`로 필수화 | 가능 |
| 5 | (로그인 도입 시) `linkIdentity`, 승계 안내, 충돌 케이스 결정 | — |

각 Phase가 단독으로 머지 가능하도록 순서를 짰다. Phase 1만 올라가도 기존 동작이
깨지지 않고, Phase 2가 없으면 헤더는 그냥 무시된다.

## 12. 이번 범위 밖

- 대화 이어쓰기(세션 영속화). `session_id` TTL 30분 규칙과 `sessionStorage` 복원은
  그대로 둔다. 필요해지면 세 단계로 나눠 검토한다 — (1) 같은 기기 재방문:
  `storage.ts`의 `sessionStorage`를 `localStorage`로 바꾸고 만료 정책을 더하면 되고
  서버·DB를 건드리지 않는다, (2) 조건만 복원: `state_store_backend`를 `supabase`로
  전환하고 `user_id`로 최근 세션을 찾는다 — 대화창은 비지만 조건은 이어진다,
  (3) 기기 간 대화 이어쓰기: 대화 로그 저장소를 신설해야 하고 B 계약 5.2절의 만료 시
  재발급 규칙까지 손대야 한다. 9-4절의 위험도 순서와 같다.
  현재 대화 메시지는 서버 어디에도 저장되지 않는다 — `AgentState`는 조건·API 컨텍스트만,
  `RecommendationHistory`는 장소 목록만 담는다.
- RLS 정책 신설. 프론트가 DB에 직접 붙게 되는 시점에 다시 논의한다.
- 소셜 로그인 자체(카카오/구글 연동). Phase 5에서 다룬다.
- 게스트 → 기존 계정 로그인 시의 데이터 병합.

## 13. 미결 사항

1. 익명 사용자 정리 주기와, 정리 대상 uid를 참조하는 `agent_states` 행의 처리.
2. Phase 4 필수화 시점의 판단 기준(토큰 없는 요청 비율 임계값).
3. 게스트 진입 시 고지·동의 문구의 범위와 문안. 9-3절의 항목을 어디까지 담을지.
4. GPS를 행정동 수준으로 낮춰 저장할지 여부와, 낮출 경우 기존 좌표 소비 경로에
   미치는 영향.
5. `LLM_API_KEY`가 결제 연결된 유료 티어 키인지 확인. 무료 티어라면 발화가 모델
   학습에 쓰일 수 있어 성격이 달라진다(9-1절).
6. 개인정보처리방침에 수탁자(Google LLC)와 국외이전 사실을 명시. 코드 변경은 아니지만
   서비스 공개 전 선행 조건이다.

(JWT 검증 방식은 4-1절에서 공개키 로컬 검증으로 확정했다.)
