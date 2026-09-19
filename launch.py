"""Launch only on loopback; leave this terminal open while processing."""
import json
import socket
import threading
import time
import urllib.request
import webbrowser
import uvicorn

PORT = 8765
URL = f'http://127.0.0.1:{PORT}'

def existing_app():
    try:
        with urllib.request.urlopen(URL + '/api/status', timeout=1) as response:
            return json.load(response).get('name') == 'Subtitle Studio'
    except Exception:
        return False


def open_when_ready():
    for _ in range(100):
        if existing_app():
            webbrowser.open(URL)
            return
        time.sleep(.1)

if __name__ == '__main__':
    if existing_app():
        webbrowser.open(URL)
    else:
        with socket.socket() as sock:
            if sock.connect_ex(('127.0.0.1', PORT)) == 0:
                raise SystemExit(f'Port {PORT} is in use by another app. Close it and try again.')
        threading.Thread(target=open_when_ready, daemon=True).start()
        print('\nSubtitle Studio — keep this window open while generating subtitles.\n')
        uvicorn.run('app.server:app', host='127.0.0.1', port=PORT, log_level='warning')
