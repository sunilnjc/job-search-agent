-- Supports live-mode records, but DOES NOT activate them. Requires 0013.
-- Two independent gates: reviewed server env activation + this service-only
-- record. Operator must separately approve pricing/caps/policies and sandbox
-- evidence before setting enabled, reviewed_at and review_reference together.
-- No test account is remapped, no membership/usage counters are changed.
begin;
create table public.mobile_billing_live_control (
  singleton boolean primary key default true check(singleton),
  enabled boolean not null default false,
  reviewed_at timestamptz,
  review_reference text check(octet_length(review_reference) between 1 and 200),
  check(not enabled or (reviewed_at is not null and review_reference is not null))
);
insert into public.mobile_billing_live_control(singleton) values(true);
alter table public.mobile_billing_live_control enable row level security;
revoke all on public.mobile_billing_live_control from public,anon,authenticated;
grant select,update on public.mobile_billing_live_control to service_role;
alter table public.mobile_billing_accounts drop constraint mobile_billing_accounts_mode_check;
alter table public.mobile_billing_accounts add constraint mobile_billing_accounts_mode_check check(mode in ('test','live'));
alter table public.mobile_billing_events drop constraint mobile_billing_events_mode_check;
alter table public.mobile_billing_events add constraint mobile_billing_events_mode_check check(mode in ('test','live'));

create function public.mobile_billing_mode_enabled(p_mode text)
returns boolean language sql stable security definer set search_path='' as $$
  select p_mode='test' or (p_mode='live' and exists(select 1 from public.mobile_billing_live_control
    where singleton and enabled and reviewed_at<=now() and review_reference is not null));
$$;
revoke all on function public.mobile_billing_mode_enabled(text) from public,anon,authenticated;
grant execute on function public.mobile_billing_mode_enabled(text) to service_role;

create or replace function public.mobile_billing_begin(p_user_id uuid,p_provider text,p_mode text)
returns jsonb language plpgsql security definer set search_path='' as $$
declare a public.mobile_billing_accounts%rowtype;
begin
  perform public.mobile_billing_require_service();
  if p_user_id is null or p_provider is null or p_mode is null or p_mode not in ('test','live') then
    raise exception 'Invalid billing configuration';
  end if;
  if not public.mobile_billing_mode_enabled(p_mode) then return jsonb_build_object('status','blocked'); end if;
  perform pg_advisory_xact_lock(hashtextextended(p_user_id::text,1010));
  if public.mobile_billing_user_erasing(p_user_id) then return jsonb_build_object('status','blocked'); end if;
  insert into public.mobile_billing_accounts(user_id,provider,mode)
    values(p_user_id,p_provider,p_mode) on conflict(user_id) do nothing;
  select * into a from public.mobile_billing_accounts where user_id=p_user_id for update;
  -- Deliberately preserve unique owner mapping. Sandbox/live accounts may not
  -- adopt each other's customer, receipt, operation or entitlement identifiers.
  if a.lifecycle<>'open' or a.provider is distinct from p_provider or a.mode is distinct from p_mode then
    return jsonb_build_object('status','blocked');
  end if;
  if a.lease_until>clock_timestamp() then return jsonb_build_object('status','busy'); end if;
  update public.mobile_billing_accounts set lease_token=extensions.gen_random_uuid(),
    lease_until=clock_timestamp()+interval '180 seconds',updated_at=now() where id=a.id returning * into a;
  return to_jsonb(a);
end;
$$;

create or replace function public.mobile_billing_event_claim(p_provider text,p_mode text,p_event_id text,
  p_customer_id text,p_body_sha256 text)
returns jsonb language plpgsql security definer set search_path='' as $$
declare a public.mobile_billing_accounts%rowtype; e public.mobile_billing_events%rowtype;
begin
  perform public.mobile_billing_require_service();
  if not coalesce(public.mobile_billing_mode_enabled(p_mode),false) then return jsonb_build_object('status','blocked'); end if;
  select * into a from public.mobile_billing_accounts
    where provider=p_provider and mode=p_mode and customer_id=p_customer_id for update;
  insert into public.mobile_billing_events(provider,mode,event_id,body_sha256,account_id,state)
    values(p_provider,p_mode,p_event_id,p_body_sha256,a.id,'pending') on conflict do nothing;
  select * into e from public.mobile_billing_events where provider=p_provider and mode=p_mode and event_id=p_event_id for update;
  if e.body_sha256 is distinct from p_body_sha256 or e.account_id is distinct from a.id then return jsonb_build_object('status','conflict'); end if;
  if e.state<>'pending' then return jsonb_build_object('status','duplicate'); end if;
  if a.id is null or a.user_id is null or a.lifecycle<>'open' or public.mobile_billing_user_erasing(a.user_id) then
    update public.mobile_billing_events set state='ignored',applied_at=now() where provider=p_provider and mode=p_mode and event_id=p_event_id;
    return jsonb_build_object('status','ignored');
  end if;
  if a.lease_until>clock_timestamp() then return jsonb_build_object('status','busy'); end if;
  update public.mobile_billing_accounts set lease_token=extensions.gen_random_uuid(),
    lease_until=clock_timestamp()+interval '180 seconds' where id=a.id returning * into a;
  update public.mobile_billing_events set lease_token=a.lease_token where provider=p_provider and mode=p_mode and event_id=p_event_id;
  return to_jsonb(a);
end;
$$;

-- Keep the owner-only status contract, adding mode and DB activation state.
-- The original implementation remains private; the wrapper cannot change owner.
alter function public.mobile_billing_customer_status() rename to mobile_billing_customer_status_private;
revoke all on function public.mobile_billing_customer_status_private() from public,anon,authenticated,service_role;
create function public.mobile_billing_customer_status()
returns jsonb language plpgsql stable security definer set search_path='' as $$
declare s jsonb; v_mode text;
begin
  s:=public.mobile_billing_customer_status_private();
  select mode into v_mode from public.mobile_billing_accounts where user_id=auth.uid();
  return s || jsonb_build_object('billing_mode',v_mode,'live_activation_enabled',public.mobile_billing_mode_enabled('live'));
end;
$$;
revoke all on function public.mobile_billing_customer_status() from public,anon,authenticated,service_role;
grant execute on function public.mobile_billing_customer_status() to authenticated;
commit;
