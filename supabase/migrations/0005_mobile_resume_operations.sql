-- Durable resume intents. Apply through the reviewed migration workflow only.
-- This migration does not contact Storage or delete any existing data.
begin;

create table public.mobile_resume_operations (
  id uuid not null,
  user_id uuid not null references public.profiles(user_id) on delete cascade,
  state text not null check (state in ('upload_pending', 'ready', 'delete_pending', 'deleted')),
  resume_data jsonb not null check (jsonb_typeof(resume_data) = 'object' and octet_length(resume_data::text) <= 8192),
  content_sha256 text check (content_sha256 is null or content_sha256 ~ '^[0-9a-f]{64}$'),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (id, user_id)
);

create index mobile_resume_operations_owner_state_idx
  on public.mobile_resume_operations(user_id, state, updated_at desc);
create trigger mobile_resume_operations_set_updated_at before update on public.mobile_resume_operations
  for each row execute function public.set_updated_at();
alter table public.mobile_resume_operations enable row level security;
create policy mobile_resume_operations_owner_only on public.mobile_resume_operations
  for all to authenticated
  using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
revoke all on public.mobile_resume_operations from anon, authenticated;
grant select on public.mobile_resume_operations to authenticated;
grant insert (id, user_id, state, resume_data, content_sha256) on public.mobile_resume_operations to authenticated;
grant update (state) on public.mobile_resume_operations to authenticated;
grant all on public.mobile_resume_operations to service_role;

-- Intents and tombstones must never be silently rewritten by upsert/replay.
create function public.mobile_resume_operation_transition()
returns trigger language plpgsql set search_path = pg_catalog as $$
begin
  if new.id <> old.id or new.user_id <> old.user_id
      or new.resume_data is distinct from old.resume_data
      or new.content_sha256 is distinct from old.content_sha256 then
    raise exception 'Resume operation identity is immutable';
  end if;
  if new.state <> old.state and not (
      (old.state = 'upload_pending' and new.state in ('ready', 'delete_pending'))
      or (old.state = 'ready' and new.state = 'delete_pending')
      or (old.state = 'delete_pending' and new.state = 'deleted')) then
    raise exception 'Invalid resume operation transition';
  end if;
  return new;
end;
$$;
revoke all on function public.mobile_resume_operation_transition() from public, anon, authenticated;
create trigger mobile_resume_operation_transition before update on public.mobile_resume_operations
  for each row execute function public.mobile_resume_operation_transition();

-- A late metadata insert must not resurrect a managed resume after delete intent.
-- Legacy/direct-web rows with no operation remain compatible. RLS is unchanged.
create function public.mobile_resume_prevent_resurrection()
returns trigger language plpgsql set search_path = pg_catalog as $$
begin
  if exists (select 1 from public.mobile_resume_operations o
      where o.id = new.id and o.user_id = new.user_id
      and o.state in ('delete_pending', 'deleted')) then
    raise exception 'Resume deletion is in progress or complete';
  end if;
  return new;
end;
$$;
revoke all on function public.mobile_resume_prevent_resurrection() from public, anon, authenticated;
create trigger mobile_resume_prevent_resurrection before insert on public.resumes
  for each row execute function public.mobile_resume_prevent_resurrection();

commit;
