# GIF Compressor for Slack

A robust Python tool to compress animated GIFs to under 128 KB for Slack compatibility. Uses an iterative optimization approach with ffmpeg (preferred) or Pillow as a fallback.

## Features

- **Iterative Optimization** - Automatically finds the best compression settings
- **Multiple Strategies** - Scaling, frame dropping, and color palette reduction
- **Dual Backend** - ffmpeg for quality, Pillow as fallback
- **Progress Tracking** - Real-time feedback on compression progress
- **Animation Preservation** - Maintains loop metadata and animation integrity

## Installation

### Prerequisites

**Option 1: ffmpeg (Recommended)**

```bash
# macOS
brew install ffmpeg

# Ubuntu/Debian
sudo apt install ffmpeg

# Windows (via Chocolatey)
choco install ffmpeg
```

**Option 2: Pillow (Fallback)**

```bash
pip install Pillow
```

### Clone and Run

```bash
git clone <repository-url>
cd slack_gif_normalizer
python gif_compressor.py --help
```

## Usage

### Basic Usage

```bash
# Compress to 128 KB (default), outputs input_compressed.gif
python gif_compressor.py input.gif

# Specify output path
python gif_compressor.py input.gif -o output.gif

# Custom target size
python gif_compressor.py input.gif -t 64

# Force Pillow backend
python gif_compressor.py input.gif --pillow
```

### Command-Line Options

| Option | Description |
|--------|-------------|
| `input` | Input GIF file path (required) |
| `-o, --output` | Output file path (default: `input_compressed.gif`) |
| `-t, --target` | Target size in KB (default: 128) |
| `--pillow` | Force Pillow backend instead of ffmpeg |

### Examples

```bash
# Compress a large GIF for Slack
python gif_compressor.py reaction.gif -o slack_ready.gif

# Aggressive compression for email
python gif_compressor.py animation.gif -t 50

# Quick compression using Pillow
python gif_compressor.py meme.gif --pillow -o meme_small.gif
```

## How It Works

The compressor uses a 4-phase optimization strategy:

### Phase 1: Scaling

Iteratively reduces dimensions by 10% until reaching a 200px minimum width.

### Phase 2: Frame Dropping

Reduces frame count by keeping every Nth frame (2nd, 3rd, or 4th), adjusting timing to maintain animation speed.

### Phase 3: Color Reduction

Reduces the color palette: 256 → 128 → 64 → 32 colors using optimized dithering.

### Phase 4: Aggressive Optimization

Combines all techniques with more extreme settings if the target still isn't reached.

```
[ffmpeg] Starting GIF compression...
  Original: 480x360, 45 frames, 10.0 fps
  Original size: 2048.0 KB

[Phase 1] Scaling down...
  → Scale to 432px: 1520.3 KB (target: 128 KB)
  → Scale to 388px: 1180.5 KB (target: 128 KB)
  ...

[Phase 2] Dropping frames...
  → Keep every 2th frame: 450.2 KB (target: 128 KB)
  ...

==================================================
✓ Success! Compressed to 118.4 KB (94.2% reduction)
  Output: /path/to/output.gif
```

## Configuration

Default settings can be modified in the script:

```python
DEFAULT_TARGET_KB = 128   # Target file size
MIN_WIDTH = 200           # Minimum width in pixels
SCALE_STEP = 0.9          # 10% reduction per iteration
MIN_COLORS = 32           # Minimum color palette
MAX_FRAME_SKIP = 4        # Maximum frame skip (keep every Nth)
```

## Troubleshooting

### Cannot reach target size

If the target cannot be reached, the script outputs the smallest possible version with suggestions:

```
⚠ Warning: Could not reach target 128 KB
  Best result: 156.3 KB (85.2% reduction)

Tips to further reduce size:
  - Crop unnecessary parts of the GIF
  - Shorten the animation duration
  - Consider converting to a video format (MP4)
```

### ffmpeg not found

The script automatically falls back to Pillow. For better quality, install ffmpeg:

```bash
# Verify installation
ffmpeg -version
```

### Pillow not installed

```bash
pip install Pillow
```

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success - target size reached |
| 1 | Warning - best effort output, target not reached |

## License

MIT License

## Contributing

Contributions welcome! Please open an issue or submit a pull request.
