#!/usr/bin/env python3
"""하네스 자기 점검(쓰레기 수거) — 드리프트와 죽은 참조를 기계로 잡는다.

검사:
  1. hooks.json 이 가리키는 파일이 있다
  2. plugin.json 과 marketplace.json 의 버전이 같다
  3. 스킬·에이전트·docs·README 의 내부 경로 참조(`${CLAUDE_PLUGIN_ROOT}/…`, 상대 링크)가 실재한다
  4. 스킬 frontmatter — name 이 디렉터리명과 같고, description 이 있으며 1024자 이하
  5. SKILL.md 500줄 미만, 100줄 넘는 SKILL.md 는 실제 목차(`## 목차` 아래 항목 3개 이상)가 있다
  9. README·AGENTS 의 개수 표기(훅·에이전트·스킬·eval 케이스·뮤턴트)가 실제와 같다
  6. 에이전트 frontmatter — name·description 이 있다
  7. eval 케이스마다 grader 가 하나 이상 있고, 축 태그(efficacy·organic·skill·overblock)가 정확히 하나다
  8. frontmatter 값이 엄격한 YAML 에서도 문자열로 읽힌다(따옴표 없는 `[`·`{`·백틱 시작, 값 안의 `: `)
 10. README 의 eval 결과 머리가 가리키는 공개본(evals/published/<버전>/)이 실재하고, 원자료 meta 의 버전이
     같으며, 공개본에 로컬 경로(홈 디렉터리·샌드박스 루트)가 남아 있지 않다

실행:  python3 scripts/doctor.py      (문제 있으면 exit 1 — CI 에서 돈다)
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
problems: list = []


def bad(where, what):
    problems.append(f"{where}: {what}")


def frontmatter(text: str) -> dict:
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        return {}
    out = {}
    for line in m.group(1).splitlines():
        k, sep, v = line.partition(":")
        if sep and not line.startswith((" ", "\t")):
            out[k.strip()] = v.strip()
    return out


def check_hooks_json():
    hj = json.loads((ROOT / "hooks/hooks.json").read_text())
    for groups in hj["hooks"].values():
        for g in groups:
            for h in g["hooks"]:
                for m in re.findall(r"\$\{CLAUDE_PLUGIN_ROOT\}/(\S+?)[\"' ]", h["command"] + " "):
                    if not (ROOT / m).exists():
                        bad("hooks/hooks.json", f"없는 파일 참조 {m}")


def check_versions():
    p = json.loads((ROOT / ".claude-plugin/plugin.json").read_text())
    m = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text())
    v = {x["name"]: x.get("version") for x in m["plugins"]}
    if v.get(p["name"]) != p["version"]:
        bad(".claude-plugin", f"버전 불일치 plugin={p['version']} marketplace={v.get(p['name'])}")


PLUGIN_REF = re.compile(r"\$\{CLAUDE_PLUGIN_ROOT\}/([\w./-]+[\w/])")
MD_LINK = re.compile(r"\]\(((?!https?:|#|mailto:)[^)\s#]+)(?:#[^)]*)?\)")


def check_refs():
    files = list(ROOT.glob("skills/*/SKILL.md")) + list(ROOT.glob("agents/*.md")) + \
        list(ROOT.glob("docs/*.md")) + [ROOT / "README.md", ROOT / "AGENTS.md"]
    for f in files:
        if not f.exists():
            continue
        text = f.read_text()
        rel = f.relative_to(ROOT)
        for ref in PLUGIN_REF.findall(text):
            if "<" in ref or "*" in ref:
                continue
            if not (ROOT / ref).exists():
                bad(rel, f"없는 하네스 경로 ${{CLAUDE_PLUGIN_ROOT}}/{ref}")
        for ref in MD_LINK.findall(text):
            if "<" in ref:
                continue
            if not (f.parent / ref).exists():
                bad(rel, f"깨진 링크 {ref}")


YAML_UNSAFE = re.compile(r"^[\[{`@&*!|>%]|: | #")


def check_yaml_values(path: Path, text: str):
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    for line in (m.group(1).splitlines() if m else []):
        k, sep, v = line.partition(": ")
        v = v.strip()
        if sep and not line.startswith((" ", "\t")) and v and v[0] not in "'\"" and YAML_UNSAFE.search(v):
            bad(path.relative_to(ROOT), f"frontmatter `{k}` 값은 따옴표로 감쌀 것(엄격한 YAML 파서에서 깨진다)")


def check_skills():
    for skill in sorted(ROOT.glob("skills/*/SKILL.md")):
        rel = skill.relative_to(ROOT)
        text = skill.read_text()
        check_yaml_values(skill, text)
        fm = frontmatter(text)
        if fm.get("name") != skill.parent.name:
            bad(rel, f"name '{fm.get('name')}' != 디렉터리 '{skill.parent.name}'")
        desc = fm.get("description", "")
        if not desc:
            bad(rel, "description 없음")
        elif len(desc) > 1024:
            bad(rel, f"description {len(desc)}자 > 1024")
        n = text.count("\n")
        if n >= 500:
            bad(rel, f"{n}줄 ≥ 500 — 참조 파일로 쪼갤 것")
        if n > 100 and not TOC.search(text):
            bad(rel, f"{n}줄인데 `## 목차`(항목 3개 이상)가 없다")


TOC = re.compile(r"^## 목차[ \t]*\n(?:[ \t]*\n)?(?:[ \t]*(?:[-*]|\d+\.)[ \t].*\n){3,}", re.M)


def actual_counts() -> dict:
    hj = json.loads((ROOT / "hooks/hooks.json").read_text())
    scripts = {m for groups in hj["hooks"].values() for g in groups for h in g["hooks"]
               for m in re.findall(r"hooks/([\w-]+)\.(?:py|sh)", h["command"])}
    mut = (ROOT / "tests/test_mutation.py").read_text()
    body = mut[mut.index("MUTANTS = ["):]
    body = body[:body.index("\n]")]
    return {
        "훅": len(scripts),
        "에이전트": len(list(ROOT.glob("agents/*.md"))),
        "스킬": len(list(ROOT.glob("skills/*/SKILL.md"))),
        "eval": len([c for c in ROOT.glob("evals/**/case.yaml") if "results" not in c.parts]),
        "술어": len(re.findall(r'^    \("', body, re.M)),       # 항목 첫 줄(들여쓰기 4칸)만 센다
    }


# 문서의 개수 표기 — 숫자를 손으로 고치다 실제와 어긋나는 드리프트(v0.33 채점에서 지적)
COUNT_CLAIMS = [
    ("README.md", r"### 훅 (\d+)개", "훅"), ("README.md", r"### 에이전트 (\d+)개", "에이전트"),
    ("README.md", r"### 스킬 (\d+)개", "스킬"), ("README.md", r"차단 술어 (\d+)개", "술어"),
    ("README.md", r"eval 스위트 (\d+)케이스", "eval"),
    ("AGENTS.md", r"훅 (\d+)개", "훅"), ("AGENTS.md", r"스킬 (\d+)개", "스킬"),
]


def check_counts():
    real = actual_counts()
    for f, pat, key in COUNT_CLAIMS:
        text = (ROOT / f).read_text()
        for m in re.finditer(pat, text):
            if int(m.group(1)) != real[key]:
                bad(f, f"'{m.group(0)}' 표기 ≠ 실제 {key} {real[key]}")


def check_agents():
    for a in sorted(ROOT.glob("agents/*.md")):
        check_yaml_values(a, a.read_text())
        fm = frontmatter(a.read_text())
        for k in ("name", "description"):
            if not fm.get(k):
                bad(a.relative_to(ROOT), f"frontmatter {k} 없음")


EVAL_AXES = {"efficacy", "organic", "skill", "overblock"}


def check_evals():
    for case in sorted(ROOT.glob("evals/**/case.yaml")):
        if "results" in case.parts:
            continue
        if not list((case.parent / "graders").glob("*.md")):
            bad(case.relative_to(ROOT), "grader 없음")
        # 축 태그 — eval-report 가 축별로 집계·게이트한다. 없으면 "미분류"로 빠져 축 게이트를 피한다
        m = re.search(r"^tags:\s*\[([^\]]*)\]", case.read_text(), re.M)
        tags = {t.strip().strip("'\"") for t in m.group(1).split(",")} if m else set()
        if len(tags & EVAL_AXES) != 1:
            bad(case.relative_to(ROOT), f"축 태그가 정확히 하나여야 한다({'·'.join(sorted(EVAL_AXES))}) — 지금 {sorted(tags & EVAL_AXES)}")


# 공개 수치는 공개본을 가리켜야 한다 — 머리만 바꾸고 원자료를 안 올리거나, 다른 버전의 원자료를 두는 드리프트.
# 로컬 경로 검사는 eval-report --publish 의 스크럽이 놓친 것을 잡는 두 번째 그물이다(0.42.2 에서 자유 텍스트 누출 실측).
LOCAL_PATH = re.compile(r"/Users/[^/\s\"'`<]|/home/(?!cwd\b)[^/\s\"'`]|/root(?=[/\s\"'`]|$)|/tmp/(?:claude-|e-|tmp\.)"
                        r"|/var/folders/|[A-Za-z]:\\{1,2}Users\\")


def check_published():
    m = re.search(r"^### eval 결과 — (\d+\.\d+\.\d+) 공개 측정", (ROOT / "README.md").read_text(), re.M)
    if not m:
        bad("README.md", "'### eval 결과 — <버전> 공개 측정' 머리가 없다 — 공개 수치가 공개본을 가리키지 않는다")
        return
    d = ROOT / "evals/published" / m.group(1)
    for need in ("REPORT.md", "aggregate.json"):
        if not (d / need).is_file():
            bad(d.relative_to(ROOT), f"{need} 없음")
    metas = sorted(d.glob("raw/*.meta.json"))
    if not metas:
        bad(d.relative_to(ROOT), "raw/*.meta.json 없음 — 출처 없는 원자료")
    for mp in metas:
        meta = json.loads(mp.read_text())
        if meta.get("pluginVersion") != m.group(1):
            bad(mp.relative_to(ROOT), f"pluginVersion ≠ README 머리 {m.group(1)}")
        if meta.get("dirty") is not False or not re.fullmatch(r"[0-9a-f]{40}", str(meta.get("sha") or "")):
            bad(mp.relative_to(ROOT), "출처가 clean 단일 커밋이 아니다(dirty 가 false 가 아니거나 40자 SHA 없음)")
    for f in sorted(ROOT.glob("evals/published/**/*")):
        if f.is_file() and LOCAL_PATH.search(f.read_text(encoding="utf-8", errors="replace")):
            bad(f.relative_to(ROOT), "로컬 경로가 남아 있다 — eval-report --publish 로 다시 만든다")


def main() -> int:
    for fn in (check_hooks_json, check_versions, check_refs, check_skills, check_agents, check_evals, check_counts,
               check_published):
        fn()
    if problems:
        print(f"doctor: 문제 {len(problems)}건")
        for p in problems:
            print("  -", p)
        return 1
    print("doctor: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
