---
name: feature-scaffold
description: 새 feature 모듈 스캐폴드 — src/features/<name>/{api,components,hooks,types.ts,index.ts} + src/pages/<name>/ 진입 페이지. 라우트는 src/routes/routes.tsx 등록 안내. 새 도메인 모듈 추가 시 사용.
disable-model-invocation: true
allowed-tools: Bash(mkdir *)
argument-hint: "<module-name> (kebab-case, 예: posts 또는 admin-metrics)"
---

`docs/RULES.md`(FE 컨벤션) 규약대로 feature 모듈을 표준 구조로 생성한다.

**핵심 규약**: 페이지는 `src/pages/{module}/`, feature 내부 로직(api/components/hooks/types)은 `src/features/{module}/`. **features 안에 pages 폴더 두지 않는다.** `lib`=인프라 전용.

## Workflow
1. **입력 검증**: `$ARGUMENTS` kebab-case 확인(공백·대문자·underscore 시 에러).
2. **중복 체크**: `src/features/<name>/` 또는 `src/pages/<name>/` 존재 시 경고 후 종료.
3. **구조 생성**:
   ```
   src/features/<name>/
   ├── api/
   │   ├── queryKeys.ts     # { all, lists, list(filter), detail(id) } 계층
   │   └── queries.ts       # TanStack Query 훅 (list/detail/mutation)
   ├── components/
   ├── hooks/
   ├── types.ts
   └── index.ts             # barrel — re-export 만 (.ts)
   src/pages/<name>/        # 페이지 진입점(라우트 타겟)
   ```
4. **파일 내용** (PascalCase = `<name>` 변환, 예: `admin-metrics`→`AdminMetrics`):
   - `queryKeys.ts`: query key factory(`export const <name>Keys = { all:[...] as const, lists:()=>..., list:(f)=>..., detail:(id)=>... }`)
   - `queries.ts`: `useXList`/`useXDetail`(useQuery) + mutation 훅(invalidate)
   - `types.ts`: `database.types.ts` 의 Row 타입 재노출 또는 도메인 타입
   - `index.ts`: 공개 API barrel
5. **페이지/라우트 안내**: 페이지 컴포넌트는 사용자/executor 가 작성. `src/routes/routes.tsx` 에 lazy 라우트 + `src/routes/paths.ts` 에 경로 상수 추가하도록 안내.
6. **자가 체크**: Naming(PascalCase 컴포넌트·`use*` 훅·`types.ts`) · import 계층(external → @/lib → @/components/ui → @/features/{self} → @/features/{other} barrel) 준수 메시지.

## 참고
- cross-feature import 는 `@/features/{module}` barrel 강제.
- 1 파일 = 1 컴포넌트 = 1 책임.
