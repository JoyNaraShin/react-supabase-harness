"""셸 파서 차분 테스트 — 가드의 셸 해석을 **실제 bash** 와 대조한다.

왜: 가드는 셸 문법을 정규식으로 근사한다. 근사가 틀린 형태를 사람이 미리 다 떠올릴 수는 없다 —
v0.35.1 은 변수 재대입 10가지를 놓쳐 `rm -rf /` 급 삭제를 통과시켰다(2026-10-09). 그래서 명령 조합을
기계적으로 만들어 실제 bash 에서 돌리고, `rm`·`touch` 가 **실제로 받는 인자**를 기록해 가드 판정과 맞춘다.

안전: `rm`·`touch` 는 인자만 기록하는 셸 함수로 덮어쓴다 — 아무것도 지워지거나 만들어지지 않는다.
말뭉치에는 `command rm`·`/bin/rm` 처럼 함수를 건너뛰는 형태를 넣지 않는다.

성질(property):
  rm   — 실제 대상 중 하나라도 위험 경로(루트·시스템 최상위·홈과 그 바로 아래·cwd 와 그 조상)면 가드는 막는다.
  touch — 실제 대상이 보호 파일(.claude/state/…)이면 가드는 막는다.
"""
import itertools
import re
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOKS = ROOT / "hooks"
sys.path.insert(0, str(HOOKS))

BASH = shutil.which("bash")

# ── 말뭉치 ────────────────────────────────────────────────────────────────────
PREFIXES = ["S={v}", "export S={v}", "S={v} && T=x", "S={v}; T=$S"]
JOINERS = ["; ", " && ", " | ", " & ", " || ", "\n"]
# 앞 대입 뒤에 끼어드는 것 — 값을 바꾸거나(위험), 안 바꾸는 것(정상)
MUTATORS = [
    "", ":", "true", "echo hi", "S+=/../../..", "S=/", "for S in /; do :; done", "read S <<< /",
    "unset S", "eval 'S=/'", "if true; then S=/; fi", "true && S=/", "false || S=/", "declare S=/",
    "typeset S=/", "printf -v S /", "IFS=/", ": ${S:=/}", "S=$(echo /)", "S=~", "cd /",
    "f(){ S=/; }; f", "x=S; eval $x=/", "readonly S=/ 2>/dev/null || true", "let x=1",
    "set -- /", "S=/ true", "(S=/)", "{ S=/; }", "select S in /; do break; done <<< 1",
    "mapfile -t A <<< /", "printf '%s' \"$S\"", "echo '$S'",
    "cd ..", "cd ../..", "cd ~", "cd", "cd $X", "cd \"$(echo /)\"", "X=$(echo /)", "echo $(echo hi)",
    # 명령 위치의 `.`·`source`(./s.sh 가 S·T 를 / 로 바꾼다)와 인자 `.`(코드를 읽지 않는다) — 0.40.0 F2
    ". ./s.sh", "\".\" ./s.sh", "\\. ./s.sh", "source ./s.sh", "\\source ./s.sh", "if true; then . ./s.sh; fi",
    "{ . ./s.sh; }", ">/dev/null . ./s.sh", "command . ./s.sh", "X=1 . ./s.sh", "! . ./s.sh", "while . ./s.sh; do break; done",
    "git add . 2>/dev/null || true", "echo .", "ls . >/dev/null",
    # 독립 검증 1회차 F1 — 리다이렉트·옵션·변수 경유 source
    "2>&1 . ./s.sh", ">&2 . ./s.sh", ">| /dev/null . ./s.sh", "time -p . ./s.sh", "command -p . ./s.sh",
    "command -- . ./s.sh", "X=source; $X ./s.sh", "echo x # it's\n. ./s.sh", "<./s.sh . ./s.sh",
    # 2회차 N2·N3 — 간접 실행, 이스케이프된 공백 뒤 `#`
    'f(){ "$@"; }; f . ./s.sh', 'set -- . ./s.sh; "$@"', "set -- source; $1 ./s.sh", "f(){ $1 $2; }; f source ./s.sh",
    "echo a\\ # ; . ./s.sh",
    # 3·4회차 R3-1·R3-2 — 중괄호 확장·치환 머리 source
    "{source,} ./s.sh", "{.,} ./s.sh", "$(printf '\\56') ./s.sh", "`printf .` ./s.sh",
]
VALUES = ["/tmp/hx/a/b", "/tmp/hx/a/b/../../..", ".", "..", "x/..", "/", "~", "build"]
RM_USES = ["rm -rf $S", "rm -rf ${S}", 'rm -rf "$S"', "rm -rf $S/", "rm -rf $S/..", "rm -rf $S/../../..",
           "rm -rf '$S'/..", "rm -rf $T", "rm -rf $X/tmp", "rm -rf \"$X\"/usr", "rm -rf ${X}/../tmp", "rm -rf work",
           "rm -rf ../work", "rm -rf $S/../work", 'rm -rf "${X:-/tmp/hx}"/x',
           # 아는 변수의 변형 확장 — 접두를 깎거나(4회차 R4-1) 원래 값을 남긴다(0.40.0 퍼즈)
           "rm -rf ${S%/*}", "rm -rf ${S%%/a*}", "rm -rf ${S:0:4}", "rm -rf ${S//?/}/usr", "rm -rf ${S/x}usr",
           "rm -rf /${S:99}", "rm -rf ${HOME%/*}", "rm -rf ${PWD::1}usr"]

