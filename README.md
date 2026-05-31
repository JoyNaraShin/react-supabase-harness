# react-supabase-harness

1인 풀스택 개발을 위한 **Claude Code 플러그인 하네스**. 이슈 플로우 스킬, 리뷰 서브에이전트, 안전·자동화 훅을 한 번에 설치해 모든 프로젝트를 동일한 고급 워크플로우로 운영한다. 스택 기준: **React + Vite + Supabase**.


## 구성

| 컴포넌트 | 상태 |
|---|---|
| `hooks/` — 안전·자동화 훅 4종 | ✅ |
| `skills/` — issue / branch / commit / pr / phase / check / verify / review-* / db-migration / feature-scaffold | 🔜 |
| `agents/` — architect-reviewer / executor / security-reviewer (+ planner / verifier) | 🔜 |
| `docs/` — RULES / PLANNING / WORKFLOW (이슈 플로우 규칙) | 🔜 |

## 훅 (현재)

| 훅 | 이벤트 / matcher | 역할 |
|---|---|---|
| `block-destructive-git` | PreToolUse / Bash | `git commit`·`push --force`·`reset --hard`·`rm -rf` 등 차단 (`/commit` 승인 우회만 허용) |
| `format-on-edit` | PostToolUse / Edit·Write | 편집 직후 biome 자동 포맷 (src/ 대상) |
| `edit-counter` | PostToolUse / Edit·Write | src/ 편집 5회 누적 시 `/check` 권고 |
| `session-start-summary` | SessionStart | 브랜치·Phase·미커밋 3줄 요약 주입 |

## 로컬 개발 / dogfood

```bash
# 다른 프로젝트에서 이 플러그인을 로드해 테스트
claude --plugin-dir /path/to/react-supabase-harness

# 변경 후 재로드
/reload-plugins
```

배포 시 별도 마켓플레이스 레포(`.claude-plugin/marketplace.json`)에서 `/plugin install react-supabase-harness@<marketplace>` 로 설치.

## 라이선스
MIT
