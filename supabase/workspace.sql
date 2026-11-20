-- Run in the dedicated gate-schedule Supabase project's SQL Editor.
-- No credentials, no user data and no automatic administrator enrollment.
begin;
create table if not exists public.gate_admin (
  singleton boolean primary key default true check (singleton),
  user_id uuid not null unique references auth.users(id) on delete restrict
);
create table if not exists public.gate_workspace (
  singleton boolean primary key default true check (singleton),
  revision bigint not null check (revision > 0),
  documents jsonb not null,
  updated_at timestamptz not null default now()
);
alter table public.gate_admin enable row level security;
alter table public.gate_workspace enable row level security;
revoke all on public.gate_admin, public.gate_workspace from public, anon, authenticated;

create or replace function public.gate_is_admin()
returns boolean language sql stable security definer set search_path = '' as $$
  select auth.uid() is not null and exists (
    select 1 from public.gate_admin where singleton and user_id = auth.uid()
  );
$$;
revoke all on function public.gate_is_admin() from public, anon;
grant execute on function public.gate_is_admin() to authenticated;
drop policy if exists gate_admin_read on public.gate_workspace;
create policy gate_admin_read on public.gate_workspace for select to authenticated
  using ((select public.gate_is_admin()));
-- No direct write grants or write policies. Only the version-checked RPC writes.

create or replace function public.gate_validate_documents(d jsonb)
returns void language plpgsql set search_path = '' as $$
declare r jsonb; g jsonb; k text; group_id text;
begin
  if d is null or jsonb_typeof(d) <> 'object' or
     (select count(*) from jsonb_object_keys(d)) <> 1 or
     not (d ? 'group-workspaces-v1') or octet_length(d::text) > 8388608 then
    raise exception 'INVALID_WORKSPACE' using errcode = '22023';
  end if;
  r := d->'group-workspaces-v1';
  if jsonb_typeof(r) is distinct from 'object' or
     r->>'formatVersion' is distinct from '3' or
     coalesce(r->>'activeGroupId','') not in ('A','B') or
     jsonb_typeof(r->'revision') is distinct from 'number' or
     (r->>'revision') !~ '^[0-9]+$' or
     jsonb_typeof(r->'groups') is distinct from 'object' or
     jsonb_typeof(r->'shared') is distinct from 'object' then
    raise exception 'INVALID_WORKSPACE_ROOT' using errcode = '22023';
  end if;
  if (select count(*) from jsonb_object_keys(r->'groups')) <> 2 or
     jsonb_typeof(r->'shared'->'flightTemplates') is distinct from 'array' or
     jsonb_typeof(r->'shared'->'weeklyFlightPresets') is distinct from 'object' then
    raise exception 'INVALID_WORKSPACE_SHARED' using errcode = '22023';
  end if;
  foreach group_id in array array['A','B'] loop
    g := r->'groups'->group_id;
    if jsonb_typeof(g) is distinct from 'object' or
       g->>'stateVersion' is distinct from '2' or g->>'timeVersion' is distinct from '2' or
       jsonb_typeof(g->'revision') is distinct from 'number' or
       (g->>'revision') !~ '^[0-9]+$' or not (g ? 'selectedPlanId') then
      raise exception 'INVALID_WORKSPACE_GROUP' using errcode = '22023';
    end if;
    foreach k in array array['people','flightLibrary','handoffPreferences','migrationReport'] loop
      if jsonb_typeof(g->k) is distinct from 'array' then
        raise exception 'INVALID_WORKSPACE_ARRAY' using errcode = '22023';
      end if;
    end loop;
    foreach k in array array['plans','configuration','displayPreferences','balanceSettings',
      'teamLeaderSettings','halfRestSettings','relaxedShiftSettings','dutyRoster'] loop
      if jsonb_typeof(g->k) is distinct from 'object' then
        raise exception 'INVALID_WORKSPACE_OBJECT' using errcode = '22023';
      end if;
    end loop;
    if jsonb_typeof(g->'configuration'->'roleConfigs') is distinct from 'array' then
      raise exception 'INVALID_WORKSPACE_CONFIGURATION' using errcode = '22023';
    end if;
  end loop;
end;
$$;
revoke all on function public.gate_validate_documents(jsonb) from public, anon, authenticated;

create or replace function public.gate_read_workspace()
returns jsonb language plpgsql security definer set search_path = '' as $$
declare w public.gate_workspace%rowtype;
begin
  if not public.gate_is_admin() then
    raise exception 'ADMIN_REQUIRED' using errcode = '42501';
  end if;
  select * into w from public.gate_workspace where singleton;
  if not found then return jsonb_build_object('revision',0,'documents',null); end if;
  perform public.gate_validate_documents(w.documents);
  return jsonb_build_object('revision',w.revision,'documents',w.documents);
end;
$$;

create or replace function public.gate_save_workspace(expected_revision bigint, new_documents jsonb)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare current_revision bigint;
begin
  -- Lock the singleton admin row, also serializing concurrent first-time saves.
  perform 1 from public.gate_admin where singleton and user_id = auth.uid() for update;
  if not found then raise exception 'ADMIN_REQUIRED' using errcode = '42501'; end if;
  perform public.gate_validate_documents(new_documents);
  select revision into current_revision from public.gate_workspace where singleton for update;
  current_revision := coalesce(current_revision,0);
  if expected_revision is null or expected_revision <> current_revision then
    raise exception 'WORKSPACE_CONFLICT' using errcode = '40001';
  end if;
  insert into public.gate_workspace(singleton,revision,documents) values(true,current_revision+1,new_documents)
    on conflict(singleton) do update set revision=excluded.revision, documents=excluded.documents, updated_at=now();
  return jsonb_build_object('revision',current_revision+1);
end;
$$;
revoke all on function public.gate_read_workspace() from public, anon;
revoke all on function public.gate_save_workspace(bigint,jsonb) from public, anon;
-- Re-running base setup must not reopen the old paths after session approval.
do $$ begin
  if to_regprocedure('public.gate_require_session(text)') is null then
    grant execute on function public.gate_read_workspace(), public.gate_save_workspace(bigint,jsonb) to authenticated;
  else
    revoke all on function public.gate_read_workspace(), public.gate_save_workspace(bigint,jsonb) from authenticated;
  end if;
end $$;
commit;

-- AFTER manually creating the sole administrator in Authentication > Users:
-- Replace only the UUID below, then run these two lines separately.
-- insert into public.gate_admin(singleton,user_id)
-- values(true,'ADMIN_AUTH_USER_UUID');
-- Do not use ON CONFLICT UPDATE: re-running setup must never silently change admin.
