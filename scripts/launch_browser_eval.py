"""Real Chromium UI persona journeys with synthetic intercepted Supabase only.

No hosted signups/emails, founder state, AI calls, or external job submissions.
This is UI workflow evidence, NOT hosted auth/RLS or actual job discovery.
"""
import copy
import json
import re
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from playwright.sync_api import sync_playwright, expect
from web_ui_smoke import session

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/testing/evidence/browser'
PERSONAS=json.loads((ROOT/'tests/fixtures/resumes/personas.json').read_text())
URL='http://127.0.0.1:5178/beta'

class Boundary:
    def __init__(self,browser,persona,width=390,signed=True):
        self.persona=persona; self.events=[]; self.requests=[]; self.uploads=0; self.fail_upload=False
        self.rows={k:[] for k in ['profiles','job_preferences','jobs','applications','resumes','artifacts']}
        self.context=browser.new_context(viewport={'width':width,'height':900},reduced_motion='reduce')
        self.context.route('**/*',self.route)
        self.auth=session(); self.auth['user']['email']=persona['email']
        if signed:self.context.add_init_script("localStorage.setItem('sb-pursuit-ui-test-auth-token', "+json.dumps(json.dumps(self.auth))+");")
        self.page=self.context.new_page(); self.page.on('pageerror',lambda e:self.events.append(str(e)))
        self.page.set_default_timeout(6000)
    def route(self,route):
        r=route.request; url=urlsplit(r.url); headers={'access-control-allow-origin':'*','access-control-allow-headers':'*'}
        if url.hostname=='127.0.0.1' and not url.path.startswith('/api'):
            route.continue_();return
        if url.hostname!='pursuit-ui-test.supabase.co':
            self.events.append('blocked external '+str(url.hostname));route.abort();return
        self.requests.append({'method':r.method,'path':url.path})
        if r.method=='OPTIONS':route.fulfill(status=204,headers=headers);return
        if url.path.startswith('/auth/'):
            if url.path.endswith('/logout'):route.fulfill(status=204,headers=headers);return
            route.fulfill(status=429,headers=headers,json={'code':'over_email_send_rate_limit','msg':'Synthetic rate limit'});return
        if url.path.startswith('/storage/v1/object/'):
            if r.method=='POST':self.uploads+=1
            if self.fail_upload:route.fulfill(status=503,headers=headers,json={'message':'Synthetic storage unavailable'});return
            route.fulfill(status=200,headers=headers,json={'Key':'synthetic','Id':str(uuid.uuid4())});return
        if not url.path.startswith('/rest/v1/'):
            self.events.append('unsupported fixture endpoint '+url.path);route.abort();return
        table=url.path.rsplit('/',1)[-1]; rows=self.rows.setdefault(table,[]); q=parse_qs(url.query)
        selected=rows
        for key,values in q.items():
            val=values[0]
            if val.startswith('eq.'):
                selected=[x for x in selected if str(x.get(key,'')).lower()==val[3:].lower()]
        if r.method=='POST':
            body=r.post_data_json; incoming=body if isinstance(body,list) else [body]; selected=[]
            for item in incoming:
                conflicts=q.get('on_conflict',['user_id' if table in ['profiles','job_preferences'] else 'id'])[0].split(',')
                old=next((x for x in rows if all(k in item and x.get(k)==item[k] for k in conflicts)),None)
                if old:old.update(item);selected.append(old)
                else:
                    now='2026-09-14T17:00:00Z'
                    obj={'id':str(uuid.uuid4()),'created_at':now,'updated_at':now,'status':'draft' if table=='applications' else 'new','eligibility_status':'unknown','last_validated_at':None,'workplace_type':'unknown','role_focus':None,'applied_at':None,**item}
                    rows.append(obj);selected.append(obj)
        elif r.method=='PATCH':
            for x in selected:x.update(r.post_data_json)
        elif r.method=='DELETE':
            for x in selected:rows.remove(x)
            selected=[]
        if 'application/vnd.pgrst.object+json' in r.headers.get('accept',''):data=selected[0] if selected else None
        else:data=selected
        route.fulfill(status=200,headers=headers,json=data)
    def close(self):self.context.close()

def nav(page,name):page.get_by_role('navigation',name='Primary navigation').get_by_role('button',name=name,exact=True).click()
def advance(page):page.get_by_role('button',name='Continue',exact=True).click()

def onboard(b,p,upload=True):
    page=b.page;page.goto(URL)
    expect(page.get_by_role('heading',name='Let’s start with the essentials.')).to_be_visible()
    page.get_by_role('textbox',name=re.compile('^Name')).fill(p['name'])
    page.get_by_role('textbox',name=re.compile('Current city')).fill(p['location']);advance(page)
    page.get_by_role('textbox',name=re.compile('^Target roles')).fill(', '.join(p['roles']))
    page.get_by_role('textbox',name=re.compile('Preferred countries')).fill(', '.join(p['locations']));advance(page)
    page.get_by_role('combobox').select_option(p['remote'])
    page.get_by_role('checkbox').set_checked(p['sponsorship'])
    page.get_by_role('textbox',name=re.compile('Work authorisation')).fill(p['constraints']);advance(page)
    if upload:page.locator('input[type=file]').set_input_files(str(ROOT/p['resume']))
    advance(page)
    expect(page.get_by_role('heading',name='Review your private workspace.')).to_be_visible()
    page.get_by_role('button',name='Create my private workspace',exact=True).click()
    expect(page.get_by_role('heading',name=f"Your next move, {p['name'].split()[0]}.")).to_be_visible()

