# Optional local neural narration

`generate.py` turns an approved, timed narration script into replaceable WAV
segments. It uses the stock US English `af_heart` voice from Kokoro-82M v1.0.
It does not clone a speaker, contact a speech API, install packages, download
weights, mux a video, or change the CAD runtime. It requires explicit local
inputs and a new output directory.

This path was executed on macOS arm64 with Python 3.12.13, kokoro-onnx 0.6.1,
and CPU ONNX Runtime 1.31.1. Other hosts have not been qualified by that run.
The 16 package versions and distribution hashes are fixed in
`requirements.lock`. There is no Torch dependency. Keep this optional runtime
outside the installed plugin and separate from the qualified CAD environment.

## Manual setup

Run these commands from the repository root only when you choose to build a
voice track. Choose a fresh ignored directory for `PE_TTS`. The explicit
downloads total 353719767 bytes, plus the Python packages.

```sh
PE_NARRATION="tests/physical-engineering/support/showcase/narration"
PE_TTS="work/physical-engineering/narration-runtime"
mkdir -p "$PE_TTS/models"
uv --no-config venv --python 3.12 "$PE_TTS/runtime"
uv --no-config pip install --python "$PE_TTS/runtime/bin/python" \
  --require-hashes --requirements "$PE_NARRATION/requirements.lock" \
  --default-index https://pypi.org/simple

curl --fail --location \
  --output "$PE_TTS/models/kokoro-v1.0.onnx" \
  https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/kokoro-v1.0.onnx
curl --fail --location \
  --output "$PE_TTS/models/voices-v1.0.bin" \
  https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/voices-v1.0.bin
```

The generator checks both downloaded files against the publisher's release
SHA256s and sizes in `assets.json` before it loads the model. It also checks
the installed package versions against the lock and fails with setup guidance
when a dependency is missing or differs. Do not copy the weights, runtime,
caches or generated audio into tracked source.

## Script and generation

The script is a nonempty JSON array with nonoverlapping `start` and `end`
times in seconds and a nonempty `text` string for each scene:

```json
[
  {
    "start": 0,
    "end": 10,
    "text": "The wider battery fails the first fit check. Codex preserves that failed run."
  }
]
```

Prepare the script in the ignored build directory, then render a new take:

```sh
"$PE_TTS/runtime/bin/python" "$PE_NARRATION/generate.py" \
  --segments work/physical-engineering/showcase-build/narration-segments.json \
  --model "$PE_TTS/models/kokoro-v1.0.onnx" \
  --voices "$PE_TTS/models/voices-v1.0.bin" \
  --output work/physical-engineering/showcase-build/narration-take-01
```

Defaults are `af_heart`, `en-us`, natural speed 1.0 and four CPU inference
threads. `--voice` can select another stock voice already in the pinned table;
`--speed` selects a rate from 0.5 to 2.0. The helper reserves 0.20 seconds before
speech and 0.20 seconds after speech within every scene. `--lead-in` and
`--tail-room` change those reservations; they do not add silence to the WAVs.

The output contains mono 24 kHz PCM24 WAVs, an exact copy of the input script,
and `audio-manifest.json`. The receipt includes text, phonemes, source/model/
code hashes, runtime versions, durations, scene headroom and sample peaks.
Those hashes identify the delivered take; regeneration is not promised to be
sample-identical. An executed repeat had the same text, duration and sample
count but different waveform samples. Preserve the accepted WAVs for a release.
The helper refuses to overwrite a prior take. If any segment exceeds its
scene, it preserves the receipt and exits unsuccessfully; revise the script
or scene timing before mixing. It does not silently stretch or truncate speech.

Align each WAV at its scene start plus the recorded lead-in. Keep the source
WAVs when normalizing loudness and encoding the final voice track. Captions
must use the approved narration text; the video mix and captions are separate
production steps.

## Provenance, licenses and review

The [ONNX wrapper](https://github.com/thewh1teagle/kokoro-onnx) is MIT licensed.
The [official model card](https://huggingface.co/hexgrad/Kokoro-82M) declares
Apache-2.0 for the model. `assets.json` pins the maintainer's
[model-files-v1.1 conversion release](https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.1),
the model card and stock-voice documentation. Python dependencies have their
own licenses, including phonemizer's GPLv3-or-later license. This companion
contains the helper, pins and source links, not those third-party binaries.

The helper requests that ONNX Runtime telemetry be disabled before creating
the inference session. It does not establish a network-monitoring claim.
No credential is read by the helper or required for these public downloads.

Numerical checks do not establish pronunciation, natural delivery or good
teaching. Listen to the rendered track, especially “Codex,” CAD/STEP/STL,
dimensions and the closing words. The phoneme receipt can reveal text handling
problems: for example, “Battery A is” treats A as an article, while “Battery A,
thirty-five millimeters wide” produces the letter name. Review loudness,
sentence joins, visual synchronization and the final decoded MP4. Disclose the voice in the media
receipt, for example: “AI narration generated locally with Kokoro, stock
af_heart voice.” Do not describe it as a human participant recording.

For an intentional dependency update, resolve in a fresh narration runtime
and repeat the executed audio and listening checks. Reproduce an existing
take from the committed lock instead of resolving again:

```sh
uv --no-config pip compile "$PE_NARRATION/requirements.in" \
  --python "$PE_TTS/runtime/bin/python" --generate-hashes \
  --output-file "$PE_NARRATION/requirements.lock" \
  --default-index https://pypi.org/simple
```
