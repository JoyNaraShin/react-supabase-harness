"""셸 명령에서 '실제로 쓰이는 경로'를 뽑는 공용 파서 — gate-engine(자기보호)과 block-impl-delegation(위임)이 함께 쓴다.

왜 공용인가: 두 훅이 각자 정규식으로 명령을 읽다 보니 같은 오판을 따로 냈다(4차 적대 리뷰).
  - 읽기 결과를 담은 heredoc 본문·따옴표 안의 `->` 를 명령·리다이렉트로 읽어 정상 작업을 막았다.
  - `cd src && cat > a.ts`, 디렉터리 목적지 `cp x src/`, `xargs sed -i` 처럼 흔한 쓰기 형태를 놓쳤다.

원칙: 데이터(heredoc 본문, 따옴표 안 문자열)는 명령이 아니다. 단 셸·인터프리터로 흘러가는
heredoc 과 `bash -c '…'` 의 인자는 실행되므로 명령으로 본다. 협조형 가드 — 난독화는 범위 밖.
"""
from __future__ import annotations
import os
import re

SHELLS = r"(?:bash|sh|zsh|dash|ash|eval)"
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_]\w*)\1")
_FEEDS_SHELL = re.compile(r"(?:^|[\s|(])(?:\S*/)?(?:bash|sh|zsh|dash|ash|python3?|node|ruby|perl)\b[^|;&]*<<|\|\s*(?:\S*/)?(?:bash|sh|zsh)\b")
LAUNCHERS = {"sudo", "doas", "env", "nohup", "time", "command", "builtin", "exec", "nice", "xargs", "stdbuf"}
DEST_LAST = {"cp", "install", "rsync", "ln"}
ALL_ARGS = {"touch", "truncate", "tee", "rm", "unlink", "mkdir", "sponge", "mv", "rmdir", "shred"}
IN_PLACE = {"sed", "perl", "awk", "gawk"}
REDIRECT = re.compile(r"(?<![0-9&<>-])>>?\|?\s*([^\s;&|<>()]+)|(?<![<>])[0-9]>>?\s*([^\s;&|<>()&]+)")


_FEEDS_POSIX_SHELL = re.compile(r"(?:^|[\s|(])(?:\S*/)?(?:bash|sh|zsh|dash|ash)\b[^|;&]*<<|\|\s*(?:\S*/)?(?:bash|sh|zsh)\b")


def strip_heredoc_bodies(cmd: str, shell_only: bool = False) -> str:
    """데이터 heredoc 본문을 지운다(헤더 줄은 남겨 `cat > f <<EOF` 의 리다이렉트는 그대로 본다).
    shell_only=True 면 셸이 읽는 본문만 남긴다 — python·node 본문은 실행되지만 셸 명령은 아니다."""
    lines, out, i = cmd.split("\n"), [], 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        m = _HEREDOC.search(line)
        i += 1
        if not m:
            continue
        tag, body = m.group(2), []
        while i < len(lines) and lines[i].strip() != tag:
            body.append(lines[i])
            i += 1
        if (_FEEDS_POSIX_SHELL if shell_only else _FEEDS_SHELL).search(line):
            out += body                                    # 셸·인터프리터가 읽는 본문은 실행된다
        if i < len(lines):
            out.append(lines[i])
            i += 1
    return "\n".join(out)


