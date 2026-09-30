#!/usr/bin/env python3
"""Cut a short GIF from a rendered video, starting at a caption.

    gifs.py VIDEO OUT.gif --run runs/A_hh1.json --caption "Register the second child" \
            [--duration 14] [--width 300] [--speed 2.5]
    gifs.py VIDEO OUT.gif --at 42.5 --duration 12 --width 640

The caption time comes from the run log: `{"run": {"captions": [{"t", "text"}]}}` as
android/record.py writes it (times in the rendered video), or a top-level
`{"captions": [...]}` / `{"log": [{"kind": "CAP", "text", "t"}]}`. --caption matches the
start of the caption text. Give the speed of the source video's time base: for a video
rendered at normal speed, --speed 2.5 plays the GIF 2.5× faster.

Defaults that kept GIFs at 400-900 KB: 8 fps, a 96-colour palette with Bayer dither,
640 px wide for web, 300 px for phone screens. FFMPEG env var, else imageio-ffmpeg.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys


def ffmpeg_bin():
    if os.environ.get("FFMPEG"):
        return os.environ["FFMPEG"]
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return shutil.which("ffmpeg") or sys.exit("no ffmpeg: pip install imageio-ffmpeg, or set FFMPEG")


def caption_time(run_file, prefix):
    d = json.load(open(run_file))
    caps = (d.get("run") or {}).get("captions") or d.get("captions")
    if caps:
        for c in caps:
            if c["text"].startswith(prefix):
                return c["t"]
    for e in d.get("log", []):
        if e.get("kind") == "CAP" and e.get("text", "").startswith(prefix):
            return e["t"]
    return None


def make_gif(ff, src, start, duration, dst, width, speed=1.0, fps=8, colours=96):
    vf = (f"setpts=PTS/{speed},fps={fps},scale={width}:-1:flags=lanczos,split[a][b];"
          f"[a]palettegen=max_colors={colours}[p];[b][p]paletteuse=dither=bayer:bayer_scale=4")
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    subprocess.run([ff, "-y", "-loglevel", "error", "-ss", str(max(0.0, start - 0.5)),
                    "-t", str(duration * speed), "-i", src, "-vf", vf, dst], check=True)
    return os.path.getsize(dst)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("out")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--at", type=float, help="start time in seconds")
    g.add_argument("--caption", help="start at the caption starting with this text (needs --run)")
    ap.add_argument("--run", help="run log JSON with caption times")
    ap.add_argument("--duration", type=float, default=12, help="seconds of the GIF (after speed-up)")
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--fps", type=int, default=8)
    a = ap.parse_args()
    start = a.at
    if a.caption:
        if not a.run:
            ap.error("--caption needs --run")
        start = caption_time(a.run, a.caption)
        if start is None:
            sys.exit(f"caption {a.caption!r} not found in {a.run}")
    size = make_gif(ffmpeg_bin(), a.video, start, a.duration, a.out, a.width, a.speed, a.fps)
    print(a.out, size // 1024, "KB")


if __name__ == "__main__":
    main()
