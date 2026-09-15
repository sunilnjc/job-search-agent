-- JP-TEST-020: reviewed, administrator-provisioned membership and durable AI cap.
-- DO NOT run automatically against hosted data. No PII/credentials/provider calls.
--
-- Rollout preflight (same migration connection, BEFORE running this file):
--   SET jobpursuit.member_user_ids = '["<reviewed-auth-user-uuid>"]';
--   SET jobpursuit.blocked_user_ids = '["<explicitly-denied-auth-user-uuid>"]';
-- Every existing profile must be explicitly allowed OR explicitly denied. The
-- migration aborts atomically otherwise, preserving the founder's prior access.
-- A blank new database needs neither setting. NEVER seed from email, profile,
-- user_metadata, existing-row age, or signup order. No automatic free AI quota.
-- Keep the API's private verified-email gate; synchronize the reviewed DB member
-- list with it. A public rollout must provision membership independently.
--
-- After review/application, a trusted SQL administrator or service_role must:
-- * provision operation cost_units for chat/rank/prepare (none seeded here),
-- * enable a member and set finite period_start/end and positive period/daily caps,
-- * reset period_reserved ONLY as an explicit paid-period renewal (never clients),
-- * select cost units conservatively for server model/input/output ceilings and
--   every possible billed call/retry. Units are NOT verified provider invoice USD.
-- Failed/uncertain requests retain reservations. There is no client refund API.
--
-- PARAMETERIZED OPERATOR EXAMPLE -- DOCUMENTATION ONLY, NOT EXECUTED BELOW.
-- Use psql in a reviewed administrator session, not an authenticated browser.
-- These dummy UUIDs and future dates intentionally do not identify a real user
-- or activate today's quota. Replace every value after identity/billing review.
-- For migration preflight, set these on the SAME connection before applying:
--   SET jobpursuit.member_user_ids = '["00000000-0000-4000-8000-000000000001"]';
--   SET jobpursuit.blocked_user_ids = '[]';
-- Include the founder and each existing profile in exactly one reviewed list.
-- Then apply this migration; missing/invalid/unreviewed IDs abort the transaction.
--
-- Initial AI provisioning AFTER migration (unit prices below are EXAMPLES,
-- NOT measured USD, recommended plan values, or permission to enable providers):
--   \set member_uuid '00000000-0000-4000-8000-000000000001'
--   \set period_start '2099-01-01T00:00:00Z'
--   \set period_end '2099-02-01T00:00:00Z'
--   \set membership_expires '2099-02-01T00:00:00Z'
--   \set period_units 100
--   \set daily_units 10
--   \set chat_units 1
--   \set rank_units 1
--   \set prepare_units 2
--   BEGIN;
--   INSERT INTO public.mobile_ai_operation_costs(operation,cost_units,enabled)
--     VALUES ('chat', :chat_units, true), ('rank', :rank_units, true),
--            ('prepare', :prepare_units, true);
--   -- Prices are global. On later member provisioning omit that INSERT;
--   -- changing existing prices requires separate operator/model-cost review.
--   INSERT INTO public.mobile_usage_memberships(user_id)
--     VALUES (:'member_uuid'::uuid) ON CONFLICT (user_id) DO NOTHING;
--   UPDATE public.mobile_usage_memberships SET enabled=true,
--     expires_at=:'membership_expires'::timestamptz,
--     period_start=:'period_start'::timestamptz, period_end=:'period_end'::timestamptz,
--     period_limit=:period_units, daily_limit=:daily_units
--     WHERE user_id=:'member_uuid'::uuid AND period_start IS NULL AND period_reserved=0
--     RETURNING user_id, enabled, period_start, period_end, period_limit, daily_limit;
--   -- Expect exactly ONE updated member; otherwise ROLLBACK and investigate.
--   COMMIT;
-- No example resets spending. Paid renewal requires a separately reviewed update
-- of the locked member row's period and counter; do not delete daily/ledger rows.
-- Daily totals retain charges across same-day renewals, conservatively.
-- To revoke: UPDATE public.mobile_usage_memberships SET enabled=false
--              WHERE user_id=:'member_uuid'::uuid;
-- Existing member revocation blocks new reservations; a reservation already
-- committed can still complete its in-flight provider work (already budgeted).
-- Release gate: verify RPC presence, confirmed-user membership, zero-quota denial,
-- cross-user RLS, replay denial, concurrent cap, and missing-RPC 503 in a reviewed
-- nonproduction database first. API auth/RPC probes themselves need no model call.
-- Do not label the local MockTransport/static suite as satisfying that DB gate.
--
-- Source grant audit: 0001 relies on deployment defaults for public tables and
-- Storage; 0002/0003/0005/0006 grant owner-scoped column writes. No hosted grants
-- inspected. Retain those grants/policies; add RESTRICTIVE checks (AND, not OR).
-- Direct web/PostgREST/Storage clients bypass API email gates/rate/body limits.
-- Membership RLS below closes the access bypass, NOT all member storage/row
-- resource quotas. Gateway/storage limits still need a separate release review.
-- Review deployed table/column ACLs, extra views/RPCs, and service-role holders:
--   SELECT * FROM information_schema.role_table_grants
--     WHERE table_schema IN ('public', 'storage');
--   SELECT * FROM information_schema.role_column_grants
--     WHERE table_schema IN ('public', 'storage');
-- These offline structural tests are not live database/concurrency/RLS proof.
begin;

