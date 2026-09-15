-- Additive, no seed/backfill, no hosted execution. Apply after 0001..0010.
-- A receipt records the owner's review, NOT employer submission or verified
-- qualifications. Historical status='ready' has no receipt and derives draft.
-- Contexts contain digests only; no duplicate resumes, prompts or contact data.
begin;

create table public.mobile_packet_contexts (
  run_id uuid primary key references public.model_runs(id) on delete cascade,
  user_id uuid not null references public.profiles(user_id) on delete cascade,
  job_id uuid not null,
  resume_id uuid not null,
  variant text not null check (variant in ('role_aligned','career_change','sse','fde')),
  context_fingerprint text not null check (context_fingerprint ~ '^[0-9a-f]{64}$'),
  model_context_sha256 text not null check (model_context_sha256 ~ '^[0-9a-f]{64}$'),
  resume_sha256 text not null check (resume_sha256 ~ '^[0-9a-f]{64}$'),
  created_at timestamptz not null default clock_timestamp(),
  foreign key (job_id,user_id) references public.jobs(id,user_id) on delete cascade,
  foreign key (resume_id,user_id) references public.resumes(id,user_id) on delete cascade
);
create index mobile_packet_contexts_owner_job on public.mobile_packet_contexts(user_id,job_id,created_at desc);
create table public.mobile_packet_reviews (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles(user_id) on delete cascade,
  job_id uuid not null,
  run_id uuid not null references public.model_runs(id) on delete cascade,
  resume_artifact_id uuid not null references public.artifacts(id) on delete cascade,
  letter_artifact_id uuid not null references public.artifacts(id) on delete cascade,
  packet_fingerprint text not null check (packet_fingerprint ~ '^[0-9a-f]{64}$'),
  reviewed_at timestamptz not null default clock_timestamp(),
  unique (user_id,job_id),
  check (resume_artifact_id <> letter_artifact_id),
  foreign key (job_id,user_id) references public.jobs(id,user_id) on delete cascade
);
alter table public.mobile_packet_contexts enable row level security;
alter table public.mobile_packet_reviews enable row level security;
create policy mobile_packet_contexts_owner on public.mobile_packet_contexts for select to authenticated
  using (auth.uid()=user_id and public.mobile_has_access());
create policy mobile_packet_reviews_owner on public.mobile_packet_reviews for select to authenticated
  using (auth.uid()=user_id and public.mobile_has_access());
revoke all on public.mobile_packet_contexts,public.mobile_packet_reviews from public,anon,authenticated;
grant select on public.mobile_packet_contexts,public.mobile_packet_reviews to authenticated;
grant all on public.mobile_packet_contexts,public.mobile_packet_reviews to service_role;

-- All helpers have no public EXECUTE. Every public entrypoint authenticates and
-- helpers derive the owner from auth.uid(), never a caller-supplied owner UUID.
create function public.mobile_packet_context(p_job_id uuid,p_resume_id uuid) returns jsonb
language plpgsql security definer set search_path='' as $$
declare u uuid:=auth.uid(); p jsonb; c jsonb; pref jsonb; j jsonb; r jsonb; obj jsonb;
  answers jsonb; questions jsonb; source_hash text; payload jsonb; versions jsonb;
