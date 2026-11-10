#!/usr/bin/env python3
"""Place accepted narration WAVs on the scene timeline and write subtitle tracks."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import textwrap
import wave


def stamp(seconds, separator=','):
    ms = round(seconds * 1000)
    return f'{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02}{separator}{ms % 1000:03}'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--script', type=Path, required=True)
    parser.add_argument('--segments', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    script_bytes = args.script.read_bytes()
    scenes = json.loads(script_bytes)
    receipt = json.loads((args.segments / 'audio-manifest.json').read_text())
    assert receipt['source_sha256'] == hashlib.sha256(script_bytes).hexdigest(), 'Narration source changed'
    duration = scenes[-1]['end']
    rate, width, channels = 24000, 3, 1
    track = bytearray(round(duration * rate) * width)
    cues, inputs = [], []
    for i, scene in enumerate(scenes, 1):
        file = args.segments / f'segment-{i:02}.wav'
        assert hashlib.sha256(file.read_bytes()).hexdigest() == receipt['segments'][i-1]['sha256'], 'Accepted narration bytes changed'
        with wave.open(str(file)) as source:
            assert (source.getframerate(), source.getsampwidth(), source.getnchannels()) == (rate, width, channels)
            data = source.readframes(source.getnframes())
        speech = len(data) / width / rate
        start = scene['start'] + .20
        assert start + speech <= scene['end'] - .20, f'Scene {i} audio exceeds its slot'
        offset = round(start * rate) * width
        track[offset:offset + len(data)] = data
        sentences = re.split(r'(?<=[.!?])\s+', scene['text'].strip())
        weights = [len(s) for s in sentences]
        cursor = start
        for sentence, weight in zip(sentences, weights):
            end = cursor + speech * weight / sum(weights)
            cues.append({'start': cursor, 'end': end, 'text': sentence})
            cursor = end
        inputs.append({'file': file.name, 'sha256': hashlib.sha256(file.read_bytes()).hexdigest(),
                       'start': start, 'duration': speech})
    args.output.mkdir(parents=True, exist_ok=True)
    output = args.output / 'narration.wav'
    with wave.open(str(output), 'wb') as target:
        target.setnchannels(channels)
        target.setsampwidth(width)
        target.setframerate(rate)
        target.writeframes(track)
    (args.output / 'captions.json').write_text(json.dumps(cues, indent=2) + '\n')
    (args.output / 'showcase.srt').write_text('\n'.join(
        f"{i}\n{stamp(c['start'])} --> {stamp(c['end'])}\n{textwrap.fill(c['text'], 82)}\n"
        for i, c in enumerate(cues, 1)))
    (args.output / 'showcase.vtt').write_text('WEBVTT\n\n' + '\n'.join(
        f"{stamp(c['start'], '.')} --> {stamp(c['end'], '.')}\n{textwrap.fill(c['text'], 82)}\n" for c in cues))
    (args.output / 'narration-mix.json').write_text(json.dumps({'duration_seconds': duration,
        'source_sha256': receipt['source_sha256'], 'voice': receipt['voice'], 'segments': inputs,
        'audio_sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
        'subtitle_alignment': 'Sentence boundaries estimated proportionally within each measured speech segment; not word forced-alignment'}, indent=2) + '\n')
    print(json.dumps({'duration_seconds': duration, 'segments': len(inputs), 'caption_cues': len(cues)}))


if __name__ == '__main__':
    main()
