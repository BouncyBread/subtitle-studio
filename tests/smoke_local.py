"""Manual real-model smoke test against the running app. Downloads Large v3 once."""
import json
from pathlib import Path
import sys
import time
import httpx

root=Path(__file__).resolve().parents[1]
client=httpx.Client(base_url='http://127.0.0.1:8765',timeout=30)
source=root / 'data' / (sys.argv[1] if len(sys.argv)>1 else 'test-speech.aiff')
response=client.post('/api/jobs/path',json={'path':str(source),'settings':{'model':'large','language':'fr' if 'french' in source.name else 'en','translate':True}})
response.raise_for_status()
job_id=response.json()['id']
print('Job:',job_id,flush=True)
previous=None
for _ in range(1800):
    job=client.get(f'/api/jobs/{job_id}').json()
    if job['stage']!=previous:
        print(job['stage'],job['message'],flush=True)
        previous=job['stage']
    if job['stage']=='done':
        assert job['result']['segments'], job
        output=client.get(f'/api/jobs/{job_id}/download/srt')
        output.raise_for_status()
        print(output.text,flush=True)
        (root/'data'/'smoke-result.srt').write_text(output.text)
        break
    if job['stage'] in ('error','cancelled'):
        raise SystemExit(json.dumps(job,indent=2))
    time.sleep(2)
else:
    raise SystemExit('Timed out waiting for transcription')
