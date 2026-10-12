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
# 종료어는 bash 처럼 메타문자 전까지의 낱말 전체에서 따옴표·백슬래시를 뗀 것이다(`$` 도 펼치지 않는다 — `<<E$X` 의
# 종료어는 `E$X`). `<<EOF"X"` 의 종료어는 `EOFX` 다(이름 부분만 잡으면
# 본문 속 `EOF` 줄에서 끝난 것으로 읽어 뒤 명령을 숨겼다 — 0.41.0 독립 검증). group(2) 는 heredoc_tag() 로 읽는다.
_HEREDOC = re.compile(r"(?<!<)<<-?[ \t]*()((?![0-9])(?:[^\s;&|<>()'\"\\]|\\.|'[^'\n]*'|\"[^\"\n]*\")+)")


def heredoc_tag(m) -> str:
    return re.sub(r"\\(.)|['\"]", lambda x: x.group(1) or "", m.group(2))
_FEEDS_SHELL = re.compile(r"(?:^|[\s|(])(?:\S*/)?(?:bash|sh|zsh|dash|ash|python3?|node|ruby|perl)\b[^|;&]*<<|\|\s*(?:\S*/)?(?:bash|sh|zsh)\b")
# 남은 인자를 명령으로 실행하는 런처. 값 = 따로 값을 받는 옵션(그 값을 프로그램으로 읽지 않게).
# 게이트마다 따로 두던 표(파괴 가드·정정 게이트·plan-first·설치 판정)를 여기 하나로 모았다(0.40.0) —
# 같은 명령을 게이트마다 다르게 읽었다(`env -u X gh pr create` 를 파괴 가드는 gh 로, plan-first 는 -u 로).
LAUNCHERS = {
    "env": {"-u", "--unset", "-C", "--chdir"},
    "command": set(), "builtin": set(), "exec": {"-a"}, "nohup": set(),
    "time": {"-f", "--format", "-o", "--output"},
    "sudo": {"-u", "--user", "-g", "--group", "-h", "--host", "-p", "--prompt",
             "-C", "--close-from", "-D", "--chdir", "-r", "--role", "-t", "--type"},
    "doas": {"-u", "-C"},
    "nice": {"-n", "--adjustment"},
    "stdbuf": {"-i", "-o", "-e"},
    "xargs": {"-I", "-L", "-n", "-P", "-d", "-E", "-s", "-a", "--arg-file",
              "--delimiter", "--max-args", "--max-procs", "--replace"},
}
# 첫 위치 인자가 프로그램이 아닌 런처(timeout DURATION cmd).
LEADING_POSITIONAL = {"timeout": {"-s", "--signal", "-k", "--kill-after"}}
# 명령 앞에 오는 셸 문법어: `while read b; do git branch -D "$b"; done` 의 `do` 뒤도 명령이다.
SHELL_KEYWORDS = {"do", "then", "else", "elif", "if", "while", "until", "{", "}", "!", "done", "fi", "esac"}
ASSIGN_WORD = re.compile(r"^[A-Za-z_]\w*=")
# git 의 값 받는 전역 옵션(git.c 기준) — `git -C dir status` 의 하위 명령은 dir 이 아니다.
GIT_VALUED = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix"}
DEST_LAST = {"cp", "install", "rsync", "ln"}
ALL_ARGS = {"touch", "truncate", "tee", "rm", "unlink", "mkdir", "sponge", "mv", "rmdir", "shred"}
IN_PLACE = {"sed", "perl", "awk", "gawk"}
REDIRECT = re.compile(r"(?<![0-9&<>-])>>?\|?\s*([^\s;&|<>()]+)|(?<![<>])[0-9]>>?\s*([^\s;&|<>()&]+)")


_FEEDS_POSIX_SHELL = re.compile(r"(?:^|[\s|(])(?:\S*/)?(?:bash|sh|zsh|dash|ash)\b[^|;&]*<<|\|\s*(?:\S*/)?(?:bash|sh|zsh)\b")


def _quote_state(line: str, q):
    """줄 끝의 따옴표 상태와, 따옴표 밖에서 시작하는 첫 heredoc 표식. 여러 줄 문자열은 다음 줄로 상태를 넘긴다.
    q 는 문맥 스택(문자열이 아니라 튜플) — 큰따옴표 안의 `$(` 는 새 명령 문맥이라 그 안의 `<<'EOF'` 는 heredoc 이다
    (커밋·PR 본문의 정형 `"$(cat <<'EOF' … EOF)"`)."""
    st = list(q or ())
    i, n, esc = 0, len(line), -2
    found = None
    while i < n:
        c = line[i]
        cur = st[-1] if st else None
        if cur == "'":
            if c == "'":
                st.pop()
            i += 1
            continue
        if cur == '"':
            if c == "\\":
                i += 2
                continue
            if line.startswith("$(", i):
                st.append("(")                           # 따옴표 안의 명령 치환 — 따옴표 밖처럼 읽는다
                i += 2
                continue
            if c == '"':
                st.pop()
            i += 1
            continue
        if c == "\\":
            esc = i + 1                                  # 이스케이프된 글자(`\ `)는 공백이 아니다 — 뒤 `#` 는 주석이 아니다
            i += 2
            continue
        if c == ")" and cur == "(":
            st.pop()
            i += 1
            continue
        if c == "#" and (i == 0 or (line[i - 1] in " \t;&|(" and esc != i - 1)):
            break                                        # 주석 — 그 안의 따옴표·`<<` 는 셸이 읽지 않는다
        if found is None and line.startswith("<<", i):
            m = _HEREDOC.match(line, i)
            if m:
                found = m
                i = m.end()
                continue
        if line.startswith("$(", i):
            st.append("(")
            i += 2
            continue
        if c in "'\"":
            st.append(c)
        i += 1
    return tuple(st), found


