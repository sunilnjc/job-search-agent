-- Apply only after 0001..0009 in a reviewed test database first. No accounts are
-- deleted by this migration. A separately configured worker is REQUIRED.
-- No client service-role key, arbitrary user selector, or direct DELETE grant.
begin;

create table public.mobile_privacy_requests (
  id uuid primary key default gen_random_uuid(),
  user_id uuid, -- intentionally survives Auth deletion until anonymous completion
  kind text not null check (kind in ('export','erase')),
  state text not null default 'queued' check (state in ('queued','processing','blocked','complete','failed')),
  created_at timestamptz not null default clock_timestamp(),
  completed_at timestamptz,
  lease_token uuid, lease_until timestamptz,
  error_code text check (error_code in ('billing_blocked','storage_scope','too_large','upstream_unavailable','invalid_snapshot')),
  export_path text, expires_at timestamptz
);
create unique index mobile_privacy_one_pending on public.mobile_privacy_requests(user_id,kind)
  where state in ('queued','processing','blocked');
create table public.mobile_privacy_worker_health (
  singleton boolean primary key default true check (singleton),
  seen_at timestamptz not null
);
alter table public.mobile_privacy_requests enable row level security;
alter table public.mobile_privacy_worker_health enable row level security;
revoke all on public.mobile_privacy_requests,public.mobile_privacy_worker_health from public,anon,authenticated;
grant all on public.mobile_privacy_requests,public.mobile_privacy_worker_health to service_role;

create function public.mobile_privacy_live_user() returns boolean
language sql stable security definer set search_path='' as $$
  select auth.role()='authenticated' and exists(select 1 from auth.users
    where id=auth.uid() and email_confirmed_at is not null);
$$;
create function public.mobile_privacy_worker_ready() returns boolean
language sql stable security definer set search_path='' as $$
  select exists(select 1 from public.mobile_privacy_worker_health
    where seen_at > statement_timestamp()-interval '5 minutes');
$$;
create function public.mobile_privacy_status() returns jsonb
language plpgsql security definer set search_path='' as $$
declare v_rows jsonb;
begin
  if not public.mobile_privacy_live_user() then
    return jsonb_build_object('user_id',auth.uid(),'error','unverified');
  end if;
  select coalesce(jsonb_agg(to_jsonb(q) order by created_at desc),'[]') into v_rows from
    (select id,kind,state,created_at,completed_at,expires_at,export_path
       from public.mobile_privacy_requests where user_id=auth.uid()
       order by created_at desc limit 50) q;
  return jsonb_build_object('user_id',auth.uid(),'requests',v_rows,
    'worker_ready',public.mobile_privacy_worker_ready());
end; $$;

