-- ============================================================================
-- SQL fixes — sl-demo  (DHIS2 2.43.0.1; prepared 2026-07-19)
-- Rendered from the fix manifest (docs/decisions.md records the decisions).
--
-- HOW TO USE — on the database server (or any host with a DB route):
--   * whole file:        psql -h <host> -U <user> -d <db> -v ON_ERROR_STOP=1 -f this_file.sql
--   * block by block:    copy/paste one BEGIN..COMMIT block at a time into psql (recommended
--                        when interleaved with API steps — see the ORDER warnings).
-- Each block is its own transaction: a failure rolls back only that block.
--
-- !! These statements were developed against a copy of this system. The playbook checks each
--    fix's PRECONDITIONS before its steps; running this file standalone skips those guards —
--    prefer driving the sequence from the playbook and executing blocks here as it pauses.
-- !! All statements are UID-based (subqueries resolve internal ids on THIS database).
-- !! IDEMPOTENCY CONTRACT: every block is authored as a self-guarding state-transition — its
--    WHERE matches the PRE-fix state, so a re-run (or a run on an already-fixed database)
--    reports 'UPDATE 0' / 'DELETE 0' and changes nothing. If a block reports 0 rows, it was
--    already applied. If a block reports MORE rows than the change proposal describes, STOP
--    and investigate before continuing — the database differs from the one this was built on.
-- !! Destructive blocks (DELETEs, data migrations) additionally open with a DO-block GUARD
--    that RAISEs on drift, rolling back that block — a 'GUARD FAILED' error is the guard
--    working, not a bug: verify which database you are on before retrying anything.
-- ============================================================================

-- ============================================================================
-- S2 (step 1/2) — Fix the 'Births' combo's wrong category (Gender -> Location PHU/Community) — resolves 12 disjoint COCs, moves NO data
--   check: category_option_combos_disjoint   decision: docs/decisions.md#S2
--   note: UID-based subqueries — never hardcode internal numeric ids
--   preconditions (verified by the playbook, NOT by this file): check_nonzero:category_option_combos_disjoint; object_exists:/categoryCombos/m2jTvAj5kkm; custom (custom)
--   !! ORDER: run this BEFORE its later API steps — drive the sequence from the playbook, which pauses here.
--   afterwards clear the metadata cache (playbook does it, or:
--     curl -X POST -u <admin> '<DHIS2_BASE_URL>/api/maintenance?cacheClear=true' )
--   guard: aborts this block (transaction rolls back) if the pre-fix state is absent
BEGIN;
DO $$ BEGIN
  IF NOT (EXISTS (SELECT 1 FROM categorycombos_categories cc JOIN categorycombo c ON c.categorycomboid=cc.categorycomboid JOIN category g ON g.categoryid=cc.categoryid WHERE c.uid='m2jTvAj5kkm' AND g.uid='cX5k9anHEHd')) THEN
    RAISE EXCEPTION 'GUARD FAILED for S2 (Fix the ''Births'' combo''s wrong category (Gender -> Location PHU/Community) — resolves 12 disjoint COCs, moves NO data): database state differs from the pre-fix state this was built on';
  END IF;
END $$;
UPDATE categorycombos_categories SET categoryid=(SELECT categoryid FROM category WHERE uid='x3uo8LqiTBk') WHERE categorycomboid=(SELECT categorycomboid FROM categorycombo WHERE uid='m2jTvAj5kkm') AND categoryid=(SELECT categoryid FROM category WHERE uid='cX5k9anHEHd');
COMMIT;
