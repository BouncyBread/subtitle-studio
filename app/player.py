"""Launch isolated mpv instances; the bundled Lua script owns subtitle buffering."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
players = {}
player_lock = threading.RLock()


def executable():
    return shutil.which('mpv') or (str(Path('/opt/homebrew/bin/mpv')) if Path('/opt/homebrew/bin/mpv').is_file() else None)


def launch(folder):
    with player_lock:
        current = players.get(folder.name)
        if current and current.poll() is None:
            return {'opened': True}
        binary = executable()
        if not binary:
            raise ValueError('The player needs mpv. Install it with: brew install mpv')
        job = json.loads((folder / 'job.json').read_text())
        if not Path(job['source']).is_file():
            raise ValueError('The source video is no longer available. Choose the file again.')
        if not job['settings'].get('streaming'):
            raise ValueError('Choose Watch with live subtitles to create a playback job.')
        ipc_dir = Path(tempfile.mkdtemp(prefix='subtitle-player-', dir='/tmp'))
        env = dict(os.environ, SUBTITLE_JOB_DIR=str(folder.resolve()),
                   SUBTITLE_BUFFER=str(job['settings'].get('buffer_seconds', 60)))
        args = [binary, '--no-config', '--pause=yes', '--keep-open=yes', '--force-window=yes',
                '--title=Subtitle Studio — ' + job['name'], '--hwdec=auto-safe',
                '--term-status-msg=', '--sub-auto=no', '--sid=no', '--aid=' + str(job['settings']['track'] + 1),
                '--script=' + str(ROOT / 'app/player.lua'),
                '--input-ipc-server=' + str(ipc_dir / 'control.sock'),
                '--demuxer-max-bytes=16MiB', '--cache-secs=20',
                '--', job.get('remote_source', job['source'])]
        try:
            with (folder / 'player.log').open('w') as output:
                proc = subprocess.Popen(args, env=env, stdout=output, stderr=output, start_new_session=True)
        except BaseException:
            shutil.rmtree(ipc_dir, ignore_errors=True)
            raise
        players[folder.name] = proc
        # Socket path is local diagnostic metadata, never returned through the API.
        (folder / 'player-ipc.txt').write_text(str(ipc_dir / 'control.sock'))
        def reap():
            proc.wait()
            shutil.rmtree(ipc_dir, ignore_errors=True)
        threading.Thread(target=reap, daemon=True).start()
        return {'opened': True}


def status(folder):
    with player_lock:
        proc = players.get(folder.name)
        running = proc is not None and proc.poll() is None
    state = {}
    try:
        state = json.loads((folder / 'player-state.json').read_text())
    except (FileNotFoundError, ValueError):
        pass
    return dict(state, running=running)


def shutdown():
    with player_lock:
        for proc in players.values():
            if proc.poll() is None:
                proc.terminate()
