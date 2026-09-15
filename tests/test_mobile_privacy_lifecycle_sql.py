"""Independent privacy lifecycle gates against disposable, real PostgreSQL.

Applies 0001..0010 verbatim. Only platform Auth/Storage interfaces are stubs;
deleting fixture storage.objects below simulates a successful Storage API delete,
NOT a production blob deletion strategy. No hosted connections/provider calls.
Storage permission probes and final writes are SQL simulations of the upstream
contract, not hosted S3 cleanup proof. No observation-only passes remain.
"""
import asyncio
import base64
import io
import json
import time
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from fastapi import HTTPException

from jobagent.mobile.account_privacy import ErasureRequest, request_erasure
from jobagent.mobile.privacy_worker import PrivacyBlocked, PrivacyWorker, WorkerSettings
from jobagent.mobile.repository import MobileRepository
from local_postgres import DisposablePostgres, SUPABASE_TEST_SCHEMAS

ROOT = Path(__file__).resolve().parents[1]
OWNER = "00000000-0000-4000-8000-000000000001"
OTHER = "00000000-0000-4000-8000-000000000002"
ERASE = "11111111-1111-4111-8111-111111111111"
EXPORT = "22222222-2222-4222-8222-222222222222"


def completed_export_fixture(user=OWNER, request_id=EXPORT, expires="now()+interval '1 day'"):
    """Publish via a real claim/fenced Storage write/finish, then advance time."""
    return f"""
      insert into public.mobile_privacy_requests(id,user_id,kind) values('{request_id}','{user}','export');
      do $$ declare job jsonb; begin
        job:=public.mobile_privacy_claim('{request_id}');
        insert into storage.objects(bucket_id,name,owner_id,version,metadata,user_metadata)
          values('account-exports','{user}/{request_id}/account.zip','{user}','fixture-version',
            '{{"mimetype":"application/zip","size":32}}',jsonb_build_object('privacy_lease',job->>'lease_token'));
        assert public.mobile_privacy_finish('{request_id}',(job->>'lease_token')::uuid,'complete',
          null,'{user}/{request_id}/account.zip');
      end $$;
      update public.mobile_privacy_requests set expires_at={expires} where id='{request_id}';
    """


class PrivacyLifecyclePostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = DisposablePostgres()
        cls.addClassCleanup(cls.db.close)
        cls.db.execute(SUPABASE_TEST_SCHEMAS)
        for path in sorted((ROOT / "supabase/migrations").glob("*.sql")):
            if path.name[:4].isdigit() and int(path.name[:4]) <= 10:
                cls.db.execute(path.read_text())
        cls.db.execute(f"""
          insert into auth.users(id,email,email_confirmed_at) values
            ('{OWNER}','one@example.test',now()),('{OTHER}','two@example.test',now());
          insert into public.mobile_usage_memberships(user_id,enabled) values
            ('{OWNER}',true),('{OTHER}',true);
        """)

    def transaction(self, sql, *, role="service_role", user=OWNER, before=""):
        return self.db.execute(f"""begin;
          {before}
          set local role {role};
          set local request.jwt.claim.role='{role}';
          set local request.jwt.claim.sub='{user}';
          {sql}
          rollback;
        """)

    def test_erasure_finish_requires_auth_and_all_owned_storage_absent(self):
        self.transaction(f"""
          do $$ declare job jsonb; begin
            job:=public.mobile_privacy_claim('{ERASE}');
            begin
              perform public.mobile_privacy_finish('{ERASE}',(job->>'lease_token')::uuid,'complete');
              raise exception 'Finish accepted live Auth';
            exception when raise_exception then assert sqlerrm='Erasure incomplete'; end;
            assert public.mobile_billing_cancel_claim('{OWNER}','{ERASE}')->>'status'='ready';
          end $$;
          reset role;
          delete from auth.users where id='{OWNER}';
          -- Prefix-owned, owner_id-owned in an unexpected bucket, and a second
          -- user's file must all be distinguished by the SQL finish guard.
          do $$ declare token uuid; item record; begin
            select lease_token into token from public.mobile_privacy_requests where id='{ERASE}';
            for item in select id from storage.objects where name like '{OWNER}/%'
                        or owner_id='{OWNER}' loop
              begin
                perform public.mobile_privacy_finish('{ERASE}',token,'complete');
                raise exception 'Finish accepted retained storage';
              exception when raise_exception then assert sqlerrm='Erasure incomplete'; end;
              delete from storage.objects where id=item.id;
            end loop;
            assert public.mobile_privacy_finish('{ERASE}',token,'complete');
            assert (select user_id is null and state='complete' and lease_token is null
                    and lease_until is null and export_path is null
                    from public.mobile_privacy_requests where id='{ERASE}');
            assert (select count(*)=1 from storage.objects where owner_id='{OTHER}');
            assert exists(select 1 from auth.users where id='{OTHER}');
          end $$;
        """, before=completed_export_fixture() + f"""
          insert into public.mobile_privacy_requests(id,user_id,kind) values('{ERASE}','{OWNER}','erase');
          insert into storage.buckets(id,name) values('unexpected-fixture-bucket','unexpected-fixture-bucket');
          insert into storage.objects(bucket_id,name,owner_id) values
            ('resumes','{OWNER}/orphan.pdf',null),
            ('unexpected-fixture-bucket','not-an-owner-prefix','{OWNER}'),
            ('resumes','{OTHER}/keep.pdf','{OTHER}');
        """)

    def test_auth_delete_blocked_until_billing_cancel_receipt_is_confirmed(self):
        self.transaction(f"""
          reset role;
          do $$ begin
            begin
              delete from auth.users where id='{OWNER}';
              raise exception 'Unconfirmed cancellation accepted';
            exception when raise_exception then
              assert sqlerrm='Billing cancellation must be confirmed before Auth deletion';
            end;
            assert exists(select 1 from auth.users where id='{OWNER}');
          end $$;
          set local role service_role;
          do $$ declare claim jsonb; receipt jsonb; begin
            claim:=public.mobile_billing_cancel_claim('{OWNER}','{ERASE}');
            receipt:=jsonb_build_object('customer_id','cus_fixture','cancellation_confirmed',true,
              'no_future_collection',true,'open_subscription_ids','[]'::jsonb,
              'open_checkout_ids','[]'::jsonb,'unresolved_operation_ids','[]'::jsonb);
            assert public.mobile_billing_cancel_finish((claim->>'id')::uuid,'{ERASE}',
              (claim->>'lease_token')::uuid,receipt||'{{"no_future_collection":false}}')->>'status'='blocked';
            assert public.mobile_billing_cancel_finish((claim->>'id')::uuid,'{ERASE}',
              (claim->>'lease_token')::uuid,receipt)->>'status'='ready';
          end $$;
          reset role;
          delete from auth.users where id='{OWNER}';
          do $$ begin
            assert not exists(select 1 from auth.users where id='{OWNER}');
            -- Financial tombstones are intentionally NOT anonymized by 0010;
            -- assert their scope here, not a claim of zero retained billing PII.
            assert exists(select 1 from public.mobile_billing_accounts where user_id is null
              and customer_id='cus_fixture' and cancellation_status='confirmed');
          end $$;
        """, before=f"""
          insert into public.mobile_billing_accounts(user_id,provider,mode,customer_id,
            subscription_id,status) values('{OWNER}','fixture','test','cus_fixture','sub_fixture','active');
        """)

    def test_expired_and_replaced_leases_cannot_snapshot_or_finish(self):
        self.transaction(f"""
          do $$ declare first_job jsonb; next_job jsonb; begin
            first_job:=public.mobile_privacy_claim('{EXPORT}');
            assert public.mobile_privacy_claim('{EXPORT}') is null;
            update public.mobile_privacy_requests set lease_until=clock_timestamp()-interval '1 second'
              where id='{EXPORT}';
            assert not public.mobile_privacy_finish('{EXPORT}',(first_job->>'lease_token')::uuid,'blocked');
            begin
              perform public.mobile_privacy_snapshot('{EXPORT}',(first_job->>'lease_token')::uuid);
              raise exception 'Expired snapshot accepted';
            exception when raise_exception then assert sqlerrm='Invalid lease'; end;
            next_job:=public.mobile_privacy_claim('{EXPORT}');
            assert next_job->>'lease_token'<>first_job->>'lease_token';
            assert not public.mobile_privacy_finish('{EXPORT}',(first_job->>'lease_token')::uuid,'complete',
              null,'{OWNER}/{EXPORT}/account.zip');
            begin
              perform public.mobile_privacy_snapshot('{EXPORT}',(first_job->>'lease_token')::uuid);
              raise exception 'Replaced snapshot accepted';
            exception when raise_exception then assert sqlerrm='Invalid lease'; end;
            assert public.mobile_privacy_snapshot('{EXPORT}',(next_job->>'lease_token')::uuid)->>'user_id'='{OWNER}';
          end $$;
        """, before=f"insert into public.mobile_privacy_requests(id,user_id,kind) values('{EXPORT}','{OWNER}','export');")

    def test_export_completion_rejects_arbitrary_owner_and_request_paths(self):
        self.transaction(f"""
          do $$ declare job jsonb; path text; begin
            job:=public.mobile_privacy_claim('{EXPORT}');
            foreach path in array array['{OTHER}/{EXPORT}/account.zip',
              '{OWNER}/{ERASE}/account.zip','{OWNER}/{EXPORT}/../account.zip'] loop
              begin
                perform public.mobile_privacy_finish('{EXPORT}',(job->>'lease_token')::uuid,'complete',null,path);
                raise exception 'Arbitrary path accepted';
              exception when raise_exception then assert sqlerrm='Invalid export path'; end;
            end loop;
          end $$;
        """, before=f"insert into public.mobile_privacy_requests(id,user_id,kind) values('{EXPORT}','{OWNER}','export');")

    def test_expired_exports_not_readable_despite_broad_owner_policy(self):
        self.transaction(f"""
          do $$ begin
            assert (select count(*)=0 from public.mobile_privacy_export_paths());
            assert (select count(*)=0 from storage.objects where bucket_id='account-exports');
            begin
              insert into storage.objects(bucket_id,name,owner_id)
                values('account-exports','{OWNER}/forged/account.zip','{OWNER}');
              raise exception 'Direct export upload accepted';
            exception when insufficient_privilege then null; end;
          end $$;
          reset role;
          update public.mobile_privacy_requests set expires_at=clock_timestamp()+interval '1 day';
          set local role authenticated;
          do $$ begin
            assert (select count(*)=1 from storage.objects where bucket_id='account-exports');
            update storage.objects set name='{OWNER}/changed.zip' where bucket_id='account-exports';
            assert not found;
            delete from storage.objects where bucket_id='account-exports';
            assert not found;
          end $$;
        """, role="authenticated", before=completed_export_fixture(expires="now()-interval '1 day'") + """
          create policy lifecycle_fixture_broad_owner on storage.objects for all to authenticated
            using(owner_id=auth.uid()::text) with check(owner_id=auth.uid()::text);
        """)

    def test_prune_retains_path_until_expired_blob_is_absent(self):
        self.transaction(f"""
          do $$ begin
            assert jsonb_array_length(public.mobile_privacy_expired_exports())=1;
            perform public.mobile_privacy_prune('{EXPORT}');
            assert (select export_path is not null from public.mobile_privacy_requests where id='{EXPORT}');
            perform public.mobile_privacy_prune('{ERASE}');
            assert (select export_path is not null from public.mobile_privacy_requests where id='{ERASE}');
          end $$;
          reset role;
          delete from storage.objects where name='{OWNER}/{EXPORT}/account.zip';
          set local role service_role;
          do $$ begin
            perform public.mobile_privacy_prune('{EXPORT}');
            assert (select export_path is null from public.mobile_privacy_requests where id='{EXPORT}');
            assert jsonb_array_length(public.mobile_privacy_expired_exports())=0;
          end $$;
        """, before=completed_export_fixture(expires="now()-interval '1 day'")
                   + completed_export_fixture(user=OTHER, request_id=ERASE))

    def test_heartbeat_and_prune_remove_only_old_anonymous_receipts(self):
        for function in ("mobile_privacy_heartbeat()", f"mobile_privacy_prune('{ERASE}')"):
            with self.subTest(function=function):
                self.transaction(f"""
                  select public.{function};
                  do $$ begin
                    assert (select count(*)=2 from public.mobile_privacy_requests);
                    assert exists(select 1 from public.mobile_privacy_requests where user_id='{OTHER}');
                    assert exists(select 1 from public.mobile_privacy_requests where user_id is null
                      and completed_at>now()-interval '7 days');
                  end $$;
                """, before=f"""
                  insert into public.mobile_privacy_requests(user_id,kind,state,completed_at) values
                    (null,'erase','complete',now()-interval '8 days'),
                    (null,'erase','complete',now()-interval '1 day'),
                    ('{OTHER}','export','complete',now()-interval '8 days');
                """)

    def test_new_owner_writes_fail_after_erasure_freeze(self):
        self.transaction(f"""
          do $$ begin
            assert public.mobile_privacy_request('erase','DELETE MY ACCOUNT','one@example.test')->'request'->>'kind'='erase';
            assert not public.mobile_has_access();
            update public.profiles set display_name='Late write' where user_id='{OWNER}';
            assert not found;
            begin
              insert into public.mobile_resume_operations(id,user_id,state,resume_data)
                values(gen_random_uuid(),'{OWNER}','upload_pending','{{}}');
              raise exception 'New resume intent accepted';
            exception when insufficient_privilege then null; end;
            begin
              insert into storage.objects(bucket_id,name,owner_id) values('resumes','{OWNER}/late.pdf','{OWNER}');
              raise exception 'New storage write accepted';
            exception when insufficient_privilege then null; end;
          end $$;
        """, role="authenticated", before="select public.mobile_privacy_heartbeat();")

    def test_all_privacy_worker_controls_are_service_only_at_execution(self):
        calls = ["mobile_privacy_heartbeat()", "mobile_privacy_claim(null)",
                 f"mobile_privacy_snapshot('{EXPORT}','{ERASE}')",
                 f"mobile_privacy_finish('{EXPORT}','{ERASE}','blocked')",
                 "mobile_privacy_expired_exports()", f"mobile_privacy_prune('{EXPORT}')"]
        for role in ("anon", "authenticated"):
            with self.subTest(role=role):
                blocks = "\n".join(f"""
                  begin
                    perform public.{call};
                    raise exception 'Unprivileged worker control accepted';
                  exception when insufficient_privilege then null; end;
                """ for call in calls)
                self.transaction("do $$ begin " + blocks + " end $$;", role=role)

    def test_storage_permission_probe_and_final_upsert_require_current_metadata_lease(self):
        """Upstream canUpload rolls back a version='1' permission INSERT.

        System metadata can be null/partial there; user_metadata must still carry
        x-metadata's decoded token. Completion later upserts as a privileged role.
        See github.com/supabase/storage/blob/master/src/storage/uploader.ts.
        """
        self.transaction(f"""
          reset role;
          do $$ declare job jsonb; token jsonb; begin
            job:=public.mobile_privacy_claim('{EXPORT}');
            token:=jsonb_build_object('privacy_lease',job->>'lease_token');
            -- Emulate the vendor's testPermission rollback without bypassing
            -- the trigger. A null system metadata placeholder is legitimate.
            begin
              insert into storage.objects(bucket_id,name,version,metadata,user_metadata)
                values('account-exports','{OWNER}/{EXPORT}/account.zip','1',null,token);
              raise exception 'fixture permission-probe rollback' using errcode='ZX001';
            exception when sqlstate 'ZX001' then null; end;
            assert not exists(select 1 from storage.objects where bucket_id='account-exports');
            -- Both first final INSERT and INSERT ON CONFLICT UPDATE invoke the
            -- authoritative fence, including the storage-superuser path.
            insert into storage.objects(bucket_id,name,version,metadata,user_metadata)
              values('account-exports','{OWNER}/{EXPORT}/account.zip','version-2','{{"size":32}}',token);
            insert into storage.objects(bucket_id,name,version,metadata,user_metadata)
              values('account-exports','{OWNER}/{EXPORT}/account.zip','version-3','{{"size":33}}',token)
              on conflict(bucket_id,name) do update set version=excluded.version,
                metadata=excluded.metadata,user_metadata=excluded.user_metadata;
            assert (select version='version-3' from storage.objects where bucket_id='account-exports');
            assert public.mobile_privacy_finish('{EXPORT}',(job->>'lease_token')::uuid,'complete',null,
              '{OWNER}/{EXPORT}/account.zip');
            -- GET bookkeeping must remain possible after the lease is retired.
            update storage.objects set last_accessed_at=clock_timestamp(),updated_at=clock_timestamp()
              where bucket_id='account-exports';
            assert found;
            begin
              update storage.objects set metadata='{{"size":99}}' where bucket_id='account-exports';
              raise exception 'Content update after completion accepted';
            exception when insufficient_privilege then null; end;
          end $$;
        """, before=f"insert into public.mobile_privacy_requests(id,user_id,kind) values('{EXPORT}','{OWNER}','export');")

    def test_storage_fence_denies_missing_malformed_foreign_and_expired_leases(self):
        self.transaction(f"""
          reset role;
          do $$ declare job jsonb; bad jsonb; path text; begin
            job:=public.mobile_privacy_claim('{EXPORT}');
            foreach bad in array array[null::jsonb,'{{}}'::jsonb,'null'::jsonb,'[]'::jsonb,
              '{{"privacy_lease":null}}'::jsonb,'{{"privacy_lease":123}}'::jsonb,
              jsonb_build_object('privacy_lease','{ERASE}')] loop
              begin
                insert into storage.objects(bucket_id,name,version,user_metadata)
                  values('account-exports','{OWNER}/{EXPORT}/account.zip','1',bad);
                raise exception 'Missing/malformed/foreign lease accepted';
              exception when insufficient_privilege then null; end;
            end loop;
            foreach path in array array['{OTHER}/{EXPORT}/account.zip','{OWNER}/{ERASE}/account.zip',
              '{OWNER}/{EXPORT}/../account.zip','{OWNER}/{EXPORT}/account.zip/extra',
              '{OWNER}/{EXPORT}/%61ccount.zip','{OWNER}/not-a-uuid/account.zip'] loop
              begin
                insert into storage.objects(bucket_id,name,user_metadata) values('account-exports',path,
                  jsonb_build_object('privacy_lease',job->>'lease_token'));
                raise exception 'Foreign/noncanonical path accepted';
              exception when insufficient_privilege then null; end;
            end loop;
            begin
              insert into storage.objects(bucket_id,name,owner_id,user_metadata) values('account-exports',
                '{OWNER}/{EXPORT}/account.zip','{OTHER}',jsonb_build_object('privacy_lease',job->>'lease_token'));
              raise exception 'Foreign Storage owner accepted';
            exception when insufficient_privilege then null; end;
            update public.mobile_privacy_requests set lease_until=clock_timestamp()-interval '1 second'
              where id='{EXPORT}';
            begin
              insert into storage.objects(bucket_id,name,user_metadata) values('account-exports',
                '{OWNER}/{EXPORT}/account.zip',jsonb_build_object('privacy_lease',job->>'lease_token'));
              raise exception 'Expired lease upload accepted';
            exception when insufficient_privilege then null; end;
            assert not exists(select 1 from storage.objects where bucket_id='account-exports');
          end $$;
        """, before=f"insert into public.mobile_privacy_requests(id,user_id,kind) values('{EXPORT}','{OWNER}','export');")

    def test_storage_fence_rejects_stale_final_update_and_export_move(self):
        self.transaction(f"""
          reset role;
          do $$ declare first_job jsonb; next_job jsonb; begin
            first_job:=public.mobile_privacy_claim('{EXPORT}');
            insert into storage.objects(bucket_id,name,version,user_metadata) values('account-exports',
              '{OWNER}/{EXPORT}/account.zip','1',jsonb_build_object('privacy_lease',first_job->>'lease_token'));
            update public.mobile_privacy_requests set lease_until=now()-interval '1 second' where id='{EXPORT}';
            next_job:=public.mobile_privacy_claim('{EXPORT}');
            begin
              update storage.objects set version='stale-completion',metadata='{{"size":32}}'
                where bucket_id='account-exports';
              raise exception 'Stale placeholder completion accepted';
            exception when insufficient_privilege then null; end;
            update storage.objects set version='current-completion',metadata='{{"size":32}}',
              user_metadata=jsonb_build_object('privacy_lease',next_job->>'lease_token')
              where bucket_id='account-exports';
            assert found;
            begin
              update storage.objects set bucket_id='resumes' where bucket_id='account-exports';
              raise exception 'Export move outside fenced bucket accepted';
            exception when insufficient_privilege then null; end;
            begin
              update storage.objects set user_metadata='{{}}' where bucket_id='account-exports';
              raise exception 'Export lease stripping accepted';
            exception when insufficient_privilege then null; end;
          end $$;
        """, before=f"insert into public.mobile_privacy_requests(id,user_id,kind) values('{EXPORT}','{OWNER}','export');")

    def wait_for_fixture_sleep(self, application_name, future):
        """Observe lock-holder readiness; bounded and limited to our fresh DB."""
        until = time.monotonic() + 5
        while time.monotonic() < until:
            if future.done():
                future.result()
                self.fail("Fixture lock holder exited before readiness")
            if self.db.execute(f"select count(*) from pg_stat_activity where application_name='{application_name}' and wait_event='PgSleep';").strip() == "1":
                return
            time.sleep(0.01)
        self.fail("Fixture lock holder did not reach readiness")

    def test_storage_write_locks_auth_presence_and_serializes_erasure_request(self):
        """Two real SQL sessions: presence/erasure cannot race a pending write."""
        with ThreadPoolExecutor(max_workers=1) as executor:
            holder = executor.submit(self.db.execute, f"""
              begin;
              set application_name='fixture_privacy_storage_hold';
              insert into public.mobile_privacy_requests(id,user_id,kind) values('{EXPORT}','{OWNER}','export');
              do $$ declare job jsonb; begin
                job:=public.mobile_privacy_claim('{EXPORT}');
                insert into storage.objects(bucket_id,name,user_metadata) values('account-exports',
                  '{OWNER}/{EXPORT}/account.zip',jsonb_build_object('privacy_lease',job->>'lease_token'));
              end $$;
              select pg_sleep(3);
              rollback;
            """)
            try:
                self.wait_for_fixture_sleep("fixture_privacy_storage_hold", holder)
                self.transaction(f"""
                  set local lock_timeout='150ms';
                  reset role;
                  do $$ begin
                    begin
                      delete from auth.users where id='{OWNER}';
                      raise exception 'Auth removal did not wait for fenced upload';
                    exception when lock_not_available then null; end;
                  end $$;
                  set local role authenticated;
                  set local request.jwt.claim.role='authenticated';
                  do $$ begin
                    begin
                      perform public.mobile_privacy_request('erase','DELETE MY ACCOUNT','one@example.test');
                      raise exception 'Erasure did not wait for fenced upload';
                    exception when lock_not_available then null; end;
                  end $$;
                """, before="select public.mobile_privacy_heartbeat();")
            finally:
                holder.result(timeout=10)

    def test_waiting_storage_write_rechecks_erasure_after_user_lock_commit(self):
        """A statement begun before freeze commit must see it after lock wait."""
        self.db.execute(f"""
          insert into public.mobile_privacy_requests(id,user_id,kind,state,lease_token,lease_until)
            values('{EXPORT}','{OWNER}','export','processing','{ERASE}',now()+interval '1 day');
          select public.mobile_privacy_heartbeat();
        """)
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                holder = executor.submit(self.db.execute, f"""
                  begin;
                  set application_name='fixture_privacy_erasure_hold';
                  set local role authenticated;
                  set local request.jwt.claim.role='authenticated';
                  set local request.jwt.claim.sub='{OWNER}';
                  select public.mobile_privacy_request('erase','DELETE MY ACCOUNT','one@example.test');
                  select pg_sleep(3);
                  commit;
                """)
                try:
                    self.wait_for_fixture_sleep("fixture_privacy_erasure_hold", holder)
                    self.transaction(f"""
                      reset role;
                      do $$ begin
                        begin
                          insert into storage.objects(bucket_id,name,user_metadata) values('account-exports',
                            '{OWNER}/{EXPORT}/account.zip','{{"privacy_lease":"{ERASE}"}}');
                          raise exception 'Waiter used stale pre-erasure snapshot';
                        exception when insufficient_privilege then null; end;
                        assert not exists(select 1 from storage.objects where bucket_id='account-exports');
                      end $$;
                    """)
                finally:
                    holder.result(timeout=10)
        finally:
            self.db.execute(f"""
              delete from public.mobile_privacy_requests where user_id='{OWNER}';
              delete from public.mobile_privacy_worker_health;
              update public.mobile_usage_memberships set enabled=true where user_id='{OWNER}';
            """)

    def test_erasure_revokes_export_lease_and_blocks_late_storage_after_scrub(self):
        """Fenced metadata publication before AND after anonymous completion."""
        self.transaction(f"""
          do $$ declare export_job jsonb; begin
            export_job:=public.mobile_privacy_claim('{EXPORT}');
            assert public.mobile_privacy_snapshot('{EXPORT}',(export_job->>'lease_token')::uuid)->>'user_id'='{OWNER}';
            perform set_config('fixture.old_export_lease',export_job->>'lease_token',true);
          end $$;
          set local role authenticated;
          set local request.jwt.claim.role='authenticated';
          do $$ declare result jsonb; begin
            result:=public.mobile_privacy_request('erase','DELETE MY ACCOUNT','one@example.test');
            perform set_config('fixture.erase_id',result->'request'->>'id',true);
          end $$;
          set local role service_role;
          set local request.jwt.claim.role='service_role';
          do $$ declare erase_id uuid:=current_setting('fixture.erase_id')::uuid; begin
            assert (select state='failed' and lease_token is null and lease_until is null
              from public.mobile_privacy_requests where id='{EXPORT}');
            assert public.mobile_privacy_claim('{EXPORT}') is null;
            begin
              perform public.mobile_privacy_snapshot('{EXPORT}',current_setting('fixture.old_export_lease')::uuid);
              raise exception 'Revoked snapshot accepted';
            exception when raise_exception then assert sqlerrm='Invalid lease'; end;
            assert not public.mobile_privacy_finish('{EXPORT}',current_setting('fixture.old_export_lease')::uuid,'complete',
              null,'{OWNER}/{EXPORT}/account.zip');
            -- Even manually requeueing a failed export cannot bypass erasure.
            update public.mobile_privacy_requests set state='queued' where id='{EXPORT}';
            assert public.mobile_privacy_claim('{EXPORT}') is null;
            assert public.mobile_privacy_claim(erase_id)->>'kind'='erase';
            assert public.mobile_billing_cancel_claim('{OWNER}',erase_id)->>'status'='ready';
          end $$;
          reset role;
          do $$ begin
            begin
              insert into storage.objects(bucket_id,name,user_metadata) values('account-exports',
                '{OWNER}/{EXPORT}/account.zip',jsonb_build_object('privacy_lease',current_setting('fixture.old_export_lease')));
              raise exception 'Upload after erasure freeze accepted';
            exception when insufficient_privilege then null; end;
          end $$;
          reset role;
          -- Erasure observes empty storage, removes Auth and completes first.
          delete from auth.users where id='{OWNER}';
          do $$ declare token uuid; erase_id uuid:=current_setting('fixture.erase_id')::uuid; begin
            select lease_token into token from public.mobile_privacy_requests where id=erase_id;
            assert public.mobile_privacy_finish(erase_id,token,'complete');
            assert not exists(select 1 from public.mobile_privacy_requests where id='{EXPORT}');
            assert (select state='complete' and user_id is null from public.mobile_privacy_requests where id=erase_id);
          end $$;
          -- Auth presence is an independent fence even after every privacy
          -- receipt is gone and a stray privileged writer recreates a queue row.
          delete from public.mobile_privacy_requests where user_id is null;
          insert into public.mobile_privacy_requests(id,user_id,kind,state,lease_token,lease_until)
            values('{EXPORT}','{OWNER}','export','processing',current_setting('fixture.old_export_lease')::uuid,
              now()+interval '1 day');
          do $$ begin
            begin
              insert into storage.objects(bucket_id,name,user_metadata) values('account-exports',
                '{OWNER}/{EXPORT}/account.zip',jsonb_build_object('privacy_lease',current_setting('fixture.old_export_lease')));
              raise exception 'Upload after Auth erasure accepted';
            exception when insufficient_privilege then null; end;
            assert not exists(select 1 from storage.objects where name='{OWNER}/{EXPORT}/account.zip');
            assert jsonb_array_length(public.mobile_privacy_expired_exports())=0;
          end $$;
        """, before=f"""
          select public.mobile_privacy_heartbeat();
          insert into public.mobile_privacy_requests(id,user_id,kind) values
            ('{EXPORT}','{OWNER}','export');
        """)

    def test_lost_erasure_ack_reports_unconfirmed_and_does_not_retry_sql_commit(self):
        """Real SQL commit, synthetic HTTP ACK loss; no worker/Auth deletion."""
        committed = []

        def transport(request):
            self.assertEqual(request.url.host, "privacy-lifecycle.invalid")
            self.assertEqual(request.url.path, "/rest/v1/rpc/mobile_privacy_request")
            self.assertEqual(json.loads(request.content), {
                "p_kind": "erase", "p_confirmation": "DELETE MY ACCOUNT", "p_email": "one@example.test"})
            result = self.db.execute(f"""
              begin;
              select public.mobile_privacy_heartbeat();
              set local role authenticated;
              set local request.jwt.claim.role='authenticated';
              set local request.jwt.claim.sub='{OWNER}';
              select public.mobile_privacy_request('erase','DELETE MY ACCOUNT','one@example.test');
              commit;
            """)
            committed.append(json.loads(result.strip()))
            raise httpx.ReadTimeout("Synthetic acknowledgement loss", request=request)

        async def request():
            async with httpx.AsyncClient(base_url="https://privacy-lifecycle.invalid", trust_env=False,
                                         transport=httpx.MockTransport(transport)) as client:
                repo = MobileRepository(client, OWNER, verified_email="one@example.test")
                with self.assertRaises(HTTPException) as failure:
                    await request_erasure(repo, ErasureRequest(
                        confirmation="DELETE MY ACCOUNT", email="one@example.test"))
                self.assertEqual(failure.exception.status_code, 503)
                self.assertNotIn("no deletion was started", failure.exception.detail)
                self.assertIn("confirm", failure.exception.detail.lower())
                self.assertIn("status", failure.exception.detail.lower())

        try:
            asyncio.run(request())
            self.assertEqual(len(committed), 1, "Uncertain destructive requests must not be blindly retried")
            self.assertEqual(committed[0]["request"]["state"], "queued")
            self.assertEqual(self.db.execute(f"""
              select count(*) from public.mobile_privacy_requests where user_id='{OWNER}' and kind='erase';
              select enabled from public.mobile_usage_memberships where user_id='{OWNER}';
            """).splitlines(), ["1", "f"])
        finally:
            # Only this newly allocated SQL fixture; restore shared test baseline.
            self.db.execute(f"""
              delete from public.mobile_privacy_requests where user_id='{OWNER}' and kind='erase';
              delete from public.mobile_privacy_worker_health;
              update public.mobile_usage_memberships set enabled=true where user_id='{OWNER}';
            """)


