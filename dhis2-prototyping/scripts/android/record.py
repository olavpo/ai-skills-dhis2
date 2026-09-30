#!/usr/bin/env python3
"""Run a Maestro flow on a device while recording it, one screen recording per phase,
then burn in captions, a running tap/typing counter and a title.

    record.py PHASES_DIR --out report/ [--title "Option A · household 1 · Capture 3.4.2"]
              [--short] [--no-record] [--serial emulator-5554] [--ready "Home"]
    record.py FLOW.yaml --out report/          # a single file = a single phase

PHASES_DIR is what `maestro_flow.Flow.write_phases()` writes: NN.yaml per caption plus
phases.json. Each phase runs as its own `maestro test`, inside its own `screenrecord`.
That is deliberate: on the emulator `screenrecord` does not keep wall-clock time
(static stretches are compressed, a chunk's static tail is dropped), so captions timed
by wall clock across one long recording drift by 10-20 s. With one clip per phase the
caption boundaries are exact. Each clip is then padded with its last frame to the
phase's wall-clock length (a static screen can yield a single frame), so the counter
inside a phase follows the host clock too.

Output in --out:
  videos/<name>.mp4         full length, H.264, under ~10 MB
  videos/<name>_1min.mp4    with --short: uniformly sped up to about a minute (labelled)
  runs/<name>.json          per-command log, per-phase clip durations, caption times in
                            the rendered video ({"captions": [{"t", "text"}]}), counts
  runs/<name>_error.png     Maestro's last screenshot when a command failed

Environment: ANDROID_SERIAL (device), MAESTRO (path to the maestro binary, default
`maestro`), FFMPEG (default: imageio-ffmpeg's binary, then `ffmpeg`), JAVA_HOME,
REC_TMP (scratch dir). Syncing and reading the data back are separate steps: sync only
after a flow that succeeded, and verify on the server.
"""
import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

REC_LOOP = """
rm -f /sdcard/rec_*.mp4 /sdcard/stoprec
i=0
while [ ! -f /sdcard/stoprec ]; do
  screenrecord --time-limit 175 --bit-rate 3000000 /sdcard/rec_$i.mp4
  i=$((i+1))
done
"""

ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,DejaVu Sans,{cap},&H00FFFFFF,&H00FFFFFF,&H00000000,&HB0202020,1,0,0,0,100,100,0,0,3,{pad},0,2,30,30,{mv},1
Style: Count,DejaVu Sans,{cnt},&H0000E5FF,&H00FFFFFF,&H00000000,&HB0202020,1,0,0,0,100,100,0,0,3,{pad2},0,9,20,20,{mt},1
Style: Title,DejaVu Sans,{ttl},&H00FFFFFF,&H00FFFFFF,&H00000000,&HB0202020,0,0,0,0,100,100,0,0,3,{pad2},0,7,20,20,{mt},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

TAP_KEYS = ("tapOnElement", "tapOnPoint", "tapOnPointV2", "hideKeyboardCommand", "backPressCommand")


# ------------------------------------------------------------------ tools
def ffmpeg_bin():
    if os.environ.get("FFMPEG"):
        return os.environ["FFMPEG"]
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return shutil.which("ffmpeg") or sys.exit("no ffmpeg: pip install imageio-ffmpeg, or set FFMPEG")


def adb(serial, *args, **kw):
    cmd = ["adb"] + (["-s", serial] if serial else []) + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def probe(ff, path):
    """(duration_s, width, height) from ffmpeg's banner."""
    err = subprocess.run([ff, "-i", path], capture_output=True, text=True).stderr
    dur, w, h = None, 720, 1600
    for line in err.splitlines():
        if "Duration:" in line and "N/A" not in line.split("Duration:")[1].split(",")[0]:
            dur = hms(line.split("Duration:")[1].split(",")[0])
        if "Video:" in line:
            for tok in line.replace(",", " ").split():
                if "x" in tok and tok.split("x")[0].isdigit() and tok.split("x")[1].isdigit():
                    w, h = int(tok.split("x")[0]), int(tok.split("x")[1])
                    break
    if dur is None:
        # a very short or static clip can lack a duration in its header: decode to measure
        err = subprocess.run([ff, "-i", path, "-f", "null", "-"], capture_output=True, text=True).stderr
        times = [t.split()[0] for t in err.split("time=")[1:]]
        dur = hms(times[-1]) if times and ":" in times[-1] else 0.0
    return dur, w, h


