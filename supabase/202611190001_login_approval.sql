-- Gate project only. Apply after workspace.sql, before publishing the new UI.
-- Re-running preserves workspace contents, administrator, leases and requests.
begin;
do $$ begin
  if to_regclass('public.gate_admin') is null or to_regclass('public.gate_workspace') is null
    or to_regprocedure('public.gate_save_workspace(bigint,jsonb)') is null
    or to_regprocedure('public.gate_read_workspace()') is null then
    raise exception 'Expected the existing gate-schedule workspace database. Install workspace.sql first.';
  end if;
end $$;
create table if not exists public.gate_editor_session (
  singleton boolean primary key default true check(singleton),
  session_id text not null,
  expires_at timestamptz not null
);
create table if not exists public.gate_login_requests (
  request_id text primary key,
  requester text not null,
  holder text not null,
  status text not null check(status in ('pending','approved','rejected','expired','cancelled')),
  expires_at timestamptz not null
);
create unique index if not exists gate_one_pending_login on public.gate_login_requests(status) where status='pending';
alter table public.gate_editor_session enable row level security;
alter table public.gate_login_requests enable row level security;
revoke all on public.gate_editor_session, public.gate_login_requests from public, anon, authenticated;

create or replace function public.gate_lock_session(p_session text) returns void
language plpgsql security definer set search_path='' as $$
begin
  perform 1 from public.gate_admin where singleton and user_id=auth.uid() for update;
  if not found then raise exception 'ADMIN_REQUIRED' using errcode='42501'; end if;
  if p_session is null or char_length(p_session) not between 16 and 200 then raise exception 'SESSION_INVALID'; end if;
end;
$$;
create or replace function public.gate_require_session(p_session text) returns void
language plpgsql security definer set search_path='' as $$
begin
  perform public.gate_lock_session(p_session);
  if not exists(select 1 from public.gate_editor_session where singleton and session_id=p_session and expires_at>now()) then
    raise exception 'SESSION_EXPIRED';
  end if;
end;
$$;
create or replace function public.gate_request_json(r public.gate_login_requests) returns jsonb
language sql immutable set search_path='' as $$
  select jsonb_build_object('id',r.request_id,'status',r.status);
$$;
create or replace function public.gate_acquire_session(p_session text) returns void
language plpgsql security definer set search_path='' as $$
declare lease public.gate_editor_session%rowtype;
begin
  perform public.gate_lock_session(p_session);
  select * into lease from public.gate_editor_session where singleton;
  if found and lease.session_id<>p_session and lease.expires_at>now() then raise exception 'SESSION_ACTIVE'; end if;
  update public.gate_login_requests set status='expired' where status='pending'
    and (expires_at<=now() or lease.expires_at<=now() or holder<>p_session);
  insert into public.gate_editor_session values(true,p_session,now()+interval '90 seconds')
    on conflict(singleton) do update set session_id=excluded.session_id,expires_at=excluded.expires_at;
end;
$$;
create or replace function public.gate_touch_session(p_session text) returns void
language plpgsql security definer set search_path='' as $$
begin
  perform public.gate_require_session(p_session);
  update public.gate_editor_session set expires_at=now()+interval '90 seconds' where singleton;
end;
$$;
create or replace function public.gate_release_session(p_session text) returns void
language plpgsql security definer set search_path='' as $$
begin
  perform public.gate_lock_session(p_session);
  delete from public.gate_editor_session where singleton and session_id=p_session;
end;
$$;
create or replace function public.gate_poll_login(p_session text,p_request text default null) returns jsonb
language plpgsql security definer set search_path='' as $$
declare r public.gate_login_requests%rowtype;
declare lease public.gate_editor_session%rowtype;
begin
  perform public.gate_lock_session(p_session);
  select * into lease from public.gate_editor_session where singleton;
  if p_request is null then
    perform public.gate_require_session(p_session);
    select * into r from public.gate_login_requests where holder=p_session and status='pending';
  else
    select * into r from public.gate_login_requests where request_id=p_request and requester=p_session;
    if not found then raise exception 'SESSION_REQUEST_INVALID'; end if;
  end if;
  if r.request_id is null then return null; end if;
  if r.status='pending' and (r.expires_at<=now() or lease.session_id is null or lease.session_id<>r.holder or lease.expires_at<=now()) then
    update public.gate_login_requests set status='expired' where request_id=r.request_id returning * into r;
  end if;
  return public.gate_request_json(r);
