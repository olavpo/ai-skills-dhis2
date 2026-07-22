# ai-skills-dhis2

A collection of [Agent Skills](https://docs.claude.com/en/docs/agents-and-tools/agent-skills) for working with [DHIS2](https://dhis2.org).

| Skill | Purpose |
|---|---|
| `create-dhis2-app` | Scaffold a lightweight vanilla-JS DHIS2 tool from the tool-template (no React/App Platform). |
| `dhis2-app-review` | Review and test DHIS2 web apps: static review, Playwright UI testing, multi-version checks. |
| `dhis2-docs` | Work with the DHIS2 Web API and look up official DHIS2 documentation. |
| `dhis2-indicators` | Author, validate, and test aggregate indicators and program indicators. |
| `dhis2-integrity` | Remediate broken/duplicate metadata so an instance passes DHIS2 data-integrity checks. |
| `dhis2-metadata` | Export, split, transform, compare, validate, and import DHIS2 metadata in bulk. |

Examples use only the public DHIS2 demo databases (Sierra Leone, Laos). No real
instance data or credentials are included.

## Use

Point your agent's skills directory at these folders (e.g. symlink each into
`~/.claude/skills/`), or manage them with a tool like
[ai-skills-sync](https://github.com/olavpo/ai-skills-sync).
