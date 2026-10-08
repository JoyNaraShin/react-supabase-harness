---
name: auth-scaffold
description: 인증 스캐폴드를 결정적으로 생성한다 — profiles 마이그레이션(RLS·자동생성 트리거·is_admin)과 pgTAP 테스트, src/features/auth(로그인·useAuth·ProtectedRoute), 라우트 배선. 회원제 프로젝트에서 인증을 처음 붙일 때 쓴다.
disable-model-invocation: true
allowed-tools: Bash(date *), Bash(supabase *), Bash(pnpm *), Write, Read, Glob, Grep
argument-hint: '[--roles admin,member(기본) | --oauth google 등 추가 옵션]'
---

하네스 스택 구조의 프로젝트에 **이메일/비밀번호 인증 + 역할(profiles) 기반 인가**의 표준 골격을 생성한다. INFRA.md 인증 규약(가입 시 profiles 자동 생성 트리거 · admin 수동 부트스트랩 · **라우트 가드 ≠ 인가, RLS가 실제 인가**)과 스택 관행(`getSupabase()` 접근자 · feature 구조 · 외부 UI lib 금지)을 그대로 따른다.

## 목차

- 0. 선행 확인
- 1. 마이그레이션 생성 — profiles·RLS·트리거·is_admin
- 1b. pgTAP 동작 테스트 생성
- 2. feature 생성 (`src/features/auth/`)
- 3. 라우트 배선
- 4. 마무리
- 금지

## 0. 선행 확인
- `src/features/auth/` 또는 `profiles` 마이그레이션이 이미 있으면 **중단하고 되묻기**(덮어쓰기 금지).
- `src/lib/supabase.ts` 의 `getSupabase()` 접근자 존재 확인(템플릿 표준).

## 1. 마이그레이션 생성 (`/db-migration` 규약: RLS enable + rollback 주석)
`supabase/migrations/<ts>_auth_profiles.sql`:
```sql
-- private 스키마: RLS 헬퍼·트리거 전용 (자기완결 — 템플릿엔 마이그 0개 전제)
create schema if not exists private;
-- schema USAGE: 정책 InitPlan 경로엔 불필요(정책은 소유자 권한으로 함수명 해석)하나,
-- 향후 private 함수를 직접 호출하는 코드 경로 대비 grant. 무해.
grant usage on schema private to authenticated;

-- profiles: auth.users 1:1, 역할 기반 인가의 SoT
create table public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  role text not null default 'member' check (role in ('admin','member')),
  display_name text,
  created_at timestamptz not null default now()
);
alter table public.profiles enable row level security;

-- 가입 시 자동 생성 (INFRA 규약)
create or replace function private.handle_new_user()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
  insert into public.profiles (id) values (new.id);
  return new;
end $$;
create trigger on_auth_user_created after insert on auth.users
  for each row execute function private.handle_new_user();

-- is_admin: RLS 정책 공용 (성능 위해 (select ...) 래핑해 사용)
create or replace function private.is_admin()
returns boolean language sql security definer set search_path = '' stable as $$
  select exists (select 1 from public.profiles where id = (select auth.uid()) and role = 'admin')
$$;
revoke execute on function private.is_admin() from public;
-- ⚠ anon 에도 EXECUTE 필수. Supabase 기본값이 anon 에 신규 public 테이블 SELECT 를
-- 부여하므로, 미인증 요청이 아래 select 정책의 (select private.is_admin()) InitPlan 을
-- anon 권한으로 평가한다. anon 이 EXECUTE 없으면 그 쿼리가 permission denied 로 실패한다
-- (로컬 실측[PG 17.x]에선 백엔드 비정상 종료까지 관측됐으나 엔진 원인은 미확정 — 이 주석은
-- 그 특정 크래시 메커니즘을 사실로 단정하지 않는다). 어느 쪽이든 방어는 동일:
-- (1) grant 로 anon 이 실행 가능하면 항상 false 반환(anon 은 auth.uid() null)이라 누출 없이
-- 정상 처리되고, (2) 아래 revoke 로 anon 의 테이블 도달을 원천 차단한다. 이중 방어.
-- RLS 헬퍼를 API 롤에 grant 하는 것은 Supabase 표준 패턴.
grant execute on function private.is_admin() to authenticated, anon;
-- 방어심화: anon 은 profiles(PII) 에 접근할 이유 없음 — 테이블 도달 자체를 차단.
revoke all on public.profiles from anon;

-- 정책: 본인 row 읽기 + admin 전체 읽기 / role 변경은 admin만
create policy "profiles_select_own" on public.profiles for select
  using (id = (select auth.uid()) or (select private.is_admin()));
create policy "profiles_update_own_no_role" on public.profiles for update
  using (id = (select auth.uid())) with check (id = (select auth.uid()) and role = 'member');
-- ⚠ with check(true) 금지: permissive 정책의 WITH CHECK 는 USING 매칭과 무관하게
-- OR 로 결합된다 → member 의 `set role='admin'` 이 이 true 로 통과(권한상승).
create policy "profiles_admin_update" on public.profiles for update
  using ((select private.is_admin())) with check ((select private.is_admin()));

-- rollback:
-- drop trigger on_auth_user_created on auth.users; drop function private.handle_new_user();
-- drop function private.is_admin(); drop table public.profiles;
-- (private 스키마는 다른 마이그가 공유할 수 있으면 유지, 이 마이그가 유일 사용자면 drop schema private;)
```
> admin 부트스트랩은 배포 후 SQL 1회(`update profiles set role='admin' where id='<uuid>'`) — 문서에 기록. admin 은 자기 role 을 member 로 강등할 수 있어 **마지막 admin 자기강등 시 락아웃**(복구 = 위 부트스트랩 SQL 재실행) — 운영 문서에 명시. 새 RLS 테이블이므로 pgTAP 동작 테스트 대상(INFRA §DB 테스트 커버리지) — 아래 1b 를 같이 만든다.

