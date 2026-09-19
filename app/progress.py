"""Read bounded snapshots of Whisper's real frame progress, without touching inference."""
from pathlib import Path
import re
import time

FRAME_PROGRESS = re.compile(r'\d+%\|[^\r\n]*?\|\s*(\d+)/(\d+)\s*\[([\d:]+)<[^\r\n]*?(?:frames/s|s/frames)\]')
ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')


def read_log(path: Path, limit=65536):
    try:
        with path.open('rb') as stream:
            stream.seek(0, 2)
            size = stream.tell()
            offset = max(0, size - limit)
            stream.seek(offset)
            text = stream.read(limit).decode('utf-8', errors='replace')
        text = ANSI.sub('', text).replace('\r', '\n')
        if offset:
            text = text.partition('\n')[2]
        return [line for line in text.splitlines() if line.strip()]
    except FileNotFoundError:
        return []


def parse_progress(lines):
    for line in reversed(lines):
        match = FRAME_PROGRESS.search(line)
        if not match:
            continue
        frames, total = int(match[1]), int(match[2])
        if total <= 0:
            continue
        elapsed = 0
        for part in match[3].split(':'):
            elapsed = elapsed * 60 + int(part)
        frames = min(frames, total)
        fraction = frames / total
        # Use the whole-run average rather than tqdm's volatile last-chunk ETA.
        eta = round(elapsed * (total - frames) / frames) if frames >= 3000 and elapsed >= 10 else None
        return {'percent': round(100 * fraction, 1), 'processed_seconds': frames / 100,
                'total_seconds': total / 100, 'elapsed_seconds': elapsed,
                'eta_seconds': 0 if frames == total else eta}
    return None


def snapshot(folder, stage):
    path = folder / 'worker.log'
    metrics = parse_progress(read_log(path))
    if metrics:
        metrics['eta_seconds'] = metrics['eta_seconds'] if stage == 'transcribing' else None
    return metrics


def log_snapshot(folder):
    path = folder / 'worker.log'
    lines = read_log(path)[-80:]
    try:
        age = max(0, round(time.time() - path.stat().st_mtime))
    except FileNotFoundError:
        age = None
    return {'lines': lines, 'seconds_since_update': age}