def strip_heredoc_bodies(cmd: str, shell_only: bool = False) -> str:
    """데이터 heredoc 본문을 지운다(헤더 줄은 남겨 `cat > f <<EOF` 의 리다이렉트는 그대로 본다).
    shell_only=True 면 셸이 읽는 본문만 남긴다 — python·node 본문은 실행되지만 셸 명령은 아니다.
    따옴표 안의 `<<EOF`(`git commit -m "a <<EOF"`)는 heredoc 이 아니다 — 뒤의 줄을 본문으로 삼켜 명령을 숨겼다."""
    lines, out, i, q = cmd.split("\n"), [], 0, ()
    while i < len(lines):
        line = lines[i]
        out.append(line)
        q, m = _quote_state(line, q)
        i += 1
        if not m:                                          # 표식은 따옴표 밖(또는 따옴표 안 `$(…)`)에서만 찾았다
            continue
        tag, body = heredoc_tag(m), []
        dash = m.group(0).startswith("<<-")
        # 종료 줄은 bash 처럼 정확히 표식만(`<<-` 면 앞 탭 허용) — `EOF ` 를 끝으로 읽으면 뒤 본문이 명령이 되고
        # 그 안의 `'` 가 다음 줄들을 문자열로 덮었다
        while i < len(lines) and (lines[i].lstrip("\t") if dash else lines[i]) != tag:
            body.append(lines[i])
            i += 1
        if (_FEEDS_POSIX_SHELL if shell_only else _FEEDS_SHELL).search(line):
            out += body                                    # 셸·인터프리터가 읽는 본문은 실행된다
        if i < len(lines):
            i += 1                                         # 종료 줄(`EOF`)은 명령이 아니다 — 정정 게이트가 `pyeof` 로 셌다
    return "\n".join(out)


def neutralize_quotes(cmd: str) -> str:
    """따옴표 안의 셸 메타문자를 공백으로 — 문자열 속 `a -> b`·`x | y` 는 리다이렉트·파이프가 아니다.
    `bash -c '…'`·`eval '…'` 의 인자는 실행되므로 그대로 펼친다."""
    out, i, n = [], 0, len(cmd)
    esc = -2
    while i < n:
        ch = cmd[i]
        if ch == "\\" and i + 1 < n:
            out.append(cmd[i + 1])
            esc = i + 1
            i += 2
            continue
        if ch == "#" and (i == 0 or (cmd[i - 1] in " \t\n;&|(" and esc != i - 1)):
            while i < n and cmd[i] != "\n":              # 주석은 실행되지 않는다 — 안의 `'`(`# it's`)를 따옴표로 읽으면
                i += 1                                   # 뒤의 줄 전체가 문자열이 되어 사라졌다
            continue
        if ch in "'\"":
            j = i + 1                                    # 큰따옴표 안의 `\"` 는 닫는 따옴표가 아니다 — 뒤가 하위 명령으로 갈라졌다
            while j < n and cmd[j] != ch:
                j += 2 if ch == '"' and cmd[j] == "\\" else 1
            j = min(j, n)
            inner = cmd[i + 1:j]
            before = "".join(out)
            if re.search(SHELLS + r"(?:\s+-\w+)*\s*$", before):
                out.append(inner)
            else:
                # 데이터 문자열 속 `#` 도 메타문자가 아니다 — `--body "fix #12" && rm -rf src` 의 `#` 를 주석으로 읽어
                # 뒤의 rm 을 지웠다. 줄바꿈은 남긴다 — 따옴표 짝을 잘못 잡으면(`"$(echo '"')"`) 다음 줄 명령이 숨는다
                out.append(re.sub(r"[<>|;&#]", " ", inner))
            i = j + 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def prep(cmd: str, shell_only: bool = False) -> str:
    text = neutralize_quotes(strip_heredoc_bodies(cmd, shell_only))
    # 주석은 neutralize_quotes 가 따옴표·이스케이프를 알고 지운다(`a\ #b` 의 `#` 는 낱말 안 — 주석이 아니다)
    # fd 복제(`2>&1`·`>&-`)와 `&>`·`|&` 의 `&` 는 명령 구분자가 아니다 — `1` 이 명령으로 세어졌다
    text = re.sub(r"\d*[<>]&(?:\d+|-)", " ", text)
    text = re.sub(r"&>>?", ">", text).replace("|&", "|")
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
# eval 외의 `source`·`.` 는 명령 위치에서만 센다(`_sources`) — 인자 `.`(`git add .`)는 코드를 읽지 않는다.
_BAIL = re.compile(
    r"(?<![\w.-])(?:eval|read|mapfile|readarray|getopts|declare|typeset|local|readonly|let|coproc|trap|IFS|alias)(?![\w-])"
    r"|(?<![\w.-])printf(?:\s+-\S+)*\s+-v\b|\(\(|\$\{!|\$\{[A-Za-z_]\w*:?[=?]")
_SEQ = (None, ";", "\n", "&&")
# 명령 조각 경계. `{`·`}` 는 낱말일 때만(`${S}` 는 쪼개지 않는다). `>|` 는 리다이렉트지 파이프가 아니다.
_CMD_PIECES = re.compile(r"[;&|\n()`]|\$\(|(?:^|(?<=\s))[{}](?=\s|$)")
# 명령 이름 찾기용 — `$(`·백틱에서 자르면 치환 머리(`$(printf '\\56') f`)가 사라진다. `(` 에서만 잘라 `$` 를 남긴다
_HEAD_PIECES = re.compile(r"[;&|\n()]|(?:^|(?<=\s))[{}](?=\s|$)")
# `.` 가 인자일 수 있는 조각의 첫 낱말 = 평범한 프로그램 이름. 아래는 뒤 낱말을 명령으로 넘길 수 있어 제외한다.
_PLAIN_HEAD = re.compile(r"[A-Za-z_][\w.+-]*")
_PASS_THROUGH = SHELL_KEYWORDS | {"command", "builtin", "time", "exec", "eval", "source", "coproc", "function",
                                  "for", "case", "select", "in", "until", "while", "nohup", "env", "sudo", "doas",
                                  "nice", "timeout", "stdbuf", "xargs"}


def _is_source_word(w: str) -> bool:
    """bash 가 이 낱말을 `.`/`source` 로 읽을 수 있는가 — 따옴표·백슬래시를 벗기고 중괄호를 펼친다(`{source,}`)."""
    w = re.sub(r"[\"'\\]", "", w)
    if w in (".", "source"):
        return True
    if "{" in w:
        exp = brace_expand(w)
        return exp is None or any(x in (".", "source") for x in exp)   # 다 펼치지 못하면 의심한다
    return False


