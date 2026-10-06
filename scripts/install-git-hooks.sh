#!/bin/sh
# 하네스 git 훅(pre-push)을 현재 저장소에 설치한다. 사용자가 프로젝트 루트에서 한 번 실행한다.
#   sh "$CLAUDE_PLUGIN_ROOT/scripts/install-git-hooks.sh"     (또는 레포 경로로 직접)
# core.hooksPath(husky 등)를 존중한다 — `git rev-parse --git-path hooks` 가 실제 훅 디렉터리를 준다.
# 기존 pre-push 는 지우지 않고 pre-push.local 로 옮겨, 하네스 훅이 먼저 그것을 실행한다.
set -e
src="$(cd "$(dirname "$0")/.." && pwd)/hooks/git/pre-push"
dir=$(git rev-parse --git-path hooks)
mkdir -p "$dir"
dst="$dir/pre-push"
if [ -f "$dst" ] && ! grep -q "react-supabase-harness pre-push" "$dst"; then
  if [ -e "$dir/pre-push.local" ]; then
    echo "중단: $dir/pre-push 와 pre-push.local 이 이미 있다. 직접 합친 뒤 다시 실행하라." >&2
    exit 1
  fi
  mv "$dst" "$dir/pre-push.local"
  echo "기존 pre-push → pre-push.local (하네스 훅이 먼저 실행한다)"
fi
cp "$src" "$dst"
chmod +x "$dst"
echo "설치: $dst"
