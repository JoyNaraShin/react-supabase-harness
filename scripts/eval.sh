#!/usr/bin/env bash
# 하네스 eval 실행 — with(플러그인) / without(기준선) 두 arm 을 돌리고, 결과 옆에 출처(meta.json)를 남긴다.
# 모델 비용이 들고 사용자 플랜 사용량을 쓴다. PR 마다 돌리지 않는다.
#
#   scripts/eval.sh                          # 세션 기본 모델, 케이스당 3런
#   MODEL=haiku RUNS=3 scripts/eval.sh       # 하위 모델 축
#   CASE=destructive-force-push scripts/eval.sh
#   TAG=overblock scripts/eval.sh            # 축 하나만(efficacy | organic | skill | overblock)
#   MAX_COST=5 scripts/eval.sh               # 비용 상한(USD) — 넘으면 부분 결과로 종료(exit 2)
#   OUT=evals/results/batch1-haiku scripts/eval.sh   # 결과 파일 이름(확장자 제외) 지정 — 끊긴 배치를 이어 붙일 때
#
# 출처: <결과>.meta.json 에 git SHA·작업 트리 dirty 여부·플러그인 버전·모델·runs·Claude Code 버전을 쓴다.
# 공개본(evals/published/)은 meta 가 모두 같은 SHA 의 clean 트리일 때만 만들어진다(scripts/eval-report.py --publish).
set -euo pipefail
cd "$(dirname "$0")/.."

args=(plugin eval . --scaffold --trust-plugin --no-publish
      --allow-tools Bash Write Edit Agent
      --runs "${RUNS:-3}" --threshold "${THRESHOLD:-0}")
[ -n "${MODEL:-}" ]    && args+=(--model "$MODEL")
[ -n "${CASE:-}" ]     && args+=(--case "$CASE")
[ -n "${TAG:-}" ]      && args+=(--tag "$TAG")
[ -n "${MAX_COST:-}" ] && args+=(--max-cost-usd "$MAX_COST")
[ -n "${KEEP:-}" ]     && args+=(--keep-temp)

mkdir -p evals/results
out="${OUT:-evals/results/$(date -u +%Y%m%dT%H%M%SZ)-${MODEL:-default}}.json"

# 출처는 실행 전에 찍는다 — 도는 동안 트리가 바뀌면 실행 후 값은 결과와 맞지 않는다.
porcelain="$(git status --porcelain --untracked-files=normal -- . ':!evals/results' ':!evals/published')"
python3 - "$out" "$porcelain" "${MODEL:-default}" "${RUNS:-3}" "${CASE:-}" "${TAG:-}" <<'PY'
import hashlib, json, subprocess, sys, datetime
out, porcelain, model, runs, case, tag = sys.argv[1:]
sh = lambda *c: subprocess.run(c, capture_output=True, text=True).stdout.strip()
meta = {
    "sha": sh("git", "rev-parse", "HEAD"),
    "dirty": bool(porcelain),
    "dirtyHash": hashlib.sha256(porcelain.encode()).hexdigest()[:12] if porcelain else None,
    "pluginVersion": json.load(open(".claude-plugin/plugin.json"))["version"],
    "model": model, "runs": int(runs), "case": case or None, "tag": tag or None,
    "claudeVersion": sh("claude", "--version"),
    "startedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
}
json.dump(meta, open(out[:-5] + ".meta.json", "w"), ensure_ascii=False, indent=1)
if meta["dirty"]:
    print("⚠ 작업 트리가 dirty — 이 결과는 공개본에 들어가지 않는다(meta.dirty=true)", file=sys.stderr)
PY

claude "${args[@]}" --json "$out"
echo "결과: $out"
