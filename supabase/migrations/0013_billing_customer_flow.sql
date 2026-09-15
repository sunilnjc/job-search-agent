-- Additive customer reconciliation. Requires 0009 + 0010. Test billing only.
-- No grants to client payment writes; no changes to manual memberships/quotas.
begin;
alter table public.mobile_billing_accounts add column reconcile_requested_at timestamptz;

create function public.mobile_billing_reconcile_claim(p_user_id uuid, p_provider text, p_mode text)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare a jsonb; v_event text; v_recent timestamptz;
begin
  perform public.mobile_billing_require_service();
  a := public.mobile_billing_begin(p_user_id,p_provider,p_mode);
  if a->>'status' in ('busy','blocked','conflict','stale') then return a; end if;
  -- begin holds the account lock until this transaction commits. The timestamp
  -- and lease therefore bound concurrent requests across processes, not browsers.
  v_recent := (a->>'reconcile_requested_at')::timestamptz;
  if v_recent > clock_timestamp() - interval '30 seconds' then
    perform public.mobile_billing_release((a->>'id')::uuid,(a->>'lease_token')::uuid);
    return jsonb_build_object('status','rate_limited');
  end if;
  update public.mobile_billing_accounts set reconcile_requested_at=clock_timestamp()
    where id=(a->>'id')::uuid;
  if a->>'customer_id' is null then
    perform public.mobile_billing_release((a->>'id')::uuid,(a->>'lease_token')::uuid);
    return jsonb_build_object('status','none');
  end if;
  -- Reclaim the oldest pending sync after a failed fetch/unknown apply ACK;
  -- don't proliferate durable pending events on every retry. Provider events
  -- use evt_ IDs, never this server-only sync_ namespace.
  select event_id into v_event from public.mobile_billing_events
    where account_id=(a->>'id')::uuid and state='pending' and event_id like 'sync\_%' escape '\'
    order by created_at limit 1 for update;
  if v_event is null then
    v_event := 'sync_' || extensions.gen_random_uuid()::text;
    insert into public.mobile_billing_events(provider,mode,event_id,body_sha256,account_id,lease_token,state)
      values(p_provider,p_mode,v_event,encode(extensions.digest(v_event,'sha256'),'hex'),
        (a->>'id')::uuid,(a->>'lease_token')::uuid,'pending');
  else
    update public.mobile_billing_events set lease_token=(a->>'lease_token')::uuid
      where provider=p_provider and mode=p_mode and event_id=v_event;
  end if;
  return a || jsonb_build_object('event_id',v_event);
end;
$$;
revoke all on function public.mobile_billing_reconcile_claim(uuid,text,text) from public,anon,authenticated;
grant execute on function public.mobile_billing_reconcile_claim(uuid,text,text) to service_role;

-- Safe owner-bound view of actual entitlement, not "checkout success". No
-- vendor IDs, checkout links, customer email, event payloads or payment details.
create function public.mobile_billing_customer_status()
returns jsonb language plpgsql stable security definer set search_path = '' as $$
declare u uuid := auth.uid(); a public.mobile_billing_accounts%rowtype;
  m public.mobile_usage_memberships%rowtype; v_daily bigint; v_allowed boolean;
begin
  if auth.role() is distinct from 'authenticated' or u is null then
    raise exception 'Authenticated customer required' using errcode='42501';
  end if;
  select * into a from public.mobile_billing_accounts where user_id=u;
  select * into m from public.mobile_usage_memberships where user_id=u;
  select reserved_units into v_daily from public.mobile_ai_daily_usage
    where user_id=u and usage_day=(now() at time zone 'utc')::date;
  v_allowed := coalesce(public.mobile_has_access(),false);
  return jsonb_build_object('user_id',u,'account_exists',a.customer_id is not null,
    'subscription',case when a.id is null then null else jsonb_build_object(
      'status',a.status,'plan_key',a.plan_key,'period_start',a.period_start,
      'paid_through',a.paid_through,'cancel_at_period_end',a.cancel_at_period_end,
      'cancellation_status',a.cancellation_status) end,
    'reconciliation_pending',exists(select 1 from public.mobile_billing_events
      where account_id=a.id and state='pending'),
    'access',jsonb_build_object('allowed',v_allowed,'grant_source',m.grant_source,
      'expires_at',m.expires_at,'period_end',m.period_end,
      'period_remaining',case when v_allowed and m.period_start<=now() and m.period_end>now()
        then greatest(0,m.period_limit-m.period_reserved) else 0 end,
      'daily_remaining',case when v_allowed then greatest(0,m.daily_limit-coalesce(v_daily,0)) else 0 end));
end;
$$;
revoke all on function public.mobile_billing_customer_status() from public,anon,authenticated,service_role;
grant execute on function public.mobile_billing_customer_status() to authenticated;
commit;
