"""Remove only app-owned, unused media; preserve transcripts and source files."""
import json
from pathlib import Path

TERMINAL = {'done', 'error', 'cancelled'}


def collect(jobs_root, is_busy, remove=False):
    jobs = []
    protected = set()
    for folder in jobs_root.iterdir():
        if folder.is_symlink() or not folder.is_dir():
            continue
        try:
            job = json.loads((folder / 'job.json').read_text())
        except (OSError, ValueError):
            continue
        try:
            progress = json.loads((folder / 'progress.json').read_text())
        except (OSError, ValueError):
            progress = {'stage': 'queued'}
        source = Path(job.get('source', '')).resolve()
        if not job.get('uploaded') or progress['stage'] not in TERMINAL or is_busy(folder):
            protected.add(source)
        jobs.append((folder, job, progress))
    summary = {'bytes': 0, 'files': 0, 'skipped_jobs': 0, 'errors': 0}
    for folder, job, progress in jobs:
        if progress['stage'] not in TERMINAL or is_busy(folder):
            summary['skipped_jobs'] += 1
            continue
        candidates = [folder / 'audio.wav']
        source = folder / 'source.media'
        if job.get('uploaded') and Path(job.get('source', '')).resolve() == source.resolve():
            candidates.append(source)
        for path in candidates:
            # Never follow symlinks, arbitrary source paths, or original-file references.
            if path.is_symlink() or path.resolve() in protected or not path.is_file():
                continue
            try:
                stat = path.stat()
                size = stat.st_blocks * 512 if job.get('remote') else stat.st_size
                if remove:
                    path.unlink()
                summary['bytes'] += size
                summary['files'] += 1
            except FileNotFoundError:
                pass
            except OSError:
                summary['errors'] += 1
    return summary
