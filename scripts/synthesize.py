#!/usr/bin/env python3
"""리뷰 종합 규약 집행기 — 결함 원장 회계 (표준 라이브러리 전용).

docs/REVIEW-PROTOCOL.md §종합 규약의 기계 패스. 메인 루프의 산문 압축 편향이 종합 단계에서
항목을 떨어뜨리거나 severity 를 강등하는 누수를, 루프가 아니라 회계로 막는다.

각 리뷰어 리포트(.md)는 맨 끝에 machine-readable 결함 원장을 emit 한다
(`id | severity | 축 | 위치 | 한 줄` 파이프 행들; 코드펜스 안이든 markdown 표든 허용).
리포트 파일명 stem 이 agent 이름이 된다.

모드 A (기본) — join 스켈레톤 생성:
    python3 scripts/synthesize.py report1.md report2.md ...
      · agent 별 원장 행 수 집계
      · 전 행 union 의 join 표 스켈레톤(목적지·처리 빈칸 강제 — 메인 루프가 채움)
      · 원장을 못 찾은 리포트는 크게 경고(침묵 스킵 금지)

모드 B (검증) — 종합 문서 보존 검사:
    python3 scripts/synthesize.py --check joined.md report1.md ...
      · 모든 `agent:id` 가 join 표의 **행 첫 칸**으로 등장하는지(누락 = 침묵 드롭).
        id 는 토큰 경계로 대조한다 — `F10` 이 남아 있다고 `F1` 이 보존된 것은 아니다
      · 그 행의 severity 칸이 원본 등급과 같은지(산문으로 "High → Medium" 을 적어도 강등이다)
      · 그 행의 처리 칸(마지막 칸)이 채워졌는지(모드 A 스켈레톤을 그대로 내면 실패)
      · 원장을 못 찾은 리포트가 있으면 실패(모드 A 와 같은 기준)
      · 하나라도 걸리면 나열 후 exit 1
      · 처리=merged/rejected 로 표기된 행을 "적대 재대조 큐"로 별도 출력(부분집합)

파싱은 관대하게(공백·컬럼 수 변형 허용), 실패는 시끄럽게.
"""

import re
import sys
from pathlib import Path

# 알려진 severity 토큰(파싱 관대화·헤더/구분행 판별·fallback 탐지용).
# 실제 리뷰어들이 쓰는 등급: Critical/High/Medium/Low + Major/Minor/Nit + Info.
SEVERITY_TOKENS = {
    "critical", "high", "medium", "low", "major", "minor", "nit", "info",
}

# ledger 섹션 헤딩 탐지(관대): "결함 원장", "defect ledger", "ledger".
LEDGER_HEADING_RE = re.compile(
    r"^#{1,6}\s.*(결함\s*원장|defect\s+ledger|\bledger\b)", re.IGNORECASE
)
HEADING_RE = re.compile(r"^#{1,6}\s")
FENCE_RE = re.compile(r"^\s*```")
# "결함 없음" / "no defects" 류 — 유효한 0행 원장.
EMPTY_LEDGER_RE = re.compile(r"(결함\s*없음|no\s+defects?\b)", re.IGNORECASE)


def _looks_like_severity(cell):
    """cell(예: 'Critical', 'Critical(운영)', 'High') 이 severity 토큰인지."""
    base = re.split(r"[\s(]", cell.strip(), maxsplit=1)[0].lower()
    return base in SEVERITY_TOKENS


def _split_pipe_row(line):
    """파이프 행을 셀 리스트로. 선행/후행 빈 셀(`| a | b |`)은 버린다."""
    parts = [c.strip() for c in line.split("|")]
    # 양끝 파이프로 생긴 빈 셀 제거
    if parts and parts[0] == "":
        parts = parts[1:]
    if parts and parts[-1] == "":
        parts = parts[:-1]
    return parts


def _is_separator_row(cells):
    """`|---|---|` 같은 markdown 표 구분행인지."""
    return bool(cells) and all(re.fullmatch(r":?-{2,}:?", c or "") for c in cells)


def _is_header_row(cells):
    """`id | severity | ...` 헤더행인지 — 첫 셀이 'id' 이거나 둘째가 'severity'."""
    if not cells:
        return False
    first = cells[0].strip().lower()
    if first in ("id", "원행", "원행(agent:id)"):
        return True
    if len(cells) >= 2 and cells[1].strip().lower().startswith("sev"):
        return True
    return False


