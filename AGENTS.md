# Contributor & agent guidelines

Guidance for anyone — humans or AI agents — authoring or editing skills in this
repo. This is a **public** repository.

## No references to real instances

Skills, references, examples, scripts, test data, and commit messages **must not
identify any real DHIS2 instance, database, or the people/organisations behind
it.** The only concrete deployments you may name are the public DHIS2 demo
databases:

- **Sierra Leone** (the `play.dhis2.org` demo)
- **Laos**

Anything else must be anonymized.

## Lessons from other instances are welcome — anonymized

Insights, patterns, and worked examples drawn from real-world instances are
valuable and encouraged. But before they go in, strip everything that ties them
to a specific deployment. Do **not** include:

- Country, region, district, or facility names (other than SL/Laos demo)
- Organisation, project, donor, partner, or team names
- User names, real people, email addresses, or accounts
- Real hostnames, URLs, IPs, or tenant identifiers
- Credentials or tokens of any kind
- UIDs, codes, or metadata copied verbatim from a private instance
  (these can fingerprint a deployment — regenerate or use demo/synthetic ones)

Reframe the lesson generically instead. For example, turn "In the Uganda HMIS,
the ANC indicator double-counted because…" into "An instance had an ANC
indicator that double-counted because…".

## What is fine to use

- Sierra Leone and Laos **demo** data and their metadata (donor options, org
  units, UIDs, etc.)
- Placeholder hostnames (`https://example.org`, `https://your-instance.org`)
- The demo credential `admin:district` and placeholder tokens (`d2pat_...`)
- Synthetic / generated data
- Throwaway local sandbox names (e.g. `dhis2-sandbox`)

## Skills are knowledge bases, not just workflows

The skills in this repo double as domain references. Their descriptions trigger
on *activities* (remediate, author, review), so a task that merely overlaps a
skill's *subject matter* — evaluating, curating, or documenting third-party
tools in its domain — won't trigger the workflow. Read the skill's references as
background anyway; real sessions that skipped this shipped guidance the skills
already contradicted and re-derived recipes the references already contained.
When authoring, keep references self-contained enough to serve this
read-as-background use.

## When unsure

If you can't tell whether something identifies a real deployment, treat it as if
it does and anonymize it.
