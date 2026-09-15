"""Real PostgreSQL JP-024 gate, using existing binaries and a disposable cluster.

PYTHONPATH=src:tests .venv/bin/python -B -m unittest -v test_mobile_profile_sql
Optionally set JOBPURSUIT_POSTGRES_BIN to an existing native bin directory.
No binaries -> explicit skip, NEVER a claim of SQL/RLS runtime verification.
The auth/storage interfaces are minimal local stubs; product migrations 0001-8
are executed verbatim. No hosted DSN, owner account, Docker, install or network.
"""
from __future__ import annotations

import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

from local_postgres import DisposablePostgres, SUPABASE_TEST_SCHEMAS

ROOT = Path(__file__).resolve().parents[1]
OWNER = "00000000-0000-4000-8000-000000000001"
OTHER = "00000000-0000-4000-8000-000000000002"
BLOCKED = "00000000-0000-4000-8000-000000000003"
MIGRATIONS = (
    "0001_beta_multi_tenant.sql", "0002_mobile_career_workspace.sql",
    "0003_profession_neutral_background.sql", "0004_career_validator_anonymous_execute.sql",
    "0005_mobile_resume_operations.sql", "0006_mobile_artifact_operations.sql",
    "0007_mobile_usage_controls.sql", "0008_atomic_profile.sql",
)


def identity(owner=OWNER, role="authenticated"):
    # Constants controlled only by this isolated test fixture, not HTTP inputs.
    return "set local role " + role + "; set local request.jwt.claim.role = '" + role + "'; set local request.jwt.claim.sub = '" + owner + "';\n"