def neutralize_quotes(cmd: str) -> str:
    """따옴표 안의 셸 메타문자를 공백으로 — 문자열 속 `a -> b`·`x | y` 는 리다이렉트·파이프가 아니다.
    `bash -c '…'`·`eval '…'` 의 인자는 실행되므로 그대로 펼친다."""
    out, i, n = [], 0, len(cmd)
    while i < n:
        ch = cmd[i]
        if ch == "\\" and i + 1 < n:
            out.append(cmd[i + 1])
            i += 2
            continue
        if ch in "'\"":
            j = cmd.find(ch, i + 1)
            if j < 0:
                j = n
            inner = cmd[i + 1:j]
            before = "".join(out)
            if re.search(SHELLS + r"(?:\s+-\w+)*\s*$", before):
                out.append(inner)
            else:
                out.append(re.sub(r"[<>|;&]", " ", inner))
            i = j + 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def prep(cmd: str, shell_only: bool = False) -> str:
    text = neutralize_quotes(strip_heredoc_bodies(cmd, shell_only))
    text = re.sub(r"(^|\s)#[^\n]*", r"\1", text)          # 주석은 실행되지 않는다
    return text


_VAR_REF = re.compile(r"\$(?:\{([A-Za-z_]\w*)\}|([A-Za-z_]\w*))")
VAR_ANY = re.compile(r"\$(?:\{[^}]*\}|[A-Za-z_]\w*|[0-9@*#?$!-])")
# 값: 리터럴만. 따옴표·치환·메타문자·글롭(`*?[`)·틸드(대입값에서 펼쳐진다)·중괄호가 있으면 모르는 값이다.
# 값은 펼쳐서 명령 문자열에 다시 넣었을 때 같은 뜻으로 읽히는 문자만 인정한다(허용 목록). `#` 같은 문자는 펼친 자리가
# 단어 첫머리면 주석이 돼서 뒤의 쓰기·삭제 대상을 파서에게서 숨긴다 — 실제 bash 에서 `$C` 는 그냥 글자 `#` 다
# (0.35.1–0.35.2 우회, 2026-10-09 커밋 보안 리뷰).
_ASSIGN = re.compile(r"(?:export\s+)?([A-Za-z_]\w*)=((?:[\w./:@%+=-]|\$\{?[A-Za-z_]\w*\}?)*)")
_STMT_SPLIT = re.compile(r"(&&|\|\||[;&|\n])")
# bash 에서 변수 값을 바꾸거나 해석을 바꿀 수 있는 수단의 닫힌 목록(bash 매뉴얼 기준) — 대입 낱말과
# for/select 변수 외의 전부다. 하나라도 있으면 아무 변수도 펼치지 않는다: eval·source·`.`(임의 코드),
# read·mapfile·readarray·getopts·printf -v·declare·typeset·local·readonly·let·`((…))`·coproc(이름에 쓰기),
# `${x:=…}`·`${x=…}`(펼치며 대입), `${!x}`·declare -n(간접 참조), IFS(낱말 분할), trap(나중에 실행되는 문자열).
_BAIL = re.compile(
    r"(?<![\w.-])(?:eval|source|read|mapfile|readarray|getopts|declare|typeset|local|readonly|let|coproc|trap|IFS)(?![\w-])"
    r"|(?<![\w.-])printf(?:\s+-\S+)*\s+-v\b|\(\(|\$\{!|\$\{[A-Za-z_]\w*:?[=?]|(?:^|[\s;&|(])\.\s")
_SEQ = (None, ";", "\n", "&&")


