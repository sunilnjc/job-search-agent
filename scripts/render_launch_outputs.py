"""Render collected synthetic application outputs; never regenerate model content."""
import subprocess
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = '/Users/Sunil/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3'
RENDER = '/Users/Sunil/.codex/plugins/cache/openai-primary-runtime/documents/26.904.11930/skills/documents/render_docx.py'
PDFTOPPM = '/Users/Sunil/.cache/codex-runtimes/codex-primary-runtime/dependencies/bin/override/pdftoppm'
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--input-dir', type=Path, default=ROOT/'docs/testing/evidence/pipeline')
parser.add_argument('--output-dir', type=Path, default=Path('/tmp/job-pursuit-launch-output-renders'))
args = parser.parse_args()
OUT = args.output_dir
OUT.mkdir(exist_ok=True)
for path in sorted(args.input_dir.iterdir()):
    if path.suffix not in {'.pdf', '.docx'}:
        continue
    folder = OUT / (path.stem + '-' + path.suffix[1:])
    folder.mkdir(exist_ok=True)
    command = ([PY, RENDER, str(path), '--output_dir', str(folder), '--dpi', '85', '--emit_pdf']
               if path.suffix == '.docx' else [PDFTOPPM, '-scale-to', '1100', '-png', str(path), str(folder/'page')])
    result = subprocess.run(command, capture_output=True, timeout=90)
    print(path.name, result.returncode, len(list(folder.glob('*.png'))), flush=True)
    if result.returncode:
        raise SystemExit('Rendering failed; inspect local renderer output before delivery')