create function public.mobile_privacy_request(p_kind text,p_confirmation text default null,p_email text default null)
returns jsonb language plpgsql security definer set search_path='' as $$
declare v_user uuid:=auth.uid(); v_row public.mobile_privacy_requests%rowtype;
begin
  if not public.mobile_privacy_live_user() then
    return jsonb_build_object('user_id',v_user,'error','unverified');
  end if;
  if p_kind is null or p_kind not in ('export','erase') then raise exception 'Invalid request'; end if;
  perform pg_advisory_xact_lock(hashtextextended(v_user::text,1010));
  -- Auth may have disappeared while this request waited for the user lock.
  perform 1 from auth.users where id=v_user and email_confirmed_at is not null for key share;
  if not found then return jsonb_build_object('user_id',v_user,'error','unverified'); end if;
  if p_kind='erase' and (p_confirmation is distinct from 'DELETE MY ACCOUNT' or
      not exists(select 1 from auth.users where id=v_user and lower(email)=lower(btrim(p_email)))) then
    return jsonb_build_object('user_id',v_user,'error','confirmation');
  end if;
  select * into v_row from public.mobile_privacy_requests where user_id=v_user
    and kind=p_kind and state in ('queued','processing','blocked') order by created_at desc limit 1;
  if found then return jsonb_build_object('user_id',v_user,'request',
    to_jsonb(v_row)-array['lease_token','lease_until','error_code','user_id']); end if;
  if exists(select 1 from public.mobile_privacy_requests where user_id=v_user and kind='erase') then
    return jsonb_build_object('user_id',v_user,'error','erasing');
  end if;
  if not public.mobile_privacy_worker_ready() then
    return jsonb_build_object('user_id',v_user,'error','worker_unavailable');
  end if;
  if p_kind='export' and exists(select 1 from public.mobile_privacy_requests where user_id=v_user
      and kind='export' and created_at > clock_timestamp()-interval '1 day' and state not in ('failed','blocked')) then
    return jsonb_build_object('user_id',v_user,'error','rate_limited');
  end if;
  -- Lock the authoritative member row before revoking; reservations use the same
  -- row lock. In-flight provider work may finish but new user writes fail RLS.
  if p_kind='erase' then
    update public.mobile_usage_memberships set enabled=false where user_id=v_user;
    -- The user lock is also taken by export claims and the Storage write fence.
    -- A worker may still hold ZIP bytes in memory; revoke its capability here,
    -- not merely its right to report completion after a late upload.
    update public.mobile_privacy_requests set state='failed',lease_token=null,
      lease_until=null,error_code=null where user_id=v_user and kind='export'
      and state in ('queued','processing','blocked');
  end if;
  insert into public.mobile_privacy_requests(user_id,kind) values(v_user,p_kind) returning * into v_row;
  return jsonb_build_object('user_id',v_user,'request',
    to_jsonb(v_row)-array['lease_token','lease_until','error_code','user_id']);
end; $$;

-- Erasure is a durable tombstone, not merely a toggle an entitlement renewal can
-- reverse. Old JWTs also cannot recreate a profile after Auth hard deletion.
create or replace function public.mobile_has_access() returns boolean
language sql stable security definer set search_path='' as $$
  select public.mobile_privacy_live_user() and exists(select 1 from public.mobile_usage_memberships m
    where m.user_id=auth.uid() and m.enabled and (m.expires_at is null or m.expires_at>statement_timestamp()))
    and not exists(select 1 from public.mobile_privacy_requests r where r.user_id=auth.uid() and r.kind='erase');
$$;
create function public.mobile_privacy_freeze_membership() returns trigger
language plpgsql security definer set search_path='' as $$
begin
  if exists(select 1 from public.mobile_privacy_requests where user_id=new.user_id and kind='erase') then
    new.enabled:=false;
  end if;
  return new;
end; $$;
create trigger mobile_privacy_membership_freeze before insert or update on public.mobile_usage_memberships
  for each row execute function public.mobile_privacy_freeze_membership();

create function public.mobile_privacy_heartbeat() returns void
language plpgsql security definer set search_path='' as $$
begin
  insert into public.mobile_privacy_worker_health(singleton,seen_at) values(true,clock_timestamp())
    on conflict(singleton) do update set seen_at=excluded.seen_at;
  delete from public.mobile_privacy_requests where user_id is null
    and completed_at<statement_timestamp()-interval '7 days';
end;
$$;
create function public.mobile_privacy_claim(p_request_id uuid default null) returns jsonb
language plpgsql security definer set search_path='' as $$
declare v_row public.mobile_privacy_requests%rowtype; v_candidate record;
begin
  -- Always acquire user lock BEFORE queue row lock: request/finish/fence use the
  -- same order. Skip busy users so one long upload cannot stall the whole queue.
  for v_candidate in select r.id,r.user_id from public.mobile_privacy_requests r
      where r.user_id is not null and (p_request_id is null or r.id=p_request_id)
      and (r.state='queued' or (r.state='processing' and r.lease_until<clock_timestamp())
        or (p_request_id is not null and r.state='blocked'))
      and (r.kind<>'export' or not exists(select 1 from public.mobile_privacy_requests e
        where e.user_id=r.user_id and e.kind='erase'))
      order by r.created_at,r.id limit 100 loop
    if not pg_try_advisory_xact_lock(hashtextextended(v_candidate.user_id::text,1010)) then continue; end if;
    -- Re-read under the lock, rather than trusting the candidate query snapshot.
    select * into v_row from public.mobile_privacy_requests r where r.id=v_candidate.id
      and r.user_id=v_candidate.user_id
      and (r.state='queued' or (r.state='processing' and r.lease_until<clock_timestamp())
        or (p_request_id is not null and r.state='blocked'))
      and (r.kind<>'export' or not exists(select 1 from public.mobile_privacy_requests e
        where e.user_id=r.user_id and e.kind='erase'))
      for update skip locked;
    if not found then continue; end if;
    update public.mobile_privacy_requests set state='processing',lease_token=gen_random_uuid(),
      lease_until=clock_timestamp()+interval '20 minutes',error_code=null where id=v_row.id returning * into v_row;
    return to_jsonb(v_row);
  end loop;
  return null;
