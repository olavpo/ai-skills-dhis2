---
name: create-dhis2-app
description: "Scaffold a simple, vanilla JavaScript DHIS2 tool from the dhis2/tool-template — no React, no TypeScript, no DHIS2 App Platform. Use this skill when the user explicitly wants a lightweight admin tool, system utility, cleanup script, or quick-and-dirty DHIS2 helper using plain JS and direct DOM manipulation. Triggers on: 'simple DHIS2 app', 'vanilla DHIS2 tool', 'lightweight admin tool', 'tool-template', 'no React', 'quick DHIS2 utility', or when the user says they don't need the full App Platform. Do NOT use for React-based apps, TypeScript apps, or anything using @dhis2/ui — those belong to the dhis2-apps skill. NEVER use for: existing app changes, DHIS2 API calls, debugging, refactoring, docs lookup, or non-DHIS2 projects."
---

# Create DHIS2 App (Simple Template)

Scaffold a new DHIS2 web application from the [dhis2/tool-template](https://github.com/dhis2/tool-template) — the simple vanilla-JS tool template.

This template is intentionally simple: plain JavaScript with ES modules, Webpack 5, and direct DOM manipulation. No React, no TypeScript, no DHIS2 App Platform, no `@dhis2/ui` component library. It's designed for lightweight system administration tools, not full-featured end-user applications.

There is also an "official" way to build DHIS2 apps using the DHIS2 App Platform (`@dhis2/cli-app-scripts`), which gives you React, TypeScript, the DHIS2 UI component library, and the full app runtime. If the user needs that, this is not the right template — let them know and don't proceed with this skill.

## Workflow

### 0. Check the environment

Before doing anything, figure out whether you're in an existing project or starting fresh.

| Check | How | Means |
|-------|-----|-------|
| `d2.config.js` exists in project root | Glob for `d2.config.js` | This is a DHIS2 App Platform project — wrong skill, suggest **dhis2-apps** instead |
| `package.json` has `@dhis2/app-runtime` | Read `package.json` | Same — wrong skill, suggest **dhis2-apps** |
| Directory has `manifest.webapp` or `src/js/d2api.js` | Glob | Existing tool-template project — skip scaffolding, help with what the user needs |
| Empty or near-empty directory | — | Proceed with scaffolding |

### 1. Confirm template choice and gather information

Before scaffolding, check that the user actually wants the simple template. If they've explicitly asked for the simple/vanilla/dummies template, or if the context makes it clear (e.g. "quick admin tool", "simple script"), go ahead. But if there's any ambiguity — especially if they mention React, TypeScript, `@dhis2/ui`, or the App Platform — ask:

> "Just to check — this template creates a simple vanilla JavaScript app (no React, no TypeScript, no DHIS2 UI components). It's great for lightweight admin tools. If you need the full DHIS2 App Platform with React and @dhis2/ui, I should use the **dhis2-apps** skill instead. Which do you want?"

Once confirmed, use `AskUserQuestion` to prompt for the app details. If the user has already provided some of these in their message, skip those and just gather anything still missing.

Prompt for at minimum:
- **App name** (kebab-case, e.g. `data-integrity-checker`) — this becomes the package name and ZIP filename
- **Description** (one-line summary of what the app does)

Use sensible defaults for the rest unless the user has specified them:

| Field | Default |
|-------|---------|
| **Developer name** | `"HISP Centre"` |
| **Developer email** | `"dev@dhis2.org"` |
| **Initial version** | `"0.1.0"` |

**The current working directory is the app directory.** The typical flow is: the user creates an empty directory, `cd`s into it, launches Claude, and asks to scaffold. So the skill should populate the current directory — not create a subdirectory.

### 2. Clone and reinitialize

Clone the template into the current directory, then strip the template's git history so the user starts fresh:

```bash
git clone https://github.com/dhis2/tool-template .
rm -rf .git
git init
```

The `.` at the end of `git clone` tells git to clone into the current directory (which should be empty or near-empty). If the directory isn't empty, clone to a temp location and copy the files over instead:

```bash
git clone https://github.com/dhis2/tool-template /tmp/dhis2-template-clone
cp -r /tmp/dhis2-template-clone/. .
rm -rf /tmp/dhis2-template-clone .git
git init
```

Remove template-specific files that don't belong in a new app:
- `CHANGELOG.md` — the new app's changelog starts now
- `docs/` — template development docs, not relevant to the new app

Keep `.github/workflows/` — `ci.yml` lints and builds on every PR and push to main, and `release.yml` builds and attaches the app zip to a GitHub release when a version tag (`v*.*.*`) is pushed. The user can customize or remove them later.

### 3. Customize template files

Update these files with the user's app details:

**`package.json`**:
- Set `name` to the app name
- Set `description` to the app description
- Set `version` to the initial version
- Update `manifest.webapp.developer` if custom developer info was provided
- The `manifest.webapp` section also drives the app name shown in DHIS2 — the `d2-manifest` tool reads `name` and `description` from the top-level package.json fields to generate `manifest.webapp`, so setting those is sufficient

**`src/index.html`**:
- Set `<title>` to a human-readable form of the app name (e.g., "Data Integrity Checker")
- Replace the `#mainView` div content with a minimal placeholder like:
  ```html
  <h1>App Name</h1>
  <p>Ready for development. Edit <code>src/index.html</code> and <code>src/app.js</code> to get started.</p>
  ```

**`README.md`** — replace entirely with a new README containing:
- The app name as a heading
- The description
- A "Getting started" section with: `yarn install`, `yarn start`, `yarn run zip`
- A note about configuring `.env` for the DHIS2 instance (reference `.env.template`)

**`AGENTS.md`** — update the Context section at the top so it references the new app's name and purpose instead of "tool-template." Keep all the rules and architecture documentation intact — they still apply.

**`src/app.js`** — keep the existing imports (`d2api.js`, `check-header-bar.js`, `style.css`) but clear any template-specific comments or placeholder logic. The file should be a clean starting point.

### 4. Set up development environment

```bash
node --version   # must be >= 18 (package.json engines; webpack config uses global fetch)
cp .env.template .env
yarn install
```

If Node is older than 18, say so and stop — the failure mode later (a crash on `yarn start`) is cryptic. Use **yarn, not npm**: the repo tracks only `yarn.lock` (`package-lock.json` is gitignored, CI installs with `--frozen-lockfile`).

Tell the user to update `.env` with their DHIS2 instance URL and credentials before running the dev server. If they have no instance handy and the `dhis2-instances` skill is available in the environment, it can provision a disposable one to develop against.

### 5. Verify the scaffold builds

Run the full packaging chain to make sure the scaffold works before committing — `zip` exercises lint → build → manifest generation → zip packaging (and catches a missing `zip` binary early), which is exactly what CI and releases rely on:

```bash
yarn run zip
```

If it fails, fix the issue before proceeding. The user should start from a known-good state.

### 6. Initial commit

```bash
git add -A
git commit -m "Initial scaffold from dhis2/tool-template"
```

### 7. Orient the user

Give the user a concise overview of their new project. Cover:

- **Where to write app logic**: `src/app.js` is the entry point. Add new modules in `src/js/`.
- **Where to edit the UI**: `src/index.html` for HTML, `src/css/style.css` for styling.
- **How to call the DHIS2 API**: Use the helpers in `src/js/d2api.js`:
  - `d2Get("/api/endpoint")` — GET
  - `d2PostJson("/api/endpoint", body)` — POST with JSON body
  - `d2PutJson("/api/endpoint", body)` — PUT with JSON body
  - `d2Delete("/api/endpoint")` — DELETE
  - `d2PostThenGet(endpoint, body?, getEndpoint?)` — POST (optional JSON body), then poll `getEndpoint` (defaults to the POST endpoint) with GET until the response is non-empty; **rejects with an error on timeout** (default 10 tries × 1 s, configurable as 4th/5th arguments)
  - Never use raw `fetch` for DHIS2 endpoints.
- **Dev server**: `yarn start` runs on port 8081 by default (override with `DHIS2_DEV_PORT` in `.env` — in an agent sandbox, set it to `$SANDBOX_HOST_PORT`). **All** API calls go same-origin through the dev-server proxy, so the DHIS2 instance needs no CORS whitelisting for development, and the credentials in `.env` stay in the webpack process — they are never served to the browser.
- **Build for DHIS2**: `yarn run zip` creates a ZIP in `compiled/` that can be uploaded via DHIS2's App Management.
- **Linting**: `yarn run lint` checks code style (4-space indent, double quotes, semicolons).

### 8. Hand off to using-superpowers (only if available)

If a `using-superpowers` skill is available in this environment, invoke it after orientation so brainstorming, debugging, and testing workflows trigger automatically as the user builds out the app. If it is not in the available-skills list, skip this step silently — do not try to invoke it or mention it.

## Rules

These constraints exist for good reasons and should be preserved in the new app:

- **Vanilla JS only** — no React, Vue, or other frameworks. These are lightweight admin tools.
- **Webpack stays** — don't replace it with Vite or other bundlers. The build chain is tuned for DHIS2 app packaging.
- **No hardcoded credentials** — URLs and auth go in `.env`, which is gitignored.
- **Use the API helpers** — `d2api.js` handles auth, endpoint normalization, and error parsing. Raw `fetch` bypasses all of that.
- **ESLint rules**: 4-space indent, double quotes, semicolons required.
- **Verify after changes.** Run `yarn run lint` after modifying JS files, and `yarn run build` after significant changes. Fix issues before moving on — the user should never inherit broken state.
