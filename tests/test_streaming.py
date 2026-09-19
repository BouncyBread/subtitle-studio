import json
from app.streaming import SubtitlePublisher


def test_coverage_tracks_silent_audio_and_partial_cues(tmp_path):
    publisher=SubtitlePublisher(tmp_path,42)
    state=publisher.publish([{'start':0,'end':3,'text':'Hello'},{'start':29,'end':35,'text':'Across boundary'}],30,120,'en')
    assert state['covered_until']==30
    assert state['complete'] is False
    assert '00:00:30,000' in (tmp_path/'live.srt').read_text()
    state=publisher.publish([],60,120,'en')
    assert state['covered_until']==60 and state['cue_count']==0
    assert (tmp_path/'live.srt').read_text()==''
    assert json.loads((tmp_path/'stream.json').read_text())['version']==2


def test_publish_final_and_non_decreasing_frontier(tmp_path):
    publisher=SubtitlePublisher(tmp_path,42)
    publisher.publish([],90,120,'en')
    assert publisher.publish([],60,120,'en')['covered_until']==90
    state=publisher.publish([{'start':119,'end':130,'text':'The end'}],120,120,'en',complete=True)
    assert state['complete'] and state['covered_until']==120
    assert '00:02:00,000' in (tmp_path/'live.srt').read_text()
