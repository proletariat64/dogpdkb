"""Fixture source for public mdocr planning and scripted run outcomes."""

PROLOGUE = r"""
import hashlib, json, subprocess, sys
from pathlib import Path
from urllib.parse import quote

args = sys.argv[1:]
def _argument(name):
    return Path(args[args.index(name) + 1])

if args[0] == 'plan':
    raise SystemExit(subprocess.call([sys.executable, '-m', 'mdocr.cli', *args]))
fixture_plan_path = _argument('--plan')
fixture_plan = json.loads(fixture_plan_path.read_bytes())
fixture_request = fixture_plan_path.with_name('request.json')
fixture_doc = fixture_plan['documents'][0]
fixture_output = Path(fixture_doc['output'])

def _sha(data):
    return hashlib.sha256(data).hexdigest()

def _complete(row, doc):
    output = Path(doc['output'])
    body = output.read_bytes() if output.exists() else Path(doc['path']).read_bytes()
    resources, images = [], {}
    settings = fixture_plan['settings']
    config = {'mode': settings['mode']}
    if settings['mode'] == 'llm':
        config.update(model=settings['model'], effort=settings['effort'],
                      prompt_sha256=_sha(settings['prompt'].encode()), schema='mdocr-image-patches-1')
    for image in doc['images']:
        key = image['resource_id']
        status = 'success' if image['selected'] else 'unselected'
        resources.append(dict(id=image['id'], resource_id=key, status=status))
        if status != 'success':
            continue
        artifact = output.with_name(f"{image['id']}.md")
        artifact.parent.mkdir(parents=True, exist_ok=True)
        link = quote(image['original_link'], safe='/:')
        block = (
            f"<!-- mdocr:ocr-begin v=2 id={image['id']} -->\n"
            f"<!-- mdocr:meta mode={config['mode']} sha256={image['sha256']} -->\n"
            f"> Original image: [source](<{link}>)\n\nFixture OCR.\n<!-- mdocr:ocr-end -->"
        ).encode()
        body += b'\n\n' + block + b'\n'
        if key not in images:
            artifact.write_bytes(block)
            images[key] = dict(resource_id=key, status='success', source_path=image['source_path'],
                               source_sha256=image['sha256'], artifact=str(artifact), artifact_sha256=_sha(block))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(body)
    evidence = dict(contract='mdocr-ocr-2', configuration=config, images=list(images.values()))
    if images and config['mode'] == 'llm':
        context = [doc['sha256'], [[im['id'], im['sha256']] for im in doc['images'] if im['selected']]]
        evidence['context_sha256'] = _sha((json.dumps(context, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode())
    row.update(output=str(output), output_sha256=_sha(body), resources=resources, ocr=evidence)

def fixture_result(payload):
    payload['plan_sha256'] = _sha(fixture_plan_path.read_bytes())
    payload['result_path'] = str(_argument('--result'))
    for position, doc in enumerate(fixture_plan['documents']):
        if position >= len(payload['documents']):
            payload['documents'].append({'status': 'completed'})
        row = payload['documents'][position]
        row.update(document_id=doc['document_id'], path=doc['path'])
        if row['status'] == 'completed':
            _complete(row, doc)
        elif not row.get('resources'):
            reason = next(iter(payload.get('causes', [])), 'FIXTURE_NOT_ATTEMPTED')
            row['resources'] = [dict(id=im['id'], resource_id=im['resource_id'],
                                     status='unattempted' if im['selected'] else 'unselected', reason=reason)
                                for im in doc['images']]
    payload.setdefault('status', 'completed' if all(row['status']=='completed' for row in payload['documents']) else 'partial')
    _argument('--result').write_text(json.dumps(payload))
"""


def write_ocr_fixture(path, body):
    path.write_text(PROLOGUE + "\n" + body)