def _normalize_row(cells, warnings, agent, lineno):
    """원장 셀 리스트를 (id, severity, 축, 위치, 한줄) 5-튜플로.

    관대: 5칸 초과면 초과분을 마지막(한줄)에 병합, 미만이면 크게 경고 후 패딩.
    """
    if len(cells) > 5:
        cells = cells[:4] + [" | ".join(cells[4:])]
    elif len(cells) < 5:
        warnings.append(
            f"  [행 형식 이상] {agent}:{lineno} — 셀 {len(cells)}개(5개 기대): "
            f"{cells!r} — 패딩 처리하나 확인 요망"
        )
        cells = cells + [""] * (5 - len(cells))
    return tuple(cells)


def parse_ledger(path, warnings, valid_empty=None):
    """리포트 md 에서 결함 원장 행들을 파싱. dict 리스트 반환.

    각 행: {agent, id, severity, axis, location, oneliner, lineno}.
    원장 헤딩을 못 찾으면 크게 경고하고 fallback(펜스 안 파이프 표) 시도.
    '결함 없음' 이면 빈 리스트(경고 아님 — valid_empty 집합에 agent 기록).
    """
    agent = path.stem
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        warnings.append(f"  [읽기 실패] {path}: {e}")
        return []

    lines = text.splitlines()
    # 원장은 리포트 맨 끝에 낸다 — 앞쪽에 "ledger" 가 든 다른 헤딩이 있어도 마지막 것이 원장이다.
    heading_idx = None
    for i, line in enumerate(lines):
        if LEDGER_HEADING_RE.match(line):
            heading_idx = i

    if heading_idx is None:
        warnings.append(
            f"  [원장 없음] {path} — '결함 원장' 헤딩을 못 찾음. "
            f"이 리뷰어는 docs/REVIEW-PROTOCOL.md §종합 규약 rule 1(원장 emit)을 위반. "
            f"fallback 파싱 시도 중..."
        )
        return _fallback_parse(agent, lines, warnings, path)

    # 헤딩 이후 ~ 같은 레벨 이상의 다음 헤딩 전까지 스캔(원장 안의 소제목 아래 행도 원장이다)
    level = len(lines[heading_idx]) - len(lines[heading_idx].lstrip("#"))
    region = []
    for line in lines[heading_idx + 1:]:
        if HEADING_RE.match(line) and len(line) - len(line.lstrip("#")) <= level:
            break
        region.append(line)

    region_text = "\n".join(region)
    rows = _parse_region_rows(agent, region, heading_idx + 2, warnings)

    if not rows:
        if EMPTY_LEDGER_RE.search(region_text):
            # 유효한 0행 원장
            if valid_empty is not None:
                valid_empty.add(agent)
            return []
        warnings.append(
            f"  [원장 비어있음] {path} — '결함 원장' 헤딩은 있으나 파이프 행도 "
            f"'결함 없음' 표기도 없음. 확인 요망."
        )
    return rows


def _parse_region_rows(agent, region_lines, base_lineno, warnings):
    """region 라인들에서 파이프 원장 행 파싱(펜스·헤더·구분행 스킵)."""
    rows = []
    in_fence = False
    for offset, line in enumerate(region_lines):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if "|" not in line:
            continue
        cells = _split_pipe_row(line)
        if not cells or _is_separator_row(cells) or _is_header_row(cells):
            continue
        lineno = base_lineno + offset
        norm = _normalize_row(cells, warnings, agent, lineno)
        rows.append({
            "agent": agent,
            "id": norm[0],
            "severity": norm[1],
            "axis": norm[2],
            "location": norm[3],
            "oneliner": norm[4],
            "lineno": lineno,
        })
    return rows


def _fallback_parse(agent, lines, warnings, path):
    """헤딩 없을 때: 펜스 안에서 severity 컬럼을 가진 파이프 표를 찾는다."""
    rows = []
    in_fence = False
    for offset, line in enumerate(lines):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence or "|" not in line:
            continue
        cells = _split_pipe_row(line)
        if len(cells) < 2 or _is_separator_row(cells) or _is_header_row(cells):
            continue
        if not _looks_like_severity(cells[1]):
            continue
        norm = _normalize_row(cells, warnings, agent, offset + 1)
        rows.append({
            "agent": agent, "id": norm[0], "severity": norm[1], "axis": norm[2],
            "location": norm[3], "oneliner": norm[4], "lineno": offset + 1,
        })
    if rows:
        warnings.append(
            f"  [fallback 성공] {path} — 펜스 안 표에서 {len(rows)}행 복구. "
            f"헤딩을 '## 결함 원장' 으로 명시할 것."
        )
    return rows


