#!/usr/bin/env python3
"""Stop hook — 이번 턴에 실패한 툴 호출이 있는데 최종 답변이 그걸 언급하지 않으면 차단한다.

RULES §12 「실패·차단·우회는 성공과 같은 비중으로 보고한다」·「틀린 단정은 정정을 먼저」의 **집행자**.

왜 규칙이 아니라 게이트인가 (실측):
    같은 세션에서 툴 실패 5건 중 4건은 보고하고 1건(권한 거부)을 뺐다. 우회에 성공했으니
    보고할 게 없다고 *내가* 판단한 것인데, 그건 사용자 판단이다. 사용자가 지적하기 전까지
    드러나지 않았고, 세션의 자가 검출률은 0이었다. 규칙 텍스트로 막힐 결함이 아니다.

판정:
    이번 턴(마지막 user 메시지 이후)의 tool_result 중 is_error=True 가 있는데,
    최종 assistant 텍스트에 실패를 가리키는 어휘가 하나도 없으면 block.

🔴 오탐 방지 (오탐은 게이트를 죽인다):
    · stop_hook_active 면 즉시 통과 — 무한 루프 금지
    · 실패 0건이면 통과
    · 어휘가 하나라도 있으면 통과 (형식이 아니라 언급 여부만 본다 — 문체를 강제하지 않는다)
    · 트랜스크립트를 못 읽으면 통과 (fail-open — 게이트가 작업을 막아 세우면 안 된다)
"""
import json
import os
import re
import sys

# 실패를 언급했다고 인정하는 어휘. 넓게 잡는다 — 목적은 문체 강제가 아니라 누락 방지다.
MENTION = re.compile(
    r"실패|차단|거부|막힘|막혔|불가|안 ?됐|안 ?됨|오류|에러|타임아웃|우회|되돌|원복|한계|"
    r"denied|blocked|failed|error|timeout|couldn't|could not|unable",
    re.IGNORECASE,
)

# 앞선 판단을 정정했다고 인정하는 어휘.
CORRECTION = re.compile(
    r"정정|틀렸|틀린|잘못|바로잡|번복|취소|앞서 ?(말|한|드린|보고)|앞선 ?(판단|주장|보고|말)|"
    r"사실과 ?다르|못 ?한다고|안 ?된다고|없다고 ?했|오진|"
    r"correction|corrected|I was wrong|earlier I (said|claimed)|retract",
    re.IGNORECASE,
)

# 🔴 신원이 없는 것은 추적하지 않는다 (첫 실사용에서 오탐이 바로 났다).
#    · 비-Bash 툴(Edit/Write/Read…) — 툴 이름만으로는 모든 호출이 서로 충돌한다.
#      "이 Edit 이 막혔다"가 "모든 Edit 이 막혔다"가 되어 다음 Edit 성공에 발동했다.
#    · 범용 인터프리터 — 스크립트가 stdin·인자에 있어 명령 문자열에 신원이 없다.
#      `python3 -` 로 A 를 돌렸다 막히고 B 를 돌려 성공하면 "번복"이 아니다.
GENERIC_RUNNER = {
    "python", "python3", "node", "bash", "sh", "zsh", "npx", "uvx", "uv",
    "deno", "bun", "ruby", "perl", "awk", "sed", "jq", "echo", "printf",
    "cat", "eval", "xargs",
}
# 되돌릴 수 없는 것 — 여기에 걸리면 차단. 그 외 번복은 알림만(오탐 비용이 크므로).
IRREVERSIBLE = re.compile(
    r"^(?:rm|rmdir|pkill|killall|kill|shred|dd|mkfs|truncate)\b"
    r"|^git\s+(?:push|reset|clean|rebase)\b"
    r"|^(?:brew|npm|pnpm|yarn)\s+(?:uninstall|remove|rm)\b"
)


# 하위 명령이 신원의 일부인 도구 — `git status` 실패와 `git push` 성공은 다른 명령이다.
SUBCOMMAND_TOOLS = {
    "git", "gh", "npm", "pnpm", "yarn", "bun", "brew", "docker", "supabase", "vercel",
    "kubectl", "cargo", "go", "pip", "pip3", "claude",
}


# "막힌다·못 한다"는 판단을 낳는 실패 — 권한·훅 거부. 일반 오류(파일 없음, 네트워크)는 판단이
# 아니라 상황이라, 그 뒤에 다른 대상으로 성공해도 번복이 아니다(3차 리뷰: `rm -rf dist` 가
# '없음'으로 실패한 뒤 `rm -rf node_modules` 성공에 거짓 정정을 요구했다).
# `rejected` 는 git 의 non-fast-forward(`! [rejected]`)처럼 상황 오류에도 나와서 빼고, 훅 거부는
# 훅 오류 표지로만 센다(4차 리뷰: pull --rebase 뒤 같은 push 성공에 하드 차단). 사용자가 승인 프롬프트를
# 거절한 결과("The user doesn't want to proceed…")에는 이 어휘가 없어 거부로 세지 않는다 — 사용자의 결정이지 세션의 단정이 아니다.
DENIAL = re.compile(
    r"permission|denied|not allowed|blocked|refused|hook error|blocked by .{0,20}hook|권한|거부|차단|승인"
    r"|operation not permitted", re.IGNORECASE)
