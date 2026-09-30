# Effort measurement

> From a household enumeration PoC that compared six tracker designs (DHIS2 2.42.6, Capture Android 3.4.2, web Capture 2.42, September 2026). Scripts named here are bundled under `scripts/` (see "Bundled scripts" in SKILL.md); `scripts/<project>/` stands for the project's own generator package, which you write.

- Count from **what ran**, not what was planned: taps, typed characters, scrolls (a
  `scrollUntilVisible` that took > 1.5 s actually scrolled), screen changes (marked in the generator).
- Keystroke-level estimate used (`scripts/effort.py`, bundled): Android 1.2 s/tap, 0.35 s/character,
  1.0 s/scroll, 1.5 s/screen; web 1.3 s/click, 0.25 s/character, same scroll and screen values. State
  them in the report, call the result "interface time", and recommend a timed manual run and a field
  trial.
- Inputs: Maestro's `--debug-output` command logs (`commands*.json`, under a hidden `.maestro`
  directory) for Android, the web runner's `{"counts": …}` logs, and the flow generator's planned
  counts (`counts.json`), which supply screen changes and stand in for a flow whose run failed
  (marked in the table). A `launchApp` relaunch counts as two taps (`--launch-taps`): it replaces the
  back presses a person would make.
- Automation run times are **not** effort: Maestro spends most of its time on scrolling checks.
- Search-before-create should be the same on both platforms for a fair comparison (Android forces
  it; web flows did it too).
