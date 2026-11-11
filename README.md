# Slack GIF Compressor

Compress an animated GIF to a byte budget while keeping as much quality as the
budget allows. The default is a **5 MiB message GIF**; the `emoji` preset targets
Slack's **128 KiB, 128x128, 50-frame** custom-emoji limit.

The tool measures real encodes instead of guessing: it brackets encoder quality,
runs a local secant search, and usually finishes in 3-7 encodes (hard cap 10).
It prefers [gifski](https://gif.ski) for quality-per-byte, falls back to a tuned
two-pass ffmpeg palette pipeline, and keeps Pillow as a last resort.

## Slack limits

- **Message/file uploads:** Slack documents a 1 GB per-file limit. 5 MiB is a
  practical budget for fast inline previews, not a documented hard cap.
- **Custom emoji:** under 128 KB (131,072 bytes), square, and GIFs are limited
  to 50 frames. Slack resizes emoji to 128x128 and re-checks the byte limit, so
  this tool aims slightly under the limit.
- **Message GIFs** keep the original GIF and animate inline; animated WebP and
  MP4 are not the default because they lose GIF autoplay/loop semantics.

See [Add files to Slack](https://slack.com/help/articles/201330736-Add-files-to-Slack)
and [Add custom emoji](https://slack.com/help/articles/206870177-Add-custom-emoji-and-aliases-to-your-workspace).

## Install

```bash
# macOS
brew install gifski ffmpeg

# Ubuntu/Debian
sudo apt install ffmpeg
# gifski is optional; install it from your package manager or from gif.ski

# the Python package (no mandatory Python runtime dependencies)
pipx install .
# or
uvx --from . gifc
```

`gifsicle` is optional. When present, it is used only as a lossy post-pass on
ffmpeg output; it is never run on gifski output (it makes those files larger).

Pillow is an optional extra and the last-resort backend:

```bash
pip install '.[pillow]'
```

## Usage

```bash
# Message GIF: default 5 MiB target
gifc meme.gif

# Custom emoji: 128 KiB, 128x128, <= 50 frames, alpha preserved
gifc meme.gif --preset emoji

# Explicit byte budget (binary units: 2MB = 2 * 1024 * 1024)
gifc meme.gif --max-size 2MB

# Fixed quality, one encode, no target search
gifc meme.gif --quality 80

# Fixed quality while searching dimensions for a target
gifc meme.gif --quality 80 --max-size 1MB

# Script-friendly output
gifc meme.gif --json
gifc meme.gif --dry-run
```

`gif_compressor.py` remains as a compatibility shim, so the old invocation
still works.

### Presets

| Preset    | Default | Target                                | Constraints                                       |
| --------- | ------- | ------------------------------------- | ------------------------------------------------- |
| `message` | yes     | 5 MiB (5,242,880 B)                   | max 1024x1024, max 30 fps                         |
| `emoji`   | no      | 128 KiB (131,072 B), 3% safety margin | exactly 128x128, max 50 frames, max 15 fps, alpha |
| `custom`  | no      | `--max-size` or `--quality` required  | user                                              |

Units are binary: `1 KB = 1024 B`, `1 MB = 1024^2 B`. A plain number is KB, so
`-t 128` still means the old 128 KB target. `--fit pad` is an alias for
`contain`.

### Quality modes

When the source is too large even at the minimum quality, the search has to
reduce dimensions or frame rate:

- `balanced` (default): spends the reduction on both, preserving resolution
  first.
- `resolution`: reduces fps first.
- `motion`: reduces width first.

### How the search works

1. If the source already fits and satisfies the preset, copy it byte-for-byte
   (0 encodes).
2. Encode once at native dimensions and the minimum quality.
3. If it fits, bracket quality upward and run a local secant until the result is
   close to the budget or the quality bracket is one point wide.
4. If it does not fit, estimate a width/fps reduction, measure the real result,
   correct with the measured slope, and repeat up to four times.
5. If the floors are exhausted, try one last encode at the minimum dimensions
   and quality; otherwise emit the smallest measured result and report failure.

The success threshold is the requested target. The internal 1% (message) or 3%
(emoji) margin is only the search's aim, so an output between the margin and
the target is still a success.

### Exit codes

| Code | Meaning                                                        |
| ---- | -------------------------------------------------------------- |
| 0    | Success; output is at or under target                          |
| 1    | Best effort; output was written but exceeds target             |
| 2    | Usage error (bad arguments, unsafe paths, unimplemented flags) |
| 3    | Processing error (probe/backend/validation/output failure)     |

Progress and warnings go to stderr. The final summary or `--json` object goes
to stdout.

## Safety

- Refuses to write over the input, including hardlinks and symlinks.
- Refuses to overwrite an existing output unless `--force` is passed.
- Writes candidates to a temporary directory and replaces the output atomically.
- Never claims an output that was not written.
- Caps input at 10,000 frames, 100M pixels per frame, and 600 seconds unless
  `--allow-large` is passed.

## Limitations

- Variable per-frame delays may be normalized by the encoder. The tool warns;
  exact per-frame timing preservation is future work.
- gifski is optional. Without it, the ffmpeg fallback reaches roughly 340-425 px
  at 5 MiB on the reference GIF; with gifsicle's lossy post-pass, roughly
  360-425 px.
- Pillow has no rate-control knob; it is used only when neither gifski nor
  ffmpeg is available, or when forced.
- 5 MB is a practical message budget, not a Slack hard limit.
- `--trim`, `--output-dir`, crop, batch, and MP4 output are not implemented in
  this version. They return exit 2 rather than being silently ignored.

## Migration from 1.x

The default target changed from **128 KB to 5 MiB** to match normal message
GIFs. To keep the old behavior, use `--preset emoji` or `-t 128`.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/ruff check .
.venv/bin/python -m pytest -q
```

The backend integration tests skip cleanly when their binary is absent. To run
the real-GIF acceptance benchmark:

```bash
SLACKGIF_BENCH_GIF=~/Downloads/meme723-1794423241586.gif \
  .venv/bin/python scripts/benchmark.py --assert
```
