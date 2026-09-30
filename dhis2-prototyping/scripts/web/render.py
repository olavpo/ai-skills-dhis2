#!/usr/bin/env python3
"""Render a recorded web flow into a captioned MP4 with a running click counter.

    python3 render.py runs/web_example.json runs/web_example.webm out.mp4 \
        [--title "Option C · household 1 · web Capture 2.42"] [--short]

Input: the JSON log written by runner.run_flow / Web.save_log and the raw .webm.
Captions come from w.cap(); the counter from clicks and typed characters. Subtitles are ASS
(libass), burnt in with ffmpeg's `subtitles` filter: the imageio-ffmpeg static binary has no
drawtext but does have libass. --short also writes a ~1 minute version at a uniform speed-up,
with the factor in the title. Output: H.264, yuv420p, +faststart, CRF raised until < 10 MB.

ffmpeg: $FFMPEG, else imageio_ffmpeg's binary, else `ffmpeg` on PATH.
"""
import argparse
import json
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
        pass
    p = shutil.which("ffmpeg") or os.path.expanduser("~/bin/ffmpeg")
    if not os.path.exists(p):
        sys.exit("ffmpeg not found: pip install imageio-ffmpeg (in a venv) or set FFMPEG")
    return p


def ass_time(t):
    t = max(0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def probe_duration(path):
    r = subprocess.run([ffmpeg_bin(), "-i", path], capture_output=True, text=True)
    for line in r.stderr.splitlines():
        if "Duration:" in line:
            h, m, s = line.split("Duration:")[1].split(",")[0].strip().split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
    return 0.0


def render(src, subs, dst, speed=1.0, crf=30, width=1280, max_bytes=9.5e6):
    vf = ([f"setpts=PTS/{speed}"] if speed != 1.0 else []) + [f"subtitles={subs}", f"scale={width}:-2"]
    while True:
        subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-i", src, "-an", "-vf", ",".join(vf),
                        "-r", "24", "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
                        "-pix_fmt", "yuv420p", "-movflags", "+faststart", dst], check=True)
        if os.path.getsize(dst) <= max_bytes or crf >= 40:
            return
        crf += 3


HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,FreeSans,34,&H00FFFFFF,&H00FFFFFF,&H00000000,&HB0202020,1,0,0,0,100,100,0,0,3,12,0,2,30,30,40,1
Style: Count,FreeSans,26,&H0000E5FF,&H00FFFFFF,&H00000000,&HB0202020,1,0,0,0,100,100,0,0,3,8,0,9,20,20,130,1
Style: Title,FreeSans,22,&H00FFFFFF,&H00FFFFFF,&H00000000,&HB0202020,0,0,0,0,100,100,0,0,3,8,0,7,20,20,130,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def write_subs(log, duration, dst, title, speed=1.0, note=None, size=(1280, 800)):
    """Three styles: caption bottom, counter top right, title top left."""
    ev = []
    caps = [(e["t"], e["text"]) for e in log if e["kind"] == "CAP"]
    cnt = [(e["t"], e["taps"], e["chars"]) for e in log if e["kind"] in ("TAP", "TYPE")]
    for i, (t, txt) in enumerate(caps):
        ev.append(("Cap", t, caps[i + 1][0] if i + 1 < len(caps) else duration, txt))
    for i, (t, n, ch) in enumerate(cnt):
        ev.append(("Count", t, cnt[i + 1][0] if i + 1 < len(cnt) else duration, f"Clicks {n} · Typed {ch}"))
    ev.append(("Title", 0, duration, title + (f"  ({note})" if note else "")))
    with open(dst, "w") as fh:
        fh.write(HEADER.format(w=size[0], h=size[1]))
        for st, s, e, txt in ev:
            fh.write(f"Dialogue: 0,{ass_time(s / speed)},{ass_time(e / speed)},{st},,0,0,0,,{txt}\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log")
    ap.add_argument("video")
    ap.add_argument("out")
    ap.add_argument("--title", default="")
    ap.add_argument("--short", action="store_true", help="also write <out>_1min.mp4 if longer than 70 s")
    ap.add_argument("--width", type=int, default=1280)
    a = ap.parse_args()
    run = json.load(open(a.log))
    if run.get("error"):
        sys.exit(f"not rendering a failed flow: {run['error'][:200]}")
    title = a.title or run.get("flow", "")
    dur = probe_duration(a.video)
    tmp = tempfile.mkdtemp()
    s1 = os.path.join(tmp, "full.ass")
    write_subs(run["log"], dur, s1, title)
    render(a.video, s1, a.out, width=a.width)
    if a.short and dur > 70:
        speed = round(dur / 60.0, 2)
        s2 = os.path.join(tmp, "short.ass")
        write_subs(run["log"], dur, s2, title, speed=speed, note=f"shown at {speed}× speed")
        render(a.video, s2, os.path.splitext(a.out)[0] + "_1min.mp4", speed=speed, width=a.width)
    shutil.rmtree(tmp, ignore_errors=True)
    print("rendered", a.out, round(dur, 1), "s")


if __name__ == "__main__":
    main()
