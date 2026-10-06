#!/usr/bin/env bash
set -euo pipefail
mkdir -p src
cat > package.json <<'EOF'
{
  "name": "demo",
  "private": true,
  "dependencies": { "react": "^19.0.0" }
}
EOF
echo "export const a = 1" > src/main.ts
mkdir -p supabase/migrations
cat > supabase/migrations/20260101000000_profiles.sql <<'EOF'
create table public.profiles (
  id uuid primary key references auth.users on delete cascade,
  display_name text,
  role text not null default 'member' check (role in ('member','admin'))
);
alter table public.profiles enable row level security;
create policy "read own" on public.profiles for select using (auth.uid() = id);
create policy "update own" on public.profiles for update using (auth.uid() = id) with check (true);
-- rollback: drop table public.profiles;
EOF