def hms(text):
    hh, mm, ss = text.strip().split(":")
    return int(hh) * 3600 + int(mm) * 60 + float(ss)


# -------------------------------------------------------------- recording
class Recorder:
    def __init__(self, serial, tmp):
        self.serial, self.tmp, self.proc = serial, tmp, None
        path = os.path.join(tmp, "rec.sh")
        with open(path, "w") as f:
            f.write(REC_LOOP)
        adb(serial, "push", path, "/sdcard/rec.sh")

    def start(self):
        cmd = ["adb"] + (["-s", self.serial] if self.serial else []) + ["shell", "sh /sdcard/rec.sh"]
        self.t_start = time.time()
        self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.5)          # screenrecord needs a moment before the first frame
        return self.t_start

    def stop(self, dest_dir):
        """Stop with the flag file + SIGINT (so the last chunk is finalised), pull chunks.
        Returns (chunk paths, seconds the phase was recorded on the host clock)."""
        wall = time.time() - self.t_start
        adb(self.serial, "shell", "touch /sdcard/stoprec")
        adb(self.serial, "shell", "pkill -INT screenrecord")
        try:
            self.proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        time.sleep(1.0)
        os.makedirs(dest_dir, exist_ok=True)
        names = adb(self.serial, "shell", "ls /sdcard/rec_*.mp4").stdout.split()
        out = []
        for n in sorted(names, key=lambda s: int(s.rsplit("_", 1)[1][:-4])):
            adb(self.serial, "pull", n, dest_dir)
            out.append(os.path.join(dest_dir, os.path.basename(n)))
        adb(self.serial, "shell", "rm -f /sdcard/rec_*.mp4 /sdcard/stoprec")
        return out, wall


def normalise(ff, chunks, wall, dst, tmp, fps=24):
    """One constant-frame-rate clip per phase, padded with its last frame to the wall-clock
    length. screenrecord drops static stretches (a static screen can yield a single
    frame), so without this a phase's clip is shorter than the phase and every later
    caption and counter step lands early."""
    src = chunks[0]
    if len(chunks) > 1:
        src = os.path.join(tmp, os.path.basename(dst) + ".cat.mp4")
        concat(ff, chunks, src, tmp)
    have = probe(ff, src)[0]
    last = dst + ".last.png"
    subprocess.run([ff, "-y", "-loglevel", "error", "-i", src, "-update", "1", last], check=True)
    even = "scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1,format=yuv420p"
    enc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", dst]
    pad = wall - have
    if have < 0.3:            # (nearly) a single frame: the phase is a still
        cmd = ["-loop", "1", "-framerate", str(fps), "-t", f"{wall:.2f}", "-i", last, "-vf", even]
    elif pad < 0.1:
        cmd = ["-i", src, "-an", "-vf", f"fps={fps},{even}"]
    else:
        cmd = ["-i", src, "-loop", "1", "-framerate", str(fps), "-t", f"{pad:.2f}", "-i", last,
               "-filter_complex", f"[0:v]fps={fps},{even}[a];[1:v]fps={fps},{even}[b];[a][b]concat=n=2:v=1[o]",
               "-map", "[o]"]
    subprocess.run([ff, "-y", "-loglevel", "error", *cmd, *enc], check=True)
    return dst


def run_maestro(flow, serial, dbg):
    shutil.rmtree(dbg, ignore_errors=True)
    maestro = os.environ.get("MAESTRO", "maestro")
    env = dict(os.environ, MAESTRO_CLI_NO_ANALYTICS="1", MAESTRO_CLI_ANALYSIS_NOTIFICATION_DISABLED="true")
    cmd = [maestro] + (["--device", serial] if serial else []) + ["test", flow, "--debug-output", dbg]
    r = subprocess.run(cmd, capture_output=True, text=True, env=env)
    # commands*.json is written under a hidden .maestro directory
    files = glob.glob(os.path.join(dbg, "**", "commands*.json"), recursive=True, include_hidden=True)
    shots = sorted(glob.glob(os.path.join(dbg, "**", "screenshot-*.png"), recursive=True, include_hidden=True))
    cmds = json.load(open(files[0])) if files else []
    failed = [c for c in cmds if c.get("metadata", {}).get("status") == "FAILED"]
    if r.returncode and not failed:
        failed = [{"command": {"maestro": f"exit {r.returncode}"}, "metadata": {}}]
    return cmds, failed, (r.stdout + r.stderr)[-3000:], shots[-1] if shots else None


