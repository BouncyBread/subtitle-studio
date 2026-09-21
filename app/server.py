import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import queue
import shutil
import signal
import subprocess
import sys
import threading
import uuid

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware
from app.subtitles import render, validate_segments
from app.worker import atomic_json
from app.progress import snapshot, log_snapshot
from app import player
from app.remote import hub, RangeCache
from app.cleanup import collect
from app.media import audio_tracks

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get('SUBTITLE_DATA', ROOT / 'data'))
JOBS = DATA / 'jobs'
JOBS.mkdir(parents=True, exist_ok=True)
UPLOADS = DATA / 'uploads'
UPLOADS.mkdir(parents=True, exist_ok=True)
work = queue.Queue()
lock = threading.RLock()
active = {}
stop = threading.Event()

class Settings(BaseModel):
    streaming: bool = False
    buffer_seconds: int = Field(default=60, ge=30, le=180)
    model: str = 'large'
    language: str = ''
    translate: bool = True
    track: int = Field(default=0, ge=0, le=99)
    width: int = Field(default=42, ge=24, le=64)
    prompt: str = Field(default='', max_length=1500)

class PathJob(BaseModel):
    path: str
    settings: Settings = Field(default_factory=Settings)

class ProbeFile(BaseModel):
    path: str

class PreparedJob(BaseModel):
    upload_id: str
    settings: Settings = Field(default_factory=Settings)

class RemoteLink(BaseModel):
    url: str = Field(min_length=1, max_length=16000)

class RemoteJob(BaseModel):
    remote_id: str
    settings: Settings = Field(default_factory=Settings)

class Segment(BaseModel):
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)
    text: str = Field(min_length=1, max_length=5000)

class Edit(BaseModel):
    segments: list[Segment] = Field(max_length=100000)


def folder_for(job_id):
    try:
        if str(uuid.UUID(job_id)) != job_id:
            raise ValueError()
    except ValueError:
        raise HTTPException(404, 'Job not found')
    folder = JOBS / job_id
    if not (folder / 'job.json').is_file():
        raise HTTPException(404, 'Job not found')
    return folder


def view(folder):
    job = json.loads((folder / 'job.json').read_text())
    progress = folder / 'progress.json'
    job.update(json.loads(progress.read_text()) if progress.exists() else {'stage': 'queued', 'message': 'Waiting to start…'})
    job.pop('source', None)
    job.pop('remote_source', None)
    return job


def supervise():
    while not stop.is_set():
        try:
            folder = work.get(timeout=0.5)
        except queue.Empty:
            continue
        try:
            with lock:
                if stop.is_set() or view(folder)['stage'] == 'cancelled':
                    continue
                with (folder / 'worker.log').open('w') as log:
                    proc = subprocess.Popen([sys.executable, '-m', 'app.worker', str(folder)], cwd=ROOT, stdout=log, stderr=log, start_new_session=True)
                active[folder.name] = proc
            code = proc.wait()
            with lock:
                active.pop(folder.name, None)
                if view(folder)['stage'] not in ('done', 'error', 'cancelled'):
                    atomic_json(folder / 'progress.json', {'stage': 'error', 'message': f'The transcription process stopped (exit {code}). Try the smaller model, or check data/jobs/{folder.name}/worker.log.'})
        finally:
            work.task_done()


@asynccontextmanager
async def lifespan(app):
    stop.clear()
    for folder in JOBS.iterdir():
        if (folder / 'job.json').exists() and view(folder)['stage'] not in ('done', 'error', 'cancelled'):
            atomic_json(folder / 'progress.json', {'stage': 'error', 'message': 'Interrupted when the app closed. Add the file again to retry.'})
    thread = threading.Thread(target=supervise, daemon=True)
    thread.start()
    yield
    stop.set()
    player.shutdown()
    with lock:
        for proc in active.values():
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
    thread.join(timeout=5)
    hub.shutdown()