begin
  if not public.mobile_has_access() then raise insufficient_privilege using message='Access denied'; end if;
  select jsonb_build_object('display_name',display_name,'phone',phone,'base_location',base_location,'timezone',timezone)
    into p from public.profiles where user_id=u;
  select jsonb_build_object('career_text',career_text,'career_background',career_background)
    into c from public.candidate_context where user_id=u;
  select to_jsonb(q)-array['created_at','updated_at'] into pref from public.job_preferences q where user_id=u;
  select jsonb_build_object('id',id,'source',source,'source_job_id',source_job_id,'source_url',source_url,
    'title',title,'company_name',company_name,'description',description,'location_text',location_text,
    'workplace_type',workplace_type,'employment_type',employment_type)
    into j from public.jobs where user_id=u and id=p_job_id;
  if j is null then raise sqlstate 'PT404' using message='Job not found'; end if;
  select to_jsonb(q)-array['label','role_focus','is_default','created_at','updated_at'] into r
    from public.resumes q where user_id=u and id=p_resume_id;
  if r is null then return jsonb_build_object('error','source_missing'); end if;
  if p is null or pref is null or nullif(btrim(c->>'career_text'),'') is null
    or nullif(btrim(j->>'description'),'') is null then return jsonb_build_object('error','facts_missing'); end if;
  select jsonb_build_object('id',id,'version',version,'updated_at',updated_at,'metadata',metadata)
    into obj from storage.objects where bucket_id='resumes' and name=r->>'storage_path'
      and split_part(name,'/',1)=u::text;
  if obj is null then return jsonb_build_object('error','source_missing'); end if;
  select content_sha256 into source_hash from public.mobile_resume_operations
    where user_id=u and id=p_resume_id and state='ready';
  select coalesce(jsonb_agg(to_jsonb(a) order by a.id),'[]') into answers from (
    select id,question,answer,scope,source_question_id,confirmed_at from public.mobile_answers
      where user_id=u and scope in ('profile','job:'||p_job_id::text)
      order by id limit 201) a;
  select coalesce(jsonb_agg(to_jsonb(q) order by q.id),'[]') into questions from (
    select id,prompt,answer,status,job_id from public.mobile_questions
      where user_id=u and (job_id=p_job_id or job_id is null) and status='answered'
      order by id limit 201) q;
  -- build_context currently bounds its merged answer context at 100. Do not
  -- certify a generation while that builder could silently omit saved facts.
  -- Conservative counting includes remembered duplicates and the review row.
  if jsonb_array_length(answers)+jsonb_array_length(questions)>100 then
    return jsonb_build_object('error','too_many_records'); end if;
  payload:=jsonb_build_object('version',1,'user_id',u,'profile',p,'career',c,'preferences',pref,
    'job',j,'resume',r,'object',obj,'source_hash',source_hash,'answers',answers,'questions',questions,
    'email',(select email from auth.users where id=u));
  -- A separate short-lived capture fence uses tuple versions to detect A->B->A
  -- writes while the API builds its model context. Do not use tuple versions for
  -- lasting freshness: renaming a resume or recording progress is not a new fact.
  versions:=jsonb_build_object(
    'profile',(select xmin::text from public.profiles where user_id=u),
    'career',(select xmin::text from public.candidate_context where user_id=u),
    'preferences',(select xmin::text from public.job_preferences where user_id=u),
    'job',(select xmin::text from public.jobs where user_id=u and id=p_job_id),
    'resume',(select xmin::text from public.resumes where user_id=u and id=p_resume_id),
    'auth',(select xmin::text from auth.users where id=u),
    'object',(select xmin::text from storage.objects where bucket_id='resumes' and name=r->>'storage_path'),
    'answers',(select jsonb_agg(jsonb_build_array(id,xmin::text) order by id) from public.mobile_answers
      where user_id=u and scope in ('profile','job:'||p_job_id::text)),
    'questions',(select jsonb_agg(jsonb_build_array(id,xmin::text) order by id) from public.mobile_questions
      where user_id=u and (job_id=p_job_id or job_id is null) and status='answered'));
  return jsonb_build_object('user_id',u,'job_id',p_job_id,'resume_id',p_resume_id,
    'context_fingerprint',encode(extensions.digest(payload::text,'sha256'),'hex'),
    'capture_version',encode(extensions.digest(versions::text,'sha256'),'hex'),
    'source',jsonb_build_object('storage_path',r->>'storage_path','byte_size',r->'byte_size','sha256',source_hash));
end; $$;

create function public.mobile_bind_packet_context(p_run_id uuid,p_resume_id uuid,p_variant text,
  p_context_fingerprint text,p_resume_sha256 text,p_capture_version text) returns jsonb