def _sources(cmd: str) -> bool:
    """`.`·`source` 가 현재 셸에서 코드를 읽을 수 있는가. 명령 위치를 하나씩 열거하는 방식은 두 번 뚫렸다
    (리다이렉트 `2>&1 .`·옵션 `time -p .`·`command -- .` — 0.40.0 독립 검증). 그래서 뒤집는다: 낱말 `.`/`source` 가
    있으면 의심하고, **조각의 첫 낱말이 평범한 프로그램 이름일 때만**(`git add .`·`ls .`) 인자로 인정한다.
    따옴표·백슬래시는 bash 처럼 벗기고 중괄호는 펼친다(`"." f`·`\\source f`·`{source,} f`). 값이 `.`·`source` 인
    대입(`X=source; $X f`)도 의심한다. 명령 이름이 변수·치환이면(`"$@"`·`$1`·`$X` — 값을 `printf '\\56'` 처럼 `.` 낱말
    없이 만들 수 있다) 무조건 펼치지 않는다(독립 검증 2·3회차)."""
    if re.search(r"(?:^|[\s;&|(])[A-Za-z_]\w*=(['\"]?)(?:\.|source)\1(?=[\s;&|)]|$)", cmd):
        return True
    pieces = _CMD_PIECES.split(cmd.replace(">|", "> "))
    for piece in _HEAD_PIECES.split(cmd.replace(">|", "> ")):
        ws, i = piece.split(), 0
        while i < len(ws):                               # 머리 앞의 문법어·대입·옵션·리다이렉트를 건너뛴 명령 낱말
            w = re.sub(r"[\"'\\]", "", ws[i])
            if w in _PASS_THROUGH or ASSIGN_WORD.match(w) or w.startswith("-") or re.match(r"\d*[<>&]", w):
                i += 2 if re.fullmatch(r"\d*(?:[<>]{1,2}|&>>?|>\|)", w) else 1
                continue
            if ws[i] not in ("[", "[[") and re.search(r"[$`*?\[]", ws[i]):
                return True                              # 변수·치환·글롭이 명령 이름(`sourc?` 는 파일 `source` 로 펼쳐진다)
            break
    words = [w for w in re.split(r"[\s;&|()<>`]+", cmd) if w]
    if any(_is_source_word(w) for w in words) and re.search(
            r"\b[A-Za-z_][\w.-]*\s*\(\s*\)|(?:^|[\s;&|(])function\s|(?:^|[\s;&|(])set\s+(?:--?\s|[^-\s])", cmd):
        return True                                      # 간접 실행: 함수·위치 매개변수(`set -- . x`·`set . x`)
    for piece in pieces:
        ws = piece.split()
        if not any(_is_source_word(w) for w in ws):
            continue
        head = re.sub(r"[\"'\\]", "", ws[0])
        if not _PLAIN_HEAD.fullmatch(head) or head in _PASS_THROUGH:
            return True
    return False


def _strip_comments(text: str) -> str:
    """따옴표 밖, 낱말 첫머리의 `#` 부터 줄 끝까지 지운다 — 주석 속 `'`(`# it's`)가 뒤 줄을 따옴표로 덮었다."""
    out, q = [], None
    for line in text.split("\n"):
        i, n, cut, esc = 0, len(line), len(line), -2
        while i < n:
            c = line[i]
            if q:
                if c == "\\" and q == '"':
                    i += 2
                    continue
                if c == q:
                    q = None
            elif c == "\\":
                esc = i + 1
                i += 2
                continue
            elif c in "'\"":
                q = c
            elif c == "#" and (i == 0 or (line[i - 1] in " \t;&|(" and esc != i - 1)):
                cut = i
                break
            i += 1
        out.append(line[:cut])
    return "\n".join(out)


