"""Real billing lifecycle SQL on an isolated disposable PostgreSQL cluster.

Executes migrations 0001..0010 verbatim with minimal local auth/storage stubs.
NOT hosted Supabase parity, HTTP PostgREST proof, or live Stripe/payment proof.
No external DSN/network/provider/owner data. Missing runtime fails this required
gate rather than silently skipping it. Uses existing tests/local_postgres.py.
"""
from __future__ import annotations

import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import uuid4

from local_postgres import DisposablePostgres, SUPABASE_TEST_SCHEMAS

ROOT = Path(__file__).resolve().parents[1]
OWNER = "00000000-0000-4000-8000-000000000001"
MANUAL = "00000000-0000-4000-8000-000000000002"
NEVER = "00000000-0000-4000-8000-000000000003"
REQUEST = "00000000-0000-4000-8000-000000000004"
MIGRATIONS = (
    "0001_beta_multi_tenant.sql", "0002_mobile_career_workspace.sql",
    "0003_profession_neutral_background.sql", "0004_career_validator_anonymous_execute.sql",
    "0005_mobile_resume_operations.sql", "0006_mobile_artifact_operations.sql",
    "0007_mobile_usage_controls.sql", "0008_atomic_profile.sql", "0009_billing.sql", "0010_account_privacy.sql",
)


class BillingPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.db = DisposablePostgres()
        except unittest.SkipTest as exc:
            raise RuntimeError("Billing SQL release gate requires a disposable PostgreSQL runtime") from exc
        cls.addClassCleanup(cls.db.close)
        cls.db.execute(SUPABASE_TEST_SCHEMAS)
        for migration in MIGRATIONS:
            cls.db.execute((ROOT / "supabase/migrations" / migration).read_text())
        cls.db.execute(r"""
        insert into auth.users(id,email,email_confirmed_at) values
          ('00000000-0000-4000-8000-000000000001','a@fixture.invalid',now()),
          ('00000000-0000-4000-8000-000000000002','manual@fixture.invalid',now()),
          ('00000000-0000-4000-8000-000000000003','never@fixture.invalid',now());
        insert into public.mobile_billing_accounts(user_id,provider,mode,customer_id) values
          ('00000000-0000-4000-8000-000000000001','stripe','test','cus_A'),
          ('00000000-0000-4000-8000-000000000002','stripe','test','cus_Manual');
        insert into public.mobile_usage_memberships(user_id,grant_source,billing_account_id,enabled,period_reserved)
          select user_id,case when customer_id='cus_A' then 'billing' else 'manual' end,id,true,7
            from public.mobile_billing_accounts;
        insert into public.mobile_ai_daily_usage(user_id,usage_day,reserved_units)
          values ('00000000-0000-4000-8000-000000000001',(now() at time zone 'utc')::date,3);
        insert into public.mobile_ai_operation_costs(operation,cost_units,enabled) values('chat',1,true);
        insert into public.mobile_ai_reservations(user_id,reservation_id,operation,reserved_units,reserved_at)
          values('00000000-0000-4000-8000-000000000001','00000000-0000-4000-8000-000000000099','chat',3,now());
        -- Fixture conveniences only: real public routines remain unmodified.
        create schema billing_fixture;
        create function billing_fixture.snapshot(
          p_start timestamptz default date_trunc('day',now())-interval '1 day',
          p_end timestamptz default date_trunc('day',now())+interval '1 day') returns jsonb
          language sql as $$ select jsonb_build_object(
            'subscription_id','sub_A','status','active','plan_key','fixture','eligible',true,
            'period_start',extract(epoch from p_start)::bigint,'period_end',extract(epoch from p_end)::bigint,
            'paid_through',extract(epoch from p_end)::bigint,'cancel_at_period_end',false,
            'period_limit',100,'daily_limit',10); $$;
        create function billing_fixture.apply(p_event text default 'evt_One',
          p_snapshot jsonb default billing_fixture.snapshot(), p_customer text default 'cus_A') returns jsonb
          language plpgsql as $$ declare a jsonb; r jsonb; begin
            a:=public.mobile_billing_event_claim('stripe','test',p_event,p_customer,repeat('a',64));
            if a->>'status' in ('busy','blocked','duplicate','ignored','conflict') then return a; end if;
            r:=public.mobile_billing_apply_snapshot((a->>'id')::uuid,(a->>'lease_token')::uuid,p_event,p_snapshot);
            perform public.mobile_billing_release((a->>'id')::uuid,(a->>'lease_token')::uuid);
            return r;
          end; $$;
        """)

    def transaction(self, sql):
        return self.db.execute("begin; set local request.jwt.claim.role='service_role';\n" + sql + "\nrollback;")

    def test_paid_snapshot_atomic_renewal_replay_same_period_and_ledger_preservation(self):
        self.transaction(r"""
        do $$ declare s jsonb; begin
          assert billing_fixture.apply()->>'status'='applied';
          assert (select enabled and period_reserved=7 and period_limit=100 and daily_limit=10
            from public.mobile_usage_memberships where user_id='00000000-0000-4000-8000-000000000001');
          assert billing_fixture.apply()->>'status'='duplicate';
          assert billing_fixture.apply('evt_DifferentSameCycle')->>'status'='applied';
          assert (select period_reserved=7 from public.mobile_usage_memberships where user_id='00000000-0000-4000-8000-000000000001');
          -- A new nonoverlapping cycle, simulated by previously exhausted period.
          update public.mobile_usage_memberships set period_start=date_trunc('day',now())-interval '3 days',
            period_end=date_trunc('day',now())-interval '1 day',period_reserved=100
            where user_id='00000000-0000-4000-8000-000000000001';
          assert billing_fixture.apply('evt_NewCycle')->>'status'='applied';
          assert (select period_reserved=0 from public.mobile_usage_memberships where user_id='00000000-0000-4000-8000-000000000001');
          assert (select reserved_units=3 from public.mobile_ai_daily_usage where user_id='00000000-0000-4000-8000-000000000001');
          assert (select count(*)=1 from public.mobile_ai_reservations where user_id='00000000-0000-4000-8000-000000000001');
        end $$;
        """)

    def test_overlapping_period_cancel_reactivate_and_stale_period_keep_spending(self):
        self.transaction(r"""
        do $$ declare s jsonb; begin
          perform billing_fixture.apply();
          s:=billing_fixture.snapshot(date_trunc('day',now()),date_trunc('day',now())+interval '2 days');
          perform billing_fixture.apply('evt_Overlap',s);
          assert (select enabled and period_reserved=7 from public.mobile_usage_memberships where user_id='00000000-0000-4000-8000-000000000001');
          perform billing_fixture.apply('evt_Cancel',s||'{"eligible":false,"status":"canceled"}');
          assert (select not enabled and period_reserved=7 from public.mobile_usage_memberships where user_id='00000000-0000-4000-8000-000000000001');
          perform billing_fixture.apply('evt_Reactivate',s);
          assert (select enabled and period_reserved=7 from public.mobile_usage_memberships where user_id='00000000-0000-4000-8000-000000000001');
          perform billing_fixture.apply('evt_Stale',billing_fixture.snapshot());
          assert (select not enabled and period_reserved=7 and period_start=date_trunc('day',now())
            from public.mobile_usage_memberships where user_id='00000000-0000-4000-8000-000000000001');
        end $$;
        """)

    def test_manual_membership_preserved_for_paid_and_cancelled_events(self):
        self.transaction(r"""
        do $$ declare original jsonb; begin
          select to_jsonb(m) into original from public.mobile_usage_memberships m
            where user_id='00000000-0000-4000-8000-000000000002';
          perform billing_fixture.apply('evt_ManualPaid',billing_fixture.snapshot(),'cus_Manual');
          perform billing_fixture.apply('evt_ManualCancel',billing_fixture.snapshot()||'{"eligible":false,"status":"canceled"}','cus_Manual');
          assert (select to_jsonb(m)=original from public.mobile_usage_memberships m
            where user_id='00000000-0000-4000-8000-000000000002');
        end $$;
        """)

    def test_invalid_payment_caps_or_dates_never_grant(self):
        self.transaction(r"""
        do $$ declare bad jsonb; n integer:=0; begin
          for bad in select value from jsonb_array_elements('[
            {"eligible":false},{"status":"past_due"},{"paid_through":null},
            {"period_limit":0},{"daily_limit":0},{"period_end":null},{"paid_through":1}
          ]') loop
            n:=n+1;
            perform billing_fixture.apply('evt_Invalid'||n,billing_fixture.snapshot()||bad);
            assert (select not enabled and period_reserved=7 from public.mobile_usage_memberships
              where user_id='00000000-0000-4000-8000-000000000001');
          end loop;
        end $$;
        """)

    def test_event_conflict_unknown_customer_and_expired_fencing(self):
        self.transaction(r"""
        do $$ declare a jsonb; b jsonb; begin
          assert public.mobile_billing_event_claim('stripe','test','evt_Unknown','cus_Unknown',repeat('a',64))->>'status'='ignored';
          a:=public.mobile_billing_event_claim('stripe','test','evt_Fence','cus_A',repeat('a',64));
          assert a->>'lease_token' is not null;
          assert public.mobile_billing_event_claim('stripe','test','evt_Fence','cus_A',repeat('b',64))->>'status'='conflict';
          assert public.mobile_billing_event_claim('stripe','test','evt_Busy','cus_A',repeat('a',64))->>'status'='busy';
          update public.mobile_billing_accounts set lease_until=clock_timestamp()-interval '1 second' where id=(a->>'id')::uuid;
          b:=public.mobile_billing_event_claim('stripe','test','evt_Newer','cus_A',repeat('a',64));
          assert a->>'lease_token' <> b->>'lease_token';
          assert public.mobile_billing_apply_snapshot((a->>'id')::uuid,(a->>'lease_token')::uuid,'evt_Fence',billing_fixture.snapshot())->>'status'='stale';
          assert public.mobile_billing_apply_snapshot((b->>'id')::uuid,null,'evt_Newer',billing_fixture.snapshot())->>'status'='stale';
          assert public.mobile_billing_apply_snapshot((b->>'id')::uuid,(b->>'lease_token')::uuid,'evt_Newer',billing_fixture.snapshot())->>'status'='applied';
          perform public.mobile_billing_release((a->>'id')::uuid,(a->>'lease_token')::uuid);
          assert (select lease_token=(b->>'lease_token')::uuid from public.mobile_billing_accounts where id=(b->>'id')::uuid);
        end $$;
        """)

    def test_mid_transaction_failure_rolls_back_quota_and_event(self):
        self.transaction(r"""
        create function billing_fixture.reject_event() returns trigger language plpgsql as $$ begin
          if new.state='applied' then raise exception 'Injected local event failure'; end if; return new;
        end $$;
        create trigger reject_event before update on public.mobile_billing_events
          for each row execute function billing_fixture.reject_event();
        do $$ begin
          begin
            perform billing_fixture.apply();
            raise exception 'Failure did not fire';
          exception when raise_exception then
            assert sqlerrm='Injected local event failure';
          end;
          assert (select period_limit=0 and period_reserved=7 from public.mobile_usage_memberships where user_id='00000000-0000-4000-8000-000000000001');
          assert not exists(select 1 from public.mobile_billing_events where event_id='evt_One');
        end $$;
        """)

    def test_service_grants_and_authenticated_owner_only_safe_projection(self):
        self.transaction(r"""
        do $$ declare f record; begin
          for f in select p.oid from pg_proc p join pg_namespace n on n.oid=p.pronamespace
            where n.nspname='public' and p.proname like 'mobile_billing_%' loop
            assert not has_function_privilege('anon',f.oid,'execute');
            assert not has_function_privilege('authenticated',f.oid,'execute');
          end loop;
        end $$;
        set local role authenticated;
        set local request.jwt.claim.role='authenticated';
        set local request.jwt.claim.sub='00000000-0000-4000-8000-000000000001';
        do $$ begin
          assert (select count(*)=1 from public.mobile_billing_export);
          assert (select user_id='00000000-0000-4000-8000-000000000001' from public.mobile_billing_export);
          begin
            perform customer_id from public.mobile_billing_accounts;
            raise exception 'Vendor ID exposed';
          exception when insufficient_privilege then null; end;
          begin
            perform public.mobile_billing_begin(auth.uid(),'stripe','test');
            raise exception 'Privileged RPC exposed';
          exception when insufficient_privilege then null; end;
          begin
            update public.mobile_billing_accounts set status='active';
            raise exception 'Billing table write exposed';
          exception when insufficient_privilege then null; end;
        end $$;
        reset role;
        -- SECURITY DEFINER independently rejects forged caller-role context.
        do $$ begin
          begin
            perform public.mobile_billing_begin('00000000-0000-4000-8000-000000000001','stripe','test');
            raise exception 'Role guard absent';
          exception when insufficient_privilege then null; end;
        end $$;
        """)

    def test_checkout_journal_idempotency_and_no_membership_grant(self):
        self.transaction(r"""
        do $$ declare a jsonb; o jsonb; again jsonb; begin
          a:=public.mobile_billing_begin('00000000-0000-4000-8000-000000000001','stripe','test');
          o:=public.mobile_billing_operation((a->>'id')::uuid,(a->>'lease_token')::uuid,'checkout',repeat('f',64),'fixture','price_Fixture');
          again:=public.mobile_billing_operation((a->>'id')::uuid,(a->>'lease_token')::uuid,'checkout',repeat('f',64),'fixture','price_Fixture');
          assert o->>'id'=again->>'id';
          assert public.mobile_billing_operation((a->>'id')::uuid,(a->>'lease_token')::uuid,'checkout',repeat('b',64),'other','price_Other')->>'status'='blocked';
          assert public.mobile_billing_complete_operation((a->>'id')::uuid,(a->>'lease_token')::uuid,(o->>'id')::uuid,
            'cs_test_One','https://checkout.stripe.com/test',extract(epoch from now()+interval '1 hour')::bigint)->>'status'='saved';
          assert (select period_limit=0 and period_reserved=7 from public.mobile_usage_memberships where user_id='00000000-0000-4000-8000-000000000001');
          update public.mobile_billing_operations set created_at=clock_timestamp()-interval '24 hours' where id=(o->>'id')::uuid;
          assert public.mobile_billing_operation((a->>'id')::uuid,(a->>'lease_token')::uuid,'checkout',repeat('f',64),'fixture','price_Fixture')->>'status'='blocked';
        end $$;
        """)

    def test_0010_queue_fences_inflight_sync_and_checkout_before_worker(self):
        self.transaction(r"""
        do $$ declare a jsonb; q jsonb; begin
          a:=public.mobile_billing_event_claim('stripe','test','evt_InFlight','cus_A',repeat('a',64));
          perform public.mobile_privacy_heartbeat();
          perform set_config('request.jwt.claim.role','authenticated',true);
          perform set_config('request.jwt.claim.sub','00000000-0000-4000-8000-000000000001',true);
          q:=public.mobile_privacy_request('erase','DELETE MY ACCOUNT','a@fixture.invalid');
          assert q->'request'->>'id' is not null;
          assert not public.mobile_has_access();
          perform set_config('request.jwt.claim.role','service_role',true);
          assert public.mobile_billing_apply_snapshot((a->>'id')::uuid,(a->>'lease_token')::uuid,'evt_InFlight',billing_fixture.snapshot())->>'status'='stale';
          assert public.mobile_billing_begin('00000000-0000-4000-8000-000000000001','stripe','test')->>'status'='blocked';
          assert public.mobile_billing_event_claim('stripe','test','evt_AfterQueue','cus_A',repeat('a',64))->>'status'='ignored';
          update public.mobile_usage_memberships set enabled=true where user_id='00000000-0000-4000-8000-000000000001';
          assert (select not enabled from public.mobile_usage_memberships where user_id='00000000-0000-4000-8000-000000000001');
        end $$;
        """)

    def test_active_cancel_guard_strict_receipt_and_lost_auth_delete_ack(self):
        self.transaction(r"""
        do $$ declare a jsonb; r jsonb; req uuid:='00000000-0000-4000-8000-000000000004'; begin
          perform billing_fixture.apply();
          begin
            delete from auth.users where id='00000000-0000-4000-8000-000000000001';
            raise exception 'Auth delete allowed with active billing';
          exception when raise_exception then
            assert sqlerrm='Billing cancellation must be confirmed before Auth deletion';
          end;
          a:=public.mobile_billing_cancel_claim('00000000-0000-4000-8000-000000000001',req);
          assert a->>'lifecycle'='erasing';
          assert (select not enabled and period_reserved=7 from public.mobile_usage_memberships where user_id='00000000-0000-4000-8000-000000000001');
          r:='{"customer_id":"cus_A","cancellation_confirmed":true,"no_future_collection":true,
            "open_subscription_ids":[],"open_checkout_ids":[],"unresolved_operation_ids":[]}';
          assert public.mobile_billing_cancel_finish((a->>'id')::uuid,req,null,r)->>'status'='stale';
          assert public.mobile_billing_cancel_finish((a->>'id')::uuid,req,(a->>'lease_token')::uuid,r||'{"no_future_collection":false}')->>'status'='blocked';
          assert public.mobile_billing_cancel_finish((a->>'id')::uuid,req,(a->>'lease_token')::uuid,r)->>'status'='ready';
          delete from auth.users where id='00000000-0000-4000-8000-000000000001';
          assert (select user_id is null and cancellation_status='confirmed' from public.mobile_billing_accounts where id=(a->>'id')::uuid);
          assert public.mobile_billing_cancel_claim('00000000-0000-4000-8000-000000000001',req)->>'status'='ready';
          assert public.mobile_billing_cancel_claim('00000000-0000-4000-8000-000000000001',gen_random_uuid())->>'status'='blocked';
          assert public.mobile_billing_event_claim('stripe','test','evt_AfterDelete','cus_A',repeat('a',64))->>'status'='ignored';
        end $$;
        """)

    def test_never_billed_requires_extant_auth_and_durable_fence_not_mapping_absence(self):
        self.transaction(r"""
        do $$ declare req uuid:='00000000-0000-4000-8000-000000000004'; begin
          assert (public.mobile_billing_erasure_status('00000000-0000-4000-8000-000000000003')->>'requires_cancellation')::boolean;
          assert public.mobile_billing_cancel_claim('00000000-0000-4000-8000-000000000003',req)->>'status'='ready';
          assert (select lifecycle='erased' and cancellation_status='confirmed' from public.mobile_billing_accounts
            where erasure_request_id=req);
          assert public.mobile_billing_begin('00000000-0000-4000-8000-000000000003','stripe','test')->>'status'='blocked';
          delete from auth.users where id='00000000-0000-4000-8000-000000000003';
          assert public.mobile_billing_cancel_claim('00000000-0000-4000-8000-000000000003',req)->>'status'='ready';
          assert public.mobile_billing_cancel_claim('00000000-0000-4000-8000-000000000003',gen_random_uuid())->>'status'='blocked';
        end $$;
        """)

    def test_pending_creation_or_existing_lease_blocks_erasure_but_commits_fence(self):
        self.transaction(r"""
        do $$ declare a jsonb; o jsonb; r jsonb; req uuid:=gen_random_uuid(); begin
          a:=public.mobile_billing_begin('00000000-0000-4000-8000-000000000003','stripe','test');
          o:=public.mobile_billing_operation((a->>'id')::uuid,(a->>'lease_token')::uuid,'customer',repeat('a',64),null,null);
          assert public.mobile_billing_cancel_claim('00000000-0000-4000-8000-000000000003',req)->>'status'='busy';
          assert (select lifecycle='erasing' from public.mobile_billing_accounts where id=(a->>'id')::uuid);
          assert public.mobile_billing_complete_operation((a->>'id')::uuid,(a->>'lease_token')::uuid,(o->>'id')::uuid,'cus_Lost',null,null)->>'status'='stale';
          perform public.mobile_billing_release((a->>'id')::uuid,(a->>'lease_token')::uuid);
          r:=public.mobile_billing_cancel_claim('00000000-0000-4000-8000-000000000003',req);
          assert r->>'status' is distinct from 'ready';
          assert jsonb_array_length(r->'operations')=1;
        end $$;
        """)

    def test_concurrent_workers_only_one_claim_and_duplicate_delivery_never_resets_quota(self):
        user, suffix = str(uuid4()), uuid4().hex
        customer = "cus_" + suffix
        self.db.execute("insert into auth.users(id,email,email_confirmed_at) values ('" + user + "','race@fixture.invalid',now());"
            "insert into public.mobile_billing_accounts(user_id,provider,mode,customer_id) values('" + user + "','stripe','test','" + customer + "');")
        barrier = Barrier(6)
        def claim(number):
            barrier.wait(timeout=10)
            return json.loads(self.db.execute("begin; set local request.jwt.claim.role='service_role'; select "
                "public.mobile_billing_event_claim('stripe','test','evt_" + suffix + str(number) + "','" + customer + "',repeat('a',64)); commit;"))
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(claim, range(6)))
        winners = [row for row in results if row.get("lease_token")]
        self.assertEqual(len(winners), 1)
        self.assertEqual(sum(row.get("status") == "busy" for row in results), 5)
        winner = winners[0]
        winner_event = self.db.execute("select event_id from public.mobile_billing_events where lease_token='" + winner["lease_token"] + "';").strip()
        self.db.execute("begin; set local request.jwt.claim.role='service_role'; select public.mobile_billing_apply_snapshot('" +
            winner["id"] + "','" + winner["lease_token"] + "','" + winner_event + "',billing_fixture.snapshot());"
            "select public.mobile_billing_release('" + winner["id"] + "','" + winner["lease_token"] + "');"
            "update public.mobile_usage_memberships set period_reserved=88 where user_id='" + user + "'; commit;")
        barrier = Barrier(6)
        def duplicate(number):
            barrier.wait(timeout=10)
            return json.loads(self.db.execute("begin; set local request.jwt.claim.role='service_role'; select "
                "public.mobile_billing_event_claim('stripe','test','" + winner_event + "','" + customer + "',repeat('a',64)); commit;"))
        with ThreadPoolExecutor(max_workers=6) as pool:
            self.assertTrue(all(row["status"] == "duplicate" for row in pool.map(duplicate, range(6))))
        self.assertEqual(self.db.execute("select period_reserved from public.mobile_usage_memberships where user_id='" + user + "';").strip(), "88")


if __name__ == "__main__":
    unittest.main()
