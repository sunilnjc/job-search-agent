-- Additive preference controls. No memberships, user data, or AI quota seeded.
-- Apply after 0011; older clients omitting this column preserve saved rules.
begin;

create function public.mobile_valid_discovery_rules(value jsonb) returns boolean
language plpgsql immutable set search_path='' as $$
declare code jsonb;
begin
  if jsonb_typeof(value) is distinct from 'object'
     or not (value ?& array['remote_country_policy','remote_country_codes','sponsorship_policy'])
     or value - array['remote_country_policy','remote_country_codes','sponsorship_policy'] <> '{}'::jsonb
     or jsonb_typeof(value->'remote_country_policy') is distinct from 'string'
     or jsonb_typeof(value->'sponsorship_policy') is distinct from 'string'
     or value->>'remote_country_policy' not in ('review','require_explicit')
     or value->>'sponsorship_policy' not in ('review','require_explicit')
     or jsonb_typeof(value->'remote_country_codes') is distinct from 'array'
  then return false; end if;
  if jsonb_array_length(value->'remote_country_codes') > 30 then return false; end if;
  for code in select * from jsonb_array_elements(value->'remote_country_codes') loop
    if jsonb_typeof(code) <> 'string' or (code #>> '{}') !~ '^[A-Z]{2}$' then return false; end if;
  end loop;
  return (select count(*) = count(distinct c) from jsonb_array_elements(value->'remote_country_codes') c);
end; $$;
revoke all on function public.mobile_valid_discovery_rules(jsonb) from public,anon;
grant execute on function public.mobile_valid_discovery_rules(jsonb) to authenticated,service_role;

alter table public.job_preferences add column discovery_rules jsonb not null
  default '{"remote_country_policy":"review","remote_country_codes":[],"sponsorship_policy":"review"}'::jsonb
  constraint job_preferences_discovery_rules_valid check (public.mobile_valid_discovery_rules(discovery_rules));
alter table public.job_preferences add constraint job_preferences_sponsorship_rule_consistent
  check (discovery_rules->>'sponsorship_policy' <> 'require_explicit' or sponsorship_required is true);
grant select(discovery_rules),insert(discovery_rules),update(discovery_rules)
  on public.job_preferences to authenticated;
notify pgrst, 'reload schema';
commit;
