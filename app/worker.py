"""One isolated inference process per job. Terminating it releases GPU memory."""
import json
import os
from pathlib import Path
import subprocess
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault('HF_HOME', str(ROOT / 'data' / 'models'))
os.environ.setdefault('HF_HUB_DISABLE_TELEMETRY', '1')
os.environ.setdefault('HF_HUB_DISABLE_XET', '1')

MODELS = {
    'turbo': 'mlx-community/whisper-large-v3-turbo',
    'large': 'mlx-community/whisper-large-v3-mlx',
    'small': 'mlx-community/whisper-small-mlx',
    'tiny': 'mlx-community/whisper-tiny',
}


def atomic_json(path, data):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    temp.replace(path)


def run(folder):
    folder = Path(folder)
    config = json.loads((folder / 'job.json').read_text())
    def progress(stage, message):
        atomic_json(folder / 'progress.json', {'stage': stage, 'message': message})
    try:
        progress('audio', 'Reading the audio track…')
        probe = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', config['source']], capture_output=True, text=True, check=True)
        info = json.loads(probe.stdout)
        audio_streams = [s for s in info['streams'] if s['codec_type'] == 'audio']
        track = config['settings']['track']
        if track >= len(audio_streams):
            raise ValueError(f'This file has {len(audio_streams)} audio tracks; audio track {track+1} is unavailable.')
        wav = folder / 'audio.wav'
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', config['source'], '-map', f'0:a:{track}', '-vn', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(wav)], check=True, capture_output=True)
        settings = config['settings']
        model = settings['model']
        if settings['translate'] and model == 'turbo':
            model = 'large'
        progress('model', 'Loading Whisper. The first run downloads the selected model; later runs reuse it.')
        import mlx_whisper
        # Download separately so the UI distinguishes model setup from inference.
        from huggingface_hub import snapshot_download
        try:
            model_path = snapshot_download(MODELS[model], local_files_only=True)
            cached = Path(model_path)
            if not (cached / 'config.json').is_file() or not any((cached / name).is_file() for name in ('weights.npz', 'weights.safetensors')):
                raise FileNotFoundError('Model download is incomplete.')
        except Exception:
            model_path = snapshot_download(MODELS[model])
        progress('transcribing', 'Translating speech into English…' if settings['translate'] else 'Listening and creating timed subtitles…')
        transcribe = mlx_whisper.transcribe
        extra = {}
        publisher = None
        if settings.get('streaming'):
            from app.vendor.streaming_whisper import transcribe
            from app.streaming import SubtitlePublisher
            publisher = SubtitlePublisher(folder, settings['width'])
            extra['segment_callback'] = publisher.publish
        result = transcribe(
            str(wav), path_or_hf_repo=model_path,
            language=settings['language'] or None,
            task='translate' if settings['translate'] else 'transcribe',
            word_timestamps=not settings['translate'],
            condition_on_previous_text=False,
            initial_prompt=settings['prompt'] or None,
            verbose=False,
            **extra,
        )
        from app.subtitles import make_cues
        cues = make_cues(result['segments'], settings['width'])
        atomic_json(folder / 'result.json', {'segments': cues, 'language': result.get('language', 'unknown'), 'model': model, 'translated': settings['translate']})
        if publisher is not None:
            import wave
            with wave.open(str(wav)) as audio:
                duration = audio.getnframes() / audio.getframerate()
            publisher.publish(result['segments'], duration, duration, result.get('language', 'unknown'), complete=True)
        progress('done', f'{len(cues)} subtitles ready.' if cues else 'No speech detected. Try selecting a different audio track or language.')
    except Exception as exc:
        detail = str(exc)
        if isinstance(exc, subprocess.CalledProcessError):
            stderr = exc.stderr or ''
            detail = stderr.decode(errors='replace') if isinstance(stderr, bytes) else stderr
        progress('error', 'Could not generate subtitles: ' + detail[-1500:])
        traceback.print_exc()
        sys.exit(1)
    finally:
        (folder / 'audio.wav').unlink(missing_ok=True)
        if config.get('uploaded') and not config['settings'].get('streaming'):
            Path(config['source']).unlink(missing_ok=True)


if __name__ == '__main__':
    run(sys.argv[1])