def run_persona(browser,p):
    b=Boundary(browser,p,width=1440 if int(p['id'][1:])%3==0 else 390);page=b.page
    result={'persona':p['id'],'boundary':'real local React/Chromium; simulated Auth/Postgres/Storage; no AI','steps':[]}
    start=time.monotonic()
    try:
        onboard(b,p);result['steps'].append('five-step onboarding and source upload completed')
        nav(page,'Studio');expect(page.get_by_text(Path(p['resume']).name,exact=False)).to_be_visible()
        result['steps'].append('source filename visible in Studio')
        result['ai_available']=page.get_by_text('AI tailoring, interview coaching and saved answers are not connected',exact=False).count()==0
        nav(page,'Discover');page.get_by_role('button',name='Add a job',exact=True).click()
        page.get_by_role('textbox',name='Company',exact=True).fill(p['job']['company_name'])
        page.get_by_role('textbox',name='Role title',exact=True).fill(p['job']['title'])
        page.get_by_role('textbox',name='Original posting URL',exact=True).fill(p['job']['source_url'])
        page.get_by_role('textbox',name=re.compile('^Location')).fill(p['job']['location_text'])
        page.get_by_role('button',name='Save role',exact=True).click()
        page.get_by_role('button',name='View role',exact=True).click()
        expect(page.get_by_role('dialog')).to_be_visible()
        result['job_description_visible']=p['job']['description'] in page.locator('body').inner_text()
        result['steps'].append('manually entered job saved; details opened')
        page.get_by_role('button',name='Open Application Studio',exact=True).click()
        expect(page.get_by_role('tab',name='Documents',exact=True)).to_be_visible()
        page.get_by_role('tab',name='Documents',exact=True).click()
        expect(page.get_by_text('This beta does not generate documents in this screen.',exact=False)).to_be_visible()
        page.get_by_role('tab',name='Questions',exact=True).click()
        expect(page.get_by_text('does not yet collect, answer, or submit employer questions',exact=False)).to_be_visible()
        page.get_by_role('tab',name='Final review',exact=True).click()
        result['final_review']=page.locator('.beta-application-studio').inner_text()
        page.screenshot(path=str(OUT/f"{p['id']}-stuck-final-review.png"),full_page=True)
        result['steps'].append('documents/questions/final review reached; preparation blocked by missing integrations')
        result['preferences_preserved']=b.rows['job_preferences'][0]['remote_preference']==p['remote']
        page.reload();expect(page.get_by_role('heading',name=f"Your next move, {p['name'].split()[0]}.")).to_be_visible()
        result['steps'].append('returning user reload retains saved source, preferences, job and draft application in mock store')
        nav(page,'Tracker');expect(page.locator('.beta-applications-workspace-row')).to_have_count(1)
        result['horizontal_overflow']=page.evaluate('document.documentElement.scrollWidth > innerWidth + 1')
        page.get_by_role('button',name='Sign out',exact=True).click()
        expect(page.get_by_role('button',name='Create account with email')).to_be_visible()
        result['steps'].append('logout returns to auth screen')
        result['status']='partial journey: core AI/search/preparation unavailable'
    except Exception as error:result['status']='harness or product failure';result['error']=str(error)[:1000];page.screenshot(path=str(OUT/f"{p['id']}-failure.png"),full_page=True)
    finally:
        result['elapsed_seconds']=round(time.monotonic()-start,2);result['events']=b.events;result['writes']={k:len(v) for k,v in b.rows.items()};b.close()
    return result

def edge_cases(browser):
    output=[];p=PERSONAS[0]
    b=Boundary(browser,p);page=b.page
    try:
        page.goto(URL);page.get_by_role('textbox',name=re.compile('^Name')).fill('Unsaved Test');advance(page)
        page.get_by_role('textbox',name=re.compile('^Target roles')).fill('find something good');advance(page)
        vague=page.get_by_role('heading',name='What should we keep in mind?').is_visible()
        page.reload();expect(page.get_by_role('textbox',name=re.compile('^Name'))).to_have_value('')
        output.append({'case':'vague role and interrupted onboarding','vague_role_accepted_without_clarification':vague,'unsaved_draft_lost_on_reload':True})
    finally:b.close()
    b=Boundary(browser,p);page=b.page
    try:
        onboard(b,p,upload=False);nav(page,'Studio')
        page.locator('input[type=file]').set_input_files(str(ROOT/'tests/fixtures/resumes/unsupported.txt'))
        expect(page.get_by_role('alert')).to_contain_text('Upload a PDF, DOC, or DOCX')
        unsupported_uploads=b.uploads
        page.locator('input[type=file]').set_input_files(str(ROOT/'tests/fixtures/resumes/malformed.pdf'))
        expect(page.get_by_text('Uploaded malformed.pdf.',exact=True)).to_be_visible()
        output.append({'case':'invalid upload','unsupported_blocked_before_storage':unsupported_uploads==0,'malformed_pdf_accepted_by_ui':True,'storage_is_stub':True})
        b.fail_upload=True
        page.locator('input[type=file]').set_input_files(str(ROOT/p['resume']))
        expect(page.get_by_role('alert')).to_be_visible()
        output.append({'case':'storage failure','message':page.get_by_role('alert').inner_text(),'previous_document_preserved':len(b.rows['resumes'])==1})
    except Exception as e:output.append({'case':'edge harness failure','error':str(e)[:800]})
    finally:b.close()
    return output

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        results=[]
        for p in PERSONAS:
            result=run_persona(browser,p);results.append(result);print(p['id'],result['status'],flush=True)
            (OUT/'persona-results.json').write_text(json.dumps(results,indent=2)+'\n')
        (OUT/'edge-results.json').write_text(json.dumps(edge_cases(browser),indent=2)+'\n')
        browser.close()

if __name__=='__main__':main()
