"""Atomically publish finalized subtitle windows for the native player."""
from pathlib import Path
import time
from app.subtitles import make_cues, render
from app.worker import atomic_json


class SubtitlePublisher:
    def __init__(self, folder, width):
        self.folder = Path(folder)
        self.width = width
        self.version = 0
        self.coverage = 0
        self.started = time.monotonic()

    def publish(self, segments, covered_until, total, language, complete=False):
        # Coverage comes from decoded audio, not the last spoken word: silence is safe.
        coverage = max(self.coverage, min(float(total), float(covered_until)))
        cues = make_cues(segments, self.width)
        # Never expose captions for audio that has not been finalized yet.
        cues = [dict(c, end=min(c['end'], coverage)) for c in cues if c['start'] < coverage]
        text = render(cues, 'srt', self.width)
        temp = self.folder / 'live.srt.tmp'
        temp.write_text(text, encoding='utf-8')
        temp.replace(self.folder / 'live.srt')
        self.version += 1
        self.coverage = coverage
        elapsed = time.monotonic() - self.started
        state = {'version': self.version, 'covered_until': coverage, 'duration': float(total),
                 'cue_count': len(cues), 'complete': complete, 'language': language,
                 'elapsed_seconds': round(elapsed, 2)}
        # Commit the manifest after the subtitle file; the player never sees a future version.
        atomic_json(self.folder / 'stream.json', state)
        print(f'Live subtitles: {coverage:.1f}/{total:.1f}s ready; {len(cues)} cues', flush=True)
        return state
