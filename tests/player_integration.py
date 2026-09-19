"""Opt-in integration test against real mpv and our real Lua script, without a window."""
from pathlib import Path
import json
import os
import socket
import subprocess
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]

def wait_for(check, message, timeout=8):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        try:
            if check():return
        except (FileNotFoundError, ValueError):pass
        time.sleep(.1)
    raise AssertionError(message)

with tempfile.TemporaryDirectory(prefix='studio-test-',dir='/tmp') as directory:
    folder=Path(directory)
    video=folder/'video.mp4'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=black:s=160x90:r=2:d=180','-c:v','libx264','-preset','ultrafast',str(video)],check=True)
    def write(name,data):
        tmp=folder/(name+'.tmp');tmp.write_text(json.dumps(data));tmp.replace(folder/name)
    def publish(version,coverage,complete=False,text='Hello from the live subtitle test'):
        tmp=folder/'live.srt.tmp';tmp.write_text('1\n00:00:00,000 --> 00:02:59,000\n'+text+'\n');tmp.replace(folder/'live.srt')
        write('stream.json',{'version':version,'covered_until':coverage,'duration':180,'complete':complete,'cue_count':1,'language':'en'})
    write('progress.json',{'stage':'transcribing','message':'Working'})
    sockpath=str(folder/'ipc')
    env=dict(os.environ,SUBTITLE_JOB_DIR=directory,SUBTITLE_BUFFER='60')
    with (folder/'mpv.log').open('w') as log:
        proc=subprocess.Popen(['mpv','--no-config','--vo=null','--ao=null','--pause=yes','--keep-open=yes','--script='+str(ROOT/'app/player.lua'),'--input-ipc-server='+sockpath,str(video)],env=env,stdout=log,stderr=log)
    try:
        wait_for(lambda:Path(sockpath).exists(),'mpv did not start')
        client=socket.socket(socket.AF_UNIX);client.settimeout(5);client.connect(sockpath)
        reader=client.makefile('rb');request=0
        def command(*args):
            global request
            request+=1
            client.sendall((json.dumps({'command':list(args),'request_id':request})+'\n').encode())
            while True:
                line=reader.readline()
                if not line:raise RuntimeError('mpv exited')
                result=json.loads(line)
                if result.get('request_id')==request:
                    assert result['error']=='success',result
                    return result.get('data')
        def state():return json.loads((folder/'player-state.json').read_text())
        wait_for(lambda:state()['buffering'],'Must initially wait for subtitles')
        assert command('get_property','pause') is True
        publish(1,70)
        wait_for(lambda:not state()['buffering'],'Should start with 60s buffered')
        wait_for(lambda:command('get_property','pause') is False,'Should auto-play')
        wait_for(lambda:command('get_property','sub-text')=='Hello from the live subtitle test','Subtitles not loaded')
        command('set_property','pause',True)
        wait_for(lambda:state()['user_paused'],'Manual pause not respected')
        publish(2,100,text='Updated subtitle')
        wait_for(lambda:state()['subtitle_version']==2,'New subtitles did not load')
        assert command('get_property','pause') is True
        wait_for(lambda:command('get_property','sub-text')=='Updated subtitle','Updated subtitle text missing')
        command('keypress','SPACE')
        wait_for(lambda:command('get_property','pause') is False,'Space should resume')
        command('seek',120,'absolute+exact')
        wait_for(lambda:state()['buffering'],'Seek past generated audio must pause')
        assert command('get_property','pause') is True
        publish(3,170)
        time.sleep(1)
        assert state()['buffering'],'Must rebuild full buffer before resuming'
        publish(4,180,complete=True)
        wait_for(lambda:not state()['buffering'],'Complete subtitles should release near-end buffer')
        assert command('get_property','pause') is False
        write('progress.json',{'stage':'error','message':'Test failure'})
        wait_for(lambda:state()['failed'] and state()['buffering'],'Errors must stop automatic playback')
        print('PASS: real mpv buffering, auto-start, live subtitle reload, manual pause, seek, completion, and error handling',flush=True)
    except BaseException:
        print((folder/'mpv.log').read_text())
        raise
    finally:
        proc.terminate();proc.wait(timeout=10)