RECORDER = r'''
__rec() { local a; for a in "$@"; do case "$a" in -*) ;; *) printf '%s\t%s\0' "$PWD" "$a" >> "$__OUT";; esac; done; }
rm() { __rec "$@"; }
touch() { __rec "$@"; }
'''


WRAPPER_DIR = str(ROOT / "bin")
# 실행 시점 가드(bin/rm)가 통과시킨 뒤 실제로 지웠을 인자를 기록한다 — 진짜 rm 자리에 끼운다(HARNESS_REAL_RM)
REAL_RM_RECORDER = """#!/bin/sh
o=1
for a in "$@"; do
  if [ "$o" = 1 ] && [ "$a" = "--" ]; then o=0; continue; fi
  if [ "$o" = 1 ]; then case "$a" in -?*) continue;; esac; fi
  printf '%s\\t%s\\0' "$PWD" "$a" >> "$__OUT"
done
"""


_STUB = []


def _stub_dir() -> str:
    """실행 PATH 의 **유일한** 디렉터리 — 삭제 도구(rm·rmdir·unlink·shred·mv)는 기록기, 그 밖에는 이 테스트가 쓰는
    도구만 링크한다. `/usr/bin:/bin` 을 PATH 에 두면 함수가 덮지 못하는 경로(`command rm`·런처·`env PATH=…`)가
    진짜 rm 에 닿는다(0.40.0 독립 검증 3회차 R3-3)."""
    if not _STUB:
        d = tempfile.mkdtemp(prefix="harness-stub-")
        for name in ("rm", "rmdir", "unlink", "shred", "mv"):
            with open(os.path.join(d, name), "w") as f:
                f.write(REAL_RM_RECORDER)
            os.chmod(os.path.join(d, name), 0o755)
        for name in ("python3", "env", "find", "xargs", "cat", "ls", "mkdir", "sh"):   # bin/rm(파이썬)·래퍼 머리용
            src = shutil.which(name, path="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin")
            if src:
                os.symlink(src, os.path.join(d, name))
        _STUB.append(d)
    return _STUB[0]


