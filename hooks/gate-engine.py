#!/usr/bin/env python3
"""게이트 엔진 — 훅 1개가 데이터로 정의된 룰 N개를 집행한다.

왜 엔진인가 (2026-08-17):
    룰 하나당 훅 하나로 만들면 룰 수만큼 훅이 늘고, 훅마다 발동 범위·탈출구·오탐 처리를
    다시 짜게 된다. 업계 정답은 ESLint 모델이다 — **룰 로직은 재사용 술어로 코드에,
    무엇을 어떤 조건으로 켤지는 데이터로.** OMC 도 `omc.jsonc` 로 같은 분리를 한다.
    (근거: 2026-08-17 하네스 리서치 — OMC 는 에이전트 32·스킬 40+ 인데 훅은 6개다.
     지식은 늘어나도 집행 지점은 한 자릿수에서 멈춘다.)

🔴 설계 원칙 — 리서치에서 역산한 것
    ① **LLM 판정 게이트를 만들지 않는다.** BMAD 의 QA 에이전트는 깨진 코드에 성공을
       보고했고, Superpowers 의 TDD 강제는 에이전트가 우회했다. 판정이 확률적이면
       "검증했다고 믿게" 만들어 없느니만 못하다. 이 엔진은 **결정론적 술어만** 쓴다.
    ② **오탐이 게이트를 죽인다.** 모든 룰에 탈출구를 두고, 파싱 실패·설정 부재는
       전부 fail-open(통과)이다. 게이트가 작업을 세우면 다음엔 통째로 꺼진다.
    ③ **룰 추가는 데이터 한 덩어리.** 코드를 고치는 건 새 *술어*가 필요할 때뿐이다.

룰 파일 (뒤가 앞을 덮어쓴다 — 같은 id 면 프로젝트 정의가 이김):
    ①  ${CLAUDE_PLUGIN_ROOT}/gates/rules.jsonc      하네스 기본
    ②  $CLAUDE_PROJECT_DIR/.claude/gates/rules.jsonc 프로젝트 추가·덮어쓰기
"""
import fnmatch
import json
import os
import re
import sys
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# 입력 정규화 — 훅 페이로드에서 술어들이 쓸 공통 필드를 뽑는다
# ─────────────────────────────────────────────────────────────────────────────


