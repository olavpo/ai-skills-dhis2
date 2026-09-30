# Recording, captions and derived media

> From a household enumeration PoC that compared six tracker designs (DHIS2 2.42.6, Capture Android 3.4.2, web Capture 2.42, September 2026). Scripts named here are bundled under `scripts/` (see "Bundled scripts" in SKILL.md); `scripts/<project>/` stands for the project's own generator package, which you write.

### Android

- **One `screenrecord` per caption or phase** (`scripts/android/record.py`). `screenrecord` stops
  after 3 minutes, and on the emulator it does **not** keep wall-clock time: static stretches are
  compressed and a chunk's static tail is dropped (175 s chunks came out at 164 s and 174 s; a
  phase on a static screen can come out as a single frame). Captions placed by wall-clock offset in
  one long, chunked recording drifted 10–20 s both ways. What works: start a new recording at each
  caption boundary (stop the previous with `adb shell pkill -INT screenrecord`, wait for it to exit,
  start the next, sleep 1.5 s before the next command), so each file *is* one phase; pad each clip
  with its last frame to the phase's wall-clock length, caption it, then concatenate.
  `record.py` does all of this: one `maestro test` per phase file from `Flow.write_phases()`
  (each costs the 15–20 s JVM start-up, outside the visible clip), a run log in `runs/<name>.json`
  with the caption times in the rendered video, and Maestro's last screenshot on failure.
- Captions and a running tap counter come from the Maestro command log, rendered as **ASS
  subtitles** (three styles: caption bottom, counter top right, title top left). Burn in with
  `-vf subtitles=file.ass`.
- A one-minute version: uniform speed-up (`setpts=PTS/<factor>`) with the factor in the title.
  The plan asked for "typing sped up, screen changes at normal speed"; uniform speed-up was simpler
  and honest enough when labelled.

### Web

- Playwright's `record_video_dir` records the whole context; `page.video.path()` after closing.
  The action log's timestamps are relative to page creation, close enough to the video start.

### Output rules

- H.264, `yuv420p`, `-movflags +faststart`, CRF 30 and raise it until under 10 MB.
- **Grid video** (`scripts/media/grid.py OUT.mp4 videos… --labels A,B,… --speed 8`): `xstack` of
  the tiles plus blank cards, every tile at the same speed factor, `tpad` so shorter flows hold their
  last frame — the difference in steps is visible at a glance.
- **GIFs** (`scripts/media/gifs.py VIDEO OUT.gif --run runs/<name>.json --caption "…"`): cut at a
  caption's time from the run log, `fps=8`, `palettegen`/`paletteuse`, 640 px (web) / 300 px
  (Android); 400–900 KB each. It also reads a web action log (`{"log": [{"kind": "CAP", "text",
  "t"}]}`) or takes `--at SECONDS`.
- ffmpeg: every script takes `$FFMPEG`, else the binary inside `imageio-ffmpeg`, else `ffmpeg` on
  the PATH. That build has no `drawtext`; captions and labels are ASS subtitles.
- Dashboard screenshots **after the last analytics run** (and again after any instance restart:
  analytics tables do not survive one): tall viewport (the dashboard scrolls internally, `full_page` doesn't help),
  scroll to trigger lazy items, then shoot.
