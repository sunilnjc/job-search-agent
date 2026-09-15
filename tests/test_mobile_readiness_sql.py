"""0011 against disposable real PostgreSQL, never a hosted DSN or user data."""
import json
import unittest
from pathlib import Path

from local_postgres import DisposablePostgres, SUPABASE_TEST_SCHEMAS
from jobagent.mobile.eligibility_review import job_fingerprint, review_id

ROOT = Path(__file__).resolve().parents[1]
OWNER = '00000000-0000-4000-8000-000000000001'
OTHER = '00000000-0000-4000-8000-000000000002'
JOB = '00000000-0000-4000-8000-000000000010'
SOURCE = '00000000-0000-4000-8000-000000000020'
RUN = '00000000-0000-4000-8000-000000000030'
RESUME = '00000000-0000-4000-8000-000000000041'
LETTER = '00000000-0000-4000-8000-000000000042'
JOB_DATA = dict(id=JOB, source_url='https://example.invalid/vacancy', title='Product designer',
                company_name='Fixture Łódź 🚀', description='Design accessible tools.\nUser research.',
                location_text='Remote', workplace_type='remote', employment_type=None)


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def identity(user=OWNER, role='authenticated'):
    return f"set local role {role}; set local request.jwt.claim.role={literal(role)}; set local request.jwt.claim.sub={literal(user)};\n"


def seed():
    sql = f"""
      insert into auth.users(id,email,email_confirmed_at) values('{OWNER}','one@fixture.invalid',now()),('{OTHER}','two@fixture.invalid',now());
      insert into public.mobile_usage_memberships(user_id,enabled) values('{OWNER}',true),('{OTHER}',true);
      update public.job_preferences set target_titles=array['Designer'] where user_id='{OWNER}';
      update public.job_preferences set target_titles=array['Finance'] where user_id='{OTHER}';
      insert into public.candidate_context(user_id,career_text) values('{OWNER}','Designed accessible tools.'),('{OTHER}','PRIVATE_OTHER_SENTINEL');
      insert into public.jobs(id,user_id,source,source_url,title,company_name,description,location_text,workplace_type)
        values('{JOB}','{OWNER}','manual',{literal(JOB_DATA['source_url'])},{literal(JOB_DATA['title'])},
        {literal(JOB_DATA['company_name'])},{literal(JOB_DATA['description'])},'Remote','remote');
      insert into public.resumes(id,user_id,label,storage_path,original_filename,mime_type,byte_size,is_default)
        values('{SOURCE}','{OWNER}','Source','{OWNER}/source.txt','source.txt','text/plain',10,true);
      insert into storage.objects(bucket_id,name,version,metadata) values('resumes','{OWNER}/source.txt','source-v1','{{"size":10}}');
      insert into public.mobile_answers(id,user_id,question,scope,answer)
        values('{review_id(OWNER, JOB)}','{OWNER}','Job-specific eligibility review (self-reported)','job:{JOB}',
        {literal(json.dumps(dict(status='eligible', confirmed=True, reason='Synthetic self report', job_fingerprint=job_fingerprint(JOB_DATA))))});
      insert into public.model_runs(id,user_id,job_id,operation,provider,model_name,status,input_summary)
        values('{RUN}','{OWNER}','{JOB}','prepare_documents','fixture','fixture','running',
        '{{"resume_id":"{SOURCE}","variant":"role_aligned","context_sha256":"{'a'*64}"}}');
    """
    for item_id, kind in [(RESUME, 'tailored_resume'), (LETTER, 'cover_letter')]:
        metadata = dict(id=item_id, job_id=JOB, resume_id=SOURCE, kind=kind, storage_path=f'{OWNER}/{item_id}.pdf',
                        filename=f'{kind}.pdf', mime_type='application/pdf', byte_size=10,
                        model_provider='fixture', model_name='fixture')
        sql += f"""
          insert into public.artifacts(id,user_id,job_id,resume_id,kind,storage_path,filename,mime_type,byte_size,model_provider,model_name)
            values('{item_id}','{OWNER}','{JOB}','{SOURCE}','{kind}','{OWNER}/{item_id}.pdf','{kind}.pdf','application/pdf',10,'fixture','fixture');
          insert into storage.objects(bucket_id,name,version,metadata) values('application-artifacts','{OWNER}/{item_id}.pdf','v1','{{"size":10}}');
          insert into public.mobile_artifact_operations(id,user_id,state,artifact_data,content_sha256)
            values('{item_id}','{OWNER}','ready',{literal(json.dumps(metadata))},'{'b'*64}');
        """
    return sql