language plpgsql security definer set search_path='' as $$
declare u uuid:=auth.uid(); run public.model_runs%rowtype; c jsonb; prior public.mobile_packet_contexts%rowtype;
begin
  if not public.mobile_has_access() then raise insufficient_privilege using message='Access denied'; end if;
  if p_variant is null or p_variant not in ('role_aligned','career_change','sse','fde')
    or p_context_fingerprint is null or p_context_fingerprint !~ '^[0-9a-f]{64}$'
    or p_resume_sha256 is null or p_resume_sha256 !~ '^[0-9a-f]{64}$'
    or p_capture_version is null or p_capture_version !~ '^[0-9a-f]{64}$' then
    raise sqlstate 'PT422' using message='Invalid preparation context'; end if;
  select * into run from public.model_runs where id=p_run_id and user_id=u for update;
  if not found then raise sqlstate 'PT404' using message='Preparation not found'; end if;
  if run.operation<>'prepare_documents' or run.status<>'running'
    or run.input_summary->>'resume_id' is distinct from p_resume_id::text
    or run.input_summary->>'variant' is distinct from p_variant
    or coalesce(run.input_summary->>'context_sha256','') !~ '^[0-9a-f]{64}$' then
    raise sqlstate 'PT409' using message='Preparation audit is not current'; end if;
  c:=public.mobile_packet_context(run.job_id,p_resume_id);
  if c->>'context_fingerprint' is distinct from p_context_fingerprint or c ? 'error'
    or c->>'capture_version' is distinct from p_capture_version
    or (c->'source'->>'sha256' is not null and c->'source'->>'sha256' <> p_resume_sha256) then
    raise sqlstate 'PT409' using message='Career, job or source context changed. No AI should be called'; end if;
  select * into prior from public.mobile_packet_contexts where run_id=p_run_id;
  if found then
    if prior.user_id<>u or prior.context_fingerprint<>p_context_fingerprint or prior.resume_id<>p_resume_id
      or prior.resume_sha256<>p_resume_sha256 or prior.model_context_sha256<>run.input_summary->>'context_sha256'
      or prior.variant<>p_variant then raise sqlstate 'PT409' using message='Preparation identity conflict'; end if;
  else
    insert into public.mobile_packet_contexts(run_id,user_id,job_id,resume_id,variant,context_fingerprint,model_context_sha256,resume_sha256)
      values(p_run_id,u,run.job_id,p_resume_id,p_variant,p_context_fingerprint,run.input_summary->>'context_sha256',p_resume_sha256);
  end if;
  return jsonb_build_object('user_id',u,'run_id',p_run_id,'bound',true);
end; $$;

-- Validate authoritative model-run references and durable artifact intents. No
-- filename parsing, approximate timestamps or newest-file guessing is used.
create function public._mobile_packet(p_run_id uuid,p_format text) returns jsonb
language plpgsql security definer set search_path='' as $$
declare u uuid:=auth.uid(); binding public.mobile_packet_contexts%rowtype; run public.model_runs%rowtype;
  a public.artifacts%rowtype; op public.mobile_artifact_operations%rowtype; c jsonb; ids jsonb; docs jsonb;
  doc jsonb; obj jsonb; files jsonb:='[]'; selected jsonb:='[]'; seen text[]:='{}'; slots text[]:='{}';
  item text; slot text; mime text; code text; current_context boolean; packet_hash text;
