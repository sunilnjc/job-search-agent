"""Read-only consistency checks for the synthetic prelaunch audit package."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT/'docs/testing'
required = ['JOB_PURSUIT_CURRENT_FLOW', 'JOB_PURSUIT_TEST_REPORT', 'LAUNCH_READINESS', 'PERSONA_RESULTS', 'CONFUSION_LOG', 'BUG_BACKLOG', 'PRODUCT_IMPROVEMENTS', 'TRUST_RISKS', 'REGRESSION_MATRIX']
errors = []; links = 0
for name in required:
    if not (DOCS/(name+'.md')).is_file(): errors.append('missing '+name)
for path in DOCS.rglob('*.md'):
    text = path.read_text()
    if sum(line.startswith('```') for line in text.splitlines()) % 2:
        errors.append('unbalanced fence: '+path.name)
    for match in re.finditer(r'\]\((</[^>]+>|/[^)]+)\)', text):
        target = match.group(1).strip('<>'); target = re.sub(r':\d+$', '', target)
        links += 1
        if not Path(target).exists(): errors.append('missing local link: '+target)
    if re.search(r'(?:sk-(?:proj-|ant-)[A-Za-z0-9_-]{16,}|sb_secret_[A-Za-z0-9_-]{12,}|eyJ[A-Za-z0-9_-]{30,}\.[A-Za-z0-9_-]{30,}\.)', text):
        errors.append('credential-like literal in '+path.name)
backlog = (DOCS/'BUG_BACKLOG.md').read_text()
ids = re.findall(r'^### (JP-TEST-\d+)', backlog, re.M)
if len(ids) != 37 or len(set(ids)) != 37: errors.append('backlog count/uniqueness')
personas = json.loads((ROOT/'tests/fixtures/resumes/personas.json').read_text())
browser = json.loads((DOCS/'evidence/browser/persona-results.json').read_text())
pipeline = json.loads((DOCS/'evidence/pipeline/results.json').read_text())
if not (len(personas) == len(browser) == len(pipeline) == 14): errors.append('persona counts')
for p in personas:
    if not (ROOT/p['resume']).is_file(): errors.append('missing resume '+p['id'])
calls = [c for r in pipeline for c in r.get('model_calls', [])]
artifacts = [a for r in pipeline for a in r.get('prepare', {}).get('artifacts', [])]
for artifact in artifacts:
    if not (ROOT/artifact['path']).is_file(): errors.append('missing output '+artifact['path'])
scores = [int(x) for x in re.findall(r'^\| [^|]+ \| (\d+) \|', (DOCS/'LAUNCH_READINESS.md').read_text(), re.M)]
if len(scores) != 20 or sum(scores) != 77: errors.append('scorecard arithmetic')
summary = {'required_reports': len(required), 'local_links_checked': links, 'backlog_entries': len(ids), 'personas': len(personas), 'real_model_calls': len(calls), 'input_tokens': sum(c['metadata'].get('input_tokens', 0) for c in calls), 'output_tokens': sum(c['metadata'].get('output_tokens', 0) for c in calls), 'collected_output_files': len(artifacts), 'score_total_200': sum(scores), 'errors': errors}
print(json.dumps(summary, indent=2))
raise SystemExit(bool(errors))