LEAD_STRIP = {"sudo", "doas", "command", "nohup", "time", "env", "nice", "stdbuf"}
LONG_FLAG = {"--force": "f", "--recursive": "r", "--verbose": "v", "--all": "a"}


def _norm_flag(t: str) -> str:
    """`-fr` 와 `-rf` 는 같은 플래그다."""
    return "-" + "".join(sorted(t[1:])) if re.fullmatch(r"-[A-Za-z]{2,}", t) else t


def _segment_shape(seg: str):
    seg = re.sub(r"\d?>>?\s*\S+|<\s*\S+", " ", seg)          # 리다이렉트는 신원이 아니다
    toks = [t for t in re.split(r"\s+", seg.strip()) if t]
    while toks:
        if re.match(r"^[A-Za-z_]\w*=", toks[0]) or os.path.basename(toks[0]) in LEAD_STRIP:
            toks = toks[1:]
        elif os.path.basename(toks[0]) == "timeout" and len(toks) > 1:
            toks = toks[2:]
        else:
            break
    if not toks:
        return None
    head = os.path.basename(toks[0]).lower()
    if head in GENERIC_RUNNER or head == "cd":
        return None
    rest = toks[1:]
    parts = [head]
    if head in SUBCOMMAND_TOOLS:
        # 전역 옵션(`git -C dir`)을 건너뛰고 첫 위치 인자를 하위 명령으로 본다.
        i = 0
        while i < len(rest) and rest[i].startswith("-"):
            i += 2 if rest[i] in ("-C", "-c", "--git-dir", "--work-tree", "--filter", "--dir") else 1
        if i < len(rest):
            parts.append(rest[i])
            rest = rest[i + 1:]
    # `-rf` = `-r -f` = `--recursive --force` — 짧은 플래그는 글자 집합으로, 흔한 긴 플래그는 짧은 것으로
    letters, longs = set(), set()
    for t in rest:
        if re.fullmatch(r"-[A-Za-z]+", t):
            letters.update(t[1:])
        elif t in LONG_FLAG:
            letters.add(LONG_FLAG[t])
        elif t.startswith("--"):
            longs.add(t.split("=", 1)[0])
    flags = (["-" + "".join(sorted(letters))] if letters else []) + sorted(longs)[:2]
    # 위치 인자도 신원이다 — 대상이 다르면 다른 명령이다. `./build/` 와 `build` 는 같은 대상.
    args = sorted(re.sub(r"^\./|/+$", "", t) or t for t in rest if not t.startswith("-"))[:2]
    return " ".join(parts + flags + args)[:80]


def cmd_shapes(tool: str, ti: dict) -> list:
    """추적 가능한 '명령 모양' 목록. `cd x && cmd` 처럼 이어진 명령은 하위 명령마다 본다."""
    if tool != "Bash":
        return []
    c = (ti.get("command") or "").strip()
    return [s for s in (_segment_shape(seg) for seg in re.split(r"&&|\|\|?|;|\n", c)) if s]


def cmd_shape(tool: str, ti: dict):
    """하위 호환 — 첫 하위 명령의 모양."""
    shapes = cmd_shapes(tool, ti)
    return shapes[0] if shapes else None


