# Ticket content

Write to a markdown file the user can paste into Jira. Sections in this order.

## Template

```markdown
# <One-line title: what breaks, where, for which inputs>

## Versions

| Instance | DHIS2 core | Core build | <App> app |
|---|---|---|---|
| <play url> | <version> | rev <revision>, built <date> | <app version> |

<One or two sentences: is the app version the latest on the App Hub, and does the
behaviour differ across the versions tested.>

<One line on the duplicate search: either "No existing issue found for <terms>"
or "Related: DHIS2-nnnnn, <how it differs>".>

## Steps to Reproduce

1. Log in to <play url> as admin / district.
2. <one action>
3. <one action>

<Optional: a single line naming a precondition that is easy to get wrong.>

Screenshot: `<filename>`.

## Actual Result

<What appeared, with the numbers. Two to four sentences.>

<One paragraph per isolating variant, no headings. State what changed and what
that tells you.>

<Optional cause hypothesis, two or three sentences, labelled as a hypothesis.>

## Expected Result

<The correct values, computed, with the arithmetic shown where it is not obvious.
Then what the app should do in the cases where a correct value does not exist.>
```

## Length limits per section

Title: one line, names the broken thing and the trigger condition. Not "pivot table bug".

Versions: the table, plus at most two sentences. Age of the app version and regression status only. Then one line on the duplicate search, naming the keys of anything related or stating that nothing was found.

Steps: one action per numbered line, imperative, under twenty words, no rationale. A reader should be able to follow them without understanding the bug. Include login and every dimension selection; skip nothing as "obvious".

Actual Result: the observed output and the numbers first. Then the variants, one paragraph each. Resist writing a heading per variant — headings make three observations look like three sections.

Expected Result: give the numbers. "The subtotal should sum correctly" is not useful; "the subtotal should read 2 924 (940 + 984 + 1 000)" is. When there is no single correct value, say what the app should do instead, including whether blank or N/A is acceptable.

Whole ticket: for a single-behaviour bug, roughly one screen of text plus the table. If it is running longer, either the bug has several distinct parts that belong in separate tickets, or the writing is padded.

## Things to include that are easy to forget

The distinction between backend and app. State which one the evidence points to.

Anything in the user's original report you could not reproduce. Say so plainly and describe the closest thing you did see. A reviewer treats "NaN everywhere" and "silently wrong number" as different severities, so a wrong guess here has consequences.

Whether the behaviour is a regression. Identical output on three versions is worth one sentence.

Any test artefact that is a property of your test setup rather than the bug. If your org unit selection double counts, or your period selection is unusual, note it so a reviewer does not chase it.

## Things to leave out

Your process. Nobody needs to know which endpoints you tried first or that you installed a browser.

Restatement of the user's own description back to them.

Speculation about code you have not read, beyond a short labelled hypothesis.

Severity and priority guesses, unless the user asked for them.

## Worked example

Title: Pivot table subtotals and totals return NaN for reporting rate (completeness) data items

Steps: log in, open Data Visualizer, choose Pivot table, set the data type selector to Reporting rates, select the data set, add Actual reports and Expected reports and Reporting rate, select three fixed months, select three org units, put Data on columns and Organisation unit and Period on rows, enable totals and subtotals, click Update.

Actual: data cells correct, every subtotal and total cell reads NaN. Then one paragraph on the variant with a SUM data element alongside (data element subtotals compute, completeness column still NaN, cross-type total correctly suppressed as N/A), and one paragraph on the variant with an indicator alongside (indicator subtotal computes even though the API reports the same AVERAGE total aggregation type for both, so the AVERAGE path works in general).

Expected: 2 924 and 3 468 for the first block, 290 and 378 for the second, and the rate recomputed over the covered cells rather than summed.

That is the whole shape. Three short sections, five screenshots, no headings inside Actual Result.