def _load_all(report_paths):
    """리포트 경로 리스트 → (agent별 rows dict, 전체 rows, warnings, valid_empty)."""
    warnings = []
    per_agent = {}
    all_rows = []
    valid_empty = set()
    for p in report_paths:
        path = Path(p)
        rows = parse_ledger(path, warnings, valid_empty)
        per_agent.setdefault(path.stem, [])
        per_agent[path.stem].extend(rows)
        all_rows.extend(rows)
    return per_agent, all_rows, warnings, valid_empty


def _print_warnings(warnings):
    if not warnings:
        return
    print("", file=sys.stderr)
    print("=" * 70, file=sys.stderr)
    print("!!! 경고 (침묵 스킵 금지 — 아래 항목 확인) !!!", file=sys.stderr)
    print("=" * 70, file=sys.stderr)
    for w in warnings:
        print(w, file=sys.stderr)
    print("=" * 70, file=sys.stderr)


def mode_a(report_paths):
    """모드 A — 집계 + join 스켈레톤."""
    per_agent, all_rows, warnings, valid_empty = _load_all(report_paths)

    print("# 종합 join 스켈레톤 (synthesize.py 모드 A)\n")
    print("## agent 별 원장 행 수")
    print()
    print("| agent | 행 수 |")
    print("|---|---|")
    for agent in sorted(per_agent):
        print(f"| {agent} | {len(per_agent[agent])} |")
    print(f"| **합계** | **{len(all_rows)}** |")
    print()

    print("## join 표 스켈레톤 (목적지·처리는 빈칸 — 메인 루프가 채운다)")
    print()
    print("| 원행(agent:id) | sev(원본) | 위치 | 한 줄 | 목적지 | 처리 |")
    print("|---|---|---|---|---|---|")
    for r in all_rows:
        origin = f"{r['agent']}:{r['id']}"
        print(f"| {origin} | {r['severity']} | {r['location']} | "
              f"{r['oneliner']} |  |  |")
    print()
    print(f"> 보존 검사(rule 4): 위 표는 {len(all_rows)}행이어야 한다. "
          f"종합 문서 작성 후 `--check` 로 재검증하라.")

    _print_warnings(warnings)
    # 원장 못 찾은 리포트가 있으면 비정상 종료 — 침묵 통과 금지.
    missing = [Path(p).stem for p in report_paths
               if not per_agent.get(Path(p).stem)
               and Path(p).stem not in valid_empty]
    if missing:
        print(f"\n[FAIL] 원장 0행 리포트: {', '.join(missing)} "
              f"(정말 결함 0이면 리포트에 '결함 없음' 명시)", file=sys.stderr)
        return 1
    return 0


def _table_rows(text):
    """종합 문서의 데이터 파이프 행들(헤더·구분행 제외)을 셀 리스트로."""
    rows = []
    for ln in text.splitlines():
        if "|" not in ln:
            continue
        cells = _split_pipe_row(ln)
        if len(cells) < 2 or _is_separator_row(cells) or _is_header_row(cells):
            continue
        rows.append(cells)
    return rows


def _origin_in_cell(origin, cell):
    """`agent:id` 가 셀에 토큰으로 있는지 — `A:F1` 이 `A:F10` 안에서 매칭되지 않게."""
    return re.search(rf"(?<![\w:-]){re.escape(origin)}(?![\w-])", cell) is not None


def _sev_base(text):
    return re.split(r"[\s(]", text.strip().strip("*`").strip(), maxsplit=1)[0].lower()


def _row_keeps_severity(orig, cells):
    """severity 칸의 등급 토큰이 원본 하나뿐이고, 목적지·처리 칸에 다른 등급이 없는지.
    `Critical → Low`(칸 안 강등)와 처리 칸에 적은 강등을 둘 다 잡는다."""
    if len(cells) < 2:
        return False
    base = _sev_base(orig)
    toks = {t.lower() for t in re.findall(r"[A-Za-z]+", cells[1]) if t.lower() in SEVERITY_TOKENS}
    if toks != {base}:
        return False
    tail = " ".join(cells[4:]) if len(cells) > 4 else ""
    others = {t.lower() for t in re.findall(r"[A-Za-z]+", tail) if t.lower() in SEVERITY_TOKENS}
    return not (others - {base})


PLACEHOLDER = {"", "-", "—", "–", "?", "tbd", "todo", "n/a", "na", "미정", "보류?", "..."}


def _filled(cell):
    return cell.strip().strip("*`").strip().lower() not in PLACEHOLDER


