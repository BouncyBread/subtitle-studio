import json
from types import SimpleNamespace
import pytest
from app import media


def test_audio_order_labels_and_default(monkeypatch):
    streams=[{'index':1,'codec_name':'aac','channels':2,'tags':{'language':'fre','title':'VFF'},'disposition':{'default':1}}, {'index':4,'codec_name':'aac','tags':{'language':'jpn'}}, {'index':6,'codec_name':'aac','tags':{'language':'eng'}}]
    def run(args,**kwargs):
        assert args[args.index('-select_streams')+1]=='a'
        return SimpleNamespace(stdout=json.dumps({'streams':streams}))
    monkeypatch.setattr(media.subprocess,'run',run)
    tracks=media.audio_tracks('/movie.mkv')
    assert [t['index'] for t in tracks]==[0,1,2]
    assert [t['stream_index'] for t in tracks]==[1,4,6]
    assert [t['language_name'] for t in tracks]==['French','Japanese','English']
    assert tracks[0]['title']=='VFF' and tracks[0]['default']
    assert not tracks[1]['default']


def test_no_audio_is_actionable(monkeypatch):
    monkeypatch.setattr(media.subprocess,'run',lambda *a,**kw:SimpleNamespace(stdout='{"streams":[]}'))
    with pytest.raises(ValueError,match='no readable audio'):media.audio_tracks('/silent.mp4')
