"""Join PNG frames into a looping GIF (make animation).

Usage: frames-to-gif.py <frames dir> <out.gif> [fps] [width] [step]
(step 2 keeps every second frame)
One 256-colour palette for all frames (from eight of them), no dithering:
small files and no flicker.
"""

import sys
from pathlib import Path

from PIL import Image

frames_dir, out = Path(sys.argv[1]), Path(sys.argv[2])
fps = int(sys.argv[3]) if len(sys.argv) > 3 else 10
width = int(sys.argv[4]) if len(sys.argv) > 4 else 960
step = int(sys.argv[5]) if len(sys.argv) > 5 else 1

files = sorted(frames_dir.glob("*.png"))[::step]
first = Image.open(files[0])
height = round(first.height * width / first.width)
frames = [
    Image.open(f).convert("RGB").resize((width, height), Image.LANCZOS) for f in files
]
picks = range(0, len(frames), max(1, len(frames) // 8))
sample = Image.new("RGB", (width, height * len(picks)))
for k, i in enumerate(picks):
    sample.paste(frames[i], (0, height * k))
palette = sample.quantize(colors=256, method=Image.Quantize.MAXCOVERAGE)
gif = [f.quantize(palette=palette, dither=Image.Dither.NONE) for f in frames]
out.parent.mkdir(parents=True, exist_ok=True)
gif[0].save(
    out,
    save_all=True,
    append_images=gif[1:],
    duration=1000 // fps,
    loop=0,
    optimize=True,
)
print(f"{out}: {len(gif)} frames, {out.stat().st_size // 1024} KB")
