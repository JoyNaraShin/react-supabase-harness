#!/usr/bin/env python3
"""PostToolUse hook — RULES §8 스택 규칙 미준수를 코드 작성 중에 적출한다.

배경(실측 사고, 같은 원인 2회째): `workflow-entry-guard` 는 **플랜 유무**로만
미탑승을 판정한다. 한 실프로젝트는 planner 가 쓴 플랜이 있었으므로 그 훅이 통과시켰고,
그 사이 메인 세션은 `docs/RULES.md` 를 한 번도 읽지 않은 채 화면을 구현했다 —
Tailwind 대신 순수 CSS, `@/*` alias 없음, TanStack Query 없음. 사용자가 육안으로 적발.
플랜이 있어도 스택 규칙은 안 읽힌다. 그래서 "플랜 있음"이 아니라 **스택 신호 자체**를 본다.

검사 대상은 기계로 확인 가능한 §8 불변식만: Tailwind · `@/*` alias · TanStack Query.
두 층으로 본다:
  ① 선언 — deps·tsconfig 에 셋이 있는가(스택 자체를 안 깐 프로젝트)
  ② 사용 — 지금 쓰는 코드가 깔린 스택을 우회하는가(템플릿으로 시작해 선언은 다 갖춘
     프로젝트에서 ①은 원리적으로 침묵한다 — 그 프로젝트의 실패는 "있는데 안 쓰는" 쪽이다)
차단이 아니라 주입이다 — 프로젝트 사정으로 예외를 둘 수 있고, 그 판단은 사용자 몫이다.
신호 종류마다 프로젝트당 1회만 알린다.
"""
import json
import os
import re
import sys
from pathlib import Path

# 훅 출력은 치환되지 않는다 — `${CLAUDE_PLUGIN_ROOT}` 를 그대로 내면 모델이 경로를 풀 수 없다.
# 훅 프로세스에는 같은 이름의 환경변수가 export 된다(plugins-reference#where-each-variable-resolves).
PLUGIN_ROOT = os.environ.get("CLAUDE_PLUGIN_ROOT") or str(Path(__file__).resolve().parent.parent)

CODE_EXT = (".ts", ".tsx", ".jsx")

# ② 사용 신호 — 모두 문자열 패턴(결정론). 오탐 여지가 있어 차단하지 않는다.
DEEP_RELATIVE = re.compile(r"""from\s+['"](?:\.\./){2,}""")
CSS_IMPORT = re.compile(r"""import\s+(?:[\w{}\s,*]+\s+from\s+)?['"]\.{1,2}/[^'"]+\.css['"]""")
EFFECT = re.compile(r"\buseEffect\s*\(")
FETCH = re.compile(r"\bfetch\s*\(|\.from\s*\(\s*['\"]\w+['\"]\s*\)\s*\.\s*(?:select|insert|update|upsert|delete)\b")


def project_dir() -> Path:
    return Path(os.environ.get("CLAUDE_PROJECT_DIR") or ".")


def state_file() -> Path:
    return project_dir() / ".claude" / "state" / "stack-compliance.json"


def package_jsons(root: Path) -> list[dict]:
    # 모노레포도 있으므로 루트 + 1단계 워크스페이스까지 본다(node_modules 는 제외).
    candidates = [root / "package.json"]
    for group in ("apps", "packages"):
        base = root / group
        if base.is_dir():
            candidates += [d / "package.json" for d in base.iterdir() if d.is_dir()]
    out = []
    for p in candidates:
        try:
            out.append(json.loads(p.read_text()))
        except Exception:
            continue
    return out


def all_deps(pkgs: list[dict]) -> set[str]:
    names: set[str] = set()
    for pkg in pkgs:
        for field in ("dependencies", "devDependencies"):
            names |= set((pkg.get(field) or {}).keys())
    return names


def has_alias(root: Path) -> bool:
    for path in root.rglob("tsconfig*.json"):
        if "node_modules" in path.parts:
            continue
        try:
            text = path.read_text()
        except Exception:
            continue
        if '"@/*"' in text:
            return True
    return False


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    if data.get("tool_name") not in ("Write", "Edit"):
        sys.exit(0)

    ti = data.get("tool_input") or {}
    path = ti.get("file_path") or ""
    is_src_code = ("/src/" in path or path.startswith("src/")) and path.endswith(CODE_EXT)
    if not is_src_code:
        sys.exit(0)
    try:
        state = json.loads(state_file().read_text())
    except Exception:
        state = {}
    seen = set(state.get("signals") or [])
    if state.get("notified"):  # 구버전 상태 파일 = 선언 신호를 이미 알린 것
        seen.add("declared")
    body = ti.get("content") or ti.get("new_string") or ""

    root = project_dir()
    pkgs = package_jsons(root)
    deps = all_deps(pkgs)

    # React 앱이 아니면 §8(FE 컨벤션)의 적용 대상이 아니다 — 엔진·CLI 전용 repo 오탐 방지.
    if "react" not in deps:
        sys.exit(0)

    alias = has_alias(root)
    missing = []
    new = []
    if "declared" not in seen:
        if "tailwindcss" not in deps:
            missing.append("Tailwind v4(§8: 외부 UI 라이브러리 금지 · Tailwind + 자체 primitives만)")
        if "@tanstack/react-query" not in deps:
            missing.append("TanStack Query(§스택 — 서버 상태를 수기 useEffect+fetch 로 짜지 않는다)")
        if not alias:
            missing.append("`@/*` → `./src/*` alias(§8)")
        if missing:
            new.append("declared")

    bypass = []
    if alias and "deep-relative" not in seen and DEEP_RELATIVE.search(body):
        bypass.append("`../../` 상대경로 import — `@/*` alias 가 설정돼 있다(§8 import 계층)")
        new.append("deep-relative")
    if "tailwindcss" in deps and "css-import" not in seen and path.endswith((".tsx", ".jsx")) \
            and CSS_IMPORT.search(body):
        bypass.append("컴포넌트에서 CSS 파일 import — Tailwind 유틸리티·`@theme` 토큰으로 쓴다(§8)")
        new.append("css-import")
    if "@tanstack/react-query" in deps and "effect-fetch" not in seen \
            and EFFECT.search(body) and FETCH.search(body):
        bypass.append("`useEffect` 안의 수기 fetch/쿼리 — 서버 상태는 TanStack Query 훅으로(§스택)")
        new.append("effect-fetch")

    if not new:
        sys.exit(0)

    parts = []
    if missing:
        parts.append("누락: " + " / ".join(missing) + ".")
    if bypass:
        parts.append(f"`{Path(path).name}` 에서 깔린 스택을 우회: " + " / ".join(bypass) + ".")
    msg = (
        f"⚠️  RULES §8 스택 규칙 미준수 신호 — 지금 `{PLUGIN_ROOT}/docs/RULES.md` §8 을 Read 하라. "
        + " ".join(parts) + " "
        "프로젝트 사정으로 예외가 필요하면 **사용자에게 물어 결정하고 RULES 에 예외를 기록**한다 — "
        "말없이 다른 스택으로 진행하지 말 것(같은 원인으로 재발해 화면을 다시 쓴 실측이 있다). "
        "디렉터리 트리는 강제 아님(목적별 분리면 충족). (신호 종류마다 프로젝트당 1회.)"
    )
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": msg,
    }}, ensure_ascii=False))
    p = state_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"signals": sorted(seen | set(new))}, ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    main()
