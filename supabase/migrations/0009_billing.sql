-- Provider-neutral billing controls. Apply only through a reviewed migration.
-- No customers, prices, memberships, payments, or provider accounts are seeded.
-- 0007 is required. All existing grants are MANUAL and remain unchanged.
-- Test-mode only until a concrete provider and live rollout are approved.
begin;

create table public.mobile_billing_accounts (
  id uuid primary key default extensions.gen_random_uuid(),
  user_id uuid unique references auth.users(id) on delete set null,
  provider text check (provider ~ '^[a-z][a-z0-9_]{0,31}$'),
  mode text check (mode = 'test'),
  customer_id text check (customer_id ~ '^[A-Za-z0-9_-]{1,200}$'),
  subscription_id text check (subscription_id ~ '^[A-Za-z0-9_-]{1,200}$'),
  lifecycle text not null default 'open' check (lifecycle in ('open', 'erasing', 'erased')),
  status text not null default 'none' check (status ~ '^[a-z_]{1,64}$'),
  plan_key text,
  period_start timestamptz,
  paid_through timestamptz,
  cancel_at_period_end boolean not null default false,
  lease_token uuid,
  lease_until timestamptz,
  erasure_request_id uuid unique,
  cancellation_status text not null default 'none' check (cancellation_status in ('none', 'pending', 'confirmed')),
  cancellation_confirmed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (provider, mode, customer_id),
  check ((provider is null and mode is null) or (provider is not null and mode is not null)),
  check (customer_id is null or provider is not null),
  check (lifecycle <> 'erased' or cancellation_status = 'confirmed')
);

create table public.mobile_billing_events (
  provider text not null,
  mode text not null check (mode = 'test'),
  event_id text not null check (event_id ~ '^[A-Za-z0-9_-]{1,200}$'),
  body_sha256 text not null check (body_sha256 ~ '^[0-9a-f]{64}$'),
  account_id uuid references public.mobile_billing_accounts(id) on delete restrict,
  lease_token uuid,
  state text not null check (state in ('pending', 'applied', 'ignored')),
  created_at timestamptz not null default now(),
  applied_at timestamptz,
  primary key (provider, mode, event_id)
);

