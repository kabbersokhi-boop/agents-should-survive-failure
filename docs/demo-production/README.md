# Project overview production

The 1:33 overview follows the same supplier case as the detailed 2:57 walkthrough:
`d587e091-7cf7-4aea-b34e-da5502ad64f6`. Its source is the original October 3 browser
recording. The new edit opens with the observed worker crash, then explains the
approval, post-commit boundary, redelivery, and unchanged business records.

Recorded UI pixels are cropped and enlarged; the diagram, headings, animated
outlines, and progress line are editorial graphics. The crash monitor is a local
harness view. It does not give the browser Docker authority. The records are from
real Temporal and PostgreSQL services; the supplier, advisory provider, and locally
stored notification have the scope described in the original recovery proof.

There is one burned-in English caption band and no additional subtitle stream.
The downloadable SRT is separate. Narration is project-focused and contains no
personal introduction. The longer walkthrough and its original verification
inventory remain available.

## Reproduce

`story.json` supplies the recorded filenames, exact source offsets, crop rectangles,
headlines, and spoken script. Scene lengths follow the generated speech; short
recorded clips hold their final frame when narration continues. The approval click
is retained at its original speed. The opening is a preview of the observed crash;
the subsequent scenes explain the same recorded case.

Use Python 3.12, `kokoro-onnx==0.6.0`, NumPy, ONNX Runtime, SoundFile, and Pillow.
FFmpeg needs libx264 and libass. DejaVu Sans must be installed. Media dependencies
are separate from the application's runtime dependencies.

```bash
python docs/demo-production/render.py \
  --raw /path/to/original/browser-recordings \
  --tools /path/to/kokoro-model-files \
  --work /tmp/survive-overview \
  --output /tmp/overview.mp4
```

The raw captures, generated WAV files, and model weights stay outside the repository.
The source recording hashes are retained in the [review record](review.json).

Narration uses the local Kokoro `am_michael` voice at speed 1.0. The
[Kokoro model](https://huggingface.co/hexgrad/Kokoro-82M) declares Apache-2.0;
the [Kokoro ONNX wrapper](https://github.com/thewh1teagle/kokoro-onnx/blob/main/LICENSE)
is MIT-licensed. Model weights and font files are not redistributed.
The model assets from the [model-file release](https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.1)
have these SHA-256 values:

| File | SHA-256 |
| --- | --- |
| `kokoro-v1.0.onnx` | `beb0d1848dee9a49da392cc3df26958d46cfa35d321edf434f52949153f0df3a` |
| `voices-v1.0.bin` | `bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d` |

## Review

The [review record](review.json) binds the finished video to its SHA-256 and source
recordings. It records decoding, caption timing, speech recognition, audio
synchronization, and browser playback checks. Automatic transcription is checked
against the script; it is not a claim of human listening.

`npm run check` verifies both media inventories and binds the short overview to the
same case as the original post-commit crash proof. The original 24-case evaluation
snapshot remains separate evidence; it is not represented as a new evaluation run.