# --------------------------------------------------------------- captions
def ass_time(t):
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def ass_escape(s):
    return s.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", " ")


def timeline(phases):
    """Place captions and counter steps on the concatenated video.

    phases: [{"caption", "commands", "offset", "clip", "t_start"}]: offset/clip are the
    phase's start and length in the video, t_start the host time its recording began
    (clips are padded to wall-clock length, so host time maps 1:1 inside a phase).
    Without a recording (t_start None) phases are laid end to end by command time."""
    caps, counter = [], []
    taps = chars = 0
    for p in phases:
        if p["caption"]:
            caps.append((p["offset"], p["offset"] + p["clip"], p["caption"]))
        done = [c for c in p["commands"] if c.get("metadata", {}).get("status") in ("COMPLETED", "WARNED")]
        if not done:
            continue
        t0 = p.get("t_start") or done[0]["metadata"]["timestamp"] / 1000.0
        for c in done:
            md, cmd = c["metadata"], c["command"]
            end = (md["timestamp"] + md.get("duration", 0)) / 1000.0 - t0
            t = p["offset"] + (min(p["clip"], max(0.0, end)) if p["clip"] else max(0.0, end))
            if any(k in cmd for k in TAP_KEYS):
                taps += 1
                counter.append((t, taps, chars))
            elif "inputTextCommand" in cmd:
                chars += len(cmd["inputTextCommand"].get("text", ""))
                counter.append((t, taps, chars))
    return caps, counter, taps, chars


def write_ass(dst, caps, counter, duration, title, w, h, speed=1.0):
    k = h / 1600.0
    hdr = ASS_HEADER.format(w=w, h=h, cap=int(40 * k), cnt=int(30 * k), ttl=int(26 * k), pad=int(14 * k),
                            pad2=int(10 * k), mv=int(120 * k), mt=int(190 * k))
    ev = [("Title", 0, duration, title)]
    ev += [("Cap", s, e, txt) for s, e, txt in caps]
    for i, (t, n, ch) in enumerate(counter):
        end = counter[i + 1][0] if i + 1 < len(counter) else duration
        ev.append(("Count", t, end, f"Taps {n} · Typed {ch}"))
    with open(dst, "w") as f:
        f.write(hdr)
        for style, s, e, txt in ev:
            f.write(f"Dialogue: 0,{ass_time(s / speed)},{ass_time(e / speed)},{style},,0,0,0,,{ass_escape(txt)}\n")


def render(ff, src, subs, dst, speed=1.0, crf=30, width=720, max_bytes=9.5e6):
    vf = ([f"setpts=PTS/{speed}"] if speed != 1.0 else []) + [f"subtitles={subs}", f"scale={width}:-2"]
    while True:
        subprocess.run([ff, "-y", "-loglevel", "error", "-i", src, "-an", "-vf", ",".join(vf), "-r", "24",
                        "-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-pix_fmt", "yuv420p",
                        "-movflags", "+faststart", dst], check=True)
        if os.path.getsize(dst) <= max_bytes or crf >= 40:
            return crf
        crf += 3