create table public.mobile_billing_operations (
  id uuid primary key default extensions.gen_random_uuid(), -- provider idempotency key
  account_id uuid not null references public.mobile_billing_accounts(id) on delete restrict,
  kind text not null check (kind in ('customer', 'checkout')),
  fingerprint text not null check (fingerprint ~ '^[0-9a-f]{64}$'),
  plan_key text,
  price_id text,
  state text not null default 'pending' check (state in ('pending', 'complete', 'cancelled')),
  object_id text,
  redirect_url text check (octet_length(redirect_url) <= 4096),
  expires_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create unique index mobile_billing_one_pending_operation
  on public.mobile_billing_operations(account_id, kind) where state = 'pending';
create index mobile_billing_operations_account on public.mobile_billing_operations(account_id, created_at desc);

alter table public.mobile_usage_memberships
  add column grant_source text not null default 'manual' check (grant_source in ('manual', 'billing')),
  add column billing_account_id uuid references public.mobile_billing_accounts(id) on delete restrict;
-- No backfill/adoption of manual/founder memberships. Explicit admin migration of
-- a member to billing ownership must set BOTH grant_source and billing_account_id.

alter table public.mobile_billing_accounts enable row level security;
alter table public.mobile_billing_events enable row level security;
alter table public.mobile_billing_operations enable row level security;
revoke all on public.mobile_billing_accounts, public.mobile_billing_events,
  public.mobile_billing_operations from public, anon, authenticated;
grant all on public.mobile_billing_accounts, public.mobile_billing_events,
  public.mobile_billing_operations to service_role;
grant select (id, user_id, provider, mode, status, plan_key, period_start, paid_through,
  cancel_at_period_end, cancellation_status, updated_at) on public.mobile_billing_accounts to authenticated;
create policy mobile_billing_read_safe_self on public.mobile_billing_accounts
  for select to authenticated using ((select auth.uid()) = user_id);

-- Export ONLY this projection with explicit user_id scope, never SELECT * from
-- billing tables. No vendor customer/subscription IDs, checkout URLs, event
-- payloads, payment-method details or email. Email belongs to verified Auth.
create view public.mobile_billing_export with (security_invoker = true) as
  select id as account_id, user_id, provider, mode, status, plan_key, period_start,
    paid_through, cancel_at_period_end, cancellation_status, updated_at
  from public.mobile_billing_accounts;
revoke all on public.mobile_billing_export from public, anon, authenticated;
grant select on public.mobile_billing_export to authenticated, service_role;

create function public.mobile_billing_require_service()
returns void language plpgsql set search_path = '' as $$
begin
  if auth.role() is distinct from 'service_role' then
    raise exception 'Trusted billing worker required' using errcode = '42501';
  end if;
end;
$$;

-- 0010 is deliberately optional at 0009 install time, mandatory at paid launch.
-- Dynamic lookup avoids a forward migration dependency. A queued erasure must
-- deny checkout/sync even before the worker obtains its billing cancellation
-- fence. The 0010 membership trigger also independently keeps enabled=false.
create function public.mobile_billing_user_erasing(p_user_id uuid)
returns boolean language plpgsql security definer set search_path = '' as $$
declare v_erasing boolean := false;
begin
  perform public.mobile_billing_require_service();
  if to_regclass('public.mobile_privacy_requests') is not null then
    execute 'select exists(select 1 from public.mobile_privacy_requests where user_id=$1 and kind=''erase'')'
      into v_erasing using p_user_id;
  end if;
  return v_erasing;
end;
$$;

create function public.mobile_billing_begin(p_user_id uuid, p_provider text, p_mode text)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare a public.mobile_billing_accounts%rowtype;
begin
  perform public.mobile_billing_require_service();
  if p_user_id is null or p_provider is null or p_mode is distinct from 'test' then
    raise exception 'Invalid billing configuration';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(p_user_id::text,1010));
  if public.mobile_billing_user_erasing(p_user_id) then return jsonb_build_object('status', 'blocked'); end if;
  insert into public.mobile_billing_accounts(user_id, provider, mode)
    values (p_user_id, p_provider, p_mode) on conflict (user_id) do nothing;
  select * into a from public.mobile_billing_accounts where user_id = p_user_id for update;
  if a.lifecycle <> 'open' or a.provider is distinct from p_provider or a.mode is distinct from p_mode then
    return jsonb_build_object('status', 'blocked');
  end if;
  if a.lease_until > clock_timestamp() then return jsonb_build_object('status', 'busy'); end if;
  update public.mobile_billing_accounts set lease_token = extensions.gen_random_uuid(),
    lease_until = clock_timestamp() + interval '180 seconds', updated_at = now()
    where id = a.id returning * into a;
  return to_jsonb(a);
end;
$$;

create function public.mobile_billing_operation(p_account_id uuid, p_lease_token uuid,
  p_kind text, p_fingerprint text, p_plan_key text, p_price_id text)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare a public.mobile_billing_accounts%rowtype; o public.mobile_billing_operations%rowtype;
begin
  perform public.mobile_billing_require_service();
  select * into a from public.mobile_billing_accounts where id = p_account_id for update;
  if not found or a.lifecycle <> 'open' or public.mobile_billing_user_erasing(a.user_id)
      or p_lease_token is null or a.lease_token is distinct from p_lease_token
      or a.lease_until is null or a.lease_until <= clock_timestamp() then return jsonb_build_object('status', 'stale'); end if;
  select * into o from public.mobile_billing_operations
    where account_id = a.id and kind = p_kind and
      (state = 'pending' or (state = 'complete' and (kind = 'customer' or expires_at > clock_timestamp())))
    order by created_at desc limit 1;
  if found then
    -- Never regenerate an idempotency key for an uncertain operation. Stripe
    -- retention is >=24h; 23h is a conservative automatic-retry ceiling. Older
    -- operations require operator reconciliation (also for an erasure worker).
    if o.fingerprint is distinct from p_fingerprint or o.created_at < clock_timestamp() - interval '23 hours' then
      return jsonb_build_object('status', 'blocked');
    end if;
    return to_jsonb(o);
  end if;
  insert into public.mobile_billing_operations(account_id, kind, fingerprint, plan_key, price_id)
    values (a.id, p_kind, p_fingerprint, p_plan_key, p_price_id) returning * into o;
  return to_jsonb(o);
