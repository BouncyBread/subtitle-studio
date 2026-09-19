# Subtitle Studio

A private subtitle generator and buffered video player for **Apple Silicon Macs**, built with MLX Whisper and FFmpeg. It runs in your browser, but the server and speech model run on your Mac. No account, subscription, API key, or cloud transcription service.

## Open the app

Double-click **Start Subtitle Studio.command** in this folder. Keep its Terminal window open while processing. The app opens at **http://127.0.0.1:8765**. Press Control-C in that Terminal to stop it.

1. **Choose file…** opens the Mac file picker and reads your movie directly. This is best for large movies. Drag-and-drop and browser selection also work, but make a temporary local copy.
2. Leave **Best accuracy · Whisper Large v3** selected for the strongest Whisper model offered here.
3. **Translate to English** is on by default. Switch it off to keep the spoken language. English speech also produces English subtitles with this option on.
4. Click **Generate subtitles**. The first run of each model downloads its weights. Subsequent runs use the local cache.
5. Review and edit the subtitle text or start/end times (in seconds). Click **Save edits**, then **Download SRT**. VTT and plain text are also available.
6. Put the subtitle file next to your movie. Give it the same base name, e.g. `My Movie.mkv` and `My Movie.srt`, or open the SRT through your player's subtitle menu.

Jobs process one at a time to avoid exhausting GPU memory. You can add more files while a job runs, switch between jobs in Recent files, and cancel a running or queued job. Closing the browser does not stop processing; closing the server does. Interrupted jobs can be retried by adding the source file again.

## Settings

| Model | Use | Approximate download |
|---|---|---|
| Large v3 | Best accuracy option here; multilingual transcription and English translation | 3 GB |
| Large v3 Turbo | Faster transcription; automatically switches to Large v3 for translation | 1.6 GB |
| Small | Lower memory use; lower accuracy | 500 MB |
| Tiny | Quick setup tests; lowest accuracy | 75 MB |

These are download sizes, not runtime memory requirements. Start with Small if your Mac struggles with Large v3. “Best accuracy” compares the options provided here; accuracy varies by language, sound quality, and content.

- **Spoken language:** automatic detection is convenient; choose a language explicitly when detection gets it wrong. It applies to the whole file. Mixed-language speech may need review.
- **Translate to English:** translates speech directly using Whisper. Other target languages are not included in this version.
- **Audio track to use:** after choosing a file, the dropdown lists its audio tracks with language, title, codec, channel count, and default flag. Select the desired dialogue/dub/commentary track. The same choice is used for transcription and live-player audio. This setting resets to each new file’s default track.
- **Characters per line:** controls wrapping, with 42 as the default. Generated cues aim for two lines. Long manual edits may need shortening.
- **Names & vocabulary:** optional recognition hints for names and uncommon terms.

Transcription uses word timestamps where available. Translation uses Whisper segment timing, so translated line splits have approximate timing. This creates sidecar subtitle files; it does not burn captions into video or label speakers. Most formats readable by FFmpeg work (including MP4, MKV, MOV, AVI, WebM, MP3, M4A, WAV, and FLAC). Files need a readable, unencrypted audio track. Music, overlapping dialogue, silence, accents, and low-quality audio can cause incorrect text or timing. Review the result.

## Files and privacy

- `data/models/`: downloaded Whisper models, shared by jobs in this installation.
- `data/jobs/<id>/`: settings, status, transcript, and a local diagnostic log for each job.
- Original files selected with **Choose file…** are read without modification. Temporary uploaded copies and extracted audio are removed after processing or cancellation in generation-only mode. Watch-mode uploads are retained in their job folder so the player can keep reading them.
- The app listens only on `127.0.0.1`. It has no analytics or remote fonts. Model downloads contact Hugging Face; your audio is not sent there. Cached models are tried without network access first.
- Downloads go to your browser's download folder. Settings are remembered in that browser.
- Job history remains until you remove the corresponding folders with the app stopped. Keep `data/models/` to avoid downloading again.

## Setup on another Mac

