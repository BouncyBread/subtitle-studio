from concurrent.futures import ThreadPoolExecutor
import json
import socket
import httpx
import pytest
from app.remote import RangeCache, BLOCK, validate_url


def cache_for(tmp_path, handler):
    return RangeCache('https://cdn.torbox.app/video?token=PRIVATE', tmp_path,
                      client=httpx.Client(transport=httpx.MockTransport(handler)), validator=lambda u: None)


def test_shared_cache_and_out_of_order_ranges(tmp_path):
    calls = []
    data = b'a' * BLOCK + b'b' * BLOCK + b'last'
    def serve(req):
        a, b = map(int, req.headers['range'][6:].split('-'))
        b = min(b, len(data)-1)
        calls.append((a, b))
        return httpx.Response(206, headers={'content-range': f'bytes {a}-{b}/{len(data)}', 'etag': 'same'}, content=data[a:b+1])
    cache = cache_for(tmp_path, serve)
    assert cache.block(0) == b'a' * BLOCK
    assert cache.block(2) == b'last'
    with ThreadPoolExecutor(4) as pool:
        assert all(x == b'b' * BLOCK for x in pool.map(cache.block, [1,1,1,1]))
    assert len(calls) == 3
    assert cache.downloaded == len(data)
    assert (tmp_path/'source.media').read_bytes() == data
    cache.close()
    assert cache.url == ''
    with pytest.raises(ValueError, match='closed'):
        cache.block(0)


@pytest.mark.parametrize('status,headers,body', [
    (403,{},b''), (200,{},b'whole file'),
    (206,{'content-range':'bytes 1-2/3'},b'xx'),
    (206,{'content-range':f'bytes 0-{BLOCK-1}/{BLOCK}'},b'truncated'),
])
def test_bad_remote_response_is_safe(tmp_path, status, headers, body):
    cache = cache_for(tmp_path, lambda req:httpx.Response(status, headers=headers, content=body))
    with pytest.raises(ValueError) as exc:
        cache.block(0)
    assert 'PRIVATE' not in str(exc.value)
    assert not (tmp_path/'source.media').exists()
    assert cache.downloaded == 0
    cache.close()


def test_exception_does_not_expose_link(tmp_path):
    def fail(req):
        raise httpx.ConnectError(str(req.url))
    cache = cache_for(tmp_path, fail)
    with pytest.raises(ValueError) as exc:
        cache.block(0)
    assert 'PRIVATE' not in str(exc.value)
    cache.close()


@pytest.mark.parametrize('url', ['http://cdn.torbox.app/a','https://evil.test/a',
    'https://torbox.app.evil.test/a','https://u:p@torbox.app/a','https://torbox.app:123/a',
    'file:///tmp/movie','https://127.0.0.1/a'])
def test_reject_non_torbox_url(url):
    with pytest.raises(ValueError):
        validate_url(url)


def test_reject_private_dns(monkeypatch):
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**kw:[(0,0,0,'',('127.0.0.1',443))])
    with pytest.raises(ValueError):
        validate_url('https://cdn.torbox.app/a')


def test_changed_video_is_rejected(tmp_path):
    def serve(req):
        a,b=map(int,req.headers['range'][6:].split('-'))
        return httpx.Response(206,headers={'content-range':f'bytes {a}-{b}/{2*BLOCK}',
             'etag':'first' if a==0 else 'changed'},content=b'x'*BLOCK)
    cache=cache_for(tmp_path,serve)
    cache.block(0)
    with pytest.raises(ValueError,match='changed'):
        cache.block(1)
    cache.close()


@pytest.mark.parametrize('host', ['torbox.app', 'cdn.torbox.app', 'tb-cdn.earth', 'nexus.hare.tb-cdn.earth'])
def test_torbox_cdn_hosts_accepted(monkeypatch, host):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **kw: [(0,0,0,'',('93.184.216.34',443))])
    validate_url(f'https://{host}/dld/test-file?token=test-only')


@pytest.mark.parametrize('host', ['tb-cdn.earth.evil.test', 'evil-tb-cdn.earth'])
def test_cdn_lookalikes_rejected(host):
    with pytest.raises(ValueError):
        validate_url(f'https://{host}/dld/test-file')


def test_torbox_redirect_to_cdn(tmp_path, monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **kw: [(0,0,0,'',('93.184.216.34',443))])
    calls = []
    def serve(request):
        calls.append(request.url.host)
        if request.url.host == 'api.torbox.app':
            return httpx.Response(302, headers={'location':'https://nexus.hare.tb-cdn.earth/dld/test?token=test-only'})
        return httpx.Response(206, headers={'content-range':'bytes 0-3/4'}, content=b'test')
    cache = RangeCache('https://api.torbox.app/test', tmp_path, client=httpx.Client(transport=httpx.MockTransport(serve)))
    try:
        assert cache.block(0) == b'test'
        assert calls == ['api.torbox.app', 'nexus.hare.tb-cdn.earth']
    finally:
        cache.close()
