"""0015 operator metrics against disposable PostgreSQL. No hosted DSN or PII."""
import unittest
from pathlib import Path

from local_postgres import DisposablePostgres, SUPABASE_TEST_SCHEMAS

ROOT = Path(__file__).resolve().parents[1]
OWNER = "00000000-0000-4000-8000-000000000001"
JOB = "00000000-0000-4000-8000-000000000010"
RANK = "00000000-0000-4000-8000-000000000031"
PREP = "00000000-0000-4000-8000-000000000032"


class OperatorMetricsPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = DisposablePostgres()
        cls.addClassCleanup(cls.db.close)
        cls.db.execute(SUPABASE_TEST_SCHEMAS)
        for path in sorted((ROOT / "supabase/migrations").glob("*.sql")):
            if path.name[:4].isdigit() and int(path.name[:4]) <= 15:
                cls.db.execute(path.read_text())
        cls.db.execute(f"""
          insert into auth.users(id,email,email_confirmed_at) values
            ('{OWNER}','one@fixture.invalid',now());
          insert into public.mobile_usage_memberships(user_id,enabled) values('{OWNER}',true)
            on conflict (user_id) do update set enabled=true;
          insert into public.jobs(id,user_id,source,source_url,title,company_name,description)
            values('{JOB}','{OWNER}','manual','https://example.invalid/role','Designer','Fixture Co','Design tools.');
        """)

    def test_authenticated_cannot_run_operator_functions(self):
        self.db.execute(f"""
          begin;
          set local role authenticated;
          set local request.jwt.claim.role='authenticated';
          set local request.jwt.claim.sub='{OWNER}';
          do $$ begin
            begin
              perform public.mobile_operator_packet_burn();
              raise exception 'operator packet burn leaked to authenticated';
            exception
              when insufficient_privilege then null;
              when others then
                if sqlerrm like 'operator packet burn leaked%' then raise; end if;
            end;
            begin
              perform public.mobile_operator_dogfood_week('2026-09-07'::date);
              raise exception 'operator dogfood leaked to authenticated';
            exception
              when insufficient_privilege then null;
              when others then
                if sqlerrm like 'operator dogfood leaked%' then raise; end if;
            end;
          end $$;
          rollback;
        """)

    def test_packet_burn_and_dogfood_week_counts(self):
        self.db.execute(f"""
          begin;
          insert into public.job_scores(user_id,job_id,score,recommendation,rationale,fit_explanation,created_at)
            values('{OWNER}','{JOB}',8.0,'review','Fit estimate, not an ATS score.',
              '{{"why":["Fit estimate, not an ATS score."],"evidence":["Confirmed information: Design tools."],"uncertainty":["Work eligibility is unknown."]}}'::jsonb,
              '2026-09-08T12:00:00Z');
          insert into public.model_runs(id,user_id,job_id,operation,provider,model_name,status,completed_at,output_summary)
            values('{RANK}','{OWNER}','{JOB}','rank_job','fixture','gpt-4.1','succeeded','2026-09-08T10:00:00Z',
              '{{"usage":{{"request_type":"assessment","estimated_cost_usd":0.02,"reserved_units":1,"input_tokens":10,"output_tokens":5}}}}'::jsonb);
          insert into public.model_runs(id,user_id,job_id,operation,provider,model_name,status,completed_at,output_summary)
            values('{PREP}','{OWNER}','{JOB}','prepare_documents','fixture','gpt-4.1','succeeded','2026-09-08T11:00:00Z',
              '{{"usage":{{"request_type":"packet_prepare","estimated_cost_usd":0.04,"reserved_units":2,"input_tokens":20,"output_tokens":10}}}}'::jsonb);
          insert into public.applications(user_id,job_id,status,updated_at)
            values('{OWNER}','{JOB}','submitted','2026-09-09T09:00:00Z');
          set local role service_role;
          set local request.jwt.claim.role='service_role';
          set local request.jwt.claim.sub='{OWNER}';
          do $$ declare burn jsonb; week jsonb; begin
            burn := public.mobile_operator_packet_burn();
            week := public.mobile_operator_dogfood_week('2026-09-07'::date);
            assert (burn->>'completed_packets')::int = 1;
            assert (burn->>'mean_reserved_units')::numeric = 3;
            assert week->>'iso_week' = '2026-W37';
            assert (week->>'roles_reviewed')::int = 1;
            assert (week->>'roles_prepared')::int = 1;
            assert (week->>'roles_applied')::int = 1;
            assert week->>'replies_status' = 'not_tracked';
            assert week->'replies' = 'null'::jsonb;
          end $$;
          rollback;
        """)

    def test_non_monday_week_start_is_rejected(self):
        self.db.execute("""
          begin;
          set local role service_role;
          set local request.jwt.claim.role='service_role';
          do $$ begin
            begin
              perform public.mobile_operator_dogfood_week('2026-09-08'::date);
              raise exception 'non-monday accepted';
            exception
              when others then
                if sqlerrm like 'non-monday accepted' then raise; end if;
            end;
          end $$;
          rollback;
        """)


if __name__ == "__main__":
    unittest.main()