class PrivacyLifecycleWorkerAcceptance(unittest.IsolatedAsyncioTestCase):
    """Offline HTTP ordering probes, separate from actual SQL controls above."""

    async def test_stale_export_precheck_prevents_upload_of_remembered_snapshot(self):
        """Defense-in-depth precheck; real SQL fence separately covers TOCTOU."""
        objects = {}
        calls = []
        path = f"/storage/v1/object/account-exports/{OWNER}/{EXPORT}/account.zip"

        def transport(request):
            self.assertEqual(request.url.host, "privacy-lifecycle.invalid")
            calls.append((request.method, request.url.path))
            if request.url.path == "/rest/v1/rpc/mobile_privacy_snapshot":
                return httpx.Response(400, json={"message": "Invalid lease"})
            raise AssertionError("Unexpected endpoint " + request.url.path)

        worker = PrivacyWorker(WorkerSettings("https://privacy-lifecycle.invalid", "sb_secret_synthetic"),
                               transport=httpx.MockTransport(transport))
        try:
            with self.assertRaises(PrivacyBlocked):
                await worker.export({"id": EXPORT, "user_id": OWNER, "lease_token": ERASE, "kind": "export"},
                    {"user_id": OWNER, "account": {"email": "retention-sentinel@example.test"},
                     "tables": {}, "objects": []})
            self.assertEqual(calls, [("POST", "/rest/v1/rpc/mobile_privacy_snapshot")])
            self.assertEqual(objects, {})
        finally:
            await worker.close()

    async def test_current_export_transmits_exact_base64_lease_metadata(self):
        objects = {}
        calls = []
        path = f"/storage/v1/object/account-exports/{OWNER}/{EXPORT}/account.zip"
        snapshot = {"user_id": OWNER, "account": {"email": "fixture@example.test"}, "tables": {}, "objects": []}

        def transport(request):
            self.assertEqual(request.url.host, "privacy-lifecycle.invalid")
            calls.append((request.method, request.url.path))
            if request.url.path == "/rest/v1/rpc/mobile_privacy_snapshot":
                return httpx.Response(200, json=snapshot)
            if request.url.path == path and request.method == "POST":
                self.assertEqual(json.loads(base64.b64decode(request.headers['x-metadata'], validate=True)),
                                 {"privacy_lease": ERASE})
                self.assertEqual(request.headers['x-upsert'], 'false')
                objects[path] = request.content
                return httpx.Response(200, json={})
            if request.url.path == path and request.method == "GET":
                return httpx.Response(200, content=objects[path])
            if request.url.path == "/rest/v1/rpc/mobile_privacy_finish":
                return httpx.Response(200, json=True)
            raise AssertionError("Unexpected endpoint " + request.url.path)

        worker = PrivacyWorker(WorkerSettings("https://privacy-lifecycle.invalid", "sb_secret_synthetic"),
                               transport=httpx.MockTransport(transport))
        try:
            await worker.export({"id": EXPORT, "user_id": OWNER, "lease_token": ERASE, "kind": "export"}, snapshot)
            self.assertEqual(calls[0], ("POST", "/rest/v1/rpc/mobile_privacy_snapshot"))
            with zipfile.ZipFile(io.BytesIO(objects[path])) as archive:
                self.assertEqual(json.loads(archive.read("account.json"))["user_id"], OWNER)
        finally:
            await worker.close()


if __name__ == "__main__":
    unittest.main()