app = FastAPI(title='Subtitle Studio', lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', 'testserver'])

@app.middleware('http')
async def local_only(request: Request, call_next):
    # Prevent other websites from submitting local paths or triggering the picker.
    origin = request.headers.get('origin')
    if origin and origin != str(request.base_url).rstrip('/'):
        return JSONResponse({'detail': 'Only requests from this app are allowed.'}, status_code=403)
    if request.headers.get('sec-fetch-site') == 'cross-site':
        return JSONResponse({'detail': 'Cross-site requests are not allowed.'}, status_code=403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'"
    return response

@app.get('/api/status')
def status():
    return {'name': 'Subtitle Studio', 'ffmpeg': bool(shutil.which('ffmpeg') and shutil.which('ffprobe')), 'engine': 'MLX · Apple Silicon', 'storage': str(JOBS), 'player_available': bool(player.executable())}

@app.get('/api/jobs')
def jobs():
    return sorted([view(p) for p in JOBS.iterdir() if (p / 'job.json').exists()], key=lambda j: j['created'], reverse=True)

def cleanup_media(remove=False):
    # Serialize against worker launches/cancellation and player opens.
    with lock, player.player_lock:
        def busy(folder):
            proc = active.get(folder.name)
            return (proc is not None and proc.poll() is None) or player.status(folder)['running']
        if remove:
            for key, cache in list(hub.caches.items()):
                if cache.started and (cache.folder / 'job.json').exists():
                    if view(cache.folder)['stage'] in ('done', 'error', 'cancelled') and not busy(cache.folder):
                        hub.discard(key)
                elif (cache.folder / 'upload.json').exists():
                    hub.discard(key)
        result = collect(JOBS, busy, remove=remove)
        for folder in UPLOADS.iterdir():
            if folder.is_symlink() or not (folder / 'upload.json').is_file():
                continue
            path = folder / 'source.media'
            if not path.is_symlink() and path.is_file():
                try:
                    stat = path.stat()
                    metadata = json.loads((folder / 'upload.json').read_text())
                    size = stat.st_blocks * 512 if metadata.get('remote') else stat.st_size
                    if remove:
                        path.unlink()
                        (folder / 'upload.json').unlink()
                        folder.rmdir()
                    result['bytes'] += size
                    result['files'] += 1
                except OSError:
                    result['errors'] += 1
        return result

@app.get('/api/cleanup')
def cleanup_preview():
    return cleanup_media()

@app.post('/api/cleanup')
def cleanup_copies():
    return cleanup_media(remove=True)

@app.post('/api/pick')
def pick():
    result = subprocess.run(['osascript', '-e', 'POSIX path of (choose file with prompt "Choose a video or audio file for subtitles")'], capture_output=True, text=True)
    if result.returncode:
        if '-128' in result.stderr:
            return {'path': None}
        raise HTTPException(500, 'Could not open the Mac file picker. Drag your file into the app instead.')
    return {'path': result.stdout.strip()}


def validate_settings(settings):
    if settings.streaming and not player.executable():
        raise HTTPException(503, 'Watch mode needs mpv. Install it with: brew install mpv')
    if settings.model not in ('large', 'turbo', 'small', 'tiny'):
        raise HTTPException(422, 'Choose a supported Whisper model.')
    if settings.language:
        # Tokenizer import does not load model weights.
        from mlx_whisper.tokenizer import LANGUAGES
        if settings.language not in LANGUAGES:
            raise HTTPException(422, 'Choose a supported spoken language.')
    if settings.translate and settings.model == 'turbo':
        settings.model = 'large'


def create_job(path, settings, uploaded=False, folder=None, name=None):
    validate_settings(settings)
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        raise HTTPException(503, 'FFmpeg is missing. Install it with: brew install ffmpeg')
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise HTTPException(422, 'That file could not be found. Choose it again.')
    folder = folder or JOBS / str(uuid.uuid4())
    folder.mkdir(exist_ok=True)
    job = {'id': folder.name, 'name': name or path.name, 'source': str(path), 'uploaded': uploaded, 'settings': settings.model_dump(), 'created': datetime.now(timezone.utc).isoformat()}
    atomic_json(folder / 'job.json', job)
    work.put(folder)
    return view(folder)

@app.post('/api/media/remote')
def prepare_remote(body: RemoteLink):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        raise HTTPException(503, 'FFmpeg is missing. Install it with: brew install ffmpeg')
    folder = UPLOADS / str(uuid.uuid4())
    folder.mkdir()
    folder.chmod(0o700)
    cache = RangeCache(body.url.strip(), folder)
    try:
        cache.block(0)
        source = hub.add(cache)
        cache.tracks = audio_tracks(source)
        probe = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                                '-of', 'json', source], capture_output=True, text=True, timeout=30)
        cache.duration = float(json.loads(probe.stdout)['format']['duration'])
        import math
        if not math.isfinite(cache.duration) or cache.duration <= 0:
            raise ValueError('The video must have a known, finite duration.')
        metadata = {'name': 'TorBox video', 'tracks': cache.tracks, 'remote': True}
        atomic_json(folder / 'upload.json', metadata)
        return dict(metadata, remote_id=folder.name, duration=cache.duration, size=cache.size)
    except Exception as exc:
        hub.discard(folder.name)
        cache.close()
        shutil.rmtree(folder, ignore_errors=True)
        message = str(exc) if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError) else 'Could not read this video. Copy a fresh direct TorBox file link.'
        raise HTTPException(422, message) from None

