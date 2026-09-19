#!/bin/zsh
cd "${0:A:h}"
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"
export UV_CACHE_DIR="$PWD/.cache/uv"
export HF_HOME="$PWD/data/models"
export HF_HUB_DISABLE_TELEMETRY=1
if [[ ! -x .venv/bin/python ]] || ! .venv/bin/python -c 'import fastapi, uvicorn; import importlib.util; assert importlib.util.find_spec("mlx_whisper")' >/dev/null 2>&1; then
  if ! command -v uv >/dev/null; then
    print 'Install uv first: brew install uv'
    read '?Press Enter to close.'
    exit 1
  fi
  uv sync --python 3.12 || { read '?Setup failed. Press Enter to close.'; exit 1; }
fi
.venv/bin/python launch.py
if [[ $? -ne 0 ]]; then
  read '?The app stopped. Press Enter to close.'
fi