end; $$;

create function public.mobile_privacy_snapshot(p_request_id uuid,p_lease_token uuid) returns jsonb
language plpgsql security definer set search_path='' as $$
declare v_user uuid; v_name text; v_count bigint; v_rows jsonb; v_tables jsonb:='{}'; v_files jsonb; v_auth jsonb;
begin
  select user_id into v_user from public.mobile_privacy_requests where id=p_request_id and lease_token=p_lease_token
    and state='processing' and lease_until>clock_timestamp();
  if v_user is null then raise exception 'Invalid lease'; end if;
  -- Fixed reviewed table set. Never export auth.identities/sessions/token hashes,
  -- provider payloads, other users, or privileged billing identifiers.
  foreach v_name in array array['profiles','candidate_context','job_preferences','resumes','jobs','job_scores',
      'artifacts','applications','application_attempts','mobile_questions','mobile_answers','model_runs',
      'mobile_resume_operations','mobile_artifact_operations','mobile_usage_memberships','mobile_ai_daily_usage',
      'mobile_ai_reservations','mobile_billing_export'] loop
    execute format('select count(*) from public.%I where user_id=$1',v_name) into v_count using v_user;
    if v_count>5000 then raise exception 'Privacy export row limit'; end if;
    execute format('select coalesce(jsonb_agg(to_jsonb(t)),''[]'') from public.%I t where user_id=$1',v_name)
      into v_rows using v_user;
    v_tables:=v_tables||jsonb_build_object(v_name,v_rows);
    if octet_length(v_tables::text)>16777216 then raise exception 'Privacy export byte limit'; end if;
  end loop;
  select jsonb_build_object('id',id,'email',email,'phone',phone,'created_at',created_at,
    'email_confirmed_at',email_confirmed_at) into v_auth from auth.users where id=v_user;
  -- Include orphans and recovery journals, not only files with a resume row.
  -- Unexpected owned objects outside managed prefixes are returned and blocked by
  -- the worker for operator review, never blindly removed from another bucket.
  select count(*) into v_count from storage.objects where owner_id=v_user::text
    or (bucket_id in ('resumes','application-artifacts','account-exports') and split_part(name,'/',1)=v_user::text);
  if v_count>2000 then raise exception 'Privacy object limit'; end if;
  select coalesce(jsonb_agg(jsonb_build_object('bucket',bucket_id,'path',name,'owner_id',owner_id)),'[]') into v_files from storage.objects
    where owner_id=v_user::text or (bucket_id in ('resumes','application-artifacts','account-exports')
      and split_part(name,'/',1)=v_user::text);
  return jsonb_build_object('user_id',v_user,'account',v_auth,'tables',v_tables,'objects',v_files);
end; $$;

create function public.mobile_privacy_finish(p_request_id uuid,p_lease_token uuid,p_state text,
  p_error_code text default null,p_export_path text default null) returns boolean
