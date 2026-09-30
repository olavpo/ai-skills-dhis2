#!/usr/bin/env python3
"""One comparison video: several flow recordings side by side, starting together.

    grid.py OUT.mp4 A.mp4 B.mp4 C.mp4 … [--labels A,B,C] [--speed 8] [--cols 4]
            [--tile 270x600]

Every tile plays at the same speed factor and holds its last frame when it ends
(tpad), so a flow with more steps visibly runs longer: the difference in effort is
readable at a glance. Empty cells are filled with a plain card. --labels draws a
small label per tile via ASS subtitles (the imageio-ffmpeg build has no drawtext).
FFMPEG env var, else imageio-ffmpeg.
"""
import argparse
import math
import os
import shutil
import subprocess
import sys
import tempfile


def ffmpeg_bin():
    if os.environ.get("FFMPEG"):
        return os.environ["FFMPEG"]
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return shutil.which("ffmpeg") or sys.exit("no ffmpeg: pip install imageio-ffmpeg, or set FFMPEG")


def duration(ff, path):
    err = subprocess.run([ff, "-i", path], capture_output=True, text=True).stderr
    for line in err.splitlines():
        if "Duration:" in line:
            h, m, s = line.split("Duration:")[1].split(",")[0].strip().split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
    sys.exit(f"cannot read duration of {path}")


def labels_ass(path, labels, cols, w, h, total):
    end = f"0:{int(total // 60):02d}:{total % 60:05.2f}" if total < 3600 else "9:59:59.00"
    with open(path, "w") as f:
        f.write(f"[Script Info]\nScriptType: v4.00+\nPlayResX: {cols * w}\nPlayResY: "
                f"{math.ceil(len(labels) / cols) * h}\n\n[V4+ Styles]\n"
                "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
                "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
                "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
                "Style: L,DejaVu Sans,22,&H00FFFFFF,&H00FFFFFF,&H00000000,&HB0202020,1,0,0,0,100,100,0,0,3,6,0,"
                "7,0,0,0,1\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
                "Effect, Text\n")
        for i, lab in enumerate(labels):
            x, y = (i % cols) * w + 8, (i // cols) * h + 8
            f.write(f"Dialogue: 0,0:00:00.00,{end},L,,0,0,0,,{{\\pos({x},{y})}}{lab}\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out")
    ap.add_argument("videos", nargs="+")
    ap.add_argument("--labels", help="comma-separated, one per video")
    ap.add_argument("--speed", type=float, default=8.0)
    ap.add_argument("--cols", type=int, default=4)
    ap.add_argument("--tile", default="270x600")
    ap.add_argument("--crf", type=int, default=32)
    a = ap.parse_args()
    ff = ffmpeg_bin()
    w, h = (int(v) for v in a.tile.split("x"))
    n = len(a.videos)
    cells = math.ceil(n / a.cols) * a.cols
    total = max(duration(ff, v) for v in a.videos) / a.speed + 2

    inputs, filters = [], []
    for i, v in enumerate(a.videos):
        inputs += ["-i", v]
        filters.append(f"[{i}:v]setpts=PTS/{a.speed},scale={w}:{h}:force_original_aspect_ratio=decrease,"
                       f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,"
                       f"tpad=stop_mode=clone:stop_duration={total:.1f}[v{i}]")
    for j in range(n, cells):
        filters.append(f"color=c=0x18231f:s={w}x{h}:d={total:.1f}[v{j}]")
    layout = "|".join(f"{(i % a.cols) * w}_{(i // a.cols) * h}" for i in range(cells))
    tail = "[g]"
    filters.append("".join(f"[v{i}]" for i in range(cells)) + f"xstack=inputs={cells}:layout={layout}:shortest=0{tail}")
    if a.labels:
        labels = a.labels.split(",")
        if len(labels) != n:
            ap.error("--labels needs one label per video")
        ass = os.path.join(tempfile.mkdtemp(), "labels.ass")
        labels_ass(ass, labels, a.cols, w, h, total)
        filters.append(f"[g]subtitles={ass}[out]")
    else:
        filters.append("[g]null[out]")
    subprocess.run([ff, "-y", "-loglevel", "error", *inputs, "-filter_complex", ";".join(filters),
                    "-map", "[out]", "-t", f"{total:.1f}", "-r", "20", "-c:v", "libx264", "-crf", str(a.crf),
                    "-preset", "medium", "-pix_fmt", "yuv420p", "-movflags", "+faststart", a.out], check=True)
    print(a.out, round(total, 1), "s", os.path.getsize(a.out) // 1024, "KB")


if __name__ == "__main__":
    main()
