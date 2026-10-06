#!/usr/bin/env python3
"""게이트 엔진 — 훅 1개가 데이터로 정의된 룰 N개를 집행한다.

왜 엔진인가 :
    룰 하나당 훅 하나로 만들면 룰 수만큼 훅이 늘고, 훅마다 발동 범위·탈출구·오탐 처리를
    다시 짜게 된다. 업계 정답은 ESLint 모델이다 — **룰 로직은 재사용 술어로 코드에,
    무엇을 어떤 조건으로 켤지는 데이터로.**
    지식(에이전트·스킬)은 늘어나도 집행 지점은 한 자릿수로 유지한다.

🔴 설계 원칙
    ① **LLM 판정 게이트를 만들지 않는다.** 판정이 확률적이면 "검증했다고 믿게"
       만들어 없느니만 못하다. 이 엔진은 **결정론적 술어만** 쓴다.
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shellparse  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# 입력 정규화 — 훅 페이로드에서 술어들이 쓸 공통 필드를 뽑는다
# ─────────────────────────────────────────────────────────────────────────────


def ctx_from(data: dict) -> dict:
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or ".")
    tool = data.get("tool_name") or ""
    ti = data.get("tool_input") or {}
    edits = None
    if tool == "MultiEdit":
        edits = [e for e in (ti.get("edits") or []) if isinstance(e, dict)]
        tool = "Edit"
        ti = dict(ti, new_string="\n".join(str(e.get("new_string") or "") for e in edits))
    if tool == "Write":
        body = ti.get("content") or ""
    elif tool == "Edit":
        body = ti.get("new_string") or ""
    elif tool == "NotebookEdit":
        body = ti.get("new_source") or ""
    else:
        body = ""
    return {
        "tool": tool,
        "input": ti,
        "path": (ti.get("file_path") or ti.get("notebook_path") or "").replace("\\", "/"),
        "body": body,
        "command": ti.get("command") or "",
        "replace_all": bool(ti.get("replace_all")),
        "edits": edits,
        "root": root,
        "cwd": str(data.get("cwd") or root),
        "phase": "post" if data.get("hook_event_name") == "PostToolUse" else "pre",
    }


DELETE_VERBS = {"rm", "unlink", "rmdir", "shred"}


def _bash_writes(ctx) -> list:
    """Bash 가 프로젝트 안에 쓰는 (절대 경로, 동사) 목록. heredoc·cp·sed -i 로 만든 코드도 Write 와 같은 축으로 본다."""
    if "writes" not in ctx:
        out, base = [], os.path.realpath(str(ctx["root"]))
        for seg, here in shellparse.segments(ctx["command"], ctx["cwd"]):
            toks = shellparse._tokens(seg)
            verb = toks[0].rsplit("/", 1)[-1] if toks else ""
            for t in shellparse.write_targets(seg, ctx["command"]):
                p = os.path.normpath(os.path.join(here, os.path.expanduser(t)))
                if os.path.realpath(p).startswith(base + os.sep):
                    out.append((p.replace("\\", "/"), verb))
        ctx["writes"] = out
    return ctx["writes"]


# ─────────────────────────────────────────────────────────────────────────────
# 술어(predicate) — 각각 (ctx, args) -> (bool, 매치문자열)
#   새 술어가 필요할 때만 코드를 고친다. 룰 추가는 데이터로.
# ─────────────────────────────────────────────────────────────────────────────

# 하위 명령 단위 설치 판정 — 옵션·워크스페이스 지정이 끼어도(`pnpm -F web add`, `yarn workspace web add`,
# `npm --prefix web i`) 설치다. 같은 줄의 다른 하위 명령(`&& grep <금지> src/`)은 설치 인자가 아니다.
PKG_MANAGERS = {"npm", "pnpm", "yarn", "bun", "deno", "volta"}
INSTALL_VERBS = {"i", "in", "ins", "install", "add", "a", "dlx", "exec", "x", "create", "init"}
RUNNERS = {"npx", "bunx", "pnpx"}


def _install_args(cmd: str) -> list:
    out = []
    for seg in re.split(r"[;&|\n]+", cmd):
        toks = [t for t in re.split(r"[\s'\"]+", seg) if t]
        while toks and (re.match(r"^[A-Za-z_]\w*=", toks[0]) or toks[0] in ("sudo", "env", "command")):
            toks = toks[1:]
        if not toks:
            continue
        head = toks[0].rsplit("/", 1)[-1].lower()
        if head in RUNNERS:
            out += toks[1:]
        elif head in PKG_MANAGERS and any(t.lower() in INSTALL_VERBS for t in toks[1:]):
            out += toks[1:]
        elif head in PKG_MANAGERS and "pkg" in toks and "set" in toks:
            # `npm pkg set dependencies.<이름>=<버전>` — package.json 을 직접 고치는 설치
            out += [m.group(1) for t in toks for m in [PKG_SET_KEY.match(t)] if m]
    return out


PKG_SET_KEY = re.compile(r"^(?:dev|peer|optional)?[dD]ependencies(?:\.|\[)([^=\]]+?)\]?=")


def _spec_names(spec: str) -> list:
    """설치 스펙 하나가 실제로 가져오는 패키지 이름 후보.
    `별칭@npm:<이름>@1` · `npm:<이름>` · 레지스트리 tarball URL(`…/<이름>/-/<이름>-1.0.0.tgz`) · `<이름>-1.0.0.tgz`."""
    s = spec.strip().strip("'\"")
    names = [s]
    if "npm:" in s:
        names.append(s.split("npm:", 1)[1])
    m = re.search(r"/((?:@[^/]+/)?[^/]+)/-/[^/]+\.tgz$", s)
    if m:
        names.append(m.group(1))
    m = re.match(r"^(?:.*/)?((?:@[^/]+/)?[^/]+?)-v?\d+\.\d+[^/]*\.tgz$", s)
    if m:
        names.append(m.group(1))
    return names
# `from '<x>'`·`require('<x>')`·`import('<x>')`, 그리고 side-effect import `import '<x>/style.css'`
IMPORT_SPEC = re.compile(r"""(?:\bfrom|\brequire\(|\bimport\(|^\s*import)\s*['"]([^'"]+)['"]""", re.MULTILINE)
CSS_IMPORT = re.compile(r"""@(?:import|use|forward)\s+(?:url\()?\s*['"]?([^'")\s;]+)""")
DEP_KEY = re.compile(r'"([^"]+)"\s*:\s*"([^"]*)"')
JS_SOURCE = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts", ".vue", ".svelte", ".astro")
CSS_SOURCE = (".css", ".scss", ".sass", ".less", ".pcss")
DEP_FIELDS = ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies")


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
    if ctx["tool"] == "Bash":
        hits = [w for w in _bash_writes(ctx) if any(fnmatch.fnmatch(w[0], g) for g in pats)]
        ctx["matched_writes"] = hits
        return (bool(hits), hits[0][0] if hits else "")
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


def _any_hit(specs, banned):
    for spec in specs:
        for n in _spec_names(spec):
            b = _pkg_hit(n, banned)
            if b:
                return b
    return None


def _dep_hits(text: str, banned: list) -> set:
    """package.json 텍스트에서 의존성 필드의 금지 패키지(키 또는 값 스펙)."""
    try:
        data = json.loads(text)
    except Exception:
        return set()
    hits = set()
    for f in DEP_FIELDS:
        for k, v in (data.get(f) or {}).items() if isinstance(data.get(f), dict) else []:
            b = _any_hit([k, str(v)], banned)
            if b:
                hits.add(b)
    return hits


def _introduced_deps(root: Path, banned: list):
    """명령 실행 결과로 새로 들어온 금지 의존성 — 커밋된 package.json(HEAD)에 없던 것만.
    설치 표기(별칭·tarball·`npm pkg set`·스크립트)와 무관하게 결과로 판정한다. 이미 커밋된 의존성은
    이 훅의 몫이 아니다(매 명령마다 같은 경고가 반복되면 게이트가 꺼진다). git 이 아니면 판정하지 않는다."""
    import subprocess
    files = [root / "package.json"] + sorted(root.glob("*/package.json")) + sorted(root.glob("*/*/package.json"))
    for f in files:
        if "node_modules" in f.parts or not f.is_file():
            continue
        rel = str(f.relative_to(root))
        try:
            r = subprocess.run(["git", "-C", str(root), "show", f"HEAD:{rel}"], capture_output=True, text=True,
                               timeout=5)
        except Exception:
            return None
        if r.returncode != 0 and "not a git repository" in r.stderr.lower():
            return None
        before = _dep_hits(r.stdout, banned) if r.returncode == 0 else set()
        new = _dep_hits(f.read_text(encoding="utf-8", errors="replace"), banned) - before
        if new:
            return f"{sorted(new)[0]} ({rel})"
    return None


def p_pkg_ref(ctx, a):
    """금지 패키지 참조 — 설치 명령 · deps · import(side-effect·CSS 포함). 실행 뒤(post)에는 결과 package.json."""
    banned = a["packages"] if isinstance(a, dict) else a
    if ctx["phase"] == "post":
        hit = _introduced_deps(ctx["root"], banned)
        return (bool(hit), hit or "")
    if ctx["tool"] == "Bash":
        b = _any_hit(_install_args(ctx["command"]), banned)
        return (bool(b), b or "")
    if ctx["path"].endswith("package.json"):
        b = _any_hit([x for m in DEP_KEY.finditer(ctx["body"]) for x in m.groups()], banned)
        return (bool(b), b or "")
    # import 구문은 JS·CSS 계열 소스에서만 의미가 있다. .py·.md 안의 예시 문자열은 도입이 아니다
    # (실측: 테스트 픽스처 문자열을 UI 라이브러리 도입으로 차단).
    low = ctx["path"].lower()
    if low.endswith(JS_SOURCE):
        specs = [m.group(1) for m in IMPORT_SPEC.finditer(ctx["body"])]
    elif low.endswith(CSS_SOURCE):
        specs = [m.group(1) for m in CSS_IMPORT.finditer(ctx["body"])]
    else:
        return (False, "")
    b = _any_hit(specs, banned)
    return (bool(b), b or "")


def p_body_size_over(ctx, a):
    """'한 문장 diff' 예외의 기계적 정의. 신규 파일 생성은 크기와 무관하게 '큼'."""
    lines, chars = a.get("lines", 20), a.get("chars", 800)
    if ctx["tool"] == "Bash":
        writes = ctx.get("matched_writes", _bash_writes(ctx))
        for p, verb in writes:
            if verb not in DELETE_VERBS and not Path(p).exists():
                return (True, f"신규 파일 {os.path.basename(p)} (셸)")
        body = ctx["command"]
        big = bool(writes) and (body.count("\n") + 1 > lines or len(body) > chars)
        return (big, f"셸 {body.count(chr(10)) + 1}줄/{len(body)}자")
    if ctx["tool"] == "Write" and ctx["path"] and not Path(ctx["path"]).exists():
        return (True, "신규 파일")
    if ctx["replace_all"]:
        return (True, "replace_all")
    big = ctx["body"].count("\n") + 1 > lines or len(ctx["body"]) > chars
    return (big, f"{ctx['body'].count(chr(10)) + 1}줄/{len(ctx['body'])}자")


def p_glob_exists(ctx, a):
    """`"패턴"` | `[패턴…]` | `{"pattern": …, "except": [파일명 패턴…]}`.
    except 는 파일 이름에 대조한다 — 예: 플랜 템플릿(`*.template.md`)은 플랜이 아니다."""
    if isinstance(a, dict):
        pats, excl = a.get("pattern", []), a.get("except", [])
    else:
        pats, excl = a, []
    pats = pats if isinstance(pats, list) else [pats]
    excl = excl if isinstance(excl, list) else [excl]
    for g in pats:
        for f in ctx["root"].glob(g):
            if f.is_file() and not any(fnmatch.fnmatch(f.name, x) for x in excl):
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
    # 매치 문자열을 내지 않는다 — 관할 여부는 '무엇이 걸렸나'가 아니라 전제 조건이다.
    # (프로젝트 폴더명을 내면 plan-first 차단 메시지에 파일 경로 대신 폴더명이 찍힌다.)
    return (ok, "")


def p_escape_hatch(ctx, a):
    """탈출구가 켜져 있는가. 파일 또는 환경변수."""
    if (ctx["root"] / ".claude" / "state" / a).is_file():  # 디렉터리는 탈출구가 아니다
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

    🔴 정규식으로 벗기면 안 된다 (실측 버그):
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
        # 부정 조건의 반환값(예: 꺼져 있는 탈출구 이름)은 '무엇이 걸렸나'가 아니다.
        # 실측: no-ui-library 차단 메시지에 패키지명 대신 `ui-lib-gate-off` 가 찍혔다.
        if m and not neg and not matched:
            matched = m
    return matched or "match"


# ─────────────────────────────────────────────────────────────────────────────
# 자기 보호 — 데이터 룰이 아니라 코드다(룰 파일로 끌 수 없어야 의미가 있다)
#   게이트에 막힌 에이전트가 툴 한 번으로 탈출구를 켜거나 룰 파일을 덮어쓰면
#   모든 deny 룰이 자기 신고로 무너진다. 탈출구는 사람이 켠다.
# ─────────────────────────────────────────────────────────────────────────────

PROTECTED_PATH = re.compile(
    r"(^|/)\.claude/(state/[\w.-]*gate-off|state/verdicts\.jsonl|gates/rules\.jsonc?)$"
    r"|(^|/)\.claude/plugins/.*/gates/rules\.jsonc?$", re.IGNORECASE)
HARNESS_ID = "react-supabase-harness"
# 설정에서 게이트를 끄는 키 — 탈출구 env · 훅 전체 끄기 · 플러그인 비활성화(JSON 과 jq 문법 둘 다)
SETTINGS_OFF = re.compile(
    r"HARNESS_GATE_|disableAllHooks"
    r"|" + HARNESS_ID + r"@[\w-]+[\"'\]]*\s*[:=]\s*(?:false|0|null)\b"
    r"|del\([^)]*" + HARNESS_ID, re.IGNORECASE)
SETTINGS_PATH = re.compile(r"(^|/)\.claude/settings(\.local)?\.json$", re.IGNORECASE)
PLUGINS_DIR = os.path.realpath(os.path.expanduser("~/.claude/plugins"))


def _unescape(text: str) -> str:
    """JSON 의 \\uXXXX 이스케이프로 키 이름을 가리는 우회를 푼다."""
    return re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), text)


def _in_plugin_root(path: str) -> bool:
    root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    return bool(root) and os.path.normpath(path).startswith(os.path.normpath(root) + os.sep)


def _installed_plugin_file(path: str) -> bool:
    """설치된 플러그인(훅 코드·hooks.json 포함)인가. 훅 파일 하나를 고치면 모든 게이트가 꺼진다.
    `data/` 는 플러그인이 쓰는 상태 디렉터리라 제외한다. `--plugin-dir` 로 소스 레포를 직접
    로드해 개발하는 경우는 설치본이 아니므로 막지 않는다."""
    return path.startswith(PLUGINS_DIR + "/") and not path.startswith(PLUGINS_DIR + "/data/")


def _candidates(path: str, root: Path) -> set:
    """판정할 경로 후보 — 정규화한 경로와 링크를 푼 실제 경로. 별칭 링크로 보호를 피하지 못한다."""
    if not path:
        return set()
    p = os.path.expanduser(path)
    if not os.path.isabs(p):
        p = os.path.join(str(root), p)
    out = {os.path.normpath(p)}
    try:
        out.add(os.path.realpath(p))
    except Exception:
        pass
    return {c.replace("\\", "/") for c in out}


def _protected_file(path: str, root: Path) -> bool:
    return any(PROTECTED_PATH.search(c) or _installed_plugin_file(c)
               or (_in_plugin_root(c) and re.search(r"/gates/rules\.jsonc?$", c))
               for c in _candidates(path, root))


def _settings_after(ctx: dict):
    """편집이 적용된 뒤의 settings 내용. new_string 만 보면 `: true` → `: false` 나 항목 삭제를 놓친다."""
    try:
        before = Path(os.path.expanduser(ctx["path"])).read_text(encoding="utf-8")
    except Exception:
        return None, ctx["body"]
    if ctx["tool"] == "Write":
        return before, ctx["body"]
    if ctx.get("edits") is not None:
        after = before
        for e in ctx["edits"]:
            old = str(e.get("old_string") or "")
            if old and old in after:
                after = after.replace(old, str(e.get("new_string") or ""), -1 if e.get("replace_all") else 1)
        return before, after
    old = ctx["input"].get("old_string") or ""
    if not old or old not in before:
        return before, ctx["body"]
    return before, before.replace(old, ctx["body"], -1 if ctx["replace_all"] else 1)


def _harness_enabled(settings_text: str):
    try:
        plugins = json.loads(settings_text).get("enabledPlugins") or {}
    except Exception:
        return None
    vals = [v for k, v in plugins.items() if k.startswith(HARNESS_ID + "@")]
    return None if not vals else all(v is True for v in vals)


def settings_turns_off(ctx: dict) -> bool:
    before, after = _settings_after(ctx)
    if SETTINGS_OFF.search(_unescape(ctx["body"])):
        return True
    try:
        data = json.loads(after)
    except Exception:
        return False
    env = data.get("env") or {}
    if data.get("disableAllHooks") or any(str(k).upper().startswith("HARNESS_GATE_") for k in env):
        return True
    return before is not None and _harness_enabled(before) is True and _harness_enabled(after) is not True


# 셸 명령은 하위 명령(`;` `&&` `|` 줄바꿈) 단위로 본다. 명령 전체에서 "보호 대상 언급"과 "쓰기"를
# 따로 찾으면, `rg 'gate-off' hooks/ > out.txt` 처럼 읽기 결과를 다른 곳에 저장하는 정상 작업이 막힌다.
PROTECTED_MENTION = re.compile(r"gate-?off|rules\.jsonc?|verdicts\.jsonl|\.claude/[{]?\s*(?:state|gates)\b", re.IGNORECASE)
CLAUDE_DIR = re.compile(r"(?:^|\s)(?:\S*/)?\.claude/?\.?(?=\s|$)")
PLUGIN_MENTION = re.compile(r"\.claude/plugins(?:/(?!data(?:/|\s|$))|/?(?=\s|$))|\$\{?CLAUDE_PLUGIN_ROOT\b")
SETTINGS_FILE = re.compile(r"(?:^|/)settings(?:\.local)?\.json$")
# 대상 경로를 바꾸는 동사. 인터프리터 한 줄 실행도 같은 하위 명령에 보호 경로가 있으면 쓰기로 본다.
WRITE_VERB = re.compile(
    r"(?:^|[\s(`])(?:touch|tee|cp|mv|ln|install|truncate|mkdir|dd|rsync|ed|ex|rm|unlink|chmod|chown|"
    r"curl|wget|tar|unzip|sed\s+-i\S*|perl\s+-\S*[ie]\S*|awk\s+-i|python3?\s+-c|node\s+-e|ruby\s+-e)\b"
    r"|\bgit\s+(?:checkout|restore|apply|stash\s+pop)\b")
REDIRECT_TARGET = re.compile(r"(?<![0-9&])>>?\|?\s*([^\s;&|]+)")
DISABLE_PLUGIN = re.compile(
    r"\bclaude\s+plugins?\s+(?:disable|uninstall|remove|rm)\b[^;&|]*react-supabase"
    r"|\bclaude\s+plugins?\s+marketplace\s+(?:remove|rm)\b[^;&|]*react-supabase", re.IGNORECASE)


def _segments(cmd: str) -> list:
    flat = shellparse.prep(_unescape(cmd))              # heredoc 데이터·따옴표 속 메타문자 제거, 주석 제거
    flat = re.sub(r"/+", "/", flat.replace("/./", "/"))
    flat = re.sub(r"~(?=/)|\$\{?HOME\}?(?=/)", os.path.expanduser("~"), flat)
    return [s.strip() for s in re.split(r"[;&|\n]+", flat) if s.strip()]


def shell_touches_protected(cmd: str):
    """보호 대상을 바꾸는 하위 명령을 찾는다. 앞선 `cd .claude/…` 로 위치를 옮긴 뒤의 쓰기도 본다."""
    if DISABLE_PLUGIN.search(cmd):
        return "plugin-disable"
    inside = False
    for seg in _segments(cmd):
        if re.match(r"cd\s+\S*\.claude/(?:state|gates|plugins/(?!data))", seg):
            inside = True                                # 게이트·플러그인 디렉터리 안으로 이동
        targets = shellparse.write_targets(seg)
        if any(SETTINGS_FILE.search(t) for t in targets) or re.match(
                r"git\s+(?:checkout|restore)\b.*settings(?:\.local)?\.json", seg):
            return "settings-bash"
        settings = re.search(r"settings(?:\.local)?\.json", seg) and SETTINGS_OFF.search(seg)
        verb = WRITE_VERB.search(seg)
        if verb and verb.group(0).strip().split()[0] in ("cp", "rsync", "install"):
            dest = [t for t in seg.split()[1:] if not t.startswith("-")][-1:]   # 복사는 마지막 인자만 쓴다
            scope = " ".join(dest)
        else:
            scope = seg
        mention = PROTECTED_MENTION.search(scope) or settings or PLUGIN_MENTION.search(scope) or (
            "/.claude/plugins/" in scope and "/.claude/plugins/data/" not in scope)
        if verb and verb.group(0).strip().startswith("mkdir") and not re.search(r"gate-?off", scope, re.I):
            verb = None                                  # 디렉터리는 탈출구가 아니다 — 셋업용 mkdir 은 통과
        if verb and (mention or inside):
            return seg
        if verb and CLAUDE_DIR.search(seg) and not seg.startswith("mkdir"):
            return seg                                   # `.claude` 디렉터리째 덮어쓰기
        if re.match(r"(?:\S*/)?ln\b", seg) and re.search(r"\.claude\b", seg):
            return seg                                   # `.claude` 로 가는 별칭 링크
        for m in REDIRECT_TARGET.finditer(seg):
            t = m.group(1)
            if t == "/dev/null":
                continue
            if inside or PROTECTED_MENTION.search(t) or PLUGIN_MENTION.search(t) or "/.claude/plugins/" in t \
                    or (settings and re.search(r"settings(?:\.local)?\.json", t)):
                return seg
    return None


def _bash_resolved_targets(cmd: str, root: Path) -> bool:
    """링크를 거쳐 보호 대상에 닿는 쓰기 — `ln -s .claude cfg` 로 만든 별칭 경유.
    링크를 푼 경로가 원래 경로와 다를 때만 본다(링크 없는 경로는 위의 문자열 판정이 맡는다)."""
    import glob
    for seg in _segments(cmd):
        for tok in shellparse.write_targets(seg):
            if "/" not in tok:
                continue
            base = tok if os.path.isabs(tok) else os.path.join(str(root), tok)
            for p in (glob.glob(base) or [base]):
                try:
                    real = os.path.realpath(p)
                except Exception:
                    continue
                if real != os.path.normpath(p) and _protected_file(real, root):
                    return True
    return False


def self_protect(ctx: dict):
    if ctx["tool"] in ("Write", "Edit", "NotebookEdit"):  # MultiEdit 는 ctx_from 에서 Edit 로 정규화
        hit = _protected_file(ctx["path"], ctx["root"]) or (
            bool(SETTINGS_PATH.search(ctx["path"])) and settings_turns_off(ctx))
    elif ctx["tool"] == "Bash":
        hit = shell_touches_protected(ctx["command"]) or _bash_resolved_targets(ctx["command"], ctx["root"])
    else:
        hit = None
    if not hit:
        return None
    if hit == "settings-bash":
        return ("settings 파일을 셸로 쓰면 하네스·훅이 꺼지는지 판정할 수 없다 — 셸 쓰기는 막는다.\n"
                "다음 행동: Edit 툴로 필요한 항목만 고쳐라(변경 후 상태를 보고 판정한다). 하네스를 끄는 변경이면 "
                "사용자가 직접 한다.")
    return ("게이트 설정(탈출구·룰 파일·settings 의 하네스 항목)과 설치된 플러그인 코드는 에이전트가 "
            "바꾸지 않는다 — 막힌 쪽이 스스로 문을 열면 게이트가 자기 신고가 된다.\n"
            "다음 행동: 원래 하려던 작업을 게이트 안에서 할 방법을 찾거나, 예외가 필요한 이유를 "
            "사용자에게 설명하고 사용자가 직접 바꾸게 하라.")


def post_check(ctx: dict) -> None:
    """PostToolUse — 룰의 `after` 에 든 도구가 끝난 뒤 결과로 판정한다(명령 표기와 무관).
    막을 수는 없으니(이미 실행됨) 모델에게 되돌리라고 돌려준다."""
    for rule in load_rules():
        if rule.get("enabled") is False or ctx["tool"] not in (rule.get("after") or []):
            continue
        m = evaluate(rule, ctx)
        if m is None:
            continue
        msg = (rule.get("message") or f"게이트 `{rule['id']}` 위반").replace("{match}", m)
        print(json.dumps({"decision": "block", "reason": (
            "방금 실행한 명령의 결과가 게이트에 걸렸다(설치 표기와 무관하게 결과 파일을 본다).\n" + msg
            + "\n\n다음 행동: 이 명령이 넣은 것을 되돌려라(예: 패키지 매니저의 remove). 필요한 도입이면 "
              "사용자에게 이유를 설명하고 결정을 받는다.")}, ensure_ascii=False))
        sys.exit(0)
    sys.exit(0)


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    ctx = ctx_from(data)
    if not ctx["tool"]:
        sys.exit(0)

    if ctx["phase"] == "post":
        post_check(ctx)
    guard = self_protect(ctx)
    if guard:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": guard,
        }}, ensure_ascii=False))
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
            lead = "" if "다음 행동" in msg else "다음 행동: 게이트 안의 대안으로 진행하거나, "
            tail = (f"\n\n{lead}이 프로젝트만 예외가 필요하면 이유와 함께 사용자에게 요청하라 — 사용자가 "
                    f"직접 `.claude/state/{hatch}` 를 만든다(에이전트의 쓰기는 차단된다).") if hatch else ""
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
