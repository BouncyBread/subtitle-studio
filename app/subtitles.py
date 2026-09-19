"""Subtitle formatting shared by generation, editing, and exports."""
import math
import re
import textwrap


def timestamp(seconds: float, separator: str = ',') -> str:
    total = max(0, round(seconds * 1000))
    hours, total = divmod(total, 3600000)
    minutes, total = divmod(total, 60000)
    secs, millis = divmod(total, 1000)
    return f'{hours:02}:{minutes:02}:{secs:02}{separator}{millis:03}'


def clean_text(text):
    return re.sub(r'\s+', ' ', text.replace('-->', '→')).strip()


def validate_segments(segments):
    previous = 0.0
    for s in segments:
        if not (math.isfinite(s['start']) and math.isfinite(s['end'])):
            raise ValueError('Timestamps must be finite numbers.')
        if s['start'] < previous or s['end'] <= s['start']:
            raise ValueError('Subtitles must be in order, without overlaps, and end after they start.')
        if not clean_text(s['text']):
            raise ValueError('Subtitle text cannot be empty.')
        previous = s['end']
    return segments


def make_cues(segments, width=42):
    """Prefer word timestamps; proportionally divide text when unavailable (translation)."""
    cues = []
    for segment in segments:
        text = clean_text(segment.get('text', ''))
        if not text:
            continue
        start = max(cues[-1]['end'] if cues else 0, float(segment['start']))
        end = float(segment['end'])
        if end <= start:
            continue
        words = segment.get('words') or []
        groups = []
        if words:
            group, length = [], 0
            for word in words:
                token = clean_text(word['word'])
                if group and (length + len(token) + 1 > width * 2 or float(word['end']) - float(group[0]['start']) > 6):
                    groups.append(group)
                    group, length = [], 0
                group.append(word)
                length += len(token) + 1
            if group:
                groups.append(group)
            for group in groups:
                a = max(start, float(group[0]['start']), cues[-1]['end'] if cues else 0)
                b = min(end, float(group[-1]['end']))
                if b > a:
                    cues.append({'start': a, 'end': b, 'text': clean_text(''.join(w['word'] for w in group))})
        else:
            lines = textwrap.wrap(text, width=width, break_long_words=True, break_on_hyphens=False)
            # For an odd line count, put the single line first so a short
            # trailing word is not flashed as its own cue.
            offset = len(lines) % 2
            chunks = lines[:offset] + [' '.join(lines[i:i+2]) for i in range(offset, len(lines), 2)]
            weights = sum(len(c) for c in chunks)
            cursor = start
            for i, chunk in enumerate(chunks):
                stop = end if i == len(chunks)-1 else cursor + (end-start)*len(chunk)/weights
                cues.append({'start': cursor, 'end': stop, 'text': chunk})
                cursor = stop
    for cue in cues:
        cue['start'] = round(cue['start'], 3)
        cue['end'] = round(cue['end'], 3)
    return [cue for cue in cues if cue['end'] > cue['start']]


def render(segments, kind='srt', width=42):
    if kind == 'txt':
        return '\n'.join(clean_text(s['text']) for s in segments) + '\n'
    sep = '.' if kind == 'vtt' else ','
    blocks = []
    for i, s in enumerate(segments, 1):
        text = '\n'.join(textwrap.wrap(clean_text(s['text']), width=width, break_long_words=True, break_on_hyphens=False))
        # VTT cue text uses HTML-like markup; escape literal markup from speech.
        if kind == 'vtt':
            import html
            text = html.escape(text)
        blocks.append(f'{i}\n{timestamp(s["start"], sep)} --> {timestamp(s["end"], sep)}\n{text}\n')
    return ('WEBVTT\n\n' if kind == 'vtt' else '') + '\n'.join(blocks)
