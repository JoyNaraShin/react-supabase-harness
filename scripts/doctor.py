#!/usr/bin/env python3
"""하네스 자기 점검(쓰레기 수거) — 드리프트와 죽은 참조를 기계로 잡는다.

검사:
  1. hooks.json 이 가리키는 파일이 있다
  2. plugin.json 과 marketplace.json 의 버전이 같다
  3. 스킬·에이전트·docs·README 의 내부 경로 참조(`${CLAUDE_PLUGIN_ROOT}/…`, 상대 링크)가 실재한다
  4. 스킬 frontmatter — name 이 디렉터리명과 같고, description 이 있으며 1024자 이하
  5. SKILL.md 500줄 미만, 100줄 넘는 SKILL.md 는 목차(## 제목 3개 이상)가 있다
  6. 에이전트 frontmatter — name·description 이 있다
  7. eval 케이스마다 grader 가 하나 이상 있다
  8. frontmatter 값이 엄격한 YAML 에서도 문자열로 읽힌다(따옴표 없는 `[`·`{`·백틱 시작, 값 안의 `: `)

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
        if n > 100 and len(re.findall(r"^## ", text, re.M)) < 3:
            bad(rel, f"{n}줄인데 목차 역할의 ## 제목이 3개 미만")


def check_agents():
    for a in sorted(ROOT.glob("agents/*.md")):
        check_yaml_values(a, a.read_text())
        fm = frontmatter(a.read_text())
        for k in ("name", "description"):
            if not fm.get(k):
                bad(a.relative_to(ROOT), f"frontmatter {k} 없음")


def check_evals():
    for case in sorted(ROOT.glob("evals/**/case.yaml")):
        if "results" in case.parts:
            continue
        if not list((case.parent / "graders").glob("*.md")):
            bad(case.relative_to(ROOT), "grader 없음")


def main() -> int:
    for fn in (check_hooks_json, check_versions, check_refs, check_skills, check_agents, check_evals):
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
