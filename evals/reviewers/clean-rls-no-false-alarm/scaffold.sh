#!/usr/bin/env bash
set -euo pipefail
cat > package.json <<'EOF'
{
  "name": "demo",
  "private": true,
  "dependencies": { "react": "^19.0.0" }
}
EOF
mkdir -p src supabase/migrations
echo "export const a = 1" > src/main.ts
cat > supabase/migrations/20260101000000_profiles.sql <<'EOF'
-- rollback: drop trigger on_auth_user_created on auth.users; drop function private.handle_new_user(); drop table public.profiles;
create table public.profiles (
  id uuid primary key references auth.users on delete cascade,
  display_name text,
  role text not null default 'member' check (role in ('member','admin'))
);
alter table public.profiles enable row level security;
create policy "read own" on public.profiles for select to authenticated using (auth.uid() = id);
create policy "update own" on public.profiles for update to authenticated
  using (auth.uid() = id) with check (auth.uid() = id);
-- role 은 열 권한으로 잠근다: 본인도 display_name 만 고칠 수 있다
revoke update on public.profiles from authenticated, anon;
grant select on public.profiles to authenticated;
grant update (display_name) on public.profiles to authenticated;
-- 가입 시 profile 자동 생성 — 클라이언트 INSERT 경로는 일부러 두지 않는다(INFRA 인증 규약)
create schema if not exists private;
revoke all on schema private from public, anon, authenticated;
create or replace function private.handle_new_user()
returns trigger language plpgsql security definer set search_path = '' as $fn$
begin
  insert into public.profiles (id) values (new.id);
  return new;
end $fn$;
create trigger on_auth_user_created after insert on auth.users
  for each row execute function private.handle_new_user();
EOF