@app.post('/api/jobs/remote')
def remote_job(body: RemoteJob):
    validate_settings(body.settings)
    with lock:
        cache = hub.caches.get(body.remote_id)
        if not cache or cache.started or not (cache.folder / 'upload.json').exists():
            raise HTTPException(404, 'Paste the TorBox link again to prepare this video.')
        if body.settings.track >= len(cache.tracks):
            raise HTTPException(422, 'Choose an available audio track.')
        with cache.lock:
            folder = JOBS / body.remote_id
            cache.folder.rename(folder)
            cache.folder = folder
            (folder / 'upload.json').unlink()
            cache.started = True
            job = {'id': folder.name, 'name': 'TorBox video', 'source': str(folder / 'source.media'),
                   'remote_source': hub.add(cache), 'remote': True, 'duration': cache.duration,
                   'uploaded': True, 'settings': body.settings.model_dump(),
                   'created': datetime.now(timezone.utc).isoformat()}
            atomic_json(folder / 'job.json', job)
        work.put(folder)
        return view(folder)

@app.post('/api/jobs/path')
def path_job(body: PathJob):
    return create_job(body.path, body.settings)

@app.post('/api/media/probe')
def probe_file(body: ProbeFile):
    path = Path(body.path).expanduser().resolve()
    if not path.is_file():
        raise HTTPException(422, 'That file could not be found. Choose it again.')
    try:
        return {'tracks': audio_tracks(path)}
    except ValueError as exc:
        raise HTTPException(422, str(exc))

@app.post('/api/media/upload')
async def prepare_upload(file: UploadFile = File(...)):
    folder = UPLOADS / str(uuid.uuid4())
    folder.mkdir()
    try:
        path = folder / 'source.media'
        # The manifest is written last, so cleanup never touches an in-flight copy.
        with path.open('wb') as target:
            while chunk := await file.read(8 * 1024 * 1024):
                await asyncio.to_thread(target.write, chunk)
        tracks = await asyncio.to_thread(audio_tracks, path)
        metadata = {'name': Path(file.filename or 'Video').name, 'tracks': tracks}
        atomic_json(folder / 'upload.json', metadata)
        return dict(metadata, upload_id=folder.name)
    except BaseException as exc:
        shutil.rmtree(folder, ignore_errors=True)
        if isinstance(exc, ValueError):
            raise HTTPException(422, str(exc))
        raise
    finally:
        await file.close()

@app.post('/api/jobs/prepared')
def prepared_job(body: PreparedJob):
    try:
        if str(uuid.UUID(body.upload_id)) != body.upload_id:
            raise ValueError()
    except ValueError:
        raise HTTPException(404, 'Prepared upload not found. Choose the file again.')
    with lock:
        upload = UPLOADS / body.upload_id
        if not (upload / 'upload.json').is_file():
            raise HTTPException(404, 'This media copy was cleaned up or already used. Choose the file again.')
        metadata = json.loads((upload / 'upload.json').read_text())
        validate_settings(body.settings)
        if body.settings.track >= len(metadata['tracks']):
            raise HTTPException(422, 'Choose one of the available audio tracks.')
        folder = JOBS / str(uuid.uuid4())
        folder.mkdir()
        path = folder / 'source.media'
        (upload / 'source.media').replace(path)
        try:
            job = create_job(path, body.settings, uploaded=True, folder=folder, name=metadata['name'])
        except BaseException:
            path.replace(upload / 'source.media')
            shutil.rmtree(folder, ignore_errors=True)
            raise
        shutil.rmtree(upload)
        return job

