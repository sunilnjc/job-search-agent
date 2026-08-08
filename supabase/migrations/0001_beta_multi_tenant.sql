-- Job Search Agent beta: Supabase Auth-backed, tenant-isolated data model.
-- This migration contains no credentials, seed data, or personally identifiable user data.

create extension if not exists pgcrypto with schema extensions;

-- Shared helper for tables that expose an updated_at column.
create or replace function public.set_updated_at()
returns trigger
language plpgsql
set search_path = public
as $$
begin
  new.updated_at = timezone('utc', now());
  return new;
end;
$$;

create table if not exists public.profiles (
  user_id uuid primary key references auth.users(id) on delete cascade,
  display_name text,
  phone text,
  base_location text,
  timezone text,
  onboarding_completed_at timestamptz,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.job_preferences (
  user_id uuid primary key references public.profiles(user_id) on delete cascade,
  target_titles text[] not null default '{}',
  preferred_locations text[] not null default '{}',
  preferred_regions text[] not null default '{}',
  remote_preference text not null default 'open'
    check (remote_preference in ('remote_only', 'hybrid', 'onsite', 'open')),
  sponsorship_required boolean not null default false,
  work_authorization_notes text,
  minimum_match_score numeric(4,2) not null default 7.00
    check (minimum_match_score between 0 and 10),
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

-- Metadata only. The file itself belongs in the private `resumes` Storage bucket.
create table if not exists public.resumes (
  id uuid primary key default extensions.gen_random_uuid(),
  user_id uuid not null references public.profiles(user_id) on delete cascade,
  label text not null,
  role_focus text,
  storage_path text not null,
  original_filename text not null,
  mime_type text,
  byte_size bigint check (byte_size is null or byte_size >= 0),
  is_default boolean not null default false,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  unique (id, user_id),
  unique (user_id, storage_path)
);
create unique index if not exists resumes_one_default_per_user
  on public.resumes (user_id) where is_default;

-- A job is a user-scoped saved/recommended copy. A future shared job catalogue can
-- feed this table without exposing another user's activity or application state.
create table if not exists public.jobs (
  id uuid primary key default extensions.gen_random_uuid(),
  user_id uuid not null references public.profiles(user_id) on delete cascade,
  source text not null,
  source_job_id text,
  source_url text not null,
  company_name text not null,
  title text not null,
  location_text text,
  workplace_type text check (workplace_type in ('remote', 'hybrid', 'onsite', 'unknown')),
  employment_type text,
  description text,
  eligibility_status text not null default 'unknown'
    check (eligibility_status in ('unknown', 'eligible', 'ineligible', 'needs_review')),
  status text not null default 'new'
    check (status in ('new', 'matched', 'ready', 'applied', 'excluded', 'archived')),
  exclusion_reason text,
  published_at timestamptz,
  last_validated_at timestamptz,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  unique (id, user_id),
  unique (user_id, source_url)
);
create index if not exists jobs_user_status_updated_idx
  on public.jobs (user_id, status, updated_at desc);
create index if not exists jobs_user_company_title_idx
  on public.jobs (user_id, company_name, title);
create index if not exists jobs_source_source_job_id_idx
  on public.jobs (source, source_job_id) where source_job_id is not null;

-- Keep model output auditable: several model runs/scores can exist for one job.
create table if not exists public.job_scores (
  id uuid primary key default extensions.gen_random_uuid(),
  user_id uuid not null references public.profiles(user_id) on delete cascade,
  job_id uuid not null,
  score numeric(4,2) not null check (score between 0 and 10),
  recommendation text not null check (recommendation in ('strong_match', 'match', 'review', 'exclude')),
  rationale text,
  model_provider text,
  model_name text,
  prompt_version text,
  created_at timestamptz not null default timezone('utc', now()),
  foreign key (job_id, user_id) references public.jobs(id, user_id) on delete cascade
);
create index if not exists job_scores_user_job_created_idx
  on public.job_scores (user_id, job_id, created_at desc);

create table if not exists public.applications (
  id uuid primary key default extensions.gen_random_uuid(),
  user_id uuid not null references public.profiles(user_id) on delete cascade,
  job_id uuid not null,
  status text not null default 'draft'
    check (status in ('draft', 'ready', 'submitted', 'interviewing', 'rejected', 'withdrawn', 'closed')),
  applied_at timestamptz,
  submission_url text,
  notes text,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  unique (id, user_id),
  unique (user_id, job_id),
  foreign key (job_id, user_id) references public.jobs(id, user_id) on delete cascade
);
create index if not exists applications_user_status_updated_idx
  on public.applications (user_id, status, updated_at desc);

-- Generated cover letters, tailored resumes, and answer packets. File payloads are
-- stored in private Storage; this table is the application-facing metadata.
create table if not exists public.artifacts (
  id uuid primary key default extensions.gen_random_uuid(),
  user_id uuid not null references public.profiles(user_id) on delete cascade,
  job_id uuid not null,
  application_id uuid,
  resume_id uuid,
  kind text not null check (kind in ('tailored_resume', 'cover_letter', 'answer_packet', 'other')),
  storage_path text not null,
  filename text not null,
  mime_type text,
  byte_size bigint check (byte_size is null or byte_size >= 0),
  model_provider text,
  model_name text,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  unique (user_id, storage_path),
  foreign key (job_id, user_id) references public.jobs(id, user_id) on delete cascade,
  foreign key (application_id, user_id) references public.applications(id, user_id) on delete cascade,
  foreign key (resume_id, user_id) references public.resumes(id, user_id) on delete set null (resume_id)
);
create index if not exists artifacts_user_job_created_idx
  on public.artifacts (user_id, job_id, created_at desc);
create index if not exists artifacts_application_idx
  on public.artifacts (application_id) where application_id is not null;

-- Tracks every non-trivial provider call without recording secret values.
create table if not exists public.model_runs (
  id uuid primary key default extensions.gen_random_uuid(),
  user_id uuid not null references public.profiles(user_id) on delete cascade,
  job_id uuid,
  application_id uuid,
  operation text not null,
  provider text not null,
  model_name text not null,
  status text not null default 'queued'
    check (status in ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
  input_summary jsonb not null default '{}'::jsonb,
  output_summary jsonb not null default '{}'::jsonb,
  error_message text,
  started_at timestamptz,
  completed_at timestamptz,
  created_at timestamptz not null default timezone('utc', now()),
  foreign key (job_id, user_id) references public.jobs(id, user_id) on delete cascade,
  foreign key (application_id, user_id) references public.applications(id, user_id) on delete cascade
);
create index if not exists model_runs_user_created_idx
  on public.model_runs (user_id, created_at desc);
create index if not exists model_runs_user_status_idx
  on public.model_runs (user_id, status);

-- Each attempted direct application is immutable operational history in spirit;
-- errors and action-required states remain available for the user to review.
create table if not exists public.application_attempts (
  id uuid primary key default extensions.gen_random_uuid(),
  user_id uuid not null references public.profiles(user_id) on delete cascade,
  application_id uuid not null,
  status text not null check (status in ('queued', 'preparing', 'action_required', 'submitted', 'failed', 'cancelled')),
  ats_provider text,
  details jsonb not null default '{}'::jsonb,
  error_message text,
  attempted_at timestamptz not null default timezone('utc', now()),
  completed_at timestamptz,
  foreign key (application_id, user_id) references public.applications(id, user_id) on delete cascade
);
create index if not exists application_attempts_user_application_idx
  on public.application_attempts (user_id, application_id, attempted_at desc);

-- Create a profile when a Supabase Auth user is created. Sensitive identity data
-- stays in auth.users; this profile intentionally does not copy email addresses.
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer set search_path = public
as $$
begin
  insert into public.profiles (user_id, display_name)
  values (new.id, coalesce(new.raw_user_meta_data ->> 'full_name', new.raw_user_meta_data ->> 'name'))
  on conflict (user_id) do nothing;

  insert into public.job_preferences (user_id)
  values (new.id)
  on conflict (user_id) do nothing;

  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute procedure public.handle_new_user();

drop trigger if exists profiles_set_updated_at on public.profiles;
create trigger profiles_set_updated_at before update on public.profiles
  for each row execute procedure public.set_updated_at();
drop trigger if exists job_preferences_set_updated_at on public.job_preferences;
create trigger job_preferences_set_updated_at before update on public.job_preferences
  for each row execute procedure public.set_updated_at();
drop trigger if exists resumes_set_updated_at on public.resumes;
create trigger resumes_set_updated_at before update on public.resumes
  for each row execute procedure public.set_updated_at();
drop trigger if exists jobs_set_updated_at on public.jobs;
create trigger jobs_set_updated_at before update on public.jobs
  for each row execute procedure public.set_updated_at();
drop trigger if exists applications_set_updated_at on public.applications;
create trigger applications_set_updated_at before update on public.applications
  for each row execute procedure public.set_updated_at();
drop trigger if exists artifacts_set_updated_at on public.artifacts;
create trigger artifacts_set_updated_at before update on public.artifacts
  for each row execute procedure public.set_updated_at();

-- Every application table uses strict user ownership. The service-role backend can
-- still perform privileged jobs, but browser clients only access auth.uid() rows.
alter table public.profiles enable row level security;
alter table public.job_preferences enable row level security;
alter table public.resumes enable row level security;
alter table public.jobs enable row level security;
alter table public.job_scores enable row level security;
alter table public.applications enable row level security;
alter table public.artifacts enable row level security;
alter table public.model_runs enable row level security;
alter table public.application_attempts enable row level security;

drop policy if exists profiles_owner_only on public.profiles;
create policy profiles_owner_only on public.profiles for all
  using (auth.uid() = user_id) with check (auth.uid() = user_id);
drop policy if exists job_preferences_owner_only on public.job_preferences;
create policy job_preferences_owner_only on public.job_preferences for all
  using (auth.uid() = user_id) with check (auth.uid() = user_id);
drop policy if exists resumes_owner_only on public.resumes;
create policy resumes_owner_only on public.resumes for all
  using (auth.uid() = user_id) with check (auth.uid() = user_id);
drop policy if exists jobs_owner_only on public.jobs;
create policy jobs_owner_only on public.jobs for all
  using (auth.uid() = user_id) with check (auth.uid() = user_id);
drop policy if exists job_scores_owner_only on public.job_scores;
create policy job_scores_owner_only on public.job_scores for all
  using (auth.uid() = user_id) with check (auth.uid() = user_id);
drop policy if exists applications_owner_only on public.applications;
create policy applications_owner_only on public.applications for all
  using (auth.uid() = user_id) with check (auth.uid() = user_id);
drop policy if exists artifacts_owner_only on public.artifacts;
create policy artifacts_owner_only on public.artifacts for all
  using (auth.uid() = user_id) with check (auth.uid() = user_id);
drop policy if exists model_runs_owner_only on public.model_runs;
create policy model_runs_owner_only on public.model_runs for all
  using (auth.uid() = user_id) with check (auth.uid() = user_id);
drop policy if exists application_attempts_owner_only on public.application_attempts;
create policy application_attempts_owner_only on public.application_attempts for all
  using (auth.uid() = user_id) with check (auth.uid() = user_id);

-- Private buckets. Object names must start with the authenticated user's UUID,
-- e.g. <auth.uid()>/original.pdf or <auth.uid()>/<application-id>/cover-letter.pdf.
insert into storage.buckets (id, name, public)
values ('resumes', 'resumes', false), ('application-artifacts', 'application-artifacts', false)
on conflict (id) do update set public = false;

drop policy if exists resumes_storage_owner_select on storage.objects;
create policy resumes_storage_owner_select on storage.objects for select
  using (bucket_id = 'resumes' and (storage.foldername(name))[1] = auth.uid()::text);
drop policy if exists resumes_storage_owner_insert on storage.objects;
create policy resumes_storage_owner_insert on storage.objects for insert
  with check (bucket_id = 'resumes' and (storage.foldername(name))[1] = auth.uid()::text);
drop policy if exists resumes_storage_owner_update on storage.objects;
create policy resumes_storage_owner_update on storage.objects for update
  using (bucket_id = 'resumes' and (storage.foldername(name))[1] = auth.uid()::text)
  with check (bucket_id = 'resumes' and (storage.foldername(name))[1] = auth.uid()::text);
drop policy if exists resumes_storage_owner_delete on storage.objects;
create policy resumes_storage_owner_delete on storage.objects for delete
  using (bucket_id = 'resumes' and (storage.foldername(name))[1] = auth.uid()::text);

drop policy if exists artifacts_storage_owner_select on storage.objects;
create policy artifacts_storage_owner_select on storage.objects for select
  using (bucket_id = 'application-artifacts' and (storage.foldername(name))[1] = auth.uid()::text);
drop policy if exists artifacts_storage_owner_insert on storage.objects;
create policy artifacts_storage_owner_insert on storage.objects for insert
  with check (bucket_id = 'application-artifacts' and (storage.foldername(name))[1] = auth.uid()::text);
drop policy if exists artifacts_storage_owner_update on storage.objects;
create policy artifacts_storage_owner_update on storage.objects for update
  using (bucket_id = 'application-artifacts' and (storage.foldername(name))[1] = auth.uid()::text)
  with check (bucket_id = 'application-artifacts' and (storage.foldername(name))[1] = auth.uid()::text);
drop policy if exists artifacts_storage_owner_delete on storage.objects;
create policy artifacts_storage_owner_delete on storage.objects for delete
  using (bucket_id = 'application-artifacts' and (storage.foldername(name))[1] = auth.uid()::text);
