#!/usr/bin/env bash
set -euo pipefail
cat > package.json <<'EOF'
{
  "name": "demo",
  "private": true,
  "dependencies": { "react": "^19.0.0" }
}
EOF
mkdir -p src supabase/migrations docs/plans
echo "export const a = 1" > src/main.ts
printf '# Phase 1\n\n## Story 2: 글 주소\nacceptance: 모든 글이 고유 slug 를 가진다\n' > docs/plans/phase-1-posts.md
cat > supabase/migrations/20260101000000_posts.sql <<'EOF'
-- rollback: drop table public.posts;
create table public.posts (
  id uuid primary key default gen_random_uuid(),
  author_id uuid not null references auth.users on delete cascade,
  title text not null,
  created_at timestamptz not null default now()
);
alter table public.posts enable row level security;
create policy "read" on public.posts for select to authenticated using (true);
EOF
