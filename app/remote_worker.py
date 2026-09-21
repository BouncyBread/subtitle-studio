"""Decode remote audio with pipe backpressure and transcribe bounded windows."""
import subprocess
import time
import numpy as np
from app.streaming import SubtitlePublisher
from app.worker import atomic_json

RATE = 16000
WINDOW = 30
CONTEXT = 2


def transcribe_remote(folder, config, model_path, transcribe):
    settings = config['settings']
    publisher = SubtitlePublisher(folder, settings['width'])
    total = config['duration']
    started = time.monotonic()
    segments = []
    language = settings['language'] or None
    # The server's localhost proxy is the only address exposed to child processes.
    proc = subprocess.Popen([
        'ffmpeg', '-nostdin', '-v', 'error', '-i', config['remote_source'],
        '-map', f"0:a:{settings['track']}", '-vn', '-ac', '1', '-ar', str(RATE),
        '-f', 's16le', '-c:a', 'pcm_s16le', 'pipe:1'
    ], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    pending = b''
    previous = b''
    offset = 0.0
    try:
        while True:
            wanted = (WINDOW + CONTEXT) * RATE * 2
            while len(pending) < wanted:
                chunk = proc.stdout.read(wanted - len(pending))
                if not chunk:
                    break
                pending += chunk
            if not pending:
                break
            final = len(pending) < wanted
            core = min(len(pending), WINDOW * RATE * 2)
            end = offset + core / (RATE * 2)
            audio = np.frombuffer(previous + pending, dtype=np.int16).astype(np.float32) / 32768
            base = offset - len(previous) / (RATE * 2)
            result = transcribe(audio, path_or_hf_repo=model_path, language=language,
                task='translate' if settings['translate'] else 'transcribe',
                word_timestamps=False, condition_on_previous_text=False,
                initial_prompt=settings['prompt'] or None, verbose=False)
            language = language or result.get('language')
            for segment in result['segments']:
                a, b = base + segment['start'], base + segment['end']
                # Assign a boundary-spanning phrase to one core by its midpoint.
                if offset <= (a + b) / 2 < end:
                    segments.append({'start': max(offset, a), 'end': min(end, b), 'text': segment['text']})
            publisher.publish(segments, end, max(total, end), language or 'unknown')
            elapsed = time.monotonic() - started
            atomic_json(folder / 'remote-metrics.json', {
                'percent': min(99.9, end / total * 100), 'processed_seconds': end,
                'total_seconds': max(total, end), 'elapsed_seconds': elapsed,
                'eta_seconds': max(0, (total - end) * elapsed / end),
            })
            previous = pending[max(0, core - CONTEXT * RATE * 2):core]
            pending = pending[core:]
            offset = end
            if final and not pending:
                break
        code = proc.wait(timeout=30)
        if code != 0 or offset < total - max(5, total * .01):
            raise ValueError('Remote audio stopped early. Check the connection or paste a fresh TorBox link and start again.')
        publisher.publish(segments, offset, offset, language or 'unknown', complete=True)
        return {'segments': segments, 'language': language or 'unknown'}
    finally:
        proc.stdout.close()
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