@app.post('/api/jobs/upload')
async def upload_job(file: UploadFile = File(...), settings: str = Form(...)):
    try:
        parsed = Settings.model_validate_json(settings)
    except ValueError:
        raise HTTPException(422, 'Invalid generation settings.')
    validate_settings(parsed)
    folder = JOBS / str(uuid.uuid4())
    folder.mkdir()
    # Fixed destination avoids trusting uploaded file names as filesystem paths.
    path = folder / 'source.media'
    try:
        with path.open('wb') as target:
            while chunk := await file.read(8 * 1024 * 1024):
                await asyncio.to_thread(target.write, chunk)
        return create_job(path, parsed, uploaded=True, folder=folder, name=Path(file.filename or 'Video').name)
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    finally:
        await file.close()

@app.get('/api/jobs/{job_id}')
def get_job(job_id: str):
    folder = folder_for(job_id)
    job = view(folder)
    source = json.loads((folder / 'job.json').read_text())['source']
    job['media_available'] = Path(source).is_file() and (not job.get('remote') or job_id in hub.caches)
    if job.get('remote'):
        cache = hub.caches.get(job_id)
        job['download'] = {'bytes': cache.downloaded, 'total': cache.size, 'error': cache.error} if cache else None
    job['metrics'] = snapshot(folder, job['stage'])
    if job.get('remote') and (folder / 'remote-metrics.json').exists():
        job['metrics'] = json.loads((folder / 'remote-metrics.json').read_text())
    stream = folder / 'stream.json'
    if stream.exists():
        job['stream'] = json.loads(stream.read_text())
    if job['settings'].get('streaming'):
        job['player'] = player.status(folder)
    if (folder / 'result.json').exists() and job['stage'] == 'done':
        job['result'] = json.loads((folder / 'result.json').read_text())
    return job

@app.post('/api/jobs/{job_id}/player')
def open_player(job_id: str):
    folder = folder_for(job_id)
    if view(folder)['stage'] in ('error', 'cancelled'):
        raise HTTPException(409, 'This job stopped. Add the file again to watch with subtitles.')
    if view(folder).get('remote') and job_id not in hub.caches:
        raise HTTPException(409, 'The TorBox session ended. Paste the link again to watch.')
    try:
        return player.launch(folder)
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc))

@app.get('/api/jobs/{job_id}/log')
def get_log(job_id: str):
    return log_snapshot(folder_for(job_id))

@app.post('/api/jobs/{job_id}/cancel')
def cancel(job_id: str):
    folder = folder_for(job_id)
    with lock:
        if view(folder)['stage'] in ('done', 'error', 'cancelled'):
            return view(folder)
        proc = active.get(job_id)
        if proc and proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=10)
        atomic_json(folder / 'progress.json', {'stage': 'cancelled', 'message': 'Cancelled. You can add the file again whenever you’re ready.'})
        (folder / 'audio.wav').unlink(missing_ok=True)
        job = json.loads((folder / 'job.json').read_text())
        if job['uploaded'] and not job.get('remote') and not job['settings'].get('streaming'):
            Path(job['source']).unlink(missing_ok=True)
    return view(folder)

@app.put('/api/jobs/{job_id}/subtitles')
def edit(job_id: str, body: Edit):
    folder = folder_for(job_id)
    if view(folder)['stage'] != 'done':
        raise HTTPException(409, 'Wait for this job to finish before editing.')
    segments = [s.model_dump() for s in body.segments]
    try:
        validate_segments(segments)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    with lock:
        result = json.loads((folder / 'result.json').read_text())
        result['segments'] = segments
        atomic_json(folder / 'result.json', result)
    return {'saved': True}

@app.get('/api/jobs/{job_id}/download/{kind}')
def download(job_id: str, kind: str):
    folder = folder_for(job_id)
    if kind not in ('srt', 'vtt', 'txt'):
        raise HTTPException(404, 'Unknown subtitle format.')
    job = view(folder)
    if job['stage'] != 'done':
        raise HTTPException(409, 'Subtitles are not ready yet.')
    result = json.loads((folder / 'result.json').read_text())
    content = render(result['segments'], kind, job['settings']['width'])
    from urllib.parse import quote
    name = Path(job['name']).stem + ('.en' if job['settings']['translate'] else '') + '.' + kind
    return Response(content, media_type='text/vtt' if kind == 'vtt' else 'text/plain', headers={'Content-Disposition': "attachment; filename*=UTF-8''" + quote(name, safe='')})

@app.get('/')
def index():
    return FileResponse(ROOT / 'app/static/index.html')

app.mount('/static', StaticFiles(directory=ROOT / 'app/static'), name='static')