create table public.mobile_usage_memberships (
  user_id uuid primary key references auth.users(id) on delete cascade,
  enabled boolean not null default false,
  expires_at timestamptz,
  period_start timestamptz,
  period_end timestamptz,
  period_limit bigint not null default 0 check (period_limit between 0 and 9000000000000000),
  daily_limit bigint not null default 0 check (daily_limit between 0 and 9000000000000000),
  period_reserved bigint not null default 0 check (period_reserved between 0 and 9000000000000000),
  check ((period_start is null and period_end is null) or
         (period_start is not null and period_end is not null and period_end > period_start
          and isfinite(period_start) and isfinite(period_end))),
  check (expires_at is null or isfinite(expires_at))
);

create table public.mobile_ai_operation_costs (
  operation text primary key check (operation in ('chat', 'rank', 'prepare')),
  cost_units bigint not null check (cost_units between 1 and 9000000000000000),
  enabled boolean not null default false
);

create table public.mobile_ai_daily_usage (
  user_id uuid not null references auth.users(id) on delete cascade,
  usage_day date not null,
  reserved_units bigint not null default 0 check (reserved_units between 0 and 9000000000000000),
  primary key (user_id, usage_day)
);

create table public.mobile_ai_reservations (
  user_id uuid not null references auth.users(id) on delete cascade,
  reservation_id uuid not null,
  operation text not null references public.mobile_ai_operation_costs(operation),
  reserved_units bigint not null check (reserved_units > 0),
  reserved_at timestamptz not null,
  primary key (user_id, reservation_id)
);

-- Control state is bound to auth.users, not caller-deletable profiles/model_runs:
-- deleting/upserting workspace data cannot erase spending or revive membership.
alter table public.mobile_usage_memberships enable row level security;
alter table public.mobile_ai_operation_costs enable row level security;
alter table public.mobile_ai_daily_usage enable row level security;
alter table public.mobile_ai_reservations enable row level security;

revoke all on public.mobile_usage_memberships, public.mobile_ai_operation_costs,
  public.mobile_ai_daily_usage, public.mobile_ai_reservations from public, anon, authenticated;
grant select on public.mobile_usage_memberships, public.mobile_ai_daily_usage,
  public.mobile_ai_reservations to authenticated;
grant all on public.mobile_usage_memberships, public.mobile_ai_operation_costs,
  public.mobile_ai_daily_usage, public.mobile_ai_reservations to service_role;

create policy mobile_membership_read_self on public.mobile_usage_memberships
  for select to authenticated using ((select auth.uid()) = user_id);
create policy mobile_daily_usage_read_self on public.mobile_ai_daily_usage
  for select to authenticated using ((select auth.uid()) = user_id);
create policy mobile_reservations_read_self on public.mobile_ai_reservations
  for select to authenticated using ((select auth.uid()) = user_id);