def blocks(msg):
    c = (msg or {}).get("content")
    return c if isinstance(c, list) else []


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    # 무한 루프 방지 — 이 훅 때문에 재개된 턴은 다시 검사하지 않는다.
    if data.get("stop_hook_active"):
        sys.exit(0)

    path = data.get("transcript_path")
    if not path:
        sys.exit(0)
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            lines = [l for l in f if l.strip().startswith("{")]
    except Exception:
        sys.exit(0)  # fail-open

    # 마지막 "진짜 user 턴"부터 훑는다. tool_result 만 담긴 user 라인은 턴 경계가 아니다.
    start = 0
    for i in range(len(lines) - 1, -1, -1):
        try:
            j = json.loads(lines[i])
        except Exception:
            continue
        if j.get("type") != "user":
            continue
        bs = blocks(j.get("message"))
        only_results = bs and all(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in bs
        )
        if not only_results:
            start = i
            break

    # ── 세션 전체를 훑어 "명령 모양 → 실패했었나"를 만든다.
    #    tool_use_id 로 결과를 호출에 되붙여야 모양을 알 수 있다.
    shape_of, failed_shapes = {}, set()
    for line in lines:
        try:
            j = json.loads(line)
        except Exception:
            continue
        if j.get("type") == "assistant":
            for b in blocks(j.get("message")):
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    shape_of[b.get("id")] = cmd_shapes(b.get("name") or "", b.get("input") or {})
        elif j.get("type") == "user":
            for b in blocks(j.get("message")):
                if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("is_error") \
                        and DENIAL.search(str(b.get("content"))):
                    failed_shapes.update(shape_of.get(b.get("tool_use_id")) or [])

    # ── 이번 턴: 실패 목록 + 성공한 명령 모양 + 최종 텍스트
    failures, succeeded, last_text = [], set(), []
    for line in lines[start:]:
        try:
            j = json.loads(line)
        except Exception:
            continue
        t = j.get("type")
        if t == "user":
            for b in blocks(j.get("message")):
                if not (isinstance(b, dict) and b.get("type") == "tool_result"):
                    continue
                if b.get("is_error"):
                    failures.append(str(b.get("content"))[:120].replace("\n", " "))
                else:
                    succeeded.update(shape_of.get(b.get("tool_use_id")) or [])
        elif t == "assistant":
            texts = [
                b.get("text", "")
                for b in blocks(j.get("message"))
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            if texts:
                last_text = texts  # 매번 갱신 → 최종 assistant 텍스트만 남는다

    # 최종 답변은 stdin 의 last_assistant_message 가 정본이다. transcript 는 비동기로 쓰여
    # 이번 턴 마지막 메시지가 아직 없을 수 있다 — 실패를 보고한 답변을 막는 오탐.
    # https://code.claude.com/docs/en/hooks#common-input-fields
    lam = data.get("last_assistant_message")
    final = lam if isinstance(lam, str) and lam.strip() else " ".join(last_text)

    # ── 게이트 ②: 앞서 실패했던 명령이 이번 턴에 성공했는데 정정이 없다.
    #    "막힌다/못 한다"는 판단이 뒤집힌 것이므로, 결과를 보고하고 끝내면 앞뒤가 달라진다.
    #    제 말을 파싱하지 않는다 — **툴 결과 이력만** 본다(결정론적).
    reversed_ = sorted(failed_shapes & succeeded)
    if reversed_ and not CORRECTION.search(final):
        hard = [s for s in reversed_ if IRREVERSIBLE.search(s)]
        listed = "\n".join(f"  · `{s}` — 이전 실패 → 이번 성공" for s in reversed_[:5])
        body = (
            "앞서 **실패했던** 명령이 이번 턴에 **성공**했는데 정정 언급이 없다 "
            "— RULES §12 「틀린 단정은 결과 보고보다 정정을 먼저 말한다」.\n"
            f"{listed}\n\n"
            '그때 "막힌다·못 한다"고 말했다면 그 판단이 틀렸던 것이다. '
            "**결과만 보고하지 말고 앞선 판단이 틀렸음을 먼저 말하라.** "
            "실행 자체는 문제가 아니다 — 앞뒤가 다른 것이 문제다."
        )
        if hard:
            # 되돌릴 수 없는 명령 — 차단한다.
            print(json.dumps({"decision": "block", "reason": "🚫 " + body}, ensure_ascii=False))
            sys.exit(0)
        # 되돌릴 수 있는 것 — 차단하지 않고 모델에게 피드백만 준다. systemMessage 는 사용자에게만
        # 보이므로 additionalContext 로 보낸다. 피드백 뒤에도 게이트 ①은 계속 검사한다.
        soft_notice = "⚠️ " + body
    else:
        soft_notice = None

    # ── 게이트 ①: 이번 턴 실패를 언급하지 않았다.
    if not failures or MENTION.search(final):
        if soft_notice:
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "Stop", "additionalContext": soft_notice,
            }}, ensure_ascii=False))
        sys.exit(0)

    listed = "\n".join(f"  · {f}" for f in failures[:5])
    more = f"\n  (외 {len(failures) - 5}건)" if len(failures) > 5 else ""
    print(json.dumps({
        "decision": "block",
        "reason": (
            f"🚫 이번 턴에 실패한 툴 호출이 {len(failures)}건 있는데 답변이 언급하지 않았다 "
            f"— RULES §12 「실패·차단·우회는 성공과 같은 비중으로 보고한다」 위반.\n"
            f"{listed}{more}\n\n"
            "우회에 성공했더라도 보고한다. **권한 거부는 특히 사용자가 알아야 할 신호다.**\n"
            "성공 / 실패 / 한계(못 한 것) 3칸을 채워 다시 답하라."
            + (f"\n\n{soft_notice}" if soft_notice else "")
        ),
    }, ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    main()