def mode_b(joined_path, report_paths):
    """모드 B — 보존 검사 + 재대조 큐."""
    per_agent, all_rows, warnings, valid_empty = _load_all(report_paths)
    no_ledger = [Path(p).stem for p in report_paths
                 if not per_agent.get(Path(p).stem) and Path(p).stem not in valid_empty]

    jpath = Path(joined_path)
    try:
        joined_text = jpath.read_text(encoding="utf-8")
    except OSError as e:
        print(f"[FAIL] 종합 문서 읽기 실패: {jpath}: {e}", file=sys.stderr)
        return 2

    table = _table_rows(joined_text)
    missing = []
    sev_mismatch = []
    unfilled = []
    for r in all_rows:
        origin = f"{r['agent']}:{r['id']}"
        hosting = [c for c in table if _origin_in_cell(origin, c[0])]
        if not hosting:
            missing.append((origin, r["severity"], r["oneliner"]))
            continue
        sev = r["severity"].strip()
        # 모든 호스팅 행이 원본 등급을 유지해야 한다 — 부록 행 하나가 강등된 행을 가리지 못하게.
        bad = [c for c in hosting if sev and not _row_keeps_severity(sev, c)]
        if bad:
            sev_mismatch.append((origin, sev, " | ".join(bad[0])))
        elif not any(len(c) > 2 and _filled(c[-1]) for c in hosting):
            unfilled.append(origin)

    # 재대조 큐: joined 표에서 처리=merged/rejected 인 행.
    recheck = []
    for ln in joined_text.splitlines():
        if "|" not in ln:
            continue
        cells = _split_pipe_row(ln)
        if len(cells) < 2 or _is_separator_row(cells) or _is_header_row(cells):
            continue
        last = cells[-1].lower()
        if "merged" in last or "rejected" in last:
            recheck.append((cells[0], cells[-1]))

    print("# 보존 검사 결과 (synthesize.py 모드 B)\n")
    print(f"- 리포트 원장 총 행: {len(all_rows)}")
    print(f"- 종합 문서: {jpath}")
    print(f"- 누락(침묵 드롭): {len(missing)}")
    print(f"- severity 불일치(강등 의심): {len(sev_mismatch)}")
    print(f"- 처리 칸 미기입: {len(unfilled)}")
    print(f"- 원장 없는 리포트: {len(no_ledger)}")
    print()

    if missing:
        print("## [FAIL] 누락 행 — 종합 문서에 등장하지 않음")
        for origin, sev, one in missing:
            print(f"- `{origin}` [{sev}] {one}")
        print()
    if sev_mismatch:
        print("## [FAIL] severity 불일치 — 원본 등급이 해당 행에 보존되지 않음")
        for origin, sev, ln in sev_mismatch:
            print(f"- `{origin}` 원본=[{sev}] → 종합 행: {ln}")
        print()
    if unfilled:
        print("## [FAIL] 처리 칸이 비어 있음 — 스켈레톤을 채우지 않은 행")
        for origin in unfilled:
            print(f"- `{origin}`")
        print()
    if no_ledger:
        print("## [FAIL] 원장을 못 찾은 리포트 — 그 리뷰어의 결함이 회계 밖에 있다")
        for agent in no_ledger:
            print(f"- `{agent}` (정말 결함 0이면 리포트에 '결함 없음' 명시)")
        print()

    print("## 적대 재대조 큐 (처리=merged/rejected — 판단 개입 행만, rule 5)")
    if recheck:
        for origin, verdict in recheck:
            print(f"- `{origin}` → {verdict}")
    else:
        print("(merged/rejected 표기 행 없음)")
    print()

    _print_warnings(warnings)

    if missing or sev_mismatch or unfilled or no_ledger:
        print(f"\n[FAIL] 보존 검사 불통과: 누락 {len(missing)}, "
              f"severity 불일치 {len(sev_mismatch)}, 처리 미기입 {len(unfilled)}, "
              f"원장 없음 {len(no_ledger)}. 메꾼 뒤 재실행.",
              file=sys.stderr)
        return 1
    print("[OK] 보존 검사 통과 — 모든 원장 행이 종합 문서에 보존됨.")
    return 0


def _usage():
    print(__doc__)
    print("사용법:")
    print("  모드 A: python3 scripts/synthesize.py report1.md report2.md ...")
    print("  모드 B: python3 scripts/synthesize.py --check joined.md report1.md ...")


def main(argv):
    args = argv[1:]
    if not args or args[0] in ("-h", "--help"):
        _usage()
        return 0
    if args[0] == "--check":
        if len(args) < 3:
            print("[FAIL] --check 는 joined.md 와 리포트 1개 이상이 필요.",
                  file=sys.stderr)
            _usage()
            return 2
        return mode_b(args[1], args[2:])
    return mode_a(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
