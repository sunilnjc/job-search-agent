-- Follow-up to the already-applied 0003 migration; do not rewrite its history.
-- The hosted project grants anon EXECUTE directly through default privileges.
-- Revoking PUBLIC in 0003 does not remove that direct role grant.
-- This pure validator remains executable by authenticated users for constraints;
-- no candidate rows, ownership rules, or other function permissions are changed.
begin;

revoke execute on function public.mobile_career_background_is_valid(jsonb) from anon;

commit;
