"""Fixture source for public mdocr planning and scripted run outcomes."""

PROLOGUE = r"""
import hashlib, json, subprocess, sys
from pathlib import Path

args = sys.argv[1:]
def _argument(name):
    return Path(args[args.index(name) + 1])

def _run_plan():
    raise SystemExit(subprocess.call([sys.executable, '-m', 'mdocr.cli', *args]))

if args[0] == 'plan':
    _run_plan()
fixture_plan_path = _argument('--plan')
fixture_plan = json.loads(fixture_plan_path.read_bytes())
fixture_request = fixture_plan_path.with_name(fixture_plan_path.name.replace('-plan.json', '-request.json'))
fixture_doc = fixture_plan['documents'][0]
fixture_output = Path(fixture_doc['output'])

def fixture_result(payload):
    payload['plan_sha256'] = hashlib.sha256(fixture_plan_path.read_bytes()).hexdigest()
    row = payload['documents'][0]
    row['document_id'] = fixture_doc['document_id']
    payload.setdefault('status', row['status'])
    if row['status'] == 'completed':
        row['output_sha256'] = hashlib.sha256(fixture_output.read_bytes()).hexdigest()
        evidence = row.setdefault('ocr', {})
        evidence.update(contract='mdocr-ocr-2', configuration={'mode': 'paddle'})
        for n, image in enumerate(evidence.setdefault('images', [])):
            image['source_sha256'] = hashlib.sha256(Path(image['source_path']).read_bytes()).hexdigest()
            artifact = fixture_output.with_name(f'fixture-{n}.md')
            artifact.write_text('# Test evidence')
            image['artifact'] = str(artifact)
            image['artifact_sha256'] = hashlib.sha256(artifact.read_bytes()).hexdigest()
    _argument('--result').write_text(json.dumps(payload))
"""


def write_ocr_fixture(path, body):
    path.write_text(PROLOGUE + "\n" + body)