begin
  select * into binding from public.mobile_packet_contexts where run_id=p_run_id and user_id=u;
  if not found then return null; end if;
  select * into run from public.model_runs where id=p_run_id and user_id=u and job_id=binding.job_id;
  if not found or run.status<>'succeeded' or run.operation<>'prepare_documents'
    or run.input_summary->>'context_sha256' is distinct from binding.model_context_sha256
    or run.input_summary->>'resume_id' is distinct from binding.resume_id::text
    or run.input_summary->>'variant' is distinct from binding.variant then return null; end if;
  ids:=run.output_summary->'artifact_ids'; docs:=run.output_summary->'documents';
  if jsonb_typeof(ids) is distinct from 'array' or jsonb_typeof(docs) is distinct from 'array' then return null; end if;
  if jsonb_array_length(ids) not between 2 and 6 or jsonb_array_length(docs)<>jsonb_array_length(ids) then return null; end if;
  if p_format='pdf' then mime:='application/pdf';
  elsif p_format='docx' then mime:='application/vnd.openxmlformats-officedocument.wordprocessingml.document';
  else return null; end if;
  for item in select value #>> '{}' from jsonb_array_elements(ids) loop
    if item is null or item !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' or item=any(seen) then return null; end if;
    seen:=array_append(seen,item);
    select * into a from public.artifacts where id=item::uuid and user_id=u and job_id=binding.job_id and resume_id=binding.resume_id;
    if not found or a.byte_size is null or a.byte_size<=0 then return null; end if;
    select * into op from public.mobile_artifact_operations where id=a.id and user_id=u and state='ready';
    if not found or (op.artifact_data - 'application_id') is distinct from
      (jsonb_build_object('id',a.id,'job_id',a.job_id,'resume_id',a.resume_id,'kind',a.kind,'storage_path',a.storage_path,
        'filename',a.filename,'mime_type',a.mime_type,'byte_size',a.byte_size,'model_provider',a.model_provider,'model_name',a.model_name)) then return null; end if;
    select value into doc from jsonb_array_elements(docs) where value->>'artifact_id'=item;
    if not found or (select count(*) from jsonb_array_elements(docs) where value->>'artifact_id'=item)<>1
      or doc->>'sha256' is distinct from op.content_sha256 then return null; end if;
    select jsonb_build_object('id',id,'version',version,'updated_at',updated_at,'metadata',metadata) into obj
      from storage.objects where bucket_id='application-artifacts' and name=a.storage_path and split_part(name,'/',1)=u::text;
    if obj is null then return null; end if;
    slot:=a.kind||':'||coalesce(a.mime_type,'');
    if slot=any(slots) then return null; end if;
    slots:=array_append(slots,slot);
    files:=files||jsonb_build_array(jsonb_build_object('artifact',to_jsonb(a)-array['created_at','updated_at','application_id'],
      'sha256',op.content_sha256,'object',obj));
    if a.mime_type=mime and a.kind in ('tailored_resume','cover_letter') then
      selected:=selected||jsonb_build_array(jsonb_build_object('id',a.id,'job_id',a.job_id,'resume_id',a.resume_id,
        'kind',a.kind,'filename',a.filename,'mime_type',a.mime_type,'byte_size',a.byte_size,'created_at',a.created_at,'sha256',op.content_sha256));
    end if;
  end loop;
  if jsonb_array_length(selected)<>2 then return null; end if;
  select jsonb_agg(value order by case value->>'kind' when 'tailored_resume' then 0 else 1 end) into selected from jsonb_array_elements(selected);
  select jsonb_agg(value order by value->'artifact'->>'id') into files from jsonb_array_elements(files);
  c:=public.mobile_packet_context(binding.job_id,binding.resume_id);
  current_context:=not(c ? 'error') and c->>'context_fingerprint'=binding.context_fingerprint;
  code:=case when current_context then null else coalesce(c->>'error','generation_context_changed') end;
  packet_hash:=encode(extensions.digest(jsonb_build_object('run_id',run.id,'context',binding.context_fingerprint,
    'model_context',binding.model_context_sha256,'source_sha256',binding.resume_sha256,'files',files,'format',p_format)::text,'sha256'),'hex');
  return jsonb_build_object('key',run.id::text||':'||p_format,'run_id',run.id,'resume_id',binding.resume_id,'variant',binding.variant,
    'format',p_format,'generated_at',run.completed_at,'current',current_context,'issue',code,
    'context_fingerprint',binding.context_fingerprint,'packet_fingerprint',packet_hash,'source_sha256',binding.resume_sha256,'artifacts',selected);
end; $$;

-- Existing eligibility_review.py uses hashlib.sha256(json.dumps(...,
-- sort_keys=True)). Reproduce its ASCII JSON exactly, including astral Unicode,
-- rather than accepting a stale self-report or hashing PostgreSQL's key order.
create function public._mobile_ascii_json(v text) returns text
language plpgsql immutable set search_path='' as $$
declare s text:=coalesce(to_json(v)::text,'null'); out text:=''; cp int; ch text;
begin
  for i in 1..char_length(s) loop
    ch:=substr(s,i,1); cp:=ascii(ch);
    if cp<128 then out:=out||ch;
    elsif cp<=65535 then out:=out||chr(92)||'u'||lpad(to_hex(cp),4,'0');
    else cp:=cp-65536; out:=out||chr(92)||'u'||to_hex(55296+cp/1024)||chr(92)||'u'||to_hex(56320+cp%1024);
    end if;
  end loop;
  return out;
end; $$;
create function public._mobile_eligibility(p_job_id uuid) returns boolean
language plpgsql security definer set search_path='' as $$
declare u uuid:=auth.uid(); j jsonb; er public.mobile_answers%rowtype; e jsonb; s text; h bytea; expected_id uuid;
begin
  select to_jsonb(q) into j from public.jobs q where user_id=u and id=p_job_id;
  if j is null then return false; end if;
  h:=substring(extensions.digest(uuid_send(u)||convert_to('eligibility-review:'||p_job_id::text,'UTF8'),'sha1') from 1 for 16);
  h:=set_byte(set_byte(h,6,(get_byte(h,6)&15)|80),8,(get_byte(h,8)&63)|128);
  expected_id:=encode(h,'hex')::uuid;
  select * into er from public.mobile_answers where user_id=u and id=expected_id
    and scope='job:'||p_job_id::text and question='Job-specific eligibility review (self-reported)';
  if not found or er.confirmed_at is null then return false; end if;
  select '{'||string_agg(public._mobile_ascii_json(k)||': '||public._mobile_ascii_json(j->>k),', ' order by k collate "C")||'}' into s
    from unnest(array['source_url','title','company_name','description','location_text','workplace_type','employment_type']) k;
  begin e:=er.answer::jsonb; exception when invalid_text_representation then return false; end;
  return coalesce(e->>'status'='eligible' and e->'confirmed'='true'::jsonb
    and e->>'job_fingerprint'=encode(extensions.digest(s,'sha256'),'hex'),false);
