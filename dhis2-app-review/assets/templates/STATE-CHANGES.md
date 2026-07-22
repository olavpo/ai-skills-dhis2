# State changes: <app name> review, <date>

Every persistent change made during this review, and its disposition.

## Project files

| File | Change | Disposition |
|---|---|---|
| `d2auth.json` | Repointed at test instance | Restored from backup / left pointing at `<url>` |

## DHIS2 instances (broker)

| Instance | Version | Seed | Disposition |
|---|---|---|---|
| `agent-review-…-242` | 2.42 | sierra-leone v42 | Deleted / left running because <reason> |

## Test data created

| Object type | UID | Instance | Deleted? |
|---|---|---|---|

## System settings changed

| Setting | Instance | Before | After | Reverted? |
|---|---|---|---|---|
| corsWhitelist | <instance> | `[…]` | `[…, http://localhost:8081]` | Yes / No — revert if not desired |

## Not reverted — action needed

<Explicit list of anything left in a changed state that the user may want to address. "Nothing" if fully clean.>
