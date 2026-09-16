"""Render all synthetic fixtures using bundled document/PDF tools only."""
import json
import subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
PY='/Users/Sunil/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3'
RENDER='/Users/Sunil/.codex/plugins/cache/openai-primary-runtime/documents/26.904.11930/skills/documents/render_docx.py'
PDFTOPPM='/Users/Sunil/.cache/codex-runtimes/codex-primary-runtime/dependencies/bin/override/pdftoppm'
OUT=Path('/tmp/job-pursuit-launch-fixture-renders');OUT.mkdir(exist_ok=True)
for p in json.loads((ROOT/'tests/fixtures/resumes/personas.json').read_text()):
    path=ROOT/p['resume']; folder=OUT/p['id'];folder.mkdir(exist_ok=True)
    command=[PY,RENDER,str(path),'--output_dir',str(folder),'--dpi','80','--emit_pdf'] if path.suffix=='.docx' else [PDFTOPPM,'-scale-to','1100','-png',str(path),str(folder/'page')]
    result=subprocess.run(command,capture_output=True,timeout=90)
    print(p['id'],result.returncode,len(list(folder.glob('*.png'))),flush=True)
