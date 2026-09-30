#!/usr/bin/env bash
# Build claude.ai-uploadable skill bundles (one .zip per skill).
#
# claude.ai expects a zip with a single top-level directory named after the
# skill, containing SKILL.md. Heavy machine-reference files (the multi-MB
# schemas-v4x.json dumps) are excluded to keep bundles lean — the skill texts
# treat them as optional lookups, and in claude.ai the model can't grep files
# that large usefully anyway.
#
# Usage:
#   scripts/build-skill-bundles.sh                 # default: the offline-friendly skills
#   scripts/build-skill-bundles.sh dhis2-docs ...  # explicit skill list
#
# Output: dist/<skill>.zip

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="$REPO_ROOT/dist"

# Skills that remain useful without live-instance access (knowledge +
# file-based workflows). dhis2-instances is deliberately absent; the
# app-dev/review skills lose too much without a build/test loop.
DEFAULT_SKILLS=(dhis2-docs dhis2-indicators dhis2-integrity dhis2-metadata dhis2-tracker-design)

EXCLUDES=(
  "*/references/schemas-v*.json"
  "*/.DS_Store"
  "*/__pycache__/*"
  "*.pyc"
)

SKILLS=("${@:-${DEFAULT_SKILLS[@]}}")

mkdir -p "$OUT_DIR"
cd "$REPO_ROOT"

for skill in "${SKILLS[@]}"; do
  if [[ ! -f "$skill/SKILL.md" ]]; then
    echo "skip: $skill (no SKILL.md at $REPO_ROOT/$skill)" >&2
    continue
  fi
  # claude.ai rejects uploads whose frontmatter 'description' exceeds 1024 chars
  python3 - "$skill/SKILL.md" <<'PY'
import sys, yaml
path = sys.argv[1]
desc = yaml.safe_load(open(path).read().split('---')[1]).get('description', '')
if len(desc) > 1024:
    sys.exit(f"error: {path} description is {len(desc)} chars (max 1024)")
PY
  zip_path="$OUT_DIR/$skill.zip"
  rm -f "$zip_path"
  zip -r -q "$zip_path" "$skill" -x "${EXCLUDES[@]}"
  echo "$(du -h "$zip_path" | cut -f1)  $zip_path"
done