class AtomicProfilePostgresTests(unittest.TestCase):
    migrations = MIGRATIONS

    @classmethod
    def setUpClass(cls):
        cls.db = DisposablePostgres()
        cls.addClassCleanup(cls.db.close)
        cls.db.execute(SUPABASE_TEST_SCHEMAS)
        for migration in cls.migrations:
            cls.db.execute((ROOT / "supabase/migrations" / migration).read_text())
        cls.db.execute("""
            insert into auth.users(id,email,email_confirmed_at) values
              ('00000000-0000-4000-8000-000000000001','a@fixture.invalid',now()),
              ('00000000-0000-4000-8000-000000000002','b@fixture.invalid',now()),
              ('00000000-0000-4000-8000-000000000003','blocked@fixture.invalid',now());
            insert into public.mobile_usage_memberships(user_id,enabled) values
              ('00000000-0000-4000-8000-000000000001',true),
              ('00000000-0000-4000-8000-000000000002',true),
              ('00000000-0000-4000-8000-000000000003',false);
            update public.profiles set display_name='Original name', phone='Original phone', timezone='UTC';
            insert into public.candidate_context(user_id,career_text) values
              ('00000000-0000-4000-8000-000000000001','Original career'),
              ('00000000-0000-4000-8000-000000000002','Other career');
        """)

    def transaction(self, sql, *, owner=OWNER, role="authenticated", before=""):
        return self.db.execute("begin;\n" + before + identity(owner, role) + sql + "\nrollback;")

    def test_atomic_merge_omission_explicit_null_and_full_response(self):
        self.transaction(r"""
        do $$ declare saved jsonb; begin
          saved := public.mobile_save_profile('{"display_name":"Changed","career_text":"Changed career"}');
          assert saved->>'display_name' = 'Changed';
          assert saved->>'career_text' = 'Changed career';
          assert saved->>'phone' = 'Original phone';
          assert saved->>'timezone' = 'UTC';
          assert saved ?& array['user_id','created_at','updated_at','career_background'];
          assert (select display_name = 'Changed' from public.profiles where user_id = auth.uid());
          assert (select career_text = 'Changed career' from public.candidate_context where user_id = auth.uid());
          saved := public.mobile_save_profile('{"phone":null,"career_text":null,"career_background":null}');
          assert saved->'phone' = 'null'::jsonb;
          assert saved->>'career_text' = '';
          assert saved->'career_background' = '{"profession":"","experience_level":"unspecified","qualifications":[]}'::jsonb;
          assert public.mobile_save_profile('{}') = saved;
          assert (select count(*) = 1 from public.profiles);
        end $$;
        """)

    def test_sql_validation_rejects_owner_injection_and_invalid_qualifications_before_changes(self):
        self.transaction(r"""
        do $$ declare bad jsonb; begin
          for bad in select value from jsonb_array_elements('[
            {"user_id":"00000000-0000-4000-8000-000000000002","display_name":"Forbidden"},
            {"enabled":true}, {"display_name":42}, {"phone":[]},
            {"onboarding_completed_at":"2026-02-30T00:00:00Z"},
            {"onboarding_completed_at":"2026-01-01T00:00:00"},
            {"career_background":{"verified":true}},
            {"career_background":{"qualifications":[{"name":"Q","kind":"licence","status":"verified"}]}},
            {"career_background":{"qualifications":[{"name":"Q","kind":"licence","expires_on":"2026-02-30"}]}},
            {"career_background":{"qualifications":[{"name":" ","kind":"licence"}]}},
            {"career_background":[]}, [], null
          ]'::jsonb) loop
            begin
              perform public.mobile_save_profile(bad);
              raise exception 'Invalid patch unexpectedly accepted';
            exception when sqlstate 'PT422' then null;
            end;
          end loop;
          assert (select display_name = 'Original name' from public.profiles where user_id=auth.uid());
          assert (select career_text = 'Original career' from public.candidate_context where user_id=auth.uid());
        end $$;
        """)

    def test_second_table_failure_rolls_back_profile_and_context(self):
        self.transaction(r"""
        do $$ begin
          begin
            perform public.mobile_save_profile('{"display_name":"Must roll back","career_text":"FAIL_CONTEXT"}');
            raise exception 'Failure injection did not fire';
          exception when check_violation then null;
          end;
          assert (select display_name = 'Original name' from public.profiles where user_id=auth.uid());
          assert (select career_text = 'Original career' from public.candidate_context where user_id=auth.uid());
          perform public.mobile_save_profile('{"display_name":"Still usable","career_text":"Valid"}');
        end $$;
        """, before=r"""
        create function public.profile_test_reject_context() returns trigger language plpgsql as $$
        begin
          if new.career_text = 'FAIL_CONTEXT' then raise check_violation using message='Synthetic rejection'; end if;
          return new;
        end $$;
        create trigger profile_test_reject_context before insert or update on public.candidate_context
          for each row execute function public.profile_test_reject_context();
        """)

    def test_other_member_updates_only_own_rows(self):
        self.transaction(r"""
        do $$ declare saved jsonb; begin
          saved := public.mobile_save_profile('{"display_name":"Other member change","career_text":"Other changed career"}');
          assert saved->>'user_id' = '00000000-0000-4000-8000-000000000002';
          assert (select count(*) = 1 from public.profiles);
          assert not exists(select 1 from public.profiles where user_id='00000000-0000-4000-8000-000000000001');
        end $$;
        reset role;
        do $$ begin
          assert (select display_name = 'Original name' from public.profiles where user_id='00000000-0000-4000-8000-000000000001');
          assert (select career_text = 'Original career' from public.candidate_context where user_id='00000000-0000-4000-8000-000000000001');
        end $$;
        """, owner=OTHER)

    def test_nonmember_anonymous_and_service_role_are_denied(self):
        denied = r"""
        do $$ begin
          begin
            perform public.mobile_save_profile('{"display_name":"Forbidden"}');
            raise exception 'Unauthorized save unexpectedly accepted';
          exception when insufficient_privilege then null;
          end;
        end $$;
        """
        self.transaction(denied, owner=BLOCKED)
        self.transaction(denied, owner="", role="anon")
        self.transaction(denied, role="service_role")

    def test_no_grant_expansion_and_existing_sql_check_still_enforced(self):
        self.transaction(r"""
        do $$ begin
          assert not has_column_privilege('authenticated','public.candidate_context','user_id','UPDATE');
          assert not has_function_privilege('anon','public.mobile_save_profile(jsonb)','EXECUTE');
          assert not has_function_privilege('service_role','public.mobile_save_profile(jsonb)','EXECUTE');
          assert not (select prosecdef from pg_proc where oid='public.mobile_save_profile(jsonb)'::regprocedure);
          begin
            update public.candidate_context set career_background='{"verified":true}' where user_id=auth.uid();
            raise exception 'Career qualification CHECK unexpectedly bypassed';
          exception when check_violation then null;
          end;
        end $$;
        """)

    def test_missing_context_is_not_created_by_unrelated_patch(self):
        self.transaction(r"""
        do $$ declare saved jsonb; begin
          saved := public.mobile_save_profile('{"phone":"Phone only"}');
          assert saved->'career_text' = 'null'::jsonb;
          assert not exists(select 1 from public.candidate_context);
          saved := public.mobile_save_profile('{"career_background":{"profession":"Teaching"}}');
          assert saved->'career_background'->>'profession' = 'Teaching';
          assert saved->'career_background'->>'experience_level' = 'unspecified';
          assert saved->>'career_text' = '';
        end $$;
        """, before="delete from public.candidate_context where user_id='" + OWNER + "';")

    def test_concurrent_disjoint_patches_preserve_both_fields(self):
        barrier = Barrier(2)

        def worker(payload):
            barrier.wait(timeout=10)
            return self.db.execute("begin;\n" + identity() + "select public.mobile_save_profile('" + json.dumps(payload)
                                   + "'::jsonb);\ncommit;")

        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(worker, {"phone": "Concurrent phone"}),
                           pool.submit(worker, {"timezone": "Concurrent timezone"})]
                for future in futures:
                    future.result(timeout=30)
            self.transaction(r"""
            do $$ declare saved jsonb; begin
              saved := public.mobile_save_profile('{}');
              assert saved->>'phone' = 'Concurrent phone';
              assert saved->>'timezone' = 'Concurrent timezone';
              assert saved->>'career_text' = 'Original career';
            end $$;
            """)
        finally:
            self.db.execute("update public.profiles set phone='Original phone', timezone='UTC' where user_id='" + OWNER + "';")
