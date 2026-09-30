# Worked example: household enumeration for out-of-school children

A PoC on DHIS2 2.42.6 compared six designs for a door-to-door listing of children aged 3–17, finding out-of-school children (OOSC) and following them until enrolled and still attending a term later. All six were built on one instance with the same 400 synthetic households, every indicator was checked against known totals (397 values, 0 mismatches), and three scenario households were entered by script on Capture Android 3.4.2 and web.

## The brief, compressed

- Unit counted: every child in a targeted household (status, reasons, birth certificate, household characteristics).
- Unit followed: the out-of-school child (about one per household); follow-up by schools, implementing partners and volunteers; enrolment in a learner registry that is mostly not yet implemented.
- Indicators: children and OOSC by sex, age band, type, reason and household characteristics (female-headed, head's education, nomadic, distance); OOSC followed up, enrolled (by provision type and school), retained; overdue follow-ups.

## Designs and results

| | Structure (pattern) | Not reachable | Android / web, household 2 | Verdict |
|---|---|---|---|---|
| A | Household + roster stage (2) | Registry link; follow-up history | 5:06 / 3:49 | Fast, but no child to follow or enrol |
| B | Household + every child as linked TE (3) | OOSC and children by household characteristics | 7:13 / 4:43 | Clean records, slowest; household breakdowns lost; in-school children get searchable records |
| **C** | Roster of every child + OOSC as linked TE (4) | Nothing | 7:28 / 5:11 | **Recommended**: complete identification, full follow-up and registry path; cost: OOSC typed twice in Capture |
| C2 | Roster of in-school children + OOSC as TE only | OOSC by household characteristics; single disaggregated total | 6:03 / 4:12 | Each child entered once, but routing at the door and split totals |
| D | Counts by age band and sex + OOSC as TE (5) | Per-child birth certificates, 12–14/15–17 split, household breakdowns | 4:22 / 3:05 | Fastest; loses data and cannot be cross-checked |
| E | OOSC only, household copied onto each (6) | Denominator (no rates), in-school counts | — | Lists for action only |

Also built: an entry-settings variant of A (drop-downs, 17 yes/no reason fields, sequential sections). Drop-downs cost an extra click per field on web, and 17 reason fields made a longer form than one multi-select.

## Why C

- Every identification indicator comes from the roster, next to the household characteristics; every follow-up indicator from the OOSC program.
- The OOSC child uses the registry's learner tracked entity type, so a school enrols the same record in the registry without re-typing, and a registry flag on the child is readable by the follow-up indicators.
- Its only cost, typing OOSC twice, is an entry cost that a custom app removes; the model stays the same whichever tool is used. Additions if an app is built: link roster row and child both ways; copy a few household characteristics onto the child's enrolment as dated values so follow-up indicators can be broken down by them.
- With the registry incomplete (about a tenth of in-school children registered), C does not depend on it and gives the future registry a ready-made record for exactly the children who most need one.
- The exception that would change it: a registry in the same instance that its owner wants seeded by the outreach for every child → B with household characteristics copied onto each child.

## What the PoC taught about the process

Design questions were settled faster by building all options side by side than by argument: the unreachable indicators, the Android relationship sequence and the registry step were each found by trying, not by reading. For how to run such a comparison (generator, synthetic data with known totals, scripted flows, effort model, report), use `dhis2-prototyping`.
