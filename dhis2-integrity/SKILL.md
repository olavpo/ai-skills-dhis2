---
name: dhis2-integrity
description: >-
  DHIS2 metadata REMEDIATION — fixing what is BROKEN in an existing instance or metadata
  dump (not building, authoring, moving, or exporting config). Use when the user wants to:
  make an instance pass DHIS2's data-integrity checks; resolve the failures an integrity run
  reports; de-duplicate near-identical metadata (combos, COCs, indicators, data elements);
  repair broken indicator/program-indicator expressions or validation rules; fix structural
  faults (orphaned stages, wrong category combos, invalid org-unit groups/geometry); or
  trial repairs on a disposable sandbox and hand back a fixed dump. Typical
  phrasings: "make our instance pass the integrity checks", "dedupe these combos without
  losing data". Do NOT trigger for non-remediation
  work — authoring new metadata or expressions, org-unit reorganisation, exporting data,
  debugging one analytics number, provisioning instances, app development, or just inspecting
  config — that belongs to dhis2-metadata, dhis2-indicators, dhis2-docs or dhis2-instances.
---

# DHIS2 Metadata Cleanup

A methodology + toolkit for taking a DHIS2 instance (or a metadata `.json` dump) from "many
integrity problems" to "clean, verified, documented". The hard-won details live in
`references/`; this file is the orchestration layer.

## Core principles (read these first — they prevent the expensive mistakes)

1. **Work on a copy, never production-first.** If you have (or can take) a dump, restore it onto a
   disposable sandbox and fix *that*. It gives you isolation, a reproducible result, and — crucially —
   a **control baseline** for verification. See `references/dump-and-sandbox.md`.
2. **Snapshot before you touch anything.** A `pg_dump` (or metadata export) taken *before* the first
   change is the only way to later prove your fixes didn't silently change analytics outputs or break
   data-entry forms. This is the #1 regret if skipped.
3. **Integrity-clean ≠ output-equivalent.** Re-running the integrity checks proves the database is
   internally *consistent*; it does **not** prove indicator numbers, forms, and tracker behaviour are
   unchanged. Verify outputs separately — see `references/verification.md`.
4. **The decision model: fix what's clearly deterministic; ASK for everything else.** This is the heart
   of the skill. A fix is *deterministic* when there's exactly one correct outcome and no source-data or
   intent you can't see is needed (whitespace/typos/`(%)`/age-range naming, set `aggregationType=NONE`
   on a text DE, quote a UID in a filter, strip an unsupported `AVG()`, add an "Unknown" bucket to a
   *universal* compulsory dimension). Everything else is a **judgment call** → surface it, don't guess:
   - **Distinguish POLICY decisions from INSTANCE decisions when reusing them.** *Instance* decisions
     (which of two datasets is canonical, which class a duplicate really is, the scope of a niche
     dimension) NEVER carry across databases/engagements — re-establish each time. *Policy* decisions
     (never delete by `DELETE_`-name — merge instead; broken rules get restored-and-flagged; bucket
     group-set stragglers into "Other/unknown") MAY carry as **proposed defaults**: present them
     pre-selected in the decisions round with one-line provenance ("as agreed on <prior case>"), and
     get a quick confirm — never apply a carried policy silently.
   - **Establish the autonomy level up front** (one `AskUserQuestion`): does the user want to decide each
     judgment call, or authorize best-effort autonomous decisions? Only the latter lets you apply
     heuristics without asking — and even then, document every heuristic choice and prefer reverting +
     flagging anything ambiguous.
   - **Scope ≠ autonomy.** "Fix everything" / "do it all" / "address every warning" sets *which* checks to
     work through — it does **not** authorize you to *decide* the judgment calls alone. Keep deciding
     those *together* (front-loaded) unless autonomy was separately granted; only deterministic fixes go
     in silently. (Real correction: "I meant do it together, not autonomously — except deterministic.")
   - **Use each check's own `recommendation`** (present on every integrity check via `/api/dataIntegrity`)
     as the starting point for the right fix — though it's not always actionable.
   - **Broken ≠ unwanted.** Metadata with clear intent that is merely *broken* (a rule missing its
     action, a notification missing its template, a meaningful-but-invalid expression) should be
     **repaired, or restored-and-flagged for the owner — not deleted.** Only delete genuinely orphan /
     empty / test / duplicate objects. **One exception — abandonment:** broken **and** untouched for
     years (`lastUpdated`) **and** zero data **and** zero references is effectively abandoned; deleting
     such objects as a batch (with a documented list in the change proposal) is acceptable without a
     per-object ask. All three conditions must hold — recent edits, any data, or any inbound reference
     puts it back in repair-or-flag territory.
   - **When the right answer needs data/intent you don't have, revert to original + flag** (which dataset
     is canonical, which of two real classes is true, what field a rule should hide, the scope of a niche
     dimension). Don't apply a silent heuristic.
   See `references/playbook.md` §2 (triage) for the per-check defaults and what's ask-vs-auto.