end; $$;

create function public.mobile_packet_readiness(p_job_id uuid) returns jsonb
language plpgsql security definer set search_path='' as $$
declare u uuid:=auth.uid(); packets jsonb:='[]'; packet jsonb; binding record; format text; n int;
  j public.jobs%rowtype; review public.mobile_packet_reviews%rowtype; app public.applications%rowtype;
  eligible boolean:=false; pending int; ready boolean:=false;
  reason text:='packet_review_required'; effective_status text; receipt jsonb;
begin
  if not public.mobile_has_access() then raise insufficient_privilege using message='Access denied'; end if;
  select * into j from public.jobs where id=p_job_id and user_id=u;
  if not found then raise sqlstate 'PT404' using message='Job not found'; end if;
  select count(*) into n from (select 1 from public.mobile_packet_contexts where user_id=u and job_id=p_job_id limit 201) q;
  if n>200 then reason:='too_many_records';
  else
    for binding in select run_id from public.mobile_packet_contexts where user_id=u and job_id=p_job_id order by created_at desc,run_id limit 200 loop
      foreach format in array array['pdf','docx'] loop
        packet:=public._mobile_packet(binding.run_id,format);
        if packet is not null then packets:=packets||jsonb_build_array(packet); end if;
      end loop;
    end loop;
  end if;
  select count(*) into pending from (select 1 from public.mobile_questions where user_id=u
    and (job_id=p_job_id or job_id is null) and status<>'answered' limit 201) q;
  eligible:=public._mobile_eligibility(p_job_id);
  select * into review from public.mobile_packet_reviews where user_id=u and job_id=p_job_id;
  if found then
    select value into packet from jsonb_array_elements(packets) where value->>'run_id'=review.run_id::text
      and value->>'packet_fingerprint'=review.packet_fingerprint
      and value->'artifacts'->0->>'id'=review.resume_artifact_id::text and value->'artifacts'->1->>'id'=review.letter_artifact_id::text;
    ready:=packet is not null and packet->'current'='true'::jsonb and eligible and pending=0;
    if not ready and n<=200 then reason:='review_stale'; end if;
    receipt:=jsonb_build_object('id',review.id,'run_id',review.run_id,'packet_fingerprint',review.packet_fingerprint,
      'resume_artifact_id',review.resume_artifact_id,'letter_artifact_id',review.letter_artifact_id,'reviewed_at',review.reviewed_at,'current',ready);
  end if;
  if pending>0 then reason:='pending_questions'; ready:=false; end if;
  if not coalesce(eligible,false) then reason:='eligibility_required'; ready:=false; end if;
  select * into app from public.applications where user_id=u and job_id=p_job_id;
  effective_status:=case when app.status in ('submitted','interviewing','rejected','withdrawn','closed') then app.status
    when app.status='ready' and ready then 'ready' else 'draft' end;
  return jsonb_build_object('user_id',u,'job_id',p_job_id,'version','packet-v1','packets',packets,'review',receipt,
    'ready',coalesce(ready and app.status='ready',false),'reason',case when ready then null else reason end,
    'pending_questions',pending,'application_status',effective_status,'recorded_status',app.status,'application_id',app.id);
end; $$;

create function public.mobile_review_packet(p_job_id uuid,p_run_id uuid,p_resume_artifact_id uuid,
  p_letter_artifact_id uuid,p_packet_fingerprint text,p_confirmed boolean) returns jsonb
