"""Offline statement-level comparison of collected synthetic model outputs."""
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
personas = {p['id']: p for p in json.loads((ROOT/'tests/fixtures/resumes/personas.json').read_text())}
runs = json.loads((ROOT/'docs/testing/evidence/pipeline/results.json').read_text())
headings = {'Professional Summary', 'Work Experience', 'Education', 'Skills', 'Cover Letter'}
boilerplate = {'Dear Hiring Team,', 'I would like to be considered for this opportunity. Relevant details from my experience follow.', 'Thank you for considering my application.', 'Sincerely,'}
output = []
for run in runs:
    p = personas[run['persona']]
    for artifact in run.get('prepare', {}).get('artifacts', []):
        if artifact['format'] != '.docx':
            continue  # PDF is a rendering duplicate, not another independent AI output.
        statements = []
        for line in artifact['text'].splitlines():
            if not line.strip() or line in headings:
                continue
            if line in boilerplate:
                statements.append({'text': line, 'classification': 'NON-CLAIM', 'support': 'fixed rhetorical template; not a candidate or company assertion'})
                continue
            support = next((f'facts[{n}]' for n, fact in enumerate(p['facts']) if line == fact), None)
            if support:
                classification = 'SUPPORTED'
            elif line in {p['name'], p['location'], p['phone']}:
                classification, support = 'SUPPORTED', 'fixture identity / confirmed profile'
            elif line == 'alex.synthetic@example.test':
                classification, support = 'SUPPORTED', 'verified in-memory QA Auth identity, deliberately shared across isolated API contexts; not a persona resume email or cross-tenant leak'
            elif line == 'Self-reported profession label (not proof of credentials): '+p['profession']:
                classification, support = 'SUPPORTED', 'profession provided in confirmed profile; internal label is inappropriate in an employer document'
            elif line == 'Self-reported career stage: '+p['career_stage']:
                classification, support = 'SUPPORTED', 'career stage supplied to profile, not inferred from resume; internal enum is inappropriate in an employer document'
            else:
                classification, support = 'UNSUPPORTED', 'manual investigation required; no exact source match'
            statements.append({'text': line, 'classification': classification, 'support': support})
        output.append({'persona': p['id'], 'kind': artifact['kind'], 'artifact': artifact['path'], 'classifications': dict(Counter(s['classification'] for s in statements)), 'statements': statements})
path = ROOT/'docs/testing/evidence/pipeline/claim-diff.json'
path.write_text(json.dumps(output, indent=2, ensure_ascii=False)+'\n')
print(json.dumps({'outputs': len(output), 'classifications': dict(Counter(s['classification'] for o in output for s in o['statements'])), 'model_calls': sum(len(r.get('model_calls', [])) for r in runs), 'input_tokens': sum(c['metadata'].get('input_tokens', 0) for r in runs for c in r.get('model_calls', [])), 'output_tokens': sum(c['metadata'].get('output_tokens', 0) for r in runs for c in r.get('model_calls', [])), 'persona_elapsed_seconds': {r['persona']: r['elapsed_seconds'] for r in runs}}, indent=2))
