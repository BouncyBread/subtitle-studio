import pytest
from app.subtitles import make_cues, render, timestamp, validate_segments


def test_timestamp_rollover():
    assert timestamp(59.9999) == '00:01:00,000'
    assert timestamp(3600.123, '.') == '01:00:00.123'


def test_translation_splitting_preserves_timing_and_content():
    text = 'This is a long translated subtitle that should wrap onto readable lines without losing any of the original words.'
    cues = make_cues([{'start': 2, 'end': 12, 'text': text}], 24)
    assert len(cues) > 1
    assert cues[0]['start'] == 2 and cues[-1]['end'] == 12
    assert ' '.join(c['text'] for c in cues) == text
    validate_segments(cues)
    assert max(len(line) for line in render(cues, width=24).splitlines() if '-->' not in line) <= 24


def test_word_timestamps_and_overlaps():
    cues = make_cues([{'start':0,'end':5,'text':' Hello world.','words':[{'start':0.1,'end':1,'word':' Hello'},{'start':1,'end':2,'word':' world.'}]}, {'start':1,'end':3,'text':'Next line.'}])
    assert cues[0] == {'start':.1,'end':2,'text':'Hello world.'}
    assert cues[1]['start'] == 2
    validate_segments(cues)


def test_formats_and_literal_markup():
    cues = [{'start':1.234,'end':4.5,'text':'A < B & C'}]
    assert render(cues).startswith('1\n00:00:01,234 --> 00:00:04,500')
    assert render(cues, 'vtt').startswith('WEBVTT\n\n1\n00:00:01.234')
    assert 'A &lt; B &amp; C' in render(cues, 'vtt')
    assert render(cues,'txt') == 'A < B & C\n'


@pytest.mark.parametrize('segments', [[{'start':2,'end':1,'text':'a'}],[{'start':0,'end':1,'text':' '}],[{'start':0,'end':float('inf'),'text':'a'}],[{'start':0,'end':2,'text':'a'},{'start':1,'end':3,'text':'b'}]])
def test_invalid_edits(segments):
    with pytest.raises(ValueError): validate_segments(segments)


def test_translation_avoids_tiny_trailing_word_cue():
    cues=make_cues([{'start':3.6,'end':8.2,'text':'You can translate the dialogues into English and record the result with your film.'}])
    assert len(cues)==2
    assert all(c['end']-c['start']>1 for c in cues)
    assert cues[-1]['text'].endswith('film.')