Requires macOS on Apple Silicon, Python 3.12, [uv](https://docs.astral.sh/uv/), and [FFmpeg](https://ffmpeg.org/). With Homebrew:

```sh
brew install uv ffmpeg mpv
```

Then open the launcher. It creates a project-local Python environment. For manual setup:

```sh
UV_CACHE_DIR="$PWD/.cache/uv" uv sync --python 3.12
.venv/bin/python launch.py
```

Dependencies are locked in `uv.lock`. No system Python packages are changed. The first setup and first model use require internet access.

## Development and checks

```sh
.venv/bin/python -m pytest -q
node --check app/static/app.js
.venv/bin/python -m uvicorn app.server:app --host 127.0.0.1 --port 8765
```

Tests cover timestamp rounding, cue splitting, subtitle formats, malformed/overlapping edits, exports, local-request protection, and translation model selection. `tests/smoke_local.py` is an opt-in real-model test that requires a running app and `data/test-speech.aiff`; it downloads Large v3 if missing. Use `tests/smoke_local.py test-french.aiff` for the generated French test clip.

Architecture: FastAPI serves a dependency-free browser UI. A serial supervisor starts one isolated Python worker per job, FFmpeg extracts mono audio, and MLX Whisper runs on the Apple GPU. JSON files persist job state atomically. Cancellation terminates the worker process group, including FFmpeg. Source media is never served to browser clients.

## Model references

- [Apple MLX Whisper implementation](https://github.com/ml-explore/mlx-examples/tree/main/whisper)
- [Whisper model options and translation limitations](https://github.com/openai/whisper#available-models-and-languages)
- [MLX Large v3 weights](https://huggingface.co/mlx-community/whisper-large-v3-mlx)
- [MLX Turbo weights](https://huggingface.co/mlx-community/whisper-large-v3-turbo)

## Progress and live logs

While a file is transcribing, the app shows the actual percentage of audio processed, processed/total audio duration, elapsed transcription time, and an approximate time remaining. The estimate appears after at least 30 seconds of audio and 10 seconds of processing, using average speed so far. It updates after completed audio chunks, not on a fake timer. Model loading, downloads, and audio extraction have an indeterminate bar; transcription estimates exclude those preparation steps. Difficult dialogue can change the estimate significantly.

Expand **Live processing log** to see the latest 80 worker-log lines, refreshed every 2.5 seconds. **Follow latest updates** scrolls to new output; uncheck it to inspect earlier lines. The last-output age distinguishes recent activity from a chunk that is taking longer. The log remains available after completion or cancellation. This is a diagnostic progress feed, not a partial subtitle transcript. The server reads only a bounded tail of the log, so long movies do not cause growing browser responses.


## Watch while subtitles are generated

Choose a video, keep your model/language/translation settings, then click **Watch with live subtitles**. A separate native **mpv** window opens paused. The app prepares the audio and model, generates a lead of finalized subtitles, and automatically begins playback. The same Desktop shortcut opens the app with this feature available.

- **Subtitle buffer:** choose 30 seconds, 60 seconds (default), or 2 minutes. This is *audio ready ahead of playback*, not wall-clock waiting time. Videos shorter than the buffer start when transcription completes.
- New subtitle sections are loaded into the player without restarting the video. After generation finishes, the usual SRT/VTT/text downloads and editor are available. Playback uses the generated live subtitle track; subsequent editor changes affect exports, not the open live track.
- If playback catches up with generation, it pauses before unfinished audio and resumes when the buffer is rebuilt. Higher playback speeds require a proportionally larger buffer.
- Seek freely within already processed audio. Seeking beyond it pauses until sequential generation reaches that position and rebuilds the buffer. This version does not reprioritize transcription around seeks.
- **Space / P:** pause or resume; a manual pause stays paused when new subtitles arrive. **F:** fullscreen. **Left / Right:** seek. **B:** show subtitle buffer information. **Q:** close the player.
- Closing the player leaves transcription running. **Open player** reopens that job from the beginning. Closing the app's server stops both its workers and its players. Cancellation or a generation error pauses the player and shows the problem.
- Audio tracks are matched to the track chosen in the app. Set the desired track before starting; changing the audio track inside mpv does not change which audio Whisper is transcribing.
- Choose File reads the original without copying it. Browser uploads in watch mode are retained as `data/jobs/<id>/source.media` for playback, including after completion/cancellation; remove a job's folder with the app/player stopped when you no longer need it. `live.srt` and `stream.json` are atomically updated playback files. Your source video is never modified.

The player is local and supports formats available in mpv/FFmpeg, including MKV and MP4. Install the optional native player dependency with `brew install mpv` on another machine. This is a custom integration around mpv, not a VLC or Stremio extension. There is still an initial buffer wait, and uninterrupted playback depends on generation keeping ahead; subtitle quality is the same model-dependent AI output as normal mode.

Streaming uses a small adaptation of **mlx-whisper 0.4.3**'s transcription loop, with a callback after finalized audio windows (including silence). It does not re-transcribe artificial overlapping chunks, so it preserves Whisper's original decoding and timestamp behavior. The vendored file is `app/vendor/streaming_whisper.py`; Apple's MIT license is included in `app/vendor/LICENSE-MLX`. The dependency is pinned to that compatible version. mpv is installed separately via Homebrew and is not redistributed by this project. Its official [subtitle reload commands](https://mpv.io/manual/stable/#track-manipulation) are used by the original `app/player.lua` integration.

Additional verification:

```sh
.venv/bin/python tests/player_integration.py
.venv/bin/python tests/watch_smoke.py
```

The first test runs the real mpv/Lua controller headlessly and checks initial buffering, auto-start, live subtitle reload, manual pause, seek-ahead waiting, end-of-file readiness, and error handling. The second requires the local server and generated `data/Live-player-demo.mp4`; it opens a muted native player and verifies that real Large v3 English translation starts playback before the transcription job finishes.

## Clean up media copies

The **Clean up media copies** button above Recent files shows reclaimable space and removes unused app-owned copied videos (`source.media`) and leftover extracted audio (`audio.wav`). Original videos, subtitles, saved edits, job history, logs, and downloaded models are kept. Queued/running jobs and open players are skipped, even if transcription has finished. Close the player before cleaning its copy. After an uploaded video's copy is removed, select its original file again to watch; existing subtitle downloads remain available. Cleanup does not remove browser downloads or arbitrary files from the project.


Audio-track inspection happens when selecting a file. **Choose file…** probes the original directly. Browser selection and drag-and-drop first copy the video once into `data/uploads/` to inspect all tracks; starting generation moves that same copy into the job folder without uploading it again. Unused prepared copies can be removed with **Clean up media copies**; copies still uploading are skipped. If inspection fails, its temporary copy is removed. Files without audio cannot be submitted. To process a browser-selected file again with different settings, choose it again.