5. **Prefer the API; use the merge endpoints for dedup/consolidation; SQL is the LAST resort.** Before
   reaching for SQL, **probe for a merge endpoint** — `POST /api/<type>/merge` with an empty `{}` returns
   `404` if none exists, `409`/`400` if it does. The merge framework **keeps expanding across versions**
   (2.41→2.43 added more types, including `categories` itself), so never rule it out from memory. An
   `E1120`/`E4030` block is **not** a license to drop to SQL — it usually means a merge endpoint should do
   the data-safe migration for you. (Hard-won: hand-editing `categorycombos_categories` in SQL to merge
   look-alike categories was wrong; `/categories/merge` existed.) See `references/playbook.md` §5 and §6.
6. **The instance can stop/restart mid-run (broker cycles, port conflicts).** Because every write goes
   through the logged client one object at a time, a mid-run drop never leaves a half-applied batch — the
   script just errors on the next call (`Name or service not known` = host down). Re-check reachability,
   restart the instance, and re-run; the fix scripts are idempotent so replay is safe. Don't assume
   corruption from a connection error — verify what actually got written via the operations log.
7. **Big instances OOM the API; SQL is the reliable path for bulk work.** On large instances (100k+ OUs
   or users, giant OU-group memberships) a shared host will OOM Tomcat on bulk API operations — big
   imports, large fetches (a 22 MB `dataSets` export), even modest `dataValueSets` POSTs — even at 8 GB
   heap. Symptoms: silent `RemoteDisconnected` / `api 000`. Push the heaviest work **straight to Postgres
   via SQL** (OU-group membership loads, user data-view fixes, exclusive-group surgery, combo-merge
   reference repointing), keep API writes chunked+resumable, and **run one instance at a time** (serialize
   cases; give the live one more heap). And **don't trust a suspiciously clean post-fix inventory on a big
   instance** — its slow checks silently under-report; verify fixes with direct SQL (see §1, verification).

## When to use which mode

| Situation | Mode |
|-----------|------|
| User points you at a live instance and is comfortable fixing it directly | **Live** (still snapshot first) |
| User hands you a metadata `.json` dump | **Dump → sandbox**: import to a fresh instance, fix there, re-export |
| Production instance, no appetite for risk | **Dump → sandbox**, then apply the reviewed result to prod |
| You need to *prove* outputs are unchanged | Keep the pre-change copy as the **control** and diff (verification) |
| **No instance you may write to at all** (interactive Q&A, a pasted check result, a metadata subset, an owner who will apply everything themselves) | **Advisory**: same pipeline (triage → decisions → fix design), but the deliverable is the **fix manifest + its renderings** (step 6) — you never apply. Work from a metadata export (dhis2-metadata skill) or the user's pasted evidence; if a disposable sandbox is available, use it to *prove* each fix before putting it in the manifest. |

**Advisory mode is the common real-world case**: an AI agent is rarely given write access to a national
system, even dev. The national core team applies the fixes — so everything below that says "fix" still
happens (on a sandbox or on paper), but what leaves the engagement is the reviewed **change proposal +
import package + playbook**, all rendered from the manifest, each fix carrying preconditions (drift
guards) and verify steps so the team can apply them safely and prove they landed.

Two boundaries within advisory mode:
- **Static triage only, executable outputs need an instance.** From a dump alone (no runnable instance
  at all) you can do the *analysis* half — scan for issues, triage, draft the change proposal — but do
  **not** hand over executable renderings (import package, playbook, SQL file) containing fixes that
  were never proven against a running DHIS2. Untested fixes ship as *proposal text* clearly marked
  "not exercised"; if even a disposable sandbox is possible, prove them there first.