language plpgsql security definer set search_path='' as $$
declare v_row public.mobile_privacy_requests%rowtype; v_user uuid;
begin
  select user_id into v_user from public.mobile_privacy_requests where id=p_request_id;
  if v_user is null then return false; end if;
  perform pg_advisory_xact_lock(hashtextextended(v_user::text,1010));
  select * into v_row from public.mobile_privacy_requests where id=p_request_id and lease_token=p_lease_token
    and state='processing' and lease_until>clock_timestamp() for update;
  if not found then return false; end if;
  if p_state not in ('complete','blocked','failed') then raise exception 'Invalid state'; end if;
  if p_state='complete' and v_row.kind='erase' then
    if exists(select 1 from auth.users where id=v_row.user_id) or exists(select 1 from storage.objects
      where owner_id=v_row.user_id::text or (bucket_id in ('resumes','application-artifacts','account-exports')
      and split_part(name,'/',1)=v_row.user_id::text)) then raise exception 'Erasure incomplete'; end if;
    -- Erasure receipts retain no subject identifier, filename, content, or email.
    delete from public.mobile_privacy_requests where user_id=v_row.user_id and id<>v_row.id;
    update public.mobile_privacy_requests set user_id=null where id=v_row.id;
  elsif p_state='complete' and v_row.kind='export' then
    if p_export_path is distinct from v_row.user_id::text||'/'||v_row.id::text||'/account.zip' then
      raise exception 'Invalid export path'; end if;
  end if;
  update public.mobile_privacy_requests set state=p_state,error_code=p_error_code,
    completed_at=case when p_state='complete' then clock_timestamp() else null end,
    export_path=p_export_path,expires_at=case when v_row.kind='export' and p_state='complete'
      then clock_timestamp()+interval '7 days' else null end,lease_token=null,lease_until=null where id=v_row.id;
  return true;
end; $$;

create function public.mobile_privacy_export_paths() returns table(path text)
language sql stable security definer set search_path='' as $$
  select export_path from public.mobile_privacy_requests where user_id=auth.uid()
    and public.mobile_privacy_live_user() and kind='export' and state='complete'
    and expires_at>statement_timestamp() and export_path is not null;
$$;
create function public.mobile_privacy_expired_exports() returns jsonb
language sql security definer set search_path='' as $$
  select coalesce(jsonb_agg(to_jsonb(q)),'[]') from (select id,user_id,export_path from public.mobile_privacy_requests
    where kind='export' and export_path is not null and expires_at<statement_timestamp() limit 100) q;
$$;
create function public.mobile_privacy_prune(p_request_id uuid) returns void
language plpgsql security definer set search_path='' as $$
begin
  update public.mobile_privacy_requests set export_path=null where id=p_request_id and kind='export'
    and expires_at<statement_timestamp() and not exists(select 1 from storage.objects
      where bucket_id='account-exports' and name=export_path);
  -- Anonymous operational receipts have a finite retention window too.
  delete from public.mobile_privacy_requests where user_id is null and completed_at<statement_timestamp()-interval '7 days';
end; $$;

insert into storage.buckets(id,name,public,file_size_limit,allowed_mime_types)
  values('account-exports','account-exports',false,67108864,array['application/zip'])
  on conflict(id) do update set public=false,file_size_limit=excluded.file_size_limit,allowed_mime_types=excluded.allowed_mime_types;