-- Deliberately no client policy or grant on operation prices; no client writes,
-- DELETE, TRUNCATE, owner/period mutation, or reservation settlement of any kind.

-- Short migration-only lock also prevents signups racing the reviewed inventory.
lock table public.profiles in share mode;
do $preflight$
declare
  v_allowed uuid[] := array(select value::uuid from jsonb_array_elements_text(
    coalesce(nullif(current_setting('jobpursuit.member_user_ids', true), ''), '[]')::jsonb));
  v_blocked uuid[] := array(select value::uuid from jsonb_array_elements_text(
    coalesce(nullif(current_setting('jobpursuit.blocked_user_ids', true), ''), '[]')::jsonb));
begin
  if array_position(v_allowed, null) is not null or array_position(v_blocked, null) is not null
      or v_allowed && v_blocked then
    raise exception 'Usage controls: member/blocked UUID lists must be non-null and disjoint';
  end if;
  if exists (select 1 from public.profiles p
      where not (p.user_id = any(v_allowed) or p.user_id = any(v_blocked))) then
    raise exception 'Usage controls not applied: review every existing profile, including founder, in jobpursuit.member_user_ids or jobpursuit.blocked_user_ids first';
  end if;
  insert into public.mobile_usage_memberships(user_id, enabled)
    select member_id, true from (select distinct unnest(v_allowed) as member_id) reviewed;
  insert into public.mobile_usage_memberships(user_id, enabled)
    select member_id, false from (select distinct unnest(v_blocked) as member_id) reviewed;
end;
$preflight$;

-- SECURITY DEFINER is necessary: callers cannot mutate or inspect others'
-- control rows. All identifiers are schema-qualified; callers select no user.
create function public.mobile_has_access()
returns boolean language sql stable security definer set search_path = '' as $$
  select auth.uid() is not null and coalesce(auth.role() = 'authenticated', false)
    and exists (select 1 from public.mobile_usage_memberships m
      where m.user_id = auth.uid() and m.enabled
      and (m.expires_at is null or m.expires_at > statement_timestamp()));
$$;

create function public.mobile_check_access()
returns jsonb language sql stable security definer set search_path = '' as $$
  select jsonb_build_object('allowed', public.mobile_has_access(),
    'user_id', auth.uid(), 'code', case when public.mobile_has_access()
      then 'ok' else 'not_entitled' end);
$$;

create function public.mobile_reserve_ai_usage(p_operation text, p_reservation_id uuid)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare
  v_user uuid := auth.uid();
  v_member public.mobile_usage_memberships%rowtype;
  v_cost bigint;
  v_daily bigint;
  v_now timestamptz;
  v_day date;
  v_reset timestamptz;
