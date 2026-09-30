# Reporting and sharing

> From a household enumeration PoC that compared six tracker designs (DHIS2 2.42.6, Capture Android 3.4.2, web Capture 2.42, September 2026). Scripts named here are bundled under `scripts/` (see "Bundled scripts" in SKILL.md); `scripts/<project>/` stands for the project's own generator package, which you write.

- Generate the report from data, so numbers in tables always match the logs: a project
  `build_report.py` reads `effort.json` (from `scripts/effort.py`), `verification.json` (from
  `scripts/verify_indicators.py`) and the run logs, and takes its prose from a separate
  `report_text.py` of strings and small functions. It is too project-specific to bundle; build it
  per PoC. Check every number in the prose against the effort file before publishing (two were
  slightly off).
- Artifact limits: 16 MB per file, **64 MB per version**. Publish a subset (one-minute videos, web
  videos, clips, GIFs, screenshots ≈ 45 MB) and keep full-length videos local; build a publish
  variant that drops links to unpublished files.
- Keep videos and GIFs out of git (`.gitignore` from the start — one early commit had to be squashed
  away). Squash history into a few commits before handing off (the user re-signs them on the host).
- Keep a running `report/findings.md` while working; the report's findings section was written from it.

## What readers asked for after the first version

- **A "How it works" section with diagrams**: the assumed workflow (lanes per actor, time left to
  right, optional steps dashed), the recommended structure (tracked entity types → programs →
  stages, relationships labelled, which indicators come from where), and the options compared
  side by side showing only what differs. Generate the per-option schematics from one spec per
  option, so they stay consistent and follow the metadata. `scripts/svg_diagram.py` (bundled) has the
  building blocks (`Svg` with lanes, boxes, arrows and labels, `figure`, and `DIAGRAM_CSS`, which reads
  the page's CSS custom properties, so the diagrams work in light and dark themes).
- **A design schematic per option**, collapsible, next to its videos and indicator gaps.
- **Say which workflow you assumed** when the brief did not fix it (registration org unit, who
  follows up, when a case closes); readers check assumptions before results.
- **No multi-video grids**: seven videos playing at once cannot be followed; one video per option
  is enough.
- **A "tools and devices" section** when a custom app is on the table: the options, offline
  support, build and maintenance estimates, and the point that the data model stays the same.

## Artifact mechanics

- Links to media files (`<a href="clip.mp4">`) replace the whole page inside the artifact frame and
  leave no way back. Open clips in an overlay `<dialog>` player with a Close button (Esc and a
  backdrop click close it too); keep the `<a href>` for no-JS fallback.
- Removing a published file needs `files: {"path": null}`, and the publish is refused until the
  file has been read or listed in the session: list the artifact's files first.
- The artifact is private until the owner shares it; whether people outside the organisation can
  open it depends on org settings. Say so when handing over the link.

## Single-file export for readers without artifact access

`scripts/make_standalone.py` (bundled) turns the published page into one HTML file: videos
re-encoded to H.264 CRF 32 at 15 fps (phone recordings 360 px wide, web 960 px), screenshots to
1200 px JPEG, GIFs to looping muted videos (a fifth of the size), all inlined as data URIs, and
media links turned into expandable players (browsers refuse to navigate to `data:` URLs). 45 MB of
published media became a 23–27 MB file. Keep it gitignored and rebuild it after every report change;
treat the artifact as the main product and the file as an export.

## Hand-over package for colleagues

- **Importable metadata** (`metadata/*.json`, imported in order with `scripts/import_package.py`) plus
  the loader and verification scripts, with a README.
- **A database dump** of the shared instance for restoring on a hosted server:
  `pg_dump --no-owner --no-privileges -T 'analytics*'` (analytics tables are regenerated; 255 MB
  → 3.2 MB gz), append `UPDATE userinfo SET disabled = true WHERE username = 'local_admin';` when
  the instance came from the broker, and **test-restore** into a scratch database on the same server
  (`CREATE DATABASE …; psql -v ON_ERROR_STOP=1`) comparing counts of tracked entities, events and
  programs. Tell the user to change the `admin` password on anything internet-facing, to run
  analytics after restore, and to set the server time zone.
- **A testers' deck** on the organisation's template (`org-templates` + `pptx`): one slide with the
  server URL and one login that can do everything (plus optional role accounts for scoped views),
  then one slide per option with the programs to pick, how to enter a case, and what to test. Check
  the exact names testers will see (relationship types carry the code prefix, e.g.
  "DEMO C household member") and that the demo user can write every program.
- **Findings as bug reports**: each platform issue with version and reproduction.