# 실행 사본에 이것이 있으면 실행하지 않는다 — 기록기를 지나쳐 진짜 삭제 도구에 닿을 수 있는 형태(절대·변형 경로,
# 기본 PATH 로 찾는 `command -p rm`(찾기만 바꾸고 자식 PATH 는 그대로라 `command -p .`·`sh` 는 안전), PATH 를 건드리는
# 모든 형태와 `env -i`·`env -u`, `python3 -c`, find -delete — 0.40.0 독립 검증 3·4회차). 판정(scan)은 원문으로 이미 했다.
_UNSAFE_RUN = re.compile(
    r"[\w.${}~-]*/(?:rm|rmdir|unlink|shred|mv)\b"                   # 경로로 부르는 삭제 도구(`/bin/rm`·`./rm`·`$X/rm`)
    r"|\bcommand(?:\s+-\w+)*\s+-\w*p\w*(?:\s+-\w+)*\s+(?:rm|rmdir|unlink|shred|mv)\b"
    r"|\bPATH\b"                                                    # PATH 재설정·`read PATH`·`env -u PATH`
    r"|\benv\b[^;&|\n]*\s-(?:\w*i\b|u\b|-ignore-environment|-unset)"  # env -i 는 기본 PATH 로 찾는다
    r"|\bpython3?\b[^;&|\n]*\s-\w*c\b|-delete\b")


def _unsafe_run(run: str) -> bool:
    """따옴표·백슬래시를 벗긴 모양으로 본다(`/bin/r\\m`·`"command" -p rm`). `\\rm`·`r\\m` 자체는 PATH(=스텁)로 풀려 안전하다."""
    return bool(_UNSAFE_RUN.search(re.sub(r"[\\'\"]", "", run)))


def real_args(cmd: str, cwd: str, home: str, wrapper: str = "") -> list:
    """bash 가 실제로 rm/touch 에 넘긴 인자 — (그때의 PWD, 인자) 를 절대 경로로.
    wrapper=<기록기 경로> 면 rm 을 함수로 바꾸지 않고 PATH 앞의 bin/rm(실행 시점 가드)을 지나게 한다 —
    그때 기록되는 것은 가드가 **통과시켜** 진짜 rm 에 넘어간 인자다."""
    out = os.path.join(home, "out.tsv")
    if os.path.exists(out):
        os.unlink(out)
    # 안전: 셸 함수 기록기는 `command rm`·`/bin/rm` 을 덮지 못한다 — 이 테스트는 0.35 부터 그 두 머리를 진짜 rm 으로
    # 실행했다(정적 가드가 통과시킨 대상만, 0.40.0 독립 검증이 찾음). 판정(scan)은 원문으로 하고, **실행하는 사본**만
    # 기록기로 돌린다: 두 머리를 `rm` 으로 바꾸고, PATH 맨 앞에 기록기 스텁(rm·rmdir·unlink·shred·mv)을 둔다.
    run = re.sub(r"(?<![\w./-])(?:command rm|/bin/rm) ", "rm ", cmd)        # 낱말 경계에서만(`/usr/bin/rm` 은 그대로 → 거부)
    if _unsafe_run(run) or re.search(r"\bcommand\s+(?:-\S+\s+)*rm\b", run):
        raise AssertionError(f"실행 사본에 기록기를 지나치는 rm 이 남았다 — 실행하지 않는다: {cmd!r}")
    stub = _stub_dir()
    env = {"PATH": stub, "HOME": home, "__OUT": out}
    script = RECORDER + run
    if wrapper:
        env.update(PATH=WRAPPER_DIR + ":" + stub, HARNESS_REAL_RM=wrapper, HARNESS_PROJECT_DIR=cwd)
        script = RECORDER.replace("rm() { __rec \"$@\"; }\n", "") + run
    subprocess.run([BASH, "-c", script + "\nwait"], cwd=cwd, env=env, capture_output=True, timeout=10)
    res = []
    if os.path.exists(out):
        with open(out, encoding="utf-8", errors="surrogateescape") as f:
            for ln in f.read().split("\0"):                  # 인자에 줄바꿈이 들어갈 수 있다(`$'\n'`) — NUL 로 나눈다
                if not ln:
                    continue
                pwd, a = ln.split("\t", 1)
                # POSIX rm 은 마지막 요소가 `.`·`..` 인 대상을 거부하고 아무것도 하지 않는다 — 실제 삭제 대상이 아니다
                if not a or os.path.basename(a.rstrip("/") or "/") in (".", ".."):
                    continue                                   # 빈 인자·`.`·`..` 는 rm 이 거부한다
                res.append(os.path.normpath(a if a.startswith("/") else os.path.join(pwd, a)))
    return res


