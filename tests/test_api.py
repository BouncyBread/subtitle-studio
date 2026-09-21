import json
import queue
import uuid
import pytest
from fastapi.testclient import TestClient
from app import server
from app.worker import atomic_json

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, 'JOBS', tmp_path)
    staging=tmp_path/'uploads';staging.mkdir()
    monkeypatch.setattr(server, 'UPLOADS', staging)
    monkeypatch.setattr(server, 'work', queue.Queue())
    # Disable the supervisor: API tests must never download or run real models.
    monkeypatch.setattr(server, 'supervise', lambda: None)
    with TestClient(server.app, base_url='http://testserver') as c:
        yield c

@pytest.fixture
def completed(client):
    folder = server.JOBS / str(uuid.uuid4())
    folder.mkdir()
    atomic_json(folder / 'job.json', {'id':folder.name,'name':'Movie 日本語.mkv','source':'/private/file','created':'2026-01-01','settings':{'translate':True,'width':42}})
    atomic_json(folder / 'progress.json', {'stage':'done','message':'Ready'})
    atomic_json(folder / 'result.json', {'segments':[{'start':0,'end':1,'text':'Hello'}]})
    return folder.name


def test_export_and_edit(client, completed):
    url=f'/api/jobs/{completed}'
    assert 'source' not in client.get(url).json()
    assert '00:00:00,000 --> 00:00:01,000' in client.get(url+'/download/srt').text
    assert '.en.srt' in client.get(url+'/download/srt').headers['content-disposition']
    assert client.get(url+'/download/vtt').text.startswith('WEBVTT')
    body={'segments':[{'start':.1,'end':2,'text':'Edited subtitle'}]}
    assert client.put(url+'/subtitles',json=body).status_code==200
    assert 'Edited subtitle' in client.get(url+'/download/txt').text
    body['segments'][0]['end']=.01
    assert client.put(url+'/subtitles',json=body).status_code==422
    assert 'Edited subtitle' in client.get(url+'/download/txt').text


def test_security_and_bad_input(client):
    assert client.post('/api/pick',headers={'Origin':'https://evil.example'}).status_code==403
    assert client.get('/api/status',headers={'Host':'evil.example'}).status_code==400
    assert client.get('/api/jobs/not-an-id').status_code==404
    assert client.post('/api/jobs/path',json={'path':'/not/real'}).status_code==422
    assert client.post('/api/jobs/path',json={'path':'/not/real','settings':{'model':'fake'}}).status_code==422
    assert client.post('/api/jobs/upload',data={'settings':'bad'},files={'file':('a.mp4',b'test')}).status_code==422


def test_turbo_translation_switches_model():
    s=server.Settings(model='turbo',translate=True)
    server.validate_settings(s)
    assert s.model=='large'


def test_upload_and_cancel_removes_only_temporary_source(client):
    response=client.post('/api/jobs/upload',data={'settings':json.dumps({'model':'small','translate':False})},files={'file':('../../movie.mp4',b'test media')})
    assert response.status_code==200
    job=response.json()
    assert job['name']=='movie.mp4'
    folder=server.JOBS/job['id']
    assert (folder/'source.media').read_bytes()==b'test media'
    assert client.post(f"/api/jobs/{job['id']}/cancel").json()['stage']=='cancelled'
    assert not (folder/'source.media').exists()
    assert client.get(f"/api/jobs/{job['id']}/download/srt").status_code==409


def test_path_cancel_preserves_original(client, tmp_path):
    source=tmp_path/'original.mp4'
    source.write_bytes(b'original')
    response=client.post('/api/jobs/path',json={'path':str(source),'settings':{'model':'turbo','translate':True}})
    assert response.status_code==200
    job=response.json()
    assert job['settings']['model']=='large'
    assert client.post(f"/api/jobs/{job['id']}/cancel").status_code==200
    assert source.read_bytes()==b'original'


def test_live_progress_and_log_endpoint(client, completed):
    folder=server.JOBS/completed
    (folder/'worker.log').write_text('50%|██| 3000/6000 [00:10<00:10, 300frames/s]\r')
    atomic_json(folder/'progress.json',{'stage':'transcribing','message':'Working'})
    job=client.get(f'/api/jobs/{completed}').json()
    assert job['metrics']['percent']==50
    assert job['metrics']['eta_seconds']==10
    log=client.get(f'/api/jobs/{completed}/log').json()
    assert len(log['lines'])==1
    assert '3000/6000' in log['lines'][0]
    assert client.get('/api/jobs/not-a-job/log').status_code==404


def test_streaming_upload_retained_and_player_status(client, monkeypatch):
    monkeypatch.setattr(server.player, 'executable', lambda: '/mock/mpv')
    response=client.post('/api/jobs/upload',data={'settings':json.dumps({'streaming':True})},files={'file':('watch.mkv',b'movie')})
    assert response.status_code==200
    job=response.json()
    folder=server.JOBS/job['id']
    atomic_json(folder/'stream.json',{'version':1,'covered_until':30,'duration':120,'complete':False})
    response=client.get(f"/api/jobs/{job['id']}")
    assert response.json()['stream']['covered_until']==30
    assert response.json()['player']['running'] is False
    client.post(f"/api/jobs/{job['id']}/cancel")
    assert (folder/'source.media').read_bytes()==b'movie'
    assert client.post(f"/api/jobs/{job['id']}/player").status_code==409


