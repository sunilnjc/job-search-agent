-- Artifact intents preserve bytes when metadata/audit acknowledgements are lost.
-- No hosted execution, object deletion, or preexisting row mutation.
begin;
create table public.mobile_artifact_operations (
  id uuid not null,
  user_id uuid not null references public.profiles(user_id) on delete cascade,
  state text not null check (state in ('upload_pending', 'ready')),
  artifact_data jsonb not null check (jsonb_typeof(artifact_data) = 'object' and octet_length(artifact_data::text) <= 8192),
  content_sha256 text not null check (content_sha256 ~ '^[0-9a-f]{64}$'),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (id, user_id)
);
create index mobile_artifact_operations_owner_state_idx
  on public.mobile_artifact_operations(user_id, state, updated_at desc);
create trigger mobile_artifact_operations_set_updated_at before update on public.mobile_artifact_operations
  for each row execute function public.set_updated_at();
alter table public.mobile_artifact_operations enable row level security;
create policy mobile_artifact_operations_owner_only on public.mobile_artifact_operations
  for all to authenticated
  using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
revoke all on public.mobile_artifact_operations from anon, authenticated;
grant select on public.mobile_artifact_operations to authenticated;
grant insert (id, user_id, state, artifact_data, content_sha256) on public.mobile_artifact_operations to authenticated;
grant update (state) on public.mobile_artifact_operations to authenticated;
grant all on public.mobile_artifact_operations to service_role;

create function public.mobile_artifact_operation_transition()
returns trigger language plpgsql set search_path = pg_catalog as $$
begin
  if new.id <> old.id or new.user_id <> old.user_id
      or new.artifact_data is distinct from old.artifact_data
      or new.content_sha256 is distinct from old.content_sha256
      or (new.state <> old.state and not (old.state = 'upload_pending' and new.state = 'ready')) then
    raise exception 'Invalid artifact operation transition';
  end if;
  return new;
end;
$$;
revoke all on function public.mobile_artifact_operation_transition() from public, anon, authenticated;
create trigger mobile_artifact_operation_transition before update on public.mobile_artifact_operations
  for each row execute function public.mobile_artifact_operation_transition();
commit;