def is_dangerous(path: str, cwd: str, home: str) -> bool:
    """가드와 독립된 판정 — 이런 경로의 재귀 삭제는 일상 정리가 아니다."""
    parts = [p for p in path.split("/") if p]
    if len(parts) <= 1:                                       # /, /tmp, /usr …
        return True
    if path == home or path == os.path.dirname(home) or (path.startswith(home + "/") and path.count("/") - home.count("/") <= 1):
        return True                                           # 홈, 홈 바로 아래 폴더
    if cwd == path or cwd.startswith(path + "/"):             # cwd 와 그 조상
        return True
    return False



def gen_grammar(rnd) -> str:
    """문법 요소를 무작위로 조합한 rm 명령 — 사람이 떠올린 목록 밖을 덮기 위해.
    (독립 적대 리뷰가 후보 생성 단계에서 중단돼 대신 기계 생성으로 넓혔다, 2026-10-09)"""
    vals = ["/tmp/hx/a/b", "/tmp/hx/a/b/../../..", ".", "..", "x/..", "/", "build", "a b", "/tmp/hx/*",
            "#", "x#", "a,b", "/tmp/hx/a,..", "-", "=/"]                 # 펼친 뒤 다시 읽으면 뜻이 바뀌는 값
    q = [lambda v: v, lambda v: f"'{v}'", lambda v: f'"{v}"', lambda v: f"$'{v}'"]
    stmts = [
        lambda: f"S={rnd.choice(q)(rnd.choice(vals))}", lambda: f"export S={rnd.choice(vals)}",
        lambda: f"A=({rnd.choice(vals)} {rnd.choice(vals)})", lambda: f"A[0]={rnd.choice(vals)}",
        lambda: "cd " + rnd.choice(["/", "..", "~", "-", "", "$S", '"$S"', "/tmp", "../.."]),
        lambda: "pushd " + rnd.choice(["/", "..", "/tmp"]) + " >/dev/null", lambda: "popd >/dev/null",
        lambda: "f(){ " + rnd.choice(["S=/", "cd /", "rm -rf $S"]) + "; }", lambda: "f",
        lambda: "(( i = 1 ))", lambda: "set -- " + rnd.choice(vals), lambda: "shift",
        lambda: "S=$(echo " + rnd.choice(vals) + ")", lambda: "S=`echo /`", lambda: "T=$S",
        lambda: "S=${S%/*}", lambda: "S=${S#*/}", lambda: "S=${S/b/..}", lambda: "true", lambda: ":",
        lambda: "{ " + rnd.choice(["S=/", "cd /", "true"]) + "; }", lambda: "( " + rnd.choice(["S=/", "cd /"]) + " )",
        lambda: rnd.choice(["", "then ", "do ", "! ", "{ ", "command ", ">/dev/null ", "X=1 "])
        + rnd.choice([".", "'.'", "\\.", "source", "\"source\""]) + " ./s.sh",
        lambda: rnd.choice(["git add .", "echo .", "ls ."]) + " >/dev/null 2>&1",
    ]
    targets = ["$S", '"$S"', "${S}", "$S/", "$S/..", "$S/*", "${A[0]}", '"${A[@]}"', "$1", '"$@"', "$T",
               "${S%/*}", "${S#/}", "$S{,/..}", "~", "~+", "~-", "$PWD", "$OLDPWD", "*", "./*", "../*",
               "work", "../work", "$S/../work", "${S:-/}", "${S:+/}", "\\\n$S",
               "$S ~", "x $S /", "{$S}/..", "/tmp/hx/{$S}/../..", "/tmp/hx/{a{1..300},..}/..",
               "/tmp/hx/{/../}/..", "/tmp/hx/a/{1..9..4}/../../..", "/tmp/hx/{a..c}/../..", "{,$S}"]
    joins = ["; ", " && ", "\n", " || ", " | ", " & "]
    parts = [rnd.choice(stmts)() for _ in range(rnd.randint(1, 4))]
    return "".join(p + rnd.choice(joins) for p in parts) + rnd.choice(["rm -rf ", "rm -r -f ", "\\rm -rf "]) \
        + rnd.choice(targets).replace("\\\\\\n", "\\\n")