def test_streaming_requires_player(client, monkeypatch, tmp_path):
    monkeypatch.setattr(server.player,'executable',lambda:None)
    source=tmp_path/'source.mkv';source.write_bytes(b'movie')
    assert client.post('/api/jobs/path',json={'path':str(source),'settings':{'streaming':True}}).status_code==503
    assert client.post('/api/jobs/path',json={'path':str(source),'settings':{'streaming':True,'buffer_seconds':1}}).status_code==422


def test_cleanup_api_keeps_exports(client, completed):
    folder=server.JOBS/completed
    job=json.loads((folder/'job.json').read_text())
    job.update(uploaded=True,source=str(folder/'source.media'))
    atomic_json(folder/'job.json',job)
    (folder/'source.media').write_bytes(b'temporary copy')
    assert client.get('/api/cleanup').json()['bytes']==14
    assert (folder/'source.media').exists()
    result=client.post('/api/cleanup').json()
    assert result['files']==1 and result['bytes']==14
    assert not (folder/'source.media').exists()
    assert client.get(f'/api/jobs/{completed}').json()['media_available'] is False
    assert 'Hello' in client.get(f'/api/jobs/{completed}/download/srt').text
    assert client.post('/api/cleanup',headers={'Origin':'https://evil.example'}).status_code==403


def test_probe_and_prepared_upload_reuse_single_copy(client, monkeypatch, tmp_path):
    tracks=[{'index':0,'language_name':'French'},{'index':1,'language_name':'Japanese'}]
    monkeypatch.setattr(server,'audio_tracks',lambda p:tracks)
    source=tmp_path/'movie.mkv';source.write_bytes(b'original movie')
    assert client.post('/api/media/probe',json={'path':str(source)}).json()['tracks']==tracks
    response=client.post('/api/media/upload',files={'file':('movie.mkv',b'media copy')})
    assert response.status_code==200
    upload_id=response.json()['upload_id']
    staged=server.UPLOADS/upload_id
    assert (staged/'source.media').read_bytes()==b'media copy'
    body={'upload_id':upload_id,'settings':{'track':2}}
    assert client.post('/api/jobs/prepared',json=body).status_code==422
    assert (staged/'source.media').exists()
    body['settings']['track']=1
    response=client.post('/api/jobs/prepared',json=body)
    assert response.status_code==200
    job=response.json()
    assert job['settings']['track']==1
    assert (server.JOBS/job['id']/'source.media').read_bytes()==b'media copy'
    assert not staged.exists()
    assert client.post('/api/jobs/prepared',json=body).status_code==404
    assert source.read_bytes()==b'original movie'


def test_cleanup_prepared_upload_skips_inflight_copy(client, monkeypatch):
    monkeypatch.setattr(server,'audio_tracks',lambda p:[{'index':0}])
    response=client.post('/api/media/upload',files={'file':('movie.mkv',b'prepared copy')})
    upload_id=response.json()['upload_id']
    inflight=server.UPLOADS/'inflight';inflight.mkdir();(inflight/'source.media').write_bytes(b'still copying')
    assert client.get('/api/cleanup').json()['bytes']==13
    assert client.post('/api/cleanup').json()['files']==1
    assert not (server.UPLOADS/upload_id).exists()
    assert (inflight/'source.media').read_bytes()==b'still copying'
    assert client.post('/api/jobs/prepared',json={'upload_id':upload_id}).status_code==404


def test_failed_probe_removes_prepared_copy(client,monkeypatch):
    def fail(path):raise ValueError('No readable audio')
    monkeypatch.setattr(server,'audio_tracks',fail)
    assert client.post('/api/media/upload',files={'file':('bad.mkv',b'bad')}).status_code==422
    assert list(server.UPLOADS.iterdir())==[]


def test_remote_job_lifecycle_and_privacy(client, monkeypatch):
    from app.remote import RangeCache, hub
    folder = server.UPLOADS / str(uuid.uuid4()); folder.mkdir()
    (folder/'source.media').write_bytes(b'cached block')
    atomic_json(folder/'upload.json',{'name':'TorBox video'})
    cache = RangeCache('https://cdn.torbox.app/file?token=SECRET',folder)
    cache.tracks = [{'index':0}]; cache.duration = 120; cache.size = 1000
    hub.caches[folder.name] = cache
    monkeypatch.setattr(hub,'add',lambda c:'http://127.0.0.1:1234/'+c.folder.name)
    body = {'remote_id':folder.name,'settings':{'track':1}}
    assert client.post('/api/jobs/remote',json=body).status_code == 422
    body['settings']['track'] = 0
    response = client.post('/api/jobs/remote',json=body)
    assert response.status_code == 200
    job = response.json()
    assert job['remote'] and 'remote_source' not in job
    assert 'SECRET' not in response.text
    metadata = (server.JOBS/job['id']/'job.json').read_text()
    assert 'SECRET' not in metadata and 'torbox.app' not in metadata
    assert client.get('/api/jobs/'+job['id']).json()['media_available']
    assert client.post('/api/jobs/remote',json=body).status_code == 404
    assert client.post('/api/cleanup').json()['skipped_jobs'] == 1
    client.post('/api/jobs/'+job['id']+'/cancel')
    client.post('/api/cleanup')
    assert job['id'] not in hub.caches and cache.url == ''
    assert not (server.JOBS/job['id']/'source.media').exists()
