-- Additive mobile career workspace. No seed data, credentials, entitlements,
-- changes to 0001 policies, or access to another user's profile/job rows.
begin;

create table public.candidate_context (
  user_id uuid primary key default auth.uid()
    references public.profiles(user_id) on delete cascade,
  career_text text not null default ''
    check (octet_length(career_text) <= 400000),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.mobile_questions (
  id uuid primary key default extensions.gen_random_uuid(),
  user_id uuid not null default auth.uid()
    references public.profiles(user_id) on delete cascade,
  job_id uuid,
  prompt text not null
    check (length(btrim(prompt)) > 0 and octet_length(prompt) <= 24000),
  answer text check (answer is null or (length(btrim(answer)) > 0 and octet_length(answer) <= 48000)),
  status text not null default 'pending' check (status in ('pending', 'answered')),
  remember boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (id, user_id),
  check ((status = 'pending' and answer is null) or (status = 'answered' and answer is not null)),
  foreign key (job_id, user_id) references public.jobs(id, user_id)
    on delete set null (job_id)
);

create table public.mobile_answers (
  id uuid primary key default extensions.gen_random_uuid(),
  user_id uuid not null default auth.uid()
    references public.profiles(user_id) on delete cascade,
  question text not null
    check (length(btrim(question)) > 0 and octet_length(question) <= 24000),
  answer text not null
    check (length(btrim(answer)) > 0 and octet_length(answer) <= 48000),
  scope text not null default 'profile'
    check (length(btrim(scope)) > 0 and octet_length(scope) <= 80),
  source_question_id uuid,
  confirmed_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (source_question_id, user_id) references public.mobile_questions(id, user_id)
    on delete set null (source_question_id)
);

create index mobile_questions_user_status_updated_idx
  on public.mobile_questions (user_id, status, updated_at desc);
create index mobile_questions_user_job_idx
  on public.mobile_questions (user_id, job_id) where job_id is not null;
create index mobile_answers_user_scope_updated_idx
  on public.mobile_answers (user_id, scope, updated_at desc);
create index mobile_answers_source_question_owner_idx
  on public.mobile_answers (source_question_id, user_id) where source_question_id is not null;

create trigger candidate_context_set_updated_at before update on public.candidate_context
  for each row execute function public.set_updated_at();
create trigger mobile_questions_set_updated_at before update on public.mobile_questions
  for each row execute function public.set_updated_at();
create trigger mobile_answers_set_updated_at before update on public.mobile_answers
  for each row execute function public.set_updated_at();

alter table public.candidate_context enable row level security;
alter table public.mobile_questions enable row level security;
alter table public.mobile_answers enable row level security;

create policy candidate_context_owner_only on public.candidate_context
  for all to authenticated
  using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
create policy mobile_questions_owner_only on public.mobile_questions
  for all to authenticated
  using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
create policy mobile_answers_owner_only on public.mobile_answers
  for all to authenticated
  using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);

-- Override Supabase default table privileges on these NEW tables only. Ownership
-- is checked by RLS on inserts; IDs/owners/audit timestamps cannot be updated by
-- a direct authenticated client. None of these columns controls entitlements.
revoke all on public.candidate_context, public.mobile_questions, public.mobile_answers from anon, authenticated;
grant select, delete on public.candidate_context, public.mobile_questions, public.mobile_answers to authenticated;
grant insert (user_id, career_text) on public.candidate_context to authenticated;
grant update (career_text) on public.candidate_context to authenticated;
grant insert (id, user_id, job_id, prompt, answer, status, remember)
  on public.mobile_questions to authenticated;
grant update (job_id, prompt, answer, status, remember)
  on public.mobile_questions to authenticated;
grant insert (id, user_id, question, answer, scope, source_question_id)
  on public.mobile_answers to authenticated;
grant update (question, answer, scope, source_question_id)
  on public.mobile_answers to authenticated;
grant all on public.candidate_context, public.mobile_questions, public.mobile_answers to service_role;

commit;
