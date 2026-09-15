-- Read-only acceptance check AFTER migrations 0003 and 0004. No candidate rows are read.
-- A missing function/column is a failure, not a reason to replay migrations.
-- All checks must be true; inspect actual schema before any corrective write.
with checks(name, passed) as (
  values
    ('background_not_null', exists (
      select 1 from information_schema.columns
      where table_schema = 'public' and table_name = 'candidate_context'
        and column_name = 'career_background' and data_type = 'jsonb'
        and is_nullable = 'NO' and column_default is not null
    )),
    ('shape_constraint_validated', exists (
      select 1 from pg_constraint
      where conrelid = 'public.candidate_context'::regclass
        and conname = 'candidate_context_career_background_shape'
        and contype = 'c' and convalidated
    )),
    ('rls_enabled', (select relrowsecurity from pg_class
      where oid = 'public.candidate_context'::regclass)),
    ('authenticated_can_insert_background', has_column_privilege(
      'authenticated', 'public.candidate_context', 'career_background', 'INSERT')),
    ('authenticated_can_update_background', has_column_privilege(
      'authenticated', 'public.candidate_context', 'career_background', 'UPDATE')),
    ('authenticated_cannot_reassign_owner', not has_column_privilege(
      'authenticated', 'public.candidate_context', 'user_id', 'UPDATE')),
    ('validator_not_privileged', not (select prosecdef from pg_proc
      where oid = 'public.mobile_career_background_is_valid(jsonb)'::regprocedure)),
    ('anonymous_cannot_execute_validator', not has_function_privilege(
      'anon', 'public.mobile_career_background_is_valid(jsonb)', 'EXECUTE')),
    ('valid_self_report_accepted', public.mobile_career_background_is_valid(
      '{"profession":"Teaching","experience_level":"career_change","qualifications":[{"name":"Synthetic course","kind":"education","status":"in_progress","expires_on":null}]}'::jsonb)),
    ('invalid_calendar_date_rejected', not public.mobile_career_background_is_valid(
      '{"qualifications":[{"name":"Synthetic licence","kind":"licence","expires_on":"2026-02-30"}]}'::jsonb)),
    ('invented_verification_rejected', not public.mobile_career_background_is_valid(
      '{"qualifications":[{"name":"Synthetic licence","kind":"licence","verified":true}]}'::jsonb)),
    ('invalid_status_rejected', not public.mobile_career_background_is_valid(
      '{"qualifications":[{"name":"Synthetic course","kind":"education","status":"verified"}]}'::jsonb)),
    ('blank_name_rejected', not public.mobile_career_background_is_valid(
      '{"qualifications":[{"name":"   ","kind":"education"}]}'::jsonb))
)
select bool_and(coalesce(passed, false)) as all_checks_pass,
       jsonb_object_agg(name, coalesce(passed, false)) as checks
from checks;

-- Review the full owner policy separately; merely enabling RLS is insufficient.
select policyname, roles, cmd, qual, with_check
from pg_policies
where schemaname = 'public' and tablename = 'candidate_context';
