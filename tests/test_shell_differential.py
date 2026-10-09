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
]
VALUES = ["/tmp/hx/a/b", "/tmp/hx/a/b/../../..", ".", "..", "x/..", "/", "~", "build"]
RM_USES = ["rm -rf $S", "rm -rf ${S}", 'rm -rf "$S"', "rm -rf $S/", "rm -rf $S/..", "rm -rf $S/../../..",
           "rm -rf '$S'/..", "rm -rf $T", "rm -rf $X/tmp", "rm -rf \"$X\"/usr", "rm -rf ${X}/../tmp", "rm -rf work",
           "rm -rf ../work", "rm -rf $S/../work", 'rm -rf "${X:-/tmp/hx}"/x']

RECORDER = r'''
__rec() { local a; for a in "$@"; do case "$a" in -*) ;; *) printf '%s\t%s\n' "$PWD" "$a" >> "$__OUT";; esac; done; }
rm() { __rec "$@"; }
touch() { __rec "$@"; }
'''


def real_args(cmd: str, cwd: str, home: str) -> list:
    """bash 가 실제로 rm/touch 에 넘긴 인자 — (그때의 PWD, 인자) 를 절대 경로로."""
    out = os.path.join(home, "out.tsv")
    if os.path.exists(out):
        os.unlink(out)
    env = {"PATH": "/usr/bin:/bin", "HOME": home, "__OUT": out}
    subprocess.run([BASH, "-c", RECORDER + cmd + "\nwait"], cwd=cwd, env=env, capture_output=True, timeout=10)
    res = []
    if os.path.exists(out):
        with open(out, encoding="utf-8") as f:
            for ln in f.read().splitlines():
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
        cls.bdg = load("block-destructive-git.py")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def corpus(self):
        for pre, join, mut, val, use in itertools.product(PREFIXES, JOINERS, MUTATORS, VALUES, RM_USES):
            p = pre.format(v=val)
            yield (p + join + mut + "; " + use) if mut else (p + join + use)

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

    def test_generated_shell_syntax_never_hides_a_dangerous_target(self):
        """문법 요소를 무작위로 조합한 명령(고정 시드)으로 같은 성질을 본다 — 사람이 떠올린 목록 밖을 덮기 위해.
        (독립 적대 리뷰가 후보 생성 단계에서 중단돼 대신 기계 생성으로 넓혔다, 2026-10-09)"""
        import random
        rnd = random.Random(int(os.environ.get("HARNESS_FUZZ_SEED", "20261009")))
        n = int(os.environ.get("HARNESS_FUZZ_N", "2500"))
        vals = ["/tmp/hx/a/b", "/tmp/hx/a/b/../../..", ".", "..", "x/..", "/", "build", "a b", "/tmp/hx/*"]
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
        ]
        targets = ["$S", '"$S"', "${S}", "$S/", "$S/..", "$S/*", "${A[0]}", '"${A[@]}"', "$1", '"$@"', "$T",
                   "${S%/*}", "${S#/}", "$S{,/..}", "~", "~+", "~-", "$PWD", "$OLDPWD", "*", "./*", "../*",
                   "work", "../work", "$S/../work", "${S:-/}", "${S:+/}", "\\\n$S"]
        joins = ["; ", " && ", "\n", " || ", " | ", " & "]
        misses, checked = [], 0
        for _ in range(n):
            parts = [rnd.choice(stmts)() for _ in range(rnd.randint(1, 4))]
            cmd = "".join(p + rnd.choice(joins) for p in parts) + rnd.choice(["rm -rf ", "rm -r -f ", "\\rm -rf "]) \
                + rnd.choice(targets).replace("\\\\\\n", "\\\n")
            if self.bdg.scan(cmd):
                continue
            checked += 1
            bad = [a for a in real_args(cmd, self.cwd, self.home) if is_dangerous(a, self.cwd, self.home)]
            if bad:
                misses.append((cmd, bad))
        self.maxDiff = None
        self.assertGreater(checked, 50)
        self.assertEqual(misses[:8], [], f"생성 명령 중 가드가 통과시켰는데 실제 대상이 위험한 것 {len(misses)}개")

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
