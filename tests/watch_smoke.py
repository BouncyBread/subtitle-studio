"""Manual end-to-end watch-mode test. Opens mpv with a generated local sample."""
import json
from pathlib import Path
import socket
import time
import httpx

ROOT=Path(__file__).resolve().parents[1]
client=httpx.Client(base_url='http://127.0.0.1:8765',timeout=30)
response=client.post('/api/jobs/path',json={'path':str(ROOT/'data/Live-player-demo.mp4'),'settings':{'streaming':True,'buffer_seconds':30,'model':'large','language':'fr','translate':True}})
response.raise_for_status();job_id=response.json()['id']
folder=ROOT/'data/jobs'/job_id
print('Watch job:',job_id,flush=True)
client.post(f'/api/jobs/{job_id}/player').raise_for_status()
# Mute only this generated test clip, using the dedicated local player IPC API.
for _ in range(100):
    try:
        sockpath=(folder/'player-ipc.txt').read_text()
        ipc=socket.socket(socket.AF_UNIX);ipc.connect(sockpath)
        ipc.sendall(b'{"command":["set_property","volume",0]}\n');ipc.close();break
    except (FileNotFoundError,ConnectionRefusedError):time.sleep(.1)
started_early=False;prior=None
for _ in range(180):
    job=client.get(f'/api/jobs/{job_id}').json()
    stream=job.get('stream',{});player=job.get('player',{})
    state=(job['stage'],stream.get('version'),player.get('buffering'))
    if state!=prior:
        print(json.dumps({'stage':job['stage'],'ready_seconds':stream.get('covered_until'),'complete':stream.get('complete'),'player':player}),flush=True);prior=state
    if job['stage']=='transcribing' and stream.get('covered_until',0)>=30 and player.get('running') and player.get('buffering') is False:
        started_early=True
    if job['stage']=='done':
        assert started_early,'Playback did not start before transcription finished'
        assert stream['complete'] and job['result']['segments']
        assert (folder/'live.srt').read_text()
        print('PASS: real Whisper publishes partial subtitles and native playback starts before completion.',flush=True)
        break
    if job['stage']=='error':raise RuntimeError(job['message'])
    time.sleep(2)
else:raise RuntimeError('Timed out')