def bind():
    return f"""
      select public.mobile_bind_packet_context('{RUN}','{SOURCE}','role_aligned',
        public.mobile_packet_context('{JOB}','{SOURCE}')->>'context_fingerprint','{'c'*64}',
        public.mobile_packet_context('{JOB}','{SOURCE}')->>'capture_version');
      update public.model_runs set status='succeeded',completed_at=clock_timestamp(),output_summary=
        '{{"artifact_ids":["{RESUME}","{LETTER}"],"documents":[{{"artifact_id":"{RESUME}","sha256":"{'b'*64}"}},{{"artifact_id":"{LETTER}","sha256":"{'b'*64}"}}]}}'
        where id='{RUN}';
    """


def approve():
    return f"""select public.mobile_review_packet('{JOB}','{RUN}','{RESUME}','{LETTER}',
      public.mobile_packet_readiness('{JOB}')->'packets'->0->>'packet_fingerprint',true);"""


class ReadinessPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = DisposablePostgres()
        cls.addClassCleanup(cls.db.close)
        cls.db.execute(SUPABASE_TEST_SCHEMAS)
        for path in sorted((ROOT/'supabase/migrations').glob('*.sql')):
            if path.name[:4].isdigit() and int(path.name[:4]) <= 12:
                cls.db.execute(path.read_text())
        cls.db.execute(seed())

    def transaction(self, sql, user=OWNER, before=''):
        return self.db.execute('begin;\n' + before + identity(user) + sql + '\nrollback;')

    def test_server_generation_pair_and_durable_review_round_trip(self):
        self.transaction(bind() + f"""
        do $$ declare s jsonb; result jsonb; begin
          s:=public.mobile_packet_readiness('{JOB}');
          assert s->'ready'='false'; assert s->>'reason'='packet_review_required';
          assert jsonb_array_length(s->'packets')=1;
          assert s->'packets'->0->'current'='true';
          assert s->'packets'->0->>'key'='{RUN}:pdf';
          result:=public.mobile_review_packet('{JOB}','{RUN}','{RESUME}','{LETTER}',s->'packets'->0->>'packet_fingerprint',true);
          assert result->'application'->>'status'='ready';
          assert public.mobile_packet_readiness('{JOB}')->'ready'='true';
          assert public.mobile_packet_readiness('{JOB}')->'review'->'current'='true';
          assert (select count(*)=1 from public.mobile_packet_reviews);
        end $$;
        """)

    def test_legacy_ready_record_never_promoted_without_receipt(self):
        self.transaction(f"""
          insert into public.applications(user_id,job_id,status) values('{OWNER}','{JOB}','ready');
          do $$ declare s jsonb; begin s:=public.mobile_packet_readiness('{JOB}');
            assert s->'ready'='false'; assert s->>'application_status'='draft';
            assert s->>'recorded_status'='ready'; assert s->'review'='null';
          end $$;
        """)

    def test_each_meaningful_context_change_invalidates_without_deleting_history(self):
        edits = [
            "update public.profiles set phone='changed'",
            "update public.candidate_context set career_text='Changed career'",
            "update public.candidate_context set career_background='{\"profession\":\"Finance\"}'",
            "update public.job_preferences set remote_preference='onsite'",
            "update public.job_preferences set discovery_rules='{\"remote_country_policy\":\"require_explicit\",\"remote_country_codes\":[\"AE\"],\"sponsorship_policy\":\"review\"}'",
            f"update public.jobs set description='New job' where id='{JOB}'",
            f"update storage.objects set version='new' where bucket_id='resumes'",
            f"update storage.objects set version='new' where bucket_id='application-artifacts'",
            f"update public.resumes set byte_size=11 where id='{SOURCE}'",
            f"insert into public.mobile_questions(user_id,job_id,prompt,answer,status) values('{OWNER}','{JOB}','Availability','Later','answered')",
            f"insert into public.mobile_answers(user_id,question,answer,scope) values('{OWNER}','Preference','Updated','profile')",
        ]
        for edit in edits:
            with self.subTest(edit=edit):
                # Administrator statement for storage metadata in local platform
                # stubs; every readiness call runs as the actual owner role.
                self.transaction(bind()+approve()+'reset role;'+edit+';'+identity()+f"""
                  do $$ declare s jsonb; begin s:=public.mobile_packet_readiness('{JOB}');
                    assert s->'ready'='false'; assert s->>'application_status'='draft';
                    assert (select count(*)=2 from public.artifacts);
                    assert (select count(*)=1 from public.mobile_packet_reviews);
                  end $$;
                """)

    def test_status_score_and_other_user_changes_do_not_stale_packet(self):
        self.transaction(bind()+approve()+f"""
          update public.jobs set status='matched',last_validated_at=now() where id='{JOB}';
          update public.resumes set label='Renamed',is_default=false where id='{SOURCE}';
          reset role; update public.candidate_context set career_text='Other changed' where user_id='{OTHER}';
        """+identity()+f"do $$ begin assert public.mobile_packet_readiness('{JOB}')->'ready'='true'; end $$;")

    def test_external_manual_progress_is_preserved_after_context_changes(self):
        self.transaction(bind()+approve()+f"""
          update public.applications set status='submitted',notes='User says applied externally';
          update public.candidate_context set career_text='Updated';
          do $$ declare s jsonb; begin s:=public.mobile_packet_readiness('{JOB}');
            assert s->>'application_status'='submitted'; assert s->'ready'='false';
            assert (select notes='User says applied externally' from public.applications);
          end $$;
        """)

    def test_cross_owner_and_revoked_access_fail_closed(self):
        self.transaction(bind()+approve()+identity(OTHER)+f"""
          do $$ begin
            assert (select count(*)=0 from public.mobile_packet_reviews);
            assert (select count(*)=0 from public.mobile_packet_contexts);
            begin perform public.mobile_packet_readiness('{JOB}'); raise exception 'Foreign job allowed';
            exception when sqlstate 'PT404' then null; end;
            begin perform public.mobile_bind_packet_context('{RUN}','{SOURCE}','role_aligned','{'a'*64}','{'c'*64}','{'a'*64}');
              raise exception 'Foreign bind allowed'; exception when sqlstate 'PT404' then null; end;
          end $$;
          reset role; update public.mobile_usage_memberships set enabled=false where user_id='{OWNER}';
        """+identity()+f"""
          do $$ begin
            begin perform public.mobile_packet_readiness('{JOB}'); raise exception 'Revoked user allowed';
            exception when insufficient_privilege then null; end;
          end $$;
        """)

    def test_eligibility_ascii_unicode_and_stale_posting_exact_match(self):
        # The seeded non-ASCII / emoji posting uses Python's actual fingerprint.
        self.transaction(bind()+f"""
          do $$ begin assert public.mobile_packet_readiness('{JOB}')->>'reason'='packet_review_required'; end $$;
          update public.jobs set description='Changed' where id='{JOB}';
          do $$ begin assert public.mobile_packet_readiness('{JOB}')->>'reason'='eligibility_required'; end $$;
        """)

    def test_pending_questions_and_unmatched_format_cannot_review(self):
        self.transaction(bind()+f"""
          insert into public.mobile_questions(user_id,prompt) values('{OWNER}','Missing fact');
          do $$ begin
            assert public.mobile_packet_readiness('{JOB}')->>'reason'='pending_questions';
            begin perform public.mobile_review_packet('{JOB}','{RUN}','{RESUME}','{LETTER}',
              public.mobile_packet_readiness('{JOB}')->'packets'->0->>'packet_fingerprint',true);
              raise exception 'Pending facts allowed'; exception when sqlstate 'PT409' then null; end;
          end $$;
        """)

    def test_bounded_lists_ambiguous_ids_and_partial_generation_fail_closed(self):
        for mutation in [
            f"update public.model_runs set output_summary=jsonb_set(output_summary,'{{artifact_ids}}','[\"{RESUME}\",\"{RESUME}\"]') where id='{RUN}'",
            f"update public.model_runs set status='failed' where id='{RUN}'",
            f"update public.artifacts set resume_id=null where id='{LETTER}'",
            f"delete from storage.objects where name='{OWNER}/{LETTER}.pdf'",
        ]:
            with self.subTest(mutation=mutation):
                self.transaction(bind()+'reset role;'+mutation+';'+identity()+f"""
                  do $$ begin assert jsonb_array_length(public.mobile_packet_readiness('{JOB}')->'packets')=0; end $$;
                """)
        self.transaction(f"""
          insert into public.mobile_answers(user_id,question,answer,scope)
            select '{OWNER}','Question '||n,'Answer','profile' from generate_series(1,100) n;
          -- Plus the job's eligibility row: builder's merged 100-row bound
          -- would omit at least one source. No context certificate is issued.
          do $$ begin assert public.mobile_packet_context('{JOB}','{SOURCE}')->>'error'='too_many_records'; end $$;
        """)

    def test_binding_compare_and_set_detects_change_and_does_not_overwrite(self):
        self.transaction(f"""
          do $$ declare old_hash text; begin
            old_hash:=public.mobile_packet_context('{JOB}','{SOURCE}')->>'context_fingerprint';
            update public.candidate_context set career_text='Changed during context build';
            begin perform public.mobile_bind_packet_context('{RUN}','{SOURCE}','role_aligned',old_hash,'{'c'*64}',
              public.mobile_packet_context('{JOB}','{SOURCE}')->>'capture_version');
              raise exception 'Stale snapshot accepted'; exception when sqlstate 'PT409' then null; end;
            assert (select count(*)=0 from public.mobile_packet_contexts);
          end $$;
        """)

    def test_capture_fence_detects_aba_across_committed_context_writes(self):
        before = json.loads(self.db.execute('begin;'+identity()+
            f"select public.mobile_packet_context('{JOB}','{SOURCE}'); commit;"))
        self.db.execute(f"begin; update public.candidate_context set career_text='Transient' where user_id='{OWNER}'; "
                        f"update public.candidate_context set career_text='Designed accessible tools.' where user_id='{OWNER}'; commit;")
        after = json.loads(self.db.execute('begin;'+identity()+
            f"select public.mobile_packet_context('{JOB}','{SOURCE}'); commit;"))
        self.assertEqual(before['context_fingerprint'], after['context_fingerprint'])
        self.assertNotEqual(before['capture_version'], after['capture_version'])
        self.transaction(f"""
          do $$ begin
            begin perform public.mobile_bind_packet_context('{RUN}','{SOURCE}','role_aligned',
              '{before['context_fingerprint']}','{'c'*64}','{before['capture_version']}');
              raise exception 'ABA snapshot accepted'; exception when sqlstate 'PT409' then null; end;
            assert (select count(*)=0 from public.mobile_packet_contexts);
          end $$;
        """)

    def test_grants_no_direct_writes_and_privacy_export_contains_receipts(self):
        self.transaction(bind()+approve()+f"""
          do $$ begin
            assert not has_table_privilege('authenticated','public.mobile_packet_reviews','INSERT');
            assert not has_table_privilege('authenticated','public.mobile_packet_contexts','UPDATE');
            assert not has_function_privilege('anon','public.mobile_packet_readiness(uuid)','EXECUTE');
            assert not has_function_privilege('service_role','public._mobile_privacy_snapshot_0010(uuid,uuid)','EXECUTE');
          end $$;
          reset role;
          insert into public.mobile_privacy_requests(user_id,kind) values('{OWNER}','export');
          set local role service_role;
          do $$ declare lease jsonb; data jsonb; begin
            lease:=public.mobile_privacy_claim();
            data:=public.mobile_privacy_snapshot((lease->>'id')::uuid,(lease->>'lease_token')::uuid);
            assert jsonb_array_length(data->'tables'->'mobile_packet_contexts')=1;
            assert jsonb_array_length(data->'tables'->'mobile_packet_reviews')=1;
            assert data::text not like '%PRIVATE_OTHER_SENTINEL%';
          end $$;
        """)


if __name__ == '__main__': unittest.main()