- **Not every use is a full review.** Someone may bring one or two specific issues and want advice, not
  an engagement. Answer the question — use the relevant playbook section, decision model, and check
  knowledge — without unrolling the whole workflow (no inventory, no manifest, no deliverable set)
  unless they ask for a review.

## Setup

- Use the **dhis2-api** skill for the connection (`.env` with `DHIS2_BASE_URL` + token, or
  basic auth). All the bundled scripts read the same env vars: `DHIS2_BASE_URL`, and either
  `DHIS2_API_TOKEN` or `DHIS2_USER`/`DHIS2_PASS`.
- For the sandbox/dump workflow, use the **dhis2-instances** skill (d2-broker) to create an empty
  instance to restore into.
- Direct SQL (optional, for guard-blocked fixes): `pip install psycopg2-binary`; find the DB
  (often published on the host gateway — `references/playbook.md` §sql explains how to locate it).
- `pip install httpx python-dotenv` for the scripts.

## The workflow

Track these as tasks; re-verify after every batch.

### 0. Establish the target & a baseline
- **Confirm/derive the DHIS2 version FIRST.** If creating the sandbox from a dump, **fingerprint the
  version from the dump before choosing the instance version** — guessing wrong makes the import reject or
  silently drop properties. Diff the dump's per-object property names against bundled `schemas-v4x.json`:
  `attributeValues` present everywhere ⇒ ≥2.42; `programIndicators.categoryCombo`/`categoryMappings` ⇒
  2.42; their absence + the top-level user structure ⇒ 2.41. On the live instance just read
  `/api/system/info`. Version drives everything downstream: the integrity framework is 2.38+, and the
  **merge endpoints are version-dependent** — `categories`/`categoryCombos` merge are **2.43+** (absent on
  2.42/2.41 → SQL for combo/category consolidation). See `references/playbook.md` §5.
- **Dump mode:** import the dump onto a fresh sandbox (matching version). `scripts/metadata_dump.py import`
  for small instances; for LARGE dumps (100k+ OUs, 100k+ users, giant OU-group memberships) use
  `dhis2-metadata/scripts/import_metadata.py --resume` and expect to fall back to **SQL for the bulk join
  tables** the API OOMs on — see `references/dump-and-sandbox.md` §"Large / heavy dumps". Keep the
  original dump as the control.
- **Reconcile object counts after import** (`/api/<type>?fields=id&pageSize=1&totalPages=true` per type).
  A dump can be **dirtier than any live instance can hold** — the importer silently drops
  non-round-trippable corruption (orphan program stages, program-less favorites, invalid job configs),
  so the rebuild is *cleaner* than the source and hides real integrity problems. Explain every delta
  (broker adds `local_admin`; DHIS2 ships built-in system jobConfigurations); record dropped objects as
  findings. Details in `references/dump-and-sandbox.md`.
- **Live mode:** take a snapshot now — `pg_dump` if you have DB access, else a full metadata export
  (`scripts/metadata_dump.py export …`) plus `dataValueSets` for the datasets in scope.

### 0.5 If the instance is empty, ASK whether to populate synthetic data
An empty (metadata-only) database can't fully exercise the methodology: the `E1120`/`E4030` data-guards
never fire, the merge-vs-SQL decision is never forced, and there's nothing to A/B-diff — fixes that pass
on empty can fail on data. But synthetic data isn't always wanted (a quick advisory triage, a metadata
subset, time pressure), so when the target has no data values, **ask the user** (fold it into the step-3
decisions round if timing allows): populate synthetic data for realistic exercise, or proceed empty with
the limitation stated in the deliverable ("fixes not exercised against data"). If populating: run
`scripts/gen_synthetic_data.py` **after import, before the snapshot** — then the snapshot is the true
control. See `references/synthetic-data.md` (the `E7617` OU-scope prerequisite, tracker rule-engine
handling, the large-instance caveat). Skip the ask only when the instance already carries representative
data.

### 1. Build the issue inventory
Run **all** checks — including the slow ones, which the summary run skips:
```bash
python scripts/integrity.py inventory   # summary + slow/programmatic details, saved to disk
```
This handles the async+cache trap (waits on `finishedTime` changing) and prints every nonzero check
grouped by severity. Persist the raw JSON.

**Two traps on real instances (learned the hard way — see `references/playbook.md` §1):**
- **The first run right after a (re)start is unreliable** — the summary can return partial/stale counts
  (e.g. 34 checks one minute, 50 the next). Let the instance settle and **re-run until two consecutive
  inventories agree** before trusting the numbers. `integrity.py inventory` re-runs and warns if unstable.
