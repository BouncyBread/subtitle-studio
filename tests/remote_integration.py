"""Opt-in real FFmpeg + local range proxy test; no TorBox account/media needed.
Run: PYTHONPATH=. .venv/bin/python tests/remote_integration.py
"""
import json
from pathlib import Path
import subprocess
import tempfile
import time
import httpx
from app.remote import RangeCache, RemoteHub
from app.remote_worker import transcribe_remote
from app.media import audio_tracks


def main():
    with tempfile.TemporaryDirectory(prefix='subtitle-remote-') as temp:
        root = Path(temp)
        movie = root/'sample.mkv'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=640x360:rate=24',
            '-f','lavfi','-i','sine=frequency=440:sample_rate=16000',
            '-f','lavfi','-i','sine=frequency=880:sample_rate=16000',
            '-t','95','-map','0:v','-map','1:a','-map','2:a','-c:v','mpeg4','-q:v','2',
            '-c:a','pcm_s16le','-metadata:s:a:0','language=eng','-metadata:s:a:1','language=fra',str(movie)],check=True)
        size=movie.stat().st_size
        calls=[]
        def upstream(request):
            a,b=map(int,request.headers['range'][6:].split('-'));b=min(b,size-1)
            calls.append((a,b));time.sleep(.01)
            with movie.open('rb') as f:f.seek(a);data=f.read(b-a+1)
            return httpx.Response(206,headers={'content-range':f'bytes {a}-{b}/{size}','etag':'fixed'},content=data)
        folder=root/'job';folder.mkdir()
        cache=RangeCache('https://cdn.torbox.app/sample?token=NOT_REAL',folder,
            client=httpx.Client(transport=httpx.MockTransport(upstream)),validator=lambda _:None)
        hub=RemoteHub()
        try:
            cache.block(0);source=hub.add(cache)
            tracks=audio_tracks(source)
            assert len(tracks)==2 and tracks[1]['language']=='fra'
            with httpx.Client() as client:
                assert client.head(source).headers['content-length']==str(size)
                assert client.get(source,headers={'Range':f'bytes={size}-'}).status_code==416
                assert client.get(source,headers={'Range':'bytes=-12'}).content==movie.read_bytes()[-12:]
                assert client.get(source,headers={'Range':'bytes=0-2','Origin':'https://evil.test'}).status_code==404
            first=[]
            def fake_transcribe(audio,**kwargs):
                if not first:first.append(cache.downloaded)
                return {'language':'fr','segments':[{'start':2,'end':4,'text':'Bonjour.'}]}
            result=transcribe_remote(folder,{'remote_source':source,'duration':95,'settings':{
                'track':1,'width':42,'language':'fr','translate':True,'prompt':''}},'unused',fake_transcribe)
            manifest=json.loads((folder/'stream.json').read_text())
            assert manifest['complete'] and abs(manifest['covered_until']-95)<.1
            assert len(result['segments'])>=3
            assert first[0] < size, (first,size)
            assert len(calls)==len(set(calls)), 'Duplicate upstream range downloads'
            print(f'PASS: 2 audio tracks, byte ranges, incremental subtitles before full download ({first[0]}/{size} bytes), EOF, shared cache.')
        finally:hub.shutdown()

if __name__=='__main__':main()