def leading_assignments(cmd: str) -> dict:
    """값을 **확실히** 아는 변수만 돌려준다 — 그 밖은 모르는 값이고, 가드는 모르는 값을 보수적으로 다룬다.

    Claude Code 의 Bash 는 호출마다 셸 상태가 새로 시작되므로, 확실한 값의 출처는 명령 맨 앞의 대입뿐이다.
    인정: 명령 **맨 앞**에서 `&&`·`;`·줄바꿈으로만 이어진 무조건 리터럴 대입(`S=/a && T=$S/b && …`).
    거부: 값을 바꿀 수단(`_BAIL`)이 명령 어디에든 있거나, 그 이름이 `$NAME`·`${NAME}` 말고 다른 꼴로 다시
    나오면(재대입·`for S`·`unset S`) — 실제 셸의 값과 달라질 수 있다(v0.35.1 우회, 2026-10-09).
    환경변수는 쓰지 않는다 — 훅 프로세스의 환경은 Bash 의 환경과 같다는 보장이 없다.
    이 근사가 맞는지는 tests/test_shell_differential.py 가 실제 bash 로 대조한다."""
    if _BAIL.search(cmd):
        return {}
    known, parts = {}, _STMT_SPLIT.split(cmd)
    op = None
    for i in range(0, len(parts), 2):
        stmt = parts[i].strip()
        nxt = parts[i + 1] if i + 1 < len(parts) else None
        if not stmt:
            if nxt not in _SEQ:
                break
            op = nxt
            continue
        m = _ASSIGN.fullmatch(stmt)
        # `S=x | …`·`S=x & …` 는 서브셸, `false && S=x`·`S=x || …` 는 조건부 — 현재 셸 값이 확실하지 않다
        if not m or op not in _SEQ or nxt not in _SEQ:
            break
        val = expand_vars(m.group(2), known)
        if "$" in val:
            break
        known[m.group(1)] = val
        op = nxt
    # `&` 는 앞의 and-or 목록 전체(`S=x && T=y &`)를 백그라운드 서브셸로 보낸다 — 그 목록의 대입은 현재 셸에 없다.
    # 확정 구간이 끝난 지점부터 그 목록의 끝(`;`·줄바꿈·`&`)을 찾아, `&` 로 끝나면 그 목록에서 얻은 값을 버린다.
    list_names, j = [], 0
    for k in range(0, len(parts), 2):
        stmt, sep = parts[k].strip(), (parts[k + 1] if k + 1 < len(parts) else None)
        m2 = _ASSIGN.fullmatch(stmt) if stmt else None
        if m2 and m2.group(1) in known:
            list_names.append(m2.group(1))
        if sep in (";", "\n", None):
            list_names = []
            if not (m2 and m2.group(1) in known) and stmt:
                break                                         # 확정 구간 밖으로 나왔다
        elif sep == "&":
            for n in list_names:
                known.pop(n, None)
            break
        elif not (m2 and m2.group(1) in known):
            # 확정 구간이 끝났지만 같은 목록이 이어진다(`S=x && cmd …`) — 목록 끝까지 계속 본다
            continue
    for name in list(known):
        bare = len(re.findall(r"(?<![\w$])" + re.escape(name) + r"\b", cmd))
        braced = len(re.findall(r"\$\{" + re.escape(name) + r"\}", cmd))
        if bare - braced != 1:                               # 대입 그 자체 1회 외의 등장 = 값이 바뀔 수 있다
            del known[name]
    return known


def is_tainted(name: str, cmd: str) -> bool:
    """이 명령 안에서 값이 바뀔 수 있는 변수인가 — 값을 바꾸는 수단이 있거나 이름이 `$NAME` 말고 다른 꼴로 나온다.
    그런 변수의 값은 환경값도 빈 값도 아닌 '아무 값'이다."""
    if _BAIL.search(cmd):
        return True
    bare = len(re.findall(r"(?<![\w$])" + re.escape(name) + r"\b", cmd))
    braced = len(re.findall(r"\$\{" + re.escape(name) + r"\b", cmd))
    return bare - braced > 0


def expand_vars(text: str, known: dict) -> str:
    """확정된 변수만 펼친다(따옴표를 모르는 단순 치환 — 대입값 안에서만 쓴다)."""
    def sub(m):
        val = known.get(m.group(1) or m.group(2))
        return m.group(0) if val is None else val
    return _VAR_REF.sub(sub, text)


