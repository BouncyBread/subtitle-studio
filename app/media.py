"""Probe the audio-only order used by FFmpeg's 0:a:N and mpv's aid=N+1."""
import json
import subprocess

LANGUAGES = {
    'eng':'English','en':'English','fre':'French','fra':'French','fr':'French',
    'jpn':'Japanese','ja':'Japanese','deu':'German','ger':'German','de':'German',
    'spa':'Spanish','es':'Spanish','ita':'Italian','it':'Italian','por':'Portuguese',
    'pt':'Portuguese','kor':'Korean','ko':'Korean','chi':'Chinese','zho':'Chinese',
    'zh':'Chinese','yue':'Cantonese','rus':'Russian','ru':'Russian','ara':'Arabic',
    'ar':'Arabic','hin':'Hindi','hi':'Hindi','tha':'Thai','th':'Thai','und':'Unknown language',
    'dut':'Dutch','nld':'Dutch','nl':'Dutch','pol':'Polish','pl':'Polish',
}


def audio_tracks(path):
    try:
        result = subprocess.run(['ffprobe','-v','error','-select_streams','a',
            '-show_entries','stream=index,codec_name,channels:stream_tags=language,title:stream_disposition=default',
            '-of','json',str(path)],capture_output=True,text=True,check=True,timeout=30)
        streams = json.loads(result.stdout).get('streams', [])
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise ValueError('Could not read the audio tracks. Choose a readable video or audio file.') from exc
    tracks=[]
    for ordinal, stream in enumerate(streams):
        tags = stream.get('tags', {})
        language = tags.get('language','und').lower()
        tracks.append({'index':ordinal,'stream_index':stream['index'],
            'language':language,'language_name':LANGUAGES.get(language,language),
            'title':tags.get('title',''),'codec':stream.get('codec_name','unknown'),
            'channels':stream.get('channels'), 'default':bool(stream.get('disposition',{}).get('default'))})
    if not tracks:
        raise ValueError('This file has no readable audio tracks.')
    return tracks
