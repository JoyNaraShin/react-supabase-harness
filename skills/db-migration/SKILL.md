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
   - **표를 새로 만들면 Data API grant 경로 확인** — 맨 앞 마이그에 `alter default privileges in schema public … on tables` 가 없으면 이 마이그에 표별 `grant select, insert, update, delete on public.<name> to authenticated;`(+ `anon`/`service_role` 필요분) 을 같이 쓴다. 2026-10-30 부터 자동 grant 가 없어 안 쓰면 그 표만 조용히 403.
   - **UPDATE 정책은 `USING`(OLD)과 `WITH CHECK`(NEW)을 나란히 쓴다.** WITH CHECK 이 더
     약하면 사용자가 행의 **부모를 바꿔 옮길 수 있다**(재부모화). 그리고 두 식 어느 쪽도
     *어떤 컬럼이 바뀌었는지*는 보지 않는다 — 컬럼 범위를 좁히려면 트리거가 필요하다.
   - **컬럼 화이트리스트 가드** — 컬럼을 열거하지 말 것(열거는 컬럼이 늘 때마다 구멍이 난다):
     ```sql
     if (to_jsonb(new) - '허용1' - '허용2' - 'updated_at' - '<앞선 트리거가 쓰는 컬럼>')
        is distinct from
        (to_jsonb(old) - '허용1' - '허용2' - 'updated_at' - '<앞선 트리거가 쓰는 컬럼>') then
       raise exception '…' using errcode = 'P0001';
     end if;
     ```
     🔴 **먼저 도는 트리거가 쓰는 컬럼(`updated_at`·검색 컬럼 등)은 반드시 뺀다.** 트리거는
     이름 **알파벳 순**으로 돈다. 안 빼면 정상 쓰기가 100% 막히는데, **단일 트랜잭션 테스트는
     `now()` 가 고정이라 초록으로 통과한다**(실측 사고 1건).
5. **검증 체크리스트 출력**:
   - [ ] 테이블 생성 시 `enable row level security` + `create policy` 포함?
   - [ ] destructive(DROP·RENAME·ALTER COLUMN TYPE)면 별도 파일로 분리?
   - [ ] 상단 `-- rollback:` 주석 작성?
   - [ ] 표 생성 시 Data API grant 경로 확보(앞선 default privileges 또는 표별 grant)?
   - [ ] 새 함수에 `revoke execute … from public`(기본이 PUBLIC 부여)?
   - [ ] UPDATE 정책의 `WITH CHECK` 이 `USING` 만큼 강한가? 부모 id 컬럼이 있으면 재부모화 점검?
   - [ ] 가드 추가 시 **정상 경로 단언을 교차 트랜잭션으로** 썼나?
         (`set local session_replication_role = replica` 로 `updated_at` 을 과거로 민 뒤 UPDATE)
6. **후속 안내**:
   ```
   supabase db reset                                                  # 로컬 재적용
   supabase gen types typescript --local > src/lib/database.types.ts  # 타입 재생성
   ```

## 참고
- 한 마이그레이션 = 한 논리적 변경(모듈 다르면 분리).
- 검증 gate = `supabase db reset` 로컬 통과 + 타입 재생성.