def ctx_from(data: dict) -> dict:
    tool = data.get("tool_name") or ""
    ti = data.get("tool_input") or {}
    if tool == "Write":
        body = ti.get("content") or ""
    elif tool == "Edit":
        body = ti.get("new_string") or ""
    else:
        body = ""
    return {
        "tool": tool,
        "input": ti,
        "path": (ti.get("file_path") or "").replace("\\", "/"),
        "body": body,
        "command": ti.get("command") or "",
        "replace_all": bool(ti.get("replace_all")),
        "root": Path(os.environ.get("CLAUDE_PROJECT_DIR") or "."),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 술어(predicate) — 각각 (ctx, args) -> (bool, 매치문자열)
#   새 술어가 필요할 때만 코드를 고친다. 룰 추가는 데이터로.
# ─────────────────────────────────────────────────────────────────────────────

INSTALL_CMD = re.compile(
    r"\b(?:npm\s+(?:i|install|add)|pnpm\s+(?:i|install|add)|yarn\s+add"
    r"|bun\s+(?:add|install)|npx\s+[\w@/.-]+)\b",
    re.IGNORECASE,
)
IMPORT_SPEC = re.compile(r"""(?:from|require\(|import\()\s*['"]([^'"]+)['"]""")
DEP_KEY = re.compile(r'"([^"]+)"\s*:\s*"[^"]*"')


def _pkg_name(spec: str) -> str:
    """버전 접미사를 뗀 패키지명. 스코프(`@org/pkg`)의 선두 @ 는 이름의 일부다."""
    s = spec.strip().lower().lstrip("~^")
    at = s.find("@", 1)
    return s[:at] if at > 0 else s


def _pkg_hit(spec: str, banned: list):
    n = _pkg_name(spec)
    for b in (x.lower() for x in banned):
        if n == b or n.startswith(b + "/"):
            return b
    return None


def p_path_glob(ctx, a):
    pats = a if isinstance(a, list) else [a]
    p = ctx["path"]
    return (any(fnmatch.fnmatch(p, g) for g in pats), p)


def p_tool_in(ctx, a):
    return (ctx["tool"] in a, ctx["tool"])


def p_body_regex(ctx, a):
    """{pattern, except?, flags?} — except 에 걸리는 매치는 무시(플레이스홀더 방어)."""
    if isinstance(a, str):
        a = {"pattern": a}
    fl = re.IGNORECASE if "i" in (a.get("flags") or "") else 0
    exc = re.compile(a["except"], fl) if a.get("except") else None
    for m in re.finditer(a["pattern"], ctx["body"], fl):
        if exc and exc.search(m.group(0)):
            continue
        return (True, m.group(0)[:60])
    return (False, "")


def p_pkg_ref(ctx, a):
    """금지 패키지 참조를 세 경로에서 동시에 본다 — 설치 명령 · deps · import."""
    banned = a["packages"] if isinstance(a, dict) else a
    if ctx["tool"] == "Bash":
        if INSTALL_CMD.search(ctx["command"]):
            for tok in re.split(r"[\s'\"]+", ctx["command"]):
                b = _pkg_hit(tok, banned)
                if b:
                    return (True, b)
        return (False, "")
    if ctx["path"].endswith("package.json"):
        for m in DEP_KEY.finditer(ctx["body"]):
            b = _pkg_hit(m.group(1), banned)
            if b:
                return (True, b)
        return (False, "")
    for m in IMPORT_SPEC.finditer(ctx["body"]):
        b = _pkg_hit(m.group(1), banned)
        if b:
            return (True, b)
    return (False, "")


def p_body_size_over(ctx, a):
    """'한 문장 diff' 예외의 기계적 정의. 신규 파일 생성은 크기와 무관하게 '큼'."""
    lines, chars = a.get("lines", 20), a.get("chars", 800)
    if ctx["tool"] == "Write" and ctx["path"] and not Path(ctx["path"]).exists():
        return (True, "신규 파일")
    if ctx["replace_all"]:
        return (True, "replace_all")
    big = ctx["body"].count("\n") + 1 > lines or len(ctx["body"]) > chars
    return (big, f"{ctx['body'].count(chr(10)) + 1}줄/{len(ctx['body'])}자")


def p_glob_exists(ctx, a):
    pats = a if isinstance(a, list) else [a]
    for g in pats:
        for f in ctx["root"].glob(g):
            if f.is_file():
                return (True, str(f.relative_to(ctx["root"])))
    return (False, "")


def p_harness_target(ctx, a):
    """하네스 관할 프로젝트인가. harness-boarding-guard 와 같은 판정(일관성)."""
    r = ctx["root"]
    if (r / ".claude-plugin").is_dir() or not (r / "package.json").is_file():
        return (False, "")
    hj = r / ".harness.json"
    if hj.is_file():
        try:
            if json.loads(hj.read_text()).get("role", "project") in ("template", "prototype"):
                return (False, "")
        except Exception:
            pass
    ok = (r / "docs" / "plans").is_dir() or (r / "docs" / "RULES.md").is_file() or (r / "supabase").is_dir()
    return (ok, str(r.name))


def p_escape_hatch(ctx, a):
    """탈출구가 켜져 있는가. 파일 또는 환경변수."""
    if (ctx["root"] / ".claude" / "state" / a).exists():
        return (True, a)
    env = "HARNESS_GATE_" + re.sub(r"[^A-Z0-9]", "_", a.upper())
    return ((os.environ.get(env) or "").lower() == "off", a)


PREDICATES = {
    "pathGlob": p_path_glob,
    "toolIn": p_tool_in,
    "bodyRegex": p_body_regex,
    "pkgRef": p_pkg_ref,
    "bodySizeOver": p_body_size_over,
    "globExists": p_glob_exists,
    "harnessTarget": p_harness_target,
    "escapeHatch": p_escape_hatch,
}


# ─────────────────────────────────────────────────────────────────────────────
# 룰 평가
# ─────────────────────────────────────────────────────────────────────────────


def strip_jsonc(t: str) -> str:
    """JSONC → JSON. **문자열 리터럴 안은 절대 건드리지 않는다.**

    🔴 정규식으로 벗기면 안 된다 (2026-08-17 실측 버그):
       `"*/src/*.ts", "*/src/*.tsx"` 안의 `/*` … `*/` 를 블록 주석으로 오인해
       glob 패턴을 통째로 삭제했고, 정규식 `[\\w.]{2,}` 의 쉼표를 후행 쉼표로 오인해
       `{2}` 로 바꿨다. 룰이 조용히 무력화되는데 파싱은 성공해서 더 위험했다.
       → 문자열 상태를 추적하는 스캐너로만 처리한다.
    """
    out, i, n = [], 0, len(t)
    in_str = esc = False
    while i < n:
        c = t[i]
        if in_str:
            out.append(c)
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
        elif c == "/" and i + 1 < n and t[i + 1] == "/":
            while i < n and t[i] != "\n":
                i += 1
        elif c == "/" and i + 1 < n and t[i + 1] == "*":
            i += 2
            while i + 1 < n and not (t[i] == "*" and t[i + 1] == "/"):
                i += 1
            i += 2
        else:
            out.append(c)
            i += 1
    # 후행 쉼표 제거도 문자열 밖에서만 — 위 스캐너 결과에 대해서만 적용한다.
    s = "".join(out)
    res, i, n = [], 0, len(s)
    in_str = esc = False
    while i < n:
        c = s[i]
        if in_str:
            res.append(c)
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            res.append(c)
            i += 1
        elif c == ",":
            j = i + 1
            while j < n and s[j] in " \t\r\n":
                j += 1
            if j < n and s[j] in "}]":
                i += 1          # 후행 쉼표 — 버린다
            else:
                res.append(c)
                i += 1
        else:
            res.append(c)
            i += 1
    return "".join(res)


def load_rules() -> list:
    """뒤 파일이 앞을 덮어쓴다(같은 id). 읽기 실패는 조용히 건너뛴다 — fail-open."""
    paths = []
    pr = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if pr:
        paths.append(Path(pr) / "gates" / "rules.jsonc")
    paths.append(Path(os.environ.get("CLAUDE_PROJECT_DIR") or ".") / ".claude/gates/rules.jsonc")
    merged = {}
    for p in paths:
        try:
            for r in json.loads(strip_jsonc(p.read_text(encoding="utf-8"))).get("rules", []):
                if r.get("id"):
                    merged[r["id"]] = r
        except Exception:
            continue
    return list(merged.values())


def evaluate(rule: dict, ctx: dict):
    """when 은 AND 리스트. 각 항목 {술어명: 인자, not?: true}. 전부 참이면 발동."""
    matched = ""
    for cond in rule.get("when", []):
        neg = bool(cond.get("not"))
        keys = [k for k in cond if k != "not"]
        if len(keys) != 1:
            return None
        fn = PREDICATES.get(keys[0])
        if not fn:
            return None  # 모르는 술어 = 룰 무효화(조용히). 엔진이 룰보다 오래 산다.
        try:
            ok, m = fn(ctx, cond[keys[0]])
        except Exception:
            return None  # fail-open
        if ok != (not neg):
            return None
        if m and not matched:
            matched = m
    return matched or "match"


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    ctx = ctx_from(data)
    if not ctx["tool"]:
        sys.exit(0)

    warns = []
    for rule in load_rules():
        if rule.get("enabled") is False:
            continue
        if ctx["tool"] not in (rule.get("on") or []):
            continue
        m = evaluate(rule, ctx)
        if m is None:
            continue
        msg = (rule.get("message") or f"게이트 `{rule['id']}` 위반").replace("{match}", m)
        if (rule.get("action") or "deny") == "deny":
            hatch = rule.get("off")
            tail = f"\n\n이 프로젝트만 예외가 필요하면 **사용자에게 먼저 확인**하고 `touch .claude/state/{hatch}`." if hatch else ""
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": msg + tail,
            }}, ensure_ascii=False))
            sys.exit(0)   # 첫 deny 에서 즉시 종료
        warns.append(msg)

    if warns:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": "\n".join(warns),
        }}, ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    main()
