"""Real disposable PostgreSQL gate for 0010; no hosted credentials or deletion.

Platform auth/storage tables are minimal stubs; all product migrations execute
verbatim. A skip means unavailable SQL infrastructure, never a privacy pass.
"""
import unittest
from pathlib import Path

from local_postgres import DisposablePostgres, SUPABASE_TEST_SCHEMAS

ROOT = Path(__file__).resolve().parents[1]
OWNER = '00000000-0000-4000-8000-000000000001'
OTHER = '00000000-0000-4000-8000-000000000002'


class PrivacyPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = DisposablePostgres()
        cls.addClassCleanup(cls.db.close)
        cls.db.execute(SUPABASE_TEST_SCHEMAS)
        cls.db.execute('''
          alter table auth.users add column if not exists email text;
          alter table auth.users add column if not exists phone text;
          alter table auth.users add column if not exists email_confirmed_at timestamptz;
          alter table auth.users add column if not exists created_at timestamptz default now();
          alter table storage.buckets add column if not exists file_size_limit bigint;
          alter table storage.buckets add column if not exists allowed_mime_types text[];
          alter table storage.objects add column if not exists owner_id text;
        ''')
        for path in sorted((ROOT/'supabase/migrations').glob('*.sql')):
            if path.name[:4].isdigit() and int(path.name[:4]) <= 10:
                cls.db.execute(path.read_text())
        cls.db.execute("""
          insert into auth.users(id,email,email_confirmed_at) values
          ('00000000-0000-4000-8000-000000000001','one@example.test',now()),
          ('00000000-0000-4000-8000-000000000002','two@example.test',now());
          insert into public.mobile_usage_memberships(user_id,enabled) values
          ('00000000-0000-4000-8000-000000000001',true),
          ('00000000-0000-4000-8000-000000000002',true);
        """)

    def transaction(self, sql, *, user=OWNER, role='authenticated', before=''):
        return self.db.execute("begin;\n" + before + "\nset local role " + role + ";\n"
            "set local request.jwt.claim.role='" + role + "';\n"
            "set local request.jwt.claim.sub='" + user + "';\n" + sql + "\nrollback;")

    def test_stale_worker_rejects_before_queueing_or_freezing(self):
        self.transaction("""
        do $$ declare result jsonb; begin
          result:=public.mobile_privacy_request('export');
          assert result->>'error'='worker_unavailable';
          assert public.mobile_has_access();
        end $$;
        reset role;
        do $$ begin assert (select count(*)=0 from public.mobile_privacy_requests); end $$;
        """)

    def test_confirmed_owner_request_idempotent_and_not_paywalled(self):
        self.transaction("""
        do $$ declare a jsonb; b jsonb; begin
          a:=public.mobile_privacy_request('export'); b:=public.mobile_privacy_request('export');
          assert a->'request'->>'id'=b->'request'->>'id';
          assert not (a->'request' ? 'lease_token');
          assert not (a->'request' ? 'user_id');
          assert jsonb_array_length(public.mobile_privacy_status()->'requests')=1;
        end $$;
        """, before="select public.mobile_privacy_heartbeat(); update public.mobile_usage_memberships set enabled=false;")

    def test_erasure_confirmation_freezes_and_cannot_be_reenabled(self):
        self.transaction("""
        do $$ declare result jsonb; begin
          result:=public.mobile_privacy_request('erase','DELETE MY ACCOUNT','two@example.test');
          assert result->>'error'='confirmation'; assert public.mobile_has_access();
          result:=public.mobile_privacy_request('erase','DELETE MY ACCOUNT','one@example.test');
          assert result->'request'->>'kind'='erase'; assert not public.mobile_has_access();
        end $$;
        reset role;
        update public.mobile_usage_memberships set enabled=true;
        do $$ begin assert not (select enabled from public.mobile_usage_memberships where user_id='00000000-0000-4000-8000-000000000001'); end $$;
        """, before='select public.mobile_privacy_heartbeat();')

    def test_owner_status_and_storage_export_paths_are_isolated(self):
        self.transaction("""
        do $$ begin
          assert jsonb_array_length(public.mobile_privacy_status()->'requests')=1;
          assert (select count(*)=1 from public.mobile_privacy_export_paths());
          assert (select count(*)=1 from storage.objects);
          assert (select name like '00000000-0000-4000-8000-000000000001/%' from storage.objects);
        end $$;
        """, before="""
        -- Use the production processing lease -> fenced Storage -> completion
        -- lifecycle; do not disable the trigger to manufacture ready exports.
        do $$ declare owner uuid; request_id uuid; job jsonb; path text; begin
          foreach owner in array array[
            '00000000-0000-4000-8000-000000000001'::uuid,
            '00000000-0000-4000-8000-000000000002'::uuid] loop
            insert into public.mobile_privacy_requests(user_id,kind) values(owner,'export') returning id into request_id;
            job:=public.mobile_privacy_claim(request_id);
            path:=owner::text||'/'||request_id::text||'/account.zip';
            insert into storage.objects(bucket_id,name,user_metadata) values('account-exports',path,
              jsonb_build_object('privacy_lease',job->>'lease_token'));
            assert public.mobile_privacy_finish(request_id,(job->>'lease_token')::uuid,'complete',null,path);
          end loop;
        end $$;
        """)

    def test_authenticated_cannot_claim_snapshot_finish_or_write_control_rows(self):
        self.transaction("""
        do $$ begin
          assert not has_function_privilege('authenticated','public.mobile_privacy_claim(uuid)','EXECUTE');
          assert not has_function_privilege('authenticated','public.mobile_privacy_snapshot(uuid,uuid)','EXECUTE');
          assert not has_function_privilege('anon','public.mobile_privacy_request(text,text,text)','EXECUTE');
          assert not has_table_privilege('authenticated','public.mobile_privacy_requests','INSERT');
          assert not has_table_privilege('authenticated','public.mobile_privacy_worker_health','UPDATE');
        end $$;
        """)

    def test_snapshot_includes_journal_orphans_and_lease_cannot_cross_request(self):
        self.transaction("""
        do $$ declare job jsonb; data jsonb; begin
          job:=public.mobile_privacy_claim();
          data:=public.mobile_privacy_snapshot((job->>'id')::uuid,(job->>'lease_token')::uuid);
          assert data->>'user_id'='00000000-0000-4000-8000-000000000001';
          assert data->'tables' ? 'mobile_resume_operations';
          assert data->'tables' ? 'mobile_artifact_operations';
          assert data->'tables' ? 'mobile_billing_export';
          assert jsonb_array_length(data->'objects')=1;
          assert not(data->'account' ? 'raw_user_meta_data');
          begin
            perform public.mobile_privacy_snapshot((job->>'id')::uuid,gen_random_uuid());
            raise exception 'Wrong lease accepted';
          exception when raise_exception then
            assert sqlerrm='Invalid lease';
          end;
        end $$;
        """, role='service_role', before="""
        insert into public.mobile_privacy_requests(user_id,kind) values('00000000-0000-4000-8000-000000000001','export');
        insert into storage.objects(bucket_id,name,owner_id) values('resumes','00000000-0000-4000-8000-000000000001/orphan.pdf','00000000-0000-4000-8000-000000000001');
        """)

    def test_expired_export_not_readable_and_stale_auth_is_not_live_user(self):
        self.transaction("""
        do $$ begin assert not public.mobile_privacy_live_user();
          assert public.mobile_privacy_status()->>'error'='unverified';
          assert not public.mobile_has_access();
        end $$;
        """, before="delete from auth.users where id='"+OWNER+"';")


if __name__ == '__main__': unittest.main()