end;
$$;

create function public.mobile_billing_complete_operation(p_account_id uuid, p_lease_token uuid,
  p_operation_id uuid, p_object_id text, p_url text, p_expires_at bigint)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare a public.mobile_billing_accounts%rowtype; o public.mobile_billing_operations%rowtype;
begin
  perform public.mobile_billing_require_service();
  select * into a from public.mobile_billing_accounts where id = p_account_id for update;
  if not found or a.lifecycle <> 'open' or public.mobile_billing_user_erasing(a.user_id)
      or p_lease_token is null or a.lease_token is distinct from p_lease_token
      or a.lease_until is null or a.lease_until <= clock_timestamp() then return jsonb_build_object('status', 'stale'); end if;
  select * into o from public.mobile_billing_operations where account_id = a.id and id = p_operation_id for update;
  if not found or o.state = 'cancelled' or p_object_id is null or p_object_id !~ '^[A-Za-z0-9_-]{1,200}$'
      or (o.object_id is not null and o.object_id is distinct from p_object_id) then
    return jsonb_build_object('status', 'conflict');
  end if;
  if o.kind = 'customer' then
    if a.customer_id is not null and a.customer_id is distinct from p_object_id then
      return jsonb_build_object('status', 'conflict');
    end if;
    update public.mobile_billing_accounts set customer_id = p_object_id, updated_at = now() where id = a.id;
  elsif p_url is null or p_expires_at is null or to_timestamp(p_expires_at) <= clock_timestamp() then
    return jsonb_build_object('status', 'conflict');
  end if;
  update public.mobile_billing_operations set state = 'complete', object_id = p_object_id,
    redirect_url = p_url, expires_at = to_timestamp(p_expires_at), updated_at = now() where id = o.id;
  -- In particular, this function NEVER writes mobile_usage_memberships.
  return jsonb_build_object('status', 'saved');
end;
$$;

create function public.mobile_billing_release(p_account_id uuid, p_lease_token uuid)
returns jsonb language plpgsql security definer set search_path = '' as $$
begin
  perform public.mobile_billing_require_service();
  update public.mobile_billing_accounts set lease_token = null, lease_until = null
    where id = p_account_id and lease_token = p_lease_token;
  return jsonb_build_object('status', 'released');
end;
$$;

create function public.mobile_billing_event_claim(p_provider text, p_mode text, p_event_id text,
  p_customer_id text, p_body_sha256 text)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare a public.mobile_billing_accounts%rowtype; e public.mobile_billing_events%rowtype;
begin
  perform public.mobile_billing_require_service();
  select * into a from public.mobile_billing_accounts
    where provider = p_provider and mode = p_mode and customer_id = p_customer_id for update;
  insert into public.mobile_billing_events(provider, mode, event_id, body_sha256, account_id, state)
    values (p_provider, p_mode, p_event_id, p_body_sha256, a.id, 'pending') on conflict do nothing;
  select * into e from public.mobile_billing_events
    where provider = p_provider and mode = p_mode and event_id = p_event_id for update;
  if e.body_sha256 is distinct from p_body_sha256 or e.account_id is distinct from a.id then
    return jsonb_build_object('status', 'conflict');
  end if;
  if e.state <> 'pending' then return jsonb_build_object('status', 'duplicate'); end if;
  if a.id is null or a.user_id is null or a.lifecycle <> 'open' or public.mobile_billing_user_erasing(a.user_id) then
    update public.mobile_billing_events set state = 'ignored', applied_at = now()
      where provider = p_provider and mode = p_mode and event_id = p_event_id;
    return jsonb_build_object('status', 'ignored');
  end if;
  if a.lease_until > clock_timestamp() then return jsonb_build_object('status', 'busy'); end if;
  update public.mobile_billing_accounts set lease_token = extensions.gen_random_uuid(),
    lease_until = clock_timestamp() + interval '180 seconds' where id = a.id returning * into a;
  update public.mobile_billing_events set lease_token = a.lease_token
    where provider = p_provider and mode = p_mode and event_id = p_event_id;
  return to_jsonb(a);