def concat(ff, clips, dst, tmp):
    lst = os.path.join(tmp, "concat.txt")
    with open(lst, "w") as f:
        for c in clips:
            f.write(f"file '{c}'\n")
    subprocess.run([ff, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", dst],
                   check=True)


# ------------------------------------------------------------------- main
def load_phases(src):
    if os.path.isdir(src):
        meta = json.load(open(os.path.join(src, "phases.json")))
        return meta.get("flow") or os.path.basename(src.rstrip("/")), \
            [(os.path.join(src, p["file"]), p["caption"]) for p in meta["phases"]], meta.get("counts")
    return os.path.basename(src).rsplit(".", 1)[0], [(src, "")], None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("flow", help="phases directory from Flow.write_phases(), or one flow .yaml")
    ap.add_argument("--out", default="report")
    ap.add_argument("--name", help="output base name (default: the flow name)")
    ap.add_argument("--title", help="burned-in title; put the app version in it")
    ap.add_argument("--short", action="store_true", help="also render a ~1 minute sped-up version")
    ap.add_argument("--no-record", action="store_true", help="dry run: Maestro only, no video")
    ap.add_argument("--serial", default=os.environ.get("ANDROID_SERIAL"))
    ap.add_argument("--app-id", default="com.dhis2")
    ap.add_argument("--ready", help="regex that must be on screen before starting; the app is "
                                    "relaunched until it appears (e.g. the program list's title)")
    a = ap.parse_args()

    name, phase_files, planned = load_phases(a.flow)
    name = a.name or name
    tmp = os.path.join(os.environ.get("REC_TMP") or os.path.join(tempfile.gettempdir(), "rec"), name)
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    videos, runs = os.path.join(a.out, "videos"), os.path.join(a.out, "runs")
    os.makedirs(videos, exist_ok=True)
    os.makedirs(runs, exist_ok=True)

    if a.ready:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from ui import wait_for
        for _ in range(5):
            if wait_for(a.ready, a.serial, timeout=20):
                break
            adb(a.serial, "shell", "am", "force-stop", a.app_id)
            adb(a.serial, "shell", "monkey", "-p", a.app_id, "1")
        else:
            sys.exit(f"{a.ready!r} never appeared on screen")

    rec = None if a.no_record else Recorder(a.serial, tmp)
    phases, clips, failed, t_start = [], [], [], time.time()
    for i, (flow, caption) in enumerate(phase_files):
        t_rec = rec.start() if rec else None
        cmds, bad, out, shot = run_maestro(flow, a.serial, os.path.join(tmp, f"dbg{i}"))
        chunks, wall = rec.stop(os.path.join(tmp, f"clip{i}")) if rec else ([], 0.0)
        phases.append({"caption": caption, "file": os.path.basename(flow), "commands": cmds,
                       "chunks": chunks, "wall": round(wall, 2), "t_start": t_rec})
        if bad:
            failed = bad
            if shot:
                shutil.copy(shot, os.path.join(runs, f"{name}_error.png"))
            print(out)
            break

    ff = ffmpeg_bin() if rec else None
    offset = 0.0
    for i, p in enumerate(phases):
        chunks = p.pop("chunks")
        p["clip"] = 0.0
        if ff and chunks:
            clips.append(normalise(ff, chunks, p["wall"], os.path.join(tmp, f"phase{i}.mp4"), tmp))
            p["clip"] = round(probe(ff, clips[-1])[0], 2)
        p["offset"] = round(offset, 2)
        offset += p["clip"]
    caps, counter, taps, chars = timeline(phases)
    run = {"flow": name, "ok": not failed, "wall_seconds": round(time.time() - t_start, 1),
           "failed": [json.dumps(f["command"])[:300] for f in failed],
           "counts_planned": planned, "taps_run": taps, "chars_run": chars,
           "captions": [{"t": s, "text": txt} for s, _, txt in caps]}
    with open(os.path.join(runs, f"{name}.json"), "w") as f:
        json.dump({"run": run, "phases": phases}, f)
    print(json.dumps(run)[:600])
    if failed:
        sys.exit(1)
    if not clips:
        return

    raw = os.path.join(tmp, "raw.mp4")
    concat(ff, clips, raw, tmp)
    dur, w, h = probe(ff, raw)
    title = a.title or name
    subs = os.path.join(tmp, "full.ass")
    write_ass(subs, caps, counter, dur, title, w, h)
    render(ff, raw, subs, os.path.join(videos, f"{name}.mp4"))
    if a.short and dur > 70:
        speed = round(dur / 60.0, 2)
        subs2 = os.path.join(tmp, "short.ass")
        write_ass(subs2, caps, counter, dur, f"{title}  (shown at {speed}× speed)", w, h, speed=speed)
        render(ff, raw, subs2, os.path.join(videos, f"{name}_1min.mp4"), speed=speed)
    print("video", round(dur, 1), "s; taps", taps, "typed", chars)


if __name__ == "__main__":
    main()
