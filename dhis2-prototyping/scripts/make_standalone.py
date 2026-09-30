#!/usr/bin/env python3
"""Export a published report (HTML + media folders) as one self-contained file.

    python3 make_standalone.py report/report_publish.html report/report_standalone.html

Every src/href to a file under the page's folder (.mp4, .gif, .png, .jpg) is
re-encoded smaller and inlined as a data: URI:
  - videos: H.264, CRF 32, 15 fps, no audio; portrait phone recordings
    ("android" in the name) 360 px wide, others 960 px
  - GIFs: become muted looping videos (about a fifth of the size)
  - PNG/JPEG: 1200 px wide JPEG, quality 78
  - <a href="x.mp4">label</a> links become <details> players: browsers refuse
    to navigate to data: URLs, so a plain link would do nothing
Re-encoded media are cached next to the output (.standalone_cache/), keyed on
path and mtime. Needs ffmpeg (on PATH, or `pip install imageio-ffmpeg`) and Pillow.
Keep the output out of git; rebuild it after every change to the report.
"""
import base64
import hashlib
import os
import re
import shutil
import subprocess
import sys

CSS = ("details.clip{margin:4px 0}details.clip summary{cursor:pointer}"
       "details.clip video,figure video{display:block;max-width:100%}")


def gif_to_video(m):
    """<img ... src="x.gif" ...> in any attribute order -> muted looping <video>."""
    attrs = m.group(1)
    src = re.search(r'\bsrc="([^":]+\.gif)"', attrs, re.I)
    if not src:
        return m.group(0)
    alt = re.search(r'\balt="([^"]*)"', attrs)
    label = f' aria-label="{alt.group(1)}"' if alt else ""
    return f'<video autoplay muted loop playsinline{label} src="{src.group(1)}"></video>'


def ffmpeg():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def main(src_html, out_html):
    root = os.path.dirname(os.path.abspath(src_html))
    cache = os.path.join(os.path.dirname(os.path.abspath(out_html)), ".standalone_cache")
    try:
        from PIL import Image
    except ImportError:
        sys.exit("needs Pillow: pip install pillow (and imageio-ffmpeg if ffmpeg is not on PATH)")
    ff = ffmpeg()

    def cached(rel, suffix, make):
        src = os.path.join(root, rel)
        key = hashlib.sha1(f"{rel}:{os.path.getmtime(src)}".encode()).hexdigest()[:12]
        out = os.path.join(cache, key + suffix)
        if not os.path.exists(out):
            os.makedirs(cache, exist_ok=True)
            make(src, out)
        return out

    def video(rel):
        width = 360 if "android" in rel and "grid" not in rel else 960

        def make(src, out):
            subprocess.run([ff, "-y", "-loglevel", "error", "-i", src,
                            "-vf", f"scale='min({width},iw)':-2,fps=15",
                            "-c:v", "libx264", "-preset", "slow", "-crf", "32",
                            "-pix_fmt", "yuv420p", "-an", "-movflags", "+faststart", out], check=True)
        return cached(rel, ".mp4", make), "video/mp4"

    def image(rel):
        def make(src, out):
            im = Image.open(src).convert("RGB")
            im.thumbnail((1200, 10000))
            im.save(out, "JPEG", quality=78, optimize=True)
        return cached(rel, ".jpg", make), "image/jpeg"

    def data_uri(rel):
        path, mime = video(rel) if rel.lower().endswith((".mp4", ".gif")) else image(rel)
        with open(path, "rb") as f:
            return f"data:{mime};base64," + base64.b64encode(f.read()).decode()

    html = open(src_html).read()
    html = re.sub(r'<a href="([^":]+\.mp4)">([^<]*)</a>',
                  lambda m: (f'<details class="clip"><summary>{m.group(2)}</summary>'
                             f'<video controls preload="none" playsinline src="{m.group(1)}"></video></details>'),
                  html)
    html = re.sub(r'<img\b([^>]*)>', gif_to_video, html, flags=re.I)
    refs = sorted({r for r in re.findall(r'(?:src|href)="([^":#?]+\.(?:mp4|gif|png|jpe?g))"', html, re.I)
                   if os.path.exists(os.path.join(root, r))})
    for rel in refs:
        html = html.replace(f'"{rel}"', f'"{data_uri(rel)}"')
    if "</style>" in html:
        html = html.replace("</style>", CSS + "</style>", 1)
    elif "</head>" in html:
        html = html.replace("</head>", f"<style>{CSS}</style></head>", 1)
    else:
        html = f"<style>{CSS}</style>" + html
    with open(out_html, "w") as f:
        f.write(html)
    print(f"wrote {out_html} {os.path.getsize(out_html) / 1e6:.1f} MB ({len(refs)} media files)")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