end;
$$;

create function public.mobile_billing_apply_snapshot(p_account_id uuid, p_lease_token uuid,
  p_event_id text, p_snapshot jsonb)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare
  a public.mobile_billing_accounts%rowtype; e public.mobile_billing_events%rowtype;
  m public.mobile_usage_memberships%rowtype;
  v_start timestamptz; v_end timestamptz; v_paid timestamptz; v_eligible boolean;
  v_period_limit bigint; v_daily_limit bigint; v_now timestamptz;
begin
  perform public.mobile_billing_require_service();
  select * into a from public.mobile_billing_accounts where id = p_account_id for update;
  if not found or a.lifecycle <> 'open' or a.user_id is null or public.mobile_billing_user_erasing(a.user_id)
      or p_lease_token is null or a.lease_token is distinct from p_lease_token
      or a.lease_until is null or a.lease_until <= clock_timestamp() then return jsonb_build_object('status', 'stale'); end if;
  select * into e from public.mobile_billing_events
    where account_id = a.id and provider = a.provider and mode = a.mode and event_id = p_event_id for update;
  if not found or e.lease_token is distinct from p_lease_token then return jsonb_build_object('status', 'stale'); end if;
  if e.state <> 'pending' then return jsonb_build_object('status', 'duplicate'); end if;
  if jsonb_typeof(p_snapshot) is distinct from 'object' or octet_length(p_snapshot::text) > 8192
      or jsonb_typeof(p_snapshot->'eligible') is distinct from 'boolean'
      or jsonb_typeof(p_snapshot->'cancel_at_period_end') is distinct from 'boolean' then
    raise exception 'Invalid normalized billing snapshot';
  end if;
  v_start := to_timestamp((p_snapshot->>'period_start')::bigint);
  v_end := to_timestamp((p_snapshot->>'period_end')::bigint);
  v_paid := least(v_end, to_timestamp((p_snapshot->>'paid_through')::bigint));
  v_period_limit := (p_snapshot->>'period_limit')::bigint;
  v_daily_limit := (p_snapshot->>'daily_limit')::bigint;
  v_now := clock_timestamp();
  v_eligible := coalesce((p_snapshot->>'eligible')::boolean
    and p_snapshot->>'status' = 'active' and p_snapshot->>'subscription_id' is not null
    and p_snapshot->>'plan_key' is not null and p_snapshot->>'paid_through' is not null
    and v_end is not null and v_start <= v_now and v_paid > v_now
    and v_end > v_start and v_period_limit between 1 and 9000000000000000
    and v_daily_limit between 1 and 9000000000000000, false);

  insert into public.mobile_usage_memberships(user_id, grant_source, billing_account_id)
    values (a.user_id, 'billing', a.id) on conflict (user_id) do nothing;
  select * into m from public.mobile_usage_memberships where user_id = a.user_id for update;
  if m.grant_source = 'billing' and m.billing_account_id = a.id then
    if v_eligible and (m.period_start is null or v_start >= m.period_start) then
      update public.mobile_usage_memberships set enabled = true, expires_at = v_paid,
        period_start = v_start, period_end = v_end, period_limit = v_period_limit, daily_limit = v_daily_limit,
        -- Replay, same period, overlapping/prorated plan changes: KEEP all use.
        -- A strictly later, nonoverlapping paid cycle alone renews period quota.
        period_reserved = case when m.period_start is not null and v_start > m.period_start
          and v_start >= m.period_end then 0 else m.period_reserved end
        where user_id = a.user_id;
    else
      -- Preserve period dates/counters even for cancellation or stale periods.
      update public.mobile_usage_memberships set enabled = false, expires_at = v_now where user_id = a.user_id;
    end if;
  end if;
  -- Manual members are NEVER enabled, disabled, renewed or repriced by events.
  -- mobile_ai_daily_usage and mobile_ai_reservations are NEVER reset here.
  update public.mobile_billing_accounts set subscription_id = p_snapshot->>'subscription_id',
    status = coalesce(p_snapshot->>'status', 'unknown'), plan_key = p_snapshot->>'plan_key',
    period_start = v_start, paid_through = v_paid,
    cancel_at_period_end = (p_snapshot->>'cancel_at_period_end')::boolean, updated_at = now()
    where id = a.id;
  update public.mobile_billing_events set state = 'applied', applied_at = now()
    where provider = a.provider and mode = a.mode and event_id = p_event_id;
  return jsonb_build_object('status', 'applied');
