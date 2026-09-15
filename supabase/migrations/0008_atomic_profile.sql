-- JP-024: one caller-scoped transaction for profile + career-context patches.
-- Apply only through the reviewed migration workflow AFTER 0007. No hosted
-- execution, data seed/backfill, existing policy change or table grant expansion.
-- Existing SQL qualification CHECK and owner/membership RLS stay in force.
--
-- RPC contract: mobile_save_profile(p_patch jsonb) -> merged profile object.
-- Omitted keys preserve current values; null clears nullable profile columns,
-- career_text becomes '', career_background becomes the neutral default object.
-- Background is a whole replacement, matching the existing ProfileUpdate API.
-- Preferences remain a separate single-table save, not cross-request atomicity.
-- The profile row lock serializes RPCs without client-version changes; disjoint
-- fields survive concurrent saves, while the same supplied field is last-writer-
-- wins. This is not compare-and-swap/stale-client conflict detection.
-- Any database failure aborts the entire function transaction. A lost HTTP ACK
-- can follow a complete commit; clients must refresh, never infer rollback.
begin;

create function public.mobile_save_profile(p_patch jsonb)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_user uuid := auth.uid();
  v_profile public.profiles%rowtype;
  v_context public.candidate_context%rowtype;
  v_key text;
  v_limit integer;
  v_onboard timestamptz;
  v_background jsonb;
  v_neutral constant jsonb := '{"profession":"","experience_level":"unspecified","qualifications":[]}'::jsonb;
begin
  if v_user is null or auth.role() is distinct from 'authenticated'
      or not public.mobile_has_access() then
    raise exception using errcode = '42501', message = 'Profile access denied';
  end if;
  if p_patch is null or jsonb_typeof(p_patch) is distinct from 'object'
      or octet_length(p_patch::text) > 1048576
      or p_patch - array['display_name', 'phone', 'base_location', 'timezone',
                        'onboarding_completed_at', 'career_text', 'career_background'] <> '{}'::jsonb then
    raise exception using errcode = 'PT422', message = 'Invalid profile patch';
  end if;

  -- Validate BEFORE either table changes, including direct authenticated RPC
  -- callers which do not pass through Pydantic. Never echo supplied values.
  foreach v_key in array array['display_name', 'phone', 'base_location', 'timezone', 'career_text'] loop
    if p_patch ? v_key and p_patch->v_key <> 'null'::jsonb then
      v_limit := case v_key when 'display_name' then 160 when 'phone' then 60
        when 'base_location' then 240 when 'timezone' then 100 else 100000 end;
      if jsonb_typeof(p_patch->v_key) is distinct from 'string'
          or char_length(p_patch->>v_key) > v_limit then
        raise exception using errcode = 'PT422', message = 'Invalid profile field';
      end if;
    end if;
  end loop;
  if p_patch ? 'onboarding_completed_at' and p_patch->'onboarding_completed_at' <> 'null'::jsonb then
    if jsonb_typeof(p_patch->'onboarding_completed_at') is distinct from 'string'
        or char_length(p_patch->>'onboarding_completed_at') > 64
        or p_patch->>'onboarding_completed_at' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt ][0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?([Zz]|[+-][0-9]{2}:[0-9]{2})$' then
      raise exception using errcode = 'PT422', message = 'Invalid onboarding timestamp';
    end if;
    begin
      v_onboard := (p_patch->>'onboarding_completed_at')::timestamptz;
    exception when invalid_datetime_format or datetime_field_overflow then
      raise exception using errcode = 'PT422', message = 'Invalid onboarding timestamp';
    end;
    if not isfinite(v_onboard) then
      raise exception using errcode = 'PT422', message = 'Invalid onboarding timestamp';
    end if;
  end if;
  if p_patch ? 'career_background' then
    v_background := case when p_patch->'career_background' = 'null'::jsonb
      then v_neutral else p_patch->'career_background' end;
    if public.mobile_career_background_is_valid(v_background) is distinct from true then
      raise exception using errcode = 'PT422', message = 'Invalid self-reported career background';
    end if;
    v_background := v_neutral || v_background;
  end if;

  -- Keep identity immutable. The existing caller INSERT/UPDATE privileges and
  -- restrictive membership/owner RLS apply, including to a missing profile.
  insert into public.profiles(user_id) values (v_user)
    on conflict (user_id) do nothing;
  select * into strict v_profile from public.profiles
    where user_id = v_user for update;
  if p_patch ?| array['display_name', 'phone', 'base_location', 'timezone', 'onboarding_completed_at'] then
    update public.profiles set
      display_name = case when p_patch ? 'display_name' then p_patch->>'display_name' else display_name end,
      phone = case when p_patch ? 'phone' then p_patch->>'phone' else phone end,
      base_location = case when p_patch ? 'base_location' then p_patch->>'base_location' else base_location end,
      timezone = case when p_patch ? 'timezone' then p_patch->>'timezone' else timezone end,
      onboarding_completed_at = case when p_patch ? 'onboarding_completed_at' then v_onboard else onboarding_completed_at end
      where user_id = v_user returning * into strict v_profile;
  end if;
  if p_patch ?| array['career_text', 'career_background'] then
    insert into public.candidate_context(user_id, career_text, career_background)
      values (v_user, coalesce(p_patch->>'career_text', ''), coalesce(v_background, v_neutral))
      on conflict (user_id) do update set
        career_text = case when p_patch ? 'career_text' then excluded.career_text else public.candidate_context.career_text end,
        career_background = case when p_patch ? 'career_background' then excluded.career_background else public.candidate_context.career_background end;
  end if;
  select * into v_context from public.candidate_context where user_id = v_user;
  -- Explicit projection prevents future operational/secret columns leaking.
  return jsonb_build_object(
    'user_id', v_profile.user_id, 'display_name', v_profile.display_name,
    'phone', v_profile.phone, 'base_location', v_profile.base_location,
    'timezone', v_profile.timezone, 'onboarding_completed_at', v_profile.onboarding_completed_at,
    'created_at', v_profile.created_at, 'updated_at', v_profile.updated_at,
    'career_text', v_context.career_text,
    'career_background', coalesce(v_context.career_background, v_neutral));
end;
$$;

revoke all on function public.mobile_save_profile(jsonb) from public, anon, authenticated, service_role;
grant execute on function public.mobile_save_profile(jsonb) to authenticated;
notify pgrst, 'reload schema';
commit;