-- Authoritative metadata fence, including Storage's privileged completion path.
-- Current upstream src/storage/uploader.ts passes decoded x-metadata into
-- user_metadata BOTH in canUpload's rolled-back permission INSERT (version '1',
-- possibly null/partial system metadata) and completeUpload's superuser upsert.
-- Test that contract on the deployed Storage version before rollout. A missing
-- privacy_lease fails closed even for a placeholder; never add a bypass flag.
-- This rejects late METADATA publication, not proof of S3 byte erasure: upstream
-- may upload a version between these two SQL transactions and then enqueue an
-- ObjectAdminDelete on final-write failure. Hosted cleanup must be verified.
create function public.mobile_privacy_export_storage_fence() returns trigger
language plpgsql security definer set search_path='' as $$
declare v_user uuid; v_request uuid; v_lease text;
begin
  -- Do not permit moving an existing export into an unfenced/public bucket.
  if tg_op='UPDATE' and old.bucket_id='account-exports' and
      (new.bucket_id is distinct from old.bucket_id or new.name is distinct from old.name) then
    raise exception 'Invalid privacy export write' using errcode='42501';
  end if;
  if new.bucket_id is distinct from 'account-exports' then return new; end if;
  if new.name is null or new.name !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/account[.]zip$'
      or jsonb_typeof(new.user_metadata) is distinct from 'object'
      or jsonb_typeof(new.user_metadata->'privacy_lease') is distinct from 'string' then
    raise exception 'Invalid privacy export write' using errcode='42501';
  end if;
  v_user:=split_part(new.name,'/',1)::uuid;
  v_request:=split_part(new.name,'/',2)::uuid;
  v_lease:=new.user_metadata->>'privacy_lease';
  if (new.owner_id is not null and new.owner_id<>'' and new.owner_id<>v_user::text)
      or (new.owner is not null and new.owner<>v_user) then
    raise exception 'Invalid privacy export write' using errcode='42501';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(v_user::text,1010));
  -- Hold Auth presence through this storage transaction, not a TOCTOU exists().
  -- After anonymous receipt pruning, Auth absence remains the durable fence.
  perform 1 from auth.users where id=v_user for key share;
  if not found or exists(select 1 from public.mobile_privacy_requests where user_id=v_user and kind='erase')
      or not exists(select 1 from public.mobile_privacy_requests where id=v_request and user_id=v_user
        and kind='export' and state='processing' and lease_until>clock_timestamp()
        and lease_token::text=v_lease) then
    raise exception 'Invalid privacy export write' using errcode='42501';
  end if;
  return new;
end; $$;
-- Reads may update last_accessed_at after completion. Fence content/identity
-- changes only; harmless bookkeeping must not require the now-retired lease.
create trigger mobile_privacy_export_storage_fence before insert or update of
  bucket_id,name,user_metadata,version,metadata,owner_id,owner on storage.objects
  for each row execute function public.mobile_privacy_export_storage_fence();
revoke all on function public.mobile_privacy_export_storage_fence() from public,anon,authenticated,service_role;

-- Broad existing owner policies must NOT accidentally make exports writable.
create policy mobile_privacy_export_read on storage.objects for select to authenticated using (
  bucket_id='account-exports' and public.mobile_privacy_live_user() and exists(
    select 1 from public.mobile_privacy_export_paths() p where p.path=name));
create policy mobile_privacy_export_read_boundary on storage.objects as restrictive for select to authenticated using (
  bucket_id<>'account-exports' or (public.mobile_privacy_live_user() and exists(
    select 1 from public.mobile_privacy_export_paths() p where p.path=name)));
create policy mobile_privacy_export_insert_boundary on storage.objects as restrictive for insert to authenticated
  with check(bucket_id<>'account-exports');
create policy mobile_privacy_export_update_boundary on storage.objects as restrictive for update to authenticated
  using(bucket_id<>'account-exports') with check(bucket_id<>'account-exports');
create policy mobile_privacy_export_delete_boundary on storage.objects as restrictive for delete to authenticated
  using(bucket_id<>'account-exports');

revoke all on function public.mobile_privacy_live_user(),public.mobile_privacy_worker_ready(),
  public.mobile_privacy_status(),public.mobile_privacy_request(text,text,text),
  public.mobile_privacy_export_paths(),public.mobile_privacy_freeze_membership(),
  public.mobile_privacy_heartbeat(),public.mobile_privacy_claim(uuid),public.mobile_privacy_snapshot(uuid,uuid),
  public.mobile_privacy_finish(uuid,uuid,text,text,text),public.mobile_privacy_expired_exports(),public.mobile_privacy_prune(uuid)
  from public,anon,authenticated;
grant execute on function public.mobile_privacy_status(),public.mobile_privacy_request(text,text,text),
  public.mobile_privacy_live_user(),public.mobile_privacy_export_paths() to authenticated;
grant execute on function public.mobile_privacy_heartbeat(),public.mobile_privacy_claim(uuid),
  public.mobile_privacy_snapshot(uuid,uuid),public.mobile_privacy_finish(uuid,uuid,text,text,text),
  public.mobile_privacy_expired_exports(),public.mobile_privacy_prune(uuid) to service_role;
notify pgrst,'reload schema';
commit;