end;
$$;

create function public.mobile_billing_erasure_status(p_user_id uuid)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare a public.mobile_billing_accounts%rowtype;
begin
  perform public.mobile_billing_require_service();
  select * into a from public.mobile_billing_accounts where user_id = p_user_id;
  return jsonb_build_object('account_id', a.id, 'status', a.status, 'lifecycle', a.lifecycle,
    'cancellation_status', a.cancellation_status,
    -- Absence is unconfirmed, never sufficient evidence to delete Auth.
    'requires_cancellation', a.cancellation_status is distinct from 'confirmed');
end;
$$;

create function public.mobile_billing_cancel_claim(p_user_id uuid, p_request_id uuid)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare a public.mobile_billing_accounts%rowtype; v_operations jsonb;
begin
  perform public.mobile_billing_require_service();
  if p_user_id is null or p_request_id is null then raise exception 'Trusted erasure UUIDs required'; end if;
  perform pg_advisory_xact_lock(hashtextextended(p_user_id::text,1010));
  -- A retry AFTER Auth deletion must find the anonymous confirmed receipt by
  -- durable request ID, not attempt a new FK-bound account insert for a gone user.
  select * into a from public.mobile_billing_accounts where erasure_request_id = p_request_id for update;
  if found and a.cancellation_status = 'confirmed' and (a.user_id = p_user_id or a.user_id is null) then
    return jsonb_build_object('status', 'ready');
  elsif found and a.user_id is distinct from p_user_id then
    return jsonb_build_object('status', 'conflict');
  end if;
  -- Absence after Auth deletion is NOT evidence of no billing. Only the matching
  -- confirmed tombstone above can authorize that retry. Lock the extant Auth row
  -- before creating a never-billed fence, and share checkout/0010's user lock.
  perform 1 from auth.users where id = p_user_id for key share;
  if not found then return jsonb_build_object('status', 'blocked'); end if;
  -- Even never-billed users receive a fence: checkout cannot race an erasure.
  insert into public.mobile_billing_accounts(user_id) values (p_user_id) on conflict (user_id) do nothing;
  select * into a from public.mobile_billing_accounts where user_id = p_user_id for update;
  if a.erasure_request_id is not null and a.erasure_request_id <> p_request_id then
    return jsonb_build_object('status', 'conflict');
  end if;
  if a.cancellation_status = 'confirmed' then return jsonb_build_object('status', 'ready'); end if;
  update public.mobile_billing_accounts set lifecycle = 'erasing', erasure_request_id = p_request_id,
    cancellation_status = 'pending', updated_at = now() where id = a.id;
  -- Unlike unrelated webhooks, verified-user erasure intentionally disables ALL
  -- of that user's grants, including manual ones. Never zero spent counters.
  update public.mobile_usage_memberships set enabled = false, expires_at = clock_timestamp() where user_id = p_user_id;
  if a.lease_until > clock_timestamp() then return jsonb_build_object('status', 'busy'); end if;
  select coalesce(jsonb_agg(to_jsonb(o)), '[]'::jsonb) into v_operations
    from public.mobile_billing_operations o where o.account_id = a.id and o.state <> 'cancelled';
  if a.customer_id is null and a.subscription_id is null and a.status = 'none'
      and jsonb_array_length(v_operations) = 0 then
    update public.mobile_billing_accounts set lifecycle = 'erased', cancellation_status = 'confirmed',
      cancellation_confirmed_at = now(), lease_token = null, lease_until = null where id = a.id;
    return jsonb_build_object('status', 'ready');
  end if;
  update public.mobile_billing_accounts set lease_token = extensions.gen_random_uuid(),
    lease_until = clock_timestamp() + interval '180 seconds' where id = a.id returning * into a;
  return to_jsonb(a) || jsonb_build_object('operations', v_operations);