def expand_command(cmd: str, known: dict) -> str:
    """명령 문자열에서 확정된 변수를 bash 처럼 펼친다 — 작은따옴표·`$'…'`·`\\$` 와 heredoc 본문 안은 펼치지 않는다."""
    if not known:
        return cmd
    lines, out, i = cmd.split("\n"), [], 0
    while i < len(lines):
        line = lines[i]
        out.append(_expand_line(line, known))
        i += 1
        m = _HEREDOC.search(line)
        if not m:
            continue
        while i < len(lines) and lines[i].strip() != m.group(2):  # 본문은 그대로(인용 heredoc 은 펼치지 않는다)
            out.append(lines[i])
            i += 1
        if i < len(lines):
            out.append(lines[i])
            i += 1
    return "\n".join(out)


def _expand_line(line: str, known: dict) -> str:
    out, i, n, dq = [], 0, len(line), False
    while i < n:
        ch = line[i]
        if ch == "\\" and i + 1 < n:
            out.append(line[i:i + 2])
            i += 2
            continue
        if not dq and (ch == "'" or line.startswith("$'", i)):
            j = line.find("'", i + (2 if ch == "$" else 1))
            j = n if j < 0 else j + 1
            out.append(line[i:j])
            i = j
            continue
        if ch == '"':
            dq = not dq
        elif ch == "$":
            m = _VAR_REF.match(line, i)
            if m and (m.group(1) or m.group(2)) in known:
                out.append(known[m.group(1) or m.group(2)])
                i = m.end()
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def segments(cmd: str, cwd: str, shell_only: bool = False) -> list:
    """(하위 명령, 그 시점의 cwd) 목록. `cd X` 는 뒤 하위 명령의 상대 경로 기준을 바꾼다.
    확정된 변수는 펼친 뒤에 돌려준다 — `S=/tmp/x && cp a $S/b` 의 대상은 `$S/b`(프로젝트 안 상대 경로)가 아니다."""
    out, here = [], cwd
    cmd = expand_command(cmd, leading_assignments(cmd))
    for seg in re.split(r"[;&|\n]+", prep(cmd, shell_only).replace("(", " ").replace(")", " ")):
        seg = seg.strip()
        if not seg:
            continue
        m = re.match(r"(?:builtin\s+)?cd\s+(\S+)\s*$", seg)
        if m:
            d = os.path.expanduser(m.group(1))
            here = os.path.normpath(d if os.path.isabs(d) else os.path.join(here, d))
            continue
        out.append((seg, here))
    return out


def _tokens(seg: str) -> list:
    toks = [t for t in re.split(r"\s+", REDIRECT.sub(" ", seg).strip()) if t]
    while toks:
        head = toks[0].rsplit("/", 1)[-1]
        if re.match(r"^[A-Za-z_]\w*=", toks[0]) or head in LAUNCHERS or toks[0].startswith("-"):
            toks = toks[1:]
        elif head == "timeout" and len(toks) > 1:
            toks = toks[2:]
        else:
            break
    return toks


_RANGE = re.compile(r"(-?\d+|[^\d])\.\.(-?\d+|[^\d])(?:\.\.(-?\d+))?")


def _brace_range(body: str):
    """`{1..5}`·`{1..9..2}`·`{a..e}`·`{/../}` → (개수, 항목, 이식성). 범위가 아니면 None(그대로 둔다).
    단계(`..2`)와 글자 아닌 문자 범위는 bash 4+ 만 펼친다 — 이식성 False."""
    m = _RANGE.fullmatch(body)
    if not m:
        return None
    x, y, st = m.group(1), m.group(2), abs(int(m.group(3) or 1)) or 1
    num = lambda v: re.fullmatch(r"-?\d+", v) is not None
    if num(x) and num(y):
        a, b = int(x), int(y)
        conv = str
    elif len(x) == 1 and len(y) == 1 and not num(x) and not num(y):
        a, b = ord(x), ord(y)
        conv = chr
    else:
        return None
    step = st if a <= b else -st
    n = abs(b - a) // st + 1
    portable = m.group(3) is None and (conv is str or (x.isalpha() and y.isalpha()))
    return n, (conv(a + k * step) for k in range(n)), portable


