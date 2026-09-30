# Program rules with the DHIS2 rule engine

`org.hisp.dhis.rules:rule-engine-jvm` (3.8.x) is the engine Capture uses; the server evaluates the same rule language on import
(its engine version follows the DHIS2 release, so edge cases can differ). It takes
rules, variables and values and returns effects; it knows nothing about screens. The adapter's job
is to build its inputs exactly as Capture does and turn its effects into form state.

Capture sources to copy semantics from:
- `dhis2-mobile-program-rules/.../RuleEngineExtensions.kt` — SDK → engine models
- `dhis2-mobile-program-rules/.../RulesRepository.kt` — which rules, events, supplementary data
- `form/.../RulesUtilsProviderImpl.kt` — effects → fields
- `commons/.../ValueTypeFormatter.kt` — formatting assigned values

## Engine API

```kotlin
val engine = RuleEngine.getInstance()
val context = RuleEngineContext(rules, ruleVariables, RuleSupplementaryData(), constantsValues)
engine.evaluate(target: RuleEvent, ruleEnrollment: RuleEnrollment?, ruleEvents: List<RuleEvent>, context): List<RuleEffect>
engine.evaluate(target: RuleEnrollment, ruleEvents: List<RuleEvent>, context): List<RuleEffect>
```

Models: `Rule(condition, actions, uid, name, programStage, priority)`,
`RuleAction(data, type, values = mapOf("field" to uid, "content" to text, …), priority)`,
`RuleVariableCurrentEvent / Attribute / NewestEvent / NewestStageEvent / PreviousEvent / CalculatedValue(name, useCodeForOptionSet, options, field, fieldType[, programStage])`,
`RuleEvent(event, programStage, programStageName, status, eventDate: RuleLocalDate, createdDate: RuleInstant, createdAtClientDate, dueDate, completedDate, organisationUnit, organisationUnitCode, dataValues)`,
`RuleEnrollment(enrollment, programName, incidentDate, enrollmentDate, status, organisationUnit, organisationUnitCode, attributeValues)`,
`RuleEffect(ruleId, ruleAction, data)`.

## Mapping rules (as Capture)

- **Rules per evaluation**: for an event, program-wide rules plus those of the event's stage
  (`programStage == null || == event.stage`); for the enrollment form, program-wide only.
- **Actions**: `values["field"]` = data element or attribute UID; `values["content"]` =
  `displayContent ?: content`; `DISPLAYTEXT`/`DISPLAYKEYVALUEPAIR` get `values["location"]`
  (default `"indicators"`); ASSIGN with no field and no content is unsupported; ASSIGN `data`
  defaults to `""`.
- **Variables**: drop variables whose data element / attribute is not on the phone, except
  `CALCULATED_VALUE`. Value type: integer/number types → `NUMERIC`, `BOOLEAN`/`TRUE_ONLY` →
  `BOOLEAN`, **everything else (including AGE and DATE) → `TEXT`**. Options from the field's
  option set as `Option(name, code)`.
- **Values**: data values passed as stored; numeric attribute values normalised
  (integers via `toInt()`, decimals via `toFloat()`).
- **Event status**: `VISITED` → `ACTIVE`.

## Effects → field state (as Capture)

- `HIDEFIELD` → hidden. Hidden fields **lose their value** (write the clear back), and nothing is
  assigned to a hidden field.
  **Caution — hidden *and* assigned**: if one rule hides a field another rule assigns, clearing it
  can make the server-side rule check reject the upload. Seen with web Capture on 2.42.6: the
  hidden assigned attribute was dropped and the enrollment rejected with E1309. Not verified with
  the SDK; test it if the program does this, and prefer changing the metadata so the field is
  shown read-only instead of hidden.
- `ASSIGN` → assigned value = `effect.data` formatted for the target's value type: integers
  `toDouble().toInt()`, decimals `toDouble()`, booleans `"1"→true`, `"0"→false`, else as is.
  Show read-only and **store it** (the server recomputes and rejects mismatches).
- `SHOWERROR` / `ERRORONCOMPLETE` → error, message `content + " " + data` (blank parts dropped);
  blocks completion.
- `SHOWWARNING` / `WARNINGONCOMPLETE` → warning.
- `SETMANDATORYFIELD` → required, on top of the metadata's mandatory flag.
- `DISPLAYTEXT` / `DISPLAYKEYVALUEPAIR` → form-level message.
- Actions without a field (message effects) → form-level messages.

**Re-evaluate after assignments.** Capture saves an assigned value and re-runs the rules; do the
same in memory: apply assignments and clears to the value map, evaluate again, stop when nothing
changes (3–4 passes is plenty). Otherwise a rule that reads a field another rule assigns (e.g.
"status vs age" reading a calculated age) sees the old value.

**Final check before "complete"**: evaluate once more on the stored values; block if any visible
field has an error, any required visible field is empty, or any hidden field still has a value.

## Keep the adapter SDK-free

Define small data classes (`RuleDef`, `ActionDef`, `VariableDef`, `ProgramRuleSet` with field
value types and stage names, `EventInput`, `EnrollmentInput`) in a JVM module. Map SDK objects to
them in the data module; load them from the program's metadata JSON (`programRules`,
`programRuleActions`, `programRuleVariables`, `dataElements`, `trackedEntityAttributes`, `options`)
in tests. Then:

- one JVM test per rule in the program, asserting the effect;
- a "changed rule" test: edit a condition in the loaded fixture and assert the effect follows
  (proves nothing is hard-coded);
- scenario tests with realistic records (assigned values, counts of derived objects).

## Verified behaviours (rule engine 3.8.1)

- `d2:contains(#{MULTI}, 'R17')` on a MULTI_TEXT value (`R01,R17,R02`) matches in any position.
- `d2:validatePattern(#{MULTI}, '[^,]+(,[^,]+){0,2}')` counts up to three selections regardless of
  code length (a better "at most three" than `d2:length(...) > 11`).
- `d2:yearsBetween(#{DOB}, V{event_date})` gives whole years; with a date of birth derived as
  "visit date minus N years" it returns N on every day of the year.
