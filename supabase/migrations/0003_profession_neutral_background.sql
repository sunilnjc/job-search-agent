-- Additive only: 0002 is already deployed and must not be rewritten.
-- All qualification facts are user-confirmed SELF-REPORTS, not independently
-- verified credentials. No role/entitlement/policy or owner updates are granted.
begin;

create function public.mobile_career_background_is_valid(background jsonb)
returns boolean
language plpgsql
immutable
strict
parallel safe
set search_path = pg_catalog
as $$
declare
  qualification jsonb;
  expiry text;
begin
  if jsonb_typeof(background) is distinct from 'object'
      or octet_length(background::text) > 300000 then
    return false;
  end if;
  if background - array['profession', 'experience_level', 'qualifications'] <> '{}'::jsonb then
    return false;
  end if;
  if background ? 'profession' then
    if jsonb_typeof(background->'profession') is distinct from 'string'
        or char_length(background->>'profession') > 120 then
      return false;
    end if;
  end if;
  if background ? 'experience_level' then
    if jsonb_typeof(background->'experience_level') is distinct from 'string'
        or background->>'experience_level' not in ('unspecified', 'student', 'entry', 'mid', 'senior', 'career_change') then
      return false;
    end if;
  end if;
  if background ? 'qualifications' then
    if jsonb_typeof(background->'qualifications') is distinct from 'array' then
      return false;
    end if;
    if jsonb_array_length(background->'qualifications') > 30 then
      return false;
    end if;
    for qualification in select value from jsonb_array_elements(background->'qualifications')
    loop
      if jsonb_typeof(qualification) is distinct from 'object' then
        return false;
      end if;
      if qualification - array['name', 'kind', 'status', 'jurisdiction', 'expires_on', 'evidence_note'] <> '{}'::jsonb
          or not (qualification ?& array['name', 'kind']) then
        return false;
      end if;
      if jsonb_typeof(qualification->'name') is distinct from 'string'
          or not ((qualification->>'name') ~ '[^[:space:]]')
          or char_length(qualification->>'name') > 160 then
        return false;
      end if;
      if jsonb_typeof(qualification->'kind') is distinct from 'string'
          or qualification->>'kind' not in ('education', 'licence', 'certification', 'other') then
        return false;
      end if;
      if qualification ? 'status' then
        if jsonb_typeof(qualification->'status') is distinct from 'string'
            or qualification->>'status' not in ('current', 'expired', 'in_progress', 'not_held', 'unknown') then
          return false;
        end if;
      end if;
      if qualification ? 'jurisdiction' then
        if jsonb_typeof(qualification->'jurisdiction') is distinct from 'string'
            or char_length(qualification->>'jurisdiction') > 160 then
          return false;
        end if;
      end if;
      if qualification ? 'evidence_note' then
        if jsonb_typeof(qualification->'evidence_note') is distinct from 'string'
            or char_length(qualification->>'evidence_note') > 2000 then
          return false;
        end if;
      end if;
      if qualification ? 'expires_on' and qualification->'expires_on' <> 'null'::jsonb then
        if jsonb_typeof(qualification->'expires_on') is distinct from 'string' then
          return false;
        end if;
        expiry := qualification->>'expires_on';
        if expiry !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' then
          return false;
        end if;
        begin
          perform expiry::date;
        exception when invalid_datetime_format or datetime_field_overflow then
          return false;
        end;
      end if;
    end loop;
  end if;
  return true;
end;
$$;

revoke all on function public.mobile_career_background_is_valid(jsonb) from public;
grant execute on function public.mobile_career_background_is_valid(jsonb) to authenticated;

alter table public.candidate_context
  add column career_background jsonb not null
    default '{"profession":"","experience_level":"unspecified","qualifications":[]}'::jsonb
    constraint candidate_context_career_background_shape
    check (public.mobile_career_background_is_valid(career_background));

comment on column public.candidate_context.career_background is
  'User-confirmed self-reported career background; not independently verified credentials.';

grant insert (career_background) on public.candidate_context to authenticated;
grant update (career_background) on public.candidate_context to authenticated;

-- Existing owner-only RLS, immutable user_id grants, and updated_at trigger from
-- 0002 remain unchanged. No data seeds, policy changes, or privileged functions.
commit;
