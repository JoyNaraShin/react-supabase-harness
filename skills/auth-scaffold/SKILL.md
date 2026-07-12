---
name: auth-scaffold
description: 인증 스캐폴드 결정적 생성 — profiles 마이그레이션(RLS·자동생성 트리거·is_admin) + src/features/auth(로그인·useAuth·ProtectedRoute) + 라우트 배선. 회원제 프로젝트의 반복 인프라를 매번 맨손으로 짓지 않게 한다.
disable-model-invocation: true
allowed-tools: Bash(date *), Bash(supabase *), Bash(pnpm *), Write, Read, Glob, Grep
argument-hint: [--roles admin,member(기본) | --oauth google 등 추가 옵션]
---

react-supabase-stack 프로젝트에 **이메일/비밀번호 인증 + 역할(profiles) 기반 인가**의 표준 골격을 생성한다. INFRA.md 인증 규약(가입 시 profiles 자동 생성 트리거 · admin 수동 부트스트랩 · **라우트 가드 ≠ 인가, RLS가 실제 인가**)과 템플릿 관행(`getSupabase()` 접근자 · feature 구조 · 외부 UI lib 금지)을 그대로 따른다.

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
-- ⚠ anon 에도 EXECUTE 필수 — 누락 시 미인증 원격 DoS(Critical). Supabase 기본값이
-- anon 에 신규 public 테이블 SELECT 를 부여하므로, 미인증 요청이 아래 select 정책의
-- (select private.is_admin()) InitPlan 을 anon 권한으로 실행 → PG 17.6 은 permission
-- denied 대신 세그폴트(엔진 버그) → postmaster 가 전 백엔드 강제종료+재시작 = 전 DB 다운.
-- anon 은 항상 admin 아님(auth.uid() null)이라 EXECUTE 부여는 누출 없음. RLS 헬퍼 표준.
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
> admin 부트스트랩은 배포 후 SQL 1회(`update profiles set role='admin' where id='<uuid>'`) — 문서에 기록. admin 은 자기 role 을 member 로 강등할 수 있어 **마지막 admin 자기강등 시 락아웃**(복구 = 위 부트스트랩 SQL 재실행) — 운영 문서에 명시. 새 RLS 테이블이므로 pgTAP 커버리지 게이트(INFRA) 대상 — 커버리지에 **anon 미인증 SELECT 가 크래시 없이 0행**(N1 회귀 방지)을 포함할 것.

## 2. feature 생성 (`src/features/auth/`)
`/feature-scaffold` 구조(api/components/hooks/types.ts/index.ts) 준수:
- **api/auth.ts** — `signIn(email, pw)` `signOut()` `getSession()` `getProfile()` (전부 `getSupabase()` 경유, zod 입력 검증).
- **hooks/useAuth.ts** — TanStack Query: `useSession()`(`onAuthStateChange` 구독 + queryClient 캐시 동기화), `useProfile()`, `useSignIn()`/`useSignOut()` mutation.
- **LoginPage** — 라우트 타겟이므로 `src/pages/auth/LoginPage.tsx` 에 생성(RULES §8: 라우트 타겟 = `src/pages/{module}/`). `components/ui` 프리미티브만 사용(Button·Card), 로딩·에러 상태 포함(한국어 메시지). auth feature 의 훅·api 를 barrel 로 소비.
- **components/ProtectedRoute.tsx** — 세션 없으면 `paths.login` redirect, `requiredRole` prop. **주석에 명시: 이 가드는 UX 편의이지 인가가 아님 — 실제 인가는 RLS.**
- **index.ts** — barrel(외부는 barrel 만 import).

## 3. 라우트 배선
- `routes/paths.ts` 에 `login` 추가 · `routes/routes.tsx` 에 `@/pages/auth/LoginPage`(lazyComponent — 템플릿 관례) + 보호 대상 라우트를 `<ProtectedRoute>` 로 감싸는 예시 1개.

## 4. 마무리
- `supabase db reset` 로컬 통과 + `pnpm gen:types` + `/check`.
- 보고: 생성 파일 목록 + admin 부트스트랩 안내 1줄 + "커밋은 `/commit`" 1줄.

## 금지
- 기존 auth 덮어쓰기 · OAuth 등 요청 안 한 확장 선제 구현 · 외부 UI lib · service_role 사용 · 라우트 가드를 인가로 서술 · 커밋(승인 게이트)
