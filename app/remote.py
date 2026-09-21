"""Private TorBox links stay in memory; local consumers share a range cache."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
from pathlib import Path
import re
import socket
import threading
from urllib.parse import urljoin, urlsplit

import httpx

BLOCK = 1024 * 1024


def validate_url(url):
    try:
        parts = urlsplit(url)
        host = (parts.hostname or '').lower()
        if (parts.scheme != 'https' or parts.username or parts.password or
                parts.port not in (None, 443) or parts.fragment or
                not (host == 'torbox.app' or host.endswith('.torbox.app'))):
            raise ValueError()
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise ValueError()
    except (ValueError, OSError):
        raise ValueError('Use a direct HTTPS video download link from TorBox (torbox.app).') from None


class RangeCache:
    def __init__(self, url, folder, client=None, validator=validate_url):
        self.folder = Path(folder)
        self.url = url
        self.validator = validator
        self.client = client or httpx.Client(timeout=30, trust_env=False)
        self.lock = threading.RLock()
        self.blocks = set()
        self.size = None
        self.downloaded = 0
        self.error = None
        self.closed = False
        self.identity = None
        self.tracks = []
        self.duration = 0
        self.started = False

    def request(self, start, end):
        url = self.url
        for _ in range(6):
            self.validator(url)
            with self.client.stream('GET', url, headers={
                'Range': f'bytes={start}-{end}', 'Accept-Encoding': 'identity'
            }) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    url = urljoin(url, response.headers.get('location', ''))
                    continue
                if response.status_code in (401, 403, 410):
                    raise ValueError('TorBox link expired or access was denied. Paste a fresh link and start again.')
                match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('content-range', ''))
                if response.status_code != 206 or not match:
                    raise ValueError('This link does not support video byte ranges. Copy the direct file download link from TorBox.')
                first, last, size = map(int, match.groups())
                if first != start or last != min(end, size - 1) or size <= 0:
                    raise ValueError('TorBox returned an invalid video range.')
                identity = response.headers.get('etag') or response.headers.get('last-modified')
                if self.size is not None and (size != self.size or (self.identity and identity != self.identity)):
                    raise ValueError('The remote file changed. Start again with a fresh link.')
                data = bytearray()
                for chunk in response.iter_bytes(65536):
                    data.extend(chunk)
                    if len(data) > last - first + 1:
                        raise ValueError('TorBox returned too much data for this video range.')
                if len(data) != last - first + 1:
                    raise ValueError('TorBox download was interrupted. Paste a fresh link and try again.')
                self.url = url
                self.size = size
                self.identity = identity
                return bytes(data)
        raise ValueError('Too many redirects. Copy a fresh direct video link from TorBox.')

    def block(self, index):
        # One upstream request at a time, shared by ffprobe, FFmpeg and mpv.
        with self.lock:
            if self.closed:
                raise ValueError('This download cache was closed. Paste the link again.')
            if self.error:
                raise ValueError(self.error)
            start = index * BLOCK
            if self.size is not None and start >= self.size:
                return b''
            path = self.folder / 'source.media'
            if index not in self.blocks:
                try:
                    data = self.request(start, start + BLOCK - 1)
                    with path.open('r+b' if path.exists() else 'w+b') as target:
                        target.seek(start)
                        target.write(data)
                    self.blocks.add(index)
                    self.downloaded += len(data)
                except Exception as exc:
                    self.error = str(exc) if isinstance(exc, ValueError) else 'Could not download from TorBox. Check your connection and try a fresh link.'
                    raise ValueError(self.error) from None
            with path.open('rb') as source:
                source.seek(start)
                return source.read(min(BLOCK, self.size - start))

    def close(self):
        with self.lock:
            self.closed = True
            self.url = ''
            self.client.close()


class RemoteHub:
    def __init__(self):
        self.caches = {}
        self.lock = threading.RLock()
        self.http = None

    def add(self, cache):
        with self.lock:
            if self.http is None:
                hub = self
                class Handler(BaseHTTPRequestHandler):
                    protocol_version = 'HTTP/1.1'
                    def log_message(self, *args):
                        pass
                    def do_HEAD(self):
                        self.serve(False)
                    def do_GET(self):
                        self.serve(True)
                    def serve(self, body):
                        # Random capability path; no cookies, CORS or public interface.
                        cache = hub.caches.get(self.path.removeprefix('/'))
                        if not cache or self.headers.get('Origin') or self.headers.get('Sec-Fetch-Site') == 'cross-site':
                            self.send_error(404)
                            return
                        try:
                            size = cache.size
                            start, end = 0, size - 1
                            header = self.headers.get('Range')
                            if header:
                                match = re.fullmatch(r'bytes=(\d*)-(\d*)', header)
                                if not match or not any(match.groups()):
                                    raise ValueError()
                                a, b = match.groups()
                                if a:
                                    start, end = int(a), min(int(b), size - 1) if b else size - 1
                                else:
                                    start = max(0, size - int(b))
                                if start > end or start >= size:
                                    raise ValueError()
                        except (ValueError, TypeError):
                            self.send_response(416)
                            self.send_header('Content-Range', f'bytes */{cache.size}')
                            self.send_header('Content-Length', '0')
                            self.end_headers()
                            return
                        self.send_response(206 if header else 200)
                        self.send_header('Accept-Ranges', 'bytes')
                        self.send_header('Content-Type', 'application/octet-stream')
                        self.send_header('Content-Length', str(end - start + 1))
                        self.send_header('Cache-Control', 'no-store')
                        if header:
                            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
                        self.end_headers()
                        if not body:
                            return
                        try:
                            while start <= end:
                                block = cache.block(start // BLOCK)
                                part = block[start % BLOCK:min(len(block), start % BLOCK + end - start + 1)]
                                if not part:
                                    break
                                self.wfile.write(part)
                                start += len(part)
                        except (OSError, ValueError):
                            self.close_connection = True
                self.http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
                self.http.daemon_threads = True
                threading.Thread(target=self.http.serve_forever, daemon=True).start()
            self.caches[cache.folder.name] = cache
            return f'http://127.0.0.1:{self.http.server_port}/{cache.folder.name}'

    def discard(self, key):
        with self.lock:
            cache = self.caches.pop(key, None)
        if cache:
            cache.close()

    def shutdown(self):
        for key in list(self.caches):
            self.discard(key)
        if self.http:
            self.http.shutdown()
            self.http.server_close()
            self.http = None


hub = RemoteHub()