begin
  if v_user is null or auth.role() is distinct from 'authenticated' then
    return jsonb_build_object('allowed', false, 'code', 'not_entitled');
  end if;
  if p_operation is null or p_operation not in ('chat', 'rank', 'prepare')
      or p_reservation_id is null then
    return jsonb_build_object('allowed', false, 'code', 'invalid_request');
  end if;

  -- One stable user row serializes all workers/operations and administrative
  -- revocation/renewal. Row lock survives until the PostgREST transaction commits.
  select * into v_member from public.mobile_usage_memberships
    where user_id = v_user for update;
  if not found then
    return jsonb_build_object('allowed', false, 'code', 'not_entitled');
  end if;
  -- Lock prices against concurrent edits for the duration of this reservation.
  select cost_units into v_cost from public.mobile_ai_operation_costs
    where operation = p_operation and enabled for share;
  if not found then
    return jsonb_build_object('allowed', false, 'code', 'operation_unprovisioned');
  end if;
  v_now := clock_timestamp(); -- sample AFTER both locks, including UTC day rollover
  if not v_member.enabled
      or (v_member.expires_at is not null and v_member.expires_at <= v_now) then
    return jsonb_build_object('allowed', false, 'code', 'not_entitled');
  end if;
  if v_member.period_start is null or v_member.period_start > v_now
      or v_member.period_end <= v_now or v_member.period_limit <= 0 or v_member.daily_limit <= 0 then
    return jsonb_build_object('allowed', false, 'code', 'quota_unprovisioned');
  end if;
  if exists (select 1 from public.mobile_ai_reservations
      where user_id = v_user and reservation_id = p_reservation_id) then
    -- A replay NEVER returns allowed=true: an old receipt isn't new authority.
    return jsonb_build_object('allowed', false, 'code', 'duplicate_reservation');
  end if;

  v_day := (v_now at time zone 'UTC')::date;
  select reserved_units into v_daily from public.mobile_ai_daily_usage
    where user_id = v_user and usage_day = v_day for update;
  v_daily := coalesce(v_daily, 0);
  -- Subtraction avoids overflow and an administrator lowering a cap fails closed.
  if v_cost > v_member.period_limit - v_member.period_reserved then
    v_reset := v_member.period_end;
  elsif v_cost > v_member.daily_limit - v_daily then
    v_reset := least(v_member.period_end, (v_day + 1)::timestamp at time zone 'UTC');
  else
    update public.mobile_usage_memberships
      set period_reserved = period_reserved + v_cost where user_id = v_user;
    insert into public.mobile_ai_daily_usage(user_id, usage_day, reserved_units)
      values (v_user, v_day, v_cost)
      on conflict (user_id, usage_day) do update
        set reserved_units = public.mobile_ai_daily_usage.reserved_units + excluded.reserved_units;
    insert into public.mobile_ai_reservations(user_id, reservation_id, operation, reserved_units, reserved_at)
      values (v_user, p_reservation_id, p_operation, v_cost, v_now);
    return jsonb_build_object('allowed', true, 'user_id', v_user,
      'reservation_id', p_reservation_id, 'operation', p_operation, 'reserved_units', v_cost,
      'remaining_period_units', v_member.period_limit - v_member.period_reserved - v_cost,
      'remaining_daily_units', v_member.daily_limit - v_daily - v_cost);
  end if;
  return jsonb_build_object('allowed', false, 'code', 'quota_exhausted',
    'retry_after', greatest(1, least(86400, ceil(extract(epoch from (v_reset - v_now))))));
end;
$$;

revoke all on function public.mobile_has_access() from public, anon, authenticated, service_role;
revoke all on function public.mobile_check_access() from public, anon, authenticated, service_role;
revoke all on function public.mobile_reserve_ai_usage(text, uuid) from public, anon, authenticated, service_role;
-- Anonymous helper can only return false; needed to deny anonymous table access
-- cleanly even if a deployment's default table ACL grants SELECT to anon.
grant execute on function public.mobile_has_access() to anon, authenticated;
grant execute on function public.mobile_check_access() to authenticated;
grant execute on function public.mobile_reserve_ai_usage(text, uuid) to authenticated;

-- Keep existing permissive owner policies. RESTRICTIVE policies AND membership
-- with every permissive path, including direct web writes; no grant expansion.
do $policies$
declare
  v_table text;
begin
  foreach v_table in array array[
    'profiles', 'job_preferences', 'resumes', 'jobs', 'job_scores',
    'applications', 'artifacts', 'model_runs', 'application_attempts',
    'candidate_context', 'mobile_questions', 'mobile_answers',
    'mobile_resume_operations', 'mobile_artifact_operations'
  ] loop
    -- These are required prior migrations: do not silently skip missing tables.
    execute format('alter table public.%I enable row level security', v_table);
    execute format('create policy mobile_entitlement_required on public.%I
      as restrictive for all to anon, authenticated
      using ((select auth.uid()) = user_id and (select public.mobile_has_access()))
      with check ((select auth.uid()) = user_id and (select public.mobile_has_access()))', v_table);
  end loop;
end;
$policies$;

create policy mobile_storage_entitlement_required on storage.objects
  as restrictive for all to anon, authenticated
  using (bucket_id not in ('resumes', 'application-artifacts') or
    ((storage.foldername(name))[1] = (select auth.uid())::text and (select public.mobile_has_access())))
  with check (bucket_id not in ('resumes', 'application-artifacts') or
    ((storage.foldername(name))[1] = (select auth.uid())::text and (select public.mobile_has_access())));

notify pgrst, 'reload schema';
commit;
