# INFRA — Supabase / 마이그레이션 / 인증 (정본)

`db-migration` 스킬과 `stability-reviewer`가 참조. 스택: Supabase(Postgres + Auth + Storage + RLS).

## Edge Function 호출 — 원인을 삼키지 않는다

- `supabase.functions.invoke` 의 `FunctionsHttpError.message` 는 상태와 무관하게 **고정 문자열**이다(`"Edge Function returned a non-2xx status code"`). 함수가 보낸 본문은 **`error.context`(Response)** 안에 있고, throw 시점에 미소비라 `context.clone().json()` 으로 읽을 수 있다. **안 읽으면 사라진다.**
- 읽기가 실패해도 **던지지 않는다** — 원인 조회가 원래 에러를 덮으면 더 나쁘다.
- 응답 자체가 없으면(네트워크 단절) 원래 에러를 그대로 올려 기존 에러 변환기가 처리하게 둔다. 여기서 문장을 새로 만들면 같은 뜻의 문구가 두 곳에 생긴다.
- 문장 규칙 둘: **사람이 할 수 있는 행동을 앞에**(로그인 풀림 ≠ 앱 설정 오류), **서버 원문을 괄호로 끝에.** 기술 용어 노출이 UX 원칙과 부딪치지만 원문 한 줄이 다음 사고에서 하루를 아낀다.
- `retryable` 같은 플래그는 **소비처가 없으면 만들지 않는다** — 거짓 신호가 된다. 행동은 문장이 말한다.
- **부분 실패를 개수로 뭉개지 않는다.** "N개 실패했어요. 다시 시도해주세요" 는 설정 문제일 때 절대 성공할 수 없는 재시도를 권한다. 동시 업로드는 대개 같은 이유로 함께 실패하므로 첫 원인을 붙인다.
- 실측(실프로젝트 #710): 함수는 `Prefix 'drawings' not allowed` 라고 정확히 말했는데 화면엔 고정 문구가 떴고, 코드가 전부 머지된 채로 **하루가 멈췄다**.

## Backfill (소급 데이터 보정)

- **트리거와 같은 규칙을 그대로 소급하면 틀린다.** insert 시점엔 살아 있는 후보가 전부 과거지만, 소급에선 **그 행 이후에 생긴 것**이 후보에 섞인다. 후보를 `ref.created_at <= target.created_at` 으로 제한한다 — 이 조건은 소급에만 필요하고 트리거엔 불필요하다. "지금 하나뿐" 이 "그때도 하나였다" 는 아니다.
- **일회용 UPDATE 로 적지 않는다.** 한 번 돌고 사라지는 DML 은 검증할 수 없다. `private.backfill_*()` 함수로 두고 마이그레이션이 한 번 호출한다 — pgTAP 이 픽스처를 만들어 **같은 코드**를 돌린다. 채운 행 수를 반환하게 해 "비어 있는 걸 전부 채우지 않았다" 를 단언한다.
- dev 에 대상 행이 0개면 **실행 결과로는 아무것도 검증되지 않는다.** 함수+pgTAP 가 아니면 미검증 로직이 그대로 운영에 나간다.
- 트리거가 이미 달린 뒤라면 픽스처는 `insert` 후 `update ... set col = null` 로 "트리거 이전 데이터" 를 만든다.

## Migration Safety
- **한 마이그레이션 = 한 논리적 변경.** 모듈 다르면 분리.
- 파일: `supabase/migrations/<YYYYMMDD>_<slug>.sql` (snake_case).
- 상단 `-- rollback:` 주석 — 생성한 객체 역순 drop.
- 테이블 생성 시 **`enable row level security` + `create policy` 필수.**
- 🔴 **표를 만들면 Data API grant 경로가 있어야 한다.** Supabase 는 **2026-10-30** 부터 `public` 신규 표에 Data API(PostgREST/supabase-js) grant 를 자동 부여하지 않는다 — 마이그·신규 프로젝트·preview 브랜치·`db reset` 전부 대상. 정본 경로는 **맨 앞 마이그의 `alter default privileges`** 하나다:
  ```sql
  alter default privileges in schema public
    grant all on tables to postgres, anon, authenticated, service_role;   -- 함수·시퀀스도 동일 3줄
  grant usage on schema public to anon, authenticated, service_role;      -- 없으면 표 grant 가 있어도 런타임 전멸
  ```
  - ⚠ `alter default privileges` 는 **실행 롤을 grantor 로 기록**하고 그 롤이 만든 객체에만 적용된다(Supabase CLI = `postgres`). `grant … on all tables in schema public` 은 **그 시점 스냅샷**이라 이후 표를 못 덮는다 — 대체재가 아니다.
  - 이 실패는 **조용하다**: 기존 표는 grant 를 유지하므로 화면 대부분이 멀쩡하고 새 표를 쓰는 화면만 403 이라, 원인을 코드에서 찾게 된다. 템플릿 `20260101000000_data_api_grants.sql` + pgTAP `01_data_api_reachability.sql` + lint 룰 3 이 3중으로 잠근다.
- destructive(DROP·RENAME·ALTER COLUMN TYPE)는 **별도 파일**로 분리.
- 검증 게이트: `supabase db reset` 로컬 통과 + `pnpm gen:types`(→ `src/lib/database.types.ts`).

## RLS 패턴 (Supabase 베스트프랙티스)
- 정책의 함수/`auth.uid()`는 **`(select ...)` 로 래핑** — 행별 재평가 방지.
  ```sql
  using ( is_published or (select private.is_admin()) )
  ```
- admin 판별 헬퍼는 **private 스키마 + security definer + `set search_path = ''`**, public/anon execute revoke:
  ```sql
  create function private.is_admin() returns boolean
    language sql stable security definer set search_path = '' as $$
    select exists (select 1 from public.profiles
                   where id = (select auth.uid()) and role = 'admin'); $$;
  revoke execute on function private.is_admin() from public;  -- from public 이 정본(#266) — anon 별도 revoke 는 중복
  grant execute on function private.is_admin() to authenticated;
  ```
- enum 타입(role/type 등) 권장 — 타입 생성 시 union 리터럴.
- `updated_at` 자동 트리거(`before update`).
- RLS/조회 컬럼 인덱스.

## DB 테스트 커버리지 (CI 강제 — 재발 차단)
> BE는 사용자가 약한 영역 → 하네스가 백스톱. "문서 교훈"은 재발을 못 막는다(같은 `EXECUTE PUBLIC` 결함이 메모리에 적은 뒤에도 2회 출하된 실측). **교훈은 CI 단언으로.**
- **새 SECURITY DEFINER RPC(money/stock/integrity invariant) + 새 RLS 테이블 → pgTAP 동작 테스트 필수.** 템플릿의 `scripts/lint-db-test-coverage.mjs`가 마이그를 스캔해 무테스트면 CI fail(read-only RPC·의도-무관 테이블은 명시 ALLOW_*). pgTAP는 "누군가 적은 단언"만 검사하므로 커버리지 자체를 게이트한다.
- **함수 EXECUTE 권한 변경 검증은 `has_function_privilege('anon', 'schema.fn(args)', 'EXECUTE')`(실효 권한)로.** `aclexplode`/ACL introspection 은 **PUBLIC 기본 grant 를 놓쳐 false-green**(#266 실패모드 — `revoke … from anon, authenticated` 는 PUBLIC 잔존, `revoke … from public` 이어야). revoke 후 `get_advisors` 재실행을 redundancy 로.
- **권한 단언은 "회수됐나"(음성)만 쓰면 false-green 이 난다.** 기제(default ACL·grant)가 사라져도 **기존 표는 grant 를 유지**해 전 단언이 통과하고, 그 뒤 추가되는 표만 조용히 403 이 된다. 결과(도달성)와 기제(`pg_default_acl` 생존) **둘 다** 잠가라.
- **pgTAP 픽스처에서 doc_no(quote_no/order_no 등)는 명시값 전달** — `assign_*_no` 트리거는 null/'' 일 때만 채번 → `supabase db reset`(seed 로드) 환경에서 빈값 생성번호가 seed 와 충돌(`*_no_key`). dev 증분에선 안 터지고 CI 에서만 터짐.

## 인증 / 시크릿
- Supabase Auth(이메일/비밀번호·OAuth). 가입 시 `profiles` 자동 생성 트리거. admin = `profiles.role = 'admin'`(수동 부트스트랩).
- **client는 anon key 전용.** `SERVICE_ROLE_KEY`는 client 절대 금지. 민감 key에 `VITE_` 접두 금지.
- 라우트 가드는 인가가 **아님** — API/RLS 필터가 실제 인가. 가드는 UX 편의.
- 🔴 **비밀이 아닌 설정을 secret 에 넣지 않는다.** 경로 prefix·허용목록·도메인 목록처럼 **코드에 리터럴로 박혀 있는 값**을 키·토큰과 같은 칸에 두면 코드와 인프라가 따로 움직인다 — 새 지점을 코드로 추가해도 대시보드를 안 고치면 조용히 4xx 가 나고 화면엔 원인이 안 나온다(실측: 기능이 전부 머지된 채 업로드가 하루 400). 넣기 전에 **"유출되면 손해인가"** 를 묻고, 아니면 코드로. 이미 secret 에 있으면 `const CODE_X = [...]` 를 코드에 두고 env 는 **union 으로 더하기만** 가능하게 한다 — `env ?? DEFAULT` 는 env 가 여전히 **좁힐** 수 있어 같은 사고가 재발한다. 드리프트는 lint 로 잡는다(src 의 리터럴을 모아 선언 목록과 대조 = 결정론적).

## Storage
- 버킷 정책을 `storage.objects`에 명시(public read / admin write 등). 공개 버킷에 PII 금지.
- 업로드 유틸은 `src/lib/storage.ts` 독점. 파일명 uuid(경로 traversal 방지).