- **On large instances the `details` endpoint silently returns empty/0 for heavy checks** (dedup,
  group-set, exclusive-violation) even when `summary` reports hundreds. **Don't trust `details=0` when
  `summary>0`** — derive the actual issue list with a **direct API/DB query** (e.g. fetch all indicators
  and group by formula yourself). The bundled fix scripts do this.
- **On VERY large instances (100k+ OUs) the whole inventory silently UNDER-reports** — the slow checks
  time out server-side and return empty, so a suspiciously low result is a partial run, not success.
  (Real case: a very large instance — hundreds of thousands of org units — reported "2 nonzero checks" while its untouched six-figure
  `orgunits_no_coordinates` had simply vanished from the report.) **Verify each fixed check with a direct
  SQL query** — authoritative and fast — rather than trusting a low post-fix inventory. Two consecutive
  agreeing runs are NOT enough here (two partial runs can agree).

### 2. Triage (don't fix blindly)
Bucket every failing check into: **deterministic-safe**, **judgment/destructive**,
**accepted-unfixable**, **blocked-by-privilege**. The taxonomy + a per-check playbook is in
`references/playbook.md` — read it; it tells you the correct fix (and the correct *non-fix*) for each
check family, plus the API guards and their SQL/merge work-arounds.

### 3. Get decisions in TWO planned rounds — front-load, then one scheduled discoveries round
**Front-load clarifications.** Once triage has enumerated every judgment call, ask them **all together**
in as few `AskUserQuestion` rounds as possible — *before* starting any fix — and only then begin fixing.
Do **not** interleave fix/ask/fix/ask; that's slow and disorienting for the user. Then **plan for one
second round mid-engagement**: real fixes surface questions triage can't see (a side-effect, an orphan
that turns out to have an owner, a cascade that forces a choice) — batch those discoveries into a single
scheduled round rather than pinging one-by-one, and tell the user up front to expect it ("one more
decision batch will come once fixing surfaces the non-obvious cases"). Only a genuinely blocking
discovery justifies an unscheduled ask.
Cover in the first round: the **autonomy level** (decide-each vs best-effort autonomous), **carried
policy defaults** (pre-selected, with provenance — see principle 4), the per-category defaults that
depend on context (deletion of unused metadata; not-viewed favorites — depends on the **age of the DB
copy**; not-contained-by-parent coordinates), **manual-review-pack findings** (promote worksheet
candidates through this same round — one decision surface, not a second process; see step 7), and any
instance-specific ambiguities the triage surfaced. When presenting each decision, **briefly explain what
the integrity check actually means** in plain terms — users don't know the check names. Record
everything in `decisions.md`.

### 4. Fix, highest-leverage first, logging everything
- Make each write through `scripts/d2_client.py` (or your own client) so every POST/PUT/PATCH/DELETE is
  appended to an operations log automatically.
- For **duplicates**, use the merge endpoints (`POST /api/<type>/merge`) — see `references/playbook.md`
  §merge for which type to use (combo vs COC vs indicator) and the scoping rule.
- Watch for **self-inflicted new violations** (e.g. emptying a rule's last action, or putting an object
  in two groups of an exclusive set). Re-run the broad summary after each batch, not just the targeted
  check.

### 5. Verify outputs (not just integrity) — proportional to what the fixes touched
Re-run all checks to confirm consistency, **then** verify outputs **proportionally to the fix mix**:
- **Fixes that touched data or expressions** (merges, COC/data migrations, formula/validation-rule
  repairs, category surgery) ⇒ the **full output diff** against the control: data values, analytics
  (regenerate on both), form COC resolvability, tracker program-indicator values, dangling references.
- **Purely structural fixes** (group membership, sharing, coordinates, naming, orphan deletion with no
  data) ⇒ integrity re-run + **targeted spot checks** of the touched objects is sufficient; a full
  analytics regeneration buys nothing there.
On large instances the full diff is expensive (double analytics regeneration) — that cost is justified
exactly when data/expressions changed, and skippable when they didn't. `scripts/verify_outputs.py` does
the core diffs; `references/verification.md` explains the acceptance criterion ("every diff must map to
a logged change") and the delta-verification fallback when no control exists.

### 6. Record every fix in the FIX MANIFEST as you go (the canonical deliverable)
While working out each fix, record it as an entry in the **fix manifest** (`scripts/manifest.py` — one
JSON file: per fix its check, decision ref, rationale, exact steps with payloads, **preconditions**,
verify assertions, reversibility, importability). Everything the engagement hands over is **rendered**
from the manifest so the formats can never drift apart:
- **change proposal** (`.md`) — the human-readable review document; always delivered;
- **import package** (`.json`) — the importable subset, for teams that only trust the Import/Export app;
- **replay playbook** (`.ipynb`, rendered via `scripts/playbook.py`) — the operator console for
  everything imports can't express (merges, guarded SQL, data migrations), run DRY_RUN-first,
  cell-by-cell with verify gates: dedicated → staging → production.
- **SQL export** (`sql-fixes-*.sql`) — every `sql` step as a standalone, psql-ready block (BEGIN/COMMIT
  per fix, ordering warnings, cache-clear reminders). **Direct DB access from the playbook environment
  is the exception, not the rule** — usually only a DBA on the server can run SQL. The playbook
  auto-detects this (`SQL_MODE`): with a DB route its SQL cells execute directly; without one they print
  the statement + its block reference in this export and pause for the DBA, so the notebook still
  drives the ordering and the verify cells still prove each fix landed. Author `sql` steps UID-based
  (subqueries/`resolve_id`, never numeric ids) so they export cleanly; `sql()` buried inside `code`
  steps is NOT exportable (validate() warns). **And author them SELF-GUARDING (idempotent)** — the .sql
  file runs without the playbook's precondition guards, so each statement must be a state-transition
  whose WHERE matches the pre-fix state (`SET new WHERE old` → a re-run reports 0 rows), INSERTs use
  `ON CONFLICT DO NOTHING`, and accumulating updates neutralize their source rows in the same
  transaction. validate() flags the common re-run-unsafe shapes. **Guards are tiered:** self-guarding
  statements are the base for every step; **destructive steps (DELETEs, data migrations) additionally
  carry a `guard` expression** on the sql step — a boolean SQL predicate asserting the expected pre-fix
  state — which the renderers emit as a `DO $$ … RAISE EXCEPTION … $$` block *inside the same
  transaction*, so on drift the whole fix aborts instead of half-applying. validate() warns when a
  destructive sql step has no guard.

**Preconditions are the drift guards** — validated by test: a playbook rendered for one instance and run
against a different one **skipped every fix** (missing UIDs, checks already 0, identity probes failing)
and its verify cells failed loudly instead of pretending success. Rules for authoring them:
- every fix carries `check_nonzero` (skip when already applied / never present) **plus at least one
  IDENTITY guard** — an `object_exists`/name probe of something the fix actually targets. A check-level
  guard alone can pass on the *wrong* instance (both instances fail the same check for different
  reasons); the identity guard is what catches that. Match on markers that **survive the clone chain**
  (UIDs + names of objects the fixes target) so any legitimate copy of the right database passes but a
  different organisation's DB fails.
- **the playbook ALSO requires an explicit operator acknowledgment** — belt and braces: the setup cell
  fetches and prints the target's identity (`systemName`, URL, key object counts) and the operator must
  copy the printed instance name into a `CONFIRM_TARGET` variable before any fix cell runs. Fingerprints
  catch the wrong database; the human ack catches the right database at the wrong *time* (prod when they
  meant staging).
- derive-at-runtime steps must ALSO guard inside their loop (act only on objects matching the recorded
  names/UIDs and expected state), so a precondition slip degrades to a no-op, not a wrong write.

Keep these guarantees in every step (they apply to the manifest and all its renderings):
- **UID-based**, never instance-specific numeric ids. SQL cells call `resolve_id(table, uid)` to look up
  the target's internal id at runtime (numeric ids differ per instance — this is the #1 portability bug).
  For derived sets (orphans, not-viewed favorites, scarce groups, dedup), don't hardcode thousands of
  UIDs — write **logic cells that re-derive the targets** at runtime (query the check / direct scan).
- **New objects get a PRE-GENERATED, fixed UID — never an unspecified `POST`.** When a fix *creates* an
  object (e.g. an "Unknown"/"Other" group), generate its UID once while authoring (`new_uids()` →
  `/api/system/id`, or reuse the UID the object got on the dedicated instance) and **hard-code that id**
  into the cell, creating via `ensure(path, uid, body)` (create-by-UID, skip if it exists). A bare
  `POST {name…}` lets the server assign a **fresh UID every run** → re-running **duplicates** the object,
  the UID **differs across instances** (so references/dimension membership don't line up dedicated→
  staging→prod), and there's no stable id to **reverse**. A fixed UID makes creation idempotent, portable,
  and reversible. (Compute *derived* fields like membership at runtime; only the **identity** is fixed.)
- **Idempotent** cells (re-check state, no-op if already applied) and a top-of-notebook `DRY_RUN` switch.
- **Verify cells** that re-run the integrity check and assert the expected count (0, or a documented residual).
- **Surface pending state.** DHIS2 writes, merges, and especially integrity-check recomputes can take
  30–120s; a cell that prints nothing until it returns looks hung. The helpers print a flushed
  **⏳ "(waiting on API/DB)"** line before each call and replace it with **✅/❌ + status + elapsed**, and
  `check()` shows a **live elapsed counter** while polling — so the operator can always tell it's working.
- **SQL cells gated** (require DB + `ALL`), in order, each followed by `cache_clear()`.
- **The manifest must mirror the FINAL decided state — re-render after any decision review.** A
  manifest built from your *first-pass* auto-fixes will, on replay, re-apply decisions that were later
  **reverted** (e.g. delete the program rules the owner chose to keep, force a reverted Unknown-group fix)
  — actively dangerous. Only fixes that were **applied AND kept** become manifest entries; everything
  reverted or left as a judgment call goes in the manifest's **`flagged` list** (rendered as
  "Flagged for owner — do NOT auto-fix" in every format, never as executable steps). Sequence entries
  that cascade (categories → category-combos → COCs) in dependency order.
Emit a **separate manifest per track** (see step 7): integrity vs naming. **Ship launch instructions with
the rendered playbook** — the admin who replays it needs the concrete *how to run* (install `jupyterlab
httpx python-dotenv psycopg2-binary`, set the target env vars, `jupyter lab <file>`; headless sandbox →
bind `$SANDBOX_HOST_PORT`; or `jupyter nbconvert --execute`). `scripts/playbook.py` bakes this into the
notebook's intro cell; also drop a short `RUN-playbook.md` in the deliverables. Don't assume the admin
knows to start Jupyter.

### 7. Document & (dump mode) hand back the result
Produce: `decisions.md`, an operations log, per-category change CSVs, a before/after counts table, a
`README.md` summary (fixed / accepted-with-reason / blocked-with-remediation), and the **fix manifest +
its three renderings** (change proposal, import package, playbook — `scripts/manifest.py render`).
There is also an optional **third track: assisted manual review** (`scripts/review_pack.py` +
`references/manual-review.md`) — evidence worksheets for the official docs' *manual* metadata-review
items (duplicate data sources, category "meaningful totals", dashboard config, OU assignment, sharing;
indicator-formula semantics are flagged and handed off to the **dhis2-indicators** skill). Strictly
**worksheets, never fixes** — the pack itself never writes anything. Its findings are **promoted through
the same batched decisions round as the integrity judgment calls** (step 3): one decision surface, one
`decisions.md`, and only user-confirmed promotions enter the manifest. Keep two separate tracks: **(a) integrity remediation** (high-confidence structural
fixes) and **(b) deterministic naming fixes** (objectively-correct, reference-safe: whitespace, obvious
typos, `(%)` postfix on percentage-type indicators, age-range notation). Keep naming as its own
playbook/section even though it's auto-applyable, because it changes end-user-visible labels while
integrity fixes are invisible — an admin may want to apply or review them on a different cadence.
**Out of scope for this process:** the acronym-prefix (`TB_`/`MAL_`…) and code-standardization overhaul —
it's a separate governance exercise needing stakeholder alignment; do not attempt renames and do not
emit a rename-proposal table. A **descriptive inventory** IS allowed as an appendix (current prefix
patterns, code coverage stats, inconsistency counts) — useful input to that governance exercise, but
strictly no proposed new names. In dump mode,
re-export the fixed metadata (`scripts/metadata_dump.py export …` or the safe exporter) as the reviewable
deliverable, and optionally round-trip-import it into one more clean instance to confirm it imports cleanly.

## Reference files

- `references/playbook.md` — **the core reference.** Triage taxonomy, per-check fixes, the API
  business-guards (`E1120`/`E4030`/`E4056`/`E8031`) and how to pass them, the 2.41+ merge/dedup
  endpoints and their scoping rules, when/how to use direct SQL (table names, locating the DB),
  naming-convention rules, and the self-inflicted-wound watch-list.
- `references/dump-and-sandbox.md` — version-fingerprint the dump, restore onto an empty sandbox
  (default-UID, dependency-order, sharing/user gotchas), **reconcile counts after import (dumps can be
  dirtier than any live instance)**, the **large/heavy-dump import strategy (resume + SQL bulk)**, and the
  metadata-dump-vs-pg_dump trade-off.
- `references/synthetic-data.md` — generating a data fixture so fixes are realistic (E7617 OU-scope,
  tracker rule-engine handling, the large-instance caveat). Use when the target is metadata-only.
- `references/manual-review.md` — the **assisted manual-review track**: mapping of the official docs'
  manual metadata-review items to evidence worksheets (the review pack), the worksheets-not-fixes
  contract, the dhis2-indicators delegation boundary, and how to run a review session over the pack.
- `references/verification.md` — output-level regression testing: control baseline, what to diff,
  acceptance criterion, gotchas, the no-control fallback, and the **concrete delta-SQL recipe** for when
  the API inventory is unreliable.

## Bundled scripts

- `scripts/d2_client.py` — auto-logging DHIS2 HTTP client (`get/post/put/patch/delete`) + JSON-Patch
  helper. Import it or run helpers from it.
- `scripts/integrity.py` — run checks (summary + slow), `finishedTime`-aware waiting, inventory print,
  and a `details <check>` command. The cure for the async/cache trap.
- `scripts/db_client.py` — logged psycopg2 wrapper for the guard-blocked SQL fixes.
- For a **gentle, anonymized export from a large live instance**, use the dhis2-metadata skill's
  `fetch_metadata.py --all-types --page-size 200 --delay 0.3 --anonymize --unshare` — it excludes PII
  and sharing server-side (`fields=:owner,!email,…`) so they never leave the server. (The former
  `export_metadata_safe.py` here was a duplicate and has been removed.)
- `scripts/metadata_dump.py` — fast full-metadata export / import / diff (small instances or sandbox).
- `scripts/gen_synthetic_data.py` — **populate synthetic data so fixes are realistically exercised** (empty
  DBs never trip the `E1120`/`E4030` guards and give nothing to A/B-diff). Aggregate via `/api/dataValueSets`
  (every aggregate DE under each of its COCs × a few OU×period, incl. disjoint COCs); tracker via
  `/api/tracker` (skips ASSIGN/validation rule-target fields + empty option sets, posts `validationMode=SKIP`).
  Seeded/deterministic. **Run it after import, before the "original" snapshot.** Prereq: assign the level-1
  root OU to the posting user's capture OUs or every value rejects with `E7617`. Tune `--ous/--periods/--teis/
  --chunk` DOWN on very large instances (very large instances OOM on big batches/fetches — see references).
- `scripts/review_pack.py` — the **assisted manual-review pack**: read-only evidence worksheets (CSV +
  INDEX.md, per-sheet caps always reported) for the docs' manual items — duplicate data sources,
  category totals, dashboard config, OU assignment, sharing, indicator-formula candidates (handoff to
  dhis2-indicators). Worksheets, never fixes. See `references/manual-review.md`.
- `scripts/manifest.py` — the **FIX MANIFEST**: schema + validator + renderers. One JSON records every
  fix (steps, payloads, **preconditions/drift-guards**, verify assertions) and every flagged non-fix;
  `render` emits the change proposal (`.md`), the import package (`.json`, importable subset) and the
  replay playbook (`.ipynb`, via playbook.py) from that single source. `demo` writes a minimal example;
  `examples/manifest-sl-demo.json` (with its rendered proposal + playbook alongside) is a full exemplar
  built on the public Sierra Leone demo database, and `examples/review-pack-*` show the manual-review track's output.
- `scripts/playbook.py` — generate the portable, replayable remediation **Jupyter notebook** (UID-based,
  idempotent, `DRY_RUN`, verify cells, gated SQL with runtime `resolve_id`). `--demo <path>` writes a
  sample to see the format. Build one notebook per track (integrity / deterministic-naming).

Run any script with `-h` for usage. They are deliberately small and readable — adapt them per task.