def _mask_data_quotes(text: str) -> str:
    """공백·메타문자가 든 따옴표 문자열을 `_` 로 덮는다 — 산문 속 `(…). 다음` 이 명령 위치로 읽혔다.
    `"."`·`"source"` 처럼 낱말 하나뿐인 따옴표는 남긴다(그것은 명령 이름이 될 수 있다)."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c == "\\":
            out.append(text[i:i + 2])
            i += 2
            continue
        if c in "'\"":
            j = i + 1
            while j < n and text[j] != c:
                j += 2 if c == '"' and text[j] == "\\" else 1
            inner = text[i + 1:j]
            out.append(c + ("_" * len(inner) if re.search(r"[\s(){}`;&|<>#]", inner) else inner) + c)
            i = j + 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def bails(cmd: str) -> bool:
    """이 명령 안에 변수 값을 바꾸거나 해석을 바꿀 수단이 있는가."""
    # `.`·`source` 는 셸이 읽는 텍스트에서만 찾는다 — 데이터 heredoc·따옴표 속 산문(`…). 기간`)은 명령이 아니다
    if _BAIL.search(cmd):
        return True
    return _sources(_mask_data_quotes(_strip_comments(strip_heredoc_bodies(cmd, shell_only=True))))


def leading_assignments(cmd: str) -> dict:
    """값을 **확실히** 아는 변수만 돌려준다 — 그 밖은 모르는 값이고, 가드는 모르는 값을 보수적으로 다룬다.

    Claude Code 의 Bash 는 호출마다 셸 상태가 새로 시작되므로, 확실한 값의 출처는 명령 맨 앞의 대입뿐이다.
    인정: 명령 **맨 앞**에서 `&&`·`;`·줄바꿈으로만 이어진 무조건 리터럴 대입(`S=/a && T=$S/b && …`).
    거부: 값을 바꿀 수단(`_BAIL`)이 명령 어디에든 있거나, 그 이름이 `$NAME`·`${NAME}` 말고 다른 꼴로 다시
    나오면(재대입·`for S`·`unset S`) — 실제 셸의 값과 달라질 수 있다(v0.35.1 우회, 2026-10-09).
    환경변수는 쓰지 않는다 — 훅 프로세스의 환경은 Bash 의 환경과 같다는 보장이 없다.
    이 근사가 맞는지는 tests/test_shell_differential.py 가 실제 bash 로 대조한다."""
    if bails(cmd):
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
    if bails(cmd):
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
        # heredoc 종료어 낱말은 펼치지 않는다 — bash 는 `<<E$X`·`<<"$X"` 의 종료어를 글자 그대로 쓴다. 펼치면 종료어가
        # 바뀌어 뒤 명령이 본문으로 숨었다(0.40.1 부터, 0.41.0 자체 점검)
        tags = [mm.group(0) for mm in _HEREDOC.finditer(line)]
        masked = _HEREDOC.sub(lambda mm: "\x00HD%d\x00" % tags.index(mm.group(0)), line) if tags else line
        expanded = _expand_line(masked, known)
        for k, t in enumerate(tags):
            expanded = expanded.replace("\x00HD%d\x00" % k, t)
        out.append(expanded)
        i += 1
        m = _HEREDOC.search(line)
        if not m:
            continue
        while i < len(lines) and lines[i].strip() != heredoc_tag(m):  # 본문은 그대로(인용 heredoc 은 펼치지 않는다)
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
            if i + 1 < n and line[i + 1] in "$!#?@*-0123456789":
                out.append(line[i:i + 2])                    # `$$S` 는 PID 뒤 글자 S 다 — `$S` 가 아니다
                i += 2
                continue
            m = _VAR_REF.match(line, i)
            if m and (m.group(1) or m.group(2)) in known:
                out.append(known[m.group(1) or m.group(2)])
                i = m.end()
                continue
        out.append(ch)
        i += 1
    return "".join(out)


_CONT = re.compile(r"(?<!\\)((?:\\\\)*)\\\n")


def join_continuations(cmd: str) -> str:
    """`\\`+줄바꿈을 **지운다** — bash 는 토큰을 나누기 전에 이 둘을 없애 앞뒤를 붙인다(`r\\⏎m -rf /` 는 `rm`).
    공백으로 바꾸면 명령 이름·대상이 둘로 갈라져 파서에게서 숨는다(0.35.3 커밋 보안 리뷰 후 문자 퍼즈가 찾음,
    0.35 이전부터 있던 구멍). 작은따옴표 안에서는 bash 도 지우지 않지만, 그 문자열이 `bash -c` 로 다시
    해석되면 지워지므로 어디서든 지운다 — 판정은 보수적이 될 뿐이다. 짝수 개 `\\` 뒤 줄바꿈은 진짜 줄바꿈이다."""
    return _CONT.sub(r"\1", cmd)


_ANSI_SIMPLE = {"a": "\a", "b": "\b", "e": "\x1b", "E": "\x1b", "f": "\f", "n": "\n", "r": "\r", "t": "\t",
                "v": "\v", "\\": "\\", "'": "'", '"': '"', "?": "?"}


def _ansi_c(body: str) -> str:
    """`$'…'` 본문을 bash 처럼 푼다 — `\\x2f`·`\\057`·`\\u002f` 도 `/` 다."""
    out, i, n = [], 0, len(body)
    while i < n:
        c = body[i]
        if c != "\\" or i + 1 >= n:
            out.append(c)
            i += 1
            continue
        e = body[i + 1]
        if e in _ANSI_SIMPLE:
            out.append(_ANSI_SIMPLE[e])
            i += 2
        elif e in "01234567":
            m = re.match(r"[0-7]{1,3}", body[i + 1:])
            out.append(chr(int(m.group(0), 8) & 0xFF))
            i += 1 + len(m.group(0))
        elif e in "xuU":
            m = re.match(r"[0-9a-fA-F]{1,%d}" % {"x": 2, "u": 4, "U": 8}[e], body[i + 2:])
            if m:
                out.append(chr(min(int(m.group(0), 16), 0x10FFFF)))
                i += 2 + len(m.group(0))
            else:
                out.append("\\" + e)
                i += 2
        elif e == "c" and i + 2 < n:
            out.append(chr(ord(body[i + 2]) & 0x1F))
            i += 3
        else:
            out.append("\\" + e)
            i += 2
    return "".join(out)


def decode_ansi_quotes(cmd: str) -> str:
    """따옴표 밖의 `$'…'` 를 푼 값의 작은따옴표 문자열로, `$"…"` 를 `"…"` 로 바꾼다. 파서는 `$'/'` 를 변수
    `$/` 처럼 읽어 대상 `/` 를 놓쳤다(0.35 이전부터 — 문자 퍼즈가 찾음). 확인용 사본에만 적용된다."""
    out, i, n, q = [], 0, len(cmd), None
    while i < n:
        c = cmd[i]
        if q:
            out.append(c)
            if c == "\\" and q == '"' and i + 1 < n:
                out.append(cmd[i + 1])
                i += 2
                continue
            if c == q:
                q = None
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            out.append(cmd[i:i + 2])
            i += 2
            continue
        if c == "$" and i + 1 < n and cmd[i + 1] == "'":
            j = i + 2
            while j < n and cmd[j] != "'":
                j += 2 if cmd[j] == "\\" else 1
            val = _ansi_c(cmd[i + 2:min(j, n)])
            out.append("'" + val.replace("'", "'\\''") + "'")
            i = j + 1
            continue
        if c == "$" and i + 1 < n and cmd[i + 1] == '"':
            i += 1                                           # `$"…"` 은 번역용 — 뜻은 `"…"` 와 같다
            continue
        if c in "'\"":
            q = c
        out.append(c)
        i += 1
    return "".join(out)


_SIMPLE_PARAM = re.compile(r"[A-Za-z_]\w*(?::?[-=?+][\w./:@%+=~-]*)?")
UNKNOWN_PARAM = "${__harness_unknown__}"


def collapse_params(cmd: str, keep=frozenset()) -> str:
    """단순하지 않은 `${…}`(이름·`이름:-리터럴` 이 아닌 것)를 '모르는 변수' 하나로 접는다. bash 는 `${X: \\x2f,}`
    처럼 공백·연산자가 든 매개변수 확장도 한 단어로 다루는데, 토크나이저는 공백에서 쪼개 대상을 놓쳤다
    (문자 퍼즈가 찾음). 모르는 변수는 빈 값일 수 있는 것으로 판정된다 — 보수적."""
    out, i, n, q = [], 0, len(cmd), None
    while i < n:
        c = cmd[i]
        if q == "'":
            out.append(c)
            q = None if c == "'" else q
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            out.append(cmd[i:i + 2])
            i += 2
            continue
        if c == "'" and q is None:
            q = "'"
        elif c == '"':
            q = None if q == '"' else '"'
        elif cmd.startswith("${", i):
            depth, j = 0, i + 1
            while j < n:
                if cmd[j] == "\\":
                    j += 2
                    continue
                depth += cmd[j] == "{"
                depth -= cmd[j] == "}"
                if depth == 0:
                    break
                j += 1
            body = cmd[i + 2:j]
            if j >= n or not _SIMPLE_PARAM.fullmatch(body):
                # 값을 바꾸는 꼴(`${S:=…}`·`${S=…}`)과 간접 참조(`${!r}`)는 접어도 `_BAIL` 이 보도록 표식을 남긴다
                m = re.match(r"(!?)([A-Za-z_]\w*)(:?=)?", body)
                if m and m.group(1):
                    out.append("${!__harness_unknown__}")
                elif m and m.group(3):
                    out.append("${" + m.group(2) + ":=__harness_unknown__}")
                elif m and m.group(2) in keep and m.end() < len(body):
                    # keep = 이 명령 안에서 값을 받는 변수(bound_names). 그 변수의 변형(`${S/x}`·`${S#x}`·`${S:1}`)은 값의 아무 조각이나
                    # 될 수 있다 — `S=x/..; ${S/x}` 는 `/..`. 루트에서 시작하는 모르는 값으로 접은 후보를 파괴 가드가
                    # 따로 본다. 모르는 변수 하나로만 접으면 `S=/; rm -rf ${S/x}usr` 가 `usr` 로 읽혔고(0.40.0 퍼즈),
                    # `${S}` + 꼬리로만 접으면 접두를 깎는 `${S%/*}` 가 깊은 경로로 읽혔다(독립 검증 4회차 R4-1)
                    out.append("/" + UNKNOWN_PARAM)
                else:
                    out.append(UNKNOWN_PARAM)
                i = j + 1
                continue
            out.append(cmd[i:j + 1])
            i = j + 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


_BINDS = re.compile(r"(?<![\w$])([A-Za-z_]\w*)(?:\[[^\]]*\])?\+?="
                    r"|\b(?:for|select|read|mapfile|readarray|declare|typeset|local|export|readonly|printf\s+-v|getopts\s+\S+)"
                    r"((?:\s+-\S+)*(?:\s+[A-Za-z_]\w*)+)")


def bound_names(cmd: str) -> frozenset:
    """이 명령 안에서 값을 받는 변수 이름 — 대입·`+=`·`for`·`read`·`printf -v`·`declare` 등(값이 확정됐는지는 묻지 않는다)."""
    out = set()
    for m in _BINDS.finditer(cmd):
        out.add(m.group(1)) if m.group(1) else out.update(w for w in m.group(2).split() if not w.startswith("-"))
    return frozenset(out)


def canonical(cmd: str, keep=frozenset()) -> str:
    """판정용 사본 — bash 가 토큰을 나누기 전에 하는 변환을 먼저 한다: 이어쓰기 제거, `$'…'` 풀기, 복잡한 `${…}` 접기."""
    return collapse_params(decode_ansi_quotes(join_continuations(cmd)), keep)


# 위치를 모른다(`cd "$X"`·`cd -`·`popd`). None 이 아니라 문자열이다 — os.path.* 에 들어가도 TypeError 로 훅이 죽지 않는다.
# 소비자마다 방향이 다르다: 파괴 가드는 막고, 스냅샷은 세션 프로젝트를 찍고, plan-first·위임은 상대 경로를 건너뛴다.
UNKNOWN = "\0unknown-cwd"


def _cd(seg: str, here: str, stack: list):
    """`cd`·`pushd`·`popd` 면 새 위치, 아니면 None. stack = 이 명령 안에서 pushd 로 쌓은 위치(popd 가 돌아갈 곳)."""
    toks = words(seg)
    if toks and toks[0] == "builtin":
        toks = toks[1:]
    if not toks or toks[0] not in ("cd", "pushd", "popd"):
        return None
    args = [t for t in toks[1:] if not re.fullmatch(r"-[LPe@]+", t)]
    if toks[0] == "popd":
        return stack.pop() if stack and not args else UNKNOWN   # 이 명령 밖에서 쌓인 스택은 모른다
    if toks[0] == "pushd" and (not args or re.fullmatch(r"[+-]\d*", args[0])):
        stack.clear()
        return UNKNOWN                                   # 스택 회전 — 알 수 없다
    if args and here != UNKNOWN:                         # `$PWD` 는 지금 위치다 — `cd "$PWD"` 로 판정을 끄지 못하게
        args[0] = re.sub(r"\$\{PWD\}|\$PWD(?!\w)", lambda _: here, args[0])
    if len(args) > 1 or (args and (args[0] == "-" or re.search(r"[$`*?\[]", args[0]))):
        new = UNKNOWN
    else:
        d = os.path.expanduser(args[0] if args else "~")
        new = os.path.normpath(d) if os.path.isabs(d) else (
            UNKNOWN if here == UNKNOWN else os.path.normpath(os.path.join(here, d)))
    if toks[0] == "pushd":
        stack.append(here)
    return new


def segments(cmd: str, cwd: str, shell_only: bool = False) -> list:
    """(하위 명령, 그 시점의 cwd 또는 UNKNOWN) 목록. `cd X` 는 뒤 하위 명령의 상대 경로 기준을 바꾼다.
    확정된 변수는 펼친 뒤에 돌려준다 — `S=/tmp/x && cp a $S/b` 의 대상은 `$S/b`(프로젝트 안 상대 경로)가 아니다.
    shell_only=True 면 인터프리터(python·node…) heredoc 본문을 명령으로 읽지 않는다 — 실행은 되지만 셸 명령이 아니다."""
    out, here, stack = [], cwd, []
    cmd = canonical(cmd)
    cmd = expand_command(cmd, leading_assignments(cmd))
    for seg in re.split(r"[;&|\n]+", prep(cmd, shell_only).replace("(", " ").replace(")", " ")):
        seg = seg.strip()
        if not seg:
            continue
        new = _cd(seg, here, stack)
        if new is not None:
            here = new
            continue
        out.append((seg, here))
    return out


def words(seg: str) -> list:
    """하위 명령의 낱말(리다이렉트 제외). 따옴표는 prep 이 이미 벗겼다."""
    return [t for t in re.split(r"\s+", REDIRECT.sub(" ", seg).strip()) if t]


def strip_launchers(toks: list, stop=frozenset()) -> list:
    """대입·셸 문법어·런처를 벗겨 실제 프로그램을 맨 앞에(이름은 basename) 둔다. `stop` 의 런처는 벗기지 않는다
    (정정 게이트는 `xargs` 를 신원 없는 실행기로 본다)."""
    for _ in range(16):                                  # 유한: `sudo env nohup git …`
        i = 0
        while i < len(toks) and (ASSIGN_WORD.match(toks[i]) or toks[i] in SHELL_KEYWORDS):
            i += 1
        toks = toks[i:]
        if not toks:
            return toks
        head = toks[0].rsplit("/", 1)[-1]
        valued = LAUNCHERS.get(head, LEADING_POSITIONAL.get(head))
        if valued is None or head in stop:
            return [head] + toks[1:]
        i = 1
        while i < len(toks):
            t = toks[i]
            if t == "--":
                i += 1
                break
            if head == "env" and t in ("-S", "--split-string") and i + 1 >= len(toks):
                return []                                # 값 없는 `env -S` — 실행할 명령이 없다(IndexError 로 훅이 죽었다)
            if head == "env" and (t in ("-S", "--split-string") or t.startswith(("-S", "--split-string="))):
                # `env -S 'cp x' f` 는 값을 낱말로 쪼개 **명령으로** 실행한다 — 값은 건너뛸 옵션 값이 아니다
                val = toks[i + 1] if t in ("-S", "--split-string") else t.split("=", 1)[1] if "=" in t else t[2:]
                rest = toks[i + 2:] if t in ("-S", "--split-string") else toks[i + 1:]
                toks = ["env"] + val.split() + rest
                i = 1
                continue
            if t in valued:
                i += 2
            elif t.startswith("-") or (head == "env" and ASSIGN_WORD.match(t)):
                i += 1
            else:
                break
        if head in LEADING_POSITIONAL:
            i += 1                                       # DURATION
        toks = toks[i:]
    return toks


def tokens(seg: str) -> list:
    """하위 명령을 실제 프로그램부터 시작하는 낱말 목록으로."""
    return strip_launchers(words(seg))


def git_sub(toks: list):
    """(git 하위 명령의 위치, `-C` 로 준 디렉터리들). toks[0] 은 git. 하위 명령이 없으면 위치 -1."""
    i, dirs = 1, []
    while i < len(toks):
        t = toks[i]
        if t in GIT_VALUED:
            if t == "-C" and i + 1 < len(toks):
                dirs.append(toks[i + 1])
            i += 2
        elif t.startswith("-"):
            i += 1
        else:
            return i, dirs
    return -1, dirs


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
    toks = tokens(seg)
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


# ── 읽기 전용 판정(두 층) — 정정 게이트와 스냅샷이 같은 판정을 쓴다(0.41.0 S4).
# 이전에는 정정 게이트가 호출 원문 전체에 허용 문자 검사를 걸어 `$S`·`>>`·heredoc 이 하나만 있어도 같은 호출의
# `| head` 까지 읽기 전용 자격을 잃었고(실사용 오탐), 스냅샷은 머리 이름만 보는 느슨한 목록(env·open·jq·git fetch)을
# 따로 썼다. 두 층으로 나눈다:
#  1) 호출 층 call_admissible — 같은 호출 안에서 **다른** 하위 명령의 뜻을 바꾸는 구조(재바인딩·묶음 리다이렉트·
#     함수 정의·빈 인자)가 없는가. 있으면 어느 하위 명령도 읽기 전용이 아니다.
#  2) 하위 명령 층 segment_read_only — segments() 가 낸 하위 명령(따옴표 벗김·확정 변수 펼침 뒤)이 허용 문자와
#     허용 형태 안에 있는가. 리다이렉트·치환·변수·글롭은 그 하위 명령만 자격을 잃는다.
# 허용 형태의 근거(독립 검증 1~6차가 깬 표기)는 tests/test_hooks.py `test_writing_commands_in_a_blocked_chain_stay_tracked`.
READ_ONLY_HEADS = {"head", "tail", "grep", "wc", "cut", "tr", "ls", "stat", "du", "df", "pwd", "which", "type",
                   "diff", "cmp", "basename", "dirname", "realpath", "readlink", "true", "false", "test",
                   "sleep", "column", "nl", "fold", "rev", "od", "cat", "echo", "printf"}
GIT_READ_ONLY = {"status", "log", "diff", "show", "rev-parse", "ls-files", "ls-tree", "blame", "describe",
                 "shortlog", "merge-base", "name-rev", "version", "for-each-ref", "grep"}
SORT_SAFE, UNIQ_SAFE = set("bdfghinMrRsuV"), set("cdiuDz")
GIT_BRANCH_SAFE = {"-a", "-r", "-v", "-vv", "-l", "--list", "--show-current", "--all", "--remotes", "--verbose"}
GIT_CONFIG_GET = {"--get", "--get-all", "--get-regexp", "--list", "-l"}
HARMLESS_REDIRECT = re.compile(r"(?<=[ \t])(?:[12]?>[ \t]*/dev/null|2>&1)(?=[ \t\n;|&]|$)")
# 입력 리다이렉트·heredoc·here-string 은 읽기다. `<>`(읽고 쓰기)·`<(`(프로세스 치환)·`<&` 는 아니다.
INPUT_REDIRECT = re.compile(r"(?:^|(?<=[ \t]))\d*<(?:<-?|<<)?(?![<>(&])[ \t]*[^\s<>()]+")
FIND_ACTIONS_WRITE = ("-delete", "-exec", "-ok", "-fprint", "-fls")       # -execdir·-okdir·-fprint0 포함(접두)
SED_PRINT_RANGE = re.compile(r"(?:\d+|\$)(?:,(?:\d+|\$))?p")
# 하위 명령 텍스트의 허용 문자. 유니코드 \w·\s 에는 셸이 공백으로 보지 않는 문자(\v·NBSP)가 있어 ASCII 로 고정한다.
SEGMENT_SPECIAL = re.compile(r"[^A-Za-z0-9_ \t./:@%+,=-]")
# 문장 첫 낱말이면 뒤 하위 명령의 이름·출력·변수를 바꿀 수 있는 내장 명령(bash·zsh).
REBINDERS = {"alias", "unalias", "hash", "export", "set", "unset", "source", ".", "eval", "builtin", "enable",
             "disable", "typeset", "declare", "local", "readonly", "read", "mapfile", "readarray", "getopts",
             "printf", "let", "trap", "exec", "setopt", "unsetopt", "emulate", "autoload", "functions", "zmodload",
             "vared", "shopt", "function", "coproc", "command_not_found_handle"}
# 이름만으로 명령 해석·낱말 분할을 바꾸는 변수(zsh 는 소문자 배열이 PATH 등과 묶여 있다).
UNSAFE_NAMES = {"PATH", "IFS", "ENV", "HOME", "PS4", "CDPATH", "FPATH", "path", "fpath", "cdpath", "manpath",
                "module_path", "mailpath", "psvar"}
_ANY_ASSIGN = re.compile(r"([A-Za-z_]\w*)(?:\[[^\]]*\])?\+?=")   # `PATH+=:x`·`path[1]=x` 도 대입이다


def _safe_name(name: str) -> bool:
    """문장 단위 대입을 허용할 이름 — 짧거나 소문자 전용(셸 스크립트의 지역 변수 관례)이고 해석을 바꾸는 이름이 아닌 것.
    길고 대문자인 이름(GIT_EXTERNAL_DIFF·LD_PRELOAD·BASH_ENV…)은 이미 export 된 환경 변수일 수 있어 인정하지 않는다."""
    return (len(name) <= 4 or name.islower()) and name not in UNSAFE_NAMES


def _statements(masked: str) -> list:
    """최상위 문장들 — `$(…)`·`(…)` 안의 구분자에서는 자르지 않는다(치환·서브셸 안의 재바인딩은 바깥에 닿지 않는다)."""
    out, cur, depth, i, n = [], [], 0, 0, len(masked)
    while i < n:
        c = masked[i]
        if c == "(":
            depth += 1
        elif c == ")" and depth:
            depth -= 1
        elif depth == 0 and c in ";&|\n":
            out.append("".join(cur))
            cur = []
            i += 2 if masked[i:i + 2] in ("&&", "||") else 1
            continue
        if depth == 0:
            cur.append(c)
        i += 1
    out.append("".join(cur))
    return out


# 호출 층의 판정 단계 — FULL = 하위 명령 층을 그대로, ARGS_UNRELIABLE = 빈 인자(`''`)가 있어 하위 명령 텍스트의 인자 수를
# 믿을 수 없다(인자를 보지 않는 명령만 읽기 전용), NONE = 어느 하위 명령도 읽기 전용이 아니다.
NONE, ARGS_UNRELIABLE, FULL = 0, 1, 2


def call_level(cmd: str) -> int:
    """호출 층 — 이 호출 안에 다른 하위 명령의 뜻을 바꾸는 구조가 없는가."""
    text = join_continuations(decode_ansi_quotes(cmd))
    body = strip_heredoc_bodies(text, shell_only=True)
    masked = _mask_data_quotes(_strip_comments(body))
    if re.search(r"(?<!\$)\(", masked):                  # 서브셸·함수 정의·zsh `=(…)`·글롭 한정자·산술 — 리다이렉트가 묶음에 붙는다
        return NONE
    if re.search(r"(?:^|[\s;&|])[{}](?=[\s;&|]|$)", masked):   # `{ head; } > out`
        return NONE
    if re.search(r"\$\{[^}]*[=!]", masked) or _sources(masked):    # 펼치며 대입·간접 참조, `.`/`source`
        return NONE
    # 재바인딩 낱말은 문장 첫 위치만 보지 않는다 — 앞에 대입·리다이렉트·수식어(`x=1 export`·`2>&1 export`·`command export`·
    # `time -p export`·`case x in x) export`·zsh `noglob`/`nocorrect`/`repeat 1`/`-`)를 붙이면 첫 낱말이 바뀌었다
    # (0.41.0 독립 검증 1회차). 최상위 문장의 **모든** 낱말을 따옴표·백슬래시를 떼고 중괄호를 펼쳐 본다 — 인자 자리의
    # `grep export f` 도 막히지만(보수) 놓치는 것보다 낫다.
    for stmt in _statements(masked):
        raw = [re.sub(r"[\"'\\]", "", w) for w in re.split(r"[\s()]+", stmt) if w]
        ws = []
        for w in raw:
            if "{" in w:
                exp = brace_expand(w)
                if exp is None:
                    return NONE                          # 다 펼치지 못하면 의심한다
                ws += exp
            else:
                ws.append(w)
        for k, w in enumerate(ws):
            nxt = ws[k + 1] if k + 1 < len(ws) else ""
            if w == "printf":                            # 출력만 하는 printf 는 흔하다 — `-v NAME`(값으로 만든 `$O` 포함)만 변수에 쓴다
                if nxt.startswith("-") or re.search(r"[$`]", nxt):
                    return NONE
                continue
            if w == "read":                              # `while read f` — 받는 이름이 모두 안전하면 바깥 해석을 바꾸지 않는다
                names = [x for x in ws[k + 1:] if not x.startswith("-")]
                if any(o.startswith("-") and set(o[1:]) & set("apdutnN") for o in ws[k + 1:]) or \
                        not names or not all(re.fullmatch(r"[A-Za-z_]\w*", x) and _safe_name(x) for x in names):
                    return NONE
                continue
            if w in REBINDERS:
                return NONE
            if w in ("for", "select") and not _safe_name(nxt):
                return NONE
            am = _ANY_ASSIGN.match(w)
            if am and not _safe_name(am.group(1)):
                return NONE                              # 해석을 바꾸는 이름의 대입(문장 단위든 앞에 붙인 것이든, `+=`·`[i]=` 포함)
    if re.search(r"''|\"\"", body):                     # 빈 인자 — segments() 가 지워 인자 수가 줄어든다(`git config K ''`)
        return ARGS_UNRELIABLE
    return FULL


def call_admissible(cmd: str) -> bool:
    return call_level(cmd) != NONE


def segment_read_only(seg: str, level: int = FULL) -> bool:
    """하위 명령 층 — segments() 가 낸 하위 명령이 확실히 읽기만 하는가(허용 문자 + 허용 형태).
    level = 같은 호출의 call_level. ARGS_UNRELIABLE 이면 인자를 보지 않는 명령만 인정한다."""
    if level == NONE:
        return False
    text = INPUT_REDIRECT.sub(" ", HARMLESS_REDIRECT.sub(" ", " " + seg))
    m = re.match(r"[ \t]*([^ \t]*)", text)
    head = m.group(1) if m else ""
    if not head or SEGMENT_SPECIAL.search(head):         # `./tail` 은 그 이름의 다른 프로그램이다. 머리는 ASCII 낱말만
        return False
    if SEGMENT_SPECIAL.search(text):                     # 리다이렉트·치환·변수·글롭·틸드·이스케이프
        # 인자를 보지 않는 명령(과 find 의 이름 패턴)은 인자가 무엇으로 펼쳐지든 읽기다 — 글롭·틸드·이스케이프·단순 변수·
        # 비ASCII(`grep "$BASE" ~/x/*.json`·`echo 완료`)는 인정한다. 명령 치환(`$(`·백틱 — segments() 에서 `$ ` 로 남는다)·
        # 쓰기 리다이렉트·남은 따옴표(파서가 따옴표 짝을 놓친 신호)는 여전히 아니다.
        rest = text[m.end():]
        if head != "find":                               # find 는 인자가 동작이다 — `find . $X`(X=-delete)
            rest = re.sub(r"\$(?:[A-Za-z_]\w*|\{[A-Za-z_]\w*\})", "", rest)
        if head not in READ_ONLY_HEADS | {"find"} or re.search(r"[$`<>\"'(){}|;&#!\x00-\x08\x0b\x0c\x0e-\x1f]", rest):
            return False
    toks = text.split()
    head, rest = toks[0], toks[1:]
    shorts = [t for t in rest if t.startswith("-") and not t.startswith("--") and t != "-"]
    pos = [t for t in rest if not t.startswith("-") or t == "-"]
    longs = [t for t in rest if t.startswith("--")]
    if head == "printf":                                 # `-v NAME` 은 변수에 쓴다
        return not any(t.startswith("-") and "v" in t for t in rest[:2])
    if head in READ_ONLY_HEADS:                          # 쓰거나 실행하는 옵션이 없는 명령 — 인자가 무엇이든 읽기다
        return True
    if head == "find":                                   # 지우기·실행·파일 출력 동작이 없으면 읽기다
        return not any(t.startswith(FIND_ACTIONS_WRITE) for t in rest)
    if level != FULL:
        return False
    if head == "sed":                                    # `sed -n 10,20p f` — 줄 범위 출력만(`w`·`e` 명령·-i 없음)
        return all(t in ("-n", "-E", "-r", "-u", "-s") for t in shorts) and "-n" in shorts and not longs and \
            len(pos) >= 1 and bool(SED_PRINT_RANGE.fullmatch(pos[0]))
    if head == "sort":                                   # `-o` 는 붙여 써도(`-oF`) 축약해도(`--outp`) 쓴다
        return not longs and all(set(t[1:]) <= SORT_SAFE for t in shorts)
    if head == "uniq":                                   # 두 번째 위치 인자는 출력 파일이다(`-` 도 위치 인자)
        return not longs and len(pos) <= 1 and all(set(t[1:]) <= UNIQ_SAFE for t in shorts)
    if head == "date":                                   # 위치 인자(BSD)·`-s`·`--set` 은 시계를 바꾼다
        return all(t.startswith("+") or t in ("-u", "-R") for t in rest)
    if head == "git" and rest:
        while len(rest) >= 2 and rest[0] == "-C":        # `-C DIR` 은 위치만 바꾼다. `-c`(설정 주입)·그 밖의 전역 옵션은 인정하지 않는다
            rest = rest[2:]
        if not rest:
            return False
        sub, args = rest[0], rest[1:]
        if sub == "grep" and any((re.fullmatch(r"-[A-Za-z0-9]+", t) and "O" in t) or
                                 (t.startswith("--o") and "--open-files-in-pager".startswith(t.split("=", 1)[0]))
                                 for t in args):
            return False                                 # 결과를 지정한 프로그램(페이저)으로 연다 — 묶음(`-iOrm`)·축약(`--op=`) 포함
        if sub in GIT_READ_ONLY:                         # `--ext-diff` 는 설정된 외부 프로그램을 실행한다
            return not any("--output".startswith(t.split("=", 1)[0]) or t.startswith("--ext")
                           for t in args if t.startswith("--"))
        if sub == "branch":
            return all(t in GIT_BRANCH_SAFE for t in args)
        if sub == "remote":
            return args in ([], ["-v"]) or (len(args) == 2 and args[0] in ("show", "get-url"))
        if sub == "stash":
            return args in (["list"], ["show"])
        if sub == "config":
            return (len(args) == 1 and "." in args[0] and not args[0].startswith("-")) or \
                (bool(args) and args[0] in GIT_CONFIG_GET and not any(t.startswith("-") for t in args[1:]))
        return False
    if head == "gh" and len(rest) >= 2:                  # 조회 하위 명령이면 뒤의 값 옵션(`--json title`)은 읽기다
        return rest[0] in ("run", "pr", "issue") and rest[1] in ("list", "view", "checks", "diff", "status") and \
            not any(t.startswith("--w") or (re.fullmatch(r"-[A-Za-z]+", t) and "w" in t) for t in rest)  # 브라우저
    return False