end;
$$;
create or replace function public.gate_request_login(p_session text,p_request text) returns jsonb
language plpgsql security definer set search_path='' as $$
declare r public.gate_login_requests%rowtype;
declare lease public.gate_editor_session%rowtype;
begin
  perform public.gate_lock_session(p_session);
  if p_request is null or char_length(p_request) not between 16 and 200 then raise exception 'SESSION_REQUEST_INVALID'; end if;
  select * into r from public.gate_login_requests where request_id=p_request;
  if found then
    if r.requester<>p_session then raise exception 'SESSION_REQUEST_INVALID'; end if;
    return public.gate_poll_login(p_session,p_request);
  end if;
  select * into lease from public.gate_editor_session where singleton;
  if not found or lease.expires_at<=now() or lease.session_id=p_session then raise exception 'SESSION_REQUEST_CLOSED'; end if;
  delete from public.gate_login_requests where expires_at<now()-interval '1 day';
  update public.gate_login_requests set status='expired' where status='pending' and (expires_at<=now() or holder<>lease.session_id);
  if exists(select 1 from public.gate_login_requests where status='pending') then raise exception 'SESSION_REQUEST_BUSY'; end if;
  insert into public.gate_login_requests values(p_request,p_session,lease.session_id,'pending',now()+interval '60 seconds') returning * into r;
  return public.gate_request_json(r);
end;
$$;
create or replace function public.gate_decide_login(p_session text,p_request text,p_allow boolean) returns void
language plpgsql security definer set search_path='' as $$
declare r public.gate_login_requests%rowtype;
begin
  perform public.gate_require_session(p_session);
  select * into r from public.gate_login_requests where request_id=p_request;
  if not found or r.holder<>p_session or r.status<>'pending' or r.expires_at<=now() or p_allow is null then raise exception 'SESSION_REQUEST_CLOSED'; end if;
  if p_allow then update public.gate_editor_session set session_id=r.requester,expires_at=now()+interval '90 seconds' where singleton; end if;
  update public.gate_login_requests set status=case when p_allow then 'approved' else 'rejected' end where request_id=p_request;
end;
$$;
create or replace function public.gate_cancel_login(p_session text,p_request text) returns void
language plpgsql security definer set search_path='' as $$
begin
  perform public.gate_lock_session(p_session);
  update public.gate_login_requests set status='cancelled' where request_id=p_request and requester=p_session and status='pending';
end;
$$;

-- Keep original document validation and CAS in their existing owner functions.
-- Old clients lose direct RPC access as soon as this upgrade is installed.
create or replace function public.gate_read_workspace_session(p_session text) returns jsonb
language plpgsql security definer set search_path='' as $$
begin
  perform public.gate_require_session(p_session);
  return public.gate_read_workspace();
end;
$$;
create or replace function public.gate_save_workspace_session(p_session text,expected_revision bigint,new_documents jsonb) returns jsonb
language plpgsql security definer set search_path='' as $$
begin
  perform public.gate_require_session(p_session);
  return public.gate_save_workspace(expected_revision,new_documents);
end;
$$;
revoke all on function public.gate_read_workspace(), public.gate_save_workspace(bigint,jsonb) from public, anon, authenticated;
revoke all on function public.gate_lock_session(text),public.gate_require_session(text),public.gate_request_json(public.gate_login_requests) from public,anon,authenticated;
revoke all on function public.gate_acquire_session(text),public.gate_touch_session(text),public.gate_release_session(text),public.gate_poll_login(text,text),public.gate_request_login(text,text),public.gate_decide_login(text,text,boolean),public.gate_cancel_login(text,text),public.gate_read_workspace_session(text),public.gate_save_workspace_session(text,bigint,jsonb) from public,anon,authenticated;
grant execute on function public.gate_acquire_session(text),public.gate_touch_session(text),public.gate_release_session(text),public.gate_poll_login(text,text),public.gate_request_login(text,text),public.gate_decide_login(text,text,boolean),public.gate_cancel_login(text,text),public.gate_read_workspace_session(text),public.gate_save_workspace_session(text,bigint,jsonb) to authenticated;
notify pgrst,'reload schema';
commit;