def gen_chars(rnd, heads=None) -> str:
    """문법 요소가 아니라 **문자** 단위로 섞는다 — `\\⏎`(이어쓰기)·`$'…'`·이스케이프·중괄호 조각처럼 토큰 경계를
    바꾸는 문자는 문법 단위 생성기가 만들지 못했다(0.35.3 커밋 보안 리뷰 뒤 이 퍼즈가 `r\\⏎m -rf /`·
    `rm -rf $'/'` 를 찾음 — 둘 다 0.35 이전부터 있던 구멍)."""
    atoms = ["$S", "${S}", "$T", "/", "..", "/..", "{", ",", "}", "\\", "\\\n", "'", '"', "$", "#", "~", "+",
             "-", "a", "*", " ", "$'", '$"', "\\x2f", "\\057", "\\,", "\\{", "=", ":", "@", "$$", "${S",
             "${S:-/}", "{,", ",}", "{/../}", "...", "usr"]
    vals = ["/tmp/hx/a/b", "/tmp/hx/a", "/", ".", "..", "a,b", "#", "x", "-", "+", "/tmp/hx/a/b/c/d"]
    lead = ["S={v}; ", "S={v} && ", "export S={v}\n", "S={v}; T=$S/..; ", ""]
    heads = heads or ["rm -rf ", "rm -r -f ", "\\rm -rf ", "r\\\nm -rf ", "rm \\\n-rf ", "command rm -rf ", "/bin/rm -rf "]
    body = "".join(rnd.choice(atoms) for _ in range(rnd.randint(1, 7)))
    return rnd.choice(lead).replace("{v}", rnd.choice(vals)) + rnd.choice(heads) + body


def fuzz_rng():
    import random
    return random.Random(int(os.environ.get("HARNESS_FUZZ_SEED", "20261009"))), int(os.environ.get("HARNESS_FUZZ_N", "2500"))