def brace_expand(t: str, limit: int = 256):
    """bash 중괄호 확장(`a{,/..}`·`{1..3}`·`{a..c}`) — 대상 하나가 여러 경로가 된다. 결과가 limit 을 넘으면 None
    (전부 보지 못한 판정은 판정이 아니다 — 잘라서 앞부분만 보면 뒤에 숨긴 대상을 놓친다). `${…}`·따옴표 안은 확장하지 않는다."""
    depth, start, m, q = 0, -1, None, None
    for i, ch in enumerate(t):
        if q:
            q = None if ch == q else q
            continue
        if ch in "'\"":
            q = ch
        elif ch == "{" and (i == 0 or t[i - 1] != "$"):
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0:
                m = (start, i)
                break
    if not m:
        if depth and start >= 0:                             # 닫히지 않은 `{` 는 글자 — 그 뒤의 중괄호는 펼쳐진다
            rest = brace_expand(t[start + 1:], limit)
            return None if rest is None else [t[:start + 1] + x for x in rest]
        return [t]
    pre, body, post = t[:m[0]], t[m[0] + 1:m[1]], t[m[1] + 1:]
    items, d, cur = [], 0, ""
    for ch in body:                                          # 최상위 쉼표로 나눈다
        if ch == "," and d == 0:
            items.append(cur)
            cur = ""
            continue
        d += ch == "{"
        d -= ch == "}"
        cur += ch
    items.append(cur)
    r, portable = None, True
    if len(items) == 1:
        r = _brace_range(body)
        if r is None:                                        # `{x}` 는 확장되지 않는다 — 뒤쪽 중괄호는 본다
            rest = brace_expand(post, limit)
            return None if rest is None else [pre + "{" + body + "}" + x for x in rest]
        n, gen, portable = r
        if n > limit:
            return None
        items = list(gen)
    out = []
    if r is not None and not portable:                       # bash 3.2 는 그대로 두고 4+ 는 펼친다 — 둘 다 본다
        rest = brace_expand(post, limit)
        if rest is None:
            return None
        out += [pre + "{" + body + "}" + x for x in rest]
    for it in items:
        sub = brace_expand(pre + it + post, limit)
        if sub is None:
            return None
        out += sub
        if len(out) > limit:
            return None
    return out


def write_targets(seg: str, whole: str = "") -> list:
    """이 하위 명령이 쓰는 경로. 대상이 파이프·find 결과라 드러나지 않으면 명령 전체의 경로 인자를 대신 돌려준다."""
    out = [a or b for a, b in REDIRECT.findall(seg) if (a or b) not in ("/dev/null", "&1", "&2")]
    toks = _tokens(seg)
    if not toks:
        return out
    head = toks[0].rsplit("/", 1)[-1]
    args = [t for t in toks[1:] if not t.startswith("-")]
    if head in DEST_LAST and args:
        out.append(args[-1])
    elif head in ALL_ARGS:
        out += args
    elif head == "dd":
        out += [t[3:] for t in toks if t.startswith("of=")]
    # 줄 어디에 있든 in-place 편집기(find -exec, xargs 뒤 포함)
    for k, t in enumerate(toks):
        name = t.rsplit("/", 1)[-1]
        if name in IN_PLACE and any(x.startswith("-") and "i" in x.lstrip("-")[:3] for x in toks[k + 1:k + 4]):
            rest = [x for x in toks[k + 1:] if not x.startswith("-") and x not in ("{}", "+", "\\;", ";")]
            files = rest[1:] if name in ("sed", "awk", "gawk") or "-e" in toks[k + 1:k + 4] else rest
            if files:
                out += files
            else:                                          # xargs/find 가 대상을 공급 — 명령 전체의 경로를 본다
                out += [x for x in re.split(r"[\s;&|]+", prep(whole or seg)) if x and not x.startswith("-")
                        and ("/" in x or "." in x or x.isalpha())]
            break
    return out