## 1b. pgTAP 동작 테스트 생성
`supabase/tests/auth_profiles_test.sql` — 위 정책이 **실제로** 막는지를 단언한다. 정책 문장만 보고 안전하다고 판단하지 않는다(`with check (true)` 권한상승은 문장으로는 멀쩡해 보였다). 이 파일은 그 취약 정책에서 5번이 실패하는 것을 확인했다.
```sql
begin;
create extension if not exists pgtap with schema extensions;
select plan(8);

-- 픽스처: 가입 트리거가 profiles 를 만든다(직접 insert 하지 않는다 — 트리거 자체가 검증 대상)
insert into auth.users (id, email) values
  ('00000000-0000-0000-0000-00000000000a', 'admin@test.local'),
  ('00000000-0000-0000-0000-00000000000b', 'member@test.local'),
  ('00000000-0000-0000-0000-00000000000c', 'other@test.local');

select results_eq(
  $$ select count(*)::int from public.profiles where role = 'member' $$, array[3],
  '가입 트리거가 profiles 를 member 로 만든다');

update public.profiles set role = 'admin' where id = '00000000-0000-0000-0000-00000000000a';  -- 부트스트랩(소유자 권한)

-- anon: 테이블 도달 자체가 거부된다(revoke) — 백엔드 종료가 아니라 42501
set local role anon;
select throws_ok($$ select * from public.profiles $$, '42501', null, 'anon 은 profiles 에 도달하지 못한다');
reset role;

-- member: 본인 행만, role 상승 불가
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-00000000000b","role":"authenticated"}', true),
       set_config('request.jwt.claim.sub', '00000000-0000-0000-0000-00000000000b', true);  -- 구버전 auth.uid() 호환
select results_eq($$ select count(*)::int from public.profiles $$, array[1], 'member 는 본인 행만 본다');
select is((select private.is_admin()), false, 'member 의 is_admin() = false');
select throws_ok(
  $$ update public.profiles set role = 'admin' where id = '00000000-0000-0000-0000-00000000000b' $$,
  '42501', null, 'member 는 자기 role 을 admin 으로 올리지 못한다(WITH CHECK)');
select lives_ok(
  $$ update public.profiles set display_name = 'B' where id = '00000000-0000-0000-0000-00000000000b' $$,
  'member 는 role 외 본인 필드를 수정할 수 있다');

-- admin: 전체 조회 + 타인 role 변경
select set_config('request.jwt.claims', '{"sub":"00000000-0000-0000-0000-00000000000a","role":"authenticated"}', true),
       set_config('request.jwt.claim.sub', '00000000-0000-0000-0000-00000000000a', true);  -- 구버전 auth.uid() 호환
select results_eq($$ select count(*)::int from public.profiles $$, array[3], 'admin 은 전체를 본다');
update public.profiles set role = 'admin' where id = '00000000-0000-0000-0000-00000000000c';
reset role;
select results_eq(
  $$ select role from public.profiles where id = '00000000-0000-0000-0000-00000000000c' $$, array['admin'],
  'admin 은 타인의 role 을 바꿀 수 있다');

select * from finish();
rollback;
```
- anon 은 `revoke all` 로 **도달 자체가 거부**(42501)되는 것이 정상이다 — 0행이 아니다.
- `request.jwt.claim.sub` 를 함께 세팅하는 것은 구버전 `auth.uid()`(단일 claim 만 읽음) 호환용이다.

## 2. feature 생성 (`src/features/auth/`)
`/feature-scaffold` 구조(api/components/hooks/types.ts/index.ts) 준수:
- **api/auth.ts** — `signIn(email, pw)` `signOut()` `getSession()` `getProfile()` (전부 `getSupabase()` 경유, zod 입력 검증).
- **hooks/useAuth.ts** — TanStack Query: `useSession()`(`onAuthStateChange` 구독 + queryClient 캐시 동기화), `useProfile()`, `useSignIn()`/`useSignOut()` mutation.
- **LoginPage** — 라우트 타겟이므로 `src/pages/auth/LoginPage.tsx` 에 생성(RULES §8: 라우트 타겟 = `src/pages/{module}/`). `components/ui` 프리미티브만 사용(Button·Card), 로딩·에러 상태 포함(사용자 언어 메시지). auth feature 의 훅·api 를 barrel 로 소비.
- **components/ProtectedRoute.tsx** — 세션 없으면 `paths.login` redirect, `requiredRole` prop. **주석에 명시: 이 가드는 UX 편의이지 인가가 아님 — 실제 인가는 RLS.**
- **index.ts** — barrel(외부는 barrel 만 import).

## 3. 라우트 배선
- `routes/paths.ts` 에 `login` 추가 · `routes/routes.tsx` 에 `@/pages/auth/LoginPage`(lazyComponent — 템플릿 관례) + 보호 대상 라우트를 `<ProtectedRoute>` 로 감싸는 예시 1개.

## 4. 마무리
- `supabase db reset` + `supabase test db`(1b 8건 PASS) 로컬 통과 + `pnpm gen:types` + `/check`.
- 보고: 생성 파일 목록 + admin 부트스트랩 안내 1줄 + "커밋은 `/commit`" 1줄.

## 금지
- 기존 auth 덮어쓰기 · OAuth 등 요청 안 한 확장 선제 구현 · 외부 UI lib · service_role 사용 · 라우트 가드를 인가로 서술 · 커밋(승인 게이트)