def load(name):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HOOKS / name)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@unittest.skipUnless(BASH, "bash 가 없으면 대조할 수 없다")
class ShellDifferentialTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="harness-diff-")
        cls.home = os.path.realpath(os.path.join(cls.tmp, "home"))
        cls.cwd = os.path.join(cls.home, "proj", "work")      # cwd 가 홈 3단계 아래 — 조상 삭제를 잴 수 있게
        os.makedirs(cls.cwd)
        with open(os.path.join(cls.cwd, "s.sh"), "w") as f:      # `. ./s.sh` 가 읽는 파일 — 값을 바꾼다
            f.write("S=/; T=/\n")
        cls.bdg = load("block-destructive-git.py")
        cls.recorder = os.path.join(cls.tmp, "real-rm-recorder.sh")
        with open(cls.recorder, "w") as f:
            f.write(REAL_RM_RECORDER)
        os.chmod(cls.recorder, 0o755)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def corpus(self):
        for pre, join, mut, val, use in itertools.product(PREFIXES, JOINERS, MUTATORS, VALUES, RM_USES):
            p = pre.format(v=val)
            yield (p + join + mut + "; " + use) if mut else (p + join + use)

    def test_executed_copy_never_reaches_a_real_rm(self):
        # 0.35–0.39 는 `command rm`·`/bin/rm` 머리를 진짜 rm 으로 실행했다 — 실행 사본은 기록기로만 간다
        self.assertEqual(real_args("command rm -rf zz; /bin/rm -rf yy", self.cwd, self.home),
                         [os.path.join(self.cwd, "zz"), os.path.join(self.cwd, "yy")])
        # 독립 검증 3회차 R3-3 — 거부 목록만으로는 변형 경로가 샌다. PATH 는 스텁뿐이고 변형도 거부한다
        for c in ["/usr/bin/rm -rf zz", "//bin/rm zz", "/bin/./rm zz", "/bin/r\\m zz", '"command" -p rm zz',
                  "env PATH=/bin rm zz", "find zz -delete",
                  # 4회차 (d) — 스텁 전용 PATH 에서도 진짜 도구에 닿는 형태
                  "env -i rm zz", "env -u PATH rm zz", "X=/bin; $X/rm zz", "cd /bin && ./rm zz",
                  "read PATH <<< /bin; rm zz", "printf -v PATH /bin; rm zz", "python3 -c 'import shutil'"]:
            with self.subTest(c=c), self.assertRaises(AssertionError):
                real_args(c, self.cwd, self.home)
        self.assertEqual(real_args("\\rm zz; env rm yy", self.cwd, self.home),       # PATH 로 풀리는 형태는 기록기에 닿는다
                         [os.path.join(self.cwd, "zz"), os.path.join(self.cwd, "yy")])
        self.assertFalse(os.path.islink(os.path.join(_stub_dir(), "rm")))                   # 스텁의 rm 은 기록기다

    def test_rm_guard_never_allows_a_dangerous_real_target(self):
        misses, checked = [], 0
        for cmd in self.corpus():
            reason = self.bdg.scan(cmd)
            if reason:
                continue                                       # 막았다 — 과차단은 이 성질의 대상이 아니다
            checked += 1
            bad = [a for a in real_args(cmd, self.cwd, self.home) if is_dangerous(a, self.cwd, self.home)]
            if bad:
                misses.append((cmd, bad))
        self.assertGreater(checked, 50, "통과한 명령이 너무 적다 — 말뭉치나 판정이 망가졌다")
        self.maxDiff = None
        self.assertEqual(misses[:8], [], f"가드가 통과시켰는데 실제 대상이 위험한 명령 {len(misses)}개")

    def _fuzz(self, gen, label):
        rnd, n = fuzz_rng()
        misses, checked = [], 0
        for _ in range(n):
            cmd = gen(rnd)
            if self.bdg.scan(cmd):
                continue
            checked += 1
            bad = [a for a in real_args(cmd, self.cwd, self.home) if is_dangerous(a, self.cwd, self.home)]
            if bad:
                misses.append((cmd, bad))
        self.maxDiff = None
        self.assertGreater(checked, 50)
        self.assertEqual(misses[:8], [], f"{label} 중 가드가 통과시켰는데 실제 대상이 위험한 것 {len(misses)}개")

    def test_generated_shell_syntax_never_hides_a_dangerous_target(self):
        self._fuzz(gen_grammar, "문법 단위 생성 명령")

    def test_character_level_shell_syntax_never_hides_a_dangerous_target(self):
        self._fuzz(gen_chars, "문자 단위 생성 명령")

    # ── 실행 시점 가드(bin/rm) — 정적 가드 없이 래퍼 혼자서 같은 성질을 지키는가 ──────────────────────
    def _wrapped_fuzz(self, gen, label):
        rnd, n = fuzz_rng()
        misses = []
        for _ in range(n):
            cmd = gen(rnd)
            bad = [a for a in real_args(cmd, self.cwd, self.home, wrapper=self.recorder)
                   if is_dangerous(a, self.cwd, self.home)]
            if bad:
                misses.append((cmd, bad))
        self.maxDiff = None
        self.assertEqual(misses[:8], [], f"{label} 중 실행 시점 가드가 진짜 rm 에 넘긴 위험 대상 {len(misses)}개")

    def test_runtime_guard_alone_never_passes_a_dangerous_target(self):
        # 정적 가드로 거르지 않는다 — 래퍼 혼자 막아야 한다. 단 `/bin/rm` 은 PATH 를 거치지 않아 래퍼 밖이다(정적 가드 몫).
        self._wrapped_fuzz(gen_grammar, "문법 단위 생성 명령")
        heads = ["rm -rf ", "rm -r -f ", "\\rm -rf ", "r\\\nm -rf ", "rm \\\n-rf ", "command rm -rf ",
                 "find . -maxdepth 0 -exec rm -rf {} + ; rm -rf ", "echo | xargs rm -rf "]
        self._wrapped_fuzz(lambda r: gen_chars(r, heads), "문자 단위 생성 명령")

    def test_runtime_guard_passes_routine_cleanup(self):
        # 정밀도: 정적 가드가 위치를 몰라 막던 정상 명령도 래퍼는 실제 경로를 보고 통과시킨다
        os.makedirs(os.path.join(self.cwd, "dist"), exist_ok=True)
        for cmd, want in [("rm -rf dist", "dist"), ("rm -rf /tmp/hx/build-x", "/tmp/hx/build-x"),
                          ('T=/tmp/hx; rm -rf "$T"/foo', "/tmp/hx/foo"), ("cd .. && rm -rf old-proj", "old-proj"),
                          ("rm notes.txt", "notes.txt"), ("rm -rf -- -weird", "-weird")]:
            with self.subTest(cmd=cmd):
                got = real_args(cmd, self.cwd, self.home, wrapper=self.recorder)
                self.assertTrue(got and got[0].endswith(want), got)

    def test_runtime_guard_follows_symlinks_only_where_rm_does(self):
        link = os.path.join(self.cwd, "to-home")
        if not os.path.islink(link):
            os.symlink(self.home, link)
        self.assertEqual(real_args("rm -rf to-home/", self.cwd, self.home, wrapper=self.recorder), [])  # 홈을 지운다
        got = real_args("rm -rf to-home", self.cwd, self.home, wrapper=self.recorder)                  # 링크만 지운다
        self.assertEqual([os.path.basename(g) for g in got], ["to-home"])

    def test_known_safe_forms_still_pass(self):
        # 정밀도: 오늘 실사용에서 막혀 고친 형태는 계속 통과해야 한다(과차단 회귀 방지)
        for cmd in ["S=/tmp/hx/a/b && rm -rf $S", "S=/tmp/hx/a/b; rm -rf ${S}/c", "export S=/tmp/hx/a/b && rm -rf \"$S\"",
                    "S=/tmp/hx/a/b; T=$S/c && rm -rf $T"]:
            with self.subTest(cmd=cmd):
                self.assertIsNone(self.bdg.scan(cmd))

    def test_self_protection_sees_the_real_write_target(self):
        proj = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(proj, ".claude", "state"), exist_ok=True)
        os.makedirs(os.path.join(proj, "src"), exist_ok=True)
        Path(proj, "package.json").write_text("{}")
        target_vals = [".claude/state", "/tmp/hx/x", "src"]
        uses = ["touch $D/plan-gate-off", "touch ${D}/plan-gate-off", "touch \"$D\"/plan-gate-off"]
        muts = ["", "D=.claude/state", "for D in .claude/state; do :; done", "read D <<< .claude/state",
                "eval 'D=.claude/state'", "declare D=.claude/state", "true && D=.claude/state", "cd .claude/state"]
        misses = []
        for val, join, mut, use in itertools.product(target_vals, ["; ", " && ", " | "], muts, uses):
            cmd = f"D={val}{join}{mut + '; ' if mut else ''}{use}"
            real = real_args(cmd, proj, self.home)
            hits = [a for a in real if "/.claude/state/" in a + "/" and a.startswith(proj)]
            if not hits:
                continue
            payload = {"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": proj, "hook_event_name": "PreToolUse"}
            env = dict(os.environ, CLAUDE_PROJECT_DIR=proj, CLAUDE_PLUGIN_ROOT=str(ROOT))
            r = subprocess.run([sys.executable, str(HOOKS / "gate-engine.py")], input=json.dumps(payload),
                               capture_output=True, text=True, env=env, cwd=proj)
            if '"deny"' not in r.stdout:
                misses.append(cmd)
        self.assertEqual(misses[:5], [], f"보호 파일을 실제로 쓰는데 통과한 명령 {len(misses)}개")


if __name__ == "__main__":
    unittest.main()
