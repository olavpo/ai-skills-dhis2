# The interactive pages

File names use the type's prefix `<p>` (`pi` for program indicators, `indicator` for aggregate indicators; `<P>` in capitals, `<Short>` the capitalised short label). Items, their fields and the aux objects (category mappings or data elements) come from the type adapter (`describe()`, `diff_fields()`), so every page works for every type.

Four pages and a printed reviewer guide. Each page is one HTML file with its data embedded (`__DATA__` in the template), written to `work_dir`. Paths are relative to the skill.

| Page (file) | Purpose | Audience | State | Built by |
|---|---|---|---|---|
| **Meeting page** (`<p>-review.html`) | Decide on topics in a meeting | Programme team in a room, on a big screen | Shared: one decision per topic | `scripts/review/build_review.py` + `review_template.html` |
| **Dictionary** (`<p>-dictionary.html`) | Look up what any item counts (program indicators: and how it maps to the aggregate data set) | Anyone | None (read-only) | `scripts/types/<type>/build_dictionary.py` → `assemble_dict.py` (+ the type's template) |
| **Fix approvals** (`<p>-fix-review.html`) | Approve or reject each proposed fix; download import files | Reviewers with Claude accounts | Shared: per fix group and item, plus edited descriptions | `scripts/approve/build_fix_review.py` + `fix_review_template.html` (mode `artifact`) |
| **Fix approvals, standalone** (`<p>-fix-review-standalone.html`) | Same, offline, no account | Reviewers without accounts; a coordinator who merges | Browser storage + decisions files | `scripts/approve/make_standalone.py` (same template, mode `standalone`) |
| **Reviewer guide** (`<Short>-Fix-Approvals-reviewer-guide.pdf`) | How to use the approval page | Reviewers | n/a | `scripts/approve/manual_shots.py` + `manual_template.html` |

## Capabilities and fallbacks

| Page | Declares | Without the capability |
|---|---|---|
| Meeting page | `db` (collection `decisions`, one doc per topic key), `user` (`id()` for `by`, `can('data.write')`, `profiles()` for "last updated by") | No `window.claude` or no `db`: "offline copy" mode. Decisions live in `localStorage` (key `<p>-review-decisions:<scope>:<dataVersion>`), a "Your name" field stamps `byName`, **Export decisions** downloads `{"format": "pireview-meeting-decisions", …}`, **Load decisions file** merges newest-wins per topic and warns on another `dataVersion`. No write permission: view only. |
| Dictionary | nothing | Works anywhere; `localStorage` only remembers the names/UIDs toggle. |
| Fix approvals | `db` (collections `approvals`, `descriptions`), `user` (`id()` as `by`, `can('data.write')`), `downloads` (`save()` for the export files) | No `window.claude`/`db`: "Not connected: approvals won't be saved" (no browser fallback in this mode: use the standalone file). Write refused: view only. No `downloads`: the file text is shown with a Copy button. Description edits are written 600 ms after typing stops. |
| Standalone approvals | nothing | Decisions in `localStorage` (key `pireview-fix-<version>`, reviewer name in `pireview-reviewer`); if storage is blocked the header says to save a decisions file before closing. Exports are plain downloads. |

Per-viewer conveniences (names/UIDs toggle, current view) also use `localStorage`, wrapped in try/catch so the pages render without it.

## Design decisions worth keeping

**Meeting page**
- One topic per screen, ordered FAILS → ALWAYS_ZERO → WRONG_VALUE → REVIEW; big type; an overview with the decision table.
- Keyboard: ←/→ (or j/k) between topics, `1`–`5` verdicts, `/` search an item by code or name, `n` note, Esc back to the overview.
- Each topic shows the count, *confidence and why*, *what it means*, *evidence*, *proposed fix*, and the items (expandable, with expression, filter, detail lines, test value vs filter matches; field names/UIDs toggle).
- Five verdicts: **Agree, fix it** / **Check each item** / **Needs domain input** / **Park** / **Not a problem**. "Check each item" is its own verdict, not a group approval: the room marks each item **Fix / OK as is / Rename / Ask domain** (stored in `items`), and the page counts "N of M items decided". Topics flagged `checkEach` in the catalogue suggest it.
- Under the other verdicts an item can be marked "not affected" (an exception, stored in `excluded`).
- Who and when is recorded on every change; the note autosaves. `write_decisions.py` turns the stored decisions into `<P>-REVIEW-DECISIONS.md`, with per-item checklists for check-each topics.
- Comment threads on the artifact work well for questions during the meeting; answer them in place and fold the answers in with `write_decisions.py --clarifications`.

**Dictionary**
- Plain-language definition per item (program indicators: one sub-agent description per PI, not a template), with the raw definition one click away and the field names/UIDs toggle.
- Filters: search, aggregate data set, unit counted, validation status, "definition differs from name", "breakdown differs from the data set", "attribute categories differ", "feeds more than one data set", "has disaggregation". Sort by code, name, data set or status.
- Program indicators: link PI code → aggregate data element code (exact, then case-insensitive) → data sets; compare the PI's breakdown with the data element's *effective* combo (data-set override applied) and the data set's attribute combo, highlighting the difference. Without data sets in the export, the link filters are hidden.

**Approval page**
- Left rail: overview and test evidence, indicator fix groups, mapping fix groups (each with a progress bar; "new" badge for fixes found during testing), and "Not proposed" (agreed topics left out, and why). Header: progress (approved / partly approved / blocked by a rejection / pending) and the four downloads.
- Group header: topic number and title, "same change on every item" or "details differ per item", the rule in one line, why, downstream-consumer note from the notes file (amber when action is needed), the group's scenario table (expected vs actual, before and after); **Approve all N pending**, **Reject all pending**, **Reset group**; filter by state, search, expand all.
- Card: chips for every fix on the item and their state; test value before → after on test data and whether it matched the independent calculation; red "Still fails after these fixes: … Cause: …", orange "Name vs definition", grey "Other open topics"; side-by-side token diff of filter, expression, aggregation type, period boundaries and mapping list (removed struck through red, added green); editable proposed description. Mapping cards show the changed option filters and how many using PIs' breakdowns matched on test data.
- **Export rule:** an item is exported only when every fix on it is approved, because the proposed object combines them; one rejection blocks it. Make it visible: "partly approved" count, a card note naming the fixes still missing, an "Approve all N fixes" button per item, and a group-level hint when items also have fixes elsewhere.
- Overview: how to review, what the downloads are and how to import them, a warning that the files carry complete objects from the export date (diff against the target system first), the end-to-end evidence (syntax check, failures before → after, totals and breakdowns equal to the independent calculation, scenario checks), the still-failing list by cause, and screenshots.
- Exports split: approved PIs that work, approved but still failing, mapping patch, summary CSV.

**Standalone variant**
- Same template and data; `make_standalone.py` sets mode `standalone` and adds a full document head. Adds "Your name", **Save decisions file** (needs a name), **Load decisions files** (several at once; newest wins per item; disagreements listed on the overview; files from another data version load with a warning), and shows who decided on each card.
- One offline file; fonts fall back to system fonts.

## Reviewer guide

`manual_shots.py` drives the standalone page in headless Chromium with fictional reviewers ("Reviewer A", "Reviewer B", "Coordinator"), each in a fresh context, picks groups and items from the data, fills `manual_template.html` and prints A4 PDF. Covers the two ways to open the page, the page at a glance, reviewing a group, approving and rejecting, working together with decisions files, producing the import files, and FAQs. Figures whose situation is not in the data are dropped. Keep the template free of program content.

## Publishing

- Meeting page and approval page: publish as Claude artifacts with the capabilities above (load the `artifact-capabilities` skill first), e.g. approval page `{"db": …, "user": …, "downloads": …}` with collections `approvals` and `descriptions`. The dictionary needs none. Republish to the same URL after a rebuild; a new `dataVersion`/`version` starts a fresh browser-storage key, while shared db documents persist by topic key or group key.
- Collect decisions from the shared store with `ArtifactData` (list the collection into a directory): meeting decisions feed `write_decisions.py --decisions` and `build_fixes.py --decisions`; approvals feed `export_approved.py --dump DIR` (`DIR/approvals/<group>.json`, `DIR/descriptions/<piId>.json`); add `--decisions dec-*.json` in the same call when some reviewers used the standalone file, so both are merged (newest wins per item, disagreements printed).
- Viewers without write access (or without an account, via a shared link) can look but not decide. Send them `<p>-fix-review-standalone.html` and the reviewer guide PDF; they return decisions files, which a coordinator loads into the standalone page or passes to `export_approved.py --decisions`.
- Size: the approval page embeds screenshots as data URIs; keep the page under the 16 MB artifact limit (fewer or smaller shots if needed).