language plpgsql security definer set search_path='' as $$
declare u uuid:=auth.uid(); state jsonb; packet jsonb; app public.applications%rowtype;
begin
  if not public.mobile_has_access() then raise insufficient_privilege using message='Access denied'; end if;
  if p_confirmed is distinct from true or p_packet_fingerprint is null or p_packet_fingerprint !~ '^[0-9a-f]{64}$'
    or p_resume_artifact_id is null or p_letter_artifact_id is null or p_resume_artifact_id=p_letter_artifact_id then
    raise sqlstate 'PT422' using message='Explicit packet review required'; end if;
  perform 1 from public.jobs where id=p_job_id and user_id=u for update;
  if not found then raise sqlstate 'PT404' using message='Job not found'; end if;
  state:=public.mobile_packet_readiness(p_job_id);
  if state->>'reason' in ('eligibility_required','pending_questions','too_many_records') then
    raise sqlstate 'PT409' using message='Resolve readiness requirements'; end if;
  select value into packet from jsonb_array_elements(state->'packets') where value->>'run_id'=p_run_id::text
    and value->>'packet_fingerprint'=p_packet_fingerprint and value->'current'='true'::jsonb
    and value->'artifacts'->0->>'id'=p_resume_artifact_id::text and value->'artifacts'->1->>'id'=p_letter_artifact_id::text;
  if packet is null then raise sqlstate 'PT409' using message='The selected packet changed or is incomplete. Refresh and review'; end if;
  select * into app from public.applications where user_id=u and job_id=p_job_id for update;
  if app.status in ('submitted','interviewing','rejected','withdrawn','closed') then
    raise sqlstate 'PT409' using message='External application progress is preserved'; end if;
  insert into public.mobile_packet_reviews(user_id,job_id,run_id,resume_artifact_id,letter_artifact_id,packet_fingerprint)
    values(u,p_job_id,p_run_id,p_resume_artifact_id,p_letter_artifact_id,p_packet_fingerprint)
    on conflict(user_id,job_id) do update set run_id=excluded.run_id,resume_artifact_id=excluded.resume_artifact_id,
      letter_artifact_id=excluded.letter_artifact_id,packet_fingerprint=excluded.packet_fingerprint,reviewed_at=clock_timestamp();
  insert into public.applications(user_id,job_id,status) values(u,p_job_id,'ready')
    on conflict(user_id,job_id) do update set status='ready'
    returning * into app;
  return jsonb_build_object('user_id',u,'application',to_jsonb(app),'readiness',public.mobile_packet_readiness(p_job_id));
end; $$;

revoke all on function public._mobile_packet(uuid,text),public._mobile_ascii_json(text),public._mobile_eligibility(uuid) from public,anon,authenticated,service_role;
revoke all on function public.mobile_packet_context(uuid,uuid),public.mobile_bind_packet_context(uuid,uuid,text,text,text,text),
  public.mobile_packet_readiness(uuid),public.mobile_review_packet(uuid,uuid,uuid,uuid,text,boolean) from public,anon,authenticated,service_role;
grant execute on function public.mobile_packet_context(uuid,uuid),public.mobile_bind_packet_context(uuid,uuid,text,text,text,text),
  public.mobile_packet_readiness(uuid),public.mobile_review_packet(uuid,uuid,uuid,uuid,text,boolean) to authenticated;

-- Extend the existing leased export; ownership/lease checks remain in 0010.
-- Profile erasure already cascades through both new tables. Never omit the new
-- personal review history from account exports or expose it via a public RPC.
alter function public.mobile_privacy_snapshot(uuid,uuid) rename to _mobile_privacy_snapshot_0010;
revoke all on function public._mobile_privacy_snapshot_0010(uuid,uuid) from public,anon,authenticated,service_role;
create function public.mobile_privacy_snapshot(p_request_id uuid,p_lease_token uuid) returns jsonb
language plpgsql security definer set search_path='' as $$
declare result jsonb; u uuid; rows jsonb; name text;
begin
  result:=public._mobile_privacy_snapshot_0010(p_request_id,p_lease_token);
  u:=(result->>'user_id')::uuid;
  foreach name in array array['mobile_packet_contexts','mobile_packet_reviews'] loop
    execute format('select coalesce(jsonb_agg(to_jsonb(q)),''[]'') from (select * from public.%I where user_id=$1 limit 5001) q',name)
      into rows using u;
    if jsonb_array_length(rows)>5000 then raise exception 'Privacy export row limit'; end if;
    result:=jsonb_set(result,array['tables',name],rows,true);
    if octet_length((result->'tables')::text)>16777216 then raise exception 'Privacy export byte limit'; end if;
  end loop;
  return result;
end; $$;
revoke all on function public.mobile_privacy_snapshot(uuid,uuid) from public,anon,authenticated,service_role;
grant execute on function public.mobile_privacy_snapshot(uuid,uuid) to service_role;
notify pgrst,'reload schema';
commit;
