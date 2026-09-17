-- Additive GTM operator metrics and structured fit explanations.
-- No memberships, quota, billing, or public-page publishing. Apply after 0011.
-- job_scores.fit_explanation is optional JSON; legacy rows keep rationale only.
begin;

alter table public.job_scores add column fit_explanation jsonb
  constraint job_scores_fit_explanation_object check (
    fit_explanation is null
    or (
      jsonb_typeof(fit_explanation) = 'object'
      and fit_explanation - array['why', 'evidence', 'uncertainty'] = '{}'::jsonb
      and jsonb_typeof(fit_explanation->'why') = 'array'
      and jsonb_typeof(fit_explanation->'evidence') = 'array'
      and jsonb_typeof(fit_explanation->'uncertainty') = 'array'
      and jsonb_array_length(fit_explanation->'why') <= 12
      and jsonb_array_length(fit_explanation->'evidence') <= 16
      and jsonb_array_length(fit_explanation->'uncertainty') <= 16
    )
  );

comment on column public.job_scores.fit_explanation is
  'Additive why/evidence/uncertainty object. Legacy clients use rationale.';

-- Additive column grant only. Existing job_scores owner RLS and other column
-- writes stay unchanged. Scores remain insert-only from the product API.
grant insert (fit_explanation) on public.job_scores to authenticated;

create or replace function public.mobile_operator_require_service()
returns void
language plpgsql
stable
security definer
set search_path = ''
as $$
begin
  if auth.role() is distinct from 'service_role' then
    raise exception 'Operator metrics require the service role';
  end if;
end;
$$;

create or replace function public.mobile_operator_packet_burn()
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  result jsonb;
begin
  perform public.mobile_operator_require_service();
  with prepares as (
    select r.id, r.user_id, r.job_id, r.completed_at, r.created_at, r.output_summary
    from public.model_runs r
    where r.operation = 'prepare_documents' and r.status = 'succeeded'
  ),
  packets as (
    select
      a.id is null as incomplete,
      coalesce((p.output_summary #>> '{usage,estimated_cost_usd}')::numeric, 0)
        + coalesce((a.output_summary #>> '{usage,estimated_cost_usd}')::numeric, 0) as estimated_cost_usd,
      coalesce((p.output_summary #>> '{usage,reserved_units}')::bigint, 0)
        + coalesce((a.output_summary #>> '{usage,reserved_units}')::bigint, 0) as reserved_units,
      (p.output_summary ? 'usage') as prepare_metered
    from prepares p
    left join lateral (
      select r.id, r.output_summary
      from public.model_runs r
      where r.user_id = p.user_id and r.job_id = p.job_id
        and r.operation = 'rank_job' and r.status = 'succeeded'
        and coalesce(r.completed_at, r.created_at) <= coalesce(p.completed_at, p.created_at)
      order by coalesce(r.completed_at, r.created_at) desc
      limit 1
    ) a on true
  )
  select jsonb_build_object(
    'completed_packets', (select count(*)::int from packets where not incomplete),
    'incomplete_packets', (select count(*)::int from packets where incomplete),
    'metered_packets', (select count(*)::int from packets where not incomplete and prepare_metered),
    'mean_estimated_cost_usd', (
      select round(avg(estimated_cost_usd), 6) from packets where not incomplete and prepare_metered
    ),
    'mean_reserved_units', (
      select round(avg(reserved_units), 4) from packets where not incomplete and prepare_metered
    ),
    'cost_basis', 'public_list_price_estimate'
  ) into result;
  return result;
end;
$$;

create or replace function public.mobile_operator_dogfood_week(p_week_start date)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_start timestamptz;
  v_end timestamptz;
  v_year int;
  v_week int;
begin
  perform public.mobile_operator_require_service();
  if p_week_start is null or not isfinite(p_week_start) then
    raise exception 'ISO week start date is required';
  end if;
  v_start := (p_week_start::timestamp at time zone 'UTC');
  if extract(isodow from p_week_start)::int <> 1 then
    raise exception 'Week start must be an ISO Monday';
  end if;
  v_end := v_start + interval '7 days';
  v_year := extract(isoyear from p_week_start)::int;
  v_week := extract(week from p_week_start)::int;
  return jsonb_build_object(
    'iso_week', v_year::text || '-W' || lpad(v_week::text, 2, '0'),
    'period_start', to_char(v_start at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
    'period_end', to_char(v_end at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
    'roles_reviewed', (
      select count(distinct job_id)::int from (
        select job_id from public.job_scores
          where created_at >= v_start and created_at < v_end
        union
        select job_id from public.model_runs
          where operation = 'rank_job' and status = 'succeeded'
            and coalesce(completed_at, created_at) >= v_start
            and coalesce(completed_at, created_at) < v_end
      ) reviewed
    ),
    'roles_prepared', (
      select count(distinct job_id)::int from public.model_runs
      where operation = 'prepare_documents' and status = 'succeeded'
        and coalesce(completed_at, created_at) >= v_start
        and coalesce(completed_at, created_at) < v_end
    ),
    'roles_applied', (
      select count(distinct job_id)::int from public.applications
      where status = 'submitted'
        and coalesce(applied_at, updated_at) >= v_start
        and coalesce(applied_at, updated_at) < v_end
    ),
    'replies', null,
    'replies_status', 'not_tracked'
  );
end;
$$;

revoke all on function public.mobile_operator_require_service() from public, anon, authenticated;
revoke all on function public.mobile_operator_packet_burn() from public, anon, authenticated;
revoke all on function public.mobile_operator_dogfood_week(date) from public, anon, authenticated;
grant execute on function public.mobile_operator_require_service() to service_role;
grant execute on function public.mobile_operator_packet_burn() to service_role;
grant execute on function public.mobile_operator_dogfood_week(date) to service_role;

commit;
