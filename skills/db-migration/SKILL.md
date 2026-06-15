---
name: db-migration
description: Supabase 마이그레이션 생성 (supabase/migrations/YYYYMMDD_<slug>.sql) — rollback 주석 + RLS enable + 정책 스켈레톤. 이후 타입 재생성 안내. 수동 호출 전용 슬래시 명령(/db-migration <slug>).
disable-model-invocation: true
allowed-tools: Bash(date *), Write, Glob
argument-hint: "<slug> (snake_case, 예: add_posts_table)"
---

`docs/INFRA.md`(Migration Safety) 규약에 맞춰 마이그레이션을 생성한다.

## Workflow
1. **입력 검증**: `$ARGUMENTS` 가 snake_case 인지 확인(kebab·space·대문자 시 에러).
2. **날짜 스탬프**: `date +%Y%m%d` → 파일명 prefix.
3. **중복 체크**: `supabase/migrations/<YYYYMMDD>_<slug>.sql` 존재 시 경고 후 종료.
4. **파일 생성** — 아래 헤더 컨벤션으로 작성:
   ```sql
   -- Phase: <plan 링크 / 무엇을 왜>
   -- 결정: <접근 근거 한 줄>
   -- rollback:
   --   drop policy ... ; drop table ... ;   (생성한 객체 역순)

   create table public.<name> (
     id uuid primary key default gen_random_uuid(),
     created_at timestamptz not null default now()
     -- ...
   );
   alter table public.<name> enable row level security;       -- 테이블 생성 시 필수

   create policy "<name> public read" on public.<name>
     for select using (true);                                  -- 또는 조건
   create policy "<name> admin write" on public.<name>
     for all to authenticated
     using ((select private.is_admin())) with check ((select private.is_admin()));
   ```
   - RLS 정책의 함수/`auth.uid()` 는 **`(select ...)` 로 래핑**(행별 재평가 방지).
   - admin 판별은 `private.is_admin()`(security definer, search_path='') 패턴.
5. **검증 체크리스트 출력**:
   - [ ] 테이블 생성 시 `enable row level security` + `create policy` 포함?
   - [ ] destructive(DROP·RENAME·ALTER COLUMN TYPE)면 별도 파일로 분리?
   - [ ] 상단 `-- rollback:` 주석 작성?
6. **후속 안내**:
   ```
   supabase db reset                                                  # 로컬 재적용
   supabase gen types typescript --local > src/lib/database.types.ts  # 타입 재생성
   ```

## 참고
- 한 마이그레이션 = 한 논리적 변경(모듈 다르면 분리).
- 검증 gate = `supabase db reset` 로컬 통과 + 타입 재생성.
