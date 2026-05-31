# INFRA — Supabase / 마이그레이션 / 인증 (정본)

`db-migration` 스킬과 `security-reviewer`가 참조. 스택: Supabase(Postgres + Auth + Storage + RLS).

## Migration Safety
- **한 마이그레이션 = 한 논리적 변경.** 모듈 다르면 분리.
- 파일: `supabase/migrations/<YYYYMMDD>_<slug>.sql` (snake_case).
- 상단 `-- rollback:` 주석 — 생성한 객체 역순 drop.
- 테이블 생성 시 **`enable row level security` + `create policy` 필수.**
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
  revoke execute on function private.is_admin() from public, anon;
  grant execute on function private.is_admin() to authenticated;
  ```
- enum 타입(role/type 등) 권장 — 타입 생성 시 union 리터럴.
- `updated_at` 자동 트리거(`before update`).
- RLS/조회 컬럼 인덱스.

## 인증 / 시크릿
- Supabase Auth(이메일/비밀번호·OAuth). 가입 시 `profiles` 자동 생성 트리거. admin = `profiles.role = 'admin'`(수동 부트스트랩).
- **client는 anon key 전용.** `SERVICE_ROLE_KEY`는 client 절대 금지. 민감 key에 `VITE_` 접두 금지.
- 라우트 가드는 인가가 **아님** — API/RLS 필터가 실제 인가. 가드는 UX 편의.

## Storage
- 버킷 정책을 `storage.objects`에 명시(public read / admin write 등). 공개 버킷에 PII 금지.
- 업로드 유틸은 `src/lib/storage.ts` 독점. 파일명 uuid(경로 traversal 방지).