end;
$$;

create function public.mobile_billing_cancel_finish(p_account_id uuid, p_request_id uuid,
  p_lease_token uuid, p_receipt jsonb)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare a public.mobile_billing_accounts%rowtype;
begin
  perform public.mobile_billing_require_service();
  select * into a from public.mobile_billing_accounts where id = p_account_id for update;
  if not found or a.lifecycle <> 'erasing' or a.erasure_request_id is distinct from p_request_id
      or p_lease_token is null or a.lease_token is distinct from p_lease_token
      or a.lease_until is null or a.lease_until <= clock_timestamp() then
    return jsonb_build_object('status', 'stale');
  end if;
  if jsonb_typeof(p_receipt) is distinct from 'object'
      or p_receipt->>'customer_id' is distinct from a.customer_id
      or p_receipt->'cancellation_confirmed' is distinct from 'true'::jsonb
      or p_receipt->'no_future_collection' is distinct from 'true'::jsonb
      or p_receipt->'open_subscription_ids' is distinct from '[]'::jsonb
      or p_receipt->'open_checkout_ids' is distinct from '[]'::jsonb
      or p_receipt->'unresolved_operation_ids' is distinct from '[]'::jsonb then
    return jsonb_build_object('status', 'blocked');
  end if;
  update public.mobile_billing_accounts set lifecycle = 'erased', status = 'canceled',
    cancellation_status = 'confirmed', cancellation_confirmed_at = now(), updated_at = now(),
    lease_token = null, lease_until = null where id = a.id;
  update public.mobile_billing_operations set state = 'cancelled', redirect_url = null, updated_at = now()
    where account_id = a.id;
  return jsonb_build_object('status', 'ready');
end;
$$;

-- Delete guard is independent of API/UI claims. 0010 must call the trusted
-- cancel helper after its durable verified-user claim and BEFORE Auth deletion.
create function public.mobile_billing_guard_auth_delete()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
  if exists (select 1 from public.mobile_billing_accounts where user_id = old.id
      and cancellation_status <> 'confirmed') then
    raise exception 'Billing cancellation must be confirmed before Auth deletion';
  end if;
  return old;
end;
$$;
create trigger mobile_billing_guard_auth_delete before delete on auth.users
  for each row execute function public.mobile_billing_guard_auth_delete();

-- Explicit grants for ONLY this migration's functions; never default PUBLIC
-- execution of service capabilities. SECURITY DEFINER routines recheck role.
do $grants$
declare signature text;
begin
  foreach signature in array array[
    'mobile_billing_require_service()',
    'mobile_billing_user_erasing(uuid)',
    'mobile_billing_begin(uuid,text,text)',
    'mobile_billing_operation(uuid,uuid,text,text,text,text)',
    'mobile_billing_complete_operation(uuid,uuid,uuid,text,text,bigint)',
    'mobile_billing_release(uuid,uuid)',
    'mobile_billing_event_claim(text,text,text,text,text)',
    'mobile_billing_apply_snapshot(uuid,uuid,text,jsonb)',
    'mobile_billing_erasure_status(uuid)',
    'mobile_billing_cancel_claim(uuid,uuid)',
    'mobile_billing_cancel_finish(uuid,uuid,uuid,jsonb)'
  ] loop
    execute 'revoke all on function public.' || signature || ' from public, anon, authenticated, service_role';
    execute 'grant execute on function public.' || signature || ' to service_role';
  end loop;
end;
$grants$;
revoke all on function public.mobile_billing_guard_auth_delete() from public, anon, authenticated, service_role;
notify pgrst, 'reload schema';
commit;
